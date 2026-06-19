"""Download the Kimodo SOMA model + LLaMA-3-8B text encoder with fallbacks.

Run this inside the Kimodo runtime interpreter (it needs `huggingface_hub`,
which was pulled in by `kimodo`).

Usage:
    # Auto-pick mirror (tries hf, then hf-mirror, then modelscope)
    python download_model.py

    # Force a specific mirror
    python download_model.py --mirror hf-mirror

    # Skip LLaMA (only grab the small 1.13GB kimodo weights)
    python download_model.py --skip-llama

    # Check-only (don't download, just report)
    python download_model.py --check

Environment:
    HF_TOKEN                 — required for LLaMA (gated model)
    HTTPS_PROXY / HTTP_PROXY — proxy for direct HF access
    HF_ENDPOINT              — override hugginface endpoint

Model layout (HF-compatible):
    ~/.cache/huggingface/hub/
        models--nvidia--Kimodo-SOMA-RP-v1/
        models--meta-llama--Meta-Llama-3-8B-Instruct/
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
KIMODO_REPOS = {
    "SOMA-RP-v1.1": "nvidia/Kimodo-SOMA-RP-v1.1",
    "SOMA-RP-v1": "nvidia/Kimodo-SOMA-RP-v1",
    "SOMA-SEED-v1": "nvidia/Kimodo-SOMA-SEED-v1",
}
LLAMA_REPO = "meta-llama/Meta-Llama-3-8B-Instruct"

MIRRORS = {
    "hf": "https://huggingface.co",
    "hf-mirror": "https://hf-mirror.com",
    # ModelScope doesn't mirror nvidia/Kimodo as of 2026-04-14;
    # kept as placeholder for future. Would need custom downloader.
    "modelscope": None,
}


def _log(msg: str, level: str = "INFO") -> None:
    color = {
        "ERROR": "\033[91m",
        "WARN": "\033[93m",
        "OK": "\033[92m",
        "STEP": "\033[96m",
    }.get(level, "")
    reset = "\033[0m" if color else ""
    # On Windows cmd without ANSI support this prints brackets — acceptable.
    print(f"{color}[{level}] {msg}{reset}", flush=True)


def _probe_endpoint(url: str, timeout: float = 5.0) -> bool:
    """Return True if the endpoint is reachable."""
    try:
        import urllib.request

        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:  # noqa: BLE001
        return False


def _pick_mirror(forced: str | None) -> str:
    if forced and forced != "auto":
        if forced not in MIRRORS:
            raise SystemExit(f"Unknown mirror: {forced}")
        if MIRRORS[forced] is None:
            raise SystemExit(f"Mirror {forced} not yet supported")
        return forced
    # Auto: try hf → hf-mirror
    _log("Probing huggingface.co ...", "STEP")
    if _probe_endpoint("https://huggingface.co", timeout=4):
        _log("huggingface.co reachable", "OK")
        return "hf"
    _log("huggingface.co unreachable, trying hf-mirror.com ...", "WARN")
    if _probe_endpoint("https://hf-mirror.com", timeout=4):
        _log("hf-mirror.com reachable", "OK")
        return "hf-mirror"
    _log("No mirror reachable. Check proxy / network.", "ERROR")
    raise SystemExit(2)


def _ensure_env(mirror: str) -> None:
    endpoint = MIRRORS[mirror]
    if endpoint is None:
        raise SystemExit(f"Mirror {mirror} has no endpoint")
    os.environ["HF_ENDPOINT"] = endpoint
    _log(f"HF_ENDPOINT={endpoint}", "OK")


def _hf_snapshot_download(repo_id: str, allow_gated: bool = False) -> Path:
    """Call huggingface_hub.snapshot_download and return the local path.

    Retries up to 3x with exponential backoff on network errors.
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import (
            GatedRepoError,
            RepositoryNotFoundError,
        )
    except ImportError as e:
        _log(f"huggingface_hub not installed: {e}", "ERROR")
        _log("Run the installer first: installer/install.ps1", "ERROR")
        raise SystemExit(3)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            _log(f"snapshot_download({repo_id})  [attempt {attempt + 1}/3]", "STEP")
            local_dir = snapshot_download(
                repo_id=repo_id,
                token=os.environ.get("HF_TOKEN") or True,
                resume_download=True,
                # Let HF manage cache — don't copy files
            )
            return Path(local_dir)
        except GatedRepoError as e:
            _log(
                f"{repo_id} is gated. Login + accept terms: "
                f"https://huggingface.co/{repo_id}",
                "ERROR",
            )
            if not allow_gated:
                raise
            last_err = e
            break
        except RepositoryNotFoundError as e:
            _log(f"Repo not found: {repo_id} — {e}", "ERROR")
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2**attempt * 5
            _log(
                f"Download failed ({type(e).__name__}: {e}) — retry in {wait}s", "WARN"
            )
            time.sleep(wait)
    raise RuntimeError(f"Failed after 3 attempts: {last_err}")


