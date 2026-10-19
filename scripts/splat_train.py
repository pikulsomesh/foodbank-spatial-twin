#!/usr/bin/env python3
"""
Auto-Twin | a small 3D Gaussian Splatting trainer in plain PyTorch (CPU or CUDA)

Optimises anisotropic Gaussians against posed video frames, following Kerbl et
al. 2023 (3DGS): EWA projection, depth-sorted front-to-back alpha compositing
per pixel, L1 + D-SSIM loss, and adaptive density control (clone, split,
prune, opacity reset). People and broadcast graphics are masked; under the masks
the loss sees an inpainted guess at low weight, so unseen gaps fill with nearby
colour instead of ghosts or holes.

It exists because the CUDA rasterisers (gsplat, the reference 3DGS code) do not
run without a GPU. Everything here is vectorised tensor code, so the same
script trains on this repo's CPU-only cloud box (hours, at about 480 px) and on
a Colab or AWS GPU (minutes, at full frame size). Colour is SH degree 0, which
is what the browser SPZ and the USD ParticleField carry today.

Input: a scene directory with images/, masks/<image>.png (255 = use) and a
COLMAP text model in model/ (cameras.txt, images.txt, points3D.txt), as written
by scripts/photoreal_twin.py. Output: a standard 3DGS .ply.

Usage:
  scripts/splat_train.py --scene build/photoreal/racking --iters 7000 --out build/photoreal/racking/splat/trained.ply
"""
from __future__ import annotations

import argparse
import gzip
import math
import struct
import time
from pathlib import Path

import numpy as np

SH_C0 = 0.28209479177387814
SPZ_MAGIC = 0x5053474E


def log(msg: str) -> None:
    print(f"[train] {msg}", flush=True)


# ------------------------------------------------------------------ COLMAP io

