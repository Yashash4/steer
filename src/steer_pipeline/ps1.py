"""`steer ps1 build`: the "Guess the number" quiz, built only from verified (or studio-cleared) claims.

Rounds are defined in ps1/rounds.toml by quote fragments, never by typed numbers. The build FAILS when a round's claim
is not usable (unless the round names a fallback gap card, which then replaces it, and the build says so). Every line
of quiz copy goes through the PS2 checks: framing lines (no numbers, no health assertion, publication rules), the
reveal sentences as health lines (numbers in the quote, wording, boundaries, the line cross-check with the paper's
study context), the CTAs as the brand's own lines. Each revealed number must be in its claim's quote. The output folder
holds the page, claims.json, subset fonts and playable.html (one file, no network).
"""

import json
import re
import shutil
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .checks import (_has, check_line, crosscheck_lines, frags, framing_attributes, load_boundaries, split_rule, study_context,
                     writable)
from .numbers import find_numbers
from .posts import claims_from_store, load_brand

PS1 = Path(__file__).resolve().parents[2] / "ps1"
FONTS = Path(__file__).resolve().parents[2] / "fonts"
# fixed, code-defined strings: the only lines allowed the "disclaimer" kind (exempt from STEER_TEAM_CLAIM only) and the
# only qualifiers exempt from a paper's avoid phrases; rounds.toml must use them verbatim, anything else is checked as usual
DISCLAIMERS = {"Educational hackathon prototype · content pending review by a Steer doctor · not a medical assessment",
               "Prototype only. Nothing is sent or stored. Use test details, not real contact information."}
FIXED_QUALIFIERS = {"An average across studies, not a prediction for you.", "All weight-management medicines, not only GLP-1.",
                    "Results varied a lot between studies.", "A comparison between groups in one study, not a prediction for you.",
                    # the safety paper's boundaries rules (class, in trials, a doctor weighs benefits and side effects)
                    "These findings are about this class of medicines, not one product.", "Seen in trials, not in everyday use.",
                    "A doctor weighs the benefits against the side effects for each person."}
# copy that must never appear anywhere in the quiz (the hook asks about the difference, not about "her" risk or a "slim" woman)
FORBIDDEN_COPY = re.compile(r"\bslim\b|\bher risk\b", re.I)
FALLS = re.compile(r"\b(fell|fall|falls|decrease[sd]?|lower|dropped|drop|loss|lost)\b", re.I)


class BuildError(Exception):
    pass


def _values(text: str) -> set:
    vals = set()
    for n in find_numbers(text):
        vals.add(n.low)
        if n.high is not None:
            vals.add(n.high)
    return vals


def number_in_quote(number: str, label: str, quote: str) -> bool:
    """Every value in the revealed number is in the quote. A magnitude may be shown without its minus sign only when
    the label says which way it went ('Absolute lean mass fell' + '1.74 kg' for a quoted -1.74 kg)."""
    have = _values(quote)
    for v in _values(number):
        if v not in have and not (-v in have and v > 0 and FALLS.search(label)):
            return False
    return bool(_values(number))


def _run_for(con, pmcid: str) -> str:
    rows = con.execute("SELECT run_id, document FROM runs JOIN documents USING (document_id) ORDER BY started_at DESC").fetchall()
    for run, doc in rows:
        if json.loads(doc)["citation"].get("pmcid") == pmcid:
            return run
    raise BuildError(f"no stored run for {pmcid}")


def _source(doc: dict, claim: dict) -> dict:
    from .posts import sources
    from .render import journal_display, short_source
    s = sources([claim["claim_id"]], {claim["claim_id"]: claim}, doc)[0]
    return {"short": short_source(s).rsplit(",", 1)[0], "location": s["location"], "doi": s["doi"] or "",
            "full": f"{s['authors']} {s['title']}. {journal_display(s['journal'])}, {s['year']}."}


