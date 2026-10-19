"""Usage (from code/): uv run python ps1/test/offline_check.py ps1/out/playable.html [shots_dir]
Loads the single-file build with every network request blocked and counted; plays the whole quiz;
asserts zero requests, a one-tap hook and one tap on every round (no slider, no lock), the 10 s ring (aria-hidden, starts with the choices, dims at zero without locking), deterministic points/streak and result labels, the reveal staging, the grouped end screen with "Worth a chat", the share card made locally on a tap, an opener and "Question N of M" on every ask screen, the\nwarm-lead demo form (exact label, nothing sent, url unchanged), CTA hrefs equal claims.cta.*.url exactly."""
import json, os, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str((HERE.parent.parent / ".browsers")))
from playwright.sync_api import sync_playwright

page_path = Path(sys.argv[1]).resolve()
shots = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
# the claims inlined in the page itself, so the check plays whatever this build contains
_html = page_path.read_text(encoding="utf-8")
claims = json.loads(_html.split('<script type="application/json" id="claims">', 1)[1].split("</script>", 1)[0])
url = page_path.as_uri()
if shots: shots.mkdir(parents=True, exist_ok=True)

def quals_of(r):   # the qualifiers a finding must carry on every surface
    assert r["qualifiers"], r["id"]
    return r["qualifiers"]

def played():      # the rounds a full play (every open doubt picked) guesses
    return [claims["hook"], *claims["rounds"]]

def need_everywhere(text, where, only=None):
    flat = " ".join(text.split()).replace("/m\u00b2", "/m2")   # the page draws kg/m2 as kg/m²
    for r in only or played():
        for q in quals_of(r): assert q in flat, f"{where}: {q!r} missing for {r['id']}"
        for rv in r["reveals"]: assert rv["sentence"] in flat or where.startswith("preview"), f"{where}: {rv['sentence']!r} missing"

def expected(r, idx):   # the result for a tap, recomputed here from the claims (bins: same / one off / further; directions: right or different)
    vals = [s["value"] for s in r["stops"]]
    if isinstance(vals[0], (int, float)):
        d = abs(idx - (sum(1 for v in vals if v <= r["answer"]) - 1))
        return "closest" if d == 0 else "close" if d == 1 else "different"
    return "closest" if vals[idx] == r["answer"] else "different"

def best(r):  # a tap that is the closest range
    return next(i for i in range(len(r["stops"])) if expected(r, i) == "closest")

def worst(r):  # a tap that is "different"
    return next(i for i in range(len(r["stops"])) if expected(r, i) == "different")

def walk_wrapped(p, k, snap):
    """The recap: one card at a time, drawn from claims.json; arrows, Back and Next work; the last Next opens the readout."""
    ui = claims["ui"]
    total = p.locator(".wcard").count()
    assert total in (3, 4), total   # card 3 is skipped when every guess was in range
    at = lambda: p.inner_text(".wcount")
    num = lambda i: at() == ui["wrapped_progress"].replace("{n}", str(i)).replace("{m}", str(total))
    for i in range(1, total + 1):
        assert p.locator(".wcard:visible").count() == 1, "more than one recap card on screen"
        assert num(i), at()
        txt = " ".join(p.inner_text(".wcard:visible").split())
        if i == 1: assert ui["wrapped_title"] in txt and claims["brand"]["wordmark"] in txt
        if i == 2:
            m = re.search(re.escape(ui["wrapped_score"]).replace(r"\{n\}", r"(\d+)").replace(r"\{m\}", r"(\d+)"), txt)
            assert m and p.inner_text(".wbig") == m.group(1) and int(m.group(1)) <= int(m.group(2)), txt
        if total == 4 and i == 3:
            assert ui["wrapped_surprise_title"] in txt and ui["you_guessed"] in txt and ui["study_range"] in txt
            assert any(r["source"]["short"] in txt and all(" ".join(q.split()).replace("/m2", "/m\u00b2") in txt for q in r["qualifiers"]) for r in played()), "caveat or cite missing on the surprise card"
        if i == total:
            assert ui["wrapped_questions"] in txt and 1 <= p.locator(".wq li").count() <= 3
        assert p.locator(".wcard:visible a, .wcard:visible input, .wcard:visible textarea").count() == 0, "contact or link on a recap card"
        snap(f"{k:02}-wrapped-{i}")
        if i == 1:
            p.keyboard.press("ArrowRight"); assert num(2)
            p.keyboard.press("ArrowLeft"); assert num(1)
        if i == 2:
            p.click(".wnav .btn.alt"); assert num(1)
            p.click(".wnav .btn:not(.alt)"); assert num(2)
        p.click(".wnav .btn:not(.alt)")
    p.wait_for_selector(".sharebox", state="attached")
    return k + 1

