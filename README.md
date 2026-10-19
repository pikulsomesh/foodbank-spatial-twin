# Auto-Twin: video to digital twin to open renderers

Turn a walkthrough video of the Central Texas Food Bank into a live digital twin: a World Labs world-model splat, converted to OpenUSD, bound to AWS-hosted telemetry, and rendered in a browser, Omniverse, or Blender. OpenUSD is the open contract between all layers. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| Layer | What | Where |
|---|---|---|
| 1. Video to twin | yt-dlp + ffmpeg, Marble draft world, usd-convert-gsplat | `scripts/` (below) |
| 2. Data on AWS | S3 site + Lambda/HTTP API telemetry (SAM) | `template.yaml`, `src/` (Thread 2 section) |
| 3. Renderers | Browser (Spark), Omniverse, Blender (spike) | `public/`, `docs/` |

# Auto-Twin: Thread 1, the AI spatial pipeline

This stage turns a walkthrough video of the Central Texas Food Bank into an Omniverse-ready OpenUSD twin.

```
YouTube ──yt-dlp──► /tmp/autotwin/source.* ──ffmpeg (blur-aware, 1 fps, ≤40)──► input_images/
        ──pick 8 sharpest, evenly spread──► World Labs media assets (free)
        ──POST /worlds:generate  model=marble-1.0-draft (250 credits)──► poll operation (free)
        ──download .spz──► usd-convert-gsplat ──► ParticleField3DGaussianSplat (.usdc)
        ──metric/ground/Y-up wrapper + UsdUtils packaging──► public/factory.usdz
                                                            public/factory.spz        (for the web viewer)
                                                            public/factory.meta.json  (alignment + provenance)
```

## Quick start

```bash
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements.txt
chmod +x scripts/ingest_and_build.sh

scripts/ingest_and_build.sh --ingest-only          # frames only, 0 credits
scripts/ingest_and_build.sh --dry-run              # prints the exact API payload, 0 credits
export WORLD_LABS_API_KEY=...                      # never commit this
scripts/ingest_and_build.sh                        # one draft world, about 250 credits ($0.20)
```

If YouTube blocks the download (it usually does from cloud and CI IPs), download the video on a workstation and run `scripts/ingest_and_build.sh --video-file path/to/video.mp4`. Use `--start SECONDS` to choose which 40-second window of the tour to capture. For a long tour, `--interval N` keeps one frame every N seconds (so 40 frames span 40N seconds) and `--crop-bottom PX` removes burned-in broadcast graphics. The first real run, on the Central Texas Food Bank warehouse aisle, is written up in [docs/CTFB_WAREHOUSE_RUN.md](docs/CTFB_WAREHOUSE_RUN.md).

Arguments after `--` go to the Python stage, for example `scripts/ingest_and_build.sh -- --spz-resolution 100k`.

## Cost protection

| Guard | What it does |
|---|---|
| Draft only | Any model other than `marble-1.0-draft` is refused without `--allow-non-draft`. The payload is checked again just before the POST, and the returned world's model is checked afterwards. |
| One generation per run | There is a single billable call site, and it runs at most once per process. |
| Resume, never resubmit | The operation id is saved the moment the job is accepted. A crash, timeout or Ctrl-C means the next run resumes polling instead of paying again. |
| Reuse finished worlds | Re-running after success converts the saved world for 0 credits. A new world needs `--force-new`. |
| Lifetime ledger | `build/ledger.json` records every charge. A run is refused if it would push the total past `--budget-credits` (default 10,000). |
| Live balance floor | `GET /credits` must show the estimate plus `--reserve-credits` (default 500) before anything is submitted. |
| No blind retries of paid calls | The generate POST is retried only on HTTP 429, which the docs define as "not accepted". A timeout or 5xx stops the run with exit code 3, so you can check the dashboard instead of risking a double charge. |
| Frame ceiling | The bash stage hard-caps extraction at 40 frames. No flag can raise it. |

Keep `build/` between runs, because it holds the state file and the ledger. Deleting it removes the resume and reuse protection.

