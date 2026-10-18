"""LTX-2.5 clips for the reel through a running ComfyUI (127.0.0.1:8188): b-roll and the AI presenter placeholder.

The graph is the one in ~/LTX-2/ltx25_comfy_run.py (the official video_ltx2_5_t2v template: 8 distilled steps at half
size, latent x2 upscale, 3 steps, CFG 1). Two additions follow Lightricks' LTX-2.5_A2V_Two_Stage_Distilled workflow:
- --image: the clip opens on that still (LTXVImgToVideoInplace, 0.7 in stage 1 and 1.0 in stage 2): one reference
  portrait for every presenter scene, so it is the same face throughout.
- --audio: OUR checked TTS WAV, encoded by the audio VAE and FROZEN (noise mask 0) in both stages, so the model moves
  the mouth to it and never rewrites it. The model's audio is never kept: clips are written muted and the reel muxes
  our voice track (the Whisper round trip runs on the final reel).
Modality guidance is off by default (reported to deform mouths, HF Lightricks/LTX-2.5 discussion 44); --modality turns it on.

Usage (one GPU job at a time, under the box's GPU lock):
  python ltx_comfy.py --prompt "..." --out clip.mp4 [--width 768 --height 1280] [--seconds 4] [--seed 7]
                      [--sampler euler|euler_ancestral] [--image ref.png] [--audio scene.wav]
"""
import argparse
import fcntl
import json
import math
import shutil
import subprocess
import time
import urllib.request
import uuid
import wave
from pathlib import Path

HOST = "http://127.0.0.1:8188"
COMFY = Path.home() / "ComfyUI"
LOCK = "/tmp/ltx-wan-gpu.lock"   # ~/LTX-2/wan2.2-test/gpu_lock.sh: one GPU job at a time
DIT = "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
CLIP = "gemma4-12b-ltx-2.5-int8-pinned-20260812.safetensors"   # one encoder revision across every clip
VVAE, AVAE = "ltx-2.5-video-vae-bf16.safetensors", "ltx-2.5-audio-vae-bf16.safetensors"
UPS = "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"
S1 = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
S2 = "0.909375, 0.725, 0.421875, 0.0"
FPS = 24
NEG = ("text, letters, words, captions, subtitles, logo, watermark, numbers, signage, medicine packaging, pills, "
       "white coat, stethoscope, hospital, clinic, distorted face, extra fingers, deformed hands, blurry, cartoon, "
       "video game, ugly, doctor, medical setting, scrubs, lanyard, ID badge, faces, hands, signage, writing, brand logos")


def graph(prompt, w, h, frames, seed, sampler, image=None, audio=None, seconds=None, modality=1.0):
    g = {
        "dit": {"class_type": "UNETLoader", "inputs": {"unet_name": DIT, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "ltxv", "device": "default"}},
        "vvae": {"class_type": "VAELoader", "inputs": {"vae_name": VVAE}},
        "avae": {"class_type": "VAELoader", "inputs": {"vae_name": AVAE}},
        "ups": {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": UPS}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": NEG}},
        "cond": {"class_type": "LTXVConditioning", "inputs": {"positive": ["pos", 0], "negative": ["neg", 0], "frame_rate": FPS}},
        "v1": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": w // 2, "height": h // 2, "length": frames, "batch_size": 1}},
        # modality > 1: LTXVModalityGuidance (an extra pass per step with the audio<->video cross-attention cut, pushing
        # toward the coupled prediction: stronger lip-sync; it runs as a post-CFG step, so it works at CFG 1)
        "mg": {"class_type": "LTXVModalityGuidance", "inputs": {"model": ["dit", 0], "modality_scale": modality,
                                                               "start_percent": 0.0, "end_percent": 1.0}},
        "guide": {"class_type": "CFGGuider", "inputs": {"model": ["mg", 0], "positive": ["cond", 0], "negative": ["cond", 1], "cfg": 1}},
        "ks": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}},
        "sg1": {"class_type": "ManualSigmas", "inputs": {"sigmas": S1}},
        "sg2": {"class_type": "ManualSigmas", "inputs": {"sigmas": S2}},
        "n1": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "n2": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed + 1}},
    }
    v1, v2 = ["v1", 0], None
    if image:
        g["img"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        g["pre"] = {"class_type": "LTXVPreprocess", "inputs": {"image": ["img", 0], "img_compression": 18}}
        g["i1"] = {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["vvae", 0], "image": ["pre", 0], "latent": v1,
                                                                    "strength": 0.7, "bypass": False}}
        v1 = ["i1", 0]
    if audio:
        g["aud"] = {"class_type": "LoadAudio", "inputs": {"audio": audio}}
        g["trim"] = {"class_type": "TrimAudioDuration", "inputs": {"audio": ["aud", 0], "start_index": 0.0, "duration": seconds}}
        g["aenc"] = {"class_type": "LTXVAudioVAEEncode", "inputs": {"audio": ["trim", 0], "audio_vae": ["avae", 0]}}
        g["zero"] = {"class_type": "SolidMask", "inputs": {"value": 0.0, "width": 1024, "height": 1024}}
        g["a"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["aenc", 0], "mask": ["zero", 0]}}   # frozen
    else:
        g["a"] = {"class_type": "LTXVEmptyLatentAudio", "inputs": {"audio_vae": ["avae", 0], "frames_number": frames,
                                                                  "frame_rate": FPS, "batch_size": 1}}
    g |= {
        "av1": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": v1, "audio_latent": ["a", 0]}},
        "s1": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["n1", 0], "guider": ["guide", 0], "sampler": ["ks", 0],
                                                                "sigmas": ["sg1", 0], "latent_image": ["av1", 0]}},
        "sep1": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["s1", 0]}},
        "up": {"class_type": "LTXVLatentUpsampler", "inputs": {"samples": ["sep1", 0], "upscale_model": ["ups", 0], "vae": ["vvae", 0]}},
    }
    v2 = ["up", 0]
    if image:
        g["i2"] = {"class_type": "LTXVImgToVideoInplace", "inputs": {"vae": ["vvae", 0], "image": ["pre", 0], "latent": v2,
                                                                    "strength": 1.0, "bypass": False}}
        v2 = ["i2", 0]
    # stage 2 gets the ORIGINAL frozen audio again, never stage 1's output (as in the official A2V workflow)
    a2 = ["a", 0] if audio else ["sep1", 1]
    g |= {
        "av2": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": v2, "audio_latent": a2}},
        "s2": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["n2", 0], "guider": ["guide", 0], "sampler": ["ks", 0],
                                                                "sigmas": ["sg2", 0], "latent_image": ["av2", 0]}},
        "sep2": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["s2", 0]}},
        "dec": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["sep2", 0], "vae": ["vvae", 0], "tile_size": 512,
                                                          "overlap": 64, "temporal_size": 64, "temporal_overlap": 16}},
        "vid": {"class_type": "CreateVideo", "inputs": {"images": ["dec", 0], "fps": FPS, "bit_depth": 8}},   # no audio: written muted
        "save": {"class_type": "SaveVideo", "inputs": {"video": ["vid", 0], "filename_prefix": "steer/clip", "format": "auto",
                                                       "codec": "auto"}},
    }
    return g


