"""Usage (from code/): uv run python ps1/test/offline_check.py ps1/out/playable.html [shots_dir]
Loads the single-file build with every network request blocked and counted; plays the whole quiz;
asserts zero requests, a one-tap hook (no slider, no lock), an opener and "Question N of M" on every ask screen, the\nwarm-lead demo form (exact label, nothing sent, url unchanged), CTA hrefs equal claims.cta.*.url exactly."""
import json, os, sys
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

def play(browser, w, h, tag):
    reqs = []
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
    def gate(route):
        if route.request.url == url: return route.continue_()
        reqs.append(route.request.url); route.abort()
    ctx.route("**/*", gate)
    p = ctx.new_page()
    p.on("request", lambda r: reqs.append(r.url) if r.url != url else None)
    snap = lambda n: shots and (p.wait_for_timeout(450), p.screenshot(path=str(shots / f"{tag}-{n}.png"), full_page=True))
    p.goto(url)
    p.wait_for_selector(".q")
    hook = claims["hook"]
    assert p.inner_text(".q") == hook["question"], "hook question missing"
    # the hook is one tap: choice buttons, no slider, no lock, nothing preselected
    assert p.locator(".slider, [role=slider]").count() == 0, "a slider on the hook"
    assert p.locator(".btn").count() == 0, "a lock/next button on the hook"
    assert p.locator(".choice").count() == len(hook["stops"]) and p.locator(".choice.on").count() == 0
    assert [p.locator(".choice").nth(i).inner_text() for i in range(len(hook["stops"]))] == [s["label"] for s in hook["stops"]]
    assert p.inner_text(".opener") == hook["opener"] and p.locator(".opener").is_visible(), "opener missing"
    assert p.inner_text(".progress") == claims["ui"]["progress_first"].replace("{n}", "1")
    snap("01-hook")
    p.locator(".choice").nth(1).click(); p.wait_for_selector(".reveal"); snap("02-reveal")
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
    k, seen_n = 6, 1
    while True:   # follow the screens this build has, in order
        p.wait_for_timeout(350)
        if p.locator(".warm").count():
            break
        if p.locator("h1.q").count() and p.inner_text("h1.q") == claims["ui"]["readout_title"]:
            snap(f"{k:02}-readout"); k += 1; p.click(".btn"); continue
        if p.locator(".card.gap").count() or (p.locator("h1.q").count() or p.locator(".choice").count()):
            # an ask screen or an info card: opener and "Question N of M", N never above M and counting up
            prog = p.inner_text(".progress")
            n = int(prog.split()[1])
            assert prog == claims["ui"]["progress"].replace("{n}", str(n)).replace("{m}", str(total)), prog
            assert n <= total and n == seen_n + 1, (prog, seen_n)
            seen_n = n
            assert p.locator(".opener").is_visible() and p.inner_text(".opener"), "an opener is missing"
        if p.locator(".card.gap").count():
            snap(f"{k:02}-gap"); k += 1; p.click(".btn"); continue
        if p.locator(".reveal").count():
            snap(f"{k:02}-reveal"); k += 1; p.click(".btn"); continue
        if p.locator(".choice").count():
            # a choice round has no lock: nothing is chosen until a choice is tapped, and the tap is the guess
            assert p.locator(".btn").count() == 0 and p.locator(".choice.on").count() == 0, "a choice round can lock early"
            snap(f"{k:02}-ask"); k += 1; p.locator(".choice").first.click(); continue
        if p.locator(".slider").count():
            assert p.locator(".stop.on").count() == 0, "a stop is preselected"
            assert p.locator(".btn").first.is_disabled(), "lock enabled before a stop is chosen"
            p.locator(".stop").nth(1).click(); snap(f"{k:02}-ask"); k += 1
            assert not p.locator(".btn").first.is_disabled(), "lock still disabled after a stop is chosen"
            p.click(".btn"); continue
        raise AssertionError("an unknown screen")
    assert seen_n == total, (seen_n, total)
    # the warm-lead demo: exact label, no <form>, nothing auto-submitted, nothing sent
    here = p.evaluate("location.href")
    assert p.inner_text(".demo") == claims["warm_lead"]["note"] == "Prototype only. Nothing is sent or stored. Use test details, not real contact information."
    assert p.locator("form").count() == 0
    assert p.locator(".warm .btn").first.is_disabled(), "show-preview enabled before consent"
    snap(f"{k:02}-next-step"); k += 1
    p.locator(".warm input[type=text]").fill("Test Person")
    p.locator(".warm input[type=tel]").fill("0000000000")
    p.locator(".consent input").check()
    p.locator(".warm .btn").first.click()
    prev = p.inner_text(".preview")
    w = claims["warm_lead"]
    for must in (w["preview_title"], w["name_label"] + ": Test Person", w["phone_label"] + ": 0000000000", w["doubts_label"] + ": ",
                 w["guesses_label"] + ": ", claims["ui"]["questions_title"] + ": "):
        assert must in prev, (must, prev)
    p.locator(".warm .btn").nth(1).click(); p.wait_for_timeout(300)
    p.locator(".warm .btn").nth(2).click(); p.wait_for_timeout(300)
    assert p.evaluate("location.href") == here, "the url changed"
    snap(f"{k:02}-form-preview"); k += 1
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
        p.wait_for_selector(".card.gap"); p.click(".btn"); p.wait_for_selector("h1.q")
        assert p.inner_text("h1.q") == claims["ui"]["readout_title"]
        assert p.locator(".card .big").count() == 1, "an info card was scored, or a count is missing"
        assert p.inner_text(".card .big") == claims["readout"]["close"].replace("{n}", "1").replace("{m}", "1")
        snap(f"{k:02}-readout-cards-only"); k += 1
    ctx.close()
    return len(reqs)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for w, h, tag in ((390, 844, "m"), (1440, 900, "d")) if shots else ((390, 844, "m"),):
        print(tag, "ok, requests attempted:", play(b, w, h, tag))
    b.close()
