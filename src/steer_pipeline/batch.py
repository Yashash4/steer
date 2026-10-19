"""`steer batch`: stored runs in, a checked week of draft posts out (S8).

Works only from the STORED runs (--from-store): no extraction, no new clearances. A claim is usable when `checks.writable`
says so (gate pass, independent supported cross-check, clean or cleared in the studio) - the same test PS1 uses.
The planner is deterministic: cells (paper x theme x doubt) with a finding/number claim, ordered by the S6 score
opp(doubt) (learn.score), ties by paper id then theme; one slot per day; no theme on consecutive days; each doubt at most
twice; formats rotate carousel -> static -> reel (so 7 slots hold 3 carousels, 2 statics, exactly 2 reels). When the
constraints cannot be met the conflict is reported and nothing is forced. Each slot is written and checked
(posts.write_and_check), rendered, given a Hinglish caption, and for a reel slot made as a v5 voice-over reel with
b-roll and the pull-back (no presenter). A blocked slot is retried once with the cell's next claim set, then left
visible as "slot unfilled (<codes>)". Every post stays pending doctor review.
"""

import html
import json
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import learn, posts, store
from .checks import load_boundaries, writable
from .write import FINDINGS, pick_brief

REPO = Path(__file__).resolve().parents[3]
BRIEFS = REPO / "code" / "briefs"   # curated briefs (editorial input, like boundaries): <pmcid>.json = {"carousel": brief, "static": brief}
ROTATION = ["carousel", "static", "reel"]
REELS, MAX_DOUBT, N_CLAIMS_DEFAULT = 2, 2, 4
READY = "draft_pending_review"
TZ = "Asia/Kolkata"


def top_line(n_papers: int, n_bench: int) -> str:
    return (f"Planned by `steer batch` from {n_papers} papers; order from the learning loop (benchmark of {n_bench} public "
            f"posts + our own rejections); every post pending doctor review.")


# --- the cells ---

def run_for(con, source: str) -> str:
    """The newest stored run of a source given as PMCID, DOI or the run's own input."""
    want = source.strip().lower()
    for run, inp, doc in con.execute("SELECT run_id, input, document FROM runs JOIN documents USING (document_id) "
                                     "ORDER BY started_at DESC").fetchall():
        cit = json.loads(doc)["citation"]
        if want in {str(x).lower() for x in (inp, cit.get("pmcid"), cit.get("doi")) if x}:
            return run
    raise KeyError(f"no stored run for {source}")


def read_papers(path: str | Path) -> list[str]:
    return [ln.strip() for ln in Path(path).read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]


def load_curated(pmcid: str, folder: Path = BRIEFS) -> dict:
    f = folder / f"{pmcid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def usable_cells(con, sources: list[str], briefs_dir: Path = BRIEFS) -> tuple[list[dict], list[dict]]:
    """(cells, papers). A cell holds the usable claims of one paper x theme x doubt; it can carry a post only with a
    finding/number claim. papers: per paper the run, pmcid, counts."""
    cells, papers = [], []
    for src in sources:
        run = run_for(con, src)
        claims, doc = posts.claims_from_store(con, run)
        pmcid = doc["citation"].get("pmcid") or src
        b = load_boundaries(pmcid)
        usable = {i: c for i, c in claims.items() if writable(c, b)}
        papers.append({"source": src, "pmcid": pmcid, "run_id": run, "stored": len(claims), "usable": len(usable),
                       "cleared_in_studio": sum(1 for c in usable.values() if c.get("s4_cleared"))})
        curated, by = load_curated(pmcid, briefs_dir), {}
        for c in usable.values():
            lab = c["labels"]
            for d in lab["doubts"]:
                if lab["theme"] and d in learn.LONG:
                    by.setdefault((lab["theme"], d), []).append(
                        {"claim_id": c["claim_id"], "claim_type": c.get("claim_type"), "labels": {"theme": lab["theme"], "doubts": [d]}})
        for (theme, d), cs in by.items():
            if any(c["claim_type"] in FINDINGS for c in cs):
                cells.append({"pmcid": pmcid, "run_id": run, "theme": theme, "doubt": d, "short": learn.LONG[d],
                              "claims": sorted(cs, key=lambda c: c["claim_id"]),
                              "curated": curated, "paper_usable": set(usable)})
    return cells, papers


