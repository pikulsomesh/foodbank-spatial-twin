# Cost estimate: running the twin on real floor data

This estimate covers one representative food bank of about 135,000 sq ft, the size the operations model is anchored to, with real sensors in place of the simulator. It is an **estimate from list prices checked on 2026-10-19**, not a quote and not a measured bill. All prices are public list prices, and what was tried was limited to what an individual AWS account could access (its quotas and organization policies). An enterprise account may see negotiated discounts, savings plans, different quotas and region policies, so treat every figure as directional. Taxes, shipping, installation labour and staff time are not included. The assumptions behind each count are listed below so that any of them can be changed.

## Summary

| | One-time | Monthly |
|---|---|---|
| Core: temperature and door sensors, zone-level forklift location, AWS backend | about **$2,700** hardware | about **$12** AWS |
| Optional: Omniverse on an AWS GPU, on demand for 10 h a week | none | about **$124** |
| Optional: forklift telematics instead of BLE, 8 trucks | vendor-dependent | **$200 to $400** |
| Optional: 360° camera to re-capture the building | **$400 to $560** | none |

At this scale the AWS bill is small. A new account's free tier covers most of it for the first year. The real costs are the hardware, installing it, and the staff time to keep the master data (zones, docks, products) current. A GPU running Omniverse around the clock would dominate everything else at about $2,100 a month, so the plan is to view the twin in the browser day to day and start Omniverse only when needed.

## Measured so far (demo, simulated data)

