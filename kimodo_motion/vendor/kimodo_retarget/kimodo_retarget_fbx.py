"""
Kimodo FBX Retarget Module

Retargets Kimodo SOMA skeleton motion onto Mixamo-rigged FBX characters.
Self-contained — no dependency on HY-Motion.
"""

from __future__ import annotations

import os
import sys
import shutil
import traceback
import numpy as np
from scipy.spatial.transform import Rotation as R

_TAG = "[Kimodo FBX]"


def _log(msg: str):
    print(f"{_TAG} {msg}", flush=True)


# FBX SDK (optional)
HAS_FBX_SDK = False
try:
    import fbx
    from fbx import (
        FbxManager,
        FbxScene,
        FbxImporter,
        FbxExporter,
        FbxIOSettings,
        FbxAnimStack,
        FbxAnimLayer,
        FbxTime,
        FbxSurfaceMaterial,
    )

    HAS_FBX_SDK = True
    _log("FBX SDK loaded OK")
except ImportError as e:
    _log(f"FBX SDK not available: {e}")
except Exception as e:
    _log(f"FBX SDK import error: {e}")

# These constants may not exist in all fbxsdkpy versions
_EXP_FBX_EMBEDDED = None
_EXP_FBX_MATERIAL = None
_EXP_FBX_TEXTURE = None
if HAS_FBX_SDK:
    try:
        from fbx import EXP_FBX_EMBEDDED, EXP_FBX_MATERIAL, EXP_FBX_TEXTURE

        _EXP_FBX_EMBEDDED = EXP_FBX_EMBEDDED
        _EXP_FBX_MATERIAL = EXP_FBX_MATERIAL
        _EXP_FBX_TEXTURE = EXP_FBX_TEXTURE
    except ImportError:
        _log("Warning: EXP_FBX_EMBEDDED/MATERIAL/TEXTURE constants not available")


# ============================================================================
# Math Utilities
# ============================================================================


def _fbx_mat_to_np(fbx_mat) -> np.ndarray:
    m = np.zeros((4, 4))
    for i in range(4):
        for j in range(4):
            m[i, j] = fbx_mat.Get(i, j)
    return m


def _mat_to_quat(mat: np.ndarray) -> np.ndarray:
    """4x4 or 3x3 matrix → quaternion [w,x,y,z].
    FBX row-major (v*M) → transpose for SciPy (M@v)."""
    m33 = mat[:3, :3].T
    q = R.from_matrix(m33).as_quat()  # [x,y,z,w]
    return np.array([q[3], q[0], q[1], q[2]])


def _quat_inv(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]]) / np.sum(q**2)


def _quat_mul(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ]
    )


# ============================================================================
# Data Structures
# ============================================================================


class BoneData:
    __slots__ = (
        "name",
        "parent_name",
        "local_matrix",
        "world_matrix",
        "head",
        "has_skeleton_attr",
        "rest_rotation",
        "animation",
        "world_animation",
        "location_animation",
        "world_location_animation",
    )

    def __init__(self, name: str):
        self.name = name
        self.parent_name = None
        self.local_matrix = np.eye(4)
        self.world_matrix = np.eye(4)
        self.head = np.zeros(3)
        self.has_skeleton_attr = False
        self.rest_rotation = np.array([1.0, 0.0, 0.0, 0.0])
        self.animation = {}
        self.world_animation = {}
        self.location_animation = {}
        self.world_location_animation = {}


class SkeletonData:
    def __init__(self, name: str = "Skeleton"):
        self.name = name
        self.bones: dict[str, BoneData] = {}
        self.all_nodes: dict[str, str] = {}
        self.node_rest_rotations: dict[str, np.ndarray] = {}
        self.fps = 30.0
        self.frame_start = 0
        self.frame_end = 0

    def add_bone(self, bone: BoneData):
        self.bones[bone.name.lower()] = bone

    def get_bone(self, name: str):
        lo = name.lower()
        if lo in self.bones:
            return self.bones[lo]
        # strip prefix
        if ":" in lo:
            stripped = lo.split(":")[-1]
            if stripped in self.bones:
                return self.bones[stripped]
        for bname, bone in self.bones.items():
            if ":" in bname and bname.split(":")[-1] == lo:
                return bone
        return None


# ============================================================================
# SOMA → Mixamo Bone Mapping
# ============================================================================

