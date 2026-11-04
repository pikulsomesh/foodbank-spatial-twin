#!/usr/bin/env python3
"""
Auto-Twin | held-out evaluation of a trained Gaussian splat (scripts/splat_train.py)

Renders the cameras that splat_train.py --holdout N kept out of training and
scores them against the real frames in unmasked pixels only: people and the
broadcast graphic are masked, so the scores cover the static scene the camera
actually saw. PSNR and SSIM are computed over unmasked pixels; for LPIPS
(AlexNet) masked pixels are copied from the frame into the render, so they add
no difference. Writes one JSON with per-frame and mean scores.

Usage:
  scripts/splat_eval.py --scene build/photoreal/racking --ply run/trained.ply --holdout 8 --out run/eval.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import splat_train as st  # noqa: E402


def main(argv=None) -> int:
    import cv2
    import torch
    import lpips
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--ply", type=Path, required=True)
    ap.add_argument("--holdout", type=int, required=True)
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--k-max", type=int, default=128)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--save-renders", type=Path, help="write render|frame pairs here")
    args = ap.parse_args(argv)

    torch.set_grad_enabled(False)
    _, test = st.split_views(st.read_colmap(args.scene / "model"), args.holdout)
    views = st.load_views(args.scene, test, args.width, torch.device("cpu"), fill_weight=0.0)
    g = st.read_ply(args.ply)
    P = {k: torch.tensor(g[f], dtype=torch.float32) for k, f in
         (("means", "xyz"), ("scales", "scale"), ("quats", "rot"), ("opac", "opacity"), ("dc", "dc"))}
    net = lpips.LPIPS(net="alex", verbose=False)
    bg = torch.zeros(3)
    rows = []
    for v in views:
        img, _, _ = st.render(P, {"Rt": v["Rt"], "tt": v["tt"], "K": v["K"]}, v["H"], v["W"], args.k_max, bg)
        img = img.clamp(0, 1)
        gt, m = v["img"], (v["mask"] > 0.5).float()
        n = m.sum() * 3
        mse = (((img - gt) ** 2).sum(-1) * m).sum() / n
        psnr = float(10 * math.log10(1.0 / max(float(mse), 1e-10)))
        ssim = float(st.ssim(img, gt, m))
        comp = img * m[..., None] + gt * (1 - m[..., None])
        lp = float(net(comp.permute(2, 0, 1)[None] * 2 - 1, gt.permute(2, 0, 1)[None] * 2 - 1))
        rows.append(dict(frame=v["name"], psnr=psnr, ssim=ssim, lpips=lp, unmasked=float(m.mean())))
        if args.save_renders:
            args.save_renders.mkdir(parents=True, exist_ok=True)
            pair = (torch.cat([img, gt], 1).numpy() * 255).astype(np.uint8)
            cv2.imwrite(str(args.save_renders / v["name"]), cv2.cvtColor(pair, cv2.COLOR_RGB2BGR))
    mean = {k: float(np.mean([r[k] for r in rows])) for k in ("psnr", "ssim", "lpips", "unmasked")}
    out = dict(scene=str(args.scene), ply=str(args.ply), holdout=args.holdout, width=args.width,
               splats=int(len(g["xyz"])), test_frames=len(rows), mean=mean, frames=rows)
    args.out.write_text(json.dumps(out, indent=1))
    print(json.dumps({"test_frames": len(rows), **mean}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
