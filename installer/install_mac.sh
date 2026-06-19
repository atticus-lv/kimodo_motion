#!/usr/bin/env bash
# Kimodo Motion — One-click runtime installer (macOS / Apple Silicon, idempotent).
#
# Mirrors install.ps1 but for macOS. Because retargeting now runs *inside Blender*
# (retarget/bpy_retarget.py), the Mac runtime does NOT need the Autodesk FBX SDK
# (fbxsdkpy) or a separate FBX subprocess — so this installer is much smaller than
# the Windows one: just a venv + PyTorch (Metal/MPS) + kimodo + the FastAPI server.
#
# Steps:
#   1. Check macOS + locate Python 3.10-3.13
#   2. Create ~/KimodoMotionRuntime/venv
#   3. pip mirror + upgrade pip
#   4. Install PyTorch (default PyPI wheels — Apple Silicon builds ship MPS/Metal)
#   5. Install kimodo (MPS fork by default) + FastAPI server deps
#   6. Run precheck.py
#
# Options via environment variables (the Blender install panel sets these):
#   KIMODO_PROXY        http(s) proxy, e.g. http://127.0.0.1:7890
#   KIMODO_PIP_MIRROR   auto | pypi | tsinghua | aliyun     (default: auto)
#   KIMODO_GIT_URL      kimodo git URL (default: atticus-lv MPS fork)
#   KIMODO_SKIP_TORCH   1 to skip torch (dev only)
#
# Optional official post-processing:
#   Install cmake/simde/pybind11/eigen and reinstall kimodo without
#   SKIP_MOTION_CORRECTION_IN_SETUP; see INSTALL.md.
#
# LICENSE: kimodo is Apache-2.0; Meta-Llama-3-8B is gated — supply your own HF token.

set -uo pipefail

# Everything is anchored to the venv path so the whole runtime stays in one place
# (delete that one folder to fully uninstall). KIMODO_VENV is passed by the Blender
# install panel from the addon's venv_path preference; runtime/log and the HF model
# cache are placed as SIBLINGS of the venv so nothing lands in ~/.cache or system dirs.
VENV_PATH="${KIMODO_VENV:-$HOME/KimodoMotionRuntime/venv}"
BASE_DIR="$(dirname "$VENV_PATH")"
RUNTIME_DIR="${KIMODO_RUNTIME:-$BASE_DIR/runtime}"
HF_CACHE="${KIMODO_HF_HOME:-$BASE_DIR/hf-cache}"
export HF_HOME="$HF_CACHE"   # models download here at first generation, not ~/.cache
LOG_PATH="$RUNTIME_DIR/install.log"
PY_MIN_MINOR=10
PY_MAX_MINOR=13
KIMODO_GIT_URL="${KIMODO_GIT_URL:-https://github.com/atticus-lv/kimodo.git}"
PIP_MIRROR="${KIMODO_PIP_MIRROR:-auto}"

mkdir -p "$RUNTIME_DIR"

log() {  # log LEVEL msg...
    local level="$1"; shift
    local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"
    local line="[$ts][$level] $*"
    echo "$line" >>"$LOG_PATH"
    case "$level" in
        ERROR) printf '\033[31m%s\033[0m\n' "$line" ;;
        WARN)  printf '\033[33m%s\033[0m\n' "$line" ;;
        OK)    printf '\033[32m%s\033[0m\n' "$line" ;;
        STEP)  printf '\033[36m%s\033[0m\n' "$line" ;;
        *)     echo "$line" ;;
    esac
}
die() { log ERROR "$*"; echo; read -r -p "安装失败，按回车关闭…" _ || true; exit 1; }

log STEP "========== Kimodo Runtime Installer (macOS) =========="
log INFO "Runtime dir: $RUNTIME_DIR"
log INFO "Venv path:   $VENV_PATH"
log INFO "kimodo url:  $KIMODO_GIT_URL"

if [ -n "${KIMODO_PROXY:-}" ]; then
    export HTTP_PROXY="$KIMODO_PROXY" HTTPS_PROXY="$KIMODO_PROXY"
    log INFO "HTTP(S)_PROXY = $KIMODO_PROXY"
fi

