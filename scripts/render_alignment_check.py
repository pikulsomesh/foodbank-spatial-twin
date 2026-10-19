#!/usr/bin/env python3
"""Render public/twin.usda (captures + crop) with the operations footprints drawn over it, to check
the OpenUSD twin and the operations layer share one frame. Writes align_overview.jpg and align_top.jpg.

    python scripts/render_alignment_check.py renders/
"""
import json, math, sys
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw
sys.path.insert(0, "scripts")
import render_usd_splats as R
out = Path(sys.argv[1])
spl = R.load_splats(Path("public/twin.usda"))
ops = json.load(open("src/telemetry/operations.json"))
W, H, fov = 1600, 900, 62
for name, eye, tgt in [("align_overview", (17, 30, 40), (17, 0, 9)), ("align_top", (19, 55, 10.01), (19, 0, 10))]:
    img = R.render(spl, eye, tgt, width=W, height=H, fov_deg=fov)
    im = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    rot = R.look_at(eye, tgt); fx = 0.5 * W / math.tan(math.radians(fov) / 2)
    def proj(p):
        d = np.asarray(p, float) - np.asarray(eye, float)
        x, y, z = rot[0] @ d, rot[1] @ d, -(rot[2] @ d)
        return (W / 2 + fx * x / z, H / 2 - fx * y / z)
    dr = ImageDraw.Draw(im)
    for z in ops["zones"]:
        (x0, x1), (z0, z1) = z["footprint"]["x"], z["footprint"]["z"]
        pts = [proj((x, 0.02, zz)) for x, zz in [(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]]
        dr.line(pts, fill=(255, 80, 160), width=3)
        dr.text(proj(((x0 + x1) / 2, 0, (z0 + z1) / 2)), z["id"], fill=(255, 255, 255))
    pts = [proj((x, 0, zz)) for x, zz in [(0, 0), (26, 0), (26, 20), (0, 20), (0, 0)]]
    dr.line(pts, fill=(255, 255, 0), width=2)
    im.save(out / f"{name}.jpg", quality=88)
    print("saved", name)
