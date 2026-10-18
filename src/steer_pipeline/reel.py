"""Turn a checked post's reel script into reel.mp4, reel.srt and reel_trace.json (S7-min).

Everything the viewer reads or hears is a line that already passed S3's checks; the reel adds no words. The post is
refused unless it is a draft whose artwork rendered, and its code checks are re-run against the current boundaries.

- Voice: one TTS call per line ([voice] in steer.toml; edge-tts by default, a Microsoft online service). The text
  sent is a fixed spoken form made by code (units, symbols, ranges); numbers stay digits.
- Picture: 1080 x 1920, 30 fps. One HTML page per scene (S3's brand tokens and fonts); each line fades in and rises
  24 px when it is spoken, a health card scales 1.00 -> 1.03. No counters and no number animation: frame time only
  moves CSS animations, it never writes text. Playwright captures every frame; ffmpeg (imageio-ffmpeg) encodes.
- Subtitles: the display text (not the spoken form), at most 2 lines of 32 characters, timed from the TTS word
  boundaries, each at least 1.2 s; burned in and written as reel.srt.
- QA (fails the reel): size, fps and 20-60 s duration of the MP4; every text node comes from the script or code;
  no overflow and body >= 28 px; audio length = timeline (+-0.3 s); the audio round trip (whisper.cpp transcribes
  each line, its numbers must equal the line's numbers); sources card lists every claim; draft mark on sampled frames.
"""

import asyncio
import html
import json
import re
import subprocess
import tempfile
import time
import wave
from decimal import Decimal
from pathlib import Path

from . import render as rd
from .numbers import find_numbers

W, H, FPS = 1080, 1920, 30
RATE = 48000                     # audio sample rate (mono, 16-bit)
LEAD, GAP_LINE, GAP_SCENE, END_CARD = 0.4, 0.35, 0.6, 3.5   # seconds
MIN_CAPTION, CAPTION_CHARS = 1.2, 32
DURATION = (20.0, 60.0)
WHISPER = rd.CODE / ".tools/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = rd.CODE / ".models/ggml-base.en.bin"
BANNED_IN_TEMPLATE = ("counter(", "counter-increment", "counter-reset", "@property", "<script", "textContent",
                      "innerText", "innerHTML")

# --- spoken form (code only; a display word becomes one or more spoken words) ---

TOKEN_RULES = [
    (re.compile(r"^(kg/m2|kg/m²)$"), "kilograms per square metre"),
    (re.compile(r"^kg$"), "kilograms"), (re.compile(r"^g$"), "grams"), (re.compile(r"^cm$"), "centimetres"),
    (re.compile(r"^mg$"), "milligrams"), (re.compile(r"^mmol/L$"), "millimoles per litre"),
    (re.compile(r"^CI$"), "confidence interval"), (re.compile(r"^I²$"), "I squared"),
    (re.compile(r"^Hg$"), ""), (re.compile(r"^mm$"), "millimetres of mercury"),
    (re.compile(r"^steerclub\.com$"), "steer club dot com"),
    (re.compile(r"^US$"), "U.S."),                       # not the word "us" (display stays "US")
]
PREFIX = [("≥", "at least "), ("≤", "at most "), ("±", "plus or minus "), ("<", "less than "), (">", "more than "),
          ("−", "minus "), ("-", "minus ")]


def _speak_token(tok: str) -> str:
    core = tok.rstrip(".,;:!?)")
    tail = tok[len(core):]
    lead = ""
    while core[:1] == "(":
        lead, core = lead + "(", core[1:]
    for pat, rep in TOKEN_RULES:
        if pat.match(core):
            return (lead + rep + tail).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)[–-](\d+(?:\.\d+)?)(.*)$", core)       # 2.09–4.28 -> 2.09 to 4.28
    if m:
        return f"{lead}{m[1]} to {_speak_token(m[2] + m[3])}{tail}"
    for sym, word in PREFIX:
        if core.startswith(sym) and core[len(sym):][:1].isdigit():
            return lead + word + _speak_token(core[len(sym):] + tail)
    if re.match(r"^\d+\+$", core):                                       # 45+ -> 45 and over
        return f"{lead}{core[:-1]} and over{tail}"
    if re.match(r"^\d+(?:\.\d+)?%$", core):
        return f"{lead}{core[:-1]} per cent{tail}"
    m = re.match(r"^(\d+(?:\.\d+)?)(kg|g|cm|mg)$", core)                  # 0.4kg -> 0.4 kilograms
    if m:
        return f"{lead}{m[1]} {_speak_token(m[2])}{tail}"
    return tok


def spoken(text: str) -> tuple[str, list[str]]:
    """(the text to voice, the spoken form of each display word) - same word order, so captions can be timed.
    Shorter speech, same numbers: the BMI unit is not voiced right after "BMI" or "body mass index" ("BMI under 25 kg/m2", "BMI <25 kg/m2"
    -> "BMI under 25"; the unit stays on screen), and "45+" is voiced "45 and over"."""
    words = text.split()
    core = lambda t: t.strip("(),.;:!?")
    per_word = []
    for i, t in enumerate(words):
        before = " ".join(core(w) for w in words[max(0, i - 5):i]).lower()
        after_bmi = bool(re.search(r"\bbmi\b|body mass index", before))
        if after_bmi and re.match(r"^(kg/m2|kg/m²)$", core(t)):
            per_word.append("")
            if per_word[:-1]:   # its trailing punctuation moves to the word before, for the voice's pause
                per_word[-2] += t[len(t.rstrip(".,;:!?)")):]
        elif after_bmi and re.match(r"^<\d", t):
            per_word.append("under " + _speak_token(t[1:]))
        else:
            per_word.append(_speak_token(t))
    return " ".join(w for w in per_word if w), per_word


# --- number round trip (transcript side may spell numbers out) ---

ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                   "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def words_to_digits(text: str) -> str:
    """Transcript only: "two point nine nine" -> "2.99", "twenty five" -> "25", "minus" kept for the sign."""
    toks, out, i = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text, flags=re.I).split(), [], 0  # "twenty-five"
    word = lambda t: t.lower().strip(".,;:!?")
    while i < len(toks):
        w = word(toks[i])
        if w in ONES or w in TENS:
            n = 0
            while i < len(toks) and (word(toks[i]) in ONES or word(toks[i]) in TENS):
                n += ONES.get(word(toks[i]), 0) + TENS.get(word(toks[i]), 0)
                i += 1
            num = str(n)
            if i + 1 < len(toks) and word(toks[i]) == "point" and word(toks[i + 1]) in ONES:
                i += 1
                digits = ""
                while i < len(toks) and word(toks[i]) in ONES and ONES[word(toks[i])] < 10:
                    digits += str(ONES[word(toks[i])])
                    i += 1
                num += "." + digits
            out.append(num)
        else:
            out.append(toks[i])
            i += 1
    return re.sub(r"\bminus (?=\d)", "-", " ".join(out), flags=re.I)  # "minus 1.74" is the number -1.74