# ─── Step 0: platform ─────────────────────────────────────────
log STEP "[0/6] Check platform"
[ "$(uname -s)" = "Darwin" ] || die "This installer is for macOS. On Windows use install.ps1."
ARCH="$(uname -m)"
log OK "macOS $(sw_vers -productVersion 2>/dev/null || echo '?')  arch=$ARCH"
[ "$ARCH" = "arm64" ] || log WARN "Not Apple Silicon (arm64=$ARCH). MPS/Metal may be unavailable; kimodo will fall back to CPU."

# ─── Step 1: locate Python 3.10-3.13 ──────────────────────────
log STEP "[1/6] Locate Python ${PY_MIN_MINOR}-${PY_MAX_MINOR}"
PYTHON_EXE=""
pick_python() {
    local exe minor
    for exe in python3.11 python3.12 python3.10 python3.13 python3; do
        command -v "$exe" >/dev/null 2>&1 || continue
        minor="$("$exe" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)" || continue
        local major; major="$("$exe" -c 'import sys; print(sys.version_info.major)' 2>/dev/null)"
        [ "$major" = "3" ] || continue
        if [ "$minor" -ge "$PY_MIN_MINOR" ] && [ "$minor" -le "$PY_MAX_MINOR" ]; then
            PYTHON_EXE="$(command -v "$exe")"; return 0
        fi
    done
    return 1
}
if ! pick_python; then
    die "No Python ${PY_MIN_MINOR}-${PY_MAX_MINOR} found. Install one (e.g. 'brew install python@3.11' or from python.org) and re-run."
fi
log OK "Using $PYTHON_EXE ($("$PYTHON_EXE" -V 2>&1))"

# ─── Step 2: create venv ──────────────────────────────────────
log STEP "[2/6] Create virtual environment"
VPY="$VENV_PATH/bin/python"
if [ -x "$VPY" ]; then
    log OK "venv already exists at $VENV_PATH — reusing"
else
    "$PYTHON_EXE" -m venv "$VENV_PATH" || die "venv creation failed"
    log OK "venv created at $VENV_PATH"
fi
[ -x "$VPY" ] || die "venv python missing at $VPY"

# ─── Step 3: pip index + upgrade ──────────────────────────────
log STEP "[3/6] Pick pip mirror + upgrade pip"
PIP_INDEX="https://pypi.org/simple"
case "$PIP_MIRROR" in
    tsinghua) PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple" ;;
    aliyun)   PIP_INDEX="https://mirrors.aliyun.com/pypi/simple" ;;
    pypi)     PIP_INDEX="https://pypi.org/simple" ;;
    auto)
        if curl -fsS --max-time 5 https://pypi.org/simple/pip/ >/dev/null 2>&1; then
            PIP_INDEX="https://pypi.org/simple"
        else
            PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
            log WARN "pypi.org slow/unreachable — using TUNA mirror"
        fi ;;
esac
log OK "pip index: $PIP_INDEX"
"$VPY" -m pip install --upgrade pip setuptools wheel --index-url "$PIP_INDEX" || die "pip upgrade failed"

# ─── Step 4: PyTorch (Metal/MPS) ──────────────────────────────
if [ "${KIMODO_SKIP_TORCH:-0}" = "1" ]; then
    log WARN "[4/6] Skip PyTorch (KIMODO_SKIP_TORCH=1)"
else
    log STEP "[4/6] Install PyTorch (Apple Silicon wheels ship Metal/MPS)"
    if "$VPY" -c 'import torch' >/dev/null 2>&1; then
        log OK "torch $("$VPY" -c 'import torch; print(torch.__version__)') already installed — skip"
    else
        # Default PyPI wheels on macOS arm64 include the MPS backend (no CUDA channel).
        # Keep official PyPI as --extra-index-url so a partial CN mirror can't miss the
        # arm64/MPS wheel (Windows isolates torch on download.pytorch.org for the same reason).
        "$VPY" -m pip install torch torchvision torchaudio \
            --index-url "$PIP_INDEX" --extra-index-url "https://pypi.org/simple" \
            --retries 5 --timeout 120 \
            || die "torch install failed"
    fi
    if "$VPY" -c 'import torch,sys; sys.exit(0 if torch.backends.mps.is_available() else 1)' 2>/dev/null; then
        log OK "torch MPS backend available"
    else
        log WARN "torch installed but MPS not available — will run on CPU"
    fi
fi

