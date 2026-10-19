#!/usr/bin/env python3
"""
Auto-Twin | photoreal route: video segment -> posed frames -> trained 3D Gaussian splat -> SPZ + USDZ

Train a real multi-view Gaussian splat for one zone from many video frames,
entirely locally and for 0 World Labs credits. The Marble 2 captures fuse 6 to
8 RGBD views, so they look right only from their source cameras; this route
optimises every splat against 60 to 150 frames, so the zone holds up as the
camera moves.

  frames  ffmpeg: cut the zone's segment from the video at a fixed rate
  masks   MediaPipe person segmentation + broadcast-graphic boxes; masked
          pixels are ignored by SfM and by training (the hosts never become splats)
  select  drop blurred frames and keep a frame only when the camera has moved
  fill    background plates for the masked pixels, warped in from frames where
          the people had moved (trained at reduced weight, so no haze or holes)
  pose    COLMAP on CPU: SIFT with masks, sequential matching, incremental mapping
  train   scripts/splat_train.py (PyTorch; CPU here, CUDA on a GPU box or Colab)
  export  level the scene with the cameras, put the floor at y = 0, scale to
          metres from the camera height, crop, then write the same contract
          as the Atlas zones: public/zones/<zone>-3dgs.spz (OpenCV frame),
          public/zones/<zone>-3dgs.usdz (ParticleField3DGaussianSplat) and an entry in public/zones/photoreal.json

Usage:
  scripts/photoreal_twin.py frames --zone racking --video /mnt/project-files/Inside-the-NEW-SnapYT.App.mp4
  scripts/photoreal_twin.py masks  --zone racking
  scripts/photoreal_twin.py select --zone racking
  scripts/photoreal_twin.py fill   --zone racking
  scripts/photoreal_twin.py pose   --zone racking
  scripts/photoreal_twin.py train  --zone racking --iters 7000
  scripts/photoreal_twin.py export --zone racking
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEG_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
                 "deeplab_v3/float32/1/deeplab_v3.tflite")
PERSON = 15  # Pascal VOC class index in the DeepLab v3 model


class PipelineError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[photoreal] {msg}", flush=True)


def run(cmd: list[str], **kw) -> None:
    log("$ " + " ".join(str(c) for c in cmd))
    res = subprocess.run([str(c) for c in cmd], **kw)
    if res.returncode:
        raise PipelineError(f"{cmd[0]} exited with {res.returncode}")


def zone_cfg(args) -> dict:
    cfg = json.loads(args.config.read_text())
    if args.zone not in cfg["zones"]:
        raise PipelineError(f"zone {args.zone!r} not in {args.config}")
    return {**cfg, **cfg["zones"][args.zone]}


# --------------------------------------------------------------------- frames

def stage_frames(args, cfg: dict) -> None:
    video = args.video or ROOT / cfg["video"]
    if not Path(video).exists():
        raise PipelineError(f"video not found: {video} (pass --video)")
    raw = args.work_dir / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)
    run(["ffmpeg", "-loglevel", "error", "-ss", cfg["start"], "-t", cfg["duration"], "-i", video,
         "-vf", f"fps={cfg['fps']},scale={args.width}:-2", "-q:v", "2", raw / "f_%04d.jpg"])
    log(f"{len(list(raw.glob('*.jpg')))} frames in {raw}")


# ---------------------------------------------------------------------- masks

def stage_masks(args, cfg: dict) -> None:
    """Write masks/<frame>.png: 255 = use this pixel, 0 = ignore (people, graphics)."""
    import cv2
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks import python as mpt
    from mediapipe.tasks.python import vision

    model = args.seg_model
    if not model.exists():
        import urllib.request
        model.parent.mkdir(parents=True, exist_ok=True)
        log(f"downloading {SEG_MODEL_URL}")
        urllib.request.urlretrieve(SEG_MODEL_URL, model)
    seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
        base_options=mpt.BaseOptions(model_asset_path=str(model)), output_category_mask=True))

    out = args.work_dir / "masks_all"
    out.mkdir(parents=True, exist_ok=True)
    frames = sorted((args.work_dir / "raw").glob("*.jpg"))
    kernel = np.ones((args.mask_dilate, args.mask_dilate), np.uint8)
    covered, masks = [], []
    for p in frames:
        img = cv2.imread(str(p))
        h, w = img.shape[:2]
        res = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        person = (res.category_mask.numpy_view() == PERSON).astype(np.uint8)
        person = cv2.dilate(cv2.resize(person, (w, h), interpolation=cv2.INTER_NEAREST), kernel)
        keep = np.where(person > 0, 0, 255).astype(np.uint8)
        for x0, y0, x1, y1 in cfg.get("overlay_boxes", []):
            keep[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = 0
        masks.append(keep)
    # The segmenter misses a hand or a dark jacket now and then; a miss in one frame
    # becomes a ghost in the splat. People barely move between neighbouring frames, so
    # also mask what the frames either side of this one marked as a person.
    for i, p in enumerate(frames):
        keep = np.minimum.reduce(masks[max(0, i - args.mask_window):i + args.mask_window + 1])
        cv2.imwrite(str(out / f"{p.stem}.png"), keep)
        covered.append(1 - keep.mean() / 255)
    log(f"masked {len(frames)} frames; mean masked area {100 * np.mean(covered):.0f}%")


# --------------------------------------------------------------------- select

def stage_select(args, cfg: dict) -> None:
    """Keep sharp frames where the camera has moved since the last kept frame."""
    import cv2
    import numpy as np

    frames = sorted((args.work_dir / "raw").glob("*.jpg"))
    masks = args.work_dir / "masks_all"
    gray, keep, sharp = [], [], []
    for p in frames:
        g = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2GRAY)
        m = cv2.imread(str(masks / f"{p.stem}.png"), 0)
        lap = cv2.Laplacian(g, cv2.CV_64F)
        sharp.append(lap[m > 0].var() if (m > 0).mean() > 0.05 else 0.0)
        gray.append((g, m))
    sharp = np.array(sharp)
    blur_floor = np.percentile(sharp, args.blur_percentile)

    last = None
    for i, p in enumerate(frames):
        g, m = gray[i]
        free = (m > 0).mean()
        if sharp[i] < blur_floor or free < args.min_free:
            continue
        if last is None:
            keep.append(i)
            last = i
            continue
        g0, m0 = gray[last]
        pts = cv2.goodFeaturesToTrack(g0, 400, 0.01, 8, mask=m0)
        if pts is None:
            continue
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(g0, g, pts, None)
        ok = st.ravel() == 1
        if ok.sum() < 30:  # lost track: a cut or a big jump; start a new run
            keep.append(i)
            last = i
            continue
        flow = np.linalg.norm((nxt - pts)[ok].reshape(-1, 2), axis=1)
        if np.median(flow) > args.min_flow * g.shape[1] / 960:
            keep.append(i)
            last = i

    if len(keep) > args.max_frames:
        keep = [keep[int(j)] for j in np.linspace(0, len(keep) - 1, args.max_frames)]
    img_dir, mask_dir = args.work_dir / "images", args.work_dir / "masks"
    for d in (img_dir, mask_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for i in keep:
        p = frames[i]
        shutil.copy(p, img_dir / p.name)
        shutil.copy(masks / f"{p.stem}.png", mask_dir / f"{p.name}.png")  # COLMAP naming: <image>.png
    log(f"kept {len(keep)} of {len(frames)} frames (blur floor {blur_floor:.0f}) in {img_dir}")


# ----------------------------------------------------------------------- fill

def stage_fill(args, cfg: dict) -> None:
    """Background plates for the masked pixels, from frames where the people had moved.

    The hosts stand in front of the same shelves for most of a segment. Left
    unsupervised, that region trains to haze; pruned, it is a hole. Instead, for
    each kept frame, warp nearby raw frames onto it with a homography fitted to
    unmasked features (good for the far shelving that the hosts hide) and copy the
    pixels that were visible there. What no neighbour saw falls back to a blurred
    inpaint. splat_train.py trains on these plates under the masks at reduced weight.
    """
    import cv2
    import numpy as np

    w = args.work_dir
    raw = sorted((w / "raw").glob("*.jpg"))
    index = {p.name: i for i, p in enumerate(raw)}
    out = w / "fill"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    sift = cv2.SIFT_create(3000)
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    cache: dict[int, tuple] = {}

    def feats(i: int):
        if i not in cache:
            img = cv2.imread(str(raw[i]))
            m = cv2.imread(str(w / "masks_all" / f"{raw[i].stem}.png"), 0)
            kp, des = sift.detectAndCompute(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (m > 128).astype(np.uint8))
            cache[i] = (img, m, kp, des)
            if len(cache) > 3 * args.fill_window:
                cache.pop(next(iter(cache)))
        return cache[i]

    from_video = []
    for p in sorted((w / "images").glob("*.jpg")):
        i = index[p.name]
        img, m, kp, des = feats(i)
        hole = m < 128
        filled = img.copy()
        todo = hole.copy()
        order = sorted((j for j in range(max(0, i - args.fill_window), min(len(raw), i + args.fill_window + 1)) if j != i),
                       key=lambda j: abs(j - i))
        for j in order:
            if todo.mean() < 0.002 or des is None:
                break
            img2, m2, kp2, des2 = feats(j)
            if des2 is None or len(kp2) < 20:
                continue
            pairs = [a for a, b in (x for x in matcher.knnMatch(des2, des, k=2) if len(x) == 2)
                     if a.distance < 0.75 * b.distance]
            if len(pairs) < 40:
                continue
            src = np.float32([kp2[a.queryIdx].pt for a in pairs])
            dst = np.float32([kp[a.trainIdx].pt for a in pairs])
            H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
            if H is None or inl.sum() < 30:
                continue
            size = (img.shape[1], img.shape[0])
            warped = cv2.warpPerspective(img2, H, size)
            valid = cv2.warpPerspective((m2 > 128).astype(np.uint8), H, size, flags=cv2.INTER_NEAREST) > 0
            valid = cv2.erode(valid.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0  # stay clear of mask edges
            take = todo & valid
            filled[take] = warped[take]
            todo &= ~take
        from_video.append(1 - todo.sum() / max(1, hole.sum()))
        if todo.any():  # nobody saw it: soft inpaint, as before
            small = cv2.resize(filled, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
            sh = cv2.resize(todo.astype(np.uint8), (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
            soft = cv2.GaussianBlur(cv2.resize(cv2.inpaint(small, sh, 9, cv2.INPAINT_TELEA), (img.shape[1], img.shape[0]),
                                               interpolation=cv2.INTER_CUBIC), (0, 0), 6)
            filled[todo] = soft[todo]
        cv2.imwrite(str(out / p.name), filled, [cv2.IMWRITE_JPEG_QUALITY, 92])
    log(f"wrote {len(from_video)} background plates to {out}; {100 * np.mean(from_video):.0f}% of masked "
        f"pixels came from other frames, the rest is inpainted")


# ----------------------------------------------------------------------- pose

def stage_pose(args, cfg: dict) -> None:
    w = args.work_dir
    db, sparse = w / "colmap.db", w / "sparse"
    if db.exists():
        db.unlink()
    if sparse.exists():
        shutil.rmtree(sparse)
    sparse.mkdir()
    colmap = args.colmap
    run([colmap, "feature_extractor", "--database_path", db, "--image_path", w / "images",
         "--ImageReader.mask_path", w / "masks", "--ImageReader.camera_model", "SIMPLE_RADIAL",
         "--ImageReader.single_camera", 1, "--SiftExtraction.use_gpu", 0,
         "--SiftExtraction.max_num_features", 4096])
    if args.matcher == "exhaustive":
        run([colmap, "exhaustive_matcher", "--database_path", db, "--SiftMatching.use_gpu", 0])
    else:
        run([colmap, "sequential_matcher", "--database_path", db, "--SiftMatching.use_gpu", 0,
             "--SequentialMatching.overlap", args.overlap, "--SequentialMatching.quadratic_overlap", 1])
    run([colmap, "mapper", "--database_path", db, "--image_path", w / "images", "--output_path", sparse,
         "--Mapper.ba_refine_principal_point", 0, "--Mapper.multiple_models", 1,
         "--Mapper.init_min_tri_angle", args.init_tri_angle, "--Mapper.init_min_num_inliers", 50,
         "--Mapper.abs_pose_min_num_inliers", 15])
    models = sorted((d for d in sparse.iterdir() if d.is_dir()),
                    key=lambda d: (d / "images.bin").stat().st_size, reverse=True)
    if not models:
        raise PipelineError("COLMAP found no model; try a longer segment or a higher fps")
    best = w / "model"
    if best.exists():
        shutil.rmtree(best)
    best.mkdir()
    run([colmap, "model_converter", "--input_path", models[0], "--output_path", best, "--output_type", "TXT"])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from splat_train import read_colmap
    n = len(read_colmap(best))
    log(f"registered {n} of {len(list((w / 'images').glob('*.jpg')))} frames in {models[0].name} "
        f"({len(models)} model(s)); text model in {best}")


# ---------------------------------------------------------------------- train

def stage_train(args, cfg: dict) -> None:
    cmd = [sys.executable, Path(__file__).with_name("splat_train.py"), "--scene", args.work_dir,
           "--iters", args.iters, "--out", args.work_dir / "splat" / "trained.ply"]
    if args.train_args:
        cmd += args.train_args
    run(cmd)


# --------------------------------------------------------------------- export

def stage_export(args, cfg: dict) -> None:
    import numpy as np
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import splat_train as st
    import world_labs_to_usd as wl

    w = args.work_dir
    g = st.read_ply(w / "splat" / "trained.ply")
    cams = st.read_colmap(w / "model")
    centres = np.stack([c["centre"] for c in cams])
    ups = np.stack([-c["R"][1] for c in cams])  # camera -y axis in world coordinates
    up = ups.mean(0)
    up /= np.linalg.norm(up)

    # Rotate so the mean camera up is +Y and the mean view direction is -Z.
    fwd = np.stack([c["R"][2] for c in cams]).mean(0)
    fwd -= up * fwd.dot(up)
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, up)
    R = np.stack([right, up, -fwd])  # rows: new x, y, z axes in old coordinates
    pts = (g["xyz"] - centres.mean(0)) @ R.T
    cam_new = (centres - centres.mean(0)) @ R.T

    # Floor: the densest low horizontal slab of confident splats.
    op = 1 / (1 + np.exp(-g["opacity"]))
    solid = op > 0.5 if (op > 0.5).sum() > 1000 else op >= np.median(op)
    y = pts[solid, 1]
    lo, hi = np.percentile(y, 0.5), cam_new[:, 1].mean()
    hist, edges = np.histogram(y[(y > lo) & (y < hi)], bins=200)
    low = edges[:-1] < lo + 0.6 * (hi - lo)
    floor = float(edges[np.argmax(np.where(low, hist, 0))] + (edges[1] - edges[0]) / 2)
    cam_h = float(cam_new[:, 1].mean() - floor)
    scale = args.camera_height / cam_h
    log(f"floor {floor:.3f}, cameras {cam_h:.3f} units above it; scale {scale:.3f} m/unit "
        f"(assumes a {args.camera_height} m camera height)")

    pts = (pts - np.array([0, floor, 0])) * scale
    cam_new = (cam_new - np.array([0, floor, 0])) * scale
    # Crop: within --radius of the camera path, between the floor and the roof.
    d = np.min(np.linalg.norm(pts[:, None, [0, 2]] - cam_new[None, ::max(1, len(cam_new) // 64), [0, 2]],
                              axis=2), axis=1)
    # Splats seen by only a few cameras sit at the edges of the frames, where nothing
    # constrains their depth; from any other viewpoint they read as floaters.
    seen = np.zeros(len(pts), int)
    for c in cams:
        pc = g["xyz"] @ c["R"].T + c["t"]
        z = np.maximum(pc[:, 2], 1e-6)
        u, v = c["fx"] * pc[:, 0] / z + c["cx"], c["fy"] * pc[:, 1] / z + c["cy"]
        seen += (pc[:, 2] > 0.05) & (u > 0) & (u < c["width"]) & (v > 0) & (v < c["height"])
    keep = (d < args.radius) & (pts[:, 1] > -0.15) & (pts[:, 1] < args.ceiling) & (op > 0.02) & \
        (seen >= args.min_views)
    log(f"kept {keep.sum():,} of {len(pts):,} splats inside the crop")

    rot = st.quat_mul_np(st.rotmat_to_quat_np(R), g["rot"])
    out = {k: v[keep] for k, v in g.items()}
    out["xyz"] = pts[keep]
    out["rot"] = rot[keep]
    out["scale"] = g["scale"][keep] + np.log(scale)

    # Same contract as the Atlas zones: SPZ in the OpenCV frame (rotate 180 deg about X
    # to view), floor at y = 0 after that rotation, so ground_plane_offset is 0.
    flip = np.array([1.0, -1.0, -1.0])
    cv = dict(out, xyz=out["xyz"] * flip, rot=st.quat_mul_np(np.array([0.0, 1.0, 0.0, 0.0]), out["rot"]))
    splat_dir = w / "splat"
    spz = splat_dir / f"{args.zone}-3dgs.spz"  # beside, not over, the Atlas capture
    st.write_spz(spz, cv)
    st.write_ply(splat_dir / f"{args.zone}-3dgs.ply", out)  # Y-up metres, for Blender/SuperSplat
    info = wl.validate_spz(spz)

    usdz = args.usdz_dir / f"{args.zone}-3dgs.usdz"
    semantics = {"metric_scale_factor": 1.0, "ground_plane_offset": 0.0, "ground_source": "trained splat floor"}
    provenance = {"route": "photoreal-3dgs", "zone": args.zone, "video_start_s": cfg["start"],
                  "video_duration_s": cfg["duration"], "frames": len(cams), "credits": 0,
                  "camera_height_m": args.camera_height,
                  "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    usdz.parent.mkdir(parents=True, exist_ok=True)
    wl.build_usdz(spz, usdz, w, semantics, provenance, True, 3600)
    wl.verify_usdz(usdz)

    # A few source cameras as viewer presets: position and a point 3 m ahead (metres, Y-up).
    fwd_new = np.stack([R @ c["R"][2] for c in cams])
    # One preset per stretch of the walk: the camera that saw the fewest people.
    import cv2
    masked = np.array([(cv2.imread(str(w / "masks" / f"{c['name']}.png"), 0) < 128).mean()
                       if (w / "masks" / f"{c['name']}.png").exists() else 0.0 for c in cams])
    bins = np.array_split(np.arange(len(cams)), min(6, len(cams)))
    pick = [int(b[np.argmin(masked[b])]) for b in bins]
    views = [{"position": cam_new[i].round(2).tolist(), "target": (cam_new[i] + 3 * fwd_new[i]).round(2).tolist(),
              "fov_deg": round(math.degrees(2 * math.atan(cams[i]["height"] / (2 * cams[i]["fy"]))), 1),
              "frame": cams[i]["name"]} for i in pick]

    public = args.public_dir
    public.mkdir(parents=True, exist_ok=True)
    shutil.copy(spz, public / spz.name)
    index = public / "photoreal.json"
    data = json.loads(index.read_text()) if index.exists() else {"zones": []}
    data["note"] = ("Trained 3DGS zones (scripts/photoreal_twin.py). Same placement rule as zones.json: "
                    "rotate 180 deg about X, lift by ground_plane_offset, then translate by position.")
    entry = {"name": args.zone.capitalize(), "label": cfg["label"], "url": f"zones/{spz.name}",
             "position": [0, 0, 0], "yaw": 0, "ground_plane_offset": 0.0, "frame": "opencv, like factory.spz",
             "splats": info["splats"], "sh_degree": info.get("sh_degree", 0),
             "extent_m": {"min": out["xyz"].min(0).round(2).tolist(), "max": out["xyz"].max(0).round(2).tolist()},
             "views": views, **provenance}
    data["zones"] = [z for z in data["zones"] if z["name"] != entry["name"]] + [entry]
    index.write_text(json.dumps(data, indent=2) + "\n")
    log(f"wrote {public / spz.name} ({(public / spz.name).stat().st_size / 1e6:.1f} MB, {info['splats']:,} "
        f"splats), {usdz} ({usdz.stat().st_size / 1e6:.1f} MB) and {index}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("stage", choices=["frames", "masks", "select", "fill", "pose", "train", "export"])
    ap.add_argument("--zone", required=True)
    ap.add_argument("--config", type=Path, default=ROOT / "data/photoreal/zones.json")
    ap.add_argument("--work-dir", type=Path, help="default build/photoreal/<zone>")
    ap.add_argument("--video", type=Path, help="frames: the tour video (default: config 'video' in the repo root)")
    ap.add_argument("--width", type=int, default=960, help="frames: output width in px")
    ap.add_argument("--seg-model", type=Path, default=ROOT / "build/models/deeplab_v3.tflite")
    ap.add_argument("--mask-dilate", type=int, default=35, help="masks: grow person masks by this many px")
    ap.add_argument("--mask-window", type=int, default=1, help="masks: also mask people seen this many frames either side")
    ap.add_argument("--blur-percentile", type=float, default=20, help="select: drop the blurriest N%%")
    ap.add_argument("--min-free", type=float, default=0.35, help="select: need this fraction of unmasked pixels")
    ap.add_argument("--min-flow", type=float, default=14, help="select: median motion in px (at 960 wide)")
    ap.add_argument("--max-frames", type=int, default=150)
    ap.add_argument("--fill-window", type=int, default=60, help="fill: search this many raw frames either side")
    ap.add_argument("--colmap", default="colmap")
    ap.add_argument("--overlap", type=int, default=12, help="pose: sequential matching window")
    ap.add_argument("--matcher", choices=["sequential", "exhaustive"], default="sequential",
                    help="pose: exhaustive helps low-texture rooms where sequential pairs fail")
    ap.add_argument("--init-tri-angle", type=float, default=8.0,
                    help="pose: min triangulation angle (deg) for the first pair; lower for forward walks")
    ap.add_argument("--iters", type=int, default=7000, help="train: optimisation steps")
    ap.add_argument("--train-args", nargs=argparse.REMAINDER, help="train: extra splat_train.py flags")
    ap.add_argument("--camera-height", type=float, default=1.6, help="export: camera height in metres")
    ap.add_argument("--radius", type=float, default=30.0, help="export: keep splats within this of the path (m)")
    ap.add_argument("--min-views", type=int, default=6, help="export: drop splats inside fewer camera frustums")
    ap.add_argument("--ceiling", type=float, default=9.0, help="export: drop splats above this (m)")
    ap.add_argument("--public-dir", type=Path, default=ROOT / "public/zones")
    ap.add_argument("--usdz-dir", type=Path, default=ROOT / "public/zones")
    args = ap.parse_args(argv)
    args.work_dir = args.work_dir or ROOT / "build/photoreal" / args.zone
    args.work_dir.mkdir(parents=True, exist_ok=True)
    try:
        cfg = zone_cfg(args)
        globals()[f"stage_{args.stage}"](args, cfg)
    except PipelineError as exc:
        log(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