def number_values(text: str) -> list[Decimal]:
    """Signed values only: the voice says "kilograms" where the screen says "kg", so units are not compared."""
    vals = []
    for n in find_numbers(text):
        vals.append(n.low)
        if n.high is not None:
            vals.append(n.high)
    return sorted(vals)


def audio_numbers_match(line_text: str, transcript: str | None) -> dict:
    if not transcript or not transcript.strip():
        return {"ok": False, "code": "AUDIO_TRANSCRIPT_MISSING", "detail": "no transcript to compare: the reel fails"}
    want, heard = number_values(line_text), number_values(words_to_digits(transcript))
    ok = want == heard
    return {"ok": ok, "code": None if ok else "AUDIO_NUMBER_MISMATCH", "screen": [str(v) for v in want],
            "heard": [str(v) for v in heard], "transcript": transcript.strip()}


# --- voice and transcription (replaceable for tests) ---

def edge_tts_voice(text: str, voice: dict) -> tuple[bytes, list[tuple[str, float, float]]]:
    try:
        audio, words = _edge_tts(text, voice)
    except Exception as e:   # network, service: a clean voice failure, as for ElevenLabs
        raise VoiceError(f"edge-tts failed: {type(e).__name__}") from None
    if not audio:
        raise VoiceError("edge-tts returned no audio")
    return audio, words


def _edge_tts(text: str, voice: dict) -> tuple[bytes, list[tuple[str, float, float]]]:
    import edge_tts

    async def run():
        audio, words = b"", []
        async for ch in edge_tts.Communicate(text, voice["name"], rate=voice.get("rate", "+0%"),
                                             boundary="WordBoundary").stream():
            if ch["type"] == "audio":
                audio += ch["data"]
            elif ch["type"] == "WordBoundary":
                words.append((ch["text"], ch["offset"] / 1e7, ch["duration"] / 1e7))
        return audio, words
    return asyncio.run(run())


VOICE_CREDIT = "Voice: AI-generated (ElevenLabs)"   # the free plan's required credit: caption footer, trace, README
ELEVEN_KEY_FILE = Path.home() / "steer-secrets" / "elevenlabs.env"   # the key is read from here only, never logged
TTS_CACHE = Path.home() / ".cache" / "steer-tts"   # re-renders reuse the audio: 0 credits


class VoiceError(RuntimeError):
    """The online voice failed (no key, quota, network, HTTP error). The message never holds the key."""


def eleven_key() -> str:
    try:
        for row in ELEVEN_KEY_FILE.read_text().splitlines():
            k, _, v = row.partition("=")
            if k.strip() == "ELEVENLABS_API_KEY" and v.strip():
                return v.strip().strip("'\"")
    except OSError:
        pass
    raise VoiceError(f"ELEVENLABS_API_KEY not found in {ELEVEN_KEY_FILE}")


def eleven_request(url: str, body: dict, key: str) -> dict:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:   # the status only: the response body or request could echo the key
        raise VoiceError(f"ElevenLabs HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise VoiceError(f"ElevenLabs unreachable: {getattr(e, 'reason', e)}") from None


def words_from_alignment(al: dict) -> list[tuple[str, float, float]]:
    """ElevenLabs' character timings -> (word, start, duration), the shape edge-tts' word boundaries have."""
    words, cur, t0, t1 = [], "", 0.0, 0.0
    for c, a, b in zip(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]):
        if c.isspace():
            if cur:
                words.append((cur, t0, t1 - t0))
            cur = ""
            continue
        if not cur:
            t0 = a
        cur, t1 = cur + c, b
    if cur:
        words.append((cur, t0, t1 - t0))
    return words


class ElevenLabs:
    """tts(text, voice) -> (audio bytes, word boundaries) through ElevenLabs' with-timestamps endpoint, cached by
    sha256(text, voice_id, model, settings). .log records each line: characters, cache key, hit or billed."""

    def __init__(self, request=None):
        self.request = request or eleven_request
        self.log = []

    def __call__(self, text: str, voice: dict):
        import base64
        import hashlib
        model = voice.get("model", "eleven_multilingual_v2")
        vs = {"stability": voice.get("stability", 0.5), "similarity_boost": voice.get("similarity", 0.75)}
        key = hashlib.sha256(json.dumps([text, voice["voice_id"], model, vs], sort_keys=True).encode()).hexdigest()
        f = TTS_CACHE / f"{key}.json"
        hit = f.exists()
        if hit:
            d = json.loads(f.read_text())
        else:
            d = self.request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice['voice_id']}/with-timestamps"
                             f"?output_format=mp3_44100_128", {"text": text, "model_id": model, "voice_settings": vs},
                             eleven_key())
            al = d.get("alignment") if isinstance(d, dict) else None
            if not (isinstance(d, dict) and d.get("audio_base64") and isinstance(al, dict) and al.get("characters")
                    and len(al["characters"]) == len(al.get("character_start_times_seconds") or [])
                    == len(al.get("character_end_times_seconds") or [])):
                raise VoiceError("ElevenLabs reply without audio or timings")   # never cached
            TTS_CACHE.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(d))
        self.log.append({"characters": len(text), "cache_key": key[:16], "cache_hit": hit})
        return base64.b64decode(d["audio_base64"]), words_from_alignment(d["alignment"])


def whisper_transcribe(wav16k: Path) -> str:
    if not WHISPER.exists() or not WHISPER_MODEL.exists():
        raise RuntimeError("whisper.cpp is not set up: run code/tools/setup-whisper.sh")
    r = subprocess.run([str(WHISPER), "-m", str(WHISPER_MODEL), "-f", str(wav16k), "-nt", "-np"],
                       capture_output=True, text=True, timeout=300)
    return r.stdout.strip() if r.returncode == 0 else ""


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode_pcm(audio: bytes, rate: int = RATE) -> bytes:
    """Any audio the TTS returns -> mono 16-bit PCM at `rate`."""
    r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(rate),
                        "pipe:1"], input=audio, capture_output=True, check=True)
    return r.stdout


def resample(pcm: bytes, rate: int) -> bytes:
    r = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-i", "pipe:0",
                        "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"], input=pcm, capture_output=True, check=True)
    return r.stdout


