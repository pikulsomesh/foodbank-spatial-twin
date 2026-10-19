#!/usr/bin/env python3
"""
Auto-Twin | Thread 1 | scripts/world_labs_to_usd.py

Spatial build stage of the Auto-Twin pipeline:

    input_images/*.jpg
        -> select the sharpest, evenly spread frames (World Labs accepts <= 8)
        -> upload as World Labs media assets            (free)
        -> POST /worlds:generate  model=marble-1.0-draft (~250 credits)
        -> poll GET /operations/{id} with exponential backoff + jitter (free)
        -> download the Gaussian splat (.spz)
        -> usd-convert-gsplat (NVIDIA) -> ParticleField3DGaussianSplat (.usdc)
        -> wrap in a metric, Y-up, ground-aligned stage and package
        -> public/factory.usdz  (+ public/factory.spz, public/factory.meta.json)

API facts used here were checked against docs.worldlabs.ai on 2026-10-18:
  base URL      https://api.worldlabs.ai/marble/v1
  auth header   WLT-Api-Key
  models        marble-1.0-draft | marble-1.0 | marble-1.1 | marble-1.1-plus
  multi-image   up to 4 images, or up to 8 with reconstruct_images=true
  draft price   230 credits (single image), 250 credits (multi-image or video)
  rate limit    ~3 generation starts/min, 60/hour (default tier); 429 = not accepted

-----------------------------------------------------------------------------
COST-PROTECTION SAFEGUARDS (read before changing anything)
-----------------------------------------------------------------------------
 1. DRAFT ONLY. Any model other than marble-1.0-draft is refused unless you
    pass --allow-non-draft. The final payload is asserted again right before
    the POST, and the returned world's model is checked afterwards.
 2. ONE GENERATION PER RUN. The script contains exactly one call site that
    starts a generation, and it runs at most once per process.
 3. RESUME, NEVER RESUBMIT. The operation id is saved to disk the moment the
    API accepts the job. A crash, Ctrl-C or timeout later means the next run
    resumes polling that same operation instead of paying again.
 4. REUSE FINISHED WORLDS. If a finished world already exists in the state
    file, the next run downloads and converts it for 0 credits. Paying for a
    new world requires --force-new.
 5. LIFETIME LEDGER. Every credit charged is recorded in build/ledger.json. A
    generation is refused if (spent + estimate) would exceed --budget-credits
    (default 10,000).
 6. LIVE BALANCE CHECK. GET /credits must show at least
    (estimate + --reserve-credits) remaining before anything is submitted.
 7. NO BLIND RETRIES OF PAID CALLS. The generate POST is retried only on HTTP
    429, which the docs define as "request not accepted". A timeout or 5xx
    could mean the job started, so the script stops and tells you to check
    the World Labs dashboard instead of risking a double charge.
 8. --dry-run builds and prints the exact payload and spends nothing.

Requirements: Python 3.11 or 3.12 (usd-convert-gsplat does not support 3.13),
    python -m pip install "usd-convert-gsplat[usd]" requests pillow
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - environment guard
    sys.exit('[world_labs] Missing dependency: python -m pip install requests pillow "usd-convert-gsplat[usd]"')

try:
    from PIL import Image, ImageFilter, ImageStat
except ImportError:  # pragma: no cover - environment guard
    sys.exit('[world_labs] Missing dependency: python -m pip install pillow')


# =============================================================================
# Configuration
# =============================================================================

API_BASE = os.environ.get("WORLD_LABS_API_BASE", "https://api.worldlabs.ai/marble/v1").rstrip("/")

DRAFT_MODEL = "marble-1.0-draft"
KNOWN_MODELS = {"marble-1.0-draft", "marble-1.0", "marble-1.1", "marble-1.1-plus"}

# Credits per request from docs.worldlabs.ai/api/pricing (2026-10-18).
# Used only for pre-flight estimates; the ledger records what was actually
# charged (operation.cost.total_credits). marble-1.1-plus uses its upper bound.
CREDIT_TABLE = {
    "marble-1.0-draft": {"image": 230, "multi-image": 250, "video": 250},
    "marble-1.0":       {"image": 1580, "multi-image": 1600, "video": 1600},
    "marble-1.1":       {"image": 1580, "multi-image": 1600, "video": 1600},
    "marble-1.1-plus":  {"image": 3080, "multi-image": 3100, "video": 3100},
}
UNKNOWN_MODEL_ESTIMATE = 3500  # pessimistic if World Labs adds a model

MAX_IMAGES_CREATIVE = 4        # multi-image without reconstruction
MAX_IMAGES_RECONSTRUCT = 8     # multi-image with reconstruct_images=true
MAX_VIDEO_BYTES = 100 * 1024 * 1024
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
        "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mkv": "video/x-matroska"}

HTTP_TIMEOUT = (10, 60)         # (connect, read) seconds for API calls
TRANSFER_TIMEOUT = (10, 300)    # uploads / downloads
SPZ_MAGIC = 0x5053474E          # "NGSP" little-endian, from the SPZ format


def log(msg: str) -> None:
    print(f"[world_labs] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[world_labs] WARNING: {msg}", file=sys.stderr, flush=True)


class PipelineError(RuntimeError):
    """A failure the user must act on; printed without a traceback."""


class AmbiguousSubmitError(PipelineError):
    """The generate call may or may not have started a (billable) job."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON via temp file + rename so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        # Never silently reset a ledger: that would erase the spend history.
        raise PipelineError(f"{path} is corrupt ({exc}); fix or move it before continuing") from exc


