# Run 1: Central Texas Food Bank warehouse aisle

Source: "Inside the NEW Central Texas Food Bank" (25 News KXXV, July 2026, <https://www.youtube.com/watch?v=8sQmr7JqXkI>), 18:32, 1920x1080 at 59.94 fps, no audio. It is an interview walk-through, so two people are in almost every shot and a "Feed the Need" QR lower-third is burned into the bottom-left corner of every frame.

## Which part of the facility

A multi-image world needs overlapping views of one space, so the run targets a single zone instead of the whole tour. Surveying the video at one frame every 10 s gives these candidate zones:

| Time | Zone | Why it was or wasn't picked |
|------|------|-----------------------------|
| 0:45-1:35 | Volunteer sorting room (roller conveyors) | Clean at 0:50, but the camera barely moves |
| 2:10-2:50 | Freezer/cooler doors | Mostly blank walls, fogged at 2:40 |
| 5:05-6:00 | Loading-dock doors and pallets | Talking heads fill the frame |
| **6:10-7:45** | **Pallet-racking aisle (bays DC21B-DC24B)** | **Picked.** Red racking, shrink-wrapped pallets, pallet jack and a bin of donations; the camera moves around the bay and down the aisle, and 6:29 has no people at all |
| 9:20-10:00 | Client grocery market | Good shelves, but people block the centre |
| 13:50-16:40 | Commercial kitchen | Reflective stainless steel, hard for splats |

The racking aisle is also the zone that matters most for a warehouse twin (inventory locations, forklift paths, telemetry zones in `src/telemetry`).

## Frames

```bash
scripts/ingest_and_build.sh --video-file Inside-the-NEW-SnapYT.App.mp4 \
    --start 305 --interval 4 --crop-bottom 264 --ingest-only
```

This keeps the sharpest of every 4 s (16 candidates per bucket) across 5:05-7:45, giving the 40-frame cap, and crops the bottom 264 px so the QR overlay is gone (frames are 1920x816). Two flags were added to the script for this: `--interval` spreads the 40 frames over a longer walk and `--crop-bottom` removes broadcast graphics.

The automatic 8-of-40 picker (equal segments, sharpest frame in each) was tried on this footage and is not good enough here: Laplacian sharpness rewards in-focus faces, so it picked six near-identical interview shots. The 8 inputs were therefore chosen by hand for viewpoint spread and fewest people, and committed in [`data/ctfb-warehouse/frames/`](../data/ctfb-warehouse/frames/) ([contact sheet](../data/ctfb-warehouse/contact_sheet.jpg)):

| Input | From pipeline frame | Approx. time | View |
|-------|------|------|------|
| frame_001 | 018 | 6:13 | Bay DC22B/DC24B, pallet jack, donation bin |
| frame_002 | 019 | 6:17 | Same bay, closer, shifted right |
| frame_003 | 020 | 6:21 | Same bay from the left |
| frame_004 | 022 | 6:29 | Racking and wrapped pallets, no people |
| frame_005 | 025 | 6:41 | Opposite side of the aisle, bay DC22B |
| frame_006 | 029 | 6:57 | Same, camera moved right |
| frame_007 | 034 | 7:17 | Wider shot under the beam, DC21B/DC22B |
| frame_008 | 038 | 7:33 | Looking down the main aisle to the dock |

All eight are 1920x816, which reconstruction mode requires.

The two hosts are masked out of the Marble 2 run (see below), and the world model is free to extrapolate what they hide and what the video never shows. Somesh approved that.

## Framing

The Central Texas Food Bank is the inspiration for the layout, not a site to reproduce exactly. The twin starts from the real racking aisle and may add plausible, world-model-generated zones so that it shows a fuller food bank operation.

## Generation: Marble 2 (Atlas) route

Somesh's World Labs account is on Marble 2: 10,000 Dev credits, and a project key that only works on `api.atlas-beta.worldlabs.ai/api/v2`. That API has no task that returns a splat world. Probes for an unlisted `image2DraftSplats` and similar names return 404, and the "Draft splat" line on the rate card (750 credits) is not available to this key. So `scripts/atlas_twin.py` builds the splat itself:

| Stage | Call | Cost (rate card, 2026-10-19) |
|-------|------|------|
| `pose` | `images2PosedRGBD` on the 8 frames: camera, metric depth and confidence per frame | 10 credits per image, so 80 |
| `generate` | `atlasGenerate` from the posed frames (hosts masked out) to an 8-camera ring around the aisle, with `returnDepth` | 50 per RGBD frame, so 400 |
| `fuse` | Local and free. Unproject every RGBD view, keep the most confident sample per 1 cm voxel, write isotropic Gaussians as `.ply` and SPZ v2, then reuse `world_labs_to_usd.build_usdz` for the USDZ | 0 |

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt numpy scipy OpenEXR
export WLT_API_KEY=...            # or WLT_API_KEY_FROM_PROXY=1 when the egress proxy injects WLT-Api-Key
.venv/bin/python scripts/atlas_twin.py pose
.venv/bin/python scripts/atlas_twin.py generate --views 8 --max-credits 400
.venv/bin/python scripts/atlas_twin.py fuse
```

Each paid stage records its operation id in `build/atlas/state.json` before waiting, so a re-run resumes or reuses the result instead of paying again. Submissions carry an `Idempotency-Key` derived from the request body. The hosts are masked by hand-drawn boxes in [`data/ctfb-warehouse/people_boxes.json`](../data/ctfb-warehouse/people_boxes.json). Masked pixels are left out of the generation context and out of the fused splat.

**Status:** `pose` succeeded (operation `929d5a1b…`, 80 credits). The depth is metric-like: the first racking beam lands about 0.4 m above the lens, as on a real beam about 1.9 m high. `generate` failed twice with `EXECUTION_FAILED` and no detail. The first attempt had all target cameras at one centre; the second spread them on a 0.6 m ring and centred the context principal points. Retries are paused until we know whether failed tasks are billed.

The committed `public/factory.spz` (270k splats, 2.1 MB) and `public/factory.usdz` (17.5 MB, `ParticleField3DGaussianSplat`, Y-up, metres, floor at y = 0) are fused from the 8 posed frames only. Because the floor is barely visible, it is placed 1.5 m below the source cameras rather than fitted. The SPZ uses the same OpenCV-style frame as Marble v1 output, so viewers rotate it 180° about X. It renders in Spark 0.1.10 ([preview](../data/ctfb-warehouse/spark_preview.jpg)). It covers only the racking bay the camera saw; the generated views are what fill in the rest of the zone.

## API access (checked 2026-10-19 from the cloud environment)

- **Network:** `api.worldlabs.ai`, `api.atlas-beta.worldlabs.ai` and `storage.googleapis.com` are all reachable.
- **Auth:** Marble 2 reads only the `WLT-Api-Key` header. A secret injected as `Authorization: Bearer` is rejected with `401 No API key found in request`. The environment secret now sends `WLT-Api-Key`.
- **Marble v1** (`api.worldlabs.ai/marble/v1`, used by `world_labs_to_usd.py`) needs a separate v1 key and v1 credits. Marble 2 keys are not valid there.
- **Billing:** 1,000 credits = $1, and the rate card is shown only in the platform. Project authorization: up to 1,000 credits in total without asking again.
