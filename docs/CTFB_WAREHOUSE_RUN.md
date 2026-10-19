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

People cannot be masked out through the Marble v1 API, so the run uses the text prompt below and accepts that the world model will either paint them in or extrapolate behind them. Somesh approved extrapolating parts the video doesn't show.

## Generation (one paid draft world)

```bash
export WORLD_LABS_API_KEY=...   # set in the cloud environment, never in chat
.venv/bin/python scripts/world_labs_to_usd.py \
    --input-dir data/ctfb-warehouse/frames \
    --display-name "Central Texas Food Bank - warehouse aisle" \
    --text-prompt "Interior of a new food bank distribution warehouse: tall red steel pallet racking with grey beams and bay labels, shrink-wrapped pallets of canned and boxed food on every level, a pallet jack beside a bin of donated groceries, polished concrete floor, high white ceiling with LED lights, long main aisle toward loading docks, no people."
```

The dry run (`--dry-run`) passes: 8 of 8 inputs, `marble-1.0-draft`, estimated 250 credits, ledger at 0 of 10,000. The real run writes `public/factory.usdz`, `public/factory.spz` and `public/factory.meta.json`, and records the spend in `build/ledger.json`.

## API access (checked 2026-10-19 from the cloud environment)

- **Network:** `api.worldlabs.ai`, `api.atlas-beta.worldlabs.ai` and `storage.googleapis.com` are all reachable; both API hosts answer with their own 401, not a proxy denial.
- **Marble v1 (`api.worldlabs.ai/marble/v1`)** is what produces a splat world (`worlds:generate`, then the SPZ download). No key for it is in the environment: `WORLD_LABS_API_KEY` is unset.
- **Atlas beta (`api.atlas-beta.worldlabs.ai/api/v2`, "Marble 2 Developer API" 2.0.0)** authenticates with the `WLT-Api-Key` header, per its `securitySchemes`. The saved Atlas secret is injected as `Authorization: Bearer`, so every authenticated call returns `401 No API key found in request`.
- **Atlas cannot replace Marble v1 for this step.** Its tasks are `atlasGenerate` (novel views from posed RGB-D context frames), `images2PosedRGBD`, `atlasMasked`, `atlasChisel`, `atlasTextToImage` and `splats2Mesh`, plus assets and operations. None returns a Gaussian-splat world. Useful later: `images2PosedRGBD` for camera poses, mask support on input frames to remove the two people, and `splats2Mesh` for a collision mesh in Omniverse. Its pricing is not in the spec.