SOMA_TO_MIXAMO = {
    # Root & Spine
    "hips": "mixamorig:hips",
    "spine1": "mixamorig:spine",
    "spine2": "mixamorig:spine1",
    "chest": "mixamorig:spine2",
    # Neck & Head
    "neck1": "mixamorig:neck",
    "head": "mixamorig:head",
    # Left Arm
    "leftshoulder": "mixamorig:leftshoulder",
    "leftarm": "mixamorig:leftarm",
    "leftforearm": "mixamorig:leftforearm",
    "lefthand": "mixamorig:lefthand",
    # Right Arm
    "rightshoulder": "mixamorig:rightshoulder",
    "rightarm": "mixamorig:rightarm",
    "rightforearm": "mixamorig:rightforearm",
    "righthand": "mixamorig:righthand",
    # Left Leg
    "leftleg": "mixamorig:leftupleg",
    "leftshin": "mixamorig:leftleg",
    "leftfoot": "mixamorig:leftfoot",
    "lefttoebase": "mixamorig:lefttoebase",
    # Right Leg
    "rightleg": "mixamorig:rightupleg",
    "rightshin": "mixamorig:rightleg",
    "rightfoot": "mixamorig:rightfoot",
    "righttoebase": "mixamorig:righttoebase",
    # ---- 77-joint fingers (Left) ----
    "lefthandthumb1": "mixamorig:lefthandthumb1",
    "lefthandthumb2": "mixamorig:lefthandthumb2",
    "lefthandthumb3": "mixamorig:lefthandthumb3",
    "lefthandindex1": "mixamorig:lefthandindex1",
    "lefthandindex2": "mixamorig:lefthandindex2",
    "lefthandindex3": "mixamorig:lefthandindex3",
    "lefthandindex4": "mixamorig:lefthandindex4",
    "lefthandmiddle1": "mixamorig:lefthandmiddle1",
    "lefthandmiddle2": "mixamorig:lefthandmiddle2",
    "lefthandmiddle3": "mixamorig:lefthandmiddle3",
    "lefthandmiddle4": "mixamorig:lefthandmiddle4",
    "lefthandring1": "mixamorig:lefthandring1",
    "lefthandring2": "mixamorig:lefthandring2",
    "lefthandring3": "mixamorig:lefthandring3",
    "lefthandring4": "mixamorig:lefthandring4",
    "lefthandpinky1": "mixamorig:lefthandpinky1",
    "lefthandpinky2": "mixamorig:lefthandpinky2",
    "lefthandpinky3": "mixamorig:lefthandpinky3",
    "lefthandpinky4": "mixamorig:lefthandpinky4",
    # ---- 77-joint fingers (Right) ----
    "righthandthumb1": "mixamorig:righthandthumb1",
    "righthandthumb2": "mixamorig:righthandthumb2",
    "righthandthumb3": "mixamorig:righthandthumb3",
    "righthandindex1": "mixamorig:righthandindex1",
    "righthandindex2": "mixamorig:righthandindex2",
    "righthandindex3": "mixamorig:righthandindex3",
    "righthandindex4": "mixamorig:righthandindex4",
    "righthandmiddle1": "mixamorig:righthandmiddle1",
    "righthandmiddle2": "mixamorig:righthandmiddle2",
    "righthandmiddle3": "mixamorig:righthandmiddle3",
    "righthandmiddle4": "mixamorig:righthandmiddle4",
    "righthandring1": "mixamorig:righthandring1",
    "righthandring2": "mixamorig:righthandring2",
    "righthandring3": "mixamorig:righthandring3",
    "righthandring4": "mixamorig:righthandring4",
    "righthandpinky1": "mixamorig:righthandpinky1",
    "righthandpinky2": "mixamorig:righthandpinky2",
    "righthandpinky3": "mixamorig:righthandpinky3",
    "righthandpinky4": "mixamorig:righthandpinky4",
}


# ============================================================================
# Kimodo Motion → Source Skeleton
# ============================================================================


