"""Command line: `steer ingest <input> --role evidence -o doc.json` and `steer verify claims.json --doc doc.json -o report.json`."""

import argparse
import sys
from pathlib import Path

import json

from . import contract, verify
from . import pipeline, posts, store
from .extract import extract
from .router import Router, load_settings
from .fetch import FetchError, fetch
from .pdf import OcrRequired, ingest_pdf
from .text import ingest_text


def ingest(source: str, role: str, allow_retracted: bool = False) -> dict:
    path = Path(source)
    if not path.exists():
        return fetch(source, role, allow_retracted=allow_retracted)  # DOI, PubMed/PMC ID or URL
    if path.suffix.lower() in (".txt", ".md"):
        return ingest_text(path, role)
    if path.suffix.lower() == ".pdf":
        return ingest_pdf(path, role)
    raise SystemExit(f"unsupported input: {source} (files: .pdf, .txt, .md; or a DOI, PubMed/PMC ID or URL)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="steer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="turn a source into a contract document (JSON)")
    p.add_argument("source")
    p.add_argument("--role", required=True, choices=contract.ROLES)
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--allow-retracted", action="store_true", help="ingest a paper PMC marks as retracted (warned)")
    v = sub.add_parser("verify", help="check claims against contract documents")
    v.add_argument("claims", help="JSON list of {claim_id, text, document_id, quote}")
    v.add_argument("--doc", action="append", required=True, help="a contract document (repeatable)")
    v.add_argument("--min-words", type=int, default=verify.MIN_WORDS)
    v.add_argument("-o", "--out", required=True)
    x = sub.add_parser("extract", help="propose claims with a model and keep only those the gate verifies")
    x.add_argument("doc", help="a contract document (role evidence)")
    x.add_argument("--settings", help="model settings file (default: steer.toml)")
    x.add_argument("-o", "--out", required=True)
    r = sub.add_parser("run", help="ingest, extract and verify, label, rules check and store one source")
    r.add_argument("source", help="a PDF or text file, or a DOI, PubMed/PMC ID or URL")
    r.add_argument("--role", default="evidence", choices=contract.ROLES)
    r.add_argument("--settings", help="settings file (default: steer.toml)")
    r.add_argument("--db", help="SQLite file (default: [storage] db in the settings)")
    r.add_argument("--export", help="also write the stored run as JSON to this file")
    w = sub.add_parser("write", help="write one post from a stored run and check every line")
    w.add_argument("--db", required=True)
    w.add_argument("--run", required=True, help="run id")
    w.add_argument("--format", default="carousel", choices=["carousel", "static"])
    w.add_argument("--brief", help="a brief JSON file (default: pick the fullest theme x doubt cell)")
    w.add_argument("--settings")
    w.add_argument("--out", default="out")
    w.add_argument("--no-render", action="store_true", help="skip the PNGs")
    v = sub.add_parser("reel", help="make the reel (MP4, SRT, trace) of a rendered draft post")
    v.add_argument("folder", help="out/<run_id>/<post_id>")
    v.add_argument("--db", required=True, help="the SQLite file the post was written from")
    v.add_argument("--settings")
    v.add_argument("--engine", default="remotion", choices=["remotion", "playwright"],
                   help="remotion: kinetic type (code/reel); playwright: the fallback renderer")
    v.add_argument("--presenter", nargs="*", default=[], metavar="SCENE=CLIP",
                   help="D-026: a muted AI presenter placeholder clip for a scene (remotion), e.g. 0=hook.mp4")
    st = sub.add_parser("studio", help="the review studio: --serve (local, with actions) or --export (read-only copy)")
    st.add_argument("--db", required=True)
    st.add_argument("--posts", nargs="*", default=[], help="folders holding written posts (out/...)")
    st.add_argument("--reviewers", default=str(Path(__file__).resolve().parents[2] / "reviewers.toml"))
    st.add_argument("--static", default=str(Path(__file__).resolve().parent / "studio_ui" / "static"),
                    help="the studio's HTML/CSS/JS folder (default: the bundled one)")
    st.add_argument("--serve", action="store_true")
    st.add_argument("--port", type=int, default=8765)
    st.add_argument("--export", help="write a read-only copy (no actions) to this folder")
    q = sub.add_parser("ps1", help="build the 'Guess the number' quiz from verified or studio-cleared claims")
    q.add_argument("action", choices=["build"])
    q.add_argument("--db", required=True)
    q.add_argument("--out", required=True)
    q.add_argument("--rounds", help="rounds.toml (default: code/ps1/rounds.toml)")
    q.add_argument("--settings")
    q.add_argument("--no-crosscheck", action="store_true", help="skip the line cross-check (offline only; not for a release)")
    d = sub.add_parser("render", help="render (again) the PNGs of a written post folder")
    d.add_argument("folder", help="out/<run_id>/<post_id>")
    args = ap.parse_args(argv)

    if args.cmd == "studio":
        from . import studio
        dirs, static = [Path(d) for d in args.posts], Path(args.static) if args.static else None
        if args.export:
            r = studio.export_site(args.db, dirs, args.reviewers, Path(args.export), static)
            print(f"read-only copy in {args.export}: {r}", file=sys.stderr)
            return 0
        if args.serve:
            studio.serve(args.db, dirs, args.reviewers, args.port, static)
            return 0
        print("say --serve or --export DIR", file=sys.stderr)
        return 2

    if args.cmd == "ps1":
        from . import ps1
        settings = load_settings(args.settings)
        try:
            rep = ps1.build(args.db, args.out, settings, args.rounds or ps1.PS1 / "rounds.toml",
                            router=None if args.no_crosscheck else Router(settings))
        except ps1.BuildError as e:
            print(f"ps1 build FAILED: {e}", file=sys.stderr)
            return 1
        print(json.dumps({k: rep[k] for k in ("rounds", "fallbacks", "fonts", "playable")}, ensure_ascii=False, indent=1),
              file=sys.stderr)
        return 0

    if args.cmd == "reel":
        settings = load_settings(args.settings)
        try:
            t = posts.reel_post(args.folder, store.connect(args.db), settings, engine=args.engine,
                               presenter={int(k): v for k, v in (x.split("=", 1) for x in args.presenter)} or None)
        except posts.ReelRefused as e:
            print(f"refused: {json.dumps(e.reasons, ensure_ascii=False)[:2000]}", file=sys.stderr)
            return 2
        print(f"reel {t['status']}: {t['probe'].get('duration')} s, {t['probe'].get('bytes')} bytes; timings {t['timings']}; "
              f"failures {[f['code'] for f in t['failures']]}", file=sys.stderr)
        return 0 if t["status"] == "passed" else 1

    if args.cmd == "render":
        from .render import qa_line
        r = posts.render_post(args.folder)
        for line in json.loads((Path(args.folder) / "render_qa.json").read_text())["pages"]:
            print(qa_line(line), file=sys.stderr)
        print(f"render {r['status']}" + (f"; sources missing for {r['missing_sources']}" if r["missing_sources"] else ""),
              file=sys.stderr)
        return 0 if r["status"] == "passed" else 1

    if args.cmd == "write":
        settings = load_settings(args.settings)
        brief = json.loads(Path(args.brief).read_text()) if args.brief else None
        rec = posts.write_and_check(store.connect(args.db), args.run, args.format, settings, brief=brief, out_dir=args.out,
                                    render=not args.no_render)
        drawn = rec.get("render")
        print(f"{rec['post_id']}: {rec['status']} ({rec['lines_checked']} lines; writer {rec['writer']['model']}; "
              f"flags {len(rec['flags'])}; layout checks pending {rec['needs_layout_check']}; "
              f"render {drawn['status'] if drawn else 'skipped'}) -> {rec['folder']}", file=sys.stderr)
        return 0 if rec["status"] == "draft_pending_review" and (not drawn or drawn["status"] == "passed") else 1

    if args.cmd == "run":
        settings = load_settings(args.settings)
        try:
            rec = pipeline.run(args.source, args.role, settings)
        except (OcrRequired, FetchError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        con = store.connect(args.db or settings.get("storage", {}).get("db", "steer.db"))
        store.save_run(con, rec)
        if args.export:
            Path(args.export).write_text(json.dumps(store.export_run(con, rec["run_id"]), ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        s, t = rec["summary"], rec["timings"]
        print(f"{rec['run_id']}: {s['proposed']} proposed, {s['passed']} passed, {s['needs_check']} need a check "
              f"{s['needs_check_by_reason']}, {s['rejected']} rejected {s['rejected_by_reason']}, {s['flagged']} flagged; "
              f"labelled {s['labelled']}, rule flags {s['rule_flags']}; verified {s['verified']}, "
              f"cross-check {s['crosscheck']}; {s['model_calls']} model calls; "
              f"time {t}", file=sys.stderr)
        return 0

    if args.cmd == "extract":
        run = extract(contract.load(args.doc), Router(load_settings(args.settings)))
        Path(args.out).write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
        s = run["summary"]
        print(f"{s['proposed']} proposed: {s['passed']} passed, {s['needs_check']} need a check, {s['rejected']} rejected "
              f"{s['rejected_by_reason']}, {s['flagged']} flagged, {s['chunks_failed']} chunks failed -> {args.out}",
              file=sys.stderr)
        return 0

    if args.cmd == "verify":
        claims = json.loads(Path(args.claims).read_text(encoding="utf-8"))
        report = verify.verify(claims, [contract.load(d) for d in args.doc], args.min_words)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        s = report["summary"]
        print(f"{s['claims']} claims: {s['passed']} passed ({s['passed_clean']} clean, {s['needs_layout_check']} need a "
              f"layout check, {s['short_quote']} short quote), {s['rejected']} rejected -> {args.out}", file=sys.stderr)
        return 0 if s["rejected"] == 0 else 1

    try:
        doc = ingest(args.source, args.role, args.allow_retracted)
    except (OcrRequired, FetchError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    contract.save(doc, args.out)
    print(f"{doc['source']['source_id']}: {len(doc['sentences'])} sentences, "
          f"{len(doc['warnings'])} warnings -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