def probe_mp4(path: Path) -> dict:
    """Size, frame rate and duration from ffmpeg's own report of the file."""
    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    vid = re.search(r"Video: h264.*?, (\d+)x(\d+).*?, ([\d.]+) fps", err)
    if not (dur and vid):
        return {}
    return {"width": int(vid[1]), "height": int(vid[2]), "fps": float(vid[3]), "audio": "Audio: aac" in err,
            "duration": int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3]), "bytes": path.stat().st_size}


MARK_BOX = (560, 206, 460, 62)  # x, y, w, h around the draft pill (top right, layout.json "pill")


SUBS_BOX = (90, 1340, 900, 230)


def draft_mark_present(path: Path, times: list[float], palette: dict) -> list[bool]:
    """Decode frames of the finished MP4 and look for the pill in its box."""
    return [box_has_content(path, t, MARK_BOX) for t in times]


def box_has_content(path: Path, t: float, box: tuple) -> bool:
    """True when enough pixels of the box in the frame at t differ from the box's most common colour (background)."""
    x, y, w, h = box
    raw = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True).stdout
    px = [raw[i:i + 3] for i in range(0, len(raw) - 2, 3)]
    if not px:
        return False
    bg = max(set(px), key=px.count)
    return sum(1 for q in px if sum(abs(a - b) for a, b in zip(q, bg)) > 60) > 300


def label_present(path: Path, t: float, box: tuple = None) -> bool:
    """The presenter label pill in its box: mostly its white ground, with some dark text pixels."""
    x, y, w, h = box or LABEL_BOX
    raw = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True).stdout
    px = [raw[i:i + 3] for i in range(0, len(raw) - 2, 3)]
    if not px:
        return False
    white = sum(1 for q in px if min(q) > 225) / len(px)
    dark = sum(1 for q in px if max(q) < 90) / len(px)
    return white > 0.4 and dark > 0.03


def transcript_table(scene_rounds: list[dict]) -> str:
    """The scene-level round trip as a table: the script text next to what whisper heard in the final audio."""
    cell = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
    rows = ["| scene | script (display text) | whisper, final audio | numbers on screen | numbers heard | result |",
            "|---|---|---|---|---|---|"]
    for r in scene_rounds:
        rows.append(f"| {r['scene']} | {cell(r['script'])} | {cell(r.get('transcript', ''))} | {', '.join(r.get('screen', []))} | "
                    f"{', '.join(r.get('heard', []))} | {'ok' if r['ok'] else r['code']} |")
    return "\n".join(rows) + "\n"


def _write_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate), w.writeframes(pcm)


# --- timeline and captions ---

def _norm(w: str) -> str:
    return re.sub(r"[^0-9a-z.]", "", w.lower())


def word_times(per_word: list[str], boundaries: list, duration: float) -> list[float]:
    """Start time of each display word: spoken words matched in order to the TTS boundaries; words the TTS did
    not mark get the time of the previous match (or a share of the line by position)."""
    spoken_words = [(d, sw) for d, s in enumerate(per_word) for sw in s.split()]
    starts: list[float | None] = [None] * len(per_word)
    j = 0
    for text, offset, _ in boundaries:
        for k in range(j, min(j + 4, len(spoken_words))):
            if _norm(spoken_words[k][1]) == _norm(text) or _norm(text) in _norm(spoken_words[k][1]):
                d = spoken_words[k][0]
                if starts[d] is None:
                    starts[d] = offset
                j = k + 1
                break
    last = 0.0
    for d in range(len(starts)):
        if starts[d] is None:
            starts[d] = max(last, duration * d / max(1, len(starts)))
        last = starts[d]
    return starts


def chunk_caption(words: list[str], chars: int = CAPTION_CHARS) -> list[tuple[int, int]]:
    """Word ranges [a, b) that fit in 2 lines of `chars` characters each."""
    chunks, a = [], 0
    while a < len(words):
        lines, b, cur = 1, a, 0
        while b < len(words):
            add = len(words[b]) + (1 if cur else 0)
            if cur + add <= chars:
                cur += add
                b += 1
            elif lines < 2 and len(words[b]) <= chars:
                lines, cur = 2, len(words[b])
                b += 1
            else:
                break
        b = max(b, a + 1)
        chunks.append((a, b))
        a = b
    return chunks


def captions_for_line(text: str, starts: list[float], line_start: float, line_end: float) -> list[dict]:
    words = text.split()
    out = []
    for a, b in chunk_caption(words):
        out.append({"text": " ".join(words[a:b]), "start": line_start + starts[a], "end": None})
    for i, c in enumerate(out):
        c["end"] = out[i + 1]["start"] if i + 1 < len(out) else line_end
    # every caption on screen for at least MIN_CAPTION: a short one is merged into its neighbour
    merged = []
    for c in out:
        if merged and (c["end"] - c["start"] < MIN_CAPTION or merged[-1]["end"] - merged[-1]["start"] < MIN_CAPTION) \
                and len(merged[-1]["text"]) + 1 + len(c["text"]) <= 2 * CAPTION_CHARS:
            merged[-1] = {"text": merged[-1]["text"] + " " + c["text"], "start": merged[-1]["start"], "end": c["end"]}
        else:
            merged.append(c)
    return merged


def srt(captions: list[dict]) -> str:
    ts = lambda t: f"{int(t // 3600):02}:{int(t % 3600 // 60):02}:{int(t % 60):02},{int(round(t % 1 * 1000)) % 1000:03}"
    return "\n".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n" for i, c in enumerate(captions, 1))


# --- scenes (HTML) ---

def _scene_css(p: dict) -> str:
    return rd._css(p, W, H) + f"""
.content {{ top: 220px; bottom: 640px; left: 90px; right: 90px; gap: 44px; }}
.foot {{ bottom: 90px; left: 90px; right: 90px; }}
.mark {{ top: 222px; right: 70px; font-size: 26px; }}
.line {{ opacity: 0; }}
.subs {{ position: absolute; left: 90px; right: 90px; top: 1340px; height: 230px; }}
.cap {{ position: absolute; left: 0; right: 0; bottom: 0; opacity: 0; background: {p['paper']}; color: {p['ink']};
        font: 600 46px/1.25 Inter; padding: 18px 26px; border-radius: 10px; text-align: center; }}
.endurl {{ font-family: Fraunces; font-variation-settings: 'WONK' 0, 'SOFT' 50; color: {p['rust']}; font-size: 56px; }}
@keyframes rise {{ from {{ opacity: 0; transform: translateY(24px); }} to {{ opacity: 1; transform: none; }} }}
@keyframes grow {{ from {{ transform: scale(1.0); }} to {{ transform: scale(1.03); }} }}
"""


def _line_div(inner: str, delay: float, extra_anim: str = "") -> str:
    anim = f"rise 0.6s ease-out {delay:.3f}s both" + (f", {extra_anim}" if extra_anim else "")
    return f'<div class="line" style="animation: {anim}">{inner}</div>'


