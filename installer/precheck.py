"""Kimodo Motion runtime precheck.

Pure-stdlib diagnostic that tells Blender UI / install.ps1 what's
missing. Safe to run under Blender's embedded Python OR the
~/.kimodo_venv Python OR any system Python 3.8+.

Usage:
    python precheck.py                  -> prints JSON to stdout
    python precheck.py --pretty         -> pretty-print
    python precheck.py --probe-venv     -> also probe venv's torch/kimodo/fbxsdkpy
                                           (runs a subprocess into the venv,
                                            safe even if current interpreter
                                            doesn't have those modules)

Returns a dict with the contract:

    {
      "python_exe":   str | None,   # current interpreter running this script
      "venv_exe":     str | None,   # ~/.kimodo_venv/Scripts/python.exe if exists
      "venv_ready":   bool,
      "gpu":          {"name": str, "compute_cap": str, "driver": str,
                       "cuda": str | None, "vram_gb": float | None} | None,
      "pytorch":      {"version": str, "cuda_available": bool,
                       "cuda_version": str} | None,
      "fbxsdkpy":     {"installed": bool, "version": str | None},
      "kimodo":       {"installed": bool, "version": str | None,
                       "model_cached": bool, "cache_size_gb": float},
      "hf_token":     {"present": bool, "path": str | None},
      "disk_free_gb": float,
      "errors":       [str, ...],
      "warnings":     [str, ...],
      "next_action":  "run_install" | "fix_proxy" | "download_model"
                      | "login_hf" | "ok" | None,
      "schema":       1,
    }

Design:
    - This script MUST NOT import torch / kimodo / fbxsdkpy in the
      outer interpreter. Probing happens via subprocess into the venv,
      so Blender's embedded Python never touches heavy DLLs.
    - Exit code 0 even on partial failure — errors are data, not process
      failure. Only crashes = exit 1.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = 1

DEFAULT_VENV = Path.home() / ".kimodo_venv"
DEFAULT_RUNTIME = Path.home() / ".kimodo_runtime"
HF_CACHE = Path.home() / ".cache" / "huggingface"
HF_TOKEN_PATH = HF_CACHE / "token"
# kimodo default cache is controlled by HF_HOME; use HF cache as
# source of truth. Model repo ID:
KIMODO_MODEL_REPO = "nvidia/Kimodo-SOMA-RP-v1"


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run a subprocess, swallow all exceptions, return (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def _venv_python(venv: Path) -> Path | None:
    """Return the venv's python.exe if present, else None."""
    if sys.platform == "win32":
        candidate = venv / "Scripts" / "python.exe"
    else:
        candidate = venv / "bin" / "python"
    return candidate if candidate.is_file() else None


def _probe_gpu() -> dict[str, Any] | None:
    rc, out, _ = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,compute_cap,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    if rc != 0 or not out.strip():
        return None
    # Take first line (first GPU)
    parts = [p.strip() for p in out.splitlines()[0].split(",")]
    if len(parts) < 3:
        return None
    name, cc, driver = parts[0], parts[1], parts[2]
    vram_gb: float | None = None
    if len(parts) >= 4:
        try:
            vram_gb = round(float(parts[3]) / 1024.0, 1)
        except ValueError:
            vram_gb = None
    # CUDA version (from nvidia-smi header)
    rc2, out2, _ = _run(["nvidia-smi"])
    cuda = None
    if rc2 == 0 and "CUDA Version" in out2:
        # Parse "CUDA Version: 13.1"
        try:
            seg = out2.split("CUDA Version:", 1)[1]
            cuda = seg.strip().split()[0].strip(" |")
        except Exception:  # noqa: BLE001
            cuda = None
    return {
        "name": name,
        "compute_cap": cc,
        "driver": driver,
        "cuda": cuda,
        "vram_gb": vram_gb,
    }


