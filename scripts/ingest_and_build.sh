#!/usr/bin/env bash
# =============================================================================
# Auto-Twin | Thread 1 | scripts/ingest_and_build.sh
#
# Edge-ingest stage of the Auto-Twin pipeline:
#
#   YouTube walkthrough  --yt-dlp-->  /tmp/autotwin/source.<ext>
#                        --ffmpeg-->  input_images/frame_NNN.jpg   (1 fps, <= 40)
#                        --python-->  public/factory.usdz          (World Labs + NVIDIA)
#
# Frame extraction is a two-pass, blur-aware process rather than a naive
# `fps=1`:
#   Pass 1  decodes the capture window at SAMPLES_PER_SEC (default 4) candidate
#           frames per second and scores every candidate with ffmpeg's
#           `blurdetect` filter (lower score == sharper).
#   Pass 2  keeps exactly ONE frame per second (the sharpest candidate in that
#           second), then deinterlaces, denoises, sharpens and writes high
#           quality JPEGs. The result is still "1 frame per second" but each
#           frame is the least motion-blurred moment of its second.
#
# COST PROTECTION (frames are the unit that drives World Labs spend):
#   * HARD_MAX_FRAMES=40 is a ceiling that no flag can raise.
#   * Only frame_NNN.jpg files this script creates are ever deleted.
#   * This script never calls World Labs itself; it hands off to
#     scripts/world_labs_to_usd.py, which owns every credit-spending guard
#     (draft-only model, ledger, balance check, resume-instead-of-resubmit).
#   * --ingest-only and --dry-run let you rehearse the whole thing for 0 credits.
#
# Usage:
#   scripts/ingest_and_build.sh [options] [-- <extra args for world_labs_to_usd.py>]
#
# Options:
#   --url URL            YouTube URL (default: Central Texas Food Bank video)
#   --video-file PATH    Use a local video instead of downloading
#   --start SECONDS      Start of the 40 s capture window (default: 0)
#   --max-frames N       Frames to keep, 1..40 (default: 40)
#   --samples-per-sec N  Blur-scoring candidates per second, 1..8 (default: 4)
#   --max-width PX       Downscale wider frames to this width (default: 1920)
#   --interval SECONDS   Keep one frame every N seconds, 1..30 (default: 1).
#                        The capture window becomes max-frames * interval, so a
#                        larger interval spreads 40 frames over a longer walk.
#   --crop-bottom PX     Drop PX rows from the bottom of every frame before
#                        scaling, e.g. a broadcast lower-third (default: 0)
#   --force-download     Re-download even if a cached copy exists in /tmp
#   --ingest-only        Stop after frame extraction (spends 0 credits)
#   --dry-run            Run the Python stage in dry-run mode (spends 0 credits)
#   -h, --help           Show this help
#
# Examples:
#   scripts/ingest_and_build.sh --ingest-only                 # frames only
#   scripts/ingest_and_build.sh --start 35 --dry-run          # rehearse API call
#   scripts/ingest_and_build.sh --video-file tour.mp4 --start 305 --interval 4 \
#       --crop-bottom 264 --ingest-only                       # warehouse aisle, no overlay
#   WORLD_LABS_API_KEY=... scripts/ingest_and_build.sh        # real draft run (~250 credits)
# =============================================================================

# -E: ERR trap is inherited by functions; -e: exit on error;
# -u: unset variables are errors; -o pipefail: a failing stage fails the pipe.
set -Eeuo pipefail
IFS=$'\n\t'
umask 022

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
readonly DEFAULT_URL="https://www.youtube.com/watch?v=8sQmr7JqXkI"
readonly HARD_MAX_FRAMES=40           # absolute ceiling; never raised by flags
readonly WORK_DIR="/tmp/autotwin"     # download cache + scratch (per the spec)

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT
readonly FRAMES_DIR="${REPO_ROOT}/input_images"
readonly PY_STAGE="${SCRIPT_DIR}/world_labs_to_usd.py"