def scene_html(scene: dict, p: dict, by_claim: dict, emphasis: str | None) -> str:
    """One scene: its lines (each appearing when spoken), its captions and the code-built parts."""
    lines_html, bold = [], scene["kind"] == "brand"
    for ln in scene["lines"]:
        k, t, d = ln["kind"], ln["text"], ln["start"] - scene["start"]
        if k == "health":
            labels = [rd.short_source(e) for e in rd.source_entries([by_claim[i] for i in ln["claim_ids"] if i in by_claim])]
            inner = ('<div class="card">' + rd._fit("body", "body", rd._esc(t))
                     + "".join(f'<div class="label">{html.escape(x)}</div>' for x in labels) + "</div>")
            lines_html.append(_line_div(inner, d, f"grow {max(1.0, scene['end'] - ln['start']):.3f}s linear {d:.3f}s both"))
        elif k == "framing":
            big = scene["kind"] == "hook" and ln is scene["lines"][0]
            lines_html.append(_line_div(rd._fit("hook" if big else "serif", "serif", rd._emph(t, emphasis if big else None)), d))
        elif k == "brand":
            lines_html.append(_line_div(rd._fit("serif", "serif brand", rd._esc(t)), d))
        elif k == "cta":
            lines_html.append(_line_div(rd._fit("serif", "serif cta", rd._esc(t)), d))
        else:
            lines_html.append(_line_div(rd._fit("body", "signoff", rd._esc(t)), d))
    caps = []
    for n, c in enumerate(scene["captions"]):
        a, dur = c["start"] - scene["start"], c["end"] - c["start"]
        caps.append(f'<div class="cap" style="animation: cap{n} {dur:.3f}s linear {a:.3f}s none">{rd._esc(c["text"])}</div>')
    keyframes = "".join(f"@keyframes cap{n} {{ 0%, 100% {{ opacity: 1; }} }}" for n in range(len(caps)))
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_scene_css(p)}{keyframes}</style></head>'
            f'<body class="{"bold" if bold else ""}"><div class="mark">{rd.DRAFT}</div>'
            f'<div class="content">{"".join(lines_html)}</div><div class="subs">{"".join(caps)}</div>'
            f'<div class="foot"><span class="wordmark">steer</span><span></span></div></body></html>')


def end_card_html(entries: list[dict], p: dict) -> str:
    content = ('<h2>Sources</h2>' + "".join(rd._entry_html(e) for e in entries)
               + rd._fit("sources", "foot-note", html.escape(rd.FOOTNOTE)) + '<div class="endurl">steerclub.com</div>')
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_scene_css(p)}</style></head><body>'
            f'<div class="mark">{rd.DRAFT}</div><div class="content sources">{content}</div>'
            f'<div class="foot"><span class="wordmark">steer</span><span></span></div></body></html>')


def lint_template(page: str) -> list[str]:
    """Nothing in a scene may count, animate a number or write text at frame time."""
    return [b for b in BANNED_IN_TEMPLATE if b in page]


# the page's animations are collected once, before any seek: Chromium drops a finished animation (fill: none) from
# document.getAnimations(), and a caption seeked past its end would then never come back
SET_TIME_JS = """t => { window.__anims = window.__anims || document.getAnimations();
  window.__anims.forEach(a => { a.pause(); a.currentTime = t; }); }"""
TEXTS_JS = """() => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), out = [];
  while (w.nextNode()) { const t = w.currentNode.data.replace(/\u00a0/g, ' ').trim(); if (t) out.push(t); }
  return out; }"""


def _plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


# --- the reel ---

def scene_kind(scene_lines: list[dict], index: int) -> str:
    kinds = {ln["kind"] for ln in scene_lines}
    if index == 0 and "framing" in kinds:
        return "hook"
    if "health" in kinds:
        return "point"
    if kinds == {"brand"}:
        return "brand"
    return "cta" if kinds & {"cta", "signoff"} else "point"


def build_timeline(script: dict, voice: dict, tts, health_hold: float = 0.0, end_card: float = END_CARD
                   ) -> tuple[list[dict], bytes, dict]:
    """Voice every line; return the scenes with line/caption/word times, the full PCM track and timing stats.
    health_hold: the pause after a health line is at least this long (the whole line holds on screen)."""
    scenes, pcm, t, tts_s = [], b"", 0.0, 0.0
    silence = lambda s: b"\x00\x00" * int(round(s * RATE))
    pcm += silence(LEAD)
    t = LEAD
    for si, sc in enumerate(script["scenes"]):
        scene = {"index": si, "kind": scene_kind(sc["lines"], si), "start": t - (LEAD if si == 0 else 0), "lines": [],
                 "captions": []}
        for li, ln in enumerate(sc["lines"]):
            say, per_word = spoken(ln["text"])
            t0 = time.perf_counter()
            audio, bounds = tts(say, voice)
            tts_s += time.perf_counter() - t0
            line_pcm = decode_pcm(audio)
            dur = len(line_pcm) / 2 / RATE
            starts = word_times(per_word, bounds, dur)
            gap = GAP_LINE if li + 1 < len(sc["lines"]) else GAP_SCENE
            if ln["kind"] == "health":
                gap = max(gap, health_hold)
            entry = ln | {"spoken": say, "start": t, "end": t + dur, "duration": dur, "pcm": line_pcm,
                          "word_starts": [t + x for x in starts], "hold_end": t + dur + gap}
            scene["lines"].append(entry)
            scene["captions"] += captions_for_line(ln["text"], starts, t, t + dur)
            pcm += line_pcm + silence(gap)
            t += dur + gap
        scene["end"] = t
        scenes.append(scene)
    end = {"index": len(scenes), "kind": "sources", "start": t, "end": t + end_card, "lines": [], "captions": []}
    pcm += silence(end_card)
    scenes.append(end)
    return scenes, pcm, {"tts_seconds": round(tts_s, 2), "timeline_seconds": round(end["end"], 3)}


# --- Remotion props (reel_props.json v2, code/reel/props.schema.json) ---

REEL_DIR = rd.CODE / "reel"
REMOTION_DURATION = (20.0, 45.0)
HOOK_MAX = 4.0   # the hook line is spoken within 4 s of the reel's start
REMOTION_END_CARD, HEALTH_HOLD = 5.0, 0.8   # the sources card holds >= 5 s (its text >= 40 px)
PRESENTER_LABEL = "AI-generated presenter · not a Steer clinician"   # D-026: on every frame a presenter clip is on screen
LABEL_BOX = (60, 222, 220, 30)  # x, y, w, h inside the label pill (top left, layout.json "pill"; Reel.tsx LABEL_BG / LABEL_INK)
GROUNDS = {"hook": "cream", "brand": "maroon", "cta": "cream", "sources": "cream"}