# =============================================================================
# Backoff
# =============================================================================

@dataclass
class Backoff:
    """Exponential backoff with full jitter (AWS-style), honouring Retry-After."""
    initial: float = 2.0
    maximum: float = 60.0
    factor: float = 2.0
    attempt: int = 0

    def next_delay(self, retry_after: float | None = None) -> float:
        cap = min(self.maximum, self.initial * (self.factor ** self.attempt))
        self.attempt += 1
        delay = random.uniform(cap / 2, cap)  # "equal jitter": never ~0, never > cap
        if retry_after is not None:
            delay = max(delay, retry_after)
        return delay

    def reset(self) -> None:
        self.attempt = 0


def parse_retry_after(resp: requests.Response | None) -> float | None:
    if resp is None:
        return None
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form; fall back to our own backoff


# =============================================================================
# World Labs client
# =============================================================================

class WorldLabsClient:
    """Thin client over the Marble REST API. The key never appears in logs."""

    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, api_key: str, base: str = API_BASE, max_retries: int = 6):
        self.base = base
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "WLT-Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "auto-twin/1.0",
        })

    # -- transport ------------------------------------------------------------

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 safe_to_retry: bool = True) -> dict:
        """Send one API request.

        safe_to_retry=True  : GETs and free calls. Retried on network errors,
                              429 and 5xx with exponential backoff.
        safe_to_retry=False : the paid generate call. Retried ONLY on 429.
        """
        url = f"{self.base}{path}"
        backoff = Backoff(initial=2.0, maximum=60.0)
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(method, url, json=body, timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                if not safe_to_retry:
                    raise AmbiguousSubmitError(
                        f"network error during a paid request ({type(exc).__name__}). The job may "
                        "or may not have started. Check https://platform.worldlabs.ai before re-running."
                    ) from exc
                if attempt >= self.max_retries:
                    raise PipelineError(f"{method} {path} failed after retries: {exc}") from exc
                delay = backoff.next_delay()
                warn(f"{method} {path}: {type(exc).__name__}; retrying in {delay:.1f}s")
                time.sleep(delay)
                continue

            if resp.status_code < 400:
                return resp.json() if resp.content else {}

            retryable = resp.status_code == 429 or (safe_to_retry and resp.status_code in self.RETRYABLE_STATUS)
            if retryable and attempt < self.max_retries:
                delay = backoff.next_delay(parse_retry_after(resp))
                warn(f"{method} {path}: HTTP {resp.status_code}; retrying in {delay:.1f}s")
                time.sleep(delay)
                continue

            detail = resp.text[:500]
            if resp.status_code == 401:
                raise PipelineError("401 Unauthorized: WORLD_LABS_API_KEY is invalid or revoked")
            if resp.status_code == 402:
                raise PipelineError("402 Payment Required: not enough World Labs credits")
            if not safe_to_retry and resp.status_code >= 500:
                raise AmbiguousSubmitError(
                    f"HTTP {resp.status_code} from a paid request: {detail}. The docs say 500 means the "
                    "job was not started, but confirm on https://platform.worldlabs.ai before re-running."
                )
            raise PipelineError(f"{method} {path} -> HTTP {resp.status_code}: {detail}")
        raise PipelineError(f"{method} {path}: retries exhausted")  # pragma: no cover

    # -- endpoints --------------------------------------------------------------

    def remaining_credits(self) -> float:
        return float(self._request("GET", "/credits")["remaining_credits"])

    def upload_media(self, path: Path, kind: str) -> str:
        """prepare_upload -> signed PUT. Free. Returns media_asset_id."""
        ext = path.suffix.lower().lstrip(".")
        prep = self._request("POST", "/media-assets:prepare_upload", body={
            "file_name": path.name[:64], "kind": kind, "extension": ext,
            "metadata": {"pipeline": "auto-twin", "sha256": sha256_file(path)[:16]},
        })
        asset_id = prep["media_asset"]["media_asset_id"]
        info = prep["upload_info"]
        headers = {"Content-Type": MIME.get(ext, "application/octet-stream")}
        headers.update(info.get("required_headers") or {})
        backoff = Backoff(initial=2.0, maximum=30.0)
        for attempt in range(self.max_retries + 1):
            try:
                with path.open("rb") as fh:
                    # Plain requests (not the API session): a signed URL must not
                    # receive the WLT-Api-Key header.
                    resp = requests.request(info.get("upload_method", "PUT"), info["upload_url"],
                                            data=fh, headers=headers, timeout=TRANSFER_TIMEOUT)
                if resp.status_code < 300:
                    return asset_id
                err = f"HTTP {resp.status_code}"
            except requests.RequestException as exc:
                err = type(exc).__name__
            if attempt >= self.max_retries:
                raise PipelineError(f"upload of {path.name} failed: {err}")
            delay = backoff.next_delay()
            warn(f"upload {path.name}: {err}; retrying in {delay:.1f}s")
            time.sleep(delay)
        raise PipelineError("unreachable")  # pragma: no cover

    def generate_world(self, payload: dict) -> dict:
        """The ONLY billable call in this file. Never retried except on 429."""
        return self._request("POST", "/worlds:generate", body=payload, safe_to_retry=False)

    def get_operation(self, operation_id: str) -> dict:
        return self._request("GET", f"/operations/{operation_id}")

    def get_world(self, world_id: str) -> dict:
        return self._request("GET", f"/worlds/{world_id}")


# =============================================================================
# Frame selection
# =============================================================================

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def sharpness(path: Path) -> float:
    """Variance of the Laplacian on a 640px greyscale copy. Higher == sharper."""
    with Image.open(path) as im:
        im = im.convert("L")
        im.thumbnail((640, 640))
        return ImageStat.Stat(im.filter(_LAPLACIAN)).var[0]


def select_frames(input_dir: Path, max_images: int) -> list[Path]:
    """Pick up to max_images frames: split the sequence into equal segments and
    take the sharpest frame of each. This keeps viewpoint spread (needed for
    reconstruction) while rejecting motion-blurred frames.
    """
    frames = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS and p.is_file())
    if not frames:
        raise PipelineError(f"no images found in {input_dir}; run scripts/ingest_and_build.sh first")

    # Reconstruction mode requires identical resolution and aspect ratio.
    sizes: dict[tuple[int, int], list[Path]] = {}
    for p in frames:
        try:
            with Image.open(p) as im:
                im.verify()
            with Image.open(p) as im:
                sizes.setdefault(im.size, []).append(p)
        except Exception as exc:  # corrupt JPEGs would waste a generation
            warn(f"skipping unreadable image {p.name}: {exc}")
    if not sizes:
        raise PipelineError("all images were unreadable")
    dominant = max(sizes, key=lambda s: len(sizes[s]))
    usable = sizes[dominant]
    if len(usable) < len(frames):
        warn(f"dropped {len(frames) - len(usable)} image(s) whose resolution differs from {dominant}")

    if len(usable) <= max_images:
        chosen = usable
    else:
        chosen = []
        n = len(usable)
        for i in range(max_images):
            segment = usable[i * n // max_images:(i + 1) * n // max_images]
            chosen.append(max(segment, key=sharpness))
    log(f"selected {len(chosen)} of {len(frames)} frame(s) at {dominant[0]}x{dominant[1]}: "
        + ", ".join(p.name for p in chosen))
    return chosen


# =============================================================================
# Cost guards
# =============================================================================

def estimate_credits(model: str, prompt_type: str) -> int:
    return CREDIT_TABLE.get(model, {}).get(prompt_type, UNKNOWN_MODEL_ESTIMATE)


def enforce_model_policy(model: str, allow_non_draft: bool) -> None:
    if model not in KNOWN_MODELS:
        warn(f"model '{model}' is not in the documented model list; the API may reject it")
    if model != DRAFT_MODEL and not allow_non_draft:
        raise PipelineError(
            f"refusing model '{model}': only '{DRAFT_MODEL}' is allowed by default "
            f"(~{estimate_credits(DRAFT_MODEL, 'multi-image')} credits vs "
            f"~{estimate_credits(model, 'multi-image')}). Pass --allow-non-draft to override."
        )


class Ledger:
    """Append-only record of credits charged, persisted across runs."""

    def __init__(self, path: Path):
        self.path = path
        self.data = read_json(path, {"entries": []})

    @property
    def spent(self) -> int:
        # An entry counts its actual cost once known, else its estimate.
        return sum(int(e.get("actual_credits") if e.get("actual_credits") is not None
                       else e.get("estimated_credits", 0)) for e in self.data["entries"])

    def record_submit(self, operation_id: str, model: str, estimate: int) -> None:
        self.data["entries"].append({"operation_id": operation_id, "model": model,
                                     "estimated_credits": estimate, "actual_credits": None,
                                     "submitted_at": utcnow()})
        atomic_write_json(self.path, self.data)

    def record_actual(self, operation_id: str, credits: int | None) -> None:
        for e in self.data["entries"]:
            if e["operation_id"] == operation_id and credits is not None:
                e["actual_credits"] = int(credits)
                e["completed_at"] = utcnow()
        atomic_write_json(self.path, self.data)


# =============================================================================
# Polling
# =============================================================================

def poll_operation(client: WorldLabsClient, operation_id: str, *, timeout_s: float,
                   initial_s: float, max_s: float) -> dict:
    """Poll until done. Polling is free, so transient errors are retried by the
    client; the backoff here only paces the status checks. Generation typically
    takes ~5 minutes.
    """
    deadline = time.monotonic() + timeout_s
    backoff = Backoff(initial=initial_s, maximum=max_s, factor=1.5)
    last_progress = None
    while True:
        op = client.get_operation(operation_id)
        if op.get("done"):
            if op.get("error"):
                err = op["error"]
                raise PipelineError(f"generation failed: code={err.get('code')} message={err.get('message')}")
            return op
        progress = (op.get("metadata") or {}).get("progress")
        if progress != last_progress:
            log(f"operation {operation_id}: running (progress={progress})")
            last_progress = progress
        if time.monotonic() > deadline:
            raise PipelineError(
                f"timed out after {timeout_s:.0f}s waiting for {operation_id}. Nothing is lost: "
                "re-run the same command to resume polling (no new credits are spent)."
            )
        time.sleep(backoff.next_delay())


# =============================================================================
# SPZ download + validation
# =============================================================================

def validate_spz(path: Path) -> dict:
    """Check gzip framing and the SPZ header before spending time converting."""
    try:
        with gzip.open(path, "rb") as fh:
            header = fh.read(16)
    except OSError as exc:
        raise PipelineError(f"{path.name} is not a gzip SPZ file: {exc}") from exc
    if len(header) < 16:
        raise PipelineError(f"{path.name}: truncated SPZ header")
    magic, version, count, sh_degree, frac_bits, flags, _ = struct.unpack_from("<IIIBBBB", header)
    if magic != SPZ_MAGIC:
        raise PipelineError(f"{path.name}: bad SPZ magic 0x{magic:08X}")
    if version not in (2, 3):
        raise PipelineError(f"{path.name}: SPZ v{version} is not supported by usd-convert-gsplat (v2/v3 only)")
    return {"version": version, "splats": count, "sh_degree": sh_degree, "fractional_bits": frac_bits}


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    backoff = Backoff(initial=2.0, maximum=30.0)
    for attempt in range(6):
        try:
            with requests.get(url, stream=True, timeout=TRANSFER_TIMEOUT) as resp:
                if resp.status_code in (401, 403):
                    raise PermissionError("signed URL rejected (expired?)")
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
            os.replace(tmp, dest)
            return
        except PermissionError:
            raise
        except requests.RequestException as exc:
            if attempt == 5:
                raise PipelineError(f"download failed: {exc}") from exc
            delay = backoff.next_delay()
            warn(f"download error ({type(exc).__name__}); retrying in {delay:.1f}s")
            time.sleep(delay)


def fetch_spz(client: WorldLabsClient | None, world: dict, resolution: str, work_dir: Path) -> Path:
    world_id = world["id"]
    dest = work_dir / f"world_{world_id}_{resolution}.spz"
    if dest.exists():
        log(f"using cached {dest.name}")
        return dest
    for refresh in (False, True):
        if refresh:
            if client is None:
                break
            log("signed URL expired; fetching a fresh world record")
            world = client.get_world(world_id)
        urls = ((world.get("assets") or {}).get("splats") or {}).get("spz_urls") or {}
        url = urls.get(resolution)
        if not url:
            raise PipelineError(f"world {world_id} has no '{resolution}' splat; available: {sorted(urls)}")
        try:
            log(f"downloading {resolution} splat -> {dest.name}")
            download(url, dest)
            return dest
        except PermissionError:
            continue
    raise PipelineError("could not download the splat (signed URL rejected twice)")


# =============================================================================
# USD conversion (NVIDIA usd-convert-gsplat)
# =============================================================================

def converter_cmd() -> list[str]:
    exe = shutil.which("usd-convert-gsplat")
    if exe:
        return [exe]
    try:
        import usd_convert_gsplat  # noqa: F401
    except ImportError as exc:
        raise PipelineError('usd-convert-gsplat not installed: python -m pip install "usd-convert-gsplat[usd]"') from exc
    return [sys.executable, "-m", "usd_convert_gsplat"]


def run_converter(args: list[str], timeout_s: int) -> None:
    cmd = converter_cmd() + args
    log("running: " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, timeout=timeout_s)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(f"usd-convert-gsplat exited with {exc.returncode}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"usd-convert-gsplat timed out after {timeout_s}s (try --spz-resolution 500k)") from exc


def build_usdz(spz: Path, output: Path, work_dir: Path, semantics: dict, provenance: dict,
               metric_xform: bool, timeout_s: int) -> None:
    """SPZ -> ParticleField3DGaussianSplat -> packaged USDZ.

    Why not just `usd-convert-gsplat -o factory.usdz`? In v0.1.15 the direct
    .usdz path writes an ASCII .usda layer and zips it with Python's zipfile,
    which (a) inflates file size and load time and (b) skips the 64-byte
    alignment the USDZ spec requires. So we convert to binary .usdc, add a
    small wrapper layer for World Labs' metric/ground/axis correction, and let
    OpenUSD's UsdUtils.CreateNewUsdzPackage build a spec-compliant package.
    """
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdUtils
    except ImportError:
        warn("OpenUSD (pxr) not importable; falling back to direct .usdz output "
             "(ASCII layer, no metric scale or ground alignment)")
        run_converter(["-i", str(spz), "-o", str(output), "-n", "FoodBankSplat",
                       "--up-axis", "Y", "--rotate-x", "180"], timeout_s)
        return

    stage_dir = work_dir / "usd"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    splat_layer = stage_dir / "food_bank_splat.usdc"
    root_layer = stage_dir / "factory.usdc"

    # 1. Raw conversion, no rotation: corrections live in the wrapper so the
    #    splat payload stays byte-identical to what World Labs produced.
    run_converter(["-i", str(spz), "-o", str(splat_layer), "-n", "FoodBankSplat", "--up-axis", "Y"], timeout_s)

    splat_stage = Usd.Stage.Open(str(splat_layer))
    splat_prim = next((p for p in splat_stage.Traverse() if p.GetTypeName() == "ParticleField3DGaussianSplat"), None)
    if splat_prim is None:
        raise PipelineError("converter output has no ParticleField3DGaussianSplat prim")
    splat_path = splat_prim.GetPath()

    # 2. Wrapper stage: /World/FoodBank (Xform carrying the corrections) and
    #    an untyped child that references the splat. The child must stay
    #    untyped: a typed `def Xform` here would override the referenced
    #    ParticleField3DGaussianSplat type and hide the splat.
    stage = Usd.Stage.CreateNew(str(root_layer))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    twin = UsdGeom.Xform.Define(stage, "/World/FoodBank")
    splat = stage.DefinePrim("/World/FoodBank/Splat")
    splat.GetReferences().AddReference(f"./{splat_layer.name}", splat_path)

    if metric_xform:
        # docs.worldlabs.ai/api/rendering-spz: centers *= metric_scale_factor,
        # then y -= ground_plane_offset (in metres), then the renderer's axis
        # conversion. Marble SPZ is OpenCV-style (y down), so rotate 180 deg
        # about X for USD Y-up. USD applies ops right-to-left in xformOpOrder,
        # so the order below is rotate( translate( scale(p) ) ).
        s = float(semantics.get("metric_scale_factor") or 1.0)
        g = float(semantics.get("ground_plane_offset") or 0.0)
        twin.AddRotateXOp().Set(180.0)
        twin.AddTranslateOp().Set(Gf.Vec3d(0.0, -g, 0.0))
        twin.AddScaleOp().Set(Gf.Vec3f(s, s, s))
        log(f"metric correction: scale={s:.4f}, ground offset={g:.4f} m, rotateX=180")

    layer = stage.GetRootLayer()
    layer.customLayerData = {"autoTwin": {k: str(v) for k, v in provenance.items()}}
    layer.Save()

    # 3. Spec-compliant USDZ (binary layers, 64-byte aligned, uncompressed).
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(root_layer)), str(output)):
        raise PipelineError("UsdUtils.CreateNewUsdzPackage failed")


