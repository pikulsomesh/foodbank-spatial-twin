# Architecture: video to world-model twin, AWS data, open renderers

One idea: **OpenUSD is the open interchange contract.** Everything upstream produces it, everything downstream renders it, and the AWS layer binds live data to prims in it. No renderer is "the" open one or "the" proprietary one.

```
 LAYER 1  Video -> world-model twin        (scripts/, build/)
   YouTube / MP4 -> yt-dlp + ffmpeg (<=40 frames) -> World Labs Marble draft world (250 credits)
                 -> factory.spz -> usd-convert-gsplat -> factory.usdz (ParticleField3DGaussianSplat)
                                         |
                                  OpenUSD contract
                       factory.usdz + factory.meta.json (scale, ground, up-axis)
                                         |
 LAYER 2  Data connectors on AWS           (template.yaml, src/)
   S3 (twin assets + static site)  <->  HTTP API + Lambda: GET /telemetry
   Today: simulated sensors.  Next: swap the Lambda body for AWS IoT Core / Timestream /
   an existing WMS or cold-chain feed, keeping the same JSON contract (zones[], forklifts[]).
                                         |
 LAYER 3  Renderers (any of them reads the same USD + the same JSON)
   a) Browser   Spark (three.js) on factory.spz   zero install, hosted on S3
   b) Omniverse Kit / RTX  on factory.usdz       full-fidelity, GPU workstation or cloud
   c) Blender   on the USD, headless on a Mac mini or in the cloud environment (spike)
```

## Why this framing

- **OpenUSD** is a Linux Foundation / AOUSD open-source project. Version 26.03 added a native `ParticleField3DGaussianSplat` schema family ([AOUSD](https://aousd.org/blog/openusd-v26-03/), [schema](https://openusd.org/dev/user_guides/schemas/usdVol/ParticleField3DGaussianSplat.html)), which is what makes a splat twin a first-class USD asset instead of a vendor format.
- **Omniverse is a mix, not a single license.** Parts are open source (for example the Isaac Sim repository and the Asset Validator are Apache 2.0 per NVIDIA's docs), while the Kit SDK is downloaded separately and the `kit-app-template` repo says it is governed by the NVIDIA Software License Agreement and Omniverse product terms ([template repo](https://github.com/NVIDIA-Omniverse/kit-app-template), [Isaac Sim licensing](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/common/licenses-isaac-sim.html)). I could not confirm that the Kit runtime itself is under an OSI license, so docs and the paper should say "open components plus NVIDIA-licensed runtime" until that is checked against the Kit LICENSE. Claims in the paper about licensing must cite the license file, not this note.
- **Blender** is GPL and has a USD importer/exporter. Its recent release notes do not mention Gaussian splat or ParticleField support ([4.5 notes](https://developer.blender.org/docs/release_notes/4.5/pipeline_assets_io/)), so Blender is treated as a renderer for the twin's *scene and telemetry overlay* and, pending a spike, for the splat itself (options: a splat add-on fed from `factory.spz`/PLY, or a USD Hydra delegate that understands ParticleField). It is not claimed to render the splat natively.
- Using USD as the pivot means the choice of renderer is a deployment decision (hardware, cost, license posture), not an architecture change.

## Contracts between layers

| Boundary | Artifact | Rules |
|---|---|---|
| 1 to 3 | `public/factory.usdz`, `public/factory.spz`, `public/factory.meta.json` | Metric, Y-up, ground at y=0 (Thread 1 wrapper). Alignment lives in the meta file. |
| 2 to 3 | `GET /telemetry` JSON | Positions are metres in the same frame as the USD. Fields: `zones[]` (temp, setpoint, status, position), `forklifts[]`. Additive changes only. |
| 2 to 3 | `GET /operations` JSON, `public/operations.usda` | Same frame. `areas[]`, `inventory[]`, `flows[]`, `kitchen`, `trucks`, keyed by the ids in `src/telemetry/operations.json`. See OPERATIONS.md. |
| 2 internal | S3 bucket `SiteBucket` | Public read, only for twin assets. |

## Repo layout (target)

```
scripts/        layer 1 pipeline + scripts/deploy.sh
template.yaml   layer 2 (SAM)
src/telemetry/  layer 2 Lambda (swap for real connectors)
public/         layer 3a web viewer (Thread 3) and published assets
renderers/      omniverse/ (Kit app or USD stage + live-data extension), blender/ (headless bpy scripts)  [planned]
paper/          arXiv LaTeX (Thread 4)
docs/           this file
```

## Open questions for the next threads

1. Blender splat path: confirm one of the two options above on the Mac mini before the paper claims it.
2. Omniverse: choose Kit app template vs. plain USD stage plus an extension that polls `/telemetry`; verify Kit license text first.
3. Real data source: pick the first non-simulated connector (IoT Core MQTT is the cheapest honest step).