# -----------------------------------------------------------------------------
# Defaults (overridable by flags)
# -----------------------------------------------------------------------------
URL="${DEFAULT_URL}"
VIDEO_FILE=""
START_SEC=0
MAX_FRAMES=${HARD_MAX_FRAMES}
SAMPLES_PER_SEC=4
MAX_WIDTH=1920
INTERVAL=1
CROP_BOTTOM=0
FORCE_DOWNLOAD=0
INGEST_ONLY=0
DRY_RUN=0
PY_ARGS=()

# -----------------------------------------------------------------------------
# Logging + error handling
# -----------------------------------------------------------------------------
log()  { printf '\033[1;36m[ingest]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[ingest] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[ingest] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

on_err() {
  local exit_code=$? line=$1 cmd=$2
  printf '\033[1;31m[ingest] FAILED\033[0m (exit %s) at line %s: %s\n' \
    "${exit_code}" "${line}" "${cmd}" >&2
  exit "${exit_code}"
}
trap 'on_err "${LINENO}" "${BASH_COMMAND}"' ERR

SCRATCH=""
cleanup() { [[ -n "${SCRATCH}" && -d "${SCRATCH}" ]] && rm -rf -- "${SCRATCH}"; }
trap cleanup EXIT
trap 'die "interrupted"' INT TERM

usage() { awk 'NR>2 && /^# =+$/ {exit} /^# Usage:/ {on=1} on {sub(/^# ?/, ""); print}' "${BASH_SOURCE[0]}"; }

is_uint() { [[ "$1" =~ ^[0-9]+$ ]]; }
is_num()  { [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]; }

# -----------------------------------------------------------------------------
# Argument parsing
# -----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)              URL="${2:?--url needs a value}"; shift 2 ;;
    --video-file)       VIDEO_FILE="${2:?--video-file needs a value}"; shift 2 ;;
    --start)            START_SEC="${2:?--start needs a value}"; shift 2 ;;
    --max-frames)       MAX_FRAMES="${2:?--max-frames needs a value}"; shift 2 ;;
    --samples-per-sec)  SAMPLES_PER_SEC="${2:?--samples-per-sec needs a value}"; shift 2 ;;
    --max-width)        MAX_WIDTH="${2:?--max-width needs a value}"; shift 2 ;;
    --interval)         INTERVAL="${2:?--interval needs a value}"; shift 2 ;;
    --crop-bottom)      CROP_BOTTOM="${2:?--crop-bottom needs a value}"; shift 2 ;;
    --force-download)   FORCE_DOWNLOAD=1; shift ;;
    --ingest-only)      INGEST_ONLY=1; shift ;;
    --dry-run)          DRY_RUN=1; shift ;;
    -h|--help)          usage; exit 0 ;;
    --)                 shift; PY_ARGS=("$@"); break ;;
    *)                  die "unknown option: $1 (see --help)" ;;
  esac
done

is_num  "${START_SEC}"       || die "--start must be a non-negative number"
is_uint "${MAX_FRAMES}"      || die "--max-frames must be an integer"
is_uint "${SAMPLES_PER_SEC}" || die "--samples-per-sec must be an integer"
is_uint "${MAX_WIDTH}"       || die "--max-width must be an integer"
(( SAMPLES_PER_SEC >= 1 && SAMPLES_PER_SEC <= 8 )) || die "--samples-per-sec must be 1..8"
(( MAX_WIDTH >= 320 ))                             || die "--max-width must be >= 320"
is_uint "${INTERVAL}"        || die "--interval must be an integer"
is_uint "${CROP_BOTTOM}"     || die "--crop-bottom must be an integer"
(( INTERVAL >= 1 && INTERVAL <= 30 ))              || die "--interval must be 1..30"