def kimodo_to_source_skeleton(motion_data, sample_index: int = 0) -> SkeletonData:
    """Convert Kimodo motion output into a SkeletonData with per-frame world animation.

    IMPORTANT: SOMA's standard T-pose has identity global rotations for all joints.
    We use identity as rest_rotation so the retarget offset correctly maps
    T-pose↔T-pose between source and target.
    """
    _log("--- Building source skeleton from Kimodo motion ---")

    output = motion_data.output_dict
    joint_names = motion_data.joint_names
    joint_parents = motion_data.joint_parents

    _log(f"  joint_names count: {len(joint_names)}")
    _log(f"  joint_parents count: {len(joint_parents)}")
    _log(f"  output keys: {list(output.keys())}")

    for key in ["posed_joints", "global_rot_mats"]:
        if key in output:
            _log(f"  {key} shape: {output[key].shape}, dtype: {output[key].dtype}")
        else:
            _log(f"  WARNING: '{key}' not found in output!")

    posed_joints = output["posed_joints"][sample_index]  # [T, J, 3]
    global_rot_mats = output["global_rot_mats"][sample_index]  # [T, J, 3, 3]
    T, J = posed_joints.shape[:2]
    _log(f"  sample_index={sample_index}, T={T} frames, J={J} joints")

    # Use T-pose (neutral_joints) as rest positions if available, else frame 0
    neutral_joints = getattr(motion_data, "neutral_joints", None)
    if neutral_joints is not None and neutral_joints.shape[0] == J:
        rest_pos = neutral_joints  # [J, 3] — actual T-pose positions
        _log(f"  Using neutral_joints as rest pose (T-pose)")
    else:
        rest_pos = posed_joints[0]
        _log(f"  WARNING: neutral_joints not available, using frame 0 as rest pose")

    skel = SkeletonData("kimodo_soma")
    skel.fps = float(motion_data.fps)
    skel.frame_start = 0
    skel.frame_end = T - 1
    _log(f"  fps={skel.fps}, frame_range=[{skel.frame_start}, {skel.frame_end}]")

    # SOMA standard T-pose: all global rotations = identity
    identity_q = np.array([1.0, 0.0, 0.0, 0.0])

    for i, name in enumerate(joint_names):
        bone = BoneData(name)
        pidx = joint_parents[i]
        bone.parent_name = joint_names[pidx] if pidx >= 0 else None

        # Rest rotation = identity (T-pose in SOMA standard frame)
        bone.rest_rotation = identity_q.copy()

        bone.head = rest_pos[i].copy()
        # World matrix at rest = identity rotation + T-pose position
        bone.world_matrix = np.eye(4)
        bone.world_matrix[3, :3] = rest_pos[i]

        # Per-frame world animation (actual poses)
        for f in range(T):
            qf = R.from_matrix(global_rot_mats[f, i]).as_quat()
            bone.world_animation[f] = np.array([qf[3], qf[0], qf[1], qf[2]])
            bone.world_location_animation[f] = posed_joints[f, i].copy()

        skel.add_bone(bone)
        skel.all_nodes[name] = name
        skel.node_rest_rotations[name] = bone.rest_rotation

    _log(f"  Source skeleton built: {len(skel.bones)} bones")
    _log(f"  Source bone names: {[b.name for b in skel.bones.values()]}")
    _log(f"  Rest rotation: identity (T-pose)")

    for i, name in enumerate(joint_names[:5]):
        b = skel.get_bone(name)
        if b:
            _log(
                f"    [{i}] {name}: T-pose head=({b.head[0]:.4f}, {b.head[1]:.4f}, {b.head[2]:.4f})"
            )

    return skel


# ============================================================================
# Load Target FBX Skeleton
# ============================================================================


def _collect_skeleton_nodes(
    node,
    skeleton: SkeletonData,
    parent_name=None,
    depth: int = 0,
    sampling_time=None,
):
    """Recursively collect bone nodes from an FBX scene."""
    attr = node.GetNodeAttribute()
    node_name = node.GetName()
    is_bone = False

    if attr:
        atype = attr.GetAttributeType()
        if atype in [3, 4]:  # Skeleton / LimbNode
            is_bone = True
        elif atype == 2 and (node.GetChildCount() > 0 or parent_name):
            is_bone = True

    kw = [
        "hips",
        "hip",
        "spine",
        "neck",
        "head",
        "arm",
        "leg",
        "foot",
        "ankle",
        "knee",
        "shoulder",
        "elbow",
        "pelvis",
        "joint",
        "mixamo",
        "thigh",
        "forearm",
        "hand",
        "finger",
        "clavicle",
        "collar",
        "toe",
        "thumb",
        "index",
        "middle",
        "ring",
        "pinky",
        "upleg",
        "wrist",
        "chest",
    ]
    if any(k in node_name.lower() for k in kw):
        is_bone = True

    t_eval = sampling_time if sampling_time else FbxTime()
    global_mat = _fbx_mat_to_np(node.EvaluateGlobalTransform(t_eval))
    skeleton.node_rest_rotations[node_name] = _mat_to_quat(global_mat)

    # BindPose
    scene = node.GetScene()
    if scene:
        for i in range(scene.GetPoseCount()):
            pose = scene.GetPose(i)
            if pose and pose.IsBindPose():
                idx = pose.Find(node)
                if idx != -1:
                    bp = _fbx_mat_to_np(pose.GetMatrix(idx))
                    skeleton.node_rest_rotations[node_name] = _mat_to_quat(bp)
                    break

    if is_bone:
        existing = skeleton.get_bone(node_name)
        is_real = attr and attr.GetAttributeType() in [3, 4]
        if existing:
            if is_real and not existing.has_skeleton_attr:
                skeleton.bones.pop(existing.name.lower(), None)
            else:
                is_bone = False

    if is_bone:
        bone = BoneData(node_name)
        bone.has_skeleton_attr = bool(attr and attr.GetAttributeType() in [3, 4])
        bone.parent_name = parent_name
        local_mat = _fbx_mat_to_np(node.EvaluateLocalTransform(t_eval))
        bone.local_matrix = local_mat
        bone.world_matrix = global_mat

        t_g = node.EvaluateGlobalTransform(t_eval).GetT()
        bone.head = np.array([t_g[0], t_g[1], t_g[2]])
        bone.rest_rotation = skeleton.node_rest_rotations[node_name]

        # BindPose head override
        if scene:
            for i in range(scene.GetPoseCount()):
                pose = scene.GetPose(i)
                if pose and pose.IsBindPose():
                    idx = pose.Find(node)
                    if idx != -1:
                        bp = _fbx_mat_to_np(pose.GetMatrix(idx))
                        if np.linalg.norm(bp[3, :3]) > 1e-4:
                            bone.head = bp[3, :3]
                        break

        skeleton.add_bone(bone)
        parent_name = node_name

    skeleton.all_nodes[node_name] = node_name
    for i in range(node.GetChildCount()):
        _collect_skeleton_nodes(
            node.GetChild(i), skeleton, parent_name, depth + 1, sampling_time
        )


