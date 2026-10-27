#!/usr/bin/env python3
"""Write the AWS, Azure and GCP architecture diagrams as draw.io files.

All three share one layout so they can be compared side by side; only the
cloud services change. Solid borders are implemented in the repository today
(with simulated data); dashed borders are the real-floor path priced in
docs/COST_ESTIMATE.md; red dashed lines are the OT trust boundaries described in
docs/OT_SECURITY.md. Export with draw.io desktop:

    python3 paper/diagrams/make_diagrams.py
    for f in aws azure gcp; do
      xvfb-run -a drawio -x -f pdf --crop -o paper/figures/arch_$f.pdf paper/diagrams/arch_$f.drawio --no-sandbox
      xvfb-run -a drawio -x -f png -s 2 -o paper/figures/arch_$f.png paper/diagrams/arch_$f.drawio --no-sandbox
    done

GCP icons are the draw.io GCP library's embedded SVGs (gcp_icons.json),
extracted from draw.io desktop 26.2.15.
"""
import json
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
GCP = json.loads((HERE / "gcp_icons.json").read_text())

AWS_COLORS = {"compute": "#ED7100", "storage": "#7AA116", "database": "#C925D1",
              "network": "#8C4FFF", "iot": "#7AA116", "mgmt": "#E7157B",
              "integration": "#E7157B", "security": "#DD344C", "finance": "#7AA116"}