def play(browser, w, h, tag):
    reqs = []
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    def gate(route):
        if route.request.url == url: return route.continue_()
        reqs.append(route.request.url); route.abort()
    ctx.route("**/*", gate)
    try: ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception: pass
    p = ctx.new_page()
    p.on("request", lambda r: reqs.append(r.url) if r.url != url and not r.url.startswith("blob:") else None)   # blob: is the local share-card download
    p.add_init_script("window.__vib = []; navigator.vibrate = (x) => { window.__vib.push(x); return true; };")
    snap = lambda n: shots and (p.wait_for_timeout(1600), p.screenshot(path=str(shots / f"{tag}-{n}.png"), full_page=True))
    p.goto(url)
    p.wait_for_selector(".q")
    hook = claims["hook"]
    assert p.inner_text(".q") == hook["question"], "hook question missing"
    # the hook is one tap: choice buttons, no slider, no lock, nothing preselected
    assert p.locator(".slider, [role=slider]").count() == 0, "a slider on the hook"
    assert p.locator(".btn").count() == 0, "a lock/next button on the hook"
    assert p.locator(".choice").count() == len(hook["stops"]) and p.locator(".choice.on").count() == 0
    assert [p.locator(".choice").nth(i).inner_text() for i in range(len(hook["stops"]))] == [s["label"] for s in hook["stops"]]
    # order on the hook: opener, headline, context line, guess prompt, buttons
    order = p.evaluate("[...document.querySelector('.screen').children].map(e => e.className.split(' ')[0])")
    assert order == ["progress", "opener", "q", "context", "prompt", "choices"], order
    assert p.inner_text(".prompt") == hook["prompt"]
    assert p.inner_text(".opener") == hook["opener"] and p.locator(".opener").is_visible(), "opener missing"
    assert p.inner_text(".progress") == claims["ui"]["progress_first"].replace("{n}", "1")
    # the ring: a decoration (aria-hidden, no text), drawn with the choices, running; it dims at zero and locks nothing
    assert p.locator(".ring").count() == 1 and p.get_attribute(".ring", "aria-hidden") == "true" and (p.text_content(".ring") or "").strip() == ""
    p.wait_for_function("document.querySelector('.ring').classList.contains('go')")
    assert p.evaluate("getComputedStyle(document.querySelector('.ring .fg')).animationPlayState") == "running"
    assert p.evaluate("document.querySelector('.ring .fg').getAnimations()[0].effect.getTiming().duration") == 10000
    snap("01-hook")
    p.wait_for_timeout(10600)
    assert p.locator(".ring.done").count() == 1, "the ring did not end"
    assert p.locator(".choice").count() == len(hook["stops"]) and p.locator(".choice:disabled").count() == 0 and p.locator(".reveal").count() == 0, "zero must not pick, lock or reveal"
    txt = p.inner_text("main").lower()
    for bad in ("wrong", "time's up", "too late"): assert bad not in txt
    # one tap on the hook, "About 3×": one bin off the 5.3 bin = close estimate, 1 point, streak 0
    hi = 2
    assert expected(hook, hi) == "close"
    p.locator(".choice").nth(hi).click(); p.wait_for_selector(".verdict")
    st = p.evaluate("[...document.querySelectorAll('.final')].map(e => +getComputedStyle(e).opacity)")
    assert st and st[0] < 1, "the verified number is on screen before the animation"
    p.wait_for_timeout(1700)
    assert p.inner_text(".verdict .rres") == claims["ui"]["result_close"] and p.inner_text(".pts") == "+" + str(claims["points"]["close"])
    assert [p.locator(".crow b").nth(i).inner_text() for i in (0, 1)] == [hook["stops"][hi]["label"], hook["stops"][3]["label"]]
    assert p.inner_text(".num") == hook["reveals"][0]["number"] and p.evaluate("+getComputedStyle(document.querySelector('.final')).opacity") == 1
    score, streak, vib = claims["points"]["close"], 0, 0
    hudtxt = lambda s, k: f'{claims["ui"]["score_label"]}: {s} \u00b7 {claims["ui"]["streak_label"]}: {k}'
    assert p.inner_text(".hud") == hudtxt(score, streak), p.inner_text(".hud")
    snap("02-reveal")
    p.click(".link"); snap("03-reveal-quote")
    p.click(".btn"); p.wait_for_selector(".chip"); snap("04-picker")
    chips = p.locator(".chip:not(.done)")
    n_picked = chips.count()
    for i in range(n_picked): chips.nth(i).click()   # every open doubt: rounds and gap cards as built
    snap("05-picked")
    # M = the hook + every step of the picked doubts (rounds and cards)
    steps = []
    for d in claims["doubts"]:
        if d["answered_by"] != ["hook"]: steps += d["answered_by"]
    total = 1 + len(steps)
    p.click(".btn")
    k, seen_n, readout_done, tick = 6, 1, False, 0
    results = {"hook": "close"}   # round id -> the result of the tap, for the end screen
    rmap = {r["id"]: r for r in claims["rounds"]}
    while True:   # follow the screens this build has, in order
        p.wait_for_timeout(350)
        if p.locator(".warm").count():
            break
        if p.locator(".wrapped").count():
            k = walk_wrapped(p, k, snap); continue
        if p.locator("h1.q").count() and p.inner_text("h1.q") == claims["ui"]["readout_title"]:
            if not readout_done:
                body = p.inner_text("main")
                assert claims["readout"]["meaning_title"] in body
                need_everywhere(body, "readout")
                for r in played(): assert r["source"]["short"] in body, "short cite missing on the readout"
                readout_done = True
            flat = " ".join(body.split())
            for gc in claims["gap_cards"]:   # the same context line + cite on the readout, still "Not scored"
                if gc.get("context"):
                    assert gc["context"] in flat and gc["source"]["short"] in flat and claims["ui"]["unscored"] in flat, "readout lost the info card's context"
            # grouped by doubt; "Worth a chat" on exactly the doubts with a "different" result; gap cards never scored
            groups = p.locator(".group")
            want = [d for d in claims["doubts"] if any(i in results or i in [g["id"] for g in claims["gap_cards"]] for i in d["answered_by"])]
            assert groups.count() == len(want), (groups.count(), len(want))
            chat = [d["label"] for d in claims["doubts"] if any(results.get(i) == "different" for i in d["answered_by"])]
            tagged = [groups.nth(i).locator("h3").inner_text() for i in range(groups.count()) if groups.nth(i).locator(".tag").count()]
            assert tagged == chat and all(groups.nth(i).locator(".tag").inner_text() == claims["ui"]["worth_a_chat"] for i in range(groups.count()) if groups.nth(i).locator(".tag").count())
            assert chat, "this play should have a different result"
            nums = [r for r in played() if isinstance(r["stops"][0]["value"], (int, float))]
            ncl = sum(1 for r in nums if results[r["id"]] == "closest")
            assert claims["readout"]["numbers"].replace("{n}", str(ncl)).replace("{m}", str(len(nums))) in body
            assert f'{claims["ui"]["score_label"]}: {score}' in body
            # the share card: nothing before the tap, then a local canvas, a download (no share API here), zero requests
            assert p.locator(".sharecard").count() == 0
            with p.expect_download() as dl:
                p.locator(".btn", has_text=claims["ui"]["share_make"]).click()
                p.wait_for_selector(".sharecard")
            assert dl.value.suggested_filename.endswith(".png")
            cv = p.evaluate("(() => { const c = document.querySelector('.sharecard'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; const s = new Set(); for (let i = 0; i < d.length; i += 4000) s.add(d[i] + ',' + d[i+1] + ',' + d[i+2]); return [c.width, c.height, s.size]; })()")
            assert cv[:2] == [1080, 1350] and cv[2] > 3, cv
            assert len(reqs) == 0, reqs
            snap(f"{k:02}-readout"); k += 1; p.click(".btn:not(.alt)"); continue
        if p.locator(".card.gap").count() or (p.locator("h1.q").count() or p.locator(".choice").count()):
            # an ask screen or an info card: opener and "Question N of M", N never above M and counting up
            prog = p.inner_text(".progress")
            n = int(prog.split()[1])
            assert prog == claims["ui"]["progress"].replace("{n}", str(n)).replace("{m}", str(total)), prog
            assert n <= total and n == seen_n + 1, (prog, seen_n)
            seen_n = n
            assert p.locator(".opener").is_visible() and p.inner_text(".opener"), "an opener is missing"
        if p.locator(".card.gap").count():
            gc = next(x for x in claims["gap_cards"] if x["title"] == p.inner_text(".card.gap h2"))
            if gc.get("context"):   # the info card carries its gate-checked context line and the paper's short cite
                assert gc["context"] in p.inner_text(".card.gap") and gc["source"]["short"] in p.inner_text(".card.gap"), "info card lost its context"
            snap(f"{k:02}-gap"); k += 1; p.click(".btn"); continue
        if p.locator(".verdict").count():
            snap(f"{k:02}-reveal"); k += 1; p.click(".btn"); continue
        if p.locator(".choice").count():
            # a choice round has no lock: nothing is chosen until a choice is tapped, and the tap is the guess
            # every round is one tap on a bin: no slider, no lock, a ring that starts with the choices
            assert p.locator(".btn").count() == 0 and p.locator(".choice.on").count() == 0 and p.locator(".slider, [role=slider]").count() == 0
            assert p.locator(".ring").count() == 1 and p.get_attribute(".ring", "aria-hidden") == "true"
            rid = next(r for r in claims["rounds"] if r["question"] == p.inner_text("h1.q"))
            tap = best(rid) if tick % 2 == 0 else worst(rid)
            res = expected(rid, tap)
            snap(f"{k:02}-ask"); k += 1
            p.locator(".choice").nth(tap).click(); p.wait_for_selector(".verdict"); p.wait_for_timeout(1700)
            assert p.inner_text(".verdict .rres") == claims["ui"]["result_" + res], res
            score += claims["points"][res]; streak = streak + 1 if res == "closest" else 0
            assert p.inner_text(".hud") == hudtxt(score, streak), (p.inner_text(".hud"), score, streak)
            assert p.inner_text(".num" if rid["reveals"][0]["number"] else ".rlabel") in (rid["reveals"][0]["number"] or rid["reveals"][0]["label"])
            results[rid["id"]] = res; tick += 1
            assert p.evaluate("window.__vib.length") == sum(1 for v in results.values() if v == "closest") + 0, "vibrate only on a closest range"
            continue
        raise AssertionError("an unknown screen")
    assert seen_n == total, (seen_n, total)
    # the warm-lead demo: exact label, no <form>, nothing auto-submitted, nothing sent
    here = p.evaluate("location.href")
    assert p.inner_text(".demo") == claims["warm_lead"]["note"] == "Prototype only. Nothing is sent or stored. Use test details, not real contact information."
    assert p.locator("form").count() == 0
    assert p.locator(".warm .btn").first.is_disabled(), "show-preview enabled before consent"
    # the demo form opens BLANK with consent UNTICKED (the shot is taken here, before the test fixture fills it)
    assert [p.locator(".warm input").nth(i).input_value() for i in (0, 1)] == ["", ""] and not p.locator(".consent input").is_checked()
    snap(f"{k:02}-next-step"); k += 1
    p.locator(".warm input[type=text]").fill("Test Person")
    p.locator(".warm input[type=tel]").fill("0000000000")
    p.locator(".consent input").check()
    p.locator(".warm .btn").first.click()
    prev = p.inner_text(".preview")
    w = claims["warm_lead"]
    for must in (w["preview_title"], w["name_label"] + ": Test Person", w["phone_label"] + ": 0000000000", w["doubts_label"] + ": ",
                 w["consent_short"] + ": " + w["yes_label"], w["guesses_label"], claims["ui"]["questions_title"]):
        assert must in prev, (must, prev)
    assert prev.count(w["guesses_label"]) == 1 and prev.count(w["your_guess"] + ": ") == len(played())
    assert "demo only): x" not in prev
    need_everywhere(prev, "preview")
    p.locator(".warm .btn").nth(1).click(); p.wait_for_timeout(300)
    p.locator(".warm .btn").nth(2).click(); p.wait_for_timeout(300)
    try:
        copied = p.evaluate("navigator.clipboard.readText()")
    except Exception:
        copied = None
    assert copied is not None and claims["ui"]["questions_title"] in copied, "the copied text could not be read back"
    need_everywhere(copied, "copied text")
    assert p.evaluate("location.href") == here, "the url changed"
    p.wait_for_timeout(2500)   # the Copied note shows only after a successful copy, for 2 s
    assert not p.locator(".copied").is_visible() and "copied" not in p.inner_text("main").lower(), "the Copied note did not hide"
    hrefs = [p.locator("a.btn").nth(i).get_attribute("href") for i in (0, 1)]
    assert hrefs == [claims["cta"]["primary"]["url"], claims["cta"]["secondary"]["url"]], hrefs
    assert len(reqs) == 0, f"requests attempted: {reqs}"
    foot = p.inner_text("#foot")
    assert claims["brand"]["footer"] in foot
    # a second play with only an info card picked: the card is never scored, the readout counts the completed rounds only
    gap_only = [d for d in claims["doubts"] if d["answered_by"] and all(x not in [r["id"] for r in claims["rounds"]] + ["hook"] for x in d["answered_by"])]
    if gap_only:
        p.locator(".btn.alt").last.click(); p.wait_for_selector(".choice")
        p.locator(".choice").last.click(); p.wait_for_selector(".reveal"); p.click(".btn"); p.wait_for_selector(".chip")
        p.locator(".chip", has_text=gap_only[0]["label"]).click(); p.click(".btn")
        p.wait_for_selector(".card.gap"); p.click(".btn"); p.wait_for_selector(".wrapped"); k = walk_wrapped(p, k, snap); p.wait_for_selector("h1.q")
        assert p.inner_text("h1.q") == claims["ui"]["readout_title"]
        assert p.locator(".card .big").count() == 1, "an info card was scored, or a count is missing"
        assert p.inner_text(".card .big") == claims["readout"]["numbers"].replace("{n}", "1").replace("{m}", "1")
        snap(f"{k:02}-readout-cards-only"); k += 1
    ctx.close()
    return len(reqs)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for w, h, tag in ((390, 844, "m"), (1440, 900, "d")) if shots else ((390, 844, "m"),):
        print(tag, "ok, requests attempted:", play(b, w, h, tag))
    b.close()