def load_target_fbx(filepath: str):
    """Load FBX and return (manager, scene, SkeletonData)."""
    _log(f"--- Loading target FBX ---")
    _log(f"  Path: {filepath}")
    _log(f"  Exists: {os.path.exists(filepath)}")
    _log(f"  Size: {os.path.getsize(filepath)} bytes")

    manager = FbxManager.Create()
    ios = FbxIOSettings.Create(manager, "IOSRoot")
    # Enable loading embedded media (textures/materials)
    try:
        ios.SetBoolProp("Import|AdvOptGrp|Fbx|Material", True)
        ios.SetBoolProp("Import|AdvOptGrp|Fbx|Texture", True)
        ios.SetBoolProp("Import|AdvOptGrp|Fbx|Model", True)
        ios.SetBoolProp("Import|AdvOptGrp|Fbx|Shape", True)
        ios.SetBoolProp("Import|AdvOptGrp|Fbx|Skin", True)
        _log("  Import IOSettings: materials/textures/skin enabled")
    except Exception as e:
        _log(f"  Warning: could not set import props: {e}")
    manager.SetIOSettings(ios)
    scene = FbxScene.Create(manager, "Scene")
    imp = FbxImporter.Create(manager, "")
    if not imp.Initialize(filepath, -1, manager.GetIOSettings()):
        err = imp.GetStatus().GetErrorString()
        _log(f"  ERROR: Cannot open FBX: {err}")
        raise RuntimeError(f"Cannot open FBX: {filepath} — {err}")
    _log("  FbxImporter initialized OK")

    if not imp.Import(scene):
        err = imp.GetStatus().GetErrorString()
        _log(f"  ERROR: Import failed: {err}")
        raise RuntimeError(f"FBX import failed: {err}")
    imp.Destroy()
    _log("  Scene imported OK")

    # Pose count
    pose_count = scene.GetPoseCount()
    _log(f"  Pose count: {pose_count}")
    for i in range(pose_count):
        pose = scene.GetPose(i)
        if pose:
            _log(
                f"    Pose[{i}]: name='{pose.GetName()}', isBindPose={pose.IsBindPose()}, nodeCount={pose.GetCount()}"
            )

    # Sample rest pose without animation
    stack = scene.GetCurrentAnimationStack()
    _log(f"  AnimStack: {stack.GetName() if stack else 'None'}")
    scene.SetCurrentAnimationStack(None)

    skel = SkeletonData(os.path.basename(filepath))
    _collect_skeleton_nodes(scene.GetRootNode(), skel)
    scene.SetCurrentAnimationStack(stack)

    _log(f"  Target skeleton built: {len(skel.bones)} bones")
    _log(f"  Target bone names: {[b.name for b in skel.bones.values()]}")
    for bname, bone in skel.bones.items():
        _log(
            f"    {bone.name:40s} parent={str(bone.parent_name):40s} head=({bone.head[0]:.2f}, {bone.head[1]:.2f}, {bone.head[2]:.2f})"
        )

    return manager, scene, skel


# ============================================================================
# Skeleton Height
# ============================================================================


def _skeleton_height(skel: SkeletonData) -> float:
    kw = [
        "hips",
        "spine",
        "neck",
        "head",
        "arm",
        "leg",
        "foot",
        "ankle",
        "knee",
        "shoulder",
        "elbow",
        "pelvis",
        "mixamo",
    ]
    y_min, y_max = 1e9, -1e9
    found = False
    for _, bone in skel.bones.items():
        if any(k in bone.name.lower() for k in kw):
            h = bone.head[1]
            if abs(h) < 1e-6:
                continue
            y_min, y_max = min(y_min, h), max(y_max, h)
            found = True
    return (y_max - y_min) if found and y_max > y_min else 1.0


# ============================================================================
# Retarget Animation
# ============================================================================