def display(text: str) -> str:
    """The documented display transforms only: kg/m2 -> kg/m², a number and its unit joined by a no-break space."""
    return rd.SQUARED.sub("/m²", rd.UNIT.sub("\\1\u00a0", text))


def display_tokens(text: str, starts: list[float]) -> list[tuple[str, float]]:
    """The line's display words with the time each is spoken: words the display joins (a number and its unit) are
    one token, timed by its first word."""
    words, out, i = text.split(), [], 0
    while i < len(words):
        tok, t0 = words[i], starts[i]
        while i + 1 < len(words) and rd.UNIT.search(f"{words[i]} {words[i + 1]}"):
            i += 1
            tok += "\u00a0" + words[i]
        out.append((rd.SQUARED.sub("/m²", tok), t0))
        i += 1
    if " ".join(t for t, _ in out) != display(text):
        raise ValueError(f"display tokens do not rebuild the line: {text!r}")
    return out


def make_beats(tokens: list[tuple[str, float]]) -> list[dict]:
    """2-4 tokens a beat, cut after punctuation once a beat has 2 tokens; a number and its unit are one token."""
    beats, cur = [], []
    for tok, t0 in tokens:
        cur.append((tok, t0))
        if len(cur) >= 4 or (len(cur) >= 2 and tok[-1:] in ",.;:?!"):
            beats.append(cur)
            cur = []
    if cur:
        if len(cur) == 1 and beats and len(beats[-1]) < 4:
            beats[-1] += cur
        else:
            beats.append(cur)
    return [{"text": " ".join(t for t, _ in b), "start": round(b[0][1], 3),
             "words": [{"text": t, "start": round(s0, 3)} for t, s0 in b]} for b in beats]


def times_x(tok: str) -> str:
    """The one display transform for a hero number: "5.27 times" -> "5.27×" (the voice still says "times")."""
    return re.sub(r"^(\d+(?:\.\d+)?)\u00a0times$", "\\1×", tok)


def make_cards(tokens: list[tuple[str, float]], max_words: int = 22) -> list[dict] | None:
    """A long health line as 2 visual cards, split by code at its first colon: card 1 the setting (who was studied),
    card 2 the findings, each number token a hero part (shown large, "N times" as "N×") with the words after it.
    Every part is an exact run of the line's display tokens. None when the line has no colon, a card would exceed
    max_words, or card 2 holds no number: the line then builds up phrase by phrase."""
    cut = next((k for k, (tok, _) in enumerate(tokens) if tok.endswith(":")), None)
    if cut is None or cut == len(tokens) - 1:
        return None
    one, two = tokens[:cut + 1], tokens[cut + 1:]
    if len(one) > max_words or len(two) > max_words or not any(re.match(r"^[−-]?\d", t) for t, _ in two):
        return None
    parts = []
    for tok, t0 in two:
        if re.match(r"^[−-]?\d", tok):
            parts.append({"text": tok, "role": "hero", "show": times_x(tok), "at": round(t0, 3)})
        elif parts and parts[-1]["role"] == "text":
            parts[-1]["text"] += " " + tok
            parts[-1]["show"] = parts[-1]["text"]
        else:
            parts.append({"text": tok, "role": "text", "show": tok, "at": round(t0, 3)})
    setting = " ".join(t for t, _ in one)
    return [{"start": round(one[0][1], 3), "parts": [{"text": setting, "role": "setting", "show": setting, "at": round(one[0][1], 3)}]},
            {"start": round(two[0][1], 3), "parts": parts}]


def checked_keys(post: dict) -> list[dict]:
    """The post's key pairs that passed its checks (slides and static): number, label and the claims behind them."""
    units = list(post.get("slides") or []) + ([post["static"]] if post.get("static") else [])
    out = []
    for u in units:
        lines = [u["headline"], *u["supporting"]] if "headline" in u else u["lines"]
        num, label = u.get("key_number"), u.get("key_label")
        if num and label:
            src = [ln for ln in lines if ln["kind"] == "health" and num in ln["text"] and label in ln["text"]]
            if src:
                out.append({"number": num, "label": label, "claim_ids": set(src[0]["claim_ids"])})
    return out


def source_entry_parts(e: dict) -> dict:
    """The end card's citation, built by code as on the S3 sources slide, split around the italic journal."""
    loc = str(e.get("location") or "").capitalize()
    return {"before": f"{e.get('authors', '')} {e.get('title', '')}.", "journal": rd.journal_display(e.get("journal")),
            "after": f", {e.get('year', '')}. doi:{e.get('doi', '')}. {loc}."}


def build_props(scenes: list[dict], record: dict, brand: dict, emphasis: str | None, keys: list[dict],
                presenter: dict | None = None) -> dict:
    """presenter: {scene index: clip path}; the clip fills that scene, muted, and carries the label (D-026)."""
    by_claim = {s["claim_id"]: s for s in record["sources"]}
    entries = rd.source_entries(record["sources"])
    out, point = [], 0
    for sc in scenes:
        if sc["kind"] == "sources":
            ground = GROUNDS["sources"]
        elif any(ln["kind"] == "brand" for ln in sc["lines"]):   # the brand line is the bold maroon beat
            ground = "maroon"
        elif sc["kind"] == "point":
            ground = ("rust", "cream")[point % 2]
            point += 1
        else:
            ground = GROUNDS[sc["kind"]]
        lines = []
        for ln in sc["lines"]:
            text = display(ln["text"])
            tokens = display_tokens(ln["text"], [x - ln["start"] for x in ln["word_starts"]])
            tokens = [(tok, ln["start"] + rel) for tok, rel in tokens]
            key = None
            for k in keys:
                if ln["kind"] == "health" and k["number"] in ln["text"] and k["label"] in ln["text"] \
                        and k["claim_ids"] & set(ln["claim_ids"]):
                    first = display(k["number"]).split(" ")[0]
                    at = next((t0 for tok, t0 in tokens if tok.startswith(first) or first.startswith(tok)), ln["start"])
                    key = {"number": display(k["number"]), "label": display(k["label"]), "at": round(at, 3)}
                    break
            labels = [rd.short_source(e) for e in rd.source_entries([by_claim[i] for i in ln["claim_ids"] if i in by_claim])]
            cards = make_cards(tokens) if ln["kind"] == "health" else None
            if cards:
                key = None   # the hero numbers are the scene's large type
            lines.append({"kind": ln["kind"], "text": text, "start": round(ln["start"], 3), "end": round(ln["end"], 3),
                          "cards": cards,
                          "hold_end": round(ln["hold_end"], 3), "beats": make_beats(tokens),
                          "emphasis": display(emphasis) if sc["kind"] == "hook" and emphasis and display(emphasis) in text
                          and ln is sc["lines"][0] else None,
                          "key": key, "source_label": "; ".join(labels) if ln["kind"] == "health" and labels else None})
        out.append({"kind": sc["kind"], "ground": ground, "start": round(sc["start"], 3), "end": round(sc["end"], 3),
                    "transition_in": "none" if not out else "wipe", "broll": None,
                    "presenter": {"video": f"presenter-{sc['index']}.mp4", "start": round(sc["start"], 3), "end": round(sc["end"], 3),
                                  "label": PRESENTER_LABEL} if presenter and sc["index"] in presenter else None, "lines": lines,
                    "sources": {"heading": "Sources", "entries": [source_entry_parts(e) for e in entries],
                                "footnote": rd.FOOTNOTE, "url": "steerclub.com"} if sc["kind"] == "sources" else None})
    return {"version": 2, "fps": FPS, "width": W, "height": H, "durationInFrames": int(round(scenes[-1]["end"] * FPS)),
            "audio": "voice.wav", "fonts": {"serif": "Fraunces.ttf", "serif_italic": "Fraunces-Italic.ttf", "sans": "Inter.ttf"},
            "palette": brand["palette"], "chrome": {"draft_mark": rd.DRAFT, "wordmark": "steer"}, "scenes": out}


