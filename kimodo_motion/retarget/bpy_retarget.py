"""In-Blender retarget — drives a target armature directly from a Kimodo NPZ.

This is the cross-platform replacement for the fbxsdkpy file-level retarget
(fbx_bridge.py + fbx_runner.py + the vendored kimodo_retarget_fbx). It needs no
Autodesk FBX SDK and no separate venv subprocess: everything runs inside Blender's
own Python (bpy + numpy + mathutils), so it works on Apple Silicon / Metal too.

Math (ported & numerically validated against Kimodo's posed_joints ground truth):
    SOMA is Y-up; Blender is Z-up  -> fixed basis change C (default +90deg about X).
    SOMA T-pose has identity global rotations, so the rest-delta offset reduces to
        off[t]    = t_rest                       (target bone rest world orientation)
        t_world[f]= (C * s_world_soma[f] * C^-1) * off[t]
    The bone's local channels (roll, parent space) are left to Blender's pose_bone
    .matrix setter. Root (Hips) gets the source root trajectory, scaled by the
    height ratio and anchored to the first animated frame.

NOTE (rest pose): off = t_rest assumes the target rig is in a T-pose (Mixamo, most
VRChat-ready VRM, Ready Player Me). Genuine A-pose rigs need full 3-axis rest-pose
matching (see FINDINGS); a direction-only fix corrects position but not roll. That
is tracked as the next iteration; T-pose rigs are correct today.
"""
from __future__ import annotations

import math
from typing import Optional

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

_AXIS_FIX = {
    "x+90": Matrix.Rotation(math.radians(90), 4, "X"),
    "x-90": Matrix.Rotation(math.radians(-90), 4, "X"),
    "none": Matrix.Identity(4),
}


def _npmat_to_quat(m: np.ndarray) -> Quaternion:
    """numpy 3x3 (M@v convention, as Kimodo emits) -> mathutils Quaternion."""
    return Matrix([list(r) for r in m]).to_quaternion()


def _resolve_target_bone(arm: bpy.types.Object, target_name: str) -> Optional[str]:
    """Case-insensitive + prefix-aware lookup of a target bone (e.g. 'mixamorig:Hips')."""
    lc = {b.name.lower(): b.name for b in arm.data.bones}
    key = target_name.lower()
    if key in lc:
        return lc[key]
    if ":" in key and key.split(":")[-1] in lc:
        return lc[key.split(":")[-1]]
    for bl, real in lc.items():
        if ":" in bl and bl.split(":")[-1] == key:
            return real
    return None


def _height_y_span(pts: np.ndarray) -> float:
    return float(pts[:, 1].max() - pts[:, 1].min())