_VENV_PROBE_SCRIPT = r"""
import json, sys, os

out = {
    "pytorch": None,
    "fbxsdkpy": {"installed": False, "version": None},
    "kimodo":   {"installed": False, "version": None},
}

try:
    import torch
    _mps = getattr(torch.backends, "mps", None)
    out["pytorch"] = {
        "version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_version": getattr(torch.version, "cuda", None),
        "mps_available": bool(_mps and _mps.is_available()),
    }
except Exception as e:
    out["pytorch"] = {"error": f"{type(e).__name__}: {e}"}

# INRIA's pypi package is named `fbxsdkpy` but the import name is
# `fbx` (Autodesk convention — fbxsdkpy ships fbx.pyd + FbxCommon.py).
# Probing `import fbxsdkpy` would ALWAYS fail.
try:
    import fbx  # noqa: F401
    try:
        from importlib.metadata import version as _v
        ver = _v("fbxsdkpy")
    except Exception:
        ver = "unknown"
    out["fbxsdkpy"] = {"installed": True, "version": ver}
except Exception as e:
    out["fbxsdkpy"] = {"installed": False, "version": None,
                       "error": f"{type(e).__name__}: {e}"}

try:
    import kimodo  # noqa: F401
    try:
        from importlib.metadata import version as _v
        kv = _v("kimodo")
    except Exception:
        kv = getattr(kimodo, "__version__", "unknown")
    out["kimodo"] = {"installed": True, "version": kv}
except Exception as e:
    out["kimodo"] = {"installed": False, "version": None,
                     "error": f"{type(e).__name__}: {e}"}

sys.stdout.write(json.dumps(out))
sys.stdout.flush()
# fbxsdkpy has a known DLL-unload segfault on Windows when Python exits.
# Use os._exit(0) to skip normal shutdown (atexit handlers, DLL unload)
# so the subprocess returns rc=0 instead of 0xC0000005.
os._exit(0)
"""


def _probe_venv(venv_py: Path) -> dict[str, Any]:
    rc, out, err = _run([str(venv_py), "-c", _VENV_PROBE_SCRIPT], timeout=30)
    # rc != 0 may be a genuine failure OR an fbxsdkpy exit-segfault.
    # Trust stdout JSON if present — the probe writes+flushes before os._exit.
    if out.strip():
        try:
            parsed = json.loads(out)
            if rc != 0:
                parsed["_probe_warn"] = (
                    f"probe rc={rc} but stdout parsed OK — trusting data"
                )
            return parsed
        except json.JSONDecodeError:
            pass
    return {
        "pytorch": None,
        "fbxsdkpy": {"installed": False, "version": None},
        "kimodo": {"installed": False, "version": None},
        "_probe_error": (err.strip() or out.strip() or "empty")[:400],
    }


def _check_hf_repo_integrity(repo_dir: Path) -> tuple[bool, int]:
    """HF cache tree 结构校验。

    正确结构:
        models--<org>--<name>/
          refs/
            main                 (文件, 内容是 snapshot sha)
          snapshots/
            <sha>/               (真实权重的符号链接或副本)
              config.json / *.safetensors / ...

    用户从网盘 copy 的包如果少了 refs/main 或 snapshots/，huggingface_hub
    会认为没缓存，触发重新下载。
    """
    if not repo_dir.is_dir():
        return False, 0
    refs_main = repo_dir / "refs" / "main"
    snapshots = repo_dir / "snapshots"
    has_refs = refs_main.is_file()
    has_snap = snapshots.is_dir() and any(snapshots.iterdir())
    total = 0
    for f in repo_dir.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return (has_refs and has_snap), total


def _hf_model_cached() -> tuple[bool, float]:
    """Check if the Kimodo SOMA model has been downloaded to HF cache.

    HuggingFace stores snapshots under:
        ~/.cache/huggingface/hub/models--nvidia--Kimodo-SOMA-RP-v1/
    """
    hf_hub = HF_CACHE / "hub"
    if not hf_hub.is_dir():
        return False, 0.0

    # Snapshot dir name convention: "models--{org}--{name}"
    candidates = [
        f"models--{KIMODO_MODEL_REPO.replace('/', '--')}",
        # Also check v1.1
        "models--nvidia--Kimodo-SOMA-RP-v1.1",
    ]
    total_bytes = 0
    valid_found = False
    for cand in candidates:
        p = hf_hub / cand
        valid, size = _check_hf_repo_integrity(p)
        total_bytes += size
        if valid:
            valid_found = True
    # Also account for LLaMA-3-8B (the gated text encoder)
    llama_dir = hf_hub / "models--meta-llama--Meta-Llama-3-8B-Instruct"
    _, llama_size = _check_hf_repo_integrity(llama_dir)
    total_bytes += llama_size
    size_gb = round(total_bytes / (1024**3), 2)
    # "model_cached" means SOMA has valid refs + snapshots AND non-trivial size
    return (valid_found and size_gb > 0.5), size_gb


def _hf_token() -> dict[str, Any]:
    for loc in (HF_TOKEN_PATH, Path.home() / ".huggingface" / "token"):
        if loc.is_file():
            try:
                txt = loc.read_text(encoding="utf-8").strip()
                if txt and len(txt) >= 10:
                    return {"present": True, "path": str(loc)}
            except OSError:
                pass
    # env var
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        return {"present": True, "path": "env"}
    return {"present": False, "path": None}