def safe_area_problems(layout_path: Path | None = None) -> list[str]:
    """Every text box in code/reel/layout.json must sit inside its safe band (below the IG top bar, above its UI)."""
    lay = json.loads((layout_path or REEL_DIR / "layout.json").read_text())
    top, bottom = lay["safe"]["top"], lay["safe"]["bottom"]
    return [f"{k}: {v['top']}-{v['top'] + v['height']} outside {top}-{bottom}" for k, v in lay.items()
            if isinstance(v, dict) and "height" in v and not (top <= v["top"] and v["top"] + v["height"] <= bottom)]


def pool_check(props: dict, script: dict, record: dict, emphasis: str | None, keys: list[dict]) -> list[dict]:
    """Every string the composition can draw, checked against the post before render: a script line verbatim (after
    the display transforms), an exact substring of its own line, the post's checked key pair and emphasis, or a
    string built by code. Any other string fails the reel."""
    import jsonschema

    fails = [{"code": "TEXT_OUTSIDE_SAFE_AREA", "detail": x} for x in safe_area_problems()]
    try:
        jsonschema.validate(props, json.loads((REEL_DIR / "props.schema.json").read_text()))
    except jsonschema.ValidationError as e:
        return [{"code": "PROPS_SCHEMA", "detail": e.message}]
    lines_ok = {display(ln["text"]) for sc in script["scenes"] for ln in sc["lines"]}
    keys_ok = {(display(k["number"]), display(k["label"])) for k in keys}
    entries = rd.source_entries(record["sources"])
    labels_ok = {rd.short_source(e) for e in entries}
    bad = lambda what, value: fails.append({"code": "TEXT_NOT_FROM_SCRIPT", "detail": f"{what}: {value!r}"})
    if props["chrome"] != {"draft_mark": rd.DRAFT, "wordmark": "steer"}:
        bad("chrome", props["chrome"])
    for sc in props["scenes"]:
        for ln in sc["lines"]:
            if ln["text"] not in lines_ok:
                bad("line", ln["text"])
            for b in ln["beats"]:
                if b["text"] not in ln["text"]:
                    bad("beat", b["text"])
                for w in b["words"]:
                    if w["text"] not in b["text"]:
                        bad("word", w["text"])
            if ln["emphasis"] is not None and (ln["emphasis"] != display(emphasis or "") or ln["emphasis"] not in ln["text"]):
                bad("emphasis", ln["emphasis"])
            if ln["cards"] is not None:   # the cards cover the line exactly, and only heroes are transformed
                parts = [q for c in ln["cards"] for q in c["parts"]]
                if " ".join(q["text"] for q in parts) != ln["text"]:
                    bad("cards", [q["text"] for q in parts])
                for q in parts:
                    if q["show"] != (times_x(q["text"]) if q["role"] == "hero" else q["text"]):
                        bad("card part", q)
            k = ln["key"]
            if k is not None and ((k["number"], k["label"]) not in keys_ok or k["number"] not in ln["text"]
                                  or k["label"] not in ln["text"]):
                bad("key", k)
            if ln["source_label"] is not None and not set(ln["source_label"].split("; ")) <= labels_ok:
                bad("source_label", ln["source_label"])
        pr = sc["presenter"]
        if pr is not None and (pr["label"] != PRESENTER_LABEL or pr["video"] != f"presenter-{props['scenes'].index(sc)}.mp4"):
            bad("presenter", pr)
        if sc["sources"] is not None and sc["sources"] != {"heading": "Sources", "entries": [source_entry_parts(e) for e in entries],
                                                           "footnote": rd.FOOTNOTE, "url": "steerclub.com"}:
            bad("sources", sc["sources"])
    return fails


def render_remotion(props: dict, pcm: bytes, out_dir: Path, presenter: dict | None = None) -> tuple[Path | None, list[dict], dict]:
    """node code/reel/render.mjs -> H.264 + PCM in MKV, then AAC encoded once into reel.mp4."""
    timings, fails = {}, []
    with tempfile.TemporaryDirectory(prefix="steer-remotion-") as tmp:
        tmp = Path(tmp)
        public = tmp / "public"
        public.mkdir()
        for f in props["fonts"].values():
            (public / f).write_bytes((rd.FONTS / f).read_bytes())
        for i, clip in (presenter or {}).items():
            (public / f"presenter-{i}.mp4").write_bytes(Path(clip).read_bytes())
        _write_wav(public / props["audio"], pcm)
        (tmp / "props.json").write_text(json.dumps(props, ensure_ascii=False))
        mkv = tmp / "reel.mkv"
        t0 = time.perf_counter()
        r = subprocess.run(["node", str(REEL_DIR / "render.mjs"), str(public), str(tmp / "props.json"), str(mkv)],
                           cwd=REEL_DIR, capture_output=True, text=True, timeout=1800)
        timings["remotion_seconds"] = round(time.perf_counter() - t0, 2)
        if r.returncode != 0 or not mkv.exists():
            fails.append({"code": "RENDER_FAILED", "detail": (r.stderr or r.stdout)[-1500:]})
            return None, fails, timings
        try:
            timings |= json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            pass
        mp4 = out_dir / "reel.mp4"
        enc = subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(mkv), "-c:v", "copy", "-c:a", "aac",
                              "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(mp4)], capture_output=True, text=True)
        if enc.returncode != 0:
            fails.append({"code": "ENCODE_FAILED", "detail": enc.stderr[-500:]})
            return None, fails, timings
    (out_dir / "reel_props.json").write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")
    return mp4, fails, timings


