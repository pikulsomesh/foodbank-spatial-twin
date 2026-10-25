# Rendering the twin from OpenUSD

The twin's open contract is `public/site.usdz` (PR 2): three fused captures as
`ParticleField3DGaussianSplat` prims (OpenUSD's Gaussian splat schema), Y-up,
metres, floor at y = 0. Anything that renders that file renders the twin. This
page covers the two renderers that run today on a plain CPU box, and what to run
in Omniverse once a GPU is available.

| Route | Draws splats as | Needs | Time for 6 stills (4 vCPU) |
|---|---|---|---|
| `scripts/render_usd_splats.py` | true Gaussian splats (EWA projection, alpha blended front to back) | Python, usd-core, numpy, numba | ~20 s including load |
| `scripts/blender_render_twin.py` | emissive spheres, one per splat (point-cloud stand-in) | Blender 5.2 or `pip install bpy` | ~5 s per 960x540 frame at 8 samples |
| Omniverse RTX (below) | path-traced particle fields | NVIDIA RTX GPU | not run yet |

Both scripts read the same camera list, `docs/renders/shots.json`, so their
stills line up shot for shot. Output from this branch is in `docs/renders/splat/` and
`docs/renders/blender/`; `docs/renders/splat_vs_blender.jpg` puts three shots side by side.

## Setup

```bash
python3.13 -m venv .venv-render
.venv-render/bin/pip install -r requirements-render.txt   # includes bpy (Blender as a module)
git checkout origin/claude/video-to-twin-a2f418 -- public/site.usdz   # until PR 2 merges
```

## Splat renderer (faithful)

```bash
.venv-render/bin/python scripts/render_usd_splats.py public/site.usdz \
    --shots docs/renders/shots.json --out renders/
.venv-render/bin/python scripts/render_usd_splats.py public/site.usdz \
    --flythrough docs/renders/flythrough.json --scale-video 1 --out renders/
```

It walks the stage for every splat prim, applies each prim's local-to-world
transform to positions and covariances, colours each splat from its degree-0
spherical harmonics, and composites in depth order. A 24 s 1080p flythrough
(576 frames) takes a few minutes on 4 vCPU.

## Blender (point-cloud stand-in)

Blender 5.2's own USD importer (`bpy.ops.wm.usd_import`) brings in the Xform
hierarchy but turns each splat prim into an empty: it does not read
`ParticleField3DGaussianSplat` yet. The script works around that by reading the
splats with the `pxr` module Blender ships and building one point cloud per prim
(Geometry Nodes *Mesh to Points*, radius from the splat scale, emission colour,
opacity as transparency), then renders with Cycles on the CPU.

```bash
# Blender app (macOS: /Applications/Blender.app/Contents/MacOS/Blender)
blender -b -P scripts/blender_render_twin.py -- public/site.usdz \
    --shots docs/renders/shots.json --out renders/blender --save-blend renders/twin.blend
# or headless with the bpy wheel
.venv-render/bin/python scripts/blender_render_twin.py public/site.usdz \
    --shots docs/renders/shots.json --out renders/blender
```

`--save-blend` keeps the scene so it can be opened and navigated in the Blender
UI. Spheres are not Gaussians, so edges look harder than in the splat renderer;
`--radius-scale` (default 1.7 sigma) trades gaps against bloat.

## What the renders show

Each zone is one sparse capture fused from a handful of video frames, so it
reads well from near its source viewpoint (each zone's origin, looking down -Z)
and falls apart into fragments from far away or behind. The stills are framed
from those viewpoints; the site overview and the flythrough's wide shots show
the fragmentation honestly. Denser captures (more frames, more generated views)
are what improves the far views, not the renderer.

## Omniverse later (rented GPU)

Not run yet; this is the plan for when GPU time is available.

Omniverse RTX renders `ParticleField` prims natively and path-traces them, so
splats receive shadows and show in reflections
([NVIDIA docs](https://docs.omniverse.nvidia.com/materials-and-rendering/latest/particle-fields.html)).
The schema ships in Kit as `omni.usd.schema.usd_particle_field` and in core
OpenUSD from 26.03, under the same prim type name the twin uses.

1. Rent one RTX-class GPU instance (an L4, L40S or A10G is plenty; any provider
   with NVIDIA driver 535+). Use an Omniverse- or Isaac-Sim-ready image if the
   provider has one, to skip driver setup.
2. Install a Kit app with RTX: the
   [Kit App Template](https://github.com/NVIDIA-Omniverse/kit-app-template)
   USD Composer or USD Viewer template (`./repo.sh template new`,
   `./repo.sh build`), or Isaac Sim's container.
3. Copy `public/site.usdz` and `docs/renders/shots.json` onto the box.
4. Open the stage headless and capture the same cameras, for example with
   `omni.kit.capture.viewport` (movie capture) or a short script that creates a
   `UsdGeom.Camera` per shot from `shots.json` (eye, target, horizontal fov) and
   calls the viewport's `capture_viewport_to_file`. Run it with
   `./_build/linux-x86_64/release/<app>.sh --no-window --exec render_shots.py`.
5. Compare against `docs/renders/` shot for shot; the cameras are identical.

Before paying for hours, check two things in the first session: that the Kit
version loads the particle field prims (it should show splats, not an empty
Xform), and the Kit runtime licence terms for the paper's wording.

## On a Mac mini

Nothing is required there: both renderers run in the cloud. If Somesh wants to
look around the twin interactively in Blender, install Blender 5.2 and run the
`blender -b ... --save-blend renders/twin.blend` command above from the repo
checkout, then open `renders/twin.blend`. Apple Silicon Blender renders it with
Metal in Cycles for faster previews.

## Omniverse trial on AWS (2026-10-25): result

Ran with `scripts/gpu_trial.py` (launch, render, upload through presigned S3 URLs, self-terminate)
and `scripts/omniverse_render_shots.py`, on one `g6.xlarge` (NVIDIA L4, 4 vCPU, 16 GB) in
us-east-2 from the Deep Learning Base OSS Nvidia Driver AMI (Ubuntu 22.04, driver 595.91),
with the `nvcr.io/nvidia/isaac-sim:5.1.0` container (Kit 107.3).

- **Runs 1 to 3, no renders.** Kit started (`app ready`) and then crashed with a segfault in
  `librtx.scenedb.plugin.so` about a second later, for both the designed facility
  (`facility.usdz`, plain meshes) and the splat captures (`site.usdz`). Since the meshes crash
  too, the splat schema is not the cause; the likely suspect is the host driver (595.x is newer
  than the 580.x branch Isaac Sim 5.1 is validated on) or the headless renderer setting. Log:
  `docs/renders/omniverse-trial/run3.log`.
- **Timings that hold regardless:** instance boot to user-data about 1 min; `docker pull` of
  Isaac Sim 5.1 about 4.5 min; Kit cold start 75 s on first run, 14 s warm.
- **Spend:** three short runs (one lost to a presigned-URL redirect bug, since fixed) totalling
  about 26 instance-minutes, about $0.35 at the $0.8048/h on-demand list price plus a cent of
  gp3 storage. Every instance terminated itself; no volumes remain.

- **Run 4 (22:19 to 22:29 UTC):** Kit's default renderer and a bare lit cube (`USD=test`) crash
  the same way right after `app ready`, before any stage is opened, so neither the twin files
  nor the renderer setting is the cause. Swapping in driver 580.65.06 from NVIDIA's runfile did
  not take: the runfile unpacked but the AMI's 595 driver stayed loaded. Log:
  `docs/renders/omniverse-trial/run4.log`. Running total about 36 instance-minutes, about $0.48.

- **Run 5 (plain Ubuntu 22.04 + `nvidia-driver-580-server`):** Kit 107.3 starts and opens the
  stage on driver 580. It then failed writing output because the container runs as a non-root
  user; fixed with `chmod -R a+rwX /work`, and Kit's caches are now mounted from the host so the
  second container starts warm. Log: `docs/renders/omniverse-trial/run5.log`.
- **Run 6:** first moving clips. Kit 107.3 draws the splat captures (`ParticleField` prims in
  `site.usdz`) as blank grey, so clips use `facility.usdz`. The forklift floated on its side:
  `/Facility` carries the Z-up to Y-up rotation, so a world pose must be mapped into its frame
  (`local = rest * world * parent^-1`, see `place_rig` in `scripts/omniverse_render_clips.py`).
- **Run 7 (working):** three 4 s clips at 1280x720, 24 fps, `rt_subframes=8`:
  `forklift_chase` (18.6 s to render), `facility_orbit` (47.7 s), `aisle_forklift` (49.4 s).
  g6.xlarge had no capacity in any us-east-2 zone, so the launcher now tries each zone and the
  run used `GPU_INSTANCE_TYPE=g5.xlarge` (A10G, $1.006/h): about 22 instance-minutes, about $0.37.

**Totals:** 7 runs, about 94 instance-minutes, about $1.34 at on-demand list prices. Every
instance terminated itself; no volumes remain. These are public list prices on an individually
created AWS account; enterprise discounts and quotas may differ, so treat them as directional.

Rerun: `python scripts/gpu_trial.py launch --base ubuntu --mode clips`, then `fetch` once the
instance has terminated. The logs `gpu_trial.py fetch` writes have the presigned URLs redacted.