def aws(icon, cat):
    return ("sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;strokeColor=#ffffff;"
            f"fillColor={AWS_COLORS[cat]};dashed=0;verticalLabelPosition=bottom;verticalAlign=top;"
            "align=center;html=1;fontSize=11;fontStyle=0;aspect=fixed;labelBackgroundColor=#ffffff;"
            f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{icon};")


def azure(path):
    return ("image;aspect=fixed;html=1;labelBackgroundColor=#ffffff;points=[];align=center;fontSize=11;"
            f"verticalLabelPosition=bottom;verticalAlign=top;image=img/lib/azure2/{path};")


def gcp(key):
    return ("shape=image;aspect=fixed;html=1;labelBackgroundColor=#ffffff;points=[];align=center;fontSize=11;"
            f"verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;image={GCP[key]};")


# Cloud-specific services: logical id -> (label, style) per cloud.
CLOUDS = {
    "aws": {
        "title": "AWS Cloud",
        "lns": ("AWS IoT Core for LoRaWAN<br>(network server)", aws("iot_core", "iot")),
        "mqtt": ("AWS IoT Core<br>(MQTT broker)", aws("iot_core", "iot")),
        "rules": ("IoT Core rules engine", aws("iot_core", "iot")),
        "ingest": ("AWS Lambda<br>ingest and normalize", aws("lambda", "compute")),
        "etl": ("AWS Lambda<br>WMS import", aws("lambda", "compute")),
        "db": ("Amazon DynamoDB<br>latest readings, history", aws("dynamodb", "database")),
        "obj": ("Amazon S3<br>twin assets, archive", aws("s3", "storage")),
        "api": ("Amazon API Gateway<br>HTTP API", aws("api_gateway", "network")),
        "apifn": ("AWS Lambda (arm64)<br>GET /telemetry, /operations", aws("lambda", "compute")),
        "cdn": ("Amazon CloudFront", aws("cloudfront", "network")),
        "mon": ("Amazon CloudWatch<br>logs, alarms", aws("cloudwatch", "mgmt")),
        "alert": ("Amazon SNS<br>excursion alerts", aws("sns", "integration")),
        "iam": ("AWS IAM", aws("identity_and_access_management", "security")),
        "budget": ("AWS Budgets", aws("budgets", "finance")),
        "gpu": ("ECS on EC2 GPU (g6e)<br>Omniverse, scales to 0<br>between renders", aws("ec2", "compute")),
        "dd": ("AWS IoT Device Defender<br>audit, detect", aws("iot_device_defender", "iot")),
        "prov": ("IoT Core registry<br>per-device X.509", aws("iot_core", "iot")),
        "edge": "Optional edge: AWS IoT Greengrass,<br>key in TPM, read-only poll of<br>controllers, receives signed OTA",
        "built": "<b>Built</b> (AWS SAM template, simulated data): GET-only HTTPS API, throttled to 5 requests/s (burst 10), "
                 "function role that can only write its own logs, no IoT credentials or device policies in the cloud, 7-day log retention.",
        "cost": ["API and ingest: Lambda and API Gateway bill per request, so an idle twin costs almost nothing.",
                 "Jobs packaged as a container image (WMS import, scene builds): ECS on Fargate, billed per second "
                 "with a 1-minute minimum, scaled to 0 tasks when idle. Fargate has no GPU.",
                 "Omniverse: ECS or AWS Batch on EC2 GPU capacity (on demand or Spot) with a minimum of 0, so an instance exists only while a render runs. New accounts need a GPU quota request.",
                 "AWS Budgets alert on the account ($10 for the demo; not in the template)."],
        "note": "Solid: in the repository today and deployed in us-east-2 (AWS SAM template; simulated data). "
                "Dashed: real-floor path priced in docs/COST_ESTIMATE.md from public list prices; directional, enterprise pricing may differ. "
                "Red dashed: OT trust boundaries (docs/OT_SECURITY.md).",
    },
    "azure": {
        "title": "Microsoft Azure",
        "lns": ("LoRaWAN network<br>server (partner)", None),
        "mqtt": ("Azure IoT Hub<br>(MQTT)", azure("iot/IoT_Hub.svg")),
        "rules": ("Azure Event Grid<br>(IoT Hub routing)", azure("integration/Event_Grid_Topics.svg")),
        "ingest": ("Azure Functions<br>ingest and normalize", azure("compute/Function_Apps.svg")),
        "etl": ("Azure Functions<br>WMS import", azure("compute/Function_Apps.svg")),
        "db": ("Azure Cosmos DB<br>(serverless)", azure("databases/Azure_Cosmos_DB.svg")),
        "obj": ("Azure Blob Storage<br>twin assets, archive", azure("storage/Storage_Accounts.svg")),
        "api": ("Azure API Management<br>(Consumption)", azure("integration/API_Management_Services.svg")),
        "apifn": ("Azure Functions<br>GET /telemetry, /operations", azure("compute/Function_Apps.svg")),
        "cdn": ("Azure Front Door", azure("networking/Front_Doors.svg")),
        "mon": ("Azure Monitor<br>logs, alerts", azure("management_governance/Monitor.svg")),
        "alert": ("Azure Monitor<br>action group alerts", azure("management_governance/Alerts.svg")),
        "iam": ("Entra ID, RBAC", azure("identity/Entra_Managed_Identities.svg")),
        "budget": ("Cost Management<br>budgets", azure("general/Cost_Budgets.svg")),
        "gpu": ("NVads A10 v5 VM<br>Omniverse, on demand<br>or Spot, deallocated after", azure("compute/Virtual_Machine.svg")),
        "dd": ("Microsoft Defender for IoT<br>(on-site sensor, portal)", azure("security/Azure_Defender.svg")),
        "prov": ("IoT Hub DPS<br>X.509 attestation", azure("iot/Device_Provisioning_Services.svg")),
        "edge": "Optional edge gateway (design),<br>key in TPM, read-only poll of<br>controllers, receives signed updates",
        "built": "<b>Built:</b> nothing on Azure; this is a design only.",
        "cost": ["API and ingest: Functions (Consumption) or Container Apps, which scale to zero and bill per second.",
                 "Omniverse: a GPU VM on demand or Spot, deallocated after a session. Container Apps serverless GPUs "
                 "(A100, T4) also scale to zero; not tested with Omniverse.",
                 "Cost Management budget alert."],
        "note": "Equivalent design, not deployed. Azure IoT Hub has no built-in LoRaWAN network server, "
                "so a partner network server forwards uplinks to IoT Hub.",
    },
    "gcp": {
        "title": "Google Cloud",
        "lns": ("LoRaWAN network<br>server (partner)", None),
        "mqtt": ("MQTT broker (partner<br>or self-hosted)", None),
        "rules": ("Pub/Sub", gcp("data analytics pub sub")),
        "ingest": ("Cloud Run functions<br>ingest and normalize", gcp("compute cloud run")),
        "etl": ("Cloud Run functions<br>WMS import", gcp("compute cloud run")),
        "db": ("Firestore<br>latest readings, history", gcp("databases firestore")),
        "obj": ("Cloud Storage<br>twin assets, archive", gcp("storage cloud storage")),
        "api": ("Cloud Run<br>HTTPS endpoint", gcp("compute cloud run")),
        "apifn": ("Cloud Run service<br>GET /telemetry, /operations", gcp("compute cloud run")),
        "cdn": ("Cloud CDN", gcp("networking cloud cdn")),
        "mon": ("Cloud Logging,<br>Cloud Monitoring", gcp("management tools logging")),
        "alert": ("Cloud Monitoring<br>alerting", gcp("management tools monitoring")),
        "iam": ("Cloud IAM", None),
        "budget": ("Cloud Billing budgets", None),
        "gpu": ("Compute Engine G2 (L4)<br>Omniverse, on demand<br>or Spot, stopped after", gcp("compute compute engine")),
        "dd": ("OT network monitoring<br>(partner)", None),
        "prov": ("Certificate Authority<br>Service", None),
        "edge": "Optional edge gateway (design),<br>key in TPM, read-only poll of<br>controllers, receives signed updates",
        "built": "<b>Built:</b> nothing on Google Cloud; this is a design only.",
        "cost": ["API and ingest: Cloud Run services and functions scale to zero when no requests arrive.",
                 "Omniverse: a G2 VM on demand or Spot, stopped after a session. Cloud Run GPUs (L4) also scale "
                 "to zero; not tested with Omniverse.",
                 "Cloud Billing budget alert."],
        "note": "Equivalent design, not deployed. Google Cloud IoT Core was retired in August 2023, "
                "so devices reach Pub/Sub through a partner or self-hosted MQTT broker and network server.",
    },
}

# Shared layout. Icons are 48 px; (x, y) is the icon's top-left corner.
SERVICES = {
    "obj": (675, 146), "cdn": (820, 146),
    "lns": (680, 374), "mqtt": (680, 470),
    "rules": (790, 430), "ingest": (930, 430), "db": (1070, 430), "apifn": (1210, 430),
    "api": (1210, 280), "gpu": (1350, 280),
    "etl": (1070, 570),
    "prov": (680, 590), "dd": (830, 590),
    "iam": (680, 705), "budget": (820, 705), "mon": (930, 705), "alert": (1070, 705),
}
IMPLEMENTED = {"obj", "api", "apifn", "mon", "iam"}  # Budgets is set on the account, not in the template

BOX = "rounded=1;whiteSpace=wrap;html=1;fontSize=11;arcSize=12;"
BOXES = {  # id: (label, x, y, w, h, fill, implemented)
    "video": ("Phone or 360° walkthrough video", 35, 130, 120, 50, "#F5F5F5", True),
    "frames": ("ffmpeg frame extraction", 175, 130, 100, 50, "#FFF4E5", True),
    "wl": ("World Labs Marble API<br>(poses, metric depth)", 300, 102, 130, 40, "#FFF4E5", True),
    "train": ("COLMAP + local 3DGS<br>trainer (CPU or GPU)", 300, 150, 130, 40, "#FFF4E5", True),
    "blender": ("Blender facility model<br>(bpy, designed layout)", 300, 198, 130, 40, "#FFF4E5", True),
    "usd": ("<b>OpenUSD twin</b><br>twin.usda, facility.usdz,<br>zone .usdz / .spz,<br>operations.usda", 450, 110, 120, 110, "#E8F0FE", True),
    "sensors": ("LoRaWAN temperature and door sensors (freezer, cooler, kitchen, docks)", 40, 300, 190, 54, "#F5F5F5", False),
    "lgw": ("LoRaWAN gateways", 40, 380, 190, 36, "#F5F5F5", False),
    "ble": ("BLE beacons on forklifts,<br>BLE-to-MQTT gateways", 40, 470, 190, 48, "#F5F5F5", False),
    "wms": ("Warehouse management system<br>(inventory, loads)", 40, 570, 190, 48, "#F5F5F5", False),
    "plant": ("Refrigeration controllers, dock door<br>controls (existing). Not connected;<br>their local alarms stay in charge", 40, 660, 190, 60, "#FDECEA", False),
    "fw": ("Site firewall or cellular router:<br>outbound TLS only (8883, 443),<br>no inbound ports, own VLAN", 300, 290, 250, 56, "#FDECEA", False),
    "edge": ("", 300, 660, 250, 60, "#FDECEA", False),
    "browser": ("Browser viewer (three.js, Spark)<br>GitHub Pages today", 1460, 145, 210, 50, "#E6F4EA", True),
    "omni": ("NVIDIA Omniverse Kit app;<br>extension binds live values", 1460, 279, 210, 50, "#E6F4EA", False),
    "blend": ("Blender or usdview on a desktop<br>opens the same USD files", 1460, 420, 210, 50, "#E6F4EA", True),
    "staff": ("Staff phones (SMS, email)", 1460, 709, 210, 40, "#E6F4EA", False),
}

def cell(cid, value, style, x, y, w, h, parent="1"):
    return (f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{escape(style)}" vertex="1" parent="{parent}">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


LABEL_POS = {("ingest", "mon"): -0.55, ("wms", "etl"): -0.45}  # move a label off a crossing line
PORTS = {("browser", "api"): "exitX=0.5;exitY=1;entryX=0.5;entryY=0;",
         ("edge", "mqtt"): "exitX=1;exitY=0.5;entryX=0;entryY=0.85;"}


def edge(eid, src, dst, label="", dashed=False):
    style = PORTS.get((src, dst), "") + ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;fontSize=10;endArrow=block;endFill=1;"
             "strokeColor=#545B64;labelBackgroundColor=#ffffff;" + ("dashed=1;" if dashed else ""))
    return (f'<mxCell id="{eid}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry x="{LABEL_POS.get((src, dst), 0)}" relative="1" as="geometry"/></mxCell>')


EDGES = [  # (src, dst, label)
    ("video", "frames", ""), ("frames", "wl", ""), ("frames", "train", ""),
    ("wl", "usd", ""), ("train", "usd", ""), ("blender", "usd", ""),
    ("usd", "obj", "publish"), ("obj", "cdn", ""), ("cdn", "browser", "site, models, USD"),
    ("browser", "api", "poll every 5 s"), ("api", "apifn", ""), ("db", "apifn", ""),
    ("omni", "gpu", ""), ("gpu", "api", "live values"),
    ("sensors", "lgw", ""), ("lgw", "lns", "TLS, gateway X.509"), ("ble", "mqtt", "MQTT over TLS, device X.509"),
    ("lns", "rules", ""), ("mqtt", "rules", ""), ("rules", "ingest", ""), ("ingest", "db", ""),
    ("wms", "etl", "nightly export, pulled"), ("etl", "db", ""),
    ("ingest", "mon", "metrics"), ("plant", "edge", "read-only poll"), ("edge", "mqtt", "MQTT, mTLS"),
    ("mon", "alert", "alarm"), ("alert", "staff", "excursion alert"),
]


def build(name):
    c = CLOUDS[name]
    out = ['<mxfile host="drawio" version="26.2.15"><diagram id="arch" name="' + name + '">'
           '<mxGraphModel dx="1700" dy="900" grid="1" gridSize="10" guides="1" page="1" pageScale="1" '
           'pageWidth="1720" pageHeight="940" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>']
    grp = "rounded=1;whiteSpace=wrap;html=1;fillColor=none;verticalAlign=top;align=left;spacingLeft=10;fontStyle=1;fontSize=13;arcSize=2;"
    out.append(cell("g_cap", "Step 1: video to twin (workstation or CI)", grp + "strokeColor=#E8A33D;", 20, 70, 565, 180))
    out.append(cell("g_floor", "Floor OT (Purdue levels 0 to 2)", grp + "strokeColor=#888888;dashed=1;", 20, 268, 230, 532))
    out.append(cell("g_dmz", "OT DMZ (Purdue level 3.5)", grp + "strokeColor=#C62828;dashed=1;strokeWidth=2;fontColor=#C62828;",
                    265, 268, 325, 532))
    out.append(cell("tb1", "<b>Trust boundary</b><br>every device has its own key or<br>certificate; data leaves the site,<br>nothing comes in except signed,<br>approved firmware",
                    "text;html=1;fontSize=10;align=center;verticalAlign=top;fontColor=#C62828;", 300, 520, 250, 80))
    out.append(cell("g_cloud", c["title"] + ": Step 2, connect data", grp + "strokeColor=#232F3E;strokeWidth=2;", 605, 70, 820, 730))
    out.append(cell("g_viz", "Step 3: visualize and act", grp + "strokeColor=#34A853;", 1445, 70, 240, 730))
    out.append(cell("title", f"<b>Food bank digital twin on {c['title'].split(' (')[0]}</b>",
                    "text;html=1;fontSize=18;align=left;verticalAlign=middle;", 20, 14, 900, 40))
    out.append(cell("note", c["note"], "text;html=1;whiteSpace=wrap;fontSize=11;align=left;verticalAlign=top;fontColor=#545B64;",
                    20, 894, 1640, 40))
    out.append('<mxCell id="tb2" value="read-only boundary: no write path to the floor" style="endArrow=none;html=1;dashed=1;'
               'strokeColor=#C62828;strokeWidth=2;fontSize=10;fontColor=#C62828;labelBackgroundColor=#ffffff;" edge="1" parent="1">'
               '<mxGeometry relative="1" as="geometry"><mxPoint x="1150" y="262" as="sourcePoint"/>'
               '<mxPoint x="1150" y="505" as="targetPoint"/></mxGeometry></mxCell>')
    sec = ("<b>OT security: zones, conduits and what is built</b> (docs/OT_SECURITY.md)<br>"
           "Field devices and edge gateways sit on the floor; the OT DMZ passes only outbound TLS; cloud ingest accepts data and sends "
           "no commands; the twin is read-only. Existing refrigeration and dock controls are not connected. Red dashed lines are trust "
           "boundaries. The only designed path toward the floor is a signed, human-approved firmware update.<br>"
           + c["built"] + "<br><b>Design only</b> (dashed): per-device certificates and keys, least-privilege device policies, "
           "segmentation, the edge gateway, device monitoring, plausibility and heartbeat checks on readings, signed updates.")
    out.append(cell("sec", sec, "rounded=1;whiteSpace=wrap;html=1;fontSize=10;align=left;verticalAlign=top;spacing=6;"
                    "arcSize=4;fillColor=#FDECEA;strokeColor=#C62828;", 20, 812, 1665, 74))
    cost = "<b>Cost controls: pay only while it runs</b>" + "".join(f"<br>• {t}" for t in c["cost"])
    out.append(cell("cost", cost, "rounded=1;whiteSpace=wrap;html=1;fontSize=10;align=left;verticalAlign=top;spacing=6;"
                    "arcSize=4;fillColor=#FFF8E1;strokeColor=#E8A33D;", 1165, 520, 250, 180 if name == "aws" else 124))
    impl = set(IMPLEMENTED)
    # Edges first, so icon labels (with a white background) sit on top of the lines.
    for i, (s, d, label) in enumerate(EDGES):
        built = {k for k, v in BOXES.items() if v[6]}
        solid = {s, d} <= (built | (impl if name == "aws" else set()))
        out.append(edge(f"e{i}", s, d, label, dashed=not solid))
    for sid, (x, y) in SERVICES.items():
        label, style = c[sid]
        dashed = sid not in impl or name != "aws"
        if style is None:  # no first-party icon: a plain box in the same place
            out.append(cell(sid, label, BOX + "fillColor=#FFFFFF;strokeColor=#545B64;" + ("dashed=1;" if dashed else ""),
                            x - 31, y, 110, 48))
        else:
            out.append(cell(sid, label, style + ("" if not dashed else "dashed=0;"), x, y, 48, 48))
            if dashed:  # dashed frame marks the not-yet-built path
                out.append(cell(sid + "_f", "", "rounded=1;fillColor=none;strokeColor=#9AA0A6;dashed=1;html=1;",
                                x - 6, y - 6, 60, 60))
    for bid, (label, x, y, w, h, fill, implemented) in BOXES.items():
        label = label or c[bid]
        dashed = not implemented or (name != "aws" and bid in {"omni"})
        out.append(cell(bid, label, BOX + f"fillColor={fill};strokeColor=#545B64;" + ("dashed=1;" if dashed else ""), x, y, w, h))
    out.append("</root></mxGraphModel></diagram></mxfile>")
    (HERE / f"arch_{name}.drawio").write_text("".join(out))


if __name__ == "__main__":
    for n in CLOUDS:
        build(n)
        print("wrote", HERE / f"arch_{n}.drawio")
