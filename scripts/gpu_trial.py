"""One-shot Omniverse GPU trial on EC2: launch, render, upload, self-terminate.

  python scripts/gpu_trial.py launch   # uploads inputs, starts one tagged GPU instance
  python scripts/gpu_trial.py status   # instance state + last console lines
  python scripts/gpu_trial.py fetch    # downloads renders and the log to renders/omniverse/

The instance needs no SSH, key pair or IAM role: inputs come in and results go out
through presigned S3 URLs, and it terminates itself when done (or after MAX_MINUTES).
Needs AWS credentials allowed to run g6/g5.xlarge tagged Project=foodbank-gpu-trial.
"""
import argparse
import os
import pathlib
import re
import tarfile

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-2")
TAG = {"Key": "Project", "Value": "foodbank-gpu-trial"}
INSTANCE_TYPE = os.environ.get("GPU_INSTANCE_TYPE", "g6.xlarge")
BASES = {
    # AWS Deep Learning Base AMI: driver, Docker and the container toolkit preinstalled. Its driver
    # was 595 in Oct 2026, and Kit 107.3 (Isaac Sim 5.1) segfaults on start with it.
    "dlami": ("amazon", "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*"),
    # Plain Ubuntu: user data installs the 580 server driver, Docker and the toolkit. Kit 107.3
    # starts on 580 (first start about 5 min while it builds its shader cache).
    "ubuntu": ("099720109477", "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"),
}
BASE = "ubuntu"
UBUNTU_SETUP = """export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
apt-get -qq install -y linux-headers-$(uname -r) docker.io ffmpeg curl
apt-get -qq install -y nvidia-driver-580-server nvidia-utils-580-server || apt-get -qq install -y nvidia-driver-570-server nvidia-utils-570-server
modprobe nvidia && nvidia-smi
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get -qq update && apt-get -qq install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker && systemctl restart docker
"""
MAX_MINUTES = 150
MODE = "stills"
PREFIX = "gpu-trial/"
ROOT = pathlib.Path(__file__).resolve().parent.parent
INPUTS = {
    "site.usdz": ROOT / "public/site.usdz",
    "facility.usdz": ROOT / "public/facility.usdz",
    "shots.json": ROOT / "docs/renders/shots.json",
    "render.py": ROOT / "scripts/omniverse_render_shots.py",
    "clips.py": ROOT / "scripts/omniverse_render_clips.py",
}
OUTPUTS = ["out.tgz", "trial.log"]

USER_DATA = """#!/bin/bash
exec > /var/log/trial.log 2>&1
shutdown -h +{max_minutes}
set -x
mkdir -p /work/out && cd /work
upload_log() {{ curl -fsS --retry 3 -X PUT -T /var/log/trial.log '{put_log}'; }}
{gets}
{setup}
python3 -c "import json; json.load(open('shots.json'))" || {{ echo INPUTS_BAD; upload_log; shutdown -h now; exit 1; }}
nvidia-smi
(apt-get -qq update && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y ffmpeg >/dev/null) & FFMPEG_INSTALL=$!
date
for tag in 5.1.0 5.0.0; do docker pull nvcr.io/nvidia/isaac-sim:$tag && IMG=nvcr.io/nvidia/isaac-sim:$tag && break; done
date
# Isaac Sim 5.x runs as a non-root user inside the container, so /work must be writable to it.
# Kit's shader and data caches live on the host so the second container reuses the first's.
mkdir -p /work/cache/kit /work/cache/home /work/cache/nv && chmod -R a+rwX /work
CACHE="-v /work/cache/kit:/isaac-sim/kit/cache -v /work/cache/home:/isaac-sim/.cache -v /work/cache/nv:/isaac-sim/.nv"
render() {{ date; docker run --rm --gpus all --network host -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \\
  -e OMNI_KIT_ALLOW_ROOT=1 -e USD=$1 -e SHOTS=$2 -e OUT=/work/out/$3 \\
  -v /work:/work $CACHE -w /isaac-sim --entrypoint bash "$IMG" -c "./python.sh /work/render.py" 2>&1 | grep -av 'crashreporter.*[]] *[a-zA-Z_]* = '; date; }}
if [ "{mode}" = clips ]; then
  clip() {{ date; docker run --rm --gpus all --network host -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \\
    -e OMNI_KIT_ALLOW_ROOT=1 -e USD=$1 -e CLIPS=$2 -e OUT=/work/frames \\
    -v /work:/work $CACHE -w /isaac-sim --entrypoint bash "$IMG" -c "./python.sh /work/clips.py" 2>&1 | grep -av 'crashreporter.*[]] *[a-zA-Z_]* = '; date; }}
  # Splat captures (site.usdz) render empty in Kit 107.3, so the clips use the designed facility.
  clip /work/facility.usdz forklift_chase,facility_orbit,aisle_forklift
  wait $FFMPEG_INSTALL
  for d in /work/frames/*/; do n=$(basename $d)
    ffmpeg -loglevel error -y -framerate 24 -i $d/rgb_%04d.png -c:v libx264 -crf 18 -pix_fmt yuv420p /work/out/$n.mp4
    cp $d/rgb_0000.png /work/out/$n-first.png; cp $(ls $d/rgb_*.png | tail -1) /work/out/$n-last.png
  done
  cp /work/frames/clips.json /work/out/ 2>/dev/null
else
  render /work/facility.usdz auto facility
  render /work/site.usdz /work/shots.json site
fi
tar czf /work/out.tgz -C /work out
curl -fsS --retry 3 -X PUT -T /work/out.tgz '{put_out}'
upload_log
shutdown -h now
"""