# COST GUARD: clamp to the hard ceiling no matter what was requested.
if (( MAX_FRAMES < 1 )); then die "--max-frames must be >= 1"; fi
if (( MAX_FRAMES > HARD_MAX_FRAMES )); then
  warn "--max-frames ${MAX_FRAMES} exceeds the hard cap; clamping to ${HARD_MAX_FRAMES}"
  MAX_FRAMES=${HARD_MAX_FRAMES}
fi

# -----------------------------------------------------------------------------
# 1. Dependency checks
# -----------------------------------------------------------------------------
require() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency '$1'. $2"
}

log "Checking dependencies..."
require ffmpeg  "Install: sudo apt-get install ffmpeg  |  brew install ffmpeg"
require ffprobe "ffprobe ships with ffmpeg; reinstall ffmpeg."
[[ -n "${VIDEO_FILE}" ]] || require yt-dlp "Install: python3 -m pip install -U yt-dlp  |  brew install yt-dlp"
(( INGEST_ONLY )) || require python3 "Install Python 3.11 or 3.12 (required by usd-convert-gsplat)."

# blurdetect landed in FFmpeg 6.0. Without it we fall back to plain fps=1.
HAS_BLURDETECT=0
# Capture output first: `ffmpeg | grep -q` would SIGPIPE ffmpeg and, under
# pipefail, report failure even when the filter exists.
FF_FILTERS="$(ffmpeg -hide_banner -filters 2>/dev/null || true)"
FF_HELP="$(ffmpeg -hide_banner -h full 2>/dev/null || true)"
if grep -qE '^\s*\S+\s+blurdetect\s' <<<"${FF_FILTERS}"; then
  HAS_BLURDETECT=1
else
  warn "ffmpeg lacks the 'blurdetect' filter (needs FFmpeg >= 6.0); using plain 1 fps sampling"
fi
# -fps_mode replaced -vsync in FFmpeg 5.1.
if grep -q -- '-fps_mode' <<<"${FF_HELP}"; then
  PASSTHRU=(-fps_mode passthrough)
else
  PASSTHRU=(-vsync passthrough)
fi

mkdir -p -- "${WORK_DIR}" "${FRAMES_DIR}"
SCRATCH="$(mktemp -d "${WORK_DIR}/scratch.XXXXXX")"

# -----------------------------------------------------------------------------
# 2. Acquire the source video (download once, cache in /tmp/autotwin)
# -----------------------------------------------------------------------------
if [[ -n "${VIDEO_FILE}" ]]; then
  [[ -f "${VIDEO_FILE}" ]] || die "video file not found: ${VIDEO_FILE}"
  SRC="${VIDEO_FILE}"
  log "Using local video: ${SRC}"
else
  # Cache key is the URL, so a different video never reuses a stale download.
  url_key="$(printf '%s' "${URL}" | sha256sum | cut -c1-12)"
  SRC="$(find "${WORK_DIR}" -maxdepth 1 -type f -name "source_${url_key}.*" \
          ! -name '*.part' ! -name '*.ytdl' -print -quit 2>/dev/null || true)"
  if [[ -n "${SRC}" && ${FORCE_DOWNLOAD} -eq 0 ]]; then
    log "Using cached download: ${SRC}"
  else
    log "Downloading ${URL} -> ${WORK_DIR}/ ..."
    # Video-only stream (audio is useless for reconstruction and skipping it
    # avoids a merge step). Prefer <=1080p: higher resolutions add upload time
    # and decode cost without improving a 1920px-wide frame.
    SRC="$(yt-dlp \
            --no-playlist --no-progress --restrict-filenames \
            --retries 5 --fragment-retries 5 --retry-sleep exp=1:30 \
            --socket-timeout 30 \
            -f 'bv*[height<=1080]/b[height<=1080]/bv*/b' \
            -o "${WORK_DIR}/source_${url_key}.%(ext)s" \
            --print after_move:filepath \
            "${URL}")" \
      || die "yt-dlp failed. YouTube often blocks datacenter/CI IPs; download the video on a workstation and pass --video-file PATH."
    [[ -f "${SRC}" ]] || die "yt-dlp reported success but no file at '${SRC}'"
  fi
