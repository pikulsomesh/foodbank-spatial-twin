# Step 2 backend: AWS serverless telemetry

`template.yaml` (AWS SAM) defines everything with no standing cost:

| Resource | Notes |
|---|---|
| S3 bucket (static website) | Public read for `public/` (viewer, `factory.usdz`, `factory.spz`). Website hosting is HTTP only. |
| Lambda `python3.12`, arm64, 128 MB, 3 s timeout | `src/telemetry/app.py`. Stateless and deterministic, so there is no database. |
| HTTP API | `GET /telemetry`, `GET /operations`, `GET /health`, CORS open, throttled to 5 rps / 10 burst by default. |
| Log group | 7-day retention. |

`GET /telemetry` returns freezer, cooler and dry-storage temperature, humidity, door state, alarm status, and a forklift looping through the building. Positions are in metres in the twin's frame, so the viewer can pin badges to them. Calibrate `ZONES` and `FORKLIFT_PATH` once the real `factory.usdz` extents are known.

```bash
python -m pip install pytest && pytest tests
scripts/deploy.sh        # sam build + deploy, syncs public/ to the bucket, prints the URLs
```

**Expected cost:** the free tier covers 1M Lambda requests and 1M HTTP API requests per month for the first 12 months, S3 is pennies at this size. Past that, a viewer polling every 5 seconds around the clock is about 520k requests per month, roughly $0.50 in API Gateway plus $0.10 in Lambda. The throttle caps the worst case.

## Deployed instance

The stack runs in **us-east-2 (Ohio)** in an individual AWS account, deployed with `scripts/deploy.sh` on 2026-10-25. The account's organization policy blocks Lambda, CloudFormation and API Gateway in us-east-1 and us-west-2, so `deploy.sh` defaults to us-east-2.

| Endpoint | URL |
|---|---|
| Telemetry | <https://f2ck9o1rx0.execute-api.us-east-2.amazonaws.com/telemetry> |
| Operations | <https://f2ck9o1rx0.execute-api.us-east-2.amazonaws.com/operations> |
| Health | <https://f2ck9o1rx0.execute-api.us-east-2.amazonaws.com/health> |
| S3 copy of the site | <http://foodbank-spatial-twin-sitebucket-fjguzww37olg.s3-website.us-east-2.amazonaws.com> |

The repository variable `TWIN_API_URL` points the GitHub Pages site at this API, so the viewer shows "Live from AWS". The data is still simulated: every payload carries `simulated: true`, and the real-floor sensor path in the architecture diagram is drawn dashed because it is designed and priced, not installed.

Measured on 2026-10-25: AWS Cost Explorer showed $0.00 billed for October 2026 (Lambda, HTTP API and S3 inside the free tier). 1,000 sequential `GET /telemetry` calls from a US cloud container at about 3 per second all succeeded, with a 67 ms median and 78 ms 95th-percentile round trip.

## Omniverse on a GPU (EC2)

Fargate has no GPUs, so Omniverse runs on EC2 GPU capacity that exists only while a render runs: an ECS service or AWS Batch job on a GPU Auto Scaling group with a minimum of 0, or a single instance launched for a session that terminates itself afterwards. The trial uses one g6.xlarge (NVIDIA L4) in us-east-2 running Omniverse Kit headless from NVIDIA's Isaac Sim container, opening `twin.usda`, rendering fixed cameras and then terminating. New accounts start with 0 GPU vCPU quota; this account was granted 4 on 2026-10-25. The trial ran on 2026-10-25: three runs, about 26 instance-minutes, about $0.36 at list price, each instance terminated afterwards. Isaac Sim 5.1 (Kit 107.3) crashed in the RTX scene database right after start, for the facility meshes and the splats alike, so the render is not working yet; see [RENDERING.md](RENDERING.md) and [COST_ESTIMATE.md](COST_ESTIMATE.md).