def _check_cached(repo_id: str) -> tuple[bool, float]:
    cache_dir = HF_CACHE / f"models--{repo_id.replace('/', '--')}"
    if not cache_dir.is_dir():
        return False, 0.0
    total = 0
    for f in cache_dir.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    gb = round(total / (1024**3), 2)
    # Heuristic: >0.5GB means weights are actually there
    return gb > 0.5, gb


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Download Kimodo SOMA + LLaMA-3-8B with mirror fallback"
    )
    ap.add_argument(
        "--mirror",
        choices=["auto", "hf", "hf-mirror", "modelscope"],
        default="auto",
    )
    ap.add_argument(
        "--model",
        choices=list(KIMODO_REPOS.keys()) + ["all"],
        default="SOMA-RP-v1",
        help="Which Kimodo variant to download (default: SOMA-RP-v1)",
    )
    ap.add_argument("--skip-llama", action="store_true")
    ap.add_argument("--check", action="store_true", help="Report-only, no download")
    args = ap.parse_args()

    _log("========= Kimodo model download =========", "STEP")

    # Disk check
    try:
        free = shutil.disk_usage(str(Path.home())).free / (1024**3)
        _log(f"Free disk: {free:.1f} GB", "OK" if free > 30 else "WARN")
        if free < 25 and not args.check:
            _log("Need ~17GB free. Aborting.", "ERROR")
            return 4
    except OSError:
        pass

    if args.check:
        report: dict[str, object] = {}
        for name, repo in KIMODO_REPOS.items():
            cached, gb = _check_cached(repo)
            report[name] = {"cached": cached, "size_gb": gb, "repo": repo}
        cached, gb = _check_cached(LLAMA_REPO)
        report["LLaMA-3-8B"] = {"cached": cached, "size_gb": gb, "repo": LLAMA_REPO}
        print(json.dumps(report, indent=2))
        return 0

    mirror = _pick_mirror(args.mirror)
    _ensure_env(mirror)

    # Determine which Kimodo repos to pull
    if args.model == "all":
        kimodo_targets = list(KIMODO_REPOS.values())
    else:
        kimodo_targets = [KIMODO_REPOS[args.model]]

    for repo in kimodo_targets:
        cached, gb = _check_cached(repo)
        if cached:
            _log(f"{repo} already cached ({gb} GB) — skip", "OK")
            continue
        _log(f"Downloading {repo} ...", "STEP")
        try:
            path = _hf_snapshot_download(repo)
            _log(f"{repo} -> {path}", "OK")
        except Exception as e:  # noqa: BLE001
            _log(f"Failed to get {repo}: {e}", "ERROR")
            return 5

    # LLaMA (gated)
    if args.skip_llama:
        _log(
            "Skipping LLaMA (--skip-llama). Kimodo WILL fail at inference time!", "WARN"
        )
    else:
        cached, gb = _check_cached(LLAMA_REPO)
        if cached:
            _log(f"{LLAMA_REPO} already cached ({gb} GB) — skip", "OK")
        else:
            if (
                not os.environ.get("HF_TOKEN")
                and not (Path.home() / ".cache" / "huggingface" / "token").is_file()
            ):
                _log("HF_TOKEN not set and no saved token.", "ERROR")
                _log(
                    "Login: python -m huggingface_hub.commands.huggingface_cli login",
                    "ERROR",
                )
                _log(
                    "Also accept terms at: "
                    "https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct",
                    "ERROR",
                )
                return 6
            _log(f"Downloading {LLAMA_REPO} (~16GB, may take 30+ min) ...", "STEP")
            try:
                path = _hf_snapshot_download(LLAMA_REPO)
                _log(f"{LLAMA_REPO} -> {path}", "OK")
            except Exception as e:  # noqa: BLE001
                _log(f"Failed to get LLaMA: {e}", "ERROR")
                return 7

    _log("=========  Done  =========", "OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
