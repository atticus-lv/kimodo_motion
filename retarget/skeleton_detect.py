"""Target-rig helpers used by the generate operator: preset auto-detection and
a Blender-safe name sanitizer. Kept separate from the legacy FBX-SDK retarget
path (fbx_bridge / fbx_runner / vendor) so that path can be excluded from the
shipped extension.
"""
from __future__ import annotations

from typing import Optional

import bpy


def sanitize_name(s: str, max_len: int = 40) -> str:
    """Make a string safe to use as a Blender data-block name."""
    result = []
    for ch in s:
        if ch.isalnum() or ch in "_-":
            result.append(ch)
        elif ch == " ":
            result.append("_")
    out = "".join(result)[:max_len].strip("_")
    return out or "motion"


def detect_skeleton_preset(arm_obj: bpy.types.Object) -> Optional[str]:
    """Return 'mixamo' | 'vroid' | 'mmd' | None based on bone naming."""
    names_lower = [b.name.lower() for b in arm_obj.data.bones]
    joined = " ".join(names_lower)

    if "mixamorig:" in joined:
        return "mixamo"
    if "j_bip_" in joined or "vroid_" in joined:
        return "vroid"
    # MMD uses Japanese bone names; check a few distinctive ones.
    for b_name in names_lower:
        for kw in ("全ての親", "上半身", "下半身", "センター", "左足", "右足"):
            if kw in b_name:
                return "mmd"
    return None