def retarget_animation(
    src: SkeletonData,
    tgt: SkeletonData,
    mapping: dict,
    force_scale: float = 0.0,
    yaw_offset: float = 0.0,
):
    _log("--- Retargeting animation ---")
    ret_rots = {}
    ret_locs = {}

    yaw_q_raw = R.from_euler("y", yaw_offset, degrees=True).as_quat()
    yaw_q = np.array([yaw_q_raw[3], yaw_q_raw[0], yaw_q_raw[1], yaw_q_raw[2]])

    # 1. Build active bone pairs
    active = []
    mapped_tgt = set()
    mapped_src = set()
    miss_src = []
    miss_tgt = []

    for s_key, t_key in mapping.items():
        s_bone = src.get_bone(s_key)
        t_bone = tgt.get_bone(t_key)
        if not s_bone:
            miss_src.append(s_key)
            continue
        if not t_bone:
            miss_tgt.append(t_key)
            continue
        if t_bone.name in mapped_tgt or s_bone.name in mapped_src:
            continue
        off = _quat_mul(_quat_inv(s_bone.rest_rotation), t_bone.rest_rotation)
        active.append((s_bone, t_bone, off))
        mapped_tgt.add(t_bone.name)
        mapped_src.add(s_bone.name)

    _log(f"  Matched bone pairs: {len(active)}")
    for s, t, _ in sorted(active, key=lambda x: x[1].name):
        _log(f"    {s.name:30s} → {t.name}")

    if miss_src:
        _log(
            f"  Source bones NOT found ({len(miss_src)}): {miss_src[:10]}{'...' if len(miss_src) > 10 else ''}"
        )
    if miss_tgt:
        _log(
            f"  Target bones NOT found ({len(miss_tgt)}): {miss_tgt[:10]}{'...' if len(miss_tgt) > 10 else ''}"
        )

    if len(active) == 0:
        _log("  ERROR: No bone pairs matched! Check skeleton naming.")
        _log(f"  Source bones available: {list(src.bones.keys())}")
        _log(f"  Target bones available: {list(tgt.bones.keys())}")
        return ret_rots, ret_locs

    # 2. Auto-scale
    src_h = _skeleton_height(src)
    tgt_h = _skeleton_height(tgt)
    scale = (
        force_scale if force_scale > 1e-4 else (tgt_h / src_h if src_h > 0.01 else 1.0)
    )
    _log(f"  Scale: {scale:.4f}  (src_h={src_h:.4f}, tgt_h={tgt_h:.4f})")

    frames = range(src.frame_start, src.frame_end + 1)
    _log(
        f"  Frame range: {src.frame_start} - {src.frame_end} ({src.frame_end - src.frame_start + 1} frames)"
    )

    # 3. World rotations
    tgt_world_anims = {}

    for s_bone, t_bone, off in active:
        tgt_world_anims[t_bone.name] = {}
        for f in frames:
            s_rot = s_bone.world_animation.get(f, s_bone.rest_rotation)
            t_rot = _quat_mul(s_rot, off)
            if yaw_offset != 0:
                t_rot = _quat_mul(yaw_q, t_rot)
            tgt_world_anims[t_bone.name][f] = t_rot

        is_root = "hips" in t_bone.name.lower() or "hips" in s_bone.name.lower()
        if is_root:
            _log(f"  Root bone detected: src={s_bone.name} → tgt={t_bone.name}")
            ret_locs[t_bone.name] = {}
            t_rest_world_pos = t_bone.world_matrix[3, :3]
            t_rest_loc = t_bone.local_matrix[3, :3]
            pname = t_bone.parent_name
            _log(
                f"    tgt rest world pos: ({t_rest_world_pos[0]:.4f}, {t_rest_world_pos[1]:.4f}, {t_rest_world_pos[2]:.4f})"
            )
            _log(
                f"    tgt rest local pos: ({t_rest_loc[0]:.4f}, {t_rest_loc[1]:.4f}, {t_rest_loc[2]:.4f})"
            )
            _log(f"    tgt parent: {pname}")

            # Root rest: use first animated frame (standing pose) instead of the
            # skeleton T-pose. Kimodo's SOMA T-pose puts Hips at origin (0,0,0),
            # so treating that as rest + applying `posed - rest` as displacement
            # injects ~1m (one hip-height) into the target, which has its Hips at
            # real standing height. Using f0 anchors the motion to the character's
            # own starting pose.
            _frames_with_anim = sorted(s_bone.world_location_animation.keys())
            if _frames_with_anim:
                s_rest_root_pos = np.asarray(
                    s_bone.world_location_animation[_frames_with_anim[0]]
                ).copy()
                _log(
                    f"    src rest (using f{_frames_with_anim[0]} as standing ref): "
                    f"({s_rest_root_pos[0]:.4f}, {s_rest_root_pos[1]:.4f}, {s_rest_root_pos[2]:.4f})"
                )
            else:
                s_rest_root_pos = s_bone.world_matrix[3, :3]
                _log("    src rest (T-pose fallback; no animation frames)")
            yaw_rot = None
            if yaw_offset != 0:
                yaw_rot = R.from_quat([yaw_q[1], yaw_q[2], yaw_q[3], yaw_q[0]])

            for f in frames:
                s_p = s_bone.world_location_animation.get(f, s_rest_root_pos)

                # Root translation should follow the source root joint trajectory
                # directly. Rotating a rest-pose offset around the root injects
                # pose-dependent wobble into every child.
                disp_scaled = (s_p - s_rest_root_pos) * scale

                if yaw_rot is not None:
                    disp_scaled = yaw_rot.apply(disp_scaled)

                prot = tgt_world_anims.get(pname, {}).get(f)
                if prot is None:
                    prot = tgt.node_rest_rotations.get(pname, np.array([1, 0, 0, 0]))
                    if yaw_offset != 0:
                        prot = _quat_mul(yaw_q, prot)
                p_rot_inv = R.from_quat([prot[1], prot[2], prot[3], prot[0]]).inv()
                local_disp = p_rot_inv.apply(disp_scaled)

                ret_locs[t_bone.name][f] = t_rest_loc + local_disp

            # Log first & last frame displacement
            f0 = src.frame_start
            fN = src.frame_end
            _log(
                f"    Root loc frame[{f0}]: ({ret_locs[t_bone.name][f0][0]:.4f}, {ret_locs[t_bone.name][f0][1]:.4f}, {ret_locs[t_bone.name][f0][2]:.4f})"
            )
            _log(
                f"    Root loc frame[{fN}]: ({ret_locs[t_bone.name][fN][0]:.4f}, {ret_locs[t_bone.name][fN][1]:.4f}, {ret_locs[t_bone.name][fN][2]:.4f})"
            )

    # 4. Local rotations from world
    for s_bone, t_bone, _ in active:
        ret_rots[t_bone.name] = {}
        pname = t_bone.parent_name
        for f in frames:
            prot = tgt_world_anims.get(pname, {}).get(f)
            if prot is None:
                prot = tgt.node_rest_rotations.get(pname, np.array([1, 0, 0, 0]))
                if yaw_offset != 0:
                    prot = _quat_mul(yaw_q, prot)
            l_rot = _quat_mul(_quat_inv(prot), tgt_world_anims[t_bone.name][f])
            ret_rots[t_bone.name][f] = l_rot

    _log(
        f"  Retarget complete: {len(ret_rots)} rotation channels, {len(ret_locs)} translation channels"
    )
    return ret_rots, ret_locs