def scores(learn_dir: str | Path) -> tuple[dict, int]:
    """{short doubt: score row}, n benchmark posts - the S6 table, recomputed by learn.score from the loop's own files."""
    d = Path(learn_dir)
    labels, own = json.loads((d / "labels.json").read_text()), json.loads((d / "own_output.json").read_text())
    return {r["doubt"]: r for r in learn.score(labels, own, len(labels))}, len(labels)


# --- the planner ---

def brief_for(cell: dict, used: set, fmt: str, n: int = N_CLAIMS_DEFAULT) -> dict | None:
    """pick_brief on the cell's claims not yet used; None when no finding/number is left."""
    cur = (cell.get("curated") or {}).get(fmt)   # the paper's curated brief for this format (any slot of the family)
    fits = cur and (cur["theme"], cur["doubt"]) == (cell["theme"], cell["doubt"]) and set(cur["claim_ids"]) <= cell.get("paper_usable", set())
    if fits and not set(cur["claim_ids"]) & used:
        return cur | {"brief_source": f"curated brief code/briefs/{cell['pmcid']}.json"}
    src = "automatic brief (next claim set; the curated set is already published this week)" if fits and set(cur["claim_ids"]) & used else "automatic brief"
    left = [c for c in cell["claims"] if c["claim_id"] not in used]
    if not any(c["claim_type"] in FINDINGS for c in left):
        return None
    return pick_brief(left, fmt, n) | {"brief_source": src}


def key(cell: dict) -> tuple:
    return cell["pmcid"], cell["theme"], cell["doubt"]


def check_plan(slots: list[dict], days: int) -> list[str]:
    """Every constraint a plan breaks (empty = valid)."""
    bad, fm = [], [s["format"] for s in slots]
    if len(slots) != days:
        bad.append(f"{len(slots)} slots for {days} days")
    bad += [f"theme {a['theme']} on consecutive days {a['date']} and {b['date']}" for a, b in zip(slots, slots[1:]) if a["theme"] == b["theme"]]
    for d in sorted({s["doubt"] for s in slots}):
        if sum(1 for s in slots if s["doubt"] == d) > MAX_DOUBT:
            bad.append(f"doubt {d} more than {MAX_DOUBT} times")
    bad += [f"no {f} slot" for f in ("carousel", "static") if f not in fm]
    if fm.count("reel") != REELS:
        bad.append(f"{fm.count('reel')} reels, exactly {REELS} wanted")
    return bad