def retarget_sample(
    target_arm: bpy.types.Object,
    npz_path: str,
    bone_mapping: dict,
    sample_index: int = 0,
    action_name: str = "Kimodo",
    with_root: bool = True,
    axis_fix: str = "x+90",
) -> bpy.types.Action:
    """Retarget one Kimodo motion sample onto target_arm, returning a new Action.

    bone_mapping: {soma_bone_name: target_bone_name} (the plugin's preset format).
    """
    C = _AXIS_FIX[axis_fix]
    C3 = C.to_3x3()
    Cq = C.to_quaternion()
    Cq_inv = Cq.inverted()

    d = np.load(npz_path, allow_pickle=True)
    posed = np.asarray(d["posed_joints"])
    grm = np.asarray(d["global_rot_mats"])
    # Clamp the requested sample to what the NPZ actually contains: if the server
    # produced fewer samples than the UI asked for, fall back to the last one rather
    # than raising an opaque numpy IndexError.
    if posed.ndim == 4:  # [B,T,J,3]
        nb = posed.shape[0]
        if sample_index >= nb:
            print(f"[Kimodo] WARN: requested sample {sample_index} but NPZ has {nb}; using sample {nb - 1}.")
            sample_index = nb - 1
        posed = posed[sample_index]
    if grm.ndim == 5:  # [B,T,J,3,3]
        grm = grm[min(sample_index, grm.shape[0] - 1)]
    T, J = posed.shape[:2]
    names = [str(x) for x in d["joint_names"]]
    idx = {n.lower(): i for i, n in enumerate(names)}
    if "hips" not in idx:
        print("[Kimodo] WARN: no 'Hips' joint in NPZ joint_names; using joint 0 as root.")
    hips = idx.get("hips", 0)

    # rest positions (for height/scale): neutral_joints if present, else frame 0
    if "neutral_joints" in d.files and np.asarray(d["neutral_joints"]).shape[0] == J:
        rest_pos = np.asarray(d["neutral_joints"], dtype=float)
    else:
        rest_pos = posed[0].astype(float)

    # resolve mapping -> (soma_idx, target_bone_name), keeping hierarchy order of `names`
    pairs = []
    for sname in names:
        tkey = bone_mapping.get(sname) or bone_mapping.get(sname.lower())
        if not tkey:
            continue
        real = _resolve_target_bone(target_arm, tkey)
        if real:
            pairs.append((idx[sname.lower()], real))
    if not pairs:
        raise RuntimeError("No bone mapping pairs resolved against the target armature.")
    hips_target = next((tn for si, tn in pairs if si == hips), None)
    if with_root and hips_target is None:
        print("[Kimodo] WARN: Hips not in bone mapping — root translation skipped (in-place motion).")

    arm = target_arm
    bones = arm.data.bones
    # rest world orientation per mapped bone (object space; rotation is scale-invariant)
    off = {tn: bones[tn].matrix_local.to_quaternion() for _, tn in pairs}

    # ---- scale (source height -> target height) ----
    # Measure height only over the MAPPED body bones. Using every bone would let
    # control/IK/root/hair helpers (often far above the head or below the floor)
    # inflate the span and over-/under-scale the root displacement.
    src_h = _height_y_span(rest_pos)
    zt = [(arm.matrix_world @ bones[tn].head_local).z for _, tn in pairs]
    tgt_h = (max(zt) - min(zt)) if len(zt) > 1 else src_h
    scale = (tgt_h / src_h) if src_h > 1e-6 else 1.0

    # root anchor: first animated frame (avoids injecting one hip-height of offset)
    s_anchor = Vector(posed[0, hips])
    hips_rest_world = (arm.matrix_world @ bones[hips_target].head_local) if hips_target else Vector((0, 0, 0))
    Mw_inv = arm.matrix_world.inverted()

    # ---- dedicated Action for this sample ----
    if arm.animation_data is None:
        arm.animation_data_create()
    # Replace a previous Kimodo action of the same name so repeated generations don't
    # accumulate orphaned '.001' datablocks (each pinned forever by use_fake_user).
    prev = bpy.data.actions.get(action_name)
    if prev is not None:
        prev.use_fake_user = False
        bpy.data.actions.remove(prev)
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    arm.animation_data.action = action
    for _, tn in pairs:
        arm.pose.bones[tn].rotation_mode = "QUATERNION"

    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 0, T - 1

    for f in range(T):
        sc.frame_set(f)
        # Bones are set in `names` (parent-before-child) order; pose_bone.matrix updates
        # the bone's pose_mat synchronously, so a child read later in the same frame sees
        # the parent's new pose without a full per-bone view_layer.update() (which would
        # cost T x B depsgraph evaluations). One update per frame keeps the chain coherent.
        bpy.context.view_layer.update()
        for si, tn in pairs:
            pb = arm.pose.bones[tn]
            s_world = _npmat_to_quat(grm[f, si])
            t_world = (Cq @ s_world @ Cq_inv) @ off[tn]
            m = t_world.to_matrix().to_4x4()
            if with_root and si == hips:
                disp_world = C3 @ (Vector(posed[f, hips]) - s_anchor) * scale
                m.translation = Mw_inv @ (hips_rest_world + disp_world)
            else:
                m.translation = pb.matrix.translation
            pb.matrix = m
            pb.keyframe_insert("rotation_quaternion", frame=f)
            if with_root and si == hips:
                pb.keyframe_insert("location", frame=f)

    return action