# ============================================================================
# Apply Animation to FBX Scene
# ============================================================================


def _get_rotation_order(node) -> str:
    order = node.RotationOrder.Get()
    return {0: "xyz", 1: "xzy", 2: "yzx", 3: "yxz", 4: "zxy", 5: "zyx"}.get(
        order, "xyz"
    )


def apply_animation_to_scene(
    scene,
    tgt_skel: SkeletonData,
    ret_rots: dict,
    ret_locs: dict,
    frame_start: int,
    frame_end: int,
):
    _log("--- Applying animation to FBX scene ---")
    _log(f"  Rotation channels: {len(ret_rots)}")
    _log(f"  Translation channels: {len(ret_locs)}")

    tmode = scene.GetGlobalSettings().GetTimeMode()
    _log(f"  TimeMode: {tmode}")

    # Clear old anim stacks
    try:
        criteria = fbx.FbxCriteria.ObjectType(FbxAnimStack.ClassId)
        old_count = scene.GetSrcObjectCount(criteria)
        _log(f"  Clearing {old_count} old anim stacks")
        for i in range(old_count - 1, -1, -1):
            s = scene.GetSrcObject(criteria, i)
            scene.DisconnectSrcObject(s)
            s.Destroy()
    except Exception as e:
        _log(f"  Warning: Could not clear old anim stacks: {e}")

    stack = FbxAnimStack.Create(scene, "Take 001")
    layer = FbxAnimLayer.Create(scene, "BaseLayer")
    stack.AddMember(layer)
    scene.SetCurrentAnimationStack(stack)
    _log("  Created new AnimStack 'Take 001'")

    applied_rots = 0
    applied_locs = 0

    def _apply(node):
        nonlocal applied_rots, applied_locs
        name = node.GetName()

        if name in ret_rots:
            try:
                node.LclRotation.ModifyFlag(
                    fbx.FbxPropertyFlags.EFlags.eAnimatable, True
                )
                ord_str = _get_rotation_order(node)

                pv = node.PreRotation.Get()
                pq = R.from_euler("xyz", [pv[0], pv[1], pv[2]], degrees=True).as_quat()
                pre_inv = _quat_inv(np.array([pq[3], pq[0], pq[1], pq[2]]))

                post_v = node.PostRotation.Get()
                post_q = R.from_euler(
                    "xyz", [post_v[0], post_v[1], post_v[2]], degrees=True
                ).as_quat()
                post_inv = _quat_inv(
                    np.array([post_q[3], post_q[0], post_q[1], post_q[2]])
                )

                cx = node.LclRotation.GetCurve(layer, "X", True)
                cy = node.LclRotation.GetCurve(layer, "Y", True)
                cz = node.LclRotation.GetCurve(layer, "Z", True)
                cx.KeyModifyBegin()
                cy.KeyModifyBegin()
                cz.KeyModifyBegin()

                for f, q_local in ret_rots[name].items():
                    t = FbxTime()
                    t.SetFrame(f, tmode)
                    q_final = _quat_mul(pre_inv, _quat_mul(q_local, post_inv))
                    rot_q = R.from_quat(
                        [q_final[1], q_final[2], q_final[3], q_final[0]]
                    )
                    e = rot_q.as_euler(ord_str.lower(), degrees=True)

                    curve_map = {"x": cx, "y": cy, "z": cz}
                    for i_ax, ch in enumerate(ord_str.lower()):
                        c = curve_map[ch]
                        idx = c.KeyAdd(t)[0]
                        c.KeySetValue(idx, float(e[i_ax]))
                        c.KeySetInterpolation(
                            idx,
                            fbx.FbxAnimCurveDef.EInterpolationType.eInterpolationLinear,
                        )

                cx.KeyModifyEnd()
                cy.KeyModifyEnd()
                cz.KeyModifyEnd()
                applied_rots += 1
            except Exception as e:
                _log(f"  ERROR applying rotation to '{name}': {e}")
                traceback.print_exc()

        if name in ret_locs:
            try:
                node.LclTranslation.ModifyFlag(
                    fbx.FbxPropertyFlags.EFlags.eAnimatable, True
                )
                tx = node.LclTranslation.GetCurve(layer, "X", True)
                ty = node.LclTranslation.GetCurve(layer, "Y", True)
                tz = node.LclTranslation.GetCurve(layer, "Z", True)
                tx.KeyModifyBegin()
                ty.KeyModifyBegin()
                tz.KeyModifyBegin()

                for f, loc in ret_locs[name].items():
                    t = FbxTime()
                    t.SetFrame(f, tmode)
                    for c, val in zip([tx, ty, tz], loc):
                        idx = c.KeyAdd(t)[0]
                        c.KeySetValue(idx, float(val))
                        c.KeySetInterpolation(
                            idx,
                            fbx.FbxAnimCurveDef.EInterpolationType.eInterpolationLinear,
                        )

                tx.KeyModifyEnd()
                ty.KeyModifyEnd()
                tz.KeyModifyEnd()
                applied_locs += 1
            except Exception as e:
                _log(f"  ERROR applying translation to '{name}': {e}")
                traceback.print_exc()

        for i in range(node.GetChildCount()):
            _apply(node.GetChild(i))

    _apply(scene.GetRootNode())
    _log(
        f"  Applied: {applied_rots} rotation channels, {applied_locs} translation channels"
    )


