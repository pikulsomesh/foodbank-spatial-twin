# OT security for the twin's sensors, gateways and data path

Most digital-twin security write-ups stop at the IT layer: the API, the website, the cloud account. The harder part of a real deployment is the **operational technology (OT)** underneath it: battery sensors in freezers, radio gateways bolted to steel racking, beacons on forklifts, and the refrigeration and dock equipment that keeps food safe. This page is the threat model and reference design for that layer, and how it plugs into the three-step pipeline (video to twin, data on AWS, Omniverse or the browser).

> **What is real and what is design.** The demo has **no real devices**. Its telemetry is simulated by one Lambda function behind an HTTP API (`simulated: true` in every payload). Only the controls listed under [Implemented today](#implemented-today-in-templateyaml) exist in [`template.yaml`](../template.yaml) and the CI workflow. Everything else on this page is a **design**, not built or tested. In the architecture diagrams, solid borders are implemented and dashed borders are design only; trust boundaries are drawn as red dashed lines. Costs are directional, from public list prices and what one individual AWS account could access.

## The principle: the twin watches, it does not drive

A food bank's process control already exists and already works: refrigeration controllers hold the freezer at temperature, dock door controllers open and close doors, forklift drivers move pallets. The twin adds **monitoring**: independent sensors, a picture of the building, and a view of what is happening.

The design keeps it that way:

1. **No write path from the cloud to the floor.** The twin's API only reads. No IoT policy lets a cloud principal publish to a device command topic; no Device Shadow desired state is used; nothing in AWS can change a setpoint, open a door or stop a truck. If the whole AWS account were taken over, an attacker could lie to the people looking at the twin, but could not warm a freezer.
2. **Add sensors, do not tap controllers.** Temperature and door sensors are separate battery devices with their own radios, not connections into the refrigeration controller or building management system. The existing local alarms on the refrigeration plant stay in place and stay authoritative.
3. **If controller data is ever wanted, read it at the edge and push it out.** An edge gateway in its own zone polls the controller read-only and publishes outward over TLS. Nothing inbound crosses into the plant network.

This is the main way a twin changes the attack surface. A **read-only twin** adds a new place where data can be **faked or stolen** (integrity and confidentiality), but not a new way to **change the physical process**. A twin that sends commands back (closed loop, "what-if" pushed to the floor, remote setpoint changes) turns the cloud into part of the control system, and would need the full OT treatment for the cloud side too: safety review, change control and a much higher security level for that conduit. This project deliberately stays read-only.

## What can go wrong (threat model)

The assets that matter, in order: **food safety** (product held at the right temperature), **people** (forklift traffic, dock doors), **operational continuity** (alerts reach staff), then **data** (inventory and location history).

| # | Threat | Where | Consequence for a food bank | Main controls |
|---|---|---|---|---|
| T1 | **Spoofed "all normal" readings** hide a real freezer failure | Sensor radio, gateway, MQTT, ingest | Spoiled product discarded or, worse, distributed | Per-device identity and keys, frame counters, plausibility checks, heartbeat-loss alarms, the refrigeration plant's own local alarm kept independent |
| T2 | **False alarms** flood staff | Same | Alarm fatigue, real excursion ignored | Same, plus rate limits on alerts and cross-sensor correlation |
| T3 | **Cloned or stolen device credential** | A sensor or gateway taken off the wall | Attacker injects data as a trusted device | Unique per-device certificate or key, revocation, Device Defender audit for shared certificates and conflicting client IDs |
| T4 | **Compromised gateway** used to pivot into office IT or the plant network | LoRaWAN or BLE gateway on the floor | Ransomware or worse reaches systems that control things | Gateways on their own VLAN or cellular backhaul, outbound only, no inbound ports, no route to the plant or office networks |
| T5 | **Malicious firmware update** | OTA path | Every device of a model compromised at once | Signed firmware verified on the device, update job started by a separate role with human approval |
| T6 | **Cloud account takeover** | AWS | Twin lies, data leaks; no physical effect while the twin is read-only | No write path to the floor, least-privilege IAM, separate accounts, logging |
| T7 | **BLE beacon spoofing or replay** | Forklift beacons | Wrong truck location on the twin | Treat BLE zone location as advisory, never as a safety input; telematics with authenticated devices where location matters |
| T8 | **Jamming or gateway outage** | Radio, uplink | Twin goes stale while looking current | Show data age on every value, alarm on missing heartbeats, keep local alarms |
| T9 | **Over-permissive IoT policy** (any device can publish anywhere) | IoT Core | One device can forge readings for all others | Policy variables tie each certificate to its own topics; Device Defender audit flags overly permissive policies |

MITRE ATT&CK for ICS describes T1 and T2 as adversary techniques: falsified reporting messages that hide the real process state or distract operators. ATT&CK v19 moved this from the old *Spoof Reporting Message* (T0856) to [*Unauthorized Message: Reporting Message* (T1692.002)](https://attack.mitre.org/techniques/T1692/); the detection advice is the same idea used here: watch for loss-of-communication and compare reports against independent data.

## Zones and conduits

The reference design splits the system into zones with a defined conduit between each pair, the model used by the ISA/IEC 62443 series (62443-3-2 partitions a system into zones and conduits and sets a target security level for each). It also lines up with the Purdue levels used in NIST SP 800-82 Rev. 3 to describe a segmented OT network. The security levels below are a reasoned target for a food bank, not an assessment carried out under the standard.

| Zone | Purdue level | Contents | Trust |
|---|---|---|---|
| **Z0 Plant control** (out of scope, untouched) | 0 to 2 | Refrigeration controllers, compressors, dock door controllers, fire and access systems | Not connected to the twin at all |
| **Z1 Field devices** | 0 to 1 | LoRaWAN temperature and door sensors, BLE beacons on forklifts | Physically exposed; trust the key, not the box |
| **Z2 Edge** | 2 | LoRaWAN gateways, BLE-to-MQTT gateways, optional AWS IoT Greengrass edge device | Physically exposed; holds credentials |
| **Z3 OT DMZ** | 3.5 | Site firewall or cellular router for the edge zone | Only outbound TLS allowed |
| **Z4 Cloud ingest** | cloud | AWS IoT Core for LoRaWAN, IoT Core MQTT broker, rules, ingest Lambda, DynamoDB | Accepts device data, never sends commands |
| **Z5 Twin and viewers** | cloud / IT | HTTP API, read Lambda, S3 and CloudFront, browser, Omniverse on an EC2 GPU | Read-only view |
| **Z6 Business IT** | 4 to 5 | Warehouse management system, office network | Exports pulled, never pushed into OT |
| **Z7 Security management** | cloud | Provisioning, certificate authority, code signing, OTA jobs, Device Defender, CloudWatch | Separate role and, ideally, a separate AWS account |

| Conduit | From to | Protocol and protection | Direction |
|---|---|---|---|
| C1 | Z1 to Z2, LoRa radio | LoRaWAN, AES-128 per-device keys; payload encrypted on the device | Uplink only for sensors |
| C2 | Z1 to Z2, BLE radio | BLE advertisements, **not authenticated** | Broadcast; advisory data only |
| C3 | Z2 to Z4 via Z3 | LoRa Basics Station over TLS with gateway X.509 certificate; MQTT over TLS (port 8883 or 443) with per-device X.509 certificate | Outbound from the site only |
| C4 | Z6 to Z4 | WMS nightly export to S3 or read by a Lambda through the WMS API | Pulled; no OT access |
| C5 | Z4 to Z5 | DynamoDB read by the API Lambda | One way into the view |
| C6 | Z5 to users | HTTPS only | Read only (GET) |
| C7 | Z7 to Z2 | Signed firmware over AWS IoT Jobs | The only path toward the floor: change-controlled, human-approved, verified on the device |

The one-way rule is enforced in three places: the firewall (no inbound to the edge zone), IoT policies (devices may publish their own telemetry; no cloud role may publish to device topics), and the API (GET routes only). A hardware data diode is not needed at this risk level because the twin carries no commands, but it is the upgrade path if controller data (Z0) is ever read.

## Reference design by layer

### Device identity and provisioning

- **One identity per device, never shared.** For MQTT devices (BLE gateways, an edge gateway), each gets its own X.509 client certificate, registered in AWS IoT Core. LoRaWAN sensors have their own per-device AES-128 root keys (AppKey) held by the device and AWS IoT Core for LoRaWAN; LoRaWAN gateways each have their own X.509 certificate for the Basics Station connection.
- **Provisioning.** For a few dozen devices, register each certificate by hand or by script during installation. For a larger fleet, use [AWS IoT fleet provisioning](https://docs.aws.amazon.com/iot/latest/developerguide/provision-wo-cert.html) with a **pre-provisioning hook** (a Lambda that checks each serial number against the installation list before a certificate is issued). Treat the claim certificate as sensitive and disable it after the install window. Just-in-time provisioning fits if devices arrive with certificates from your own registered CA.
- **Keys in hardware where the device allows it.** An edge gateway running [AWS IoT Greengrass V2 can keep its private key in a TPM or HSM through PKCS#11](https://docs.aws.amazon.com/greengrass/v2/developerguide/hardware-security.html) (Linux only). Most low-cost BLE gateways store keys in flash, which is why physical access and revocation matter.
- **Rotation and revocation.** Track expiry with the Device Defender audit check for expiring certificates. A device that is lost, stolen or replaced has its certificate set to **REVOKED**; [a revoked certificate cannot be set back to active](https://docs.aws.amazon.com/iot/latest/developerguide/revoke-ca-cert.html). LoRaWAN session keys are refreshed through the join process.

### Least-privilege IoT policies

Each device may connect only as itself and publish only to its own topic. AWS IoT policy variables make one policy work for every device: connect only with a client ID equal to the thing name bound to the certificate, and only while the thing is attached ([connect policy examples](https://docs.aws.amazon.com/iot/latest/developerguide/connect-policy.html), [thing policy variables](https://docs.aws.amazon.com/iot/latest/developerguide/thing-policy-variables.html)):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iot:Connect",
      "Resource": "arn:aws:iot:REGION:ACCOUNT:client/${iot:Connection.Thing.ThingName}",
      "Condition": { "Bool": { "iot:Connection.Thing.IsAttached": "true" } }
    },
    {
      "Effect": "Allow",
      "Action": "iot:Publish",
      "Resource": "arn:aws:iot:REGION:ACCOUNT:topic/foodbank/site1/telemetry/${iot:Connection.Thing.ThingName}"
    }
  ]
}
```

There is deliberately **no `iot:Subscribe` or `iot:Receive`** for sensors and BLE gateways: they never take instructions. Only an edge device that receives signed OTA jobs gets subscribe rights, and only on its own `$aws/things/<name>/jobs/` topics. The ingest rule reads `foodbank/site1/telemetry/+` and records the topic's thing name with each reading, so a device cannot claim to be another one. This policy is a sketch to be validated against the IoT policy documentation before use.

### Transport security

- MQTT devices connect with **TLS mutual authentication** on port 8883 (or 443 with ALPN). AWS IoT Core [supports TLS 1.2 and 1.3](https://docs.aws.amazon.com/iot/latest/developerguide/transport-security.html); pick the default `IoTSecurityPolicy_TLS13_1_2_2022_10` or stricter in the domain configuration.
- LoRaWAN gateways reach AWS IoT Core for LoRaWAN through LoRa Basics Station over a TLS-protected WebSocket, authenticated with the gateway's X.509 certificate, and the CUPS endpoint can update the gateway's credentials ([data and transport security](https://docs.aws.amazon.com/iot/latest/developerguide/connect-iot-lorawan-security.html), [adding a gateway](https://docs.aws.amazon.com/iot-wireless/latest/developerguide/lorawan-onboard-gateway-add.html)). Sensors encrypt their payload with AES-128 before it leaves the device, so a compromised gateway can drop or delay messages but cannot read or forge them without the device keys.
- Note the trust point: AWS IoT Core for LoRaWAN decrypts the payload and hands it to the rules engine, so the LoRaWAN application keys live in AWS. That is acceptable for temperature data; a site that wants to keep them on its own servers would use its own LoRaWAN network and application server instead.

### Gateways, edge and the network

- **Segment.** Put every gateway on its own VLAN (or its own cellular router), with a firewall rule that allows only outbound TCP 8883 and 443 to the AWS IoT endpoints and DNS and NTP, and **no inbound ports**. No route from the gateway VLAN to the office network or the refrigeration controllers. A cellular backhaul avoids the office network entirely.
- **Harden.** Change default passwords, turn off unused services (local web UI, SSH) after setup, keep firmware current, log in only from the management network. Mount gateways out of reach and note their serial numbers.
- **Optional edge device (Greengrass).** Only if the food bank later wants data from the refrigeration controllers or building management system: one hardened Linux box in Z2, key in a TPM, reading the controller with a read-only account (for example Modbus or BACnet reads only, never writes), and publishing outward. This is the only place the design touches Z0, and it reads.

### Monitoring and anomaly detection

- **[AWS IoT Device Defender](https://docs.aws.amazon.com/iot-device-defender/latest/devguide/what-is-device-defender.html)** for devices that connect to IoT Core as MQTT principals (BLE gateways, edge device). **Audit** checks the configuration, including overly permissive policies, certificates shared by several devices, conflicting client IDs and expiring certificates. **Detect** watches device behaviour (message counts and sizes, connection attempts, authorization failures) against rules or ML Detect baselines. **Mitigation actions** can, for example, move a device to a quarantine thing group or set its certificate inactive. LoRaWAN sensors are not MQTT principals, so for them the checks below do the work.
- **Data plausibility in the ingest Lambda** (design): reject readings outside a physical range for the sensor, impossible rates of change (a freezer cannot move 20 °C in a minute), duplicate or out-of-order frame counts, and a door "closed" while the zone's temperature curve says it is open. Flag rather than silently drop, so an attack shows up as an alert.
- **Heartbeat and data age.** Every reading carries its age; the twin greys out stale values; a missing heartbeat raises an alert. This is the defence against "frozen normal" (T1, T8), and it is the one MITRE recommends for spoofed reporting: watch for loss of communication.
- **Independent ground truth.** Keep the refrigeration plant's own local alarm and manual temperature logs. The twin is a second opinion, never the only one.
- **Account-level.** CloudTrail and CloudWatch alarms on IoT policy changes, certificate status changes and new devices.

### Secure updates (OTA)

The only design path from the cloud toward a device is a firmware update, so it gets the strictest control: firmware signed with a dedicated signing profile (Code Signing for AWS IoT, or the vendor's own signing), [verified on the device before it is installed](https://docs.aws.amazon.com/freertos/latest/userguide/ota-manager.html), delivered by an AWS IoT Jobs document that only a separate deployment role can create, after a person approves it. Many off-the-shelf LoRaWAN sensors are updated by the vendor's tool rather than over the air; in that case the same rule applies to who may run the tool and which images are allowed.

### Twin integrity

- The API returns `simulated: true` today. With real devices it becomes `false` and each reading also carries `device_id`, `received_at` and a `checks` field (passed, flagged), so the browser and Omniverse can show **where a value came from and how old it is**.
- Write access to the twin's scene (S3, OpenUSD files) is limited to the publishing pipeline; viewers read only. A tampered USD file is a misleading picture, not a physical effect, but the twin should still be versioned (S3 versioning, design) so a change can be traced.

## Implemented today (in template.yaml)

Checked against [`template.yaml`](../template.yaml), [`src/telemetry/app.py`](../src/telemetry/app.py) and [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) on 2026-10-27. `tests/test_ot_boundary.py` checks the first four rows on every pull request.

| Control | How it is implemented |
|---|---|
| Read-only API: no write method to anything | The HTTP API has three routes, all `GET` (`/telemetry`, `/operations`, `/health`); CORS allows only `GET` and `OPTIONS`. The handler ignores request bodies and query strings |
| No IoT resources and no device credentials exist | The template declares no `AWS::IoT*` resources, certificates, policies or things |
| Least-privilege function role | The function declares no `Policies` or `Role`, so AWS SAM generates a role with only the basic Lambda execution permissions (write its own logs) |
| Cost and abuse ceiling | API throttling: 5 requests per second, burst 10 (`ThrottleRate`, `ThrottleBurst`) |
| Encryption in transit for the API | API Gateway [exposes HTTPS endpoints only](https://docs.aws.amazon.com/apigateway/latest/developerguide/data-protection-encryption.html) |
| Fail fast | 128 MB, 3-second timeout, no VPC, no stored state |
| Public data only in the public bucket | S3 bucket policy grants `s3:GetObject` only; ACLs blocked (`BlockPublicAcls`, `IgnorePublicAcls`); the bucket holds only the viewer and twin assets |
| Bounded logs | CloudWatch log retention 7 days |
| CI least privilege | The Pages workflow runs with `contents: read`; only the deploy job gets `pages: write` and `id-token: write` |
| Honest data | Every payload says `simulated: true` |

Known gaps in what is deployed: the S3 website endpoint serves HTTP only (the public site is served over HTTPS from GitHub Pages instead); the API has no authentication because its data is public and simulated; there is no WAF. With real data, the API would sit behind CloudFront with authentication (for example Amazon Cognito or IAM) before anything non-public is exposed.

## Design only (not built)

Everything in [Reference design by layer](#reference-design-by-layer): devices, certificates, provisioning, IoT policies, network segmentation, the edge device, Device Defender, plausibility checks, signed OTA, S3 versioning, separate AWS accounts. These are dashed in the diagrams.

## Cost of the security layer (directional)

For the real-floor case in [COST_ESTIMATE.md](COST_ESTIMATE.md) (24 temperature and 21 door sensors, 2 LoRaWAN gateways, 10 BLE gateways, 8 beacons):

- **Certificates and IoT policies:** no charge in AWS IoT Core itself; device certificates issued by AWS IoT Core cost nothing extra. A private CA (AWS Private CA) is optional at this size and is priced per CA per month, so check its pricing page before choosing it.
- **Device Defender:** billed per active device principal per month for Audit and per metric datapoint for Detect, with no minimum fee ([pricing](https://aws.amazon.com/iot-device-defender/pricing/)). At about a dozen MQTT principals this should be cents to a few dollars a month; we could not confirm the current us-east-1 rates, so check the pricing page.
- **Plausibility checks:** run inside the existing ingest Lambda; no extra service.
- **Network:** a VLAN on the existing switch and firewall is configuration, not hardware. A separate cellular router for the gateways is a few hundred dollars plus a data plan.
- **Hardware security:** an edge device with a TPM is only needed if controller data is ever read.

The security layer adds little to the AWS bill. The real cost is installation discipline: unique credentials per device, a record of what is installed where, and someone who reviews alerts.

## Standards and sources

Checked 2026-10-27. Only claims that could be traced to these pages are made above.

- NIST SP 800-82 Rev. 3, *Guide to Operational Technology (OT) Security*, September 2023: <https://csrc.nist.gov/pubs/sp/800/82/r3/final>. An initial public draft of Rev. 4 was published in September 2026 for comment; Rev. 3 remains the final version.
- ISA/IEC 62443 series (zones and conduits, security levels; part 3-2 risk assessment and system design): <https://www.isa.org/standards-and-publications/isa-standards/isa-iec-62443-series-of-standards>; IEC overview: <https://syc-se.iec.ch/deliveries/cybersecurity-guidelines/security-standards-and-best-practices/iec-62443/>
- MITRE ATT&CK for ICS, Unauthorized Message (T1692), Reporting Message sub-technique: <https://attack.mitre.org/techniques/T1692/>
- AWS IoT Core: transport security <https://docs.aws.amazon.com/iot/latest/developerguide/transport-security.html>; revoking a client certificate <https://docs.aws.amazon.com/iot/latest/developerguide/revoke-ca-cert.html>; connect policy examples <https://docs.aws.amazon.com/iot/latest/developerguide/connect-policy.html>; fleet provisioning <https://docs.aws.amazon.com/iot/latest/developerguide/provision-wo-cert.html>
- AWS IoT Core for LoRaWAN, data and transport security: <https://docs.aws.amazon.com/iot/latest/developerguide/connect-iot-lorawan-security.html>
- AWS IoT Device Defender: <https://docs.aws.amazon.com/iot-device-defender/latest/devguide/what-is-device-defender.html>; mitigation actions <https://docs.aws.amazon.com/iot-device-defender/latest/devguide/dd-mitigation-actions.html>
- AWS IoT Greengrass V2 hardware security integration: <https://docs.aws.amazon.com/greengrass/v2/developerguide/hardware-security.html>
- FreeRTOS OTA Update Manager (signed updates): <https://docs.aws.amazon.com/freertos/latest/userguide/ota-manager.html>
- API Gateway data encryption (HTTPS only): <https://docs.aws.amazon.com/apigateway/latest/developerguide/data-protection-encryption.html>
- LoRa Alliance, LoRaWAN security FAQ: <https://lora-alliance.org/wp-content/uploads/2020/11/la_faq_security_0220_v1.2_0.pdf>
- Azure and Google Cloud equivalents used in the design-only diagrams: Microsoft Defender for IoT (agentless OT network monitoring) <https://learn.microsoft.com/en-us/azure/defender-for-iot/organizations/overview>; Azure IoT Hub Device Provisioning Service X.509 attestation <https://learn.microsoft.com/azure/iot-dps/concepts-x509-attestation>; Google Cloud Certificate Authority Service <https://docs.cloud.google.com/certificate-authority-service/docs/ca-service-overview>
