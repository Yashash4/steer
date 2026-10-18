"""Usage (from code/): uv run python ps1/test/offline_check.py ps1/out/playable.html [shots_dir]
Loads the single-file build with every network request blocked and counted; plays the whole quiz;
asserts zero requests, no preselected stop, CTA hrefs equal claims.cta.*.url exactly."""
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
    assert p.inner_text(".q") == claims["hook"]["question"], "hook question missing"
    assert p.locator(".stop.on").count() == 0, "a stop is preselected"
    assert p.locator('[role=slider]').get_attribute("aria-valuenow") is None
    assert p.locator(".btn").first.is_disabled(), "lock enabled before a move"
    snap("01-hook")
    p.locator(".slider").focus(); p.keyboard.press("ArrowRight"); p.keyboard.press("ArrowRight")
    assert p.locator('[role=slider]').get_attribute("aria-valuetext") == claims["hook"]["stops"][1]["label"]
    p.locator(".stop").nth(3).click()
    assert not p.locator(".btn").first.is_disabled(), "lock still disabled after a stop is chosen"
    snap("02-hook-moved")
    p.click(".btn"); p.wait_for_selector(".reveal"); snap("03-reveal")
    p.click(".link"); snap("04-reveal-quote")
    p.click(".btn"); p.wait_for_selector(".chip"); snap("05-picker")
    chips = p.locator(".chip:not(.done)")
    for i in range(chips.count()): chips.nth(i).click()   # every open doubt: rounds and gap cards as built
    snap("06-picked")
    p.click(".btn")
    k = 7
    while True:   # follow the screens this build has, in order
        p.wait_for_timeout(350)
        if p.locator(".warm").count():
            break
        if p.locator("h1.q").count() and p.inner_text("h1.q") == claims["ui"]["readout_title"]:
            snap(f"{k:02}-readout"); k += 1; p.click(".btn"); continue
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
    snap(f"{k:02}-next-step")
    hrefs = [p.locator("a.btn").nth(i).get_attribute("href") for i in (0, 1)]
    assert hrefs == [claims["cta"]["primary"]["url"], claims["cta"]["secondary"]["url"]], hrefs
    assert len(reqs) == 0, f"requests attempted: {reqs}"
    foot = p.inner_text("#foot")
    assert claims["brand"]["footer"] in foot
    ctx.close()
    return len(reqs)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for w, h, tag in ((390, 844, "m"), (1440, 900, "d")) if shots else ((390, 844, "m"),):
        print(tag, "ok, requests attempted:", play(b, w, h, tag))
    b.close()
