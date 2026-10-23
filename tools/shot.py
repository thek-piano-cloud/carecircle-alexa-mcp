"""Quick visual check: python tools/shot.py <path> <out.png> [width height]"""
import sys
from playwright.sync_api import sync_playwright

url, out = sys.argv[1], sys.argv[2]
w, h = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (1280, 720)
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")
    pg = b.new_page(viewport={"width": w, "height": h})
    pg.goto("http://127.0.0.1:8780" + url)
    pg.wait_for_timeout(1500)
    pg.screenshot(path=out)
    b.close()