def plan_week(cells: list[dict], score_rows: dict, start: date, days: int = 7, n_claims: int = N_CLAIMS_DEFAULT) -> dict:
    """{slots, conflicts}. Day by day the best-scoring cell that keeps the constraints (depth-first, so a dead end backs up);
    when no full plan exists or a constraint check fails, slots is empty and the conflict says why."""
    opp = lambda c: score_rows.get(c["short"], {}).get("opp", 0.0)
    has_curated = lambda c: any((v["theme"], v["doubt"]) == (c["theme"], c["doubt"]) for v in (c.get("curated") or {}).values())
    # equal scores: a cell with a curated brief first, then paper id
    order = sorted(cells, key=lambda c: (-opp(c), not has_curated(c), c["pmcid"], c["theme"], c["doubt"]))
    fmts = [ROTATION[i % len(ROTATION)] for i in range(days)]
    deepest = [0]

    def dfs(i, picked, used):
        deepest[0] = max(deepest[0], i)
        if i == days:
            return picked
        prev = picked[-1][0]["theme"] if picked else None
        for c in order:
            if c["theme"] == prev or sum(1 for p, _ in picked if p["doubt"] == c["doubt"]) >= MAX_DOUBT:
                continue
            b = brief_for(c, used.get(key(c), set()), fmts[i] if fmts[i] != "reel" else "carousel", n_claims)
            if b is None:
                continue
            got = dfs(i + 1, picked + [(c, b)], used | {key(c): used.get(key(c), set()) | set(b["claim_ids"])})
            if got:
                return got
        return None

    got = dfs(0, [], {})
    if not got:
        return {"slots": [], "conflicts": [f"no plan: the constraints (no theme on consecutive days, each doubt at most "
                                           f"{MAX_DOUBT}x, a finding/number claim per post) cannot fill day {deepest[0] + 1} of {days} "
                                           f"from {len(cells)} cells"]}
    slots, seen = [], {}
    for i, (c, b) in enumerate(got):
        r = score_rows.get(c["short"], {})
        seen[c["short"]] = seen.get(c["short"], 0) + 1
        d = start + timedelta(days=i)
        prev = got[i - 1][0]["theme"] if i else None
        slots.append({"slot": i + 1, "date": d.isoformat(), "weekday": d.strftime("%a"), "format": fmts[i], "pmcid": c["pmcid"],
                      "run_id": c["run_id"], "theme": c["theme"], "doubt": c["doubt"], "short": c["short"], "claims": b["claim_ids"],
                      "opp": r.get("opp"), "brief_source": b["brief_source"], "reason": (
                          f"best-scoring cell still free: {c['pmcid']} {c['theme']} x {c['doubt']}; opp({c['short']}) = "
                          f"{r.get('opp', 0.0):.4f} (benchmark posts {r.get('k', 0)}/{r.get('n', 0)}, our posts {r.get('c', 0)}/{r.get('m', 0)}, "
                          f"flags+rejections {r.get('flags_rejections', 0)}); theme differs from the day before"
                          f"{' (' + prev + ')' if prev else ' (first day)'}; doubt {c['short']} used {seen[c['short']]} of {MAX_DOUBT}; "
                          f"claims {b['claim_ids']}; {b['brief_source']}")})
    bad = check_plan(slots, days)
    return {"slots": [], "conflicts": bad} if bad else {"slots": slots, "conflicts": []}


# --- calendar file (RFC 5545) ---

def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Content lines are at most 75 octets; a longer one is folded with CRLF + one space (never inside a UTF-8 character)."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > (75 if not out else 74):
            out.append(cur)
            cur = ""
        cur += ch
    out.append(cur)
    return "\r\n ".join(out)


def ics(events: list[dict], post_time: str, stamp: datetime | None = None) -> str:
    """events: {date: 'YYYY-MM-DD', uid, summary, description}. One VEVENT at post_time Asia/Kolkata, 30 minutes long."""
    stamp = (stamp or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    hh, mm = (int(x) for x in post_time.split(":"))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Steer//steer batch//EN", "CALSCALE:GREGORIAN",
             "BEGIN:VTIMEZONE", f"TZID:{TZ}", "BEGIN:STANDARD", "DTSTART:19700101T000000", "TZOFFSETFROM:+0530", "TZOFFSETTO:+0530",
             "TZNAME:IST", "END:STANDARD", "END:VTIMEZONE"]
    for e in events:
        start = datetime.fromisoformat(e["date"]).replace(hour=hh, minute=mm)
        f = lambda t: t.strftime("%Y%m%dT%H%M%S")
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}@steer", f"DTSTAMP:{stamp}", f"DTSTART;TZID={TZ}:{f(start)}",
                  f"DTEND;TZID={TZ}:{f(start + timedelta(minutes=30))}", f"SUMMARY:{esc(e['summary'])}",
                  f"DESCRIPTION:{esc(e['description'])}", "STATUS:TENTATIVE", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in lines) + "\r\n"


# --- filling a slot ---

def blocked_codes(rec: dict, folder: Path) -> list[str]:
    codes = [f["code"] for f in rec.get("post_failures", [])]
    t = folder / "trace.json"
    if t.exists():
        codes += [f["code"] for ln in json.loads(t.read_text())["lines"] for f in ln["failures"]]
    return sorted(set(codes)) or [rec["status"].upper()]


def render_codes(folder: Path) -> list[str]:
    q = folder / "render_qa.json"
    codes = [f for p in json.loads(q.read_text())["pages"] for f in p["failures"]] if q.exists() else []
    return sorted(set(codes)) or ["RENDER_FAILED"]


