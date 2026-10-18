"""Render line-art PNGs with p5.brush (headless Chromium, software WebGL2).
   cd code && uv run python lineart/render.py <outdir>   -> slide.png, slide-rust.png, set/<id>.png, set-sheet.png"""
import json, os, sys, tempfile
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(CODE / ".browsers"))
from playwright.sync_api import sync_playwright

INK, CREAM, RUST = "#351D1D", "#F5EBDF", "#C04E2A"
W, H = 1080, 1350
PAGE = f"""<!doctype html><meta charset=utf-8><body style="margin:0">
<script src="{(CODE / 'vendor/p5brush/brush.js').as_uri()}"></script>
<script src="{(CODE / 'lineart/draw.js').as_uri()}"></script>
<script>
brush.createCanvas({W}, {H}, {{parent: document.body, pixelDensity: 1}});
brush.scaleBrushes(2);
window.go = (chai, ink, bg) => drawChai(brush, chai, 1, {W}, {H}, ink, bg);
</script>"""

# p5.brush mixes pigment, so light ink on a dark ground comes out grey. Light-on-dark is drawn as black on white
# and mapped (darkness -> ink colour over bg) on a 2D overlay before the screenshot.
REMAP = """([ink, bg]) => {
  const s = document.querySelector('canvas'), c = document.createElement('canvas'); c.width = s.width; c.height = s.height;
  const x = c.getContext('2d'); x.drawImage(s, 0, 0); const d = x.getImageData(0, 0, c.width, c.height);
  const h = v => [1, 3, 5].map(i => parseInt(v.slice(i, i + 2), 16)), I = h(ink), B = h(bg);
  for (let i = 0; i < d.data.length; i += 4) { const a = 1 - d.data[i] / 255;
    for (let k = 0; k < 3; k++) d.data[i + k] = B[k] + (I[k] - B[k]) * a; d.data[i + 3] = 255; }
  x.putImageData(d, 0, 0); c.id = 'ov'; c.style.cssText = 'position:fixed;left:0;top:0'; document.body.appendChild(c);
}"""

def main(out: Path):
    out = out.resolve(); (out / "set").mkdir(parents=True, exist_ok=True)
    ids = [e["id"] for e in json.loads((CODE / "lineart/index.json").read_text())]
    with tempfile.TemporaryDirectory() as d, sync_playwright() as p:
        f = Path(d) / "p.html"; f.write_text(PAGE)
        b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_page(viewport={"width": W, "height": H})
        pg.on("pageerror", lambda e: print("pageerror:", e))
        pg.goto(f.as_uri())
        def shot(id_, ink, bg, path):
            light = ink == CREAM
            pg.evaluate("([c,i,b]) => go(c,i,b)", [json.loads((CODE / f"lineart/{id_}.json").read_text()),
                                                    "#000000" if light else ink, "#ffffff" if light else bg])
            if light: pg.evaluate(REMAP, [ink, bg])
            pg.screenshot(path=str(path))
            if light: pg.evaluate("document.getElementById('ov').remove()")
        shot("chai", INK, CREAM, out / "slide.png"); shot("chai", CREAM, RUST, out / "slide-rust.png")
        for i in ids: shot(i, INK, CREAM, out / "set" / f"{i}.png")
        cells = "".join(f'<figure><img src="{(out / "set" / (i + ".png")).as_uri()}"><figcaption>{i}</figcaption></figure>' for i in ids)
        sheet = Path(d) / "s.html"
        sheet.write_text(f"""<!doctype html><meta charset=utf-8><style>body{{margin:0;background:{CREAM};width:2000px;
display:grid;grid-template-columns:repeat(5,1fr);gap:20px;padding:20px;box-sizing:border-box}}
figure{{margin:0}}img{{width:100%;display:block}}figcaption{{font:28px sans-serif;color:{INK};text-align:center;padding:8px}}</style>{cells}""")
        pg.set_viewport_size({"width": 2000, "height": 1400}); pg.goto(sheet.as_uri()); pg.wait_for_load_state()
        pg.screenshot(path=str(out / "set-sheet.png"), full_page=True); b.close()

if __name__ == "__main__":
    main(Path(sys.argv[1]))
