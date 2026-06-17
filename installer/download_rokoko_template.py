"""Download a default T-pose FBX template for Kimodo retarget.

RATIONALE:
    Kimodo's FBX retarget bridge needs a reference T-pose FBX to target
    when no user rig is selected. Mixamo X Bot is the de-facto standard
    but requires Adobe login (cannot auto-download).

    Rokoko's Bruno mascot is explicitly free for commercial use but has
    no stable public CDN URL — the Rokoko team uses form-gated downloads.

    Therefore this script:
      1. Tries a list of known-stable URLs (currently empty / user-maintained)
      2. If all fail, falls back to a minimal SOMA-compatible skeleton
         programmatically generated via `kimodo_fbxbuilder` (fbxsdkpy
         script that writes a 24-bone humanoid T-pose).
      3. Caches output to ~/.kimodo_runtime/templates/default_tpose.fbx

LICENSE:
    - Bundled URLs point only to assets that are explicitly CC0 /
      CC-BY or "free for commercial use". See LICENSES.md in templates dir.
    - Do NOT point this to Mixamo / Adobe content.

Usage:
    python download_rokoko_template.py                 # Try all URLs
    python download_rokoko_template.py --force-build   # Skip URLs, build programmatically
    python download_rokoko_template.py --verify        # Check only
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

TEMPLATES_DIR = Path.home() / ".kimodo_runtime" / "templates"
OUTPUT_FBX = TEMPLATES_DIR / "default_tpose.fbx"

# Curated list — currently empty because:
#   - Rokoko Bruno: form-gated, no CDN URL
#   - Mixamo X Bot: Adobe ToS forbids redistribution
#   - RPM: platform-specific generator, no public CDN
# ADD HERE as you verify licenses:
#     (name, url, license, sha256)
TEMPLATE_URLS: list[tuple[str, str, str, str]] = [
    # Example (commented out until verified):
    # ("DefaultTPose", "https://example.com/tpose.fbx", "CC0", "<sha256>"),
]


def _log(msg: str, level: str = "INFO") -> None:
    tag = {"ERROR": "[E]", "WARN": "[W]", "OK": "[+]", "STEP": "[*]"}.get(level, "[-]")
    print(f"{tag} {msg}", flush=True)


def _try_download(name: str, url: str, dest: Path) -> bool:
    try:
        _log(f"Fetching {name} from {url}", "STEP")
        with urllib.request.urlopen(url, timeout=30) as resp:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        _log(f"Downloaded to {dest}", "OK")
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _log(f"Failed: {e}", "WARN")
        if dest.is_file():
            try:
                dest.unlink()
            except OSError:
                pass
        return False


def _verify_sha256(path: Path, expected: str) -> bool:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    got = h.hexdigest()
    if got.lower() != expected.lower():
        _log(
            f"Checksum mismatch (got {got[:12]}... expected {expected[:12]}...)",
            "ERROR",
        )
        return False
    return True


def _build_programmatic(dest: Path) -> bool:
    """Build a minimal SOMA-compatible skeleton FBX using fbxsdkpy.

    This is the SAFE fallback — no license issues because we're just
    writing a mathematically-defined T-pose skeleton, not redistributing
    any third-party asset.
    """
    try:
        # INRIA pypi package name `fbxsdkpy` but import name is `fbx`.
        import fbx
    except ImportError:
        _log(
            "fbxsdkpy not installed. Either:\n"
            "  - Run the installer first, OR\n"
            "  - Supply a T-pose FBX manually at {dest}".format(dest=dest),
            "ERROR",
        )
        return False

    # SOMA-24 joint spec (simplified — approximation of the SOMA topology
    # published in the Kimodo tech report). Coordinates are in centimeters.
    # Hierarchy: name -> (parent, (x,y,z) local offset from parent)
    spec: list[tuple[str, str | None, tuple[float, float, float]]] = [
        ("Hips", None, (0.0, 90.0, 0.0)),
        ("Spine", "Hips", (0.0, 10.0, 0.0)),
        ("Spine1", "Spine", (0.0, 15.0, 0.0)),
        ("Spine2", "Spine1", (0.0, 15.0, 0.0)),
        ("Neck", "Spine2", (0.0, 10.0, 0.0)),
        ("Head", "Neck", (0.0, 10.0, 0.0)),
        ("LeftShoulder", "Spine2", (5.0, 5.0, 0.0)),
        ("LeftArm", "LeftShoulder", (10.0, 0.0, 0.0)),
        ("LeftForeArm", "LeftArm", (25.0, 0.0, 0.0)),
        ("LeftHand", "LeftForeArm", (25.0, 0.0, 0.0)),
        ("RightShoulder", "Spine2", (-5.0, 5.0, 0.0)),
        ("RightArm", "RightShoulder", (-10.0, 0.0, 0.0)),
        ("RightForeArm", "RightArm", (-25.0, 0.0, 0.0)),
        ("RightHand", "RightForeArm", (-25.0, 0.0, 0.0)),
        ("LeftUpLeg", "Hips", (10.0, -10.0, 0.0)),
        ("LeftLeg", "LeftUpLeg", (0.0, -40.0, 0.0)),
        ("LeftFoot", "LeftLeg", (0.0, -40.0, 5.0)),
        ("LeftToeBase", "LeftFoot", (0.0, -5.0, 10.0)),
        ("RightUpLeg", "Hips", (-10.0, -10.0, 0.0)),
        ("RightLeg", "RightUpLeg", (0.0, -40.0, 0.0)),
        ("RightFoot", "RightLeg", (0.0, -40.0, 5.0)),
        ("RightToeBase", "RightFoot", (0.0, -5.0, 10.0)),
    ]

    try:
        manager = fbx.FbxManager.Create()
        scene = fbx.FbxScene.Create(manager, "default_tpose")

        nodes: dict[str, object] = {}
        for name, parent, offset in spec:
            skel_attr = fbx.FbxSkeleton.Create(manager, f"{name}_attr")
            # Root = root, else = limb node
            if parent is None:
                skel_attr.SetSkeletonType(fbx.FbxSkeleton.EType.eRoot)
            else:
                skel_attr.SetSkeletonType(fbx.FbxSkeleton.EType.eLimbNode)
            node = fbx.FbxNode.Create(manager, name)
            node.SetNodeAttribute(skel_attr)
            node.LclTranslation.Set(fbx.FbxDouble3(*offset))
            nodes[name] = node
            if parent is None:
                scene.GetRootNode().AddChild(node)
            else:
                nodes[parent].AddChild(node)

        exporter = fbx.FbxExporter.Create(manager, "")
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok = exporter.Initialize(str(dest), -1, manager.GetIOSettings())
        if not ok:
            _log("FbxExporter.Initialize failed", "ERROR")
            return False
        exporter.Export(scene)
        exporter.Destroy()
        manager.Destroy()
        _log(f"Wrote programmatic T-pose to {dest}", "OK")
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"Programmatic build failed: {type(e).__name__}: {e}", "ERROR")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--force-build",
        action="store_true",
        help="Skip URL list, always build programmatically",
    )
    ap.add_argument(
        "--verify", action="store_true", help="Just check status, don't modify anything"
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_FBX,
        help=f"Output path (default {OUTPUT_FBX})",
    )
    args = ap.parse_args()

    out: Path = args.output

    if args.verify:
        if out.is_file():
            size = out.stat().st_size
            _log(f"Template present: {out}  ({size / 1024:.1f} KB)", "OK")
            return 0
        _log(f"Template missing: {out}", "WARN")
        return 1

    if out.is_file() and out.stat().st_size > 1024:
        _log(f"Template already exists at {out} — reuse", "OK")
        return 0

    if not args.force_build:
        for name, url, lic, sha in TEMPLATE_URLS:
            if _try_download(name, url, out):
                if sha and not _verify_sha256(out, sha):
                    out.unlink(missing_ok=True)
                    continue
                _log(f"Template: {name} (license {lic})", "OK")
                return 0
        if TEMPLATE_URLS:
            _log("All curated URLs failed — falling back to programmatic build", "WARN")

    if _build_programmatic(out):
        return 0
    _log(
        "Unable to produce template. Manually place a T-pose FBX at:\n" f"   {out}",
        "ERROR",
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