def usage(router) -> dict:
    log = getattr(router, "log", [])
    toks = [a["tokens"] for a in log if a.get("tokens")]
    return {"model_calls": len(log), "calls_ok": sum(1 for a in log if a.get("outcome") == "ok"),
            "input_tokens": sum(t.get("input_tokens") or 0 for t in toks), "output_tokens": sum(t.get("output_tokens") or 0 for t in toks),
            "calls_without_usage": sum(1 for a in log if a.get("outcome") == "ok" and not a.get("tokens"))}


def hook_of(rec: dict) -> str:
    p = rec["post"]
    first = p["slides"][0]["lines"][0] if p.get("slides") else p["static"]["headline"]
    return first["text"]


def fill_slot(slot: dict, cell: dict, used: set, ctx: dict) -> dict:
    """Write (+ checks, render), Hinglish, and for a reel slot the v5 reel. One retry with the cell's next claim set."""
    router, t0, tried, attempts = ctx["make_router"](), time.time(), set(), []   # tried: claims of blocked automatic attempts
    week = ctx["week_dir"]
    out = slot | {"status": "unfilled", "post_id": None, "folder": None, "hinglish": False, "codes": [], "attempts": attempts}
    limit = 3
    for n in (1, 2, 3):
        if n > limit:
            break
        brief = brief_for(cell, used | tried, "carousel" if slot["format"] == "reel" else slot["format"], ctx["n_claims"])
        if brief is None:
            attempts.append({"attempt": n, "codes": ["NO_CLAIMS_LEFT"]})
            break
        allowed = limit = 3 if brief["brief_source"].startswith("curated") else 2   # curated: like the hand runs; automatic: retry once
        att = {"attempt": n, "claims": brief["claim_ids"], "tries_allowed": allowed}
        attempts.append(att)
        if allowed == 2:
            tried |= set(brief["claim_ids"])
        try:
            rec = ctx["write"](ctx["con"], slot["run_id"], brief["format"], ctx["settings"], router=router, brief=brief,
                               out_dir=week / "posts")
        except Exception as e:   # a router chain that failed, a schema error: the slot is blocked, not the week
            att["codes"] = [type(e).__name__]
            continue
        folder = Path(rec["folder"])
        tp = Path(rec["folder"]) / "trace.json"
        if tp.exists():   # the trace says which brief the post was written from
            tr = json.loads(tp.read_text())
            tp.write_text(json.dumps(tr | {"brief_source": brief["brief_source"], "write_try": n, "write_tries_allowed": allowed}, ensure_ascii=False, indent=1), encoding="utf-8")
        att |= {"brief_source": brief["brief_source"], "post_id": rec["post_id"], "folder": str(folder.relative_to(week)), "status": rec["status"]}
        if rec["status"] != READY:
            att["codes"] = blocked_codes(rec, folder)
            continue
        if (rec.get("render") or {}).get("status") != "passed":
            att["codes"] = render_codes(folder)
            continue
        try:
            hi = ctx["hinglish"](folder, ctx["con"], ctx["settings"], router)
        except Exception as e:
            hi = {"status": "failed", "failures": [type(e).__name__]}
        att["hinglish"] = hi
        if slot["format"] == "reel":
            tl = json.loads((folder / "trace.json").read_text())["lines"]   # the first claim the post's own lines use
            main = next((i for ln in tl for i in ln["claim_ids"] if i in {s["claim_id"] for s in rec["sources"]}), rec["sources"][0]["claim_id"])
            try:
                tr = ctx["reel"](folder, ctx["con"], ctx["settings"], engine="remotion",
                                 v4={"broll": {"lane": str(ctx["broll"][slot["slot"] % len(ctx["broll"])])}, "v5": True,
                                     "pullback_claim": f"{slot['pmcid']}/{main}", "db": ctx["db"]})
                att["reel"] = {"status": tr["status"], "pullback_claim": f"{slot['pmcid']}/{main}", "probe": tr.get("probe"),
                               "timings": tr.get("timings"), "failures": [f["code"] for f in tr.get("failures", [])]}
                if tr["status"] != "passed":
                    att["codes"] = att["reel"]["failures"] or ["REEL_FAILED"]
                    continue
            except posts.ReelRefused as e:
                att["reel"] = {"status": "refused"}
                att["codes"] = sorted({r["code"] for r in e.reasons})
                continue
            except Exception as e:
                att["reel"] = {"status": "error"}
                att["codes"] = [type(e).__name__]
                continue
        return out | {"status": "filled", "post_id": rec["post_id"], "folder": att["folder"], "claims": rec["brief"]["claim_ids"],
                      "hinglish": hi["status"] == "passed", "brief_source": brief["brief_source"], "write_try": n, "hook": hook_of(rec), "brief_why": rec["brief"]["why"],
                      "post_status": rec["status"], "codes": [], "wall_seconds": round(time.time() - t0, 1), **usage(router)}
    return out | {"codes": sorted({c for a in attempts for c in a.get("codes", [])}), "wall_seconds": round(time.time() - t0, 1), **usage(router)}