def _disk_free_gb(path: Path) -> float:
    try:
        p = path if path.exists() else path.parent
        total, used, free = shutil.disk_usage(str(p))
        return round(free / (1024**3), 1)
    except OSError:
        return 0.0


def run(probe_venv: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    venv_py = _venv_python(DEFAULT_VENV)
    venv_ready = venv_py is not None

    gpu = _probe_gpu()
    if gpu is None:
        errors.append(
            "nvidia-smi unavailable — 没检测到 NVIDIA GPU 或驱动，Kimodo 必须 NVIDIA"
        )
    else:
        try:
            cc = float(gpu["compute_cap"])
            if cc < 8.0:
                warnings.append(f"Compute capability {cc} < 8.0 — 推荐 RTX 30/40/50 系")
        except (TypeError, ValueError):
            pass
        if gpu.get("vram_gb") and gpu["vram_gb"] < 16:
            warnings.append(
                f"显存 {gpu['vram_gb']}GB < 16GB — LLaMA-3-8B 文本编码器会 OOM"
            )

    pytorch = None
    fbxsdkpy_info = {"installed": False, "version": None}
    kimodo_info: dict[str, Any] = {
        "installed": False,
        "version": None,
        "model_cached": False,
        "cache_size_gb": 0.0,
    }

    if venv_ready and probe_venv:
        probe = _probe_venv(venv_py)  # type: ignore[arg-type]
        pytorch = probe.get("pytorch")
        fbxsdkpy_info = probe.get("fbxsdkpy") or fbxsdkpy_info
        k = probe.get("kimodo") or {}
        kimodo_info["installed"] = bool(k.get("installed"))
        kimodo_info["version"] = k.get("version")
        if "_probe_error" in probe:
            warnings.append(f"venv probe: {probe['_probe_error']}")

    cached, size_gb = _hf_model_cached()
    kimodo_info["model_cached"] = cached
    kimodo_info["cache_size_gb"] = size_gb

    hf = _hf_token()
    disk_free = _disk_free_gb(DEFAULT_RUNTIME)

    # Aggregate error checks.
    # Only claim pytorch/fbxsdkpy/kimodo are missing when we actually probed —
    # with --no-venv-probe the defaults (null/False) mean "not checked", not "absent".
    if not venv_ready:
        errors.append(f"venv not found: {DEFAULT_VENV}")
    if venv_ready and probe_venv:
        if pytorch is None:
            errors.append("PyTorch not installed in venv")
        elif "error" in pytorch:
            errors.append(f"PyTorch import error: {pytorch['error']}")
        elif not pytorch.get("cuda_available"):
            errors.append("PyTorch installed but CUDA unavailable — 版本不匹配")
        if not fbxsdkpy_info["installed"]:
            errors.append("fbxsdkpy not installed (FBX SDK Python wrapper)")
        if not kimodo_info["installed"]:
            errors.append("kimodo not installed")
    if not hf["present"] and kimodo_info["installed"]:
        warnings.append("HuggingFace token 未设置 — LLaMA-3-8B 是 gated model 必须登录")
    if disk_free < 30:
        warnings.append(f"磁盘剩余 {disk_free}GB < 30GB — 模型要 17GB+")

    # Next action recommendation
    next_action: str | None
    if not venv_ready or not pytorch or (pytorch and "error" in pytorch):
        next_action = "run_install"
    elif not fbxsdkpy_info["installed"] or not kimodo_info["installed"]:
        next_action = "run_install"
    elif not hf["present"]:
        next_action = "login_hf"
    elif not kimodo_info["model_cached"]:
        next_action = "download_model"
    elif errors:
        next_action = "run_install"
    else:
        next_action = "ok"

    return {
        "python_exe": sys.executable,
        "venv_exe": str(venv_py) if venv_py else None,
        "venv_ready": venv_ready,
        "gpu": gpu,
        "pytorch": pytorch,
        "fbxsdkpy": fbxsdkpy_info,
        "kimodo": kimodo_info,
        "hf_token": hf,
        "disk_free_gb": disk_free,
        "errors": errors,
        "warnings": warnings,
        "next_action": next_action,
        "schema": SCHEMA,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--no-venv-probe", action="store_true")
    args = ap.parse_args()
    result = run(probe_venv=not args.no_venv_probe)
    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