## Corrections to the original brief (checked on docs.worldlabs.ai, 2026-10-18)

- **Pricing is lower than assumed.** A draft multi-image world costs 250 credits ($0.20), not 750. A standard world costs 1,600, not 3,000. The 10,000 credits cover about 38 drafts while keeping the 500-credit reserve. [Pricing](https://docs.worldlabs.ai/api/pricing.md)
- **The API accepts at most 8 images.** That limit applies with `reconstruct_images: true`. Without reconstruction the limit is 4. The 40 extracted frames are therefore a candidate pool: the script splits them into 8 segments and sends the sharpest frame from each (variance of the Laplacian). Reconstruction also requires every image to have the same resolution, so the script drops any frames that differ. [Generate](https://docs.worldlabs.ai/api/reference/worlds/generate.md), [Multi-image guide](https://docs.worldlabs.ai/marble/create/prompt-guides/multi-image-prompt.md)
- **Video input costs the same.** A video prompt (up to 100 MB) is also 250 credits on draft. It is available with `--mode video --video-file clip.mp4` and is worth an A/B test against the 8 frames.
- **Auth uses the `WLT-Api-Key` header** against `https://api.worldlabs.ai/marble/v1`. The model name `marble-1.0-draft` is real.

## Technical assessment: from `.spz` to `ParticleField3DGaussianSplat`

**1. Quantized data becomes float32, so files grow about 8×.** SPZ packs each splat tightly: 24-bit fixed-point positions, 8-bit log-scales, opacity and colour, 3 to 4 bytes of rotation, 8-bit SH, then gzip. usd-convert-gsplat decodes all of this to `point3f[]` positions, `float3[]` scales (after exp), `quatf[]` orientations, `float[]` opacities (after sigmoid) and `float3[]` SH coefficients. USDZ is uncompressed by specification, so the growth carries straight through to the download. In the test run, 20,000 SH-degree-1 splats went from 262 KB of SPZ to 2.0 MB of USDZ (7.7×), and SH degree 3 would grow more. Optimization opportunities:
   - Choose the splat tier. Use `--spz-resolution 500k` (the default) for Omniverse and `100k` for anything sent to a browser.
   - Use half precision. The schema already defines half-precision twins (`positionsh`, `scalesh`, `orientationsh`, `opacitiesh`, `radiance:sphericalHarmonicsCoefficientsh`), but converter 0.1.15 writes only the float32 versions. Writing the half versions would roughly halve the file. This is the best follow-up if size becomes a problem.
   - Truncate SH for the web. Dropping to SH degree 0 removes most of the payload, at the cost of view-dependent shine.

**2. The converter's direct `.usdz` path is a bottleneck, so the pipeline bypasses it.** In v0.1.15, `-o x.usdz` writes an ASCII `.usda` layer and zips it with Python's `zipfile`. That makes the file larger and slower to load, and it skips the 64-byte alignment the USDZ spec requires. The pipeline converts to binary `.usdc` instead and packages with OpenUSD's `UsdUtils.CreateNewUsdzPackage`. The converter also loops per splat in Python, so a `full_res` world can take minutes. `--convert-timeout` guards against that.

**3. Marble's coordinate frame has to be corrected.** World Labs SPZ files use an OpenCV-style frame (Y down) in arbitrary units. Each world ships `metric_scale_factor` and `ground_plane_offset`. The pipeline leaves the splat payload untouched and applies `rotateX(180) · translate(0, −offset, 0) · scale(s)` on a parent Xform, following the order in [Rendering SPZ](https://docs.worldlabs.ai/api/rendering-spz.md). The stage is Y-up with `metersPerUnit = 1`. With synthetic data, the ground landed at exactly y = 0. Metric units matter for later threads: IoT badges and forklift positions can then be placed in metres.

**4. The wrapper prim has to stay untyped.** A `def Xform` that references the splat would override its `ParticleField3DGaussianSplat` type and hide it. The pipeline wraps it as `/World/FoodBank` (Xform, holding the corrections) with an untyped child `/World/FoodBank/Splat` (the reference). The script re-opens the final USDZ and fails unless it finds that prim with positions.

**5. Edge cases handled**
- Motion blur in a handheld walkthrough: ffmpeg scores 4 candidates per second with `blurdetect` and keeps the sharpest. Python then ranks again with Laplacian variance.
- Interlaced sources: `bwdif` runs only on frames flagged as interlaced.
- Videos shorter than `--start` plus 40 seconds: the capture window is clamped.
- Signed SPZ URLs that expire: the world record is fetched again.
- Rate limits: the default tier allows about 3 starts per minute. The script honours `Retry-After` and uses exponential backoff with jitter.
- Unsupported SPZ versions or a corrupt gzip file are caught before conversion.

## Implications for later threads

- **Thread 3 should not depend on `<model-viewer>` to show the splat.** It renders glTF. It opens `.usdz` only by handing off to iOS AR Quick Look, which does not render USD particle fields. The recommendation is to render `public/factory.spz` with Spark (World Labs' three.js splat renderer) in the browser, and keep `factory.usdz` as the Omniverse deliverable. Alternatively, show the world's collider GLB (URL in `factory.meta.json`) in `<model-viewer>`. `factory.meta.json` has the scale and offset needed to align IoT badges.
- **Thread 2:** USDZ files are large and static. Serve them from S3 with long `Cache-Control` headers. CloudFront is optional, but it would make the download cheaper than direct S3 egress.

## What was verified and what wasn't

Verified in this session:
- Both scripts ran end to end against a local mock of the World Labs API, using real `usd-convert-gsplat` 0.1.15 with OpenUSD on Python 3.12.
- The mock checked the full submission path: the 429 retry on generate, a 503 during polling, the resume-in-flight path (exactly 1 generate call across 3 runs), reuse of a finished world, refusal of non-draft models, ledger accounting, and that the API key never reaches the signed upload URL.
- The output USDZ opens in OpenUSD with a `ParticleField3DGaussianSplat` prim and correct ground alignment.
- Blur-aware extraction ran on a synthetic 1080p video.

Not verified:
- **Real World Labs calls.** No API key was used. In particular, whether draft worlds offer every SPZ tier (`100k`, `500k`, `full_res`) is unknown. If a tier is missing, the script stops and lists the tiers that are available.
- **Whether `reconstruct_images` costs extra.** The pricing page lists no surcharge for it.
- **The YouTube download.** This sandbox's network blocks YouTube.
- **Rendering in Omniverse.** There was no RTX machine available.

Also worth confirming before a public showcase: permission from the Central Texas Food Bank (or whoever owns the video) to publish a reconstruction of their facility.

# Thread 2: AWS serverless backend

`template.yaml` (AWS SAM) defines everything with no standing cost:

| Resource | Notes |
|---|---|
| S3 bucket (static website) | Public read for `public/` (viewer, `factory.usdz`, `factory.spz`). Website hosting is HTTP only. |
| Lambda `python3.12`, arm64, 128 MB, 3 s timeout | `src/telemetry/app.py`. Stateless and deterministic, so there is no database. |
| HTTP API | `GET /telemetry`, `GET /health`, CORS open, throttled to 5 rps / 10 burst by default. |
| Log group | 7-day retention. |

`GET /telemetry` returns freezer, cooler and dry-storage temperature, humidity, door state, alarm status, and a forklift looping through the building. Positions are in metres in the twin's frame, so the viewer can pin badges to them. Calibrate `ZONES` and `FORKLIFT_PATH` once the real `factory.usdz` extents are known.

```bash
python -m pip install pytest && pytest tests
scripts/deploy.sh        # sam build + deploy, syncs public/ to the bucket, prints the URLs
```

**Expected cost:** the free tier covers 1M Lambda requests and 1M HTTP API requests per month for the first 12 months, S3 is pennies at this size. Past that, a viewer polling every 5 seconds around the clock is about 520k requests per month, roughly $0.50 in API Gateway plus $0.10 in Lambda. The throttle caps the worst case.
