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