def call(path, data=None):
    req = urllib.request.Request(HOST + path, data=json.dumps(data).encode() if data else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def padded_wav(src: Path, dst: Path, seconds: float) -> None:
    """Our WAV, padded with silence to exactly the clip length (the frozen audio latent spans the whole clip)."""
    with wave.open(str(src)) as r:
        params, pcm = r.getparams(), r.readframes(r.getnframes())
    want = int(round(seconds * params.framerate)) * params.sampwidth * params.nchannels
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(pcm[:want] + b"\0" * max(0, want - len(pcm)))


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--prompt", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--width", type=int, default=768)
    a.add_argument("--height", type=int, default=1280)
    a.add_argument("--seconds", type=float, help="clip length; default 4, or the WAV's length with --audio")
    a.add_argument("--seed", type=int, default=7)
    a.add_argument("--sampler", default="euler_ancestral", choices=["euler", "euler_ancestral"])
    a.add_argument("--image")
    a.add_argument("--audio")
    a.add_argument("--modality", type=float, default=1.0, help="audio->video modality guidance scale (1 = off; reference 3)")
    a = a.parse_args()
    tag = uuid.uuid4().hex[:8]
    seconds = a.seconds or 4.0
    if a.audio and not a.seconds:
        with wave.open(a.audio) as r:
            seconds = r.getnframes() / r.getframerate()
    frames = 1 + 8 * math.ceil(seconds * FPS / 8)   # 8k + 1 frames covering the audio
    clip_s = frames / FPS
    image = audio = None
    inp = COMFY / "input"
    if a.image:
        image = f"steer_{tag}{Path(a.image).suffix}"
        shutil.copyfile(a.image, inp / image)
    if a.audio:
        audio = f"steer_{tag}.wav"
        padded_wav(Path(a.audio), inp / audio, clip_s)
    g = graph(a.prompt, a.width, a.height, frames, a.seed, a.sampler, image, audio, clip_s, a.modality)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        t0 = time.time()
        pid = call("/prompt", {"prompt": g, "client_id": tag})["prompt_id"]
        while True:
            time.sleep(2)
            h = call(f"/history/{pid}")
            if pid in h:
                break
        took = time.time() - t0
    rec = h[pid]
    if rec.get("status", {}).get("status_str") != "success":
        raise SystemExit("FAILED " + json.dumps(rec.get("status", {}).get("messages", []))[-1500:])
    files = [f for o in rec["outputs"].values() for v in o.values() if isinstance(v, list) for f in v
             if isinstance(f, dict) and f.get("filename")]
    src = COMFY / "output" / files[0].get("subfolder", "") / files[0]["filename"]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, a.out)
    meta = {"out": a.out, "seconds": round(took, 1), "frames": frames, "fps": FPS, "clip_s": round(clip_s, 3), "seed": a.seed,
            "sampler": a.sampler, "modality": a.modality, "size": [a.width, a.height], "image": a.image, "audio": a.audio, "prompt": a.prompt,
            "negative": NEG, "transformer": DIT, "text_encoder": CLIP}
    Path(a.out).with_suffix(".json").write_text(json.dumps(meta, indent=1))
    print("DONE", json.dumps(meta))


if __name__ == "__main__":
    main()
