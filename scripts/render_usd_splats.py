#!/usr/bin/env python3
"""Auto-Twin | headless CPU renderer for the twin's OpenUSD Gaussian splats.

Reads every UsdVolParticleField3DGaussianSplat prim in a .usd/.usdc/.usdz stage
(positions, scales, orientations, opacities, degree-0 spherical harmonics),
applies each prim's local-to-world transform, and rasterises the splats with
EWA projection and front-to-back alpha compositing, the same model 3DGS
viewers and Omniverse use. No GPU, no display: it runs in CI or a cloud box.

It exists so the OpenUSD file, not the SPZ the browser loads, is what gets
rendered: if this script draws the twin, any renderer that implements the
ParticleField schema has everything it needs in the same file.

  python scripts/render_usd_splats.py public/site.usdz --shots docs/renders/shots.json --out renders/
  python scripts/render_usd_splats.py public/site.usdz --flythrough docs/renders/flythrough.json --out renders/

Requires: usd-core, numpy, numba, pillow (imageio + imageio-ffmpeg for video).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from numba import njit, prange
from PIL import Image
from pxr import Gf, Usd, UsdGeom

SPLAT_TYPE = "ParticleField3DGaussianSplat"
SH_C0 = 0.28209479177387814


def log(msg: str) -> None:
    print(f"[render] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# USD -> world-space splats
# ---------------------------------------------------------------------------

def quat_to_mat(q: np.ndarray) -> np.ndarray:
    """(N,4) quaternions as (w, x, y, z) -> (N,3,3) rotation matrices (column vectors)."""
    q = q / np.linalg.norm(q, axis=1, keepdims=True)
    w, x, y, z = q.T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1),
    ], 1)


def load_splats(path: Path, include: list[str] | None = None) -> dict[str, np.ndarray]:
    """All splat prims in the stage, merged into one world-space set (USD frame, metres)."""
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise SystemExit(f"cannot open {path}")
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    cache = UsdGeom.XformCache()
    parts = {k: [] for k in ("pos", "cov", "rgb", "opa")}
    for prim in stage.Traverse():
        if prim.GetTypeName() != SPLAT_TYPE:
            continue
        if include and not any(s.lower() in str(prim.GetPath()).lower() for s in include):
            continue
        get = lambda name: prim.GetAttribute(name).Get()
        pos = np.asarray(get("positions"), dtype=np.float64)
        scl = np.asarray(get("scales"), dtype=np.float64)
        opa = np.asarray(get("opacities"), dtype=np.float64)
        quats = get("orientations")
        q = np.array([[r.GetReal(), *r.GetImaginary()] for r in quats], dtype=np.float64) \
            if quats is not None and len(quats) else np.tile([1.0, 0, 0, 0], (len(pos), 1))
        sh = get("radiance:sphericalHarmonicsCoefficients")
        degree = get("radiance:sphericalHarmonicsDegree") or 0
        if sh is not None and len(sh):
            per = (degree + 1) ** 2
            dc = np.asarray(sh, dtype=np.float64).reshape(len(pos), per, 3)[:, 0]
            rgb = np.clip(0.5 + SH_C0 * dc, 0, 1)
        else:
            rgb = np.clip(np.asarray(get("primvars:displayColor"), dtype=np.float64), 0, 1)

        # USD matrices act on row vectors: p_world = p @ M.
        m = np.array(cache.GetLocalToWorldTransform(prim), dtype=np.float64) * mpu
        lin, t = m[:3, :3], m[3, :3]
        world = pos @ lin + t
        r = quat_to_mat(q)                                   # local rotation, column form
        rs = r * scl[:, None, :]                             # R @ diag(s)
        a = np.einsum("ji,njk->nik", lin, rs)                # L^T R S  (L^T maps local->world columns)
        cov = a @ np.transpose(a, (0, 2, 1))
        parts["pos"].append(world); parts["cov"].append(cov)
        parts["rgb"].append(rgb); parts["opa"].append(opa)
        log(f"{prim.GetPath()}: {len(pos):,} splats")
    if not parts["pos"]:
        raise SystemExit(f"no {SPLAT_TYPE} prims in {path}")
    return {k: np.concatenate(v).astype(np.float32) for k, v in parts.items()}


# ---------------------------------------------------------------------------
# Camera + rasteriser
# ---------------------------------------------------------------------------

def look_at(eye, target, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    """World->camera rotation rows (right, up, back); the camera looks down -Z like USD."""
    eye, target, up = (np.asarray(v, dtype=np.float64) for v in (eye, target, up))
    back = eye - target
    back /= np.linalg.norm(back)
    right = np.cross(up, back)
    right /= np.linalg.norm(right)
    return np.stack([right, np.cross(back, right), back])


@njit(cache=True, fastmath=True, parallel=True)
def _project(pos, cov, rot, eye, fx, fy, cx, cy, w, h, near):
    n = pos.shape[0]
    out = np.zeros((n, 7), dtype=np.float32)  # u, v, depth, conic a, b, c, radius
    for i in prange(n):
        d0 = pos[i, 0] - eye[0]; d1 = pos[i, 1] - eye[1]; d2 = pos[i, 2] - eye[2]
        x = rot[0, 0] * d0 + rot[0, 1] * d1 + rot[0, 2] * d2
        y = rot[1, 0] * d0 + rot[1, 1] * d1 + rot[1, 2] * d2
        z = -(rot[2, 0] * d0 + rot[2, 1] * d1 + rot[2, 2] * d2)   # forward depth
        if z < near:
            out[i, 2] = -1.0
            continue
        u = cx + fx * x / z
        v = cy - fy * y / z
        # J = d(u,v)/d(x,y,z) in camera space; v flips y.
        j00 = fx / z; j02 = -fx * x / (z * z)
        j11 = -fy / z; j12 = fy * y / (z * z)
        # camera-space covariance: W Sigma W^T with W = diag(1,1,-1) @ rot
        c = np.empty((3, 3))
        for r in range(3):
            for s in range(3):
                acc = 0.0
                for k in range(3):
                    for l in range(3):
                        acc += rot[r, k] * cov[i, k, l] * rot[s, l]
                c[r, s] = acc
        c[2, 0] = -c[2, 0]; c[0, 2] = -c[0, 2]; c[2, 1] = -c[2, 1]; c[1, 2] = -c[1, 2]
        # 2D covariance = J C J^T, J = [[j00, 0, j02], [0, j11, j12]]
        a = j00 * j00 * c[0, 0] + 2 * j00 * j02 * c[0, 2] + j02 * j02 * c[2, 2] + 0.3
        b = j00 * j11 * c[0, 1] + j00 * j12 * c[0, 2] + j02 * j11 * c[2, 1] + j02 * j12 * c[2, 2]
        cc = j11 * j11 * c[1, 1] + 2 * j11 * j12 * c[1, 2] + j12 * j12 * c[2, 2] + 0.3
        det = a * cc - b * b
        if det <= 0:
            out[i, 2] = -1.0
            continue
        mid = 0.5 * (a + cc)
        lam = mid + math.sqrt(max(0.1, mid * mid - det))
        rad = math.ceil(3.0 * math.sqrt(lam))
        if u + rad < 0 or u - rad >= w or v + rad < 0 or v - rad >= h or rad > 0.5 * max(w, h):
            out[i, 2] = -1.0
            continue
        out[i, 0] = u; out[i, 1] = v; out[i, 2] = z
        out[i, 3] = cc / det; out[i, 4] = -b / det; out[i, 5] = a / det; out[i, 6] = rad
    return out


@njit(cache=True, fastmath=True)
def _composite(order, proj, rgb, opa, w, h, bg):
    img = np.zeros((h, w, 3), dtype=np.float32)
    trans = np.ones((h, w), dtype=np.float32)
    for k in range(order.shape[0]):
        i = order[k]
        u = proj[i, 0]; v = proj[i, 1]; rad = proj[i, 6]
        ca = proj[i, 3]; cb = proj[i, 4]; cc = proj[i, 5]
        x0 = max(0, int(u - rad)); x1 = min(w - 1, int(u + rad))
        y0 = max(0, int(v - rad)); y1 = min(h - 1, int(v + rad))
        o = opa[i]
        for py in range(y0, y1 + 1):
            dy = py + 0.5 - v
            for px in range(x0, x1 + 1):
                t = trans[py, px]
                if t < 1e-4:
                    continue
                dx = px + 0.5 - u
                power = -0.5 * (ca * dx * dx + cc * dy * dy) - cb * dx * dy
                if power > 0:
                    continue
                alpha = min(0.99, o * math.exp(power))
                if alpha < 1.0 / 255.0:
                    continue
                f = alpha * t
                img[py, px, 0] += f * rgb[i, 0]
                img[py, px, 1] += f * rgb[i, 1]
                img[py, px, 2] += f * rgb[i, 2]
                trans[py, px] = t * (1 - alpha)
    for py in range(h):
        for px in range(w):
            t = trans[py, px]
            img[py, px, 0] += t * bg[0]; img[py, px, 1] += t * bg[1]; img[py, px, 2] += t * bg[2]
    return img


def render(splats, eye, target, *, width=1920, height=1080, fov_deg=60.0, near=0.05,
           background=(0.06, 0.07, 0.08)) -> np.ndarray:
    """One frame as an (h, w, 3) float image. fov_deg is horizontal."""
    rot = look_at(eye, target).astype(np.float64)
    fx = 0.5 * width / math.tan(math.radians(fov_deg) / 2)
    proj = _project(splats["pos"], splats["cov"], rot, np.asarray(eye, dtype=np.float64),
                    fx, fx, width / 2, height / 2, width, height, near)
    vis = np.nonzero(proj[:, 2] > 0)[0]
    order = vis[np.argsort(proj[vis, 2], kind="stable")].astype(np.int64)
    return _composite(order, proj, splats["rgb"], splats["opa"], width, height,
                      np.asarray(background, dtype=np.float32))


def save(img: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path, quality=92)


# ---------------------------------------------------------------------------
# Shot lists
# ---------------------------------------------------------------------------

def catmull_rom(points: np.ndarray, samples: int) -> np.ndarray:
    """Smooth path through key points (clamped ends), `samples` evenly spaced in parameter."""
    p = np.vstack([points[0], points, points[-1]])
    segs = len(points) - 1
    out = []
    for s in np.linspace(0, segs, samples, endpoint=False if segs else True):
        i = min(int(s), segs - 1); t = s - i
        p0, p1, p2, p3 = p[i], p[i + 1], p[i + 2], p[i + 3]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                          + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    return np.array(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("usd", type=Path, help=".usd/.usdc/.usdz with ParticleField3DGaussianSplat prims")
    ap.add_argument("--out", type=Path, default=Path("renders"))
    ap.add_argument("--shots", type=Path, help='JSON {"shots": [{name, eye, target, fov?, width?, height?}]}')
    ap.add_argument("--flythrough", type=Path, help="JSON camera path: keys[{eye,target}], seconds, fps")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--scale-video", type=float, default=0.5, help="flythrough resolution factor")
    args = ap.parse_args()
    if not (args.shots or args.flythrough):
        ap.error("give --shots and/or --flythrough")

    t0 = time.time()
    splats = load_splats(args.usd)
    log(f"{len(splats['pos']):,} splats loaded in {time.time() - t0:.1f}s")

    if args.shots:
        for shot in json.loads(args.shots.read_text())["shots"]:
            t = time.time()
            img = render(splats, shot["eye"], shot["target"], width=shot.get("width", args.width),
                         height=shot.get("height", args.height), fov_deg=shot.get("fov", 60.0))
            save(img, args.out / f"{shot['name']}.jpg")
            log(f"{shot['name']}: {time.time() - t:.1f}s")

    if args.flythrough:
        import imageio.v2 as imageio
        spec = json.loads(args.flythrough.read_text())
        fps, n = spec.get("fps", 24), int(spec.get("seconds", 20) * spec.get("fps", 24))
        eyes = catmull_rom(np.array([k["eye"] for k in spec["keys"]], dtype=float), n)
        targets = catmull_rom(np.array([k["target"] for k in spec["keys"]], dtype=float), n)
        w = int(args.width * args.scale_video) // 2 * 2
        h = int(args.height * args.scale_video) // 2 * 2
        path = args.out / f"{spec.get('name', 'flythrough')}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        t = time.time()
        with imageio.get_writer(path, fps=fps, codec="libx264", quality=8, macro_block_size=2) as vid:
            for i, (e, g) in enumerate(zip(eyes, targets)):
                img = render(splats, e, g, width=w, height=h, fov_deg=spec.get("fov", 65.0))
                vid.append_data((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8))
                if i % fps == 0:
                    log(f"frame {i}/{n} ({time.time() - t:.0f}s)")
        log(f"wrote {path} ({n} frames, {w}x{h})")


if __name__ == "__main__":
    main()