def _qvec_to_rot(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def read_colmap(model: Path) -> list[dict]:
    """Cameras from a COLMAP text model; R, t map world to camera (OpenCV axes)."""
    intr = {}
    for line in (model / "cameras.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        cid, kind, w, h, prm = int(p[0]), p[1], int(p[2]), int(p[3]), [float(v) for v in p[4:]]
        if kind == "SIMPLE_PINHOLE":
            f, cx, cy, k1 = prm[0], prm[1], prm[2], 0.0
            fx = fy = f
        elif kind == "SIMPLE_RADIAL":
            f, cx, cy, k1 = prm
            fx = fy = f
        elif kind == "PINHOLE":
            fx, fy, cx, cy = prm
            k1 = 0.0
        else:
            raise ValueError(f"unsupported COLMAP camera model {kind}")
        intr[cid] = dict(width=w, height=h, fx=fx, fy=fy, cx=cx, cy=cy, k1=k1)
    lines = [ln for ln in (model / "images.txt").read_text().splitlines() if not ln.startswith("#")]
    cams = []
    for i in range(0, len(lines) - 1, 2):
        p = lines[i].split()
        if len(p) < 10:
            continue
        q, t = np.array([float(v) for v in p[1:5]]), np.array([float(v) for v in p[5:8]])
        R = _qvec_to_rot(q / np.linalg.norm(q))
        cams.append(dict(intr[int(p[8])], name=p[9], R=R, t=t, centre=-R.T @ t))
    return sorted(cams, key=lambda c: c["name"])


def read_points(model: Path):
    xyz, rgb = [], []
    for line in (model / "points3D.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if float(p[7]) > 2.0:  # reprojection error in px
            continue
        xyz.append([float(v) for v in p[1:4]])
        rgb.append([int(v) for v in p[4:7]])
    return np.array(xyz, np.float32), np.array(rgb, np.float32) / 255.0


# ------------------------------------------------------------ quaternion math

def rotmat_to_quat_np(R):
    """(w, x, y, z) for a proper rotation matrix."""
    m = R
    tr = np.trace(m)
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    return np.array(q)


def quat_mul_np(a, b):
    """Hamilton product a * b; a is (4,) or (N, 4), b is (N, 4); (w, x, y, z)."""
    a = np.broadcast_to(a, b.shape)
    w1, x1, y1, z1 = a.T
    w2, x2, y2, z2 = b.T
    return np.stack([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2], 1)


# --------------------------------------------------------------- splat files

PLY_FIELDS = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
              "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]


def write_ply(path: Path, g: dict) -> None:
    """Standard 3DGS binary PLY: f_dc is the SH DC term, scale is log, rot is (w, x, y, z)."""
    n = len(g["xyz"])
    cols = np.concatenate([g["xyz"], np.zeros((n, 3)), g["dc"], g["opacity"][:, None], g["scale"], g["rot"]], 1)
    head = "ply\nformat binary_little_endian 1.0\n" + f"element vertex {n}\n" + \
        "".join(f"property float {k}\n" for k in PLY_FIELDS) + "end_header\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(head.encode())
        fh.write(cols.astype("<f4").tobytes())


def read_ply(path: Path) -> dict:
    raw = path.read_bytes()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    head = raw[:end].decode().splitlines()
    names = [ln.split()[-1] for ln in head if ln.startswith("property")]
    n = int(next(ln for ln in head if ln.startswith("element vertex")).split()[-1])
    a = np.frombuffer(raw[end:end + 4 * n * len(names)], "<f4").reshape(n, len(names)).astype(np.float64)
    col = {k: a[:, i] for i, k in enumerate(names)}
    return {"xyz": np.stack([col["x"], col["y"], col["z"]], 1),
            "dc": np.stack([col[f"f_dc_{i}"] for i in range(3)], 1),
            "opacity": col["opacity"],
            "scale": np.stack([col[f"scale_{i}"] for i in range(3)], 1),
            "rot": np.stack([col[f"rot_{i}"] for i in range(4)], 1)}


def write_spz(path: Path, g: dict, frac_bits: int = 12) -> None:
    """Niantic SPZ v2, SH degree 0, with per-splat anisotropic scale and rotation."""
    n = len(g["xyz"])
    fixed = np.round(g["xyz"] * (1 << frac_bits)).astype(np.int64)
    if np.abs(fixed).max() >= 1 << 23:
        raise ValueError("scene too large for 24-bit SPZ positions; lower frac_bits")
    fixed &= 0xFFFFFF
    pos = np.stack([fixed & 0xFF, (fixed >> 8) & 0xFF, (fixed >> 16) & 0xFF], -1).astype(np.uint8)
    alpha = np.clip(np.round(255.0 / (1.0 + np.exp(-g["opacity"]))), 0, 255).astype(np.uint8)
    colors = np.clip(np.round((g["dc"] * 0.15 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    scales = np.clip(np.round((g["scale"] + 10.0) * 16.0), 0, 255).astype(np.uint8)
    q = g["rot"] / np.linalg.norm(g["rot"], axis=1, keepdims=True)
    q = q * np.where(q[:, :1] < 0, -1.0, 1.0)  # w >= 0, so only x, y, z are stored
    rot = np.clip(np.round(q[:, 1:] * 127.5 + 127.5), 0, 255).astype(np.uint8)
    header = struct.pack("<IIIBBBB", SPZ_MAGIC, 2, n, 0, frac_bits, 0, 0)
    with gzip.open(path, "wb", compresslevel=9) as fh:
        for block in (header, pos, alpha, colors, scales, rot):
            fh.write(block.tobytes() if hasattr(block, "tobytes") else block)


# ------------------------------------------------------------------ renderer

def quat_to_rot(q):
    import torch
    q = torch.nn.functional.normalize(q, dim=-1)
    w, x, y, z = q.unbind(-1)
    return torch.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
                        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
                        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1).view(-1, 3, 3)


def render(P: dict, cam: dict, H: int, W: int, k_max: int, bg):
    """Rasterise all Gaussians into an (H, W, 3) image. Returns the image, the
    visible ids and their 2D means (for densification gradients).

    Sparse formulation: every Gaussian emits one (pixel, Gaussian) pair per
    pixel inside its 3-sigma box, pairs that would add less than 1/255 alpha
    are dropped, the rest are sorted by pixel (stable, so depth order holds)
    and composited with a segmented cumulative sum of log(1 - alpha). This does
    work in proportion to real coverage, which is what makes CPU training usable.
    """
    import torch
    dev = P["means"].device
    R, t = cam["Rt"], cam["tt"]
    fx, fy, cx, cy = cam["K"]
    pc = P["means"] @ R.T + t
    z = pc[:, 2]
    tanx, tany = 0.5 * W / fx * 1.3, 0.5 * H / fy * 1.3
    zc = z.clamp(min=1e-4)
    u0, v0 = fx * pc[:, 0] / zc + cx, fy * pc[:, 1] / zc + cy
    vis = (z > 0.05) & (u0 > -W * 0.5) & (u0 < W * 1.5) & (v0 > -H * 0.5) & (v0 < H * 1.5)
    ids = vis.nonzero().squeeze(1)
    if ids.numel() == 0:
        return bg.expand(H, W, 3).clone(), ids, None

    pc, z = pc[ids], z[ids]
    scale = torch.exp(P["scales"][ids])
    Rg = quat_to_rot(P["quats"][ids])
    M = Rg * scale[:, None, :]
    cov3 = M @ M.transpose(1, 2)
    xz = (pc[:, 0] / z).clamp(-tanx, tanx) * z
    yz = (pc[:, 1] / z).clamp(-tany, tany) * z
    zero = torch.zeros_like(z)
    J = torch.stack([fx / z, zero, -fx * xz / (z * z), zero, fy / z, -fy * yz / (z * z)], -1).view(-1, 2, 3)
    T = J @ R
    cov2 = T @ cov3 @ T.transpose(1, 2)
    a = cov2[:, 0, 0] + 0.3
    b = cov2[:, 0, 1]
    c = cov2[:, 1, 1] + 0.3
    det = (a * c - b * b).clamp(min=1e-10)
    conic = torch.stack([c / det, -b / det, a / det], -1)
    means2d = torch.stack([fx * pc[:, 0] / z + cx, fy * pc[:, 1] / z + cy], -1)
    if means2d.requires_grad:
        means2d.retain_grad()
    opac = torch.sigmoid(P["opac"][ids])
    col = (SH_C0 * P["dc"][ids] + 0.5).clamp(min=0.0)

    with torch.no_grad():
        cap = max(H, W) / 4
        # Extent where alpha falls below 1/255: sqrt(2 ln(255 opacity)) sigma, at most 3 sigma.
        k = torch.sqrt(2 * torch.log((255 * opac).clamp(min=1.0))).clamp(max=3.0)
        rx = torch.ceil(k * torch.sqrt(a)).clamp(max=cap)
        ry = torch.ceil(k * torch.sqrt(c)).clamp(max=cap)
        x0 = (means2d[:, 0] - rx).floor().clamp(0, W - 1).long()
        x1 = (means2d[:, 0] + rx).floor().clamp(0, W - 1).long()
        y0 = (means2d[:, 1] - ry).floor().clamp(0, H - 1).long()
        y1 = (means2d[:, 1] + ry).floor().clamp(0, H - 1).long()
        onscreen = (means2d[:, 0] + rx > 0) & (means2d[:, 0] - rx < W) & \
                   (means2d[:, 1] + ry > 0) & (means2d[:, 1] - ry < H)
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
        cnt = torch.where(onscreen, nx * ny, torch.zeros_like(nx))
        order = torch.argsort(z)  # near to far
        cnt_o = cnt[order]
        gid = torch.repeat_interleave(order, cnt_o)
        start = torch.repeat_interleave(torch.cumsum(cnt_o, 0) - cnt_o, cnt_o)
        local = torch.arange(gid.numel(), device=dev) - start
        del start
        nxg = nx[gid]
        px = x0[gid] + local % nxg
        py = y0[gid] + torch.div(local, nxg, rounding_mode="floor")
        del local, nxg
        dx = px.float() + 0.5 - means2d[gid, 0]
        dy = py.float() + 0.5 - means2d[gid, 1]
        cg = conic[gid]
        pw = -0.5 * (cg[:, 0] * dx * dx + cg[:, 2] * dy * dy) - cg[:, 1] * dx * dy
        live = (opac[gid] * torch.exp(pw.clamp(max=0.0))) > 1.0 / 255
        gid, pix = gid[live], (py * W + px)[live]
        del dx, dy, cg, pw, live, px, py
        pix, perm = torch.sort(pix, stable=True)  # stable keeps depth order inside a pixel
        gid = gid[perm]
        per_pix = torch.bincount(pix, minlength=H * W)
        first = torch.cumsum(per_pix, 0) - per_pix
        keep = (torch.arange(pix.numel(), device=dev) - first[pix]) < k_max
        gid, pix = gid[keep], pix[keep]

    fpx = (pix % W).float() + 0.5
    fpy = torch.div(pix, W, rounding_mode="floor").float() + 0.5
    m = means2d[gid]
    cg = conic[gid]
    dx, dy = fpx - m[:, 0], fpy - m[:, 1]
    power = -0.5 * (cg[:, 0] * dx * dx + cg[:, 2] * dy * dy) - cg[:, 1] * dx * dy
    alpha = (opac[gid] * torch.exp(power.clamp(max=0.0))).clamp(max=0.99)
    l1m = torch.log1p(-alpha).double()
    cs = torch.cumsum(l1m, 0)
    with torch.no_grad():
        seg_first = torch.ones_like(pix, dtype=torch.bool)
        seg_first[1:] = pix[1:] != pix[:-1]
        first_idx = torch.cummax(torch.where(seg_first, torch.arange(pix.numel(), device=dev),
                                             torch.zeros_like(pix)), 0).values
    base = (cs - l1m)[first_idx]
    trans = torch.exp(cs - l1m - base).float()
    w = alpha * trans
    img = torch.zeros(H * W, 3, device=dev).index_add(0, pix, w[:, None] * col[gid])
    log_final = torch.zeros(H * W, device=dev, dtype=torch.float64).index_add(0, pix, l1m)
    img = img + torch.exp(log_final).float()[:, None] * bg
    return img.view(H, W, 3), ids, means2d


def ssim(a, b, mask):
    """Masked SSIM of (H, W, 3) images in [0, 1]."""
    import torch
    import torch.nn.functional as F
    g = torch.exp(-((torch.arange(11, device=a.device) - 5.0) ** 2) / (2 * 1.5 ** 2))
    g = (g / g.sum())
    win = (g[:, None] * g[None, :]).expand(3, 1, 11, 11).contiguous()
    x, y = a.permute(2, 0, 1)[None], b.permute(2, 0, 1)[None]
    f = lambda t: F.conv2d(t, win, padding=5, groups=3)
    mx, my = f(x), f(y)
    sxx, syy, sxy = f(x * x) - mx * mx, f(y * y) - my * my, f(x * y) - mx * my
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    s = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
    m = mask[None, None]
    return (s * m).sum() / (m.sum() * 3 + 1e-8)


# ------------------------------------------------------------------- training

def load_views(scene: Path, cams: list[dict], width: int, device, fill_weight: float = 0.0):
    import cv2
    import torch
    views = []
    for c in cams:
        img = cv2.imread(str(scene / "images" / c["name"]))
        mpath = scene / "masks" / f"{c['name']}.png"
        mask = cv2.imread(str(mpath), 0) if mpath.exists() else np.full(img.shape[:2], 255, np.uint8)
        fpath = scene / "fill" / c["name"]
        fill = cv2.imread(str(fpath)) if fill_weight > 0 and fpath.exists() else None
        K = np.array([[c["fx"], 0, c["cx"]], [0, c["fy"], c["cy"]], [0, 0, 1]])
        if c["k1"]:
            dist = np.array([c["k1"], 0, 0, 0])
            img = cv2.undistort(img, K, dist)
            mask = cv2.undistort(mask, K, dist)
            fill = cv2.undistort(fill, K, dist) if fill is not None else None
        s = width / img.shape[1]
        H = int(round(img.shape[0] * s))
        img = cv2.resize(img, (width, H), interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, (width, H), interpolation=cv2.INTER_AREA)
        keep = mask > 200
        weight = keep.astype(np.float32)
        if fill is not None:  # masked pixels: inpainted guess at low weight instead of nothing
            fill = cv2.resize(fill, (width, H), interpolation=cv2.INTER_AREA)
            img = np.where(keep[..., None], img, fill)
            weight = np.where(keep, 1.0, fill_weight).astype(np.float32)
        views.append(dict(
            name=c["name"], H=H, W=width,
            img=torch.tensor(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), dtype=torch.float32, device=device) / 255.0,
            mask=torch.tensor(weight, dtype=torch.float32, device=device),
            Rt=torch.tensor(c["R"], dtype=torch.float32, device=device),
            tt=torch.tensor(c["t"], dtype=torch.float32, device=device),
            K=(c["fx"] * s, c["fy"] * s, c["cx"] * s, c["cy"] * s)))
    return views


def knn_scale(xyz):
    from scipy.spatial import cKDTree
    d, _ = cKDTree(xyz).query(xyz, k=4)
    return np.log(np.clip(np.sqrt((d[:, 1:] ** 2).mean(1)), 1e-4, None))


class Trainer:
    LR = {"means": 1.6e-4, "scales": 5e-3, "quats": 1e-3, "opac": 5e-2, "dc": 2.5e-3}

    def __init__(self, xyz, rgb, extent, device, max_splats):
        import torch
        self.torch, self.device, self.extent, self.max_splats = torch, device, extent, max_splats
        n = len(xyz)
        s = knn_scale(xyz) + math.log(0.5)  # start smaller: cheaper to rasterise, densification fills gaps
        init = {
            "means": torch.tensor(xyz, dtype=torch.float32),
            "scales": torch.tensor(np.repeat(s[:, None], 3, 1), dtype=torch.float32),
            "quats": torch.tensor(np.tile([1.0, 0, 0, 0], (n, 1)), dtype=torch.float32),
            "opac": torch.full((n,), math.log(0.1 / 0.9)),
            "dc": torch.tensor((rgb - 0.5) / SH_C0, dtype=torch.float32),
        }
        self.P = {k: v.to(device).requires_grad_(True) for k, v in init.items()}
        self.opt = torch.optim.Adam([{"params": [v], "name": k, "lr": self.LR[k] * (extent if k == "means" else 1)}
                                     for k, v in self.P.items()], eps=1e-15)
        self.reset_stats()

    def reset_stats(self):
        n = len(self.P["means"])
        self.grad_acc = self.torch.zeros(n, device=self.device)
        self.grad_cnt = self.torch.zeros(n, device=self.device)

    def _replace(self, keep, extra: dict):
        """Keep rows `keep` and append `extra`, carrying Adam state (zeros for new rows)."""
        torch = self.torch
        for group in self.opt.param_groups:
            k = group["name"]
            old = group["params"][0]
            new = torch.cat([old.detach()[keep], extra[k]], 0).requires_grad_(True)
            st = self.opt.state.pop(old, None)
            if st:
                for key in ("exp_avg", "exp_avg_sq"):
                    st[key] = torch.cat([st[key][keep], torch.zeros_like(extra[k])], 0)
                self.opt.state[new] = st
            group["params"][0] = new
            self.P[k] = new
        self.reset_stats()

    def densify(self, grad_thresh, prune_big: bool):
        torch = self.torch
        P = self.P
        with torch.no_grad():
            g = self.grad_acc / self.grad_cnt.clamp(min=1)
            smax = torch.exp(P["scales"]).max(1).values
            hot = g > grad_thresh
            room = self.max_splats - len(g)
            clone = hot & (smax <= 0.01 * self.extent)
            split = hot & (smax > 0.01 * self.extent)
            if room <= 0:
                clone &= False
                split &= False
            elif clone.sum() + split.sum() > room:  # keep the hottest that fit
                cut = torch.quantile(g[hot], 1 - room / float(hot.sum()))
                clone &= g >= cut
                split &= g >= cut
            extra = {k: v.detach()[clone] for k, v in P.items()}
            # Split: two children sampled inside the parent, 1.6x smaller.
            sel = split.nonzero().squeeze(1)
            sc = torch.exp(P["scales"][sel])
            Rg = quat_to_rot(P["quats"][sel])
            kids = []
            for _ in range(2):
                off = (Rg @ (torch.randn_like(sc) * sc)[..., None]).squeeze(-1)
                kids.append({"means": P["means"][sel] + off, "scales": P["scales"][sel] - math.log(1.6),
                             "quats": P["quats"][sel], "opac": P["opac"][sel], "dc": P["dc"][sel]})
            extra = {k: torch.cat([extra[k]] + [kd[k].detach() for kd in kids], 0) for k in P}
            prune = (torch.sigmoid(P["opac"]) < 0.005) | split
            if prune_big:
                prune |= smax > 0.1 * self.extent
            self._replace(~prune, extra)
        return int(clone.sum()), int(split.sum()), int(prune.sum())

    def reset_opacity(self):
        torch = self.torch
        with torch.no_grad():
            self.P["opac"].clamp_(max=math.log(0.01 / 0.99))
        st = self.opt.state.get(self.P["opac"])
        if st:
            st["exp_avg"].zero_()
            st["exp_avg_sq"].zero_()


def hull_mask(P: dict, views, min_views: int = 5, frac: float = 0.5):
    """True for Gaussians inside the people's visual hull: their centre lands on a
    masked pixel in most of the views that see it. Nothing real can sit there (the
    hosts were there), so such Gaussians only explain the masked pixels as haze in
    front of the shelving; carving them forces the background plates to be
    explained at the depth of the background instead."""
    import torch
    with torch.no_grad():
        xyz = P["means"]
        hit = torch.zeros(len(xyz), device=xyz.device)
        seen = torch.zeros(len(xyz), device=xyz.device)
        for v in views:
            pc = xyz @ v["Rt"].T + v["tt"]
            z = pc[:, 2].clamp(min=1e-6)
            fx, fy, cx, cy = v["K"]
            u = (fx * pc[:, 0] / z + cx).long()
            w = (fy * pc[:, 1] / z + cy).long()
            ok = (pc[:, 2] > 0.05) & (u >= 0) & (u < v["W"]) & (w >= 0) & (w < v["H"])
            masked = v["mask"][w[ok], u[ok]] < 0.999
            seen[ok] += 1
            hit[ok] += masked.float()
        return (seen >= min_views) & (hit / seen.clamp(min=1) > frac)


def train(args) -> None:
    import torch
    torch.manual_seed(0)
    np.random.seed(0)
    if args.threads:
        torch.set_num_threads(args.threads)
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    cams = read_colmap(args.scene / "model")
    xyz, rgb = read_points(args.scene / "model")
    centres = np.stack([c["centre"] for c in cams])
    extent = float(np.linalg.norm(centres - centres.mean(0), axis=1).max() * 1.1) or 1.0
    width = args.width or (cams[0]["width"] if device.type == "cuda" else 480)
    log(f"{len(cams)} cameras, {len(xyz):,} SfM points, extent {extent:.2f}, device {device}, width {width}px")
    views = load_views(args.scene, cams, width, device, args.fill_weight)
    tr = Trainer(xyz, rgb, extent, device, args.max_splats)
    bg = torch.zeros(3, device=device)
    densify_until = int(args.iters * 0.6)
    lr0, lr1 = Trainer.LR["means"] * extent, Trainer.LR["means"] * extent * 0.01
    t0 = time.time()
    order = []
    for it in range(1, args.iters + 1):
        if not order:
            order = list(np.random.permutation(len(views)))
        v = views[order.pop()]
        # Coarse-to-fine: half resolution for the first quarter of training.
        if it < args.iters // 4:
            H, W, K = v["H"] // 2, v["W"] // 2, tuple(k / 2 for k in v["K"])
            gt = torch.nn.functional.interpolate(v["img"].permute(2, 0, 1)[None], (H, W), mode="area")[0].permute(1, 2, 0)
            mk = torch.nn.functional.interpolate(v["mask"][None, None], (H, W), mode="area")[0, 0]
        else:
            H, W, K, gt, mk = v["H"], v["W"], v["K"], v["img"], v["mask"]
        cam = {"Rt": v["Rt"], "tt": v["tt"], "K": K}
        for group in tr.opt.param_groups:
            if group["name"] == "means":
                group["lr"] = lr0 * (lr1 / lr0) ** (it / args.iters)
        img, ids, m2 = render(tr.P, cam, H, W, args.k_max, bg)
        l1 = ((img - gt).abs().sum(-1) * mk).sum() / (mk.sum() * 3 + 1e-8)
        loss = 0.8 * l1 + 0.2 * (1 - ssim(img, gt, mk))
        tr.opt.zero_grad(set_to_none=True)
        loss.backward()
        if m2 is not None and m2.grad is not None and it < densify_until:
            with torch.no_grad():
                gn = (m2.grad * torch.tensor([W / 2, H / 2], device=device)).norm(dim=-1)
                tr.grad_acc.index_add_(0, ids, gn)
                tr.grad_cnt.index_add_(0, ids, (gn > 0).float())
        tr.opt.step()
        if it >= args.densify_from and it < densify_until and it % args.densify_every == 0:
            c, s, p = tr.densify(args.grad_thresh, prune_big=it > args.opacity_reset)
            log(f"it {it}: cloned {c:,}, split {s:,}, pruned {p:,} -> {len(tr.P['means']):,} splats")
        if args.carve and it >= args.densify_from and it % args.carve_every == 0:
            inside = hull_mask(tr.P, views)
            if inside.any():
                tr._replace(~inside, {k: v.detach()[:0] for k, v in tr.P.items()})
                log(f"it {it}: carved {int(inside.sum()):,} splats inside the people's visual hull")
        if it % args.opacity_reset == 0 and it < densify_until:
            tr.reset_opacity()
        if it % 100 == 0 or it == 1:
            el = time.time() - t0
            log(f"it {it}/{args.iters} loss {loss.item():.4f} l1 {l1.item():.4f} splats {len(tr.P['means']):,} "
                f"{el / it:.2f}s/it, eta {el / it * (args.iters - it) / 60:.0f} min")
        if args.save_every and it % args.save_every == 0:
            save(tr, args.out)
        if args.preview_every and it % args.preview_every == 0:
            preview(tr, views, args, bg, it)
    # Final prune of near-transparent splats.
    with torch.no_grad():
        keep = torch.sigmoid(tr.P["opac"]) > 0.01
        if args.carve:
            keep &= ~hull_mask(tr.P, views)
    tr._replace(keep, {k: v.detach()[:0] for k, v in tr.P.items()})
    save(tr, args.out)
    preview(tr, views, args, bg, args.iters)
    log(f"done in {(time.time() - t0) / 60:.1f} min: {len(tr.P['means']):,} splats -> {args.out}")


def save(tr: Trainer, out: Path) -> None:
    P = {k: v.detach().cpu().numpy().astype(np.float64) for k, v in tr.P.items()}
    q = P["quats"] / np.linalg.norm(P["quats"], axis=1, keepdims=True)
    write_ply(out, {"xyz": P["means"], "dc": P["dc"], "opacity": P["opac"], "scale": P["scales"], "rot": q})


def preview(tr: Trainer, views, args, bg, it) -> None:
    """Side-by-side render vs frame for three evenly spaced cameras."""
    import cv2
    import torch
    rows = []
    with torch.no_grad():
        for v in (views[0], views[len(views) // 2], views[-1]):
            img, _, _ = render(tr.P, {"Rt": v["Rt"], "tt": v["tt"], "K": v["K"]}, v["H"], v["W"], args.k_max, bg)
            pair = torch.cat([img.clamp(0, 1), v["img"]], 1).cpu().numpy()
            rows.append((pair * 255).astype(np.uint8))
    out = args.out.with_name(f"preview_{it:05d}.jpg")
    cv2.imwrite(str(out), cv2.cvtColor(np.concatenate(rows, 0), cv2.COLOR_RGB2BGR))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--scene", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--iters", type=int, default=7000)
    ap.add_argument("--width", type=int, default=0, help="training width in px (default 480 on CPU, full on GPU)")
    ap.add_argument("--k-max", type=int, default=128, help="max Gaussians blended per pixel")
    ap.add_argument("--max-splats", type=int, default=600_000)
    ap.add_argument("--densify-from", type=int, default=500)
    ap.add_argument("--densify-every", type=int, default=100)
    ap.add_argument("--opacity-reset", type=int, default=3000)
    ap.add_argument("--grad-thresh", type=float, default=0.0002)
    ap.add_argument("--save-every", type=int, default=1000)
    ap.add_argument("--preview-every", type=int, default=500)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--fill-weight", type=float, default=0.3,
                    help="loss weight of the inpainted fill/ images under the masks (0 = ignore masked pixels)")
    ap.add_argument("--carve", action=argparse.BooleanOptionalAction, default=True,
                    help="remove Gaussians inside the people's visual hull while training")
    ap.add_argument("--carve-every", type=int, default=250)
    ap.add_argument("--cpu", action="store_true", help="ignore a GPU even if one is present")
    args = ap.parse_args(argv)
    train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