def build(db: str | Path, out: str | Path, settings: dict, rounds_path: str | Path = PS1 / "rounds.toml",
          router=None, playable: bool = True, fonts: bool = True) -> dict:
    """Write the quiz into `out`; returns a report. router=None skips the line cross-check (offline tests only)."""
    import jsonschema

    spec = tomllib.loads(Path(rounds_path).read_text())
    brand = load_brand()
    con = store.connect(db)
    report = {"rounds": [], "fallbacks": [], "failures": [], "crosscheck": {}}
    papers: dict[str, tuple] = {}
    out_rounds, gaps_used, answered = [], {}, {d["id"]: list(d["answered_by"]) for d in spec["doubt"]}
    gap_ids = {g["id"]: g for g in spec["gap"]}
    lines = []   # (kind, text, claim_ids, where) for the PS2 checks

    openers = {o["id"]: o["text"] for o in spec.get("opener", [])}

    def paper(pm):
        if pm not in papers:
            run = _run_for(con, pm)
            claims, doc = claims_from_store(con, run)
            b = load_boundaries(pm)
            papers[pm] = (run, claims, doc, b, study_context(b, doc))
        return papers[pm]

    for r in spec["round"]:
        run, claims, doc, b, ctx = paper(r["pmcid"])
        hits = sorted((c for c in claims.values() if _has(c["quote"], r["quote_contains"])), key=lambda c: c["claim_id"])
        if not hits:
            raise BuildError(f"round {r['id']}: no stored claim of {r['pmcid']} has the quote fragment {r['quote_contains']!r}")
        c = hits[0]
        if not writable(c, b):
            why = (f"round {r['id']}: claim {run}/{c['claim_id']} is not usable ({c['status']}, review reasons "
                   f"{c.get('review_reasons')}, cross-check {(c.get('crosscheck') or {}).get('verdict')}); it must be "
                   f"verified or cleared in the studio")
            if r.get("fallback_gap") in gap_ids:
                report["fallbacks"].append(why + f" -> gap card {r['fallback_gap']}")
                for d, ids in answered.items():
                    answered[d] = [r["fallback_gap"] if x == r["id"] else x for x in ids]
                continue
            raise BuildError(why)
        # each revealed number in its quote; number and label exact substrings of the checked sentence
        if r["reveal_number"] not in r["sentence"] or r["reveal_label"] not in r["sentence"]:
            raise BuildError(f"round {r['id']}: the reveal number and label must be copied from its sentence")
        if r["opener"] not in openers:
            raise BuildError(f"round {r['id']}: no opener {r['opener']!r}")
        # a round without a number says so in the file (reveal_number = ""); then the sentence must hold no number either
        if not r["reveal_number"]:
            if find_numbers(r["sentence"]):
                raise BuildError(f"round {r['id']}: a round without a revealed number must have a sentence without numbers")
        elif not number_in_quote(r["reveal_number"], r["reveal_label"], c["quote"]):
            raise BuildError(f"round {r['id']}: {r['reveal_number']!r} is not a number in the claim's quote")
        if r.get("context"):
            allowed = _values(c["quote"]) | {v for q in ctx for v in _values(q["quote"])}
            bad = _values(r["context"]) - allowed
            if bad:
                raise BuildError(f"round {r['id']}: context numbers {sorted(map(str, bad))} are not in the quote or the paper's context quotes")
        if r["kind"] == "choice" and (any(r["reveal_number"] and r["reveal_number"] in s[0] for s in r["stops"]) or \
                (isinstance(r["answer"], (int, float)) and any(str(r["answer"]) in s[0] for s in r["stops"]))):
            raise BuildError(f"round {r['id']}: a choice label must never be the answer itself")
        lines.append(("health", r["sentence"], [c["claim_id"]], f"{r['id']}/sentence", r["pmcid"]))
        lines.append(("framing", r["question"], [], f"{r['id']}/question", r["pmcid"]))
        lines.append(("opener", openers[r["opener"]], [], f"{r['id']}/opener", r["pmcid"]))
        lines += [("qualifier" if q in FIXED_QUALIFIERS else "framing", q, [c["claim_id"]], f"{r['id']}/qualifier", r["pmcid"])
                  for q in r.get("qualifiers", [])]
        out_rounds.append({"id": r["id"], "doubt": r["doubt"], "kind": r["kind"], "context": r.get("context"),
                           "question": r["question"], "opener": openers[r["opener"]], "stops": [{"label": s[0], "value": s[1]} for s in r["stops"]],
                           "answer": r["answer"],
                           "reveals": [{"number": r["reveal_number"], "label": r["reveal_label"], "sentence": r["sentence"]}],
                           "qualifiers": r.get("qualifiers", []), "quote": c["quote"], "source": _source(doc, c),
                           "claim_ids": [f"{run}/{c['claim_id']}"]})
        report["rounds"].append(f"{r['id']}: {run}/{c['claim_id']} ({'cleared in the studio' if c.get('s4_cleared') else 'verified'})")
    # the paper's require_with pairs hold across rounds: a round whose partner is missing falls back too (or fails)
    changed = True
    while changed:
        changed = False
        for r in list(out_rounds):
            pm = next(x["pmcid"] for x in spec["round"] if x["id"] == r["id"])
            _, claims, _, b, _ = papers[pm]
            used_quotes = [x["quote"] for x in out_rounds
                           if next(y["pmcid"] for y in spec["round"] if y["id"] == x["id"]) == pm]
            for rule in b.get("require_with", []):
                trig, need = split_rule(rule)
                if not any(_has(r["quote"], f) for e in trig for f in frags(e)):
                    continue
                missing = [e for e in need if not any(_has(q, f) for q in used_quotes for f in frags(e))]
                if missing:
                    spec_r = next(x for x in spec["round"] if x["id"] == r["id"])
                    why = f"round {r['id']}: its pair partner {[frags(e) for e in missing]} is not in the quiz ({rule.get('why', '')})"
                    if spec_r.get("fallback_gap") not in gap_ids:
                        raise BuildError(why)
                    report["fallbacks"].append(why + f" -> gap card {spec_r['fallback_gap']}")
                    out_rounds.remove(r)
                    report["rounds"] = [x for x in report["rounds"] if not x.startswith(r["id"] + ":")]
                    lines[:] = [ln for ln in lines if not ln[3].startswith(r["id"] + "/")]
                    for d, ids in answered.items():
                        answered[d] = [spec_r["fallback_gap"] if x == r["id"] else x for x in ids]
                    changed = True
                    break
            if changed:
                break
    for d, ids in answered.items():   # one gap card per doubt, not the same card twice
        answered[d] = list(dict.fromkeys(ids))
    for ids in answered.values():
        for x in ids:
            if x in gap_ids:
                gaps_used[x] = gap_ids[x]

    # every line of copy through the PS2 checks
    for k, v in spec["ui"].items():
        v = v.replace("{n}", "").replace("{m}", "")
        lines.append(("qualifier" if v in FIXED_QUALIFIERS else "framing", v, [], f"ui/{k}", None))
    for k in ("under", "over", "close", "direction", "meaning_title"):
        lines.append(("framing", spec["readout"][k].replace("{n}", "").replace("{m}", ""), [], f"readout/{k}", None))
    for d in spec["doubt"]:
        lines.append(("framing", d["label"], [], f"doubt/{d['id']}", None))
    for g in gaps_used.values():
        if g["opener"] not in openers:
            raise BuildError(f"gap card {g['id']}: no opener {g['opener']!r}")
        lines += [("framing", g["title"], [], f"{g['id']}/title", None), ("framing", g["body"], [], f"{g['id']}/body", None),
                  ("opener", openers[g["opener"]], [], f"{g['id']}/opener", None)]
        if g.get("context"):   # an info card's context line: the paper's own gate-checked context quote, nothing else
            _, _, _, _, gctx = paper(g["pmcid"])
            if not any(_has(q["quote"], g["context_phrase"]) for q in gctx):
                raise BuildError(f"gap card {g['id']}: the context phrase is not in a context quote of {g['pmcid']}")
            if _values(g["context"]) - {v for q in gctx for v in _values(q["quote"])}:
                raise BuildError(f"gap card {g['id']}: the context line has numbers outside the context quotes")
            if not _has(g["context"], g["context_phrase"]):
                raise BuildError(f"gap card {g['id']}: the context line must contain its context phrase")
    for doubt, qs in spec["doctor_questions"].items():
        lines += [("framing", q, [], f"questions/{doubt}", None) for q in qs]
    for k, v in spec["warm_lead"].items():
        lines.append(("disclaimer" if v in DISCLAIMERS else "framing", v, [], f"warm_lead/{k}", None))
    lines += [("disclaimer" if spec["footer"] in DISCLAIMERS else "framing", spec["footer"], [], "footer", None),
              ("signoff", spec["signoff"], [], "signoff", None),
              ("cta", spec["cta"]["primary"], [], "cta/primary", None), ("cta", spec["cta"]["secondary"], [], "cta/secondary", None)]
    rules = settings["rules"]
    quotes = {x["id"]: x["quote"] for x in out_rounds}
    for kind, text, ids, where, pm in lines:
        _, claims, _, b, _ = papers.get(pm, (None, {}, None, {}, None))
        if FORBIDDEN_COPY.search(text):
            report["failures"].append(f"{where}: FORBIDDEN_COPY ({FORBIDDEN_COPY.search(text).group(0)!r})")
        if kind == "opener":   # daily-life framing: no number, no health fact, no attribute the round's claim does not state
            attrs = framing_attributes(text, quotes.get(where.split("/")[0], ""))
            if attrs:
                report["failures"].append(f"{where}: FRAMING_ADDS_ATTRIBUTE ({attrs})")
        line = {"kind": "framing" if kind in ("qualifier", "disclaimer", "opener") else kind, "text": text, "claim_ids": ids,
                "path": where}
        fails = [f for f in check_line(line, claims, brand, rules, b if kind != "qualifier" else {})
                 if not (kind == "qualifier" and f["code"] == "FRAMING_HAS_NUMBER")
                 # the disclaimer states the review status ("content pending review by a Steer doctor"), like the draft mark
                 and not (kind == "disclaimer" and f["code"] == "STEER_TEAM_CLAIM")]
        if kind == "qualifier" and re.search(r"\d", text.replace("GLP-1", "")):   # the class name, not a number
            fails.append({"code": "QUALIFIER_HAS_NUMBER", "detail": text})
        for f in fails:
            report["failures"].append(f"{where}: {f['code']} ({f.get('detail', '')})")
    if router is not None:
        groups = [(pm, claims, ctx) for pm, (run, claims, doc, b, ctx) in papers.items()] + [(None, {}, ())]
        for pm, claims, ctx in groups:   # the last group: copy tied to no paper (gap cards, questions, UI words)
            ls = [{"kind": k if k not in ("qualifier", "opener") else "framing", "text": t, "claim_ids": i, "path": w}
                  for k, t, i, w, p in lines if p == pm and k in ("health", "framing", "opener")]
            if not ls:
                continue
            res = crosscheck_lines(ls, claims, router, "claude_sonnet", ctx)
            for path, v in res.items():
                report["crosscheck"][path] = f"{v['verdict']} ({v.get('independence')})"
                if v["verdict"] != "supported" or v.get("independence") not in ("different_provider", "different_model_same_provider"):
                    report["failures"].append(f"{path}: cross-check {v['verdict']} ({v.get('independence')}): {v.get('reason')}")
    if report["failures"]:
        raise BuildError("quiz copy failed the checks:\n" + "\n".join(report["failures"]))

    hook = next((r for r in out_rounds if r["id"] == "hook"), None)
    if hook is None:
        raise BuildError("the hook round has no usable claim: the quiz cannot start without it")
    claims_json = {
        "version": "1.1", "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brand": {"palette": brand["palette"], "fonts": {"serif": "Fraunces.woff2", "serif_italic": "Fraunces-Italic.woff2",
                  "sans": "Inter.woff2"}, "wordmark": spec["wordmark"], "signoff": spec["signoff"], "footer": spec["footer"]},
        "ui": spec["ui"], "hook": hook,
        "doubts": [{"id": d["id"], "label": d["label"], "answered_by": answered[d["id"]]} for d in spec["doubt"]],
        "rounds": [r for r in out_rounds if r["id"] != "hook"],
        "gap_cards": [{"id": g["id"], "title": g["title"], "body": g["body"], "opener": openers[g["opener"]],
                       "context": g.get("context")} for g in gaps_used.values()],
        "readout": spec["readout"], "doctor_questions": spec["doctor_questions"],
        "cta": {"primary": {"label": spec["cta"]["primary"], "url": spec["cta"]["url"]},
                "secondary": {"label": spec["cta"]["secondary"], "url": spec["cta"]["url"]}},
        "warm_lead": spec["warm_lead"]}
    jsonschema.validate(claims_json, json.loads((PS1 / "claims.schema.json").read_text()))
    out = Path(out)
    (out / "fonts").mkdir(parents=True, exist_ok=True)
    (out / "claims.json").write_text(json.dumps(claims_json, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in ("index.html", "app.js"):
        shutil.copy(PS1 / f, out / f)
    css = (PS1 / "style.css").read_text()
    css = re.sub(r'url\("fonts/([\w-]+)\.ttf"\) format\("truetype"\)', r'url("fonts/\1.woff2") format("woff2")', css)
    (out / "style.css").write_text(css)
    report["fonts"] = subset_fonts(json.dumps(claims_json, ensure_ascii=False), out / "fonts") if fonts else {}
    if playable:
        r = subprocess.run(["node", str(PS1 / "build_playable.mjs"), str(out / "claims.json"), str(out / "playable.html"),
                            str(out)], capture_output=True, text=True)
        if r.returncode != 0:
            raise BuildError(f"playable build failed: {r.stderr[-500:]}")
        report["playable"] = r.stdout.strip()
    report["files_kb"] = {p.name: round(p.stat().st_size / 1024, 1) for p in sorted(out.rglob("*")) if p.is_file()}
    (out / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def subset_fonts(text: str, out: Path) -> dict:
    """Fraunces and Inter cut to the characters the quiz uses (fontTools via uvx, not a project dependency; OFL allows
    subsetting, and the licence files are copied beside the fonts)."""
    chars = "".join(sorted(set(text) | set(" !\"'(),-.0123456789:;?ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz×·—–")))
    sizes = {}
    for name in ("Fraunces", "Fraunces-Italic", "Inter"):
        dest = out / f"{name}.woff2"
        r = subprocess.run(["uvx", "--from", "fonttools", "--with", "brotli", "pyftsubset", str(FONTS / f"{name}.ttf"),
                            f"--text={chars}", "--flavor=woff2", f"--output-file={dest}", "--layout-features=*"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise BuildError(f"font subsetting failed for {name}: {r.stderr[-300:]}")
        sizes[dest.name] = round(dest.stat().st_size / 1024, 1)
    for lic in ("OFL-Fraunces.txt", "OFL-Inter.txt"):
        shutil.copy(FONTS / lic, out / lic)
    return sizes