def render_playwright(scenes: list[dict], pcm: bytes, script: dict, record: dict, p: dict, out_dir: Path,
                      post_emphasis: str | None) -> tuple[Path, list[dict], list[dict], dict]:
    """The fallback renderer: every frame captured by Playwright from one HTML page per scene."""
    from playwright.sync_api import sync_playwright

    by_claim = {s["claim_id"]: s for s in record["sources"]}
    entries = rd.source_entries(record["sources"])
    total = scenes[-1]["end"]
    fails, scene_qa, timings = [], [], {}
    # every text node on screen must sit inside one of these (script lines, or parts built by code)
    norm = lambda s: " ".join(s.replace("²", "2").replace("\u00a0", " ").split())
    pool = [ln["text"] for sc in script["scenes"] for ln in sc["lines"]]
    pool += [rd.DRAFT, "steer", "steerclub.com", "Sources", rd.FOOTNOTE] + [rd.short_source(e) for e in entries]
    pool = [norm(x) for x in pool + [_plain(rd._entry_html(e)) for e in entries]]
    with tempfile.TemporaryDirectory(prefix="steer-reel-") as tmp:
        tmp = Path(tmp)
        for f in ("Fraunces.ttf", "Fraunces-Italic.ttf", "Inter.ttf"):
            (tmp / f).symlink_to(rd.FONTS / f)
        audio_path = tmp / "reel.wav"
        _write_wav(audio_path, pcm)
        pages = []
        for sc in scenes:
            page = end_card_html(entries, p) if sc["kind"] == "sources" else \
                scene_html(sc, p, by_claim, post_emphasis if sc["kind"] == "hook" else None)
            bad = lint_template(page)
            if bad:
                fails.append({"code": "TEMPLATE_LINT", "scene": sc["index"], "detail": bad})
            src = tmp / f"scene-{sc['index']:02}.html"
            src.write_text(page, encoding="utf-8")
            pages.append(src)
        mp4 = out_dir / "reel.mp4"
        enc = subprocess.Popen([ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS),
                                "-c:v", "mjpeg", "-i", "pipe:0", "-i", str(audio_path), "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium", "-r", str(FPS),
                                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", str(mp4)],
                               stdin=subprocess.PIPE)
        t0 = time.perf_counter()
        frames = int(round(total * FPS))
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            k = 0
            for sc, src in zip(scenes, pages):
                page.goto(src.as_uri())
                page.evaluate("document.fonts.ready")
                fit = page.evaluate(rd.FIT_JS)
                page.evaluate(SET_TIME_JS, (sc["end"] - sc["start"]) * 1000)   # everything visible: texts check
                texts = page.evaluate(TEXTS_JS)
                extra = [x for x in texts if not any(norm(x) in s for s in pool)]
                small = [b["px"] for b in fit["boxes"] if b["kind"] in rd.FLOORS and b["px"] < rd.FLOORS[b["kind"]]]
                scene_qa.append({"scene": sc["index"], "kind": sc["kind"], "start": round(sc["start"], 3),
                                 "end": round(sc["end"], 3), "overflow": fit["overflow"], "fit_step": fit["step"],
                                 "extra_text": extra, "below_floor": small})
                if fit["overflow"]:
                    fails.append({"code": "TEXT_OVERFLOW", "scene": sc["index"]})
                if extra:
                    fails.append({"code": "TEXT_NOT_FROM_SCRIPT", "scene": sc["index"], "detail": extra})
                if small:
                    fails.append({"code": "TEXT_BELOW_FLOOR", "scene": sc["index"], "detail": small})
                end_frame = int(round(sc["end"] * FPS)) if sc is not scenes[-1] else frames
                while k < end_frame:
                    page.evaluate(SET_TIME_JS, (k / FPS - sc["start"]) * 1000)
                    enc.stdin.write(page.screenshot(type="jpeg", quality=92))
                    k += 1
            browser.close()
        enc.stdin.close()
        enc.wait()
        timings["capture_and_encode_seconds"] = round(time.perf_counter() - t0, 2)
        timings["frames"] = frames
        if enc.returncode != 0:
            fails.append({"code": "ENCODE_FAILED", "detail": f"ffmpeg exit {enc.returncode}"})
    return mp4, fails, scene_qa, timings