The SAM stack was deployed to us-east-2 on 2026-10-25 (the account's organization policy blocks Lambda, CloudFormation and API Gateway in us-east-1). AWS Cost Explorer showed **$0.00** billed for October 2026 at that date: Lambda, the HTTP API and S3 sit inside the free tier at demo traffic. Over 1,000 sequential `GET /telemetry` requests from a US cloud container, paced at about 3 per second, all succeeded, with a 67 ms median and 78 ms 95th-percentile round trip. In an earlier run of the same size, 1.4% of requests were throttled (HTTP 429) by the API's deliberate 5-per-second cost ceiling.

## Building the twin: measured cost of this run

The whole twin was built from one publicly available news video in seven days (2026-10-19 to 2026-10-25), with no CAD drawings, no laser scan and no site visit. What that cost:

| Step | What ran | Measured | Cost |
|---|---|---|---|
| 1. Video to world-model capture | World Labs Marble 2 `images2PosedRGBD` on 20 frames for 3 zones (8 + 6 + 6) | 200 credits | **$0.20** |
| | Two `atlasGenerate` attempts that failed (`EXECUTION_FAILED`) | not shown on the usage page; worst case 800 credits at list price | at most $0.80 |
| | Frame extraction, RGBD fusion, COLMAP and 3D Gaussian Splatting training for 4 zones, OpenUSD conversion | local CPU in a cloud container | $0 extra |
| 1b. Blender facility model | `scripts/build_facility.py` (headless `bpy`), whole building from the designed footprints | about 5 s per build | $0 |
| 2. Data on AWS | SAM stack (S3, HTTP API, Lambda) in us-east-2, October 2026 | Cost Explorer | **$0.00** (free tier) |
| 3. Omniverse on a rented GPU | One g6.xlarge (NVIDIA L4), Omniverse Kit headless, renders `twin.usda`, then terminates | **Pending:** runtime, instance-hours and billed cost will be filled in from the trial | **Pending** (planned at about $0.80 an hour, $2 to $3 in total) |
| **Total so far** | | | **$0.20 confirmed, at most $1.00**, before the GPU trial |

## Scaled to a full facility (directional)

The run above captured 3 zones partially and designed the rest. A complete capture of a facility of about 135,000 sq ft, using the same tools at list prices, might look like this. The zone count, frames per zone and GPU hours are assumptions; replace the GPU rate with the trial's measured figure once it is in.

| Item | Assumption | Estimate |
|---|---|---|
| World-model poses | 12 zones × 8 frames × 10 credits | about $1 |
| World-model generated views | 12 zones × 8 views × 50 credits | about $5 |
| 3DGS training on a GPU | about 1 GPU-hour per zone on a g6.xlarge at about $0.80 an hour | about $10 |
| Blender facility model | one script run from the facility's own footprints (master data) | $0 |
| Omniverse review sessions | 10 GPU-hours on g6.xlarge (compute only) or g6e.xlarge via the Marketplace listing ($2.86 an hour) | $8 to $29 |
| **Capture to deployed twin** | cloud spend only, staff time not included | **about $25 to $50** |

Running it afterwards, per facility, from the tables below:

| Scenario | One-time | Monthly | First year |
|---|---|---|---|
| Browser twin on simulated or exported data | none | about $0 to $12 | under $150 |
| Real sensors, browser twin | about $2,700 | about $12 | about **$2,850** |
| Real sensors, browser twin, Omniverse on demand 10 h a week | about $2,700 | about $136 | about **$4,330** |
| Ten facilities, real sensors, one shared on-demand Omniverse workstation | about $27,000 | about $244 | about **$30,000** |

The pattern is the point: capturing and modeling the building costs tens of dollars, and the AWS bill stays in the tens of dollars a month. The real costs are sensors, installation and the staff time to keep master data current. Omniverse Enterprise licensing for production use is separate and must be confirmed with NVIDIA.

## What gets tracked, and how

| On the floor | Device | Path into AWS | Twin field it replaces |
|---|---|---|---|
| Freezer, cooler, walk-in, blast chiller, market fridge and dock temperatures | LoRaWAN temperature sensor with external probe (Dragino LHT65N), every 5 min | AWS IoT Core for LoRaWAN, then a rule, Lambda and DynamoDB | `zones[].temp_c`, `alarm` |
| Cold-room and dock doors | LoRaWAN door contact (Dragino LDS02), on change plus a 15-min heartbeat | same | `zones[].door_open` |
| Forklift zone (which area each truck is in) | BLE beacon on each truck, BLE-to-MQTT gateway per area | AWS IoT Core (MQTT), then the same rule path | `forklifts[].position` (zone-level, not centimetre-level) |
| Inventory, inbound and outbound loads | The food bank's existing warehouse management system: nightly export to S3 or its API | Lambda reads it, maps to product families and areas | `/operations` inventory and flows |
| Building geometry | Phone or 360° walkthrough, re-captured when the layout changes | World model or local splat training, then OpenUSD | `site.usdz` captures |

The telemetry contract (`/telemetry`, `/operations`) does not change; real values replace the simulator behind it and the `simulated` flag becomes `false`. Amazon Timestream for LiveAnalytics is [closed to new customers since 20 June 2025](https://docs.aws.amazon.com/timestream/latest/developerguide/AmazonTimestreamForLiveAnalytics-availability-change.html) and Timestream for InfluxDB starts at a dedicated instance, so the estimate stores readings in DynamoDB with an S3 archive.

## Assumptions

- **24 temperature sensors**: freezer and cooler (6 each), kitchen walk-in and blast chillers (4), market fridges (4), receiving and dry storage (4).
- **21 door sensors**: 15 dock doors, 6 cold-room doors. About 100 door events per door per day.
- **2 LoRaWAN gateways**, for coverage through steel racking and insulated cold-room walls, with one spare path.
- **8 forklifts and pallet jacks, 10 BLE gateways** (one per operating area plus the dock apron). The fleet size is an assumption; the operations master data does not publish it.
- **20 people** watch the twin in the browser, polling every 5 s, 10 h a day, 22 days a month (3.2 million API calls).
- Sensor messages: 0.33 million LoRaWAN uplinks and 2.6 million MQTT messages per month.
- Prices are us-east-1 on-demand without free tier, so the monthly figure holds after the first year.

## Monthly AWS breakdown

| Service | Unit price | Monthly |
|---|---|---|
| IoT Core for LoRaWAN | $2.30 per million messages | $0.76 |
| IoT Core MQTT messaging and connectivity | $1.00 per million messages; $0.08 per million connection-minutes | $2.63 |
| IoT rules engine | $0.15 per million rules + $0.15 per million actions | $0.88 |
| DynamoDB on-demand | $0.625 per million writes; $0.125 per million reads; first 25 GB storage free | $2.62 |
| API Gateway HTTP API | $1.00 per million requests | $3.17 |
| Lambda (arm64, 128 MB) | $0.20 per million requests; $0.0000133334 per GB-second | $1.58 |
| CloudWatch Logs | $0.50 per GB ingested | $0.53 |
| S3 archive, CloudFront for the static site | $0.023 per GB-month; CloudFront free tier 1 TB and 10 million requests | about $0.10 |
| **Total** | | **about $12** |

Doubling the viewers or the sensor rate roughly doubles the relevant lines, so the total stays in the tens of dollars per month. The IoT Core rules and Device Shadow prices were shown on the AWS page only in other-region examples, so check the us-east-1 rate in the AWS Pricing Calculator.

## Hardware breakdown (one-time)

| Item | Unit price | Qty | Total |
|---|---|---|---|
| Dragino LHT65N temperature sensor with probe (US915) | $46.92 | 24 | $1,126 |
| Dragino LDS02 door sensor (US915) | $18.75 | 21 | $394 |
| Dragino LPS8N indoor LoRaWAN gateway | $170 | 2 | $340 |
| BLE beacon (Minew MBT02, about £7.41 at volume) | about $10 | 8 | $80 |
| BLE-to-MQTT gateway (Minew G1, £58.27) | about $75 | 10 | $750 |
| **Total** | | | **about $2,690** |

Pound prices were converted at roughly $1.29 per pound. Industrial alternatives cost more: a Milesight EM500-PT100 freezer probe sensor is €167 to €487, and a Milesight UG65 gateway is $555. Battery life in freezers is shorter than the datasheet figure at room temperature, so plan battery checks for the freezer sensors.

## Options that change the picture

- **Omniverse.** The NVIDIA Omniverse Production Workstation on AWS Marketplace charges $1.00 per hour on top of the EC2 instance; on a g6e.xlarge (1× L40S, $1.861 per hour) that is $2.86 per hour, about $124 a month at 10 h a week or about $2,090 a month always on. A g5.xlarge (1× A10G) is $1.006 per hour if the listing supports it. Omniverse Enterprise is licensed per GPU; resellers quote about $4,500 per GPU per year, which we could not confirm on an NVIDIA page. Confirm licensing with NVIDIA before production use.
- **Pay only while it runs.** Every part of the design can bill only while it is in use, which is shown as a cost-controls panel in each architecture diagram (`paper/diagrams/`).
  - The API and ingest functions (Lambda behind API Gateway) bill per request, so an idle twin costs almost nothing. This is already the cheapest option for the polling API.
  - Work shipped as a container image, such as a warehouse import that outlasts Lambda's 15-minute limit or a scene rebuild, can run on **Amazon ECS with AWS Fargate**: you hand AWS an image and a CPU and memory size, it runs the task with no servers to manage, and bills per second with a 1-minute minimum (us-east-1 Linux ARM: $0.0000089944 per vCPU-second, $0.0000009889 per GB-second; x86 about 25% more). At 0 tasks it costs nothing. An always-on 0.25 vCPU / 0.5 GB ARM task is about $7.21 a month, more than the whole demo API, so containers suit jobs, not the API. Fargate Spot is up to 70% cheaper for interruptible tasks.
  - **Fargate has no GPUs**, so Omniverse runs on EC2 GPU capacity: an ECS service or AWS Batch job whose Auto Scaling group has a minimum of 0, so an instance exists only while a render runs (or, by hand, an instance started for a session and stopped right after). A new account starts with 0 on-demand GPU (G and VT) vCPU quota, so request it before the first run. Spot lowers the price but the instance can be reclaimed mid-session. A stopped instance's EBS disk still bills, so snapshot and delete it if the GPU will sit unused for weeks.
  - Azure Container Apps and Google Cloud Run are the equivalents of Fargate and scale to zero by themselves. Both also offer serverless GPUs that scale to zero (Container Apps: A100, T4; Cloud Run: L4), which could host a headless renderer; Omniverse has not been tested on them.
  - A budget alert (the demo AWS account has a $10 one, set on the account rather than in the template) catches anything left running.
- **Forklift position.** BLE gives which area a truck is in, which is enough for the twin's dock and aisle views. Telematics subscriptions run about $25 to $50 per vehicle per month (ForkOn €22.70; Powerfleet and iWAREHOUSE figures are third-party estimates) and add hours, impacts and operator log-in. Centimetre-level UWB positioning is a quote-only purchase: a Pozyx kit covering about 1,000 m² starts at $4,970, and this building is about 12,500 m².
- **AWS IoT TwinMaker or IoT SiteWise.** Not needed: the twin already has its own scene (OpenUSD) and API. TwinMaker's tiered plans start at $220 a month for 800 entities.
- **Re-capturing the building.** A phone works; an Insta360 X4 is $425 to $560. World-model posing costs 10 credits per frame (a 20-frame zone is 200 credits, $0.20); locally trained splats cost no credits.

## Sources (accessed 2026-10-19)

- AWS IoT Core and IoT Core for LoRaWAN: https://aws.amazon.com/iot-core/pricing/
- DynamoDB on-demand: https://aws.amazon.com/dynamodb/pricing/on-demand/
- Lambda: https://aws.amazon.com/lambda/pricing/
- API Gateway: https://aws.amazon.com/api-gateway/pricing/
- CloudFront: https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/
- CloudWatch: https://aws.amazon.com/cloudwatch/pricing/
- Timestream: https://aws.amazon.com/timestream/pricing/
- IoT TwinMaker: https://aws.amazon.com/iot-twinmaker/pricing/
- EC2 g6e.xlarge and g5.xlarge (aggregator, AWS feed unreachable from here): https://calculator.holori.com/aws/ec2/g6e.xlarge/us-east-1, https://calculator.holori.com/aws/ec2/g5.xlarge/us-east-1
- Omniverse on AWS Marketplace: https://aws.amazon.com/marketplace/pp/prodview-p6ol6bkurke6u
- Dragino LHT65N: https://www.robotshop.com/es/products/sensor-temperatura-y-humedad-lorawan-lht65n-dragino-2400-mah-us915
- Dragino LDS02: https://www.robotshop.com/products/dragino-lorawan-door-sensor-us915
- Dragino LPS8N: https://robotshop.com/products/lps8n-indoor-lorawan-gateway-915-mhz-north-america
- Minew G1: https://www.digikey.co.uk/en/products/detail/minew/G1/26776367
- Milesight EM500-PT100: https://invibitshop.com/collections/milesight/ingress-protection_ip67
- Milesight UG65: https://embeddedworks.net/product/sens820
- Pozyx: https://www.pozyx.io/store
- AWS Fargate (accessed 2026-10-25): https://aws.amazon.com/fargate/pricing/
- Azure Container Apps serverless GPUs (accessed 2026-10-25): https://learn.microsoft.com/azure/container-apps/gpu-serverless-overview
- Cloud Run GPUs (accessed 2026-10-25): https://cloud.google.com/run/docs/configuring/services/gpu
- Forklift telematics: https://mpi.motionminers.com/en/offerings/forkon-forkx-platform-and-forklink1-telematics, https://airpinpoint.com/equipment-tracking/forklift-tracking-system
- Insta360 X4: https://store.insta360.com/in/product/26