# --- outputs ---

def frame(mp4: Path, png: Path, at: float = 1.5) -> Path:
    from .reel import ffmpeg_exe
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-ss", str(at), "-i", str(mp4), "-frames:v", "1", str(png)], check=True)
    return png


def frames_sheet(mp4: Path, png: Path, duration: float, n: int = 8) -> Path:
    from .reel import ffmpeg_exe
    subprocess.run([ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(mp4), "-vf", f"fps={n}/{duration:.2f},scale=270:-1,tile={n}x1",
                    "-frames:v", "1", str(png)], check=True)
    return png


def first_image(week: Path, s: dict) -> Path | None:
    if s["status"] != "filled":
        return None
    folder = week / s["folder"]
    if s["format"] == "reel":
        return frame(folder / "reel.mp4", folder / "reel-frame.png")
    return folder / json.loads((folder / "post.json").read_text())["render"]["pngs"][0]


def contact_sheet(week: Path, slots: list[dict], out: Path) -> None:
    from playwright.sync_api import sync_playwright
    cols = []
    for s in slots:
        img = first_image(week, s)
        label = f"<b>{html.escape(s['weekday'])} {s['date'][5:]}</b><br>{html.escape(s['format'])}"
        body = (f'<img src="{img.resolve().as_uri()}">' if img else
                f'<div class="blank">slot unfilled<br>({html.escape(", ".join(s["codes"]) or "no plan")})</div>')
        cap = f"{html.escape(s['pmcid'])}<br>{html.escape(s['short'])}" + ("" if img else "<br>unfilled")
        cols.append(f'<div class="c"><div class="l">{label}</div><div class="f">{body}</div><div class="p">{cap}</div></div>')
    page = ("<html><style>@font-face{font-family:I;src:url('" + (REPO / "code/fonts/Inter.ttf").resolve().as_uri() + "')}"
            "body{margin:0;background:#f4eee2;font-family:I,sans-serif;display:flex;gap:10px;padding:14px;width:1980px;box-sizing:border-box}"
            ".c{width:270px}.l{font-size:20px;height:56px}.f{height:480px;border:1px solid #6b1d1d33;display:flex;align-items:center;"
            "justify-content:center;background:#fff}img{max-width:100%;max-height:100%}.blank{font-size:18px;text-align:center;"
            "color:#6b1d1d;border:2px dashed #6b1d1d;padding:30px 10px}.p{font-size:14px;margin-top:6px;height:50px}</style><body>"
            + "".join(cols) + "</body></html>")
    with tempfile.TemporaryDirectory(prefix="steer-sheet-") as tmp, sync_playwright() as pw:
        f = Path(tmp) / "sheet.html"
        f.write_text(page)
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1980, "height": 660})
        pg.goto(f.as_uri())
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(out))
        b.close()


