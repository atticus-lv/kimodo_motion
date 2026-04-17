"""Subprocess entry point — runs inside kimodo_venv Python 3.12.

Called by fbx_bridge.run_fbx_retarget(). Receives NPZ + target FBX + mapping JSON,
writes retargeted FBX to output path. Stdout/stderr are captured by the caller.

Usage:
    python fbx_runner.py \
        --npz PATH \
        --target-fbx PATH \
        --out PATH \
        --mapping-json PATH \
        [--sample 0] [--yaw 0.0] [--force-scale 0.0]
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import types
import traceback


def main() -> int:
    parser = argparse.ArgumentParser(description="Kimodo FBX retarget runner")
    parser.add_argument("--npz", required=True, help="NPZ file from server /generate")
    parser.add_argument(
        "--target-fbx", required=True, help="Target armature FBX (T-pose template)"
    )
    parser.add_argument("--out", required=True, help="Output animated FBX path")
    parser.add_argument(
        "--mapping-json", required=True, help="JSON file {soma_name: target_name}"
    )
    parser.add_argument(
        "--sample", type=int, default=0, help="Which batch sample to use"
    )
    parser.add_argument("--yaw", type=float, default=0.0, help="Yaw offset (degrees)")
    parser.add_argument(
        "--force-scale", type=float, default=0.0, help="Force scale (0=auto)"
    )
    args = parser.parse_args()

    # Locate vendored kimodo_retarget_fbx.py (sibling to retarget/ dir)
    here = os.path.dirname(os.path.abspath(__file__))
    addon_root = os.path.dirname(here)
    vendor_dir = os.path.join(addon_root, "vendor", "kimodo_retarget")
    if not os.path.isdir(vendor_dir):
        print(f"[runner] ERROR: vendor dir missing: {vendor_dir}", file=sys.stderr)
        return 2
    sys.path.insert(0, vendor_dir)

    try:
        import numpy as np
        from kimodo_retarget_fbx import export_kimodo_fbx, HAS_FBX_SDK
    except ImportError as e:
        print(f"[runner] ERROR: import failed: {e}", file=sys.stderr)
        print(
            "[runner] Ensure kimodo_venv has numpy + scipy + fbxsdkpy installed.",
            file=sys.stderr,
        )
        return 3

    if not HAS_FBX_SDK:
        print("[runner] ERROR: fbxsdkpy not loaded. Install with:", file=sys.stderr)
        print(
            "  pip install fbxsdkpy==2020.3.7.post1 --extra-index-url "
            "https://gitlab.inria.fr/api/v4/projects/18692/packages/pypi/simple",
            file=sys.stderr,
        )
        return 4

    # Load NPZ
    try:
        npz = np.load(args.npz, allow_pickle=True)
    except Exception as e:
        print(f"[runner] ERROR loading NPZ {args.npz}: {e}", file=sys.stderr)
        return 5

    required = ("posed_joints", "global_rot_mats", "joint_parents", "joint_names")
    missing = [k for k in required if k not in npz.files]
    if missing:
        print(f"[runner] ERROR: NPZ missing keys: {missing}", file=sys.stderr)
        print(f"[runner] Available keys: {npz.files}", file=sys.stderr)
        return 6

    posed_joints = npz["posed_joints"]  # expect [B, T, J, 3] or [T, J, 3]
    global_rot_mats = npz["global_rot_mats"]  # expect [B, T, J, 3, 3] or [T, J, 3, 3]

    # Ensure batch dimension
    if posed_joints.ndim == 3:
        posed_joints = posed_joints[None, ...]
    if global_rot_mats.ndim == 4:
        global_rot_mats = global_rot_mats[None, ...]

    output_dict = {
        "posed_joints": posed_joints,
        "global_rot_mats": global_rot_mats,
    }

    joint_parents = list(npz["joint_parents"].tolist())
    joint_names = [str(n) for n in npz["joint_names"]]
    fps = float(npz["fps"]) if "fps" in npz.files else 30.0
    neutral_joints = npz["neutral_joints"] if "neutral_joints" in npz.files else None
    skeleton_name = (
        str(npz["skeleton_name"]) if "skeleton_name" in npz.files else "soma"
    )

    # Load bone mapping JSON
    try:
        with open(args.mapping_json, "r", encoding="utf-8") as fh:
            mapping_raw = json.load(fh)
    except Exception as e:
        print(
            f"[runner] ERROR loading mapping JSON {args.mapping_json}: {e}",
            file=sys.stderr,
        )
        return 7

    # mapping JSON may have a "_comment" key — filter it
    bone_mapping = {k: v for k, v in mapping_raw.items() if not k.startswith("_")}
    if not bone_mapping:
        print(f"[runner] ERROR: mapping JSON is empty", file=sys.stderr)
        return 8

    # Normalize mapping keys/values to lowercase (matches skeleton get_bone lookup)
    bone_mapping = {k.lower(): v.lower() for k, v in bone_mapping.items()}

    motion_data = types.SimpleNamespace(
        output_dict=output_dict,
        model_name="kimodo-soma",
        skeleton_name=skeleton_name,
        fps=fps,
        texts=[""],
        num_frames=[posed_joints.shape[1]],
        num_samples=posed_joints.shape[0],
        batch_size=int(posed_joints.shape[0]),
        joint_parents=joint_parents,
        joint_names=joint_names,
        neutral_joints=neutral_joints,
        skeleton=None,
        constraint_lst=[],
    )

    print(
        f"[runner] NPZ loaded: T={posed_joints.shape[1]} J={posed_joints.shape[2]} B={posed_joints.shape[0]} fps={fps}"
    )
    print(f"[runner] Mapping: {len(bone_mapping)} pairs")
    print(f"[runner] Sample index: {args.sample}")

    try:
        out = export_kimodo_fbx(
            motion_data=motion_data,
            target_fbx_path=args.target_fbx,
            output_path=args.out,
            sample_index=args.sample,
            yaw_offset=args.yaw,
            force_scale=args.force_scale,
            bone_mapping=bone_mapping,
        )
    except Exception as e:
        print(f"[runner] ERROR in export_kimodo_fbx: {e}", file=sys.stderr)
        traceback.print_exc()
        return 9

    print(f"[runner] DONE: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