# ============================================================================
# Copy Textures
# ============================================================================


def _copy_textures(scene, output_path: str):
    out_dir = os.path.dirname(os.path.abspath(output_path))
    base = os.path.splitext(os.path.basename(output_path))[0]
    tex_dir = os.path.join(out_dir, f"{base}_textures")
    count = 0

    try:
        for i in range(scene.GetMaterialCount()):
            mat = scene.GetMaterial(i)
            for prop_name in [
                FbxSurfaceMaterial.sDiffuse,
                FbxSurfaceMaterial.sNormalMap,
                FbxSurfaceMaterial.sSpecular,
                FbxSurfaceMaterial.sEmissive,
                FbxSurfaceMaterial.sBump,
                "DiffuseColor",
                "NormalMap",
            ]:
                prop = mat.FindProperty(prop_name)
                if prop.IsValid():
                    for j in range(prop.GetSrcObjectCount()):
                        tex = prop.GetSrcObject(j)
                        if tex and hasattr(tex, "GetFileName"):
                            orig = tex.GetFileName()
                            if orig and os.path.exists(orig):
                                os.makedirs(tex_dir, exist_ok=True)
                                fn = os.path.basename(orig)
                                dest = os.path.join(tex_dir, fn)
                                if not os.path.exists(dest):
                                    shutil.copy2(orig, dest)
                                    count += 1
                                rel = os.path.join(f"{base}_textures", fn)
                                tex.SetFileName(rel)
                                tex.SetRelativeFileName(rel)
    except Exception as e:
        _log(f"  Warning: texture copy issue: {e}")

    if count:
        _log(f"  Copied {count} texture(s)")
    else:
        _log("  No textures to copy")


# ============================================================================
# Save FBX
# ============================================================================