def make_reel(record: dict, script: dict, brand: dict, out_dir: Path, voice: dict, tts=None, transcribe=None,
              post_emphasis: str | None = None, engine: str = "playwright", keys: list[dict] | None = None,
              presenter: dict | None = None) -> dict:
    """Write reel.mp4, reel.srt, reel_transcript.md and reel_trace.json into out_dir; returns the trace (status passed |
    failed). engine: "remotion" (kinetic type, code/reel) or "playwright" (the fallback). presenter (remotion only):
    {scene index: muted clip} for the AI presenter placeholder (D-026)."""
    eleven = voice.get("provider") == "elevenlabs"
    tts = tts or (ElevenLabs() if eleven else edge_tts_voice)
    transcribe = transcribe or whisper_transcribe
    p = brand["palette"]
    by_claim = {s["claim_id"]: s for s in record["sources"]}
    entries = rd.source_entries(record["sources"])
    remotion = engine == "remotion"
    holds = (HEALTH_HOLD if remotion else 0.0, REMOTION_END_CARD if remotion else END_CARD)
    fallback = None
    try:
        scenes, pcm, timings = build_timeline(script, voice, tts, *holds)
    except VoiceError as e:
        out_dir.mkdir(parents=True, exist_ok=True)
        if presenter or not eleven:   # presenter clips move to THIS voice (any provider); edge-tts has no fallback
            trace = {"post_id": record["post_id"], "run_id": record["run_id"], "status": "failed", "engine": engine,
                     "voice": {k: v for k, v in voice.items()},
                     "failures": [{"code": "VOICE_FAILED", "detail": f"{e}; " + ("the reel has presenter clips animated "
                                   "from this voice, so no other voice may replace it" if presenter else "no fallback voice")}]}
            (out_dir / "reel_trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1), encoding="utf-8")
            return trace
        fallback = {"error": str(e), "to": "edge-tts"}   # the whole reel in one voice, never mixed
        voice, tts, eleven = voice | {"provider": "edge-tts"}, edge_tts_voice, False
        scenes, pcm, timings = build_timeline(script, voice, tts, *holds)
    total = scenes[-1]["end"]
    out_dir.mkdir(parents=True, exist_ok=True)
    fails, scene_qa = [], []
    if remotion:
        props = build_props(scenes, record, brand, post_emphasis, keys or [], presenter)
        fails = pool_check(props, script, record, post_emphasis, keys or [])
        mp4 = None
        if not fails:   # nothing is rendered from props that failed the check
            mp4, more, t = render_remotion(props, pcm, out_dir, presenter)
            fails += more
            timings |= t
        scene_qa = [{"scene": i, "kind": sc["kind"], "ground": sc["ground"], "start": sc["start"], "end": sc["end"],
                     "keys": [ln["key"] for ln in sc["lines"] if ln["key"]]} for i, sc in enumerate(props["scenes"])]
    else:
        mp4, fails, scene_qa, t = render_playwright(scenes, pcm, script, record, p, out_dir, post_emphasis)
        timings |= t

    # audio round trip, line by line
    t0 = time.perf_counter()
    rounds, scene_rounds = [], []
    with tempfile.TemporaryDirectory(prefix="steer-stt-") as tmp:
        for sc in scenes:
            for ln in sc["lines"]:
                wav = Path(tmp) / f"line-{sc['index']}-{len(rounds)}.wav"
                _write_wav(wav, resample(ln["pcm"], 16000), 16000)
                r = audio_numbers_match(ln["text"], transcribe(wav))
                rounds.append({"scene": sc["index"], "line": ln["text"]} | r)
                if not r["ok"]:
                    fails.append({"code": r["code"], "scene": sc["index"], "detail": r})
    timings["transcribe_seconds"] = round(time.perf_counter() - t0, 2)

    lo, hi = REMOTION_DURATION if remotion else DURATION
    hook = scenes[0]
    if remotion and hook["kind"] == "hook" and hook["lines"][0]["end"] - hook["start"] > HOOK_MAX + 1e-6:
        fails.append({"code": "HOOK_TOO_LONG", "detail": round(hook["lines"][0]["end"] - hook["start"], 2)})
    probe = probe_mp4(mp4) if mp4 and mp4.exists() else {}
    if probe:
        if (probe["width"], probe["height"]) != (W, H):
            fails.append({"code": "WRONG_SIZE", "detail": probe})
        if abs(probe["fps"] - FPS) > 0.01:
            fails.append({"code": "WRONG_FPS", "detail": probe})
        if not lo <= probe["duration"] <= hi:
            fails.append({"code": "DURATION_OUT_OF_RANGE", "detail": probe["duration"]})
        if abs(probe["duration"] - total) > 0.3:
            fails.append({"code": "AUDIO_TIMELINE_MISMATCH", "detail": {"mp4": probe["duration"], "timeline": total}})
        marks = draft_mark_present(mp4, [total * f for f in (0.1, 0.5, 0.9)], p)
        if not all(marks):
            fails.append({"code": "DRAFT_MARK_MISSING", "detail": marks})
        for sc in scenes:   # the presenter label, on decoded frames across each presenter scene
            if presenter and sc["index"] in presenter:
                times = [sc["start"] + (sc["end"] - sc["start"]) * f for f in (0.05, 0.5, 0.95)]
                seen = [label_present(mp4, x) for x in times]
                if not all(seen):
                    fails.append({"code": "PRESENTER_LABEL_MISSING", "scene": sc["index"], "detail": seen})
        # the round trip again, per scene, on the FINAL audio: decoded from reel.mp4, not from the voice track
        with tempfile.TemporaryDirectory(prefix="steer-stt-") as tmp:
            for sc in scenes:
                if not sc["lines"]:
                    continue
                wav = Path(tmp) / f"scene-{sc['index']}.wav"
                subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-ss", f"{sc['start']:.3f}", "-t",
                                f"{sc['end'] - sc['start']:.3f}", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                               check=True)
                text = " ".join(ln["text"] for ln in sc["lines"])
                r = audio_numbers_match(text, transcribe(wav))
                scene_rounds.append({"scene": sc["index"], "script": text} | r)
                if not r["ok"]:
                    fails.append({"code": "SCENE_" + r["code"], "scene": sc["index"], "detail": r})
    elif not fails:
        fails.append({"code": "NO_VIDEO", "detail": "no reel.mp4 was written"})
    used = sorted({i for sc in script["scenes"] for ln in sc["lines"] for i in ln["claim_ids"]})
    listed = {i for e in entries for i in e["claim_ids"]}
    if [i for i in used if i not in listed]:
        fails.append({"code": "SOURCE_MISSING", "detail": [i for i in used if i not in listed]})
    captions = [c for sc in scenes for c in sc["captions"]]
    short = [c for c in captions if c["end"] - c["start"] < MIN_CAPTION - 1e-6]
    if short and not remotion:   # the srt only, in the kinetic reel (the beats are the on-screen words)
        fails.append({"code": "CAPTION_TOO_SHORT", "detail": short})
    (out_dir / "reel.srt").write_text(srt(captions), encoding="utf-8")
    trace = {
        "post_id": record["post_id"], "run_id": record["run_id"], "status": "failed" if fails else "passed",
        "engine": engine,
        "voice": voice | {"note": "edge-tts is Microsoft's online voice service; the spoken text leaves the box"
                          if voice.get("provider") == "edge-tts" else
                          "ElevenLabs online service; the spoken text leaves the box" if eleven else "",
                          "credit": VOICE_CREDIT if eleven else None, "fallback": fallback,
                          "calls": getattr(tts, "log", None)},
        "transcriber": "whisper.cpp v1.9.1, ggml-base.en (local)", "timings": timings, "probe": probe,
        "scenes": [{"index": sc["index"], "kind": sc["kind"], "start": round(sc["start"], 3), "end": round(sc["end"], 3),
                    "lines": [{"kind": ln["kind"], "text": ln["text"], "spoken": ln["spoken"], "claim_ids": ln["claim_ids"],
                               "start": round(ln["start"], 3), "duration": round(ln["duration"], 3),
                               "claims": [by_claim.get(i) for i in ln["claim_ids"]]} for ln in sc["lines"]]}
                   for sc in scenes],
        "captions": [{"text": c["text"], "start": round(c["start"], 3), "end": round(c["end"], 3)} for c in captions],
        "scene_qa": scene_qa, "audio_round_trip": rounds, "scene_round_trip": scene_rounds, "failures": fails,
    }
    (out_dir / "reel_transcript.md").write_text(transcript_table(scene_rounds), encoding="utf-8")
    cap = out_dir / "caption.txt"
    if eleven and cap.exists() and VOICE_CREDIT not in cap.read_text():   # the voice's credit in the caption footer
        cap.write_text(cap.read_text().rstrip("\n") + "\n\n" + VOICE_CREDIT + "\n", encoding="utf-8")
    (out_dir / "reel_trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return trace