def week_md(week_id: str, header: str, slots: list[dict], conflicts: list[str]) -> str:
    rows = ["| Day | Format | Paper | Doubt | Hook | Status | Post | Brief | Hinglish |", "|---|---|---|---|---|---|---|---|---|"]
    for s in slots:
        ok = s["status"] == "filled"
        rows.append(f"| {s['weekday']} {s['date']} 19:00 IST | {s['format']} | {s['pmcid']} | {s['short']} | "
                    f"{(s.get('hook') or '').replace('|', '/')} | {'pending doctor review' if ok else 'slot unfilled (' + ', '.join(s['codes']) + ')'} | "
                    f"{s['post_id'] + ' (' + s['folder'] + ')' if ok else '-'} | {((s.get('brief_source') or '').replace('code/briefs/', '').replace(' brief ', ' ') + (f" (try {s['write_try']})" if s.get('write_try') else '')) or '-'} | {'yes' if s.get('hinglish') else 'no'} |")
    why = [f"- {s['weekday']} {s['date']}: {s['reason']}" for s in slots]
    return "\n".join([header, "", f"The week starts {date.fromisoformat(week_id).strftime('%a %d %b %Y')}. Posts go out at 19:00 IST (week.ics); this is a draft plan, not a publishing schedule.",
                      "Method: cells ordered by the S6 score opp(doubt); equal scores: a cell with a curated brief first, then paper id; "
                      "one slot per day, no theme on consecutive days, each doubt at most 2 times; formats rotate carousel, static, reel.",
                      "", *rows, "", "Why each slot", "", *why, *(["", "Conflicts", "", *[f"- {c}" for c in conflicts]] if conflicts else []), ""])


def human_time(con, post_ids: list[str]) -> dict:
    from .studio import review_minutes
    mins = review_minutes(store.actions(con), lambda a: a["target"] if a["target_kind"] == "post" else None)
    return {p: (f"{mins[p]} min (timestamp estimate)" if p in mins else "not reviewed yet") for p in post_ids}


def funnel(con, papers: list[dict], slots: list[dict]) -> list[dict]:
    from .studio import review_minutes
    acts = store.actions(con)
    mins = review_minutes(acts, lambda a: a["target"].split("/")[0] if a["target_kind"] == "claim" else None)
    out = []
    for p in papers:
        s = json.loads(con.execute("SELECT summary FROM runs WHERE run_id = ?", (p["run_id"],)).fetchone()[0])
        used = {i for sl in slots if sl["status"] == "filled" and sl["run_id"] == p["run_id"] for i in sl["claims"]}
        out.append(p | {"proposed": s.get("proposed"), "gate_passed": s.get("passed"), "needs_check": s.get("needs_check"),
                        "rejected": s.get("rejected"), "claims_used_in_this_week": len(used),
                        "studio_review_time": f"{mins[p['run_id']]} min (timestamp estimate)" if p["run_id"] in mins else "not reviewed yet"})
    return out


def report(week_id: str, header: str, slots: list[dict], papers: list[dict], con, wall: float) -> tuple[dict, str]:
    ht = human_time(con, [s["post_id"] for s in slots if s["post_id"]])
    rows = [{"slot": s["slot"], "date": s["date"], "format": s["format"], "status": s["status"], "codes": s["codes"], "post_id": s["post_id"],
             **{k: s.get(k) for k in ("model_calls", "input_tokens", "output_tokens", "calls_without_usage", "wall_seconds")},
             "human_time": ht.get(s["post_id"], "not reviewed yet")} for s in slots]
    tot = {k: sum(r[k] or 0 for r in rows) for k in ("model_calls", "input_tokens", "output_tokens", "calls_without_usage")}
    rep = {"week": week_id, "header": header, "wall_seconds": round(wall, 1), "totals": tot,
           "filled": sum(1 for s in slots if s["status"] == "filled"), "papers": papers, "slots": rows,
           "note": "tokens as reported by the connectors; calls_without_usage = successful calls that reported none (claude CLI reports its own); "
                   "model_calls counts every attempt incl. retries and the cross-check; reel TTS/whisper are not model calls here"}
    md = [f"# Run report, week {week_id}", "", header, "", f"Wall time {rep['wall_seconds']} s; {rep['filled']} of {len(slots)} slots filled; model calls "
          f"{tot['model_calls']}; tokens as reported: {tot['input_tokens']} in, {tot['output_tokens']} out "
          f"({tot['calls_without_usage']} successful calls reported none).", "", "## Funnel per paper", "",
          "| paper | stored claims | gate passed (run) | needs check | rejected | proposed | usable | cleared in studio | used this week | studio review time |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {p['pmcid']} | {p['stored']} | {p['gate_passed']} | {p['needs_check']} | {p['rejected']} | {p['proposed']} | {p['usable']} | "
           f"{p['cleared_in_studio']} | {p['claims_used_in_this_week']} | {p['studio_review_time']} |" for p in papers]
    md += ["", "## Slots", "", "| slot | date | format | status | calls | tokens in/out | wall s | human time |", "|---|---|---|---|---|---|---|---|"]
    md += [f"| {r['slot']} | {r['date']} | {r['format']} | {r['status']}{' (' + ', '.join(r['codes']) + ')' if r['codes'] else ''} | {r['model_calls']} | "
           f"{r['input_tokens']}/{r['output_tokens']} | {r['wall_seconds']} | {r['human_time']} |" for r in rows]
    return rep, "\n".join(md) + "\n"