def _save_fbx(manager, scene, path: str):
    _log(f"--- Saving FBX ---")
    _log(f"  Output: {path}")

    ios = manager.GetIOSettings()
    if not ios:
        ios = FbxIOSettings.Create(manager, "IOSRoot")
        manager.SetIOSettings(ios)

    # Enable all export features: embed textures, keep materials, skin, shapes
    if _EXP_FBX_EMBEDDED is not None:
        try:
            ios.SetBoolProp(_EXP_FBX_EMBEDDED, True)
            ios.SetBoolProp(_EXP_FBX_MATERIAL, True)
            ios.SetBoolProp(_EXP_FBX_TEXTURE, True)
            _log("  Embedded export props set OK")
        except Exception as e:
            _log(f"  Warning: Could not set embedded props: {e}")
    # Also set string-based props as belt-and-suspenders
    try:
        ios.SetBoolProp("Export|AdvOptGrp|Fbx|Material", True)
        ios.SetBoolProp("Export|AdvOptGrp|Fbx|Texture", True)
        ios.SetBoolProp("Export|AdvOptGrp|Fbx|Model", True)
        ios.SetBoolProp("Export|AdvOptGrp|Fbx|Animation", True)
        ios.SetBoolProp("Export|AdvOptGrp|Fbx|Shape", True)
        ios.SetBoolProp("Export|AdvOptGrp|Fbx|Skin", True)
        _log("  String-based export props set OK")
    except Exception as e:
        _log(f"  Warning: Could not set string-based props: {e}")

    fmt = manager.GetIOPluginRegistry().GetNativeWriterFormat()
    _log(f"  Native writer format: {fmt}")

    exporter = FbxExporter.Create(manager, "")
    if not exporter.Initialize(path, fmt, ios):
        err = exporter.GetStatus().GetErrorString()
        _log(f"  ERROR: Exporter init failed: {err}")
        raise RuntimeError(f"FBX exporter init failed: {err}")

    _log("  Exporter initialized, writing...")
    if not exporter.Export(scene):
        err = exporter.GetStatus().GetErrorString()
        _log(f"  ERROR: Export failed: {err}")
        raise RuntimeError(f"FBX export failed: {err}")

    exporter.Destroy()

    if os.path.exists(path):
        size = os.path.getsize(path)
        _log(f"  SUCCESS: Saved {path} ({size} bytes)")
    else:
        _log(f"  WARNING: File not found after save: {path}")


# ============================================================================
# Public API
# ============================================================================


def export_kimodo_fbx(
    motion_data,
    target_fbx_path: str,
    output_path: str,
    sample_index: int = 0,
    yaw_offset: float = 0.0,
    force_scale: float = 0.0,
    bone_mapping: dict = None,
) -> str:
    """
    Export Kimodo SOMA motion to animated FBX via retargeting to a target character.

    bone_mapping: {soma_bone_name: target_bone_name} dict. If None, uses default
        SOMA_TO_MIXAMO mapping. Pass VRoid/MMD/custom mapping to retarget other rigs.

    Returns path to the saved FBX.
    """
    _log("=" * 60)
    _log("KIMODO FBX EXPORT START")
    _log("=" * 60)

    if not HAS_FBX_SDK:
        _log("ERROR: FBX SDK not available!")
        raise ImportError(
            "FBX SDK (fbx / fbxsdkpy) is required for FBX export. "
            "Install from: https://gitlab.inria.fr/mmuslam/fbxsdkpy"
        )

    _log(f"  sample_index: {sample_index}")
    _log(f"  target_fbx_path: {target_fbx_path}")
    _log(f"  output_path: {output_path}")
    _log(f"  yaw_offset: {yaw_offset}")
    _log(f"  force_scale: {force_scale}")
    _log(f"  motion skeleton: {motion_data.skeleton_name}")
    _log(f"  motion fps: {motion_data.fps}")
    _log(f"  motion batch_size: {motion_data.batch_size}")
    _log(f"  motion joint_names: {motion_data.joint_names}")

    try:
        # 1. Build source skeleton
        src_skel = kimodo_to_source_skeleton(motion_data, sample_index)

        # 2. Load target FBX
        manager, scene, tgt_skel = load_target_fbx(target_fbx_path)

        _log(f"  Source bones: {len(src_skel.bones)}")
        _log(f"  Target bones: {len(tgt_skel.bones)}")

        # 3. Retarget
        mapping_to_use = bone_mapping if bone_mapping is not None else SOMA_TO_MIXAMO
        _log(
            f"  Bone mapping entries: {len(mapping_to_use)} "
            f"({'custom' if bone_mapping else 'default SOMA_TO_MIXAMO'})"
        )
        ret_rots, ret_locs = retarget_animation(
            src_skel,
            tgt_skel,
            mapping_to_use,
            force_scale=force_scale,
            yaw_offset=yaw_offset,
        )

        if len(ret_rots) == 0:
            _log("WARNING: No bone pairs matched — FBX will have no animation!")

        # 4. Apply to FBX scene
        apply_animation_to_scene(
            scene,
            tgt_skel,
            ret_rots,
            ret_locs,
            src_skel.frame_start,
            src_skel.frame_end,
        )

        # 5. Save (textures/materials preserved from the loaded scene)
        mat_count = scene.GetMaterialCount()
        _log(f"  Materials in scene: {mat_count}")
        for i in range(mat_count):
            m = scene.GetMaterial(i)
            _log(f"    [{i}] {m.GetName()}")
        _save_fbx(manager, scene, output_path)
        manager.Destroy()

        _log("=" * 60)
        _log("KIMODO FBX EXPORT DONE")
        _log("=" * 60)
        return output_path

    except Exception as e:
        _log(f"FATAL ERROR: {e}")
        traceback.print_exc()
        raise