fi

# -----------------------------------------------------------------------------
# 3. Probe the video and size the capture window
# -----------------------------------------------------------------------------
DURATION="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "${SRC}" || true)"
is_num "${DURATION%%.*}" || die "could not read duration from ${SRC} (corrupt download?)"
VSTREAM="$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height,field_order \
             -of csv=p=0 "${SRC}" || true)"
[[ -n "${VSTREAM}" ]] || die "no video stream in ${SRC}"
log "Source: ${DURATION}s, stream (w,h,field_order) = ${VSTREAM}"

# awk handles the float math portably (no bc dependency).
awk -v s="${START_SEC}" -v d="${DURATION}" 'BEGIN{exit !(s < d)}' \
  || die "--start ${START_SEC}s is beyond the video's ${DURATION}s"
WINDOW="$(awk -v s="${START_SEC}" -v d="${DURATION}" -v m="${MAX_FRAMES}" -v i="${INTERVAL}" \
            'BEGIN{w=d-s; if (w>m*i) w=m*i; printf "%.3f", w}')"
log "Capture window: ${START_SEC}s .. +${WINDOW}s  (max ${MAX_FRAMES} frames, 1 every ${INTERVAL}s)"

SRC_H="$(cut -d, -f2 <<<"${VSTREAM}")"
CROP=""
if (( CROP_BOTTOM > 0 )); then
  is_uint "${SRC_H}" || die "could not read frame height for --crop-bottom"
  (( CROP_BOTTOM < SRC_H / 2 )) || die "--crop-bottom ${CROP_BOTTOM} would remove half the ${SRC_H}px frame"
  # Even height keeps 4:2:0 chroma happy.
  CROP="crop=iw:$(( (SRC_H - CROP_BOTTOM) / 2 * 2 )):0:0,"
  log "Cropping ${CROP_BOTTOM}px from the bottom of each frame"
fi

# Shared head of both ffmpeg passes. Identical seeking + fps means the frame
# index `n` in pass 2 refers to exactly the same picture scored in pass 1.
# -ss before -i is a fast seek; when transcoding ffmpeg still lands frame-accurately.
COMMON_IN=(-hide_banner -loglevel error -nostdin -ss "${START_SEC}" -t "${WINDOW}" -i "${SRC}" -an -sn -dn)

# Post-selection enhancement chain, in order:
#   bwdif    deinterlace, but only frames flagged interlaced (no-op on progressive)
#   scale    cap width with Lanczos (sharpest standard resampler); -2 keeps even height
#   hqdn3d   light spatial-only denoise (temporal terms 0: frames are 1 s apart)
#   unsharp  mild luma unsharp mask to recover edge contrast lost to compression
#   format   full-range 4:2:0 for the MJPEG encoder
ENHANCE="bwdif=mode=send_frame:parity=auto:deint=interlaced,${CROP}scale='min(${MAX_WIDTH},iw)':-2:flags=lanczos,hqdn3d=1.5:1.0:0:0,unsharp=5:5:0.6:5:5:0.0,format=yuvj420p"

# Delete only frames this script owns, never arbitrary user files.
find "${FRAMES_DIR}" -maxdepth 1 -type f -name 'frame_[0-9][0-9][0-9].jpg' -delete

