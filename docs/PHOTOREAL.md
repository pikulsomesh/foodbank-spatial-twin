# Photoreal zones: Gaussian splats trained from the video

The Marble 2 (Atlas) zones in `public/zones/*.spz` fuse 6 to 8 RGBD frames each. They are holey and read well only from their source cameras. This layer trains a real 3D Gaussian Splatting model (Kerbl et al. 2023) for each zone from 65 to 83 posed video frames, so a zone holds up as the camera moves. It runs locally and costs 0 World Labs credits.

```
tour video ──ffmpeg──► frames (4 to 6 fps, 960 px)
           ──MediaPipe DeepLab v3──► person masks + broadcast-graphic boxes
           ──blur + optical-flow keyframing──► 79 to 142 moving frames
           ──COLMAP (CPU SIFT, masks)──► camera poses + sparse points
           ──scripts/splat_train.py (PyTorch, CPU or CUDA)──► trained 3DGS .ply
           ──level, floor at y = 0, metric scale, crop──► public/zones/<zone>-3dgs.spz
                                                         public/zones/<zone>-3dgs.usdz
                                                         public/zones/photoreal.json
```

## Run it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt mediapipe opencv-python-headless scipy torch
sudo apt-get install colmap libgles2 libegl1          # COLMAP 3.9; the GL libs are for MediaPipe
for z in racking market sorting; do
  .venv/bin/python scripts/photoreal_twin.py frames --zone $z --video Inside-the-NEW-SnapYT.App.mp4
  .venv/bin/python scripts/photoreal_twin.py masks  --zone $z
  .venv/bin/python scripts/photoreal_twin.py select --zone $z
  .venv/bin/python scripts/photoreal_twin.py pose   --zone $z   # sorting: add --matcher exhaustive --init-tri-angle 3
  .venv/bin/python scripts/photoreal_twin.py train  --zone $z --iters 6000
  .venv/bin/python scripts/photoreal_twin.py export --zone $z
done
```

Segments live in [`data/photoreal/zones.json`](../data/photoreal/zones.json). Work files go to `build/photoreal/<zone>/` (ignored by git); `splat/preview_*.jpg` shows render versus frame for three cameras as training runs.

On a GPU, [`notebooks/photoreal_gpu.ipynb`](../notebooks/photoreal_gpu.ipynb) runs the same stages on a free Colab or Kaggle T4 at full 960 px frames, 15,000 steps and up to 1.5M splats. The trainer picks CUDA automatically.

## Why a new trainer

The usual 3DGS trainers (the reference code, gsplat, Nerfstudio splatfacto) rasterise with CUDA kernels, and the cloud box that builds this repo has no GPU. `scripts/splat_train.py` is the same method in plain tensor code:

- EWA projection of each anisotropic Gaussian to a 2D conic.
- Sparse rasterisation: one (pixel, Gaussian) pair per pixel inside the Gaussian's visible extent, pairs below 1/255 alpha dropped, a stable sort by pixel to keep depth order, and front-to-back compositing with a segmented cumulative sum of log(1 − alpha). Work scales with real coverage, which makes CPU training practical (about 1 s per step at 480 px on 4 cores).
- L1 + 0.2 D-SSIM loss, both masked so the hosts and the broadcast lower-third never become splats.
- Adaptive density control: clone small and split large Gaussians with high screen-space gradient, prune transparent ones, reset opacity every 3,000 steps, with Adam state carried across.
- Coarse to fine: half resolution for the first quarter of the steps.

Colour is SH degree 0, which is what the browser SPZ and the USD ParticleField carry today.

## Output contract

The same rules as the Atlas zones, so nothing downstream changes:

| File | Frame | Use |
|------|-------|-----|
| `public/zones/<zone>-3dgs.spz` | OpenCV (x right, y down, z forward), metres; rotate 180° about X to view, `ground_plane_offset` 0 | Browser (Spark) |
| `public/zones/<zone>-3dgs.usdz` | Y-up, metres, floor at y = 0, `ParticleField3DGaussianSplat` | Omniverse, Blender, usdview |
| `public/zones/photoreal.json` | Same fields as `zones.json`, plus splat count, extent and view presets from real source cameras | Viewers |
| `build/photoreal/<zone>/splat/<zone>-3dgs.ply` | Y-up metres, standard 3DGS PLY | SuperSplat, Blender add-ons |

The Atlas files are left in place. [`public/photoreal.html`](../public/photoreal.html) shows a trained zone next to its Atlas capture, from the presets.

**Scale and level.** COLMAP poses have no metric scale. Export levels the scene so the mean camera up is +Y, finds the floor as the densest low slab of solid splats, and scales so the cameras sit 1.6 m above it (a shoulder-height broadcast camera; `--camera-height` to change). That puts the racking beams at about the right height; it is not a survey.

## Limits

- Only what the camera saw is reconstructed. The video is an interview walk-through: shallow depth of field, mostly forward motion, and two people in most frames. Expect soft areas behind where the hosts stood and thin coverage at the edges of each zone.
- The sorting room is white walls and a grey floor, so COLMAP needs exhaustive matching and a lower initial triangulation angle there.
- CC0 texture libraries (Poly Haven, ambientCG) are blocked from the build box, so the generated building is not textured by this layer.
