"""Coordinate transforms shared by Kimodo constraints and in-Blender retarget.

Kimodo/SOMA motion is Y-up. Blender scenes are Z-up. This module is the single
place that describes that basis change and the target-rig scale used to map
authored world-space paths into Kimodo's canonical meters and back again.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Optional

import bpy
from mathutils import Matrix, Vector

KIMODO_FPS = 30.0
DEFAULT_SOMA_HEIGHT_M = 1.70


def kimodo_to_blender_vec(v: Iterable[float]) -> Vector:
    """Kimodo Y-up vector -> Blender Z-up vector."""
    x, y, z = v
    return Vector((x, -z, y))


def blender_to_kimodo_vec(v: Iterable[float]) -> Vector:
    """Blender Z-up vector -> Kimodo Y-up vector."""
    vv = Vector(v)
    return Vector((vv.x, vv.z, -vv.y))


def blender_to_kimodo_2d(v: Iterable[float]) -> list[float]:
    """Blender ground-plane vector/point -> Kimodo [x, z] ground-plane pair."""
    kk = blender_to_kimodo_vec(v)
    return [float(kk.x), float(kk.z)]


def kimodo_2d_to_blender_vec(v: Iterable[float]) -> Vector:
    """Kimodo [x, z] ground-plane pair -> Blender ground-plane vector."""
    x, z = v
    return kimodo_to_blender_vec((x, 0.0, z))


def rotation_from_matrix_world(obj: bpy.types.Object | None) -> Matrix:
    """Return object world rotation without scale/shear."""
    if obj is None:
        return Matrix.Identity(3)
    return obj.matrix_world.to_3x3().to_quaternion().to_matrix()


def source_height_from_neutral(neutral_joints) -> float:
    """Height span of Kimodo neutral joints in Y-up meters."""
    if neutral_joints is None:
        return DEFAULT_SOMA_HEIGHT_M
    try:
        ys = [float(row[1]) for row in neutral_joints]
    except Exception:
        return DEFAULT_SOMA_HEIGHT_M
    if not ys:
        return DEFAULT_SOMA_HEIGHT_M
    span = max(ys) - min(ys)
    return span if span > 1e-6 else DEFAULT_SOMA_HEIGHT_M


def _resolve_bone_name(arm: bpy.types.Object, target_name: str) -> Optional[str]:
    lc = {b.name.lower(): b.name for b in arm.data.bones}
    key = target_name.lower()
    if key in lc:
        return lc[key]
    stripped = key.split(":")[-1]
    if stripped in lc:
        return lc[stripped]
    for bone_key, real in lc.items():
        if bone_key.split(":")[-1] == stripped:
            return real
    return None


def target_height_world(
    arm: bpy.types.Object,
    bone_mapping: Optional[dict] = None,
) -> float:
    """Measure target humanoid height in Blender world meters.

    The measurement uses mapped target bones when available, projected onto the
    armature object's world-up axis. That keeps the height stable when the
    armature object has a world rotation.
    """
    if arm is None or arm.type != "ARMATURE":
        return DEFAULT_SOMA_HEIGHT_M

    names = []
    if bone_mapping:
        for target in bone_mapping.values():
            if not isinstance(target, str) or target.startswith("_"):
                continue
            real = _resolve_bone_name(arm, target)
            if real:
                names.append(real)
    if not names:
        names = [b.name for b in arm.data.bones]

    rot = rotation_from_matrix_world(arm)
    up_world = (rot @ Vector((0.0, 0.0, 1.0))).normalized()
    dots = []
    for name in set(names):
        bone = arm.data.bones.get(name)
        if bone is None:
            continue
        head = arm.matrix_world @ bone.head_local
        dots.append(float(head.dot(up_world)))
    if len(dots) < 2:
        return DEFAULT_SOMA_HEIGHT_M
    height = max(dots) - min(dots)
    return height if height > 1e-6 else DEFAULT_SOMA_HEIGHT_M


def blender_frame_to_kimodo(
    blender_frame: int,
    scene_start: int,
    blender_fps: float,
    kimodo_fps: float = KIMODO_FPS,
) -> int:
    elapsed = (int(blender_frame) - int(scene_start)) / float(blender_fps)
    return max(0, int(round(elapsed * float(kimodo_fps))))


@dataclass
class MotionSpaceContext:
    """Maps authored Blender world points to Kimodo canonical constraint points."""

    anchor_world: Vector
    root_scale: float = 1.0
    author_rotation: Matrix = Matrix.Identity(3)

    @classmethod
    def for_target(
        cls,
        target_arm: bpy.types.Object | None,
        anchor_world: Iterable[float],
        root_scale: float = 1.0,
    ) -> "MotionSpaceContext":
        return cls(
            anchor_world=Vector(anchor_world),
            root_scale=max(float(root_scale), 1e-6),
            author_rotation=rotation_from_matrix_world(target_arm),
        )

    @property
    def author_rotation_inv(self) -> Matrix:
        return self.author_rotation.transposed()

    def world_delta_to_kimodo(self, delta_world: Iterable[float]) -> Vector:
        local_blender = self.author_rotation_inv @ Vector(delta_world)
        return blender_to_kimodo_vec(local_blender) / self.root_scale

    def kimodo_delta_to_world(self, delta_kimodo: Iterable[float]) -> Vector:
        local_blender = kimodo_to_blender_vec(delta_kimodo) * self.root_scale
        return self.author_rotation @ local_blender

    def world_point_to_kimodo_2d(self, point_world: Iterable[float]) -> list[float]:
        kk = self.world_delta_to_kimodo(Vector(point_world) - self.anchor_world)
        return [float(kk.x), float(kk.z)]

    def kimodo_2d_to_world_point(self, point_2d: Iterable[float]) -> Vector:
        return self.anchor_world + self.kimodo_delta_to_world(
            (float(point_2d[0]), 0.0, float(point_2d[1]))
        )

    def world_point_to_kimodo_pos(self, point_world: Iterable[float]) -> list[float]:
        point = Vector(point_world)
        kk_delta = self.world_delta_to_kimodo(point - self.anchor_world)
        local_point = self.author_rotation_inv @ point
        height = blender_to_kimodo_vec(local_point).y / self.root_scale
        return [float(kk_delta.x), float(height), float(kk_delta.z)]

