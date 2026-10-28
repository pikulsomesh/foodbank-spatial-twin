"""The OT security claims in docs/OT_SECURITY.md that are implemented must stay true.

The twin is read-only: every API route is a GET, no IoT credentials or device policies
live in the template, the function role is SAM's logs-only default, and the API is throttled.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class _Loader(yaml.SafeLoader):
    pass


# CloudFormation short-form tags (!Ref, !Sub, ...) are kept as plain values.
_Loader.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_scalar(node)
                              if isinstance(node, yaml.ScalarNode) else None)

TEMPLATE = yaml.load((ROOT / "template.yaml").read_text(), Loader=_Loader)
RESOURCES = TEMPLATE["Resources"]


def _functions():
    return {k: v for k, v in RESOURCES.items() if v["Type"] == "AWS::Serverless::Function"}


def test_every_api_route_is_read_only():
    for name, fn in _functions().items():
        for event in fn["Properties"].get("Events", {}).values():
            if event["Type"] in ("HttpApi", "Api"):
                assert event["Properties"]["Method"].upper() == "GET", name
    cors = RESOURCES["TelemetryApi"]["Properties"]["CorsConfiguration"]
    assert set(cors["AllowMethods"]) <= {"GET", "OPTIONS"}


def test_no_iot_resources_or_device_credentials_in_the_cloud():
    assert not [k for k, v in RESOURCES.items() if v["Type"].startswith(("AWS::IoT", "AWS::IoTWireless"))]


def test_function_role_is_the_sam_default():
    for name, fn in _functions().items():
        props = fn["Properties"]
        assert "Policies" not in props and "Role" not in props, f"{name} widened its role; update OT_SECURITY.md"


def test_api_is_throttled():
    route = RESOURCES["TelemetryApi"]["Properties"]["DefaultRouteSettings"]
    assert route["ThrottlingRateLimit"] and route["ThrottlingBurstLimit"]
    assert TEMPLATE["Parameters"]["ThrottleRate"]["Default"] <= 5
