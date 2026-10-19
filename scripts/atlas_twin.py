#!/usr/bin/env python3
"""
Auto-Twin | Marble 2 (Atlas) route: frames -> posed RGBD -> generated views -> splat -> USDZ

Build a Gaussian-splat twin through the Marble 2 Developer API, which has no
world/splat task on most accounts. Instead:

  pose      images2PosedRGBD on the input frames (camera + metric depth per frame)
  generate  atlasGenerate from those posed frames to new target cameras, so the
            world model extrapolates the parts of the space the video never shows
  fuse      unproject every RGBD view into one point set and write it as an
            isotropic 3D Gaussian splat (.ply), then .spz/.usdz via usd-convert-gsplat

Every paid call is recorded in --work-dir/state.json together with its
operation id, so a re-run resumes or reuses results instead of paying twice.
Submissions carry an Idempotency-Key derived from the request body for the
same reason. Nothing is spent by `--dry-run` or by the `fuse` stage.

Auth: the WLT-Api-Key header, from WLT_API_KEY, or injected by an egress proxy
(set WLT_API_KEY_FROM_PROXY=1 so no header is sent locally).

Usage:
  scripts/atlas_twin.py pose     --input-dir data/ctfb-warehouse/frames
  scripts/atlas_twin.py generate --views 8
  scripts/atlas_twin.py fuse
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

API_BASE = os.environ.get("WLT_API_BASE_URL", "https://api.atlas-beta.worldlabs.ai/api/v2").rstrip("/")

# Rate card shown in the Marble platform, 2026-10-19 (1,000 credits = 1 USD).
# Docs say it can change during beta; these are for the pre-spend estimate only.
CREDITS = {"images2PosedRGBD": 10, "atlasGenerate_rgb": 40, "atlasGenerate_rgbd": 50}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
GRID = (1280, 720)  # atlasGenerate targets must use this grid

DEFAULT_PROMPT = ("Interior of a large modern food bank distribution warehouse: tall red steel pallet "
                  "racking with grey beams and bay labels, shrink-wrapped pallets of canned and boxed "
                  "food on every level, pallet jacks, polished concrete floor, high white ceiling with "
                  "LED lights, wide aisles leading to loading docks. Empty, no people.")


class PipelineError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[atlas] {msg}", file=sys.stderr, flush=True)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


# =============================================================================
# Client
# =============================================================================

class Atlas:
    def __init__(self, base: str = API_BASE):
        self.base = base
        self.s = requests.Session()
        key = os.environ.get("WLT_API_KEY", "").strip()
        if key:
            self.s.headers["WLT-Api-Key"] = key
        elif not os.environ.get("WLT_API_KEY_FROM_PROXY"):
            raise PipelineError("set WLT_API_KEY, or WLT_API_KEY_FROM_PROXY=1 when a proxy injects the header")

    def req(self, method: str, path: str, body: dict | None = None, *, idem: str | None = None,
            params: dict | None = None, timeout: float = 120) -> dict:
        headers = {"Idempotency-Key": idem} if idem else {}
        for attempt in range(6):
            r = self.s.request(method, self.base + path, json=body, params=params,
                               headers=headers, timeout=timeout)
            if r.status_code in (429, 502, 503, 504) and attempt < 5:
                delay = float(r.headers.get("Retry-After") or 2 ** (attempt + 1))
                log(f"{path}: HTTP {r.status_code}, retrying in {delay:.0f}s")
                time.sleep(delay)
                continue
            if r.status_code >= 400:
                raise PipelineError(f"{method} {path}: HTTP {r.status_code}: {r.text[:800]}")
            return r.json() if r.content else {}
        raise PipelineError("unreachable")

    def upload_image(self, path: Path) -> str:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        data = base64.b64encode(path.read_bytes()).decode()
        out = self.req("POST", "/assets", {"asset": {"displayName": path.name, "base64": f"data:{mime};base64,{data}"}})
        if out.get("state") not in (None, "READY"):
            raise PipelineError(f"asset for {path.name} is {out.get('state')}, expected READY")
        return out["id"]

    def submit(self, task: str, body: dict) -> dict:
        idem = f"autotwin-{task}-" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]
        return self.req("POST", f"/tasks:{task}", body, idem=idem)

    def wait(self, op_id: str, timeout_s: float = 1800) -> dict:
        deadline = time.time() + timeout_s
        while True:
            op = self.req("POST", f"/operations/{op_id}:wait", {"timeoutSecs": 30}, timeout=90)
            if op.get("done"):
                if op.get("error"):
                    raise PipelineError(f"operation {op_id} failed: {json.dumps(op['error'])[:800]}")
                return op
            if time.time() > deadline:
                raise PipelineError(f"operation {op_id} still running after {timeout_s:.0f}s; re-run to resume")
            log(f"operation {op_id}: {(op.get('metadata') or {}).get('state', 'running')}")

    def read_url(self, asset: dict) -> str:
        if asset.get("assetId"):
            return self.req("POST", f"/assets/{asset['assetId']}:createReadUrl", {})["readUrl"]
        return asset["url"]


def download(client: Atlas, asset: dict, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if asset.get("base64"):
        b = asset["base64"].split(",", 1)[-1]
        dest.write_bytes(base64.b64decode(b))
        return dest
    r = requests.get(client.read_url(asset), timeout=300)  # signed URL: no API key header
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def run_task(client: Atlas, state: dict, state_path: Path, stage: str, task: str, body: dict,
             estimate: int, dry_run: bool) -> dict:
    """Submit once, record the operation id before waiting, resume on re-run."""
    rec = state.get(stage) or {}
    if rec.get("response"):
        log(f"{stage}: reusing completed operation {rec['operation_id']} (0 credits)")
        return rec["response"]
    if dry_run:
        log(f"DRY RUN {stage}: would submit {task}, est. {estimate} credits")
        print(json.dumps(body, indent=2)[:4000])
        return {}
    if rec.get("operation_id"):
        log(f"{stage}: resuming operation {rec['operation_id']}")
    else:
        log(f"{stage}: submitting {task}, est. {estimate} credits")
        op = client.submit(task, body)
        state[stage] = rec = {"operation_id": op["id"], "task": task, "estimate_credits": estimate,
                              "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        write_json(state_path, state)
    try:
        done = client.wait(rec["operation_id"])
    except PipelineError:
        # A failed operation is terminal. Keep it on record and clear the stage
        # so the next explicit run submits a fresh task instead of re-waiting.
        state.setdefault("failed", []).append(rec)
        state.pop(stage, None)
        write_json(state_path, state)
        raise
    rec["response"] = done["response"]
    write_json(state_path, state)
    return done["response"]


# =============================================================================
# Stages
# =============================================================================

def stage_pose(args, client: Atlas | None, state: dict, state_path: Path) -> None:
    frames = sorted(p for p in args.input_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not 2 <= len(frames) <= args.max_frames:
        raise PipelineError(f"need 2..{args.max_frames} frames in {args.input_dir}, found {len(frames)}")
    estimate = CREDITS["images2PosedRGBD"] * len(frames)
    uploads = state.setdefault("uploads", {})
    if not args.dry_run:
        for p in frames:
            if p.name not in uploads:
                uploads[p.name] = client.upload_image(p)
                log(f"uploaded {p.name} -> {uploads[p.name]} (free)")
        write_json(state_path, state)
    body = {"frames": [{"imageAsset": {"assetId": uploads.get(p.name, "<asset>")}} for p in frames],
            "targetResolution": list(GRID), "canonicalizeToFirstFrame": True, "refinePoses": True}
    resp = run_task(client, state, state_path, "pose", "images2PosedRGBD", body, estimate, args.dry_run)
    if resp:
        fetch_views(client, resp["frames"], args.work_dir / "views" / "pose")


def fetch_views(client: Atlas, frames: list[dict], out: Path) -> None:
    for i, f in enumerate(frames):
        download(client, f["imageAsset"], out / f"{i:03d}_rgb.img")
        depth = f.get("depth") or {}
        if depth.get("depthAsset"):
            download(client, depth["depthAsset"], out / f"{i:03d}_depth.exr")
        if depth.get("confidenceAsset"):
            download(client, depth["confidenceAsset"], out / f"{i:03d}_conf.png")
        (out / f"{i:03d}_camera.json").write_text(json.dumps(f["camera"], indent=2))
    log(f"saved {len(frames)} view(s) to {out}")


def yaw_quat(deg: float) -> list[float]:
    """XYZW quaternion for a rotation of `deg` about world +Y (RUB, gravity-levelled)."""
    h = math.radians(deg) / 2
    return [0.0, math.sin(h), 0.0, math.cos(h)]


def ring_cameras(posed: list[dict], n: int, fx: float, radius: float = 0.6) -> list[dict]:
    """n level cameras looking outward from a small ring around the mean posed camera centre.

    The posed cameras stood in the real aisle, so their mean is free space. A
    ring from there asks the world model for a full panorama of the zone,
    which is what fills the parts the video never looked at. A wider lens
    than the source (fx ~1900 px at 1280 wide) covers more per paid frame.
    Each camera sits `radius` behind the centre along its own view axis:
    returnDepth recovers scale from camera centres and rejects a rig whose
    targets all share one centre.
    """
    centres = [f["camera"]["extrinsics"]["position"] for f in posed]
    c = [sum(v[i] for v in centres) / len(centres) for i in range(3)]
    w, h = GRID
    intr = {"fx": fx, "fy": fx, "cx": w / 2, "cy": h / 2, "width": w, "height": h}
    cams = []
    for k in range(n):
        yaw = math.radians(360.0 * k / n)
        # RUB camera looks along -Z; rotated by yaw about +Y, forward is (-sin, 0, -cos).
        back = [math.sin(yaw), 0.0, math.cos(yaw)]
        pos = [c[i] + radius * back[i] for i in range(3)]
        cams.append({"extrinsics": {"position": pos, "quaternion": yaw_quat(360.0 * k / n),
                                    "coordinateSystem": "rub"}, "intrinsics": intr})
    return cams


def centred(camera: dict) -> dict:
    """Snap the principal point to the grid centre.

    With a masked context frame, atlasGenerate only lets a frame join the
    depth reconstruction when its principal point is centred. images2PosedRGBD
    returns cy ~0.8 px off centre, so without this every masked frame drops
    out and the reconstruction fails. A sub-pixel shift is far below the
    depth noise.
    """
    intr = dict(camera["intrinsics"])
    intr["cx"], intr["cy"] = intr["width"] / 2, intr["height"] / 2
    return {**camera, "intrinsics": intr}


def write_masks(boxes_path: Path, out: Path, n: int) -> dict[int, Path]:
    """8-bit keep-masks (255 keep, 0 exclude) from hand-drawn boxes on the posed 1280x720 grid."""
    from PIL import Image, ImageDraw
    boxes = read_json(boxes_path, {})
    masks = {}
    for i in range(n):
        rects = boxes.get(str(i)) or []
        if not rects:
            continue
        im = Image.new("L", GRID, 255)
        draw = ImageDraw.Draw(im)
        for r in rects:
            draw.rectangle(r, fill=0)
        out.mkdir(parents=True, exist_ok=True)
        masks[i] = out / f"{i:03d}_mask.png"
        im.save(masks[i])
    return masks


def stage_generate(args, client: Atlas | None, state: dict, state_path: Path) -> None:
    posed = ((state.get("pose") or {}).get("response") or {}).get("frames")
    if not posed:
        raise PipelineError("run the pose stage first")
    estimate = CREDITS["atlasGenerate_rgbd"] * args.views
    if estimate > args.max_credits:
        raise PipelineError(f"{args.views} RGBD views ~ {estimate} credits exceeds --max-credits {args.max_credits}")
    masks = write_masks(args.mask_boxes, args.work_dir / "masks", len(posed)) if args.mask_boxes else {}
    mask_ids = state.setdefault("mask_uploads", {})
    if not args.dry_run:
        for i, path in masks.items():
            if str(i) not in mask_ids:
                mask_ids[str(i)] = client.upload_image(path)
        write_json(state_path, state)
    context = []
    for i, f in enumerate(posed):
        depth = {"depthAsset": {"assetId": f["depth"]["depthAsset"]["assetId"]}}
        if f["depth"].get("confidenceAsset"):
            depth["confidenceAsset"] = {"assetId": f["depth"]["confidenceAsset"]["assetId"]}
        frame = {"imageAsset": {"assetId": f["imageAsset"]["assetId"]}, "camera": centred(f["camera"]), "depth": depth}
        if i in masks:
            frame["maskAsset"] = {"assetId": mask_ids.get(str(i), "<mask>")}
        context.append(frame)
    log(f"context: {len(context)} posed frame(s), {len(masks)} with people masked out")
    body = {"contextFrames": context, "targetCameras": ring_cameras(posed, args.views, args.fx),
            "prompt": args.prompt, "returnDepth": True}
    resp = run_task(client, state, state_path, "generate", "atlasGenerate", body, estimate, args.dry_run)
    if resp:
        log(f"promptUsed: {resp.get('promptUsed')}")
        fetch_views(client, resp["frames"], args.work_dir / "views" / "generate")



# =============================================================================
# Fuse: posed RGBD views -> isotropic Gaussian splat (free, local)
# =============================================================================

SH_C0 = 0.28209479177387814
SPZ_MAGIC = 0x5053474E


def load_view(prefix: Path, mask: Path | None):
    import numpy as np
    import OpenEXR
    from PIL import Image
    cam = json.loads(prefix.with_name(prefix.name + "_camera.json").read_text())
    exr = OpenEXR.File(str(prefix.with_name(prefix.name + "_depth.exr")))
    depth = next(iter(exr.parts[0].channels.values())).pixels.astype(np.float32)
    rgb = np.asarray(Image.open(prefix.with_name(prefix.name + "_rgb.img")).convert("RGB"))
    conf_path = prefix.with_name(prefix.name + "_conf.png")
    conf = (np.asarray(Image.open(conf_path).convert("L"), dtype=np.float32) / 255.0
            if conf_path.exists() else np.ones_like(depth))
    keep = np.ones_like(depth, dtype=bool)
    if mask is not None and mask.exists():
        keep = np.asarray(Image.open(mask).convert("L")) > 127
    return cam, depth, rgb, conf, keep


def unproject(cam: dict, depth, rgb, conf, keep, *, stride: int, max_depth: float, min_conf: float):
    """Pixels -> world points (RUB, the API's frame), colours, confidences and pixel footprints."""
    import numpy as np
    from scipy.spatial.transform import Rotation
    intr, ext = cam["intrinsics"], cam["extrinsics"]
    if ext.get("coordinateSystem", "rub") != "rub":
        raise PipelineError(f"unexpected camera convention {ext.get('coordinateSystem')}")
    h, w = depth.shape
    v, u = np.mgrid[0:h:stride, 0:w:stride]
    d = depth[v, u]
    ok = np.isfinite(d) & (d > 0) & (d < max_depth) & (conf[v, u] >= min_conf) & keep[v, u]
    u, v, d = u[ok], v[ok], d[ok]
    x = (u + 0.5 - intr["cx"]) / intr["fx"] * d
    y = -(v + 0.5 - intr["cy"]) / intr["fy"] * d
    pts = Rotation.from_quat(ext["quaternion"]).apply(np.stack([x, y, -d], 1)) + np.asarray(ext["position"])
    footprint = d / intr["fx"] * stride
    return pts, rgb[v, u], conf[v, u][...], footprint


def voxel_merge(pts, rgb, conf, size, voxel: float):
    """Keep the most confident sample per voxel so overlapping views don't stack splats."""
    import numpy as np
    keys = np.floor(pts / voxel).astype(np.int64)
    order = np.lexsort((-conf, keys[:, 2], keys[:, 1], keys[:, 0]))
    k = keys[order]
    first = np.ones(len(k), dtype=bool)
    first[1:] = np.any(k[1:] != k[:-1], axis=1)
    idx = order[first]
    return pts[idx], rgb[idx], conf[idx], size[idx]


def find_floor(y, cam_y: float, camera_height: float) -> tuple[float, str]:
    """Floor height in API units, and how it was found.

    1. A visible floor shows up as one dense 5 cm slab low in the scene.
    2. Otherwise, when images2PosedRGBD found the ground it puts it at y = 0
       (cameras then sit well above 0, as in the sorting room at ~1.2).
    3. Otherwise (racking aisle: floor barely visible, cameras near 0), put it
       `camera_height` below the source cameras.
    """
    import numpy as np
    low = y[y < np.percentile(y, 15)]
    hist, edges = np.histogram(low, bins=np.arange(low.min(), low.max() + 0.05, 0.05))
    i = int(np.argmax(hist))
    if hist[i] > 0.05 * len(y):
        return float(edges[i] + 0.025), "dense floor slab in the fused points"
    if cam_y > 0.5:
        return 0.0, "ground plane y=0 from images2PosedRGBD"
    return cam_y - camera_height, f"assumed {camera_height} m below the source cameras"


def write_spz(path: Path, pts, rgb, opacity, log_scale, frac_bits: int = 12) -> None:
    """Niantic SPZ v2, SH degree 0. Layout per usd_convert_gsplat.spz_reader / nianticlabs/spz."""
    import gzip
    import struct
    import numpy as np
    n = len(pts)
    fixed = np.round(pts * (1 << frac_bits)).astype(np.int64)
    if np.abs(fixed).max() >= 1 << 23:
        raise PipelineError("scene too large for 24-bit SPZ positions; lower --fractional-bits")
    fixed &= 0xFFFFFF
    pos = np.stack([fixed & 0xFF, (fixed >> 8) & 0xFF, (fixed >> 16) & 0xFF], -1).astype(np.uint8)
    alpha = np.clip(np.round(255.0 / (1.0 + np.exp(-opacity))), 0, 255).astype(np.uint8)
    f_dc = (rgb.astype(np.float32) / 255.0 - 0.5) / SH_C0
    colors = np.clip(np.round((f_dc * 0.15 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    scales = np.clip(np.round((log_scale + 10.0) * 16.0), 0, 255).astype(np.uint8)
    rot = np.full((n, 3), 128, dtype=np.uint8)  # identity quaternion (x, y, z ~ 0)
    header = struct.pack("<IIIBBBB", SPZ_MAGIC, 2, n, 0, frac_bits, 0, 0)
    with gzip.open(path, "wb", compresslevel=9) as fh:
        for block in (header, pos.tobytes(), alpha.tobytes(), colors.tobytes(),
                      np.repeat(scales[:, None], 3, 1).tobytes(), rot.tobytes()):
            fh.write(block)


def write_ply(path: Path, pts, rgb, opacity, log_scale) -> None:
    """Standard 3DGS binary PLY (what most splat viewers and trainers read)."""
    import numpy as np
    n = len(pts)
    f_dc = (rgb.astype(np.float32) / 255.0 - 0.5) / SH_C0
    cols = [pts[:, 0], pts[:, 1], pts[:, 2], f_dc[:, 0], f_dc[:, 1], f_dc[:, 2], opacity,
            log_scale, log_scale, log_scale, np.ones(n), np.zeros(n), np.zeros(n), np.zeros(n)]
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
             "rot_0", "rot_1", "rot_2", "rot_3"]
    data = np.stack(cols, 1).astype("<f4")
    head = "ply\nformat binary_little_endian 1.0\n" + f"element vertex {n}\n" + \
        "".join(f"property float {k}\n" for k in names) + "end_header\n"
    with path.open("wb") as fh:
        fh.write(head.encode())
        fh.write(data.tobytes())


def stage_fuse(args, state: dict) -> None:
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import world_labs_to_usd as wl

    views = args.work_dir / "views"
    sources = []
    masks = write_masks(args.mask_boxes, args.work_dir / "masks", 64) if args.mask_boxes else {}
    for cam in sorted((views / "pose").glob("*_camera.json")):
        i = int(cam.name[:3])
        sources.append(("pose", cam.with_name(cam.name[:3]), masks.get(i)))
    for cam in sorted((views / "generate").glob("*_camera.json")):
        sources.append(("generate", cam.with_name(cam.name[:3]), None))
    if not sources:
        raise PipelineError("no views to fuse; run the pose stage first")

    P, C, K, S = [], [], [], []
    for kind, prefix, mask in sources:
        cam, depth, rgb, conf, keep = load_view(prefix, mask)
        p, c, k, s = unproject(cam, depth, rgb, conf, keep, stride=args.stride,
                               max_depth=args.max_depth, min_conf=args.min_conf)
        log(f"{kind} view {prefix.name}: {len(p):,} points")
        P.append(p), C.append(c), K.append(k), S.append(s)
    pts, rgb, conf, size = (np.concatenate(a) for a in (P, C, K, S))
    pts, rgb, conf, size = voxel_merge(pts, rgb, conf, size, args.voxel)
    log(f"fused {len(pts):,} splats after {args.voxel * 100:.0f} cm voxel merge")

    cam_y = float(np.mean([json.loads(p.with_name(p.name + "_camera.json").read_text())
                           ["extrinsics"]["position"][1] for k, p, _ in sources if k == "pose"]))
    floor, how = find_floor(pts[:, 1], cam_y, args.camera_height)
    scale = args.metric_scale
    log(f"cameras at y={cam_y:.3f}; floor at y={floor:.3f} ({how}); scale {scale} m/unit")

    # Write in the same OpenCV-style frame Marble v1 SPZ uses (x right, y down,
    # z forward) so build_usdz's existing correction (scale, ground offset,
    # rotateX 180) and any viewer written for Marble output apply unchanged.
    rdf = pts * np.array([1.0, -1.0, -1.0])
    opacity = np.log(np.clip(conf * 0.95, 0.05, 0.95) / (1 - np.clip(conf * 0.95, 0.05, 0.95)))
    log_scale = np.log(np.clip(size * 0.8, 1e-4, None))
    out_dir = args.work_dir / "splat"
    out_dir.mkdir(parents=True, exist_ok=True)
    spz, ply = out_dir / "twin.spz", out_dir / "twin.ply"
    write_spz(spz, rdf, rgb, opacity, log_scale)
    write_ply(ply, rdf, rgb, opacity, log_scale)
    info = wl.validate_spz(spz)

    # build_usdz applies scale first, then subtracts the offset, so it is in metres.
    semantics = {"metric_scale_factor": scale, "ground_plane_offset": -floor * scale,
                 "ground_source": how}
    provenance = {"route": "marble2-atlas", "pose_operation": (state.get("pose") or {}).get("operation_id"),
                  "generate_operation": (state.get("generate") or {}).get("operation_id"),
                  "views": len(sources), "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "credits": sum((state.get(k) or {}).get("estimate_credits", 0) for k in ("pose", "generate"))}
    wl.build_usdz(spz, args.output, args.work_dir, semantics, provenance, True, 3600)
    n = wl.verify_usdz(args.output)
    public = args.output.parent
    (public / "factory.spz").write_bytes(spz.read_bytes())
    write_json(public / "factory.meta.json", {**provenance, "splats": info["splats"], "sh_degree": 0,
                                               "frame": "opencv (x right, y down, z forward), like Marble v1 SPZ",
                                               "semantics_metadata": semantics})
    log(f"wrote {args.output} ({args.output.stat().st_size / 1e6:.1f} MB, {n:,} splats), "
        f"{public / 'factory.spz'} ({(public / 'factory.spz').stat().st_size / 1e6:.1f} MB)")


def stage_site(args) -> None:
    """Compose fused zones into one OpenUSD site and publish per-zone SPZ for the browser.

    Each zone keeps its own USD layer (from `fuse`), referenced under
    /World/Site/<Zone> with that zone's placement from the layout file, so a
    zone can be re-captured without touching the others. The browser gets one
    SPZ per zone plus public/zones/zones.json with the same placements.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdUtils
    layout = read_json(args.layout, None)
    if not layout:
        raise PipelineError(f"no layout at {args.layout}")
    out_dir = args.work_dir / "site"
    if out_dir.exists():
        import shutil
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    zones_dir = args.output.parent / "zones"
    zones_dir.mkdir(parents=True, exist_ok=True)

    stage = Usd.Stage.CreateNew(str(out_dir / "site.usdc"))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.Xform.Define(stage, "/World/Site")
    manifest = []
    for z in layout["zones"]:
        wd = Path(z["work_dir"])
        usd_dir, spz = wd / "usd", wd / "splat" / "twin.spz"
        if not (usd_dir / "factory.usdc").exists() or not spz.exists():
            raise PipelineError(f"zone {z['name']}: run `fuse --work-dir {wd}` first")
        # Copy the zone's layers next to the site layer so the package is self-contained.
        for layer in usd_dir.glob("*.usdc"):
            (out_dir / f"{z['name'].lower()}_{layer.name}").write_bytes(layer.read_bytes())
        root = out_dir / f"{z['name'].lower()}_factory.usdc"
        zl = Sdf.Layer.FindOrOpen(str(root))

        def rename_refs(path, prefix=z["name"].lower()):
            spec = zl.GetPrimAtPath(path)
            if spec and spec.referenceList.prependedItems:
                spec.referenceList.prependedItems = [
                    Sdf.Reference(f"./{prefix}_{Path(r.assetPath).name}", r.primPath)
                    for r in spec.referenceList.prependedItems]
        zl.Traverse(Sdf.Path.absoluteRootPath, rename_refs)
        zl.Save()
        xf = UsdGeom.Xform.Define(stage, f"/World/Site/{z['name']}")
        xf.AddTranslateOp().Set(Gf.Vec3d(*z["position"]))
        xf.AddRotateYOp().Set(float(z.get("yaw", 0)))
        xf.GetPrim().GetReferences().AddReference(f"./{root.name}", "/World")
        xf.GetPrim().SetCustomDataByKey("label", z.get("label", z["name"]))
        meta = read_json(wd / "state.json", {})
        (zones_dir / f"{z['name'].lower()}.spz").write_bytes(spz.read_bytes())
        manifest.append({"name": z["name"], "label": z.get("label"), "url": f"zones/{z['name'].lower()}.spz",
                         "position": z["position"], "yaw": z.get("yaw", 0),
                         "pose_operation": (meta.get("pose") or {}).get("operation_id"),
                         "ground_plane_offset": _zone_ground(wd), "frame": "opencv, like factory.spz"})
    stage.GetRootLayer().customLayerData = {"autoTwin": {"layout": str(args.layout)}}
    stage.GetRootLayer().Save()
    site = args.output.parent / "site.usdz"
    if site.exists():
        site.unlink()
    if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(out_dir / "site.usdc")), str(site)):
        raise PipelineError("UsdUtils.CreateNewUsdzPackage failed for the site")
    check = Usd.Stage.Open(str(site))
    n = sum(len(p.GetAttribute("positions").Get() or []) for p in check.Traverse()
            if p.GetTypeName() == "ParticleField3DGaussianSplat")
    write_json(zones_dir / "zones.json", {"note": "Place each SPZ like factory.spz (rotate 180 deg about X, "
                                          "lift by ground_plane_offset), then translate by position.",
                                          "zones": manifest})
    log(f"wrote {site} ({site.stat().st_size / 1e6:.1f} MB, {len(manifest)} zones, {n:,} splats) "
        f"and {zones_dir}/zones.json")


def _zone_ground(work_dir: Path) -> float | None:
    """ground_plane_offset the zone's fuse applied (read back from its wrapper layer)."""
    from pxr import Usd, UsdGeom
    st = Usd.Stage.Open(str(work_dir / "usd" / "factory.usdc"))
    ops = UsdGeom.Xformable(st.GetPrimAtPath("/World/FoodBank")).GetOrderedXformOps()
    op = next((o for o in ops if o.GetOpType() == UsdGeom.XformOp.TypeTranslate), None)
    return None if op is None else -float(op.Get()[1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("stage", choices=["pose", "generate", "fuse", "site"])
    ap.add_argument("--input-dir", type=Path, default=Path("data/ctfb-warehouse/frames"))
    ap.add_argument("--work-dir", type=Path, default=Path("build/atlas"))
    ap.add_argument("--max-frames", type=int, default=8)
    ap.add_argument("--views", type=int, default=8, help="generate: target cameras on the ring")
    ap.add_argument("--fx", type=float, default=800.0, help="generate: target focal length in px at 1280 wide")
    ap.add_argument("--max-credits", type=int, default=400, help="generate: refuse to submit above this estimate")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--mask-boxes", type=Path, default=Path("data/ctfb-warehouse/people_boxes.json"),
                    help="JSON of exclusion boxes per posed frame; masked pixels are not used as context")
    ap.add_argument("--layout", type=Path, default=Path("data/site/layout.json"), help="site: zone placements")
    ap.add_argument("--output", type=Path, default=Path("public/factory.usdz"), help="fuse: USDZ path")
    ap.add_argument("--stride", type=int, default=2, help="fuse: sample every Nth pixel")
    ap.add_argument("--max-depth", type=float, default=20.0, help="fuse: drop depth beyond this (API units)")
    ap.add_argument("--min-conf", type=float, default=0.5, help="fuse: drop pixels below this depth confidence")
    ap.add_argument("--voxel", type=float, default=0.01, help="fuse: merge radius in API units")
    ap.add_argument("--camera-height", type=float, default=1.5, help="fuse: assumed camera height (m) above the floor")
    ap.add_argument("--metric-scale", type=float, default=1.0, help="fuse: metres per API unit")
    ap.add_argument("--dry-run", action="store_true", help="print the request; upload and spend nothing")
    args = ap.parse_args(argv)

    state_path = args.work_dir / "state.json"
    state = read_json(state_path, {})
    client = None if args.dry_run or args.stage in ("fuse", "site") else Atlas()
    try:
        if args.stage == "pose":
            stage_pose(args, client, state, state_path)
        elif args.stage == "generate":
            stage_generate(args, client, state, state_path)
        elif args.stage == "fuse":
            stage_fuse(args, state)
        else:
            stage_site(args)
    except PipelineError as exc:
        log(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
