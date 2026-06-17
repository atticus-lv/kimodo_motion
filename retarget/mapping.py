"""Bone name mapping presets: SOMA -> target skeleton conventions."""

import json
import os

# SOMA 77 joint hierarchy — verified from Kimodo source (2026-04-09)
# Key body joints used for retarget (excluding finger End joints and face)
SOMA_BODY_JOINTS = [
    "Hips",  # Root / Pelvis
    "Spine1",  # Lower spine
    "Spine2",  # Mid spine
    "Chest",  # Upper spine / chest
    "Neck1",  # Lower neck
    "Neck2",  # Upper neck
    "Head",  # Head
    "LeftShoulder",
    "LeftArm",
    "LeftForeArm",
    "LeftHand",
    "RightShoulder",
    "RightArm",
    "RightForeArm",
    "RightHand",
    "LeftLeg",  # Upper leg (NOT "LeftUpLeg")
    "LeftShin",  # Lower leg (NOT "LeftLeg")
    "LeftFoot",
    "LeftToeBase",
    "RightLeg",  # Upper leg
    "RightShin",  # Lower leg
    "RightFoot",
    "RightToeBase",
]

# Full SOMA 77 hierarchy: (joint_name, parent_name)
SOMA77_HIERARCHY = [
    ("Hips", None),
    ("Spine1", "Hips"),
    ("Spine2", "Spine1"),
    ("Chest", "Spine2"),
    ("Neck1", "Chest"),
    ("Neck2", "Neck1"),
    ("Head", "Neck2"),
    ("HeadEnd", "Head"),
    ("Jaw", "Head"),
    ("LeftEye", "Head"),
    ("RightEye", "Head"),
    ("LeftShoulder", "Chest"),
    ("LeftArm", "LeftShoulder"),
    ("LeftForeArm", "LeftArm"),
    ("LeftHand", "LeftForeArm"),
    ("LeftHandThumb1", "LeftHand"),
    ("LeftHandThumb2", "LeftHandThumb1"),
    ("LeftHandThumb3", "LeftHandThumb2"),
    ("LeftHandThumbEnd", "LeftHandThumb3"),
    ("LeftHandIndex1", "LeftHand"),
    ("LeftHandIndex2", "LeftHandIndex1"),
    ("LeftHandIndex3", "LeftHandIndex2"),
    ("LeftHandIndex4", "LeftHandIndex3"),
    ("LeftHandIndexEnd", "LeftHandIndex4"),
    ("LeftHandMiddle1", "LeftHand"),
    ("LeftHandMiddle2", "LeftHandMiddle1"),
    ("LeftHandMiddle3", "LeftHandMiddle2"),
    ("LeftHandMiddle4", "LeftHandMiddle3"),
    ("LeftHandMiddleEnd", "LeftHandMiddle4"),
    ("LeftHandRing1", "LeftHand"),
    ("LeftHandRing2", "LeftHandRing1"),
    ("LeftHandRing3", "LeftHandRing2"),
    ("LeftHandRing4", "LeftHandRing3"),
    ("LeftHandRingEnd", "LeftHandRing4"),
    ("LeftHandPinky1", "LeftHand"),
    ("LeftHandPinky2", "LeftHandPinky1"),
    ("LeftHandPinky3", "LeftHandPinky2"),
    ("LeftHandPinky4", "LeftHandPinky3"),
    ("LeftHandPinkyEnd", "LeftHandPinky4"),
    ("RightShoulder", "Chest"),
    ("RightArm", "RightShoulder"),
    ("RightForeArm", "RightArm"),
    ("RightHand", "RightForeArm"),
    ("RightHandThumb1", "RightHand"),
    ("RightHandThumb2", "RightHandThumb1"),
    ("RightHandThumb3", "RightHandThumb2"),
    ("RightHandThumbEnd", "RightHandThumb3"),
    ("RightHandIndex1", "RightHand"),
    ("RightHandIndex2", "RightHandIndex1"),
    ("RightHandIndex3", "RightHandIndex2"),
    ("RightHandIndex4", "RightHandIndex3"),
    ("RightHandIndexEnd", "RightHandIndex4"),
    ("RightHandMiddle1", "RightHand"),
    ("RightHandMiddle2", "RightHandMiddle1"),
    ("RightHandMiddle3", "RightHandMiddle2"),
    ("RightHandMiddle4", "RightHandMiddle3"),
    ("RightHandMiddleEnd", "RightHandMiddle4"),
    ("RightHandRing1", "RightHand"),
    ("RightHandRing2", "RightHandRing1"),
    ("RightHandRing3", "RightHandRing2"),
    ("RightHandRing4", "RightHandRing3"),
    ("RightHandRingEnd", "RightHandRing4"),
    ("RightHandPinky1", "RightHand"),
    ("RightHandPinky2", "RightHandPinky1"),
    ("RightHandPinky3", "RightHandPinky2"),
    ("RightHandPinky4", "RightHandPinky3"),
    ("RightHandPinkyEnd", "RightHandPinky4"),
    ("LeftLeg", "Hips"),
    ("LeftShin", "LeftLeg"),
    ("LeftFoot", "LeftShin"),
    ("LeftToeBase", "LeftFoot"),
    ("LeftToeEnd", "LeftToeBase"),
    ("RightLeg", "Hips"),
    ("RightShin", "RightLeg"),
    ("RightFoot", "RightShin"),
    ("RightToeBase", "RightFoot"),
    ("RightToeEnd", "RightToeBase"),
]

# Root bone name used for root translation scaling
SOMA_ROOT_BONE = "Hips"

# Leg bones for computing scale ratio
SOMA_LEFT_LEG_CHAIN = ["LeftLeg", "LeftShin", "LeftFoot"]


def get_presets_dir() -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets"
    )


def load_preset(name: str) -> dict:
    """Load a bone mapping preset JSON file.

    Returns dict of {soma_bone_name: target_bone_name}.
    """
    path = os.path.join(get_presets_dir(), f"{name}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Preset not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_preset(name: str, mapping: dict):
    """Save a bone mapping as a preset JSON file."""
    presets_dir = get_presets_dir()
    os.makedirs(presets_dir, exist_ok=True)
    path = os.path.join(presets_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)


def auto_match_bones(source_bone_names: list, target_bone_names: list) -> dict:
    """Auto-match bones by name (case-insensitive), with prefix stripping.

    Handles prefixes on both sides (e.g., "mixamorig:" on target).
    Returns {source_name: target_name} for matched pairs.
    """
    # Build two lookup tables for target: full name and stripped name
    target_lower = {n.lower(): n for n in target_bone_names}
    target_stripped = {}
    for n in target_bone_names:
        stripped = n.split(":")[-1].lower() if ":" in n else None
        if stripped:
            target_stripped[stripped] = n

    result = {}
    for src in source_bone_names:
        key = src.lower()
        if key in target_lower:
            # Exact match
            result[src] = target_lower[key]
        elif key in target_stripped:
            # Source "Hips" matches target "mixamorig:Hips"
            result[src] = target_stripped[key]
        else:
            # Try stripping source prefix
            src_stripped = src.split(":")[-1].lower() if ":" in src else None
            if src_stripped and src_stripped in target_lower:
                result[src] = target_lower[src_stripped]
            elif src_stripped and src_stripped in target_stripped:
                result[src] = target_stripped[src_stripped]
    return result