# -----------------------------------------------------------------------------
# 4. Two-pass, blur-aware 1 fps extraction
# -----------------------------------------------------------------------------
if (( HAS_BLURDETECT )); then
  log "Pass 1/2: scoring ${SAMPLES_PER_SEC} candidates/s for motion blur..."
  ffmpeg "${COMMON_IN[@]}" \
    -vf "fps=${SAMPLES_PER_SEC},blurdetect=block_width=32:block_height=32,metadata=mode=print:key=lavfi.blur:file=${SCRATCH}/blur.txt" \
    -f null -

  # blur.txt alternates "frame:N pts:P pts_time:T" / "lavfi.blur=X".
  # For each INTERVAL-second bucket (relative to the window) keep the lowest blur score.
  SELECT_EXPR="$(awk -v spp="${SAMPLES_PER_SEC}" -v iv="${INTERVAL}" -v max="${MAX_FRAMES}" '
      /^frame:/ { n=$1; sub("frame:","",n); n+=0; next }
      /^lavfi.blur=/ {
        b=$0; sub("lavfi.blur=","",b); b+=0
        sec=int(n/(spp*iv))
        if (sec < max && (!(sec in best) || b < best[sec])) { best[sec]=b; pick[sec]=n }
      }
      END {
        out=""
        for (s=0; s<max; s++) if (s in pick) out = out (out==""?"":"+") "eq(n," pick[s] ")"
        print out
      }' "${SCRATCH}/blur.txt")"
  [[ -n "${SELECT_EXPR}" ]] || die "blur scoring produced no frames (is the window empty?)"

  log "Pass 2/2: extracting the sharpest frame of each ${INTERVAL}s bucket..."
  ffmpeg "${COMMON_IN[@]}" \
    -vf "fps=${SAMPLES_PER_SEC},select='${SELECT_EXPR}',${ENHANCE}" \
    "${PASSTHRU[@]}" -frames:v "${MAX_FRAMES}" -q:v 2 \
    "${FRAMES_DIR}/frame_%03d.jpg"
else
  log "Extracting 1 frame every ${INTERVAL}s (no blur scoring available)..."
  ffmpeg "${COMMON_IN[@]}" \
    -vf "fps=1/${INTERVAL},${ENHANCE}" \
    "${PASSTHRU[@]}" -frames:v "${MAX_FRAMES}" -q:v 2 \
    "${FRAMES_DIR}/frame_%03d.jpg"
fi

# -----------------------------------------------------------------------------
# 5. Verify the output
# -----------------------------------------------------------------------------
FRAME_COUNT="$(find "${FRAMES_DIR}" -maxdepth 1 -type f -name 'frame_[0-9][0-9][0-9].jpg' | wc -l | tr -d ' ')"
(( FRAME_COUNT >= 1 )) || die "no frames were written to ${FRAMES_DIR}"
if (( FRAME_COUNT > HARD_MAX_FRAMES )); then
  die "internal error: ${FRAME_COUNT} frames exceeds the hard cap of ${HARD_MAX_FRAMES}"
fi
# Zero-byte or truncated JPEGs would waste an upload; catch them here.
while IFS= read -r f; do
  [[ -s "${f}" ]] || die "empty frame: ${f}"
done < <(find "${FRAMES_DIR}" -maxdepth 1 -type f -name 'frame_[0-9][0-9][0-9].jpg')
log "Wrote ${FRAME_COUNT} frame(s) to ${FRAMES_DIR}"

if (( INGEST_ONLY )); then
  log "--ingest-only: stopping before World Labs (0 credits spent)."
  exit 0
fi

# -----------------------------------------------------------------------------
# 6. Hand off to the spatial build stage
# -----------------------------------------------------------------------------
[[ -f "${PY_STAGE}" ]] || die "missing ${PY_STAGE}"
if (( ! DRY_RUN )) && [[ -z "${WORLD_LABS_API_KEY:-}" ]]; then
  die "WORLD_LABS_API_KEY is not set. Export it, or use --dry-run / --ingest-only."
fi

PY_CMD=(python3 "${PY_STAGE}"
        --input-dir "${FRAMES_DIR}"
        --output "${REPO_ROOT}/public/factory.usdz"
        --work-dir "${REPO_ROOT}/build")
(( DRY_RUN )) && PY_CMD+=(--dry-run)
PY_CMD+=("${PY_ARGS[@]+"${PY_ARGS[@]}"}")

log "Running: $(printf '%q ' "${PY_CMD[@]}")"
"${PY_CMD[@]}"
log "Done."
