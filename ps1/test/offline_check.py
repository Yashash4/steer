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
claims = json.loads((HERE.parent / "sample" / "claims.json").read_text())
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
    snap("02-hook-moved")
    p.click(".btn"); p.wait_for_selector(".reveal"); snap("03-reveal")
    p.click(".link"); snap("04-reveal-quote")
    p.click(".btn"); p.wait_for_selector(".chip"); snap("05-picker")
    chips = p.locator(".chip:not(.done)")
    for i in (0, 1, 2): chips.nth(i).click()   # safe (gap), muscle (2 rounds), regain
    snap("06-picked")
    p.click(".btn")
    p.wait_for_selector(".card.gap"); snap("07-gap"); p.click(".btn")
    p.wait_for_selector(".choice"); snap("08-muscle-a-ask"); p.locator(".choice").nth(1).click()
    p.wait_for_selector(".reveal"); snap("09-muscle-a-reveal"); p.click(".btn")
    p.wait_for_selector(".choice"); p.locator(".choice").nth(0).click()
    p.wait_for_selector(".reveal"); snap("10-muscle-b-reveal"); p.click(".btn")
    p.wait_for_selector(".slider"); p.locator(".stop").nth(2).click(); snap("11-regain-ask")
    p.click(".btn"); p.wait_for_selector(".reveal"); snap("12-regain-reveal"); p.click(".btn")
    p.wait_for_selector(".big"); snap("13-readout"); p.click(".btn")
    p.wait_for_selector(".warm"); snap("14-next-step")
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