def batch(papers_file: str, week: str, db: str, out: str, settings: dict, days: int = 7, ops: dict | None = None) -> dict:
    """Plan and fill the week; write week.json, week.md, week.ics, contact-sheet.png and the run report under out/week-<date>/."""
    t0 = time.time()
    b = settings.get("batch", {})
    ops = ops or {"write": posts.write_and_check, "hinglish": posts.hinglish_caption, "reel": posts.reel_post, "make_router": None}
    con = store.connect(db)
    cells, papers = usable_cells(con, read_papers(papers_file))
    rows, n_bench = scores(REPO / b.get("learn_dir", "trials/s6-1"))
    header = top_line(len(papers), n_bench)
    plan = plan_week(cells, rows, date.fromisoformat(week), days, settings["writer"].get("brief_claims", N_CLAIMS_DEFAULT))
    wk = Path(out) / f"week-{week}"
    wk.mkdir(parents=True, exist_ok=True)
    ctx = {"con": con, "db": db, "settings": settings, "week_dir": wk, "n_claims": settings["writer"].get("brief_claims", N_CLAIMS_DEFAULT),
           "write": ops["write"], "hinglish": ops["hinglish"], "reel": ops["reel"], "broll": [REPO / p for p in b.get("broll", [])],
           "make_router": ops.get("make_router") or (lambda: __import__("steer_pipeline.router", fromlist=["Router"]).Router(settings))}
    by_key, used, slots = {key(c): c for c in cells}, {}, []
    for s in plan["slots"]:
        c = by_key[(s["pmcid"], s["theme"], s["doubt"])]
        r = fill_slot(s, c, used.get(key(c), set()), ctx)
        if r["status"] == "filled":   # only a published set is used up; a blocked one is free for the next slot
            used.setdefault(key(c), set()).update(r["claims"])
        slots.append(r)
        print(f"slot {s['slot']} {s['date']} {s['format']}: {r['status']} {r['post_id'] or r['codes']}", flush=True)
    events = [{"date": s["date"], "uid": s["post_id"], "summary": s["hook"], "description": "\n".join(
        (wk / s["folder"] / "caption.txt").read_text(encoding="utf-8").split("\n\nSources:")[0].splitlines()[:3] +
        [f"Post folder: {s['folder']}", "Pending doctor review."])} for s in slots if s["status"] == "filled"]
    (wk / "week.ics").write_text(ics(events, b.get("post_time", "19:00")), encoding="utf-8", newline="")
    (wk / "week.json").write_text(json.dumps({"week": week, "header": header, "conflicts": plan["conflicts"], "slots": slots},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    (wk / "week.md").write_text(week_md(week, header, slots, plan["conflicts"]), encoding="utf-8")
    for s in slots:
        if s["format"] == "reel" and s["status"] == "filled":
            tr = json.loads((wk / s["folder"] / "reel_trace.json").read_text())
            frames_sheet(wk / s["folder"] / "reel.mp4", wk / s["folder"] / "frames-sheet.png", tr["probe"]["duration"])
    if slots:
        contact_sheet(wk, slots, wk / "contact-sheet.png")
    rep, md = report(week, header, slots, funnel(con, papers, slots), con, time.time() - t0)
    (wk / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (wk / "report.md").write_text(md, encoding="utf-8")
    return {"week_dir": str(wk), "slots": slots, "conflicts": plan["conflicts"], "report": rep}
