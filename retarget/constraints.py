"""Kimodo constraint JSON builders for Blender-authored guides."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Optional

import bpy
from mathutils import Matrix, Vector

from . import motion_space

SOMA_JOINT_ORDER = [
    "Hips",
    "Spine1",
    "Spine2",
    "Chest",
    "Neck1",
    "Neck2",
    "Head",
    "Jaw",
    "LeftEye",
    "RightEye",
    "LeftShoulder",
    "LeftArm",
    "LeftForeArm",
    "LeftHand",
    "LeftHandThumbEnd",
    "LeftHandMiddleEnd",
    "RightShoulder",
    "RightArm",
    "RightForeArm",
    "RightHand",
    "RightHandThumbEnd",
    "RightHandMiddleEnd",
    "LeftLeg",
    "LeftShin",
    "LeftFoot",
    "LeftToeBase",
    "RightLeg",
    "RightShin",
    "RightFoot",
    "RightToeBase",
]

SOMA_JOINT_PARENTS = [
    -1,
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    6,
    6,
    3,
    10,
    11,
    12,
    13,
    13,
    3,
    16,
    17,
    18,
    19,
    19,
    0,
    22,
    23,
    24,
    0,
    26,
    27,
    28,
]

EFFECTOR_IDX = {
    "left_hand": 13,
    "right_hand": 19,
    "left_foot": 24,
    "right_foot": 28,
}

EFFECTOR_REST_OFFSETS = {
    "left_hand": (0.72, 0.45, 0.0),
    "right_hand": (-0.72, 0.45, 0.0),
    "left_foot": (0.10, -0.90, 0.0),
    "right_foot": (-0.10, -0.90, 0.0),
}

SOMA_PROXY_REST = {
    "Hips": (0.00, 0.95, 0.00),
    "Spine1": (0.00, 1.12, 0.00),
    "Spine2": (0.00, 1.28, 0.00),
    "Chest": (0.00, 1.45, 0.00),
    "Neck1": (0.00, 1.58, 0.00),
    "Neck2": (0.00, 1.67, 0.00),
    "Head": (0.00, 1.78, 0.00),
    "Jaw": (0.00, 1.72, -0.05),
    "LeftEye": (0.04, 1.82, -0.07),
    "RightEye": (-0.04, 1.82, -0.07),
    "LeftShoulder": (0.18, 1.44, 0.00),
    "LeftArm": (0.38, 1.43, 0.00),
    "LeftForeArm": (0.65, 1.43, 0.00),
    "LeftHand": (0.90, 1.43, 0.00),
    "LeftHandThumbEnd": (0.98, 1.38, -0.04),
    "LeftHandMiddleEnd": (1.05, 1.43, 0.00),
    "RightShoulder": (-0.18, 1.44, 0.00),
    "RightArm": (-0.38, 1.43, 0.00),
    "RightForeArm": (-0.65, 1.43, 0.00),
    "RightHand": (-0.90, 1.43, 0.00),
    "RightHandThumbEnd": (-0.98, 1.38, -0.04),
    "RightHandMiddleEnd": (-1.05, 1.43, 0.00),
    "LeftLeg": (0.10, 0.86, 0.00),
    "LeftShin": (0.10, 0.46, 0.00),
    "LeftFoot": (0.10, 0.08, -0.08),
    "LeftToeBase": (0.10, 0.04, -0.24),
    "RightLeg": (-0.10, 0.86, 0.00),
    "RightShin": (-0.10, 0.46, 0.00),
    "RightFoot": (-0.10, 0.08, -0.08),
    "RightToeBase": (-0.10, 0.04, -0.24),
}


@dataclass
class ConstraintBuildResult:
    constraints: list[dict]
    anchor_world: Optional[Vector]
    root_scale: float
    action_start_frame: int


def heading_from_angle(angle_rad: float) -> list[float]:
    return [math.cos(float(angle_rad)), math.sin(float(angle_rad))]


def rotation_axis_angle_vec(q) -> list[float]:
    q = q.normalized()
    angle = 2.0 * math.acos(max(-1.0, min(1.0, q.w)))
    s = math.sqrt(max(0.0, 1.0 - q.w * q.w))
    if s < 1e-6:
        return [0.0, 0.0, 0.0]
    axis = Vector((q.x / s, q.y / s, q.z / s))
    return [float(axis.x * angle), float(axis.y * angle), float(axis.z * angle)]


def _rot3_to_axis_angle(m: Matrix) -> list[float]:
    return rotation_axis_angle_vec(m.to_quaternion())


def _constraint_type_name(ctype: str) -> str:
    return ctype.replace("_", "-")


def _frame_to_kimodo(scene, frame: int, start_frame: int, kimodo_fps: float) -> int:
    fps = scene.render.fps / scene.render.fps_base
    return motion_space.blender_frame_to_kimodo(frame, start_frame, fps, kimodo_fps)


def _first_anchor(items) -> Optional[Vector]:
    for item in items:
        obj = item.marker_object
        if obj is None:
            continue
        if item.constraint_type == "root2d":
            return obj.matrix_world.translation.copy()
        if item.constraint_type == "fullbody":
            return _root_world_position(obj)
    return None


def _root_world_position(obj) -> Vector:
    if obj and obj.type == "ARMATURE":
        for name in ("Hips", "hips", "Hip", "pelvis", "Pelvis"):
            pb = obj.pose.bones.get(name)
            if pb:
                return obj.matrix_world @ pb.head
    return obj.matrix_world.translation.copy()


def _pose_local_joint_rots(armature_obj: bpy.types.Object) -> list[list[float]]:
    pose_bones = armature_obj.pose.bones
    basis_bk = Matrix(((1, 0, 0), (0, 0, 1), (0, -1, 0)))
    basis_kb = basis_bk.transposed()
    global_rots: dict[str, Matrix] = {}
    for name in SOMA_JOINT_ORDER:
        pb = pose_bones.get(name)
        if pb is None:
            global_rots[name] = Matrix.Identity(3)
            continue
        rest = pb.bone.matrix_local.to_3x3()
        pose = pb.matrix.to_3x3()
        delta = pose @ rest.transposed()
        global_rots[name] = basis_bk @ delta @ basis_kb

    out = []
    for idx, name in enumerate(SOMA_JOINT_ORDER):
        parent_idx = SOMA_JOINT_PARENTS[idx]
        if parent_idx < 0:
            local = global_rots[name]
        else:
            local = global_rots[SOMA_JOINT_ORDER[parent_idx]].transposed() @ global_rots[name]
        out.append(_rot3_to_axis_angle(local))
    return out


def sample_curve_arc_length(curve_obj, n_samples: int, depsgraph) -> list[Vector]:
    evaluated = curve_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        verts = [curve_obj.matrix_world @ v.co for v in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()

    if len(verts) < 2:
        return verts
    lengths = [0.0]
    for i in range(len(verts) - 1):
        lengths.append(lengths[-1] + (verts[i + 1] - verts[i]).length)
    total = lengths[-1]
    if total <= 1e-8:
        return [verts[0].copy() for _ in range(n_samples)]

    samples = []
    seg = 0
    for i in range(n_samples):
        distance = total * i / max(n_samples - 1, 1)
        while seg < len(lengths) - 2 and lengths[seg + 1] < distance:
            seg += 1
        seg_len = lengths[seg + 1] - lengths[seg]
        frac = (distance - lengths[seg]) / seg_len if seg_len > 1e-8 else 0.0
        samples.append(verts[seg].lerp(verts[seg + 1], frac))
    return samples


def build_constraints_json(
    constraint_items,
    scene: bpy.types.Scene,
    target_arm: bpy.types.Object | None = None,
    bone_mapping: Optional[dict] = None,
    action_start_frame: Optional[int] = None,
    kimodo_fps: float = motion_space.KIMODO_FPS,
    auto_canonicalize: bool = True,
) -> ConstraintBuildResult:
    items = sorted(
        [item for item in constraint_items if item.enabled and item.marker_object],
        key=lambda item: int(item.frame),
    )
    start_frame = int(action_start_frame if action_start_frame is not None else scene.frame_start)
    target_h = motion_space.target_height_world(target_arm, bone_mapping)
    root_scale = target_h / motion_space.DEFAULT_SOMA_HEIGHT_M
    anchor = _first_anchor(items) if auto_canonicalize else None
    if anchor is None and items:
        anchor = items[0].marker_object.matrix_world.translation.copy()
    if anchor is None:
        return ConstraintBuildResult([], None, root_scale, start_frame)

    ctx = motion_space.MotionSpaceContext.for_target(target_arm, anchor, root_scale)
    grouped: dict[str, list] = {}
    order: list[str] = []
    for item in items:
        if item.constraint_type not in grouped:
            order.append(item.constraint_type)
            grouped[item.constraint_type] = []
        grouped[item.constraint_type].append(item)

    saved_frame = scene.frame_current
    constraints: list[dict] = []
    try:
        for ctype in order:
            group = grouped[ctype]
            frame_indices = []
            smooth_root_2d = []
            root_positions = []
            local_joints_rot = []
            global_root_heading = []

            for item in group:
                frame_indices.append(_frame_to_kimodo(scene, item.frame, start_frame, kimodo_fps))
                obj = item.marker_object

                if ctype == "root2d":
                    smooth_root_2d.append(ctx.world_point_to_kimodo_2d(obj.matrix_world.translation))
                    if item.include_heading:
                        global_root_heading.append(heading_from_angle(item.heading_angle))
                    continue

                if ctype == "fullbody":
                    scene.frame_set(item.frame)
                    bpy.context.view_layer.update()
                    root = _root_world_position(obj)
                    root_positions.append(ctx.world_point_to_kimodo_pos(root))
                    smooth_root_2d.append(ctx.world_point_to_kimodo_2d(root))
                    if obj.type == "ARMATURE":
                        local_joints_rot.append(_pose_local_joint_rots(obj))
                    else:
                        local_joints_rot.append([[0.0, 0.0, 0.0] for _ in SOMA_JOINT_ORDER])
                    continue

                if ctype in EFFECTOR_IDX:
                    target = obj.matrix_world.translation
                    target_k = ctx.world_delta_to_kimodo(target - anchor)
                    ox, oy, oz = EFFECTOR_REST_OFFSETS[ctype]
                    hips_k = [float(target_k.x - ox), float(target_k.y - oy), float(target_k.z - oz)]
                    root_positions.append(hips_k)
                    smooth_root_2d.append([hips_k[0], hips_k[2]])
                    jrots = [[0.0, 0.0, 0.0] for _ in SOMA_JOINT_ORDER]
                    eff_idx = EFFECTOR_IDX[ctype]
                    q = (ctx.author_rotation_inv @ obj.matrix_world.to_3x3()).to_quaternion()
                    jrots[eff_idx] = rotation_axis_angle_vec(q)
                    local_joints_rot.append(jrots)

            block = {"type": _constraint_type_name(ctype), "frame_indices": frame_indices}
            if smooth_root_2d:
                block["smooth_root_2d"] = smooth_root_2d
            if root_positions:
                block["root_positions"] = root_positions
            if local_joints_rot:
                block["local_joints_rot"] = local_joints_rot
            if global_root_heading and len(global_root_heading) == len(frame_indices):
                block["global_root_heading"] = global_root_heading
            constraints.append(block)
    finally:
        scene.frame_set(saved_frame)
        bpy.context.view_layer.update()

    return ConstraintBuildResult(constraints, anchor, root_scale, start_frame)


def create_soma_proxy_armature(context, name: str = "Kimodo_SOMA_Proxy"):
    """Create a simple SOMA30 proxy rig for authoring fullbody constraints."""
    bpy.ops.object.mode_set(mode="OBJECT") if context.object else None
    arm_data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, arm_data)
    context.collection.objects.link(arm)
    context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = arm_data.edit_bones
    bones = {}
    for idx, joint in enumerate(SOMA_JOINT_ORDER):
        eb = edit_bones.new(joint)
        head_k = SOMA_PROXY_REST[joint]
        head_b = motion_space.kimodo_to_blender_vec(head_k)
        child_indices = [i for i, p in enumerate(SOMA_JOINT_PARENTS) if p == idx]
        if child_indices:
            tail_k = SOMA_PROXY_REST[SOMA_JOINT_ORDER[child_indices[0]]]
            tail_b = motion_space.kimodo_to_blender_vec(tail_k)
        else:
            tail_b = head_b + Vector((0.0, 0.0, 0.08))
        eb.head = head_b
        eb.tail = tail_b
        bones[joint] = eb
    for idx, joint in enumerate(SOMA_JOINT_ORDER):
        parent_idx = SOMA_JOINT_PARENTS[idx]
        if parent_idx >= 0:
            bones[joint].parent = bones[SOMA_JOINT_ORDER[parent_idx]]
            bones[joint].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.show_in_front = True
    arm["kimodo_soma_proxy"] = True
    return arm