# ─── Step 5: kimodo + server deps (NO fbxsdkpy) ───────────────
log STEP "[5/6] Install kimodo + FastAPI server deps"
log INFO "fbxsdkpy is intentionally NOT installed on macOS — retarget runs inside Blender."
# Plain `kimodo` (NOT kimodo[all]): the FastAPI server only needs load_model + skeleton +
# exports.bvh, none of which import viser or py-soma-x (verified). This avoids the heavy
# SOMA-X build on macOS. Server runtime deps (fastapi/uvicorn/scipy/bvhio) are added below.
if "$VPY" -c 'import kimodo' >/dev/null 2>&1; then
    log OK "kimodo $("$VPY" -c 'import kimodo; print(getattr(kimodo,"__version__","unknown"))' 2>/dev/null) already installed — skip"
else
    # SKIP_MOTION_CORRECTION_IN_SETUP=1: keep the default macOS install small and
    # robust by skipping kimodo's optional native motion-correction package. Recent
    # kimodo forks can build it on Apple Silicon via SIMDe; see INSTALL.md for the
    # optional official post-processing setup.
    # KIMODO_GIT_URL may be a local directory (e.g. a working copy with MPS support):
    # install it as a path; otherwise treat it as a git URL.
    if [ -d "$KIMODO_GIT_URL" ]; then
        log INFO "Installing kimodo from local path $KIMODO_GIT_URL …"
        SKIP_MOTION_CORRECTION_IN_SETUP=1 "$VPY" -m pip install "$KIMODO_GIT_URL" \
            --index-url "$PIP_INDEX" --retries 5 --timeout 180 \
            || die "kimodo install failed"
    else
        log INFO "Installing kimodo from $KIMODO_GIT_URL (this takes a while)…"
        SKIP_MOTION_CORRECTION_IN_SETUP=1 "$VPY" -m pip install "kimodo @ git+${KIMODO_GIT_URL}" \
            --index-url "$PIP_INDEX" --retries 5 --timeout 180 \
            || die "kimodo install failed"
    fi
fi
if "$VPY" -c 'import importlib.util; raise SystemExit(0 if importlib.util.find_spec("motion_correction") else 1)' >/dev/null 2>&1; then
    log OK "Kimodo motion_correction found — official post-processing can be enabled."
else
    log WARN "Kimodo motion_correction is not installed on macOS; official post-processing will be skipped."
    log WARN "Optional: brew install cmake simde pybind11 eigen, then reinstall kimodo without SKIP_MOTION_CORRECTION_IN_SETUP to enable it."
fi
# Sanity: warn loudly if the installed kimodo lacks the MPS backend (e.g. the remote
# fork was not pushed) — generation would silently fall back to CPU.
if ! "$VPY" -c 'import kimodo.device_utils' >/dev/null 2>&1; then
    log WARN "Installed kimodo has no device_utils (MPS backend) — it will run on CPU."
    log WARN "Use a kimodo with MPS support: set KIMODO_GIT_URL to your MPS fork/branch or a local path."
fi
"$VPY" -m pip install scipy fastapi "uvicorn[standard]" pydantic bvhio requests \
    --index-url "$PIP_INDEX" --retries 5 --timeout 60 \
    || die "server deps install failed"

# ─── Step 6: verify ───────────────────────────────────────────
log STEP "[6/6] Run precheck.py"
PRECHECK="$(cd "$(dirname "$0")" && pwd)/precheck.py"
if [ -f "$PRECHECK" ]; then
    "$VPY" "$PRECHECK" --pretty || log WARN "precheck returned non-zero (partial install)"
else
    log WARN "precheck.py not found at $PRECHECK — skipping verify"
fi

log OK ""
log OK "=============================================="
log OK "Kimodo runtime installed."
log OK "Venv:   $VENV_PATH"
log OK "Python: $VPY"
log OK "Log:    $LOG_PATH"
log STEP "Next steps:"
log STEP "  1. Blender > Preferences > Add-ons > Kimodo Motion > venv path = $VENV_PATH"
log STEP "  2. First generation downloads ~16GB LLaMA-3-8B text encoder (gated):"
log STEP "       $VPY -m huggingface_hub.commands.huggingface_cli login"
log STEP "  3. Text encoder + diffusion run on Metal (MPS) automatically; override with KIMODO_DEVICE."
echo
read -r -p "完成，按回车关闭…" _ || true