def bucket(session):
    cfn = session.client("cloudformation")
    out = cfn.describe_stacks(StackName="aws-sam-cli-managed-default")["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in out if o["OutputKey"] == "SourceBucket")


def s3_client(session):
    # Regional endpoint: presigned URLs on the global endpoint answer 307, which curl PUT won't follow.
    return session.client("s3", endpoint_url=f"https://s3.{REGION}.amazonaws.com",
                          config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))


def launch(session):
    s3, ec2 = s3_client(session), session.client("ec2")
    b = bucket(session)
    for key, path in INPUTS.items():
        s3.upload_file(str(path), b, PREFIX + key)
    week = 7 * 24 * 3600
    gets = "\n".join(
        f"curl -fsSL --retry 3 -o {k} '{s3.generate_presigned_url('get_object', Params={'Bucket': b, 'Key': PREFIX + k}, ExpiresIn=week)}'"
        for k in INPUTS
    )
    put = {k: s3.generate_presigned_url("put_object", Params={"Bucket": b, "Key": PREFIX + "results/" + k}, ExpiresIn=week)
           for k in OUTPUTS}
    ami = sorted(
        ec2.describe_images(Owners=[BASES[BASE][0]], Filters=[{"Name": "name", "Values": [BASES[BASE][1]]}])["Images"],
        key=lambda i: i["CreationDate"],
    )[-1]["ImageId"]
    # GPU capacity runs out per availability zone; try each zone before giving up.
    zones = [z["ZoneName"] for z in ec2.describe_availability_zones()["AvailabilityZones"]]
    for zone in zones:
        try:
            r = ec2.run_instances(
                ImageId=ami, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
                Placement={"AvailabilityZone": zone},
                InstanceInitiatedShutdownBehavior="terminate",
                UserData=USER_DATA.format(max_minutes=MAX_MINUTES, mode=MODE, setup=UBUNTU_SETUP if BASE == "ubuntu" else "", gets=gets, put_out=put["out.tgz"], put_log=put["trial.log"]),
                BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 150, "VolumeType": "gp3", "DeleteOnTermination": True}}],
                TagSpecifications=[{"ResourceType": t, "Tags": [TAG]} for t in ("instance", "volume")],
            )
            break
        except ec2.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "InsufficientInstanceCapacity" or zone == zones[-1]:
                raise
            print("no capacity in", zone)
    print("launched", r["Instances"][0]["InstanceId"], INSTANCE_TYPE, ami)


def status(session):
    ec2 = session.client("ec2")
    res = ec2.describe_instances(Filters=[{"Name": "tag:Project", "Values": [TAG["Value"]]}])["Reservations"]
    for inst in (i for r in res for i in r["Instances"]):
        print(inst["InstanceId"], inst["State"]["Name"], inst.get("LaunchTime"))
        if inst["State"]["Name"] == "running":
            out = ec2.get_console_output(InstanceId=inst["InstanceId"], Latest=True).get("Output", "")
            print("\n".join(out.splitlines()[-15:]))


def fetch(session):
    s3, b = s3_client(session), bucket(session)
    dest = ROOT / "renders/omniverse"
    dest.mkdir(parents=True, exist_ok=True)
    for k in OUTPUTS:
        s3.download_file(b, PREFIX + "results/" + k, str(dest / k))
    # The log echoes the presigned URLs (they carry the access key id and a signature); redact them.
    log = dest / "trial.log"
    log.write_text(re.sub(r"https://\S*X-Amz-\S*?(?='|\s|$)", "<presigned-url-redacted>", log.read_text(errors="replace")))
    with tarfile.open(dest / "out.tgz") as t:
        t.extractall(dest, filter="data")
    print("fetched to", dest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["launch", "status", "fetch"])
    ap.add_argument("--mode", choices=["stills", "clips"], default="stills",
                    help="stills: shots.json + auto views; clips: moving 4 s clips encoded to mp4")
    ap.add_argument("--base", choices=sorted(BASES), default="ubuntu")
    args = ap.parse_args()
    cmd, MODE, BASE = args.cmd, args.mode, args.base
    globals()[cmd](boto3.Session(region_name=REGION))