def verify_usdz(output: Path) -> int:
    try:
        from pxr import Usd
    except ImportError:
        if not output.exists() or output.stat().st_size == 0:
            raise PipelineError(f"{output} was not written")
        return -1
    stage = Usd.Stage.Open(str(output))
    if stage is None:
        raise PipelineError(f"{output} cannot be opened by OpenUSD")
    for prim in stage.Traverse():
        if prim.GetTypeName() == "ParticleField3DGaussianSplat":
            positions = prim.GetAttribute("positions").Get()
            n = len(positions) if positions is not None else 0
            if n == 0:
                raise PipelineError("ParticleField3DGaussianSplat prim has no positions")
            return n
    raise PipelineError(f"{output} contains no ParticleField3DGaussianSplat prim")


# =============================================================================
# Main flow
# =============================================================================

def build_payload(args: argparse.Namespace, asset_ids: list[str]) -> tuple[dict, str]:
    if args.mode == "video":
        prompt: dict[str, Any] = {"type": "video",
                                  "video_prompt": {"source": "media_asset", "media_asset_id": asset_ids[0]}}
    elif len(asset_ids) == 1:
        prompt = {"type": "image", "image_prompt": {"source": "media_asset", "media_asset_id": asset_ids[0]}}
    else:
        prompt = {
            "type": "multi-image",
            # azimuth=None lets reconstruction mode infer the camera layout.
            "multi_image_prompt": [{"azimuth": None, "content": {"source": "media_asset", "media_asset_id": a}}
                                   for a in asset_ids],
            "reconstruct_images": args.reconstruct,
        }
    if args.text_prompt:
        prompt["text_prompt"] = args.text_prompt
    payload = {
        "display_name": args.display_name[:64],
        "model": args.model,
        "world_prompt": prompt,
        "tags": ["auto-twin", "food-bank", "draft" if args.model == DRAFT_MODEL else "paid"],
        "permission": {"public": False},
    }
    if args.seed is not None:
        payload["seed"] = args.seed
    return payload, prompt["type"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", type=Path, default=Path("input_images"))
    ap.add_argument("--output", type=Path, default=Path("public/factory.usdz"))
    ap.add_argument("--work-dir", type=Path, default=Path("build"),
                    help="state, ledger, downloaded SPZ and intermediate USD layers")
    ap.add_argument("--mode", choices=["multi-image", "video"], default="multi-image")
    ap.add_argument("--video-file", type=Path, help="video for --mode video (<= 100 MB)")
    ap.add_argument("--max-images", type=int, default=MAX_IMAGES_RECONSTRUCT,
                    help="frames to send (<= 8 with reconstruction, <= 4 without)")
    ap.add_argument("--no-reconstruct", dest="reconstruct", action="store_false",
                    help="creative multi-image mode instead of reconstructing one real space")
    ap.add_argument("--text-prompt", default=None, help="optional text guidance for generation")
    ap.add_argument("--display-name", default="Central Texas Food Bank - Auto-Twin")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model", default=DRAFT_MODEL)
    ap.add_argument("--allow-non-draft", action="store_true",
                    help="permit a non-draft model (6-12x the credits)")
    ap.add_argument("--budget-credits", type=int, default=10_000,
                    help="lifetime cap tracked in the ledger")
    ap.add_argument("--reserve-credits", type=int, default=500,
                    help="refuse to submit if the live balance would drop below this")
    ap.add_argument("--force-new", action="store_true",
                    help="pay for a new world even though a finished one exists")
    ap.add_argument("--spz-resolution", choices=["100k", "500k", "full_res"], default="500k")
    ap.add_argument("--no-metric-xform", dest="metric_xform", action="store_false",
                    help="skip metric scale / ground alignment / Y-up rotation")
    ap.add_argument("--poll-timeout", type=float, default=1800.0)
    ap.add_argument("--poll-initial", type=float, default=10.0)
    ap.add_argument("--poll-max", type=float, default=60.0)
    ap.add_argument("--convert-timeout", type=int, default=3600)
    ap.add_argument("--convert-only", type=Path, metavar="SPZ",
                    help="skip World Labs entirely and convert an existing .spz")
    ap.add_argument("--dry-run", action="store_true", help="print the payload; spend nothing")
    args = ap.parse_args(argv)

    limit = MAX_IMAGES_RECONSTRUCT if args.reconstruct else MAX_IMAGES_CREATIVE
    if not 1 <= args.max_images <= limit:
        ap.error(f"--max-images must be 1..{limit} ({'with' if args.reconstruct else 'without'} reconstruction)")
    if args.mode == "video" and not args.video_file:
        ap.error("--mode video requires --video-file")
    return args


def finish(args: argparse.Namespace, client: WorldLabsClient | None, world: dict, cost: int | None) -> None:
    """Download, convert and publish a finished world. Spends no credits."""
    work_dir: Path = args.work_dir
    returned_model = world.get("model")
    if returned_model and returned_model != DRAFT_MODEL and not args.allow_non_draft:
        warn(f"world {world['id']} reports model '{returned_model}', not '{DRAFT_MODEL}'. Check billing.")

    spz = fetch_spz(client, world, args.spz_resolution, work_dir)
    info = validate_spz(spz)
    log(f"SPZ v{info['version']}: {info['splats']:,} splats, SH degree {info['sh_degree']}")

    semantics = ((world.get("assets") or {}).get("splats") or {}).get("semantics_metadata") or {}
    provenance = {"world_id": world["id"], "model": returned_model or args.model,
                  "spz_resolution": args.spz_resolution, "credits": cost, "built_at": utcnow(),
                  "marble_url": world.get("world_marble_url")}
    build_usdz(spz, args.output, work_dir, semantics, provenance, args.metric_xform, args.convert_timeout)
    publish_sidecars(args, spz, world, semantics, info, provenance)


def publish_sidecars(args, spz: Path, world: dict, semantics: dict, info: dict, provenance: dict) -> None:
    n = verify_usdz(args.output)
    size_mb = args.output.stat().st_size / 1e6
    log(f"wrote {args.output} ({size_mb:.1f} MB, {n:,} splats in ParticleField3DGaussianSplat)")
    if size_mb > 150:
        warn("USDZ is large for web delivery; consider --spz-resolution 100k for the browser build")

    # Browsers cannot render USD Gaussian splats; ship the SPZ alongside for a
    # web splat renderer (Thread 3), plus the metadata it needs to align it.
    out_dir = args.output.parent
    if not (out_dir / "factory.spz").exists() or not (out_dir / "factory.spz").samefile(spz):
        shutil.copyfile(spz, out_dir / "factory.spz")
    atomic_write_json(out_dir / "factory.meta.json", {
        **provenance,
        "splats": info["splats"], "sh_degree": info["sh_degree"],
        "semantics_metadata": semantics,
        "collider_mesh_url": ((world.get("assets") or {}).get("mesh") or {}).get("collider_mesh_url"),
        "thumbnail_url": (world.get("assets") or {}).get("thumbnail_url"),
    })
    log(f"wrote {out_dir / 'factory.spz'} and {out_dir / 'factory.meta.json'}")


def run(args: argparse.Namespace) -> None:
    work_dir: Path = args.work_dir
    work_dir.mkdir(parents=True, exist_ok=True)
    state_path = work_dir / "world_labs_state.json"
    ledger = Ledger(work_dir / "ledger.json")

    # ---- Offline path: convert an SPZ you already have ----------------------
    if args.convert_only:
        info = validate_spz(args.convert_only)
        provenance = {"world_id": "local", "model": "n/a", "spz_resolution": "n/a",
                      "credits": 0, "built_at": utcnow(), "marble_url": None}
        build_usdz(args.convert_only, args.output, work_dir, {}, provenance, args.metric_xform, args.convert_timeout)
        publish_sidecars(args, args.convert_only, {"id": "local"}, {}, info, provenance)
        return

    enforce_model_policy(args.model, args.allow_non_draft)
    state = read_json(state_path, {})

    api_key = os.environ.get("WORLD_LABS_API_KEY", "").strip()
    if not api_key and not args.dry_run:
        raise PipelineError("WORLD_LABS_API_KEY is not set")
    client = WorldLabsClient(api_key) if api_key else None

    # ---- Safeguard 3: resume an in-flight job instead of paying again ----------
    if state.get("operation_id") and not state.get("world") and not args.force_new:
        if client is None:
            raise PipelineError(f"operation {state['operation_id']} is in flight; set WORLD_LABS_API_KEY to resume")
        log(f"resuming in-flight operation {state['operation_id']} (0 new credits)")
        op = poll_operation(client, state["operation_id"], timeout_s=args.poll_timeout,
                            initial_s=args.poll_initial, max_s=args.poll_max)
        return complete(args, client, ledger, state_path, state, op)

    # ---- Safeguard 4: reuse a finished world ------------------------------------
    if state.get("world") and not args.force_new:
        log(f"reusing finished world {state['world']['id']} (0 new credits; --force-new to regenerate)")
        return finish(args, client, state["world"], state.get("cost"))

    # ---- Prepare inputs ----------------------------------------------------------
    if args.mode == "video":
        video = args.video_file
        if not video.is_file():
            raise PipelineError(f"video not found: {video}")
        if video.stat().st_size > MAX_VIDEO_BYTES:
            raise PipelineError(f"{video} is {video.stat().st_size / 1e6:.0f} MB; World Labs accepts <= 100 MB")
        inputs = [video]
    else:
        inputs = select_frames(args.input_dir, args.max_images)

    payload, prompt_type = build_payload(args, ["<media_asset_id>"] * len(inputs))
    estimate = estimate_credits(args.model, prompt_type)
    log(f"plan: {prompt_type} world from {len(inputs)} input(s) with {args.model}, "
        f"est. {estimate} credits (${estimate * 0.0008:.2f})")

    # ---- Safeguard 5: lifetime ledger ------------------------------------------
    if ledger.spent + estimate > args.budget_credits:
        raise PipelineError(f"ledger shows {ledger.spent} credits spent; this run (+{estimate}) would exceed "
                            f"--budget-credits {args.budget_credits}")
    log(f"ledger: {ledger.spent} of {args.budget_credits} credits used before this run")

    if args.dry_run:
        log("DRY RUN: payload that would be sent (asset ids are placeholders):")
        print(json.dumps(payload, indent=2))
        log("DRY RUN: nothing uploaded, nothing submitted, 0 credits spent")
        return

    # ---- Safeguard 6: live balance check -----------------------------------------
    remaining = client.remaining_credits()
    log(f"live balance: {remaining:.0f} credits")
    if remaining - estimate < args.reserve_credits:
        raise PipelineError(f"balance {remaining:.0f} minus estimate {estimate} is below the "
                            f"--reserve-credits floor of {args.reserve_credits}")

    # ---- Upload (free) -----------------------------------------------------------
    kind = "video" if args.mode == "video" else "image"
    asset_ids = []
    for p in inputs:
        asset_ids.append(client.upload_media(p, kind))
        log(f"uploaded {p.name} -> {asset_ids[-1]}")
    payload, prompt_type = build_payload(args, asset_ids)

    # ---- Safeguard 1 (again) + 2: the single billable call ------------------------
    assert payload["model"] == DRAFT_MODEL or args.allow_non_draft, "draft-only guard tripped"
    log(f"submitting ONE generation with model={payload['model']}")
    op = client.generate_world(payload)
    operation_id = op["operation_id"]

    # Persist immediately so a crash from here on resumes instead of re-paying.
    state = {"operation_id": operation_id, "submitted_at": utcnow(), "model": payload["model"],
             "inputs": [p.name for p in inputs]}
    atomic_write_json(state_path, state)
    ledger.record_submit(operation_id, payload["model"], estimate)
    log(f"accepted: operation {operation_id} (state saved to {state_path})")

    if not op.get("done"):
        op = poll_operation(client, operation_id, timeout_s=args.poll_timeout,
                            initial_s=args.poll_initial, max_s=args.poll_max)
    complete(args, client, ledger, state_path, state, op)


def complete(args, client, ledger: Ledger, state_path: Path, state: dict, op: dict) -> None:
    world = op.get("response") or {}
    if not world.get("id"):
        world_id = (op.get("metadata") or {}).get("world_id")
        if not world_id:
            raise PipelineError("operation finished without a world in its response")
        world = client.get_world(world_id)
    cost = (op.get("cost") or {}).get("total_credits")
    ledger.record_actual(op["operation_id"], cost)
    state.update({"world": world, "cost": cost, "completed_at": utcnow()})
    atomic_write_json(state_path, state)
    log(f"world {world['id']} ready; charged {cost if cost is not None else 'unknown'} credits; "
        f"ledger total {ledger.spent}")
    if cost is not None and cost > estimate_credits(state.get("model", args.model), "multi-image") * 1.5:
        warn(f"charged {cost} credits, well above the draft estimate. Review billing before the next run.")
    finish(args, client, world, cost)


def main(argv: list[str] | None = None) -> int:
    if sys.version_info >= (3, 13):
        warn("usd-convert-gsplat supports Python 3.11-3.12; conversion may fail on this interpreter")
    args = parse_args(argv)
    try:
        run(args)
    except AmbiguousSubmitError as exc:
        print(f"[world_labs] STOPPED TO PROTECT CREDITS: {exc}", file=sys.stderr)
        return 3
    except PipelineError as exc:
        print(f"[world_labs] ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[world_labs] interrupted; re-run to resume any in-flight operation", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
