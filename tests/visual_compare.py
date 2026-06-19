"""Visual smoke test for Kimodo retarget root-path anchoring.

Run:
    blender --background --python tests/visual_compare.py

Outputs a PNG contact sheet in /private/tmp/kimodo_visual_compare.png using the
sample GLB rigs from ../kimodo-retarget-proto/rigs when available.
"""
import os
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from retarget import bpy_retarget, motion_space, skeleton_detect  # noqa: E402
from retarget.mapping import load_preset  # noqa: E402

RIG_DIR = "/Users/atticus/Desktop/PythonProject/kimodo-retarget-proto/rigs"
OUT_PNG = os.path.join(tempfile.gettempdir(), "kimodo_visual_compare.png")


def _synthetic_npz():
    names = [
        "Hips",
        "Spine1",
        "Spine2",
        "Chest",
        "Neck1",
        "Head",
        "LeftArm",
        "LeftForeArm",
        "LeftHand",
        "RightArm",
        "RightForeArm",
        "RightHand",
        "LeftLeg",
        "LeftShin",
        "LeftFoot",
        "RightLeg",
        "RightShin",
        "RightFoot",
    ]
    parents = np.array(
        [-1, 0, 1, 2, 3, 4, 3, 6, 7, 3, 9, 10, 0, 12, 13, 0, 15, 16],
        dtype=np.int64,
    )
    neutral = np.array(
        [
            (0, 0.95, 0),
            (0, 1.12, 0),
            (0, 1.28, 0),
            (0, 1.45, 0),
            (0, 1.60, 0),
            (0, 1.78, 0),
            (0.30, 1.43, 0),
            (0.60, 1.43, 0),
            (0.86, 1.43, 0),
            (-0.30, 1.43, 0),
            (-0.60, 1.43, 0),
            (-0.86, 1.43, 0),
            (0.10, 0.86, 0),
            (0.10, 0.46, 0),
            (0.10, 0.08, -0.08),
            (-0.10, 0.86, 0),
            (-0.10, 0.46, 0),
            (-0.10, 0.08, -0.08),
        ],
        dtype=np.float32,
    )
    T = 36
    posed = np.tile(neutral, (T, 1, 1))
    for f in range(T):
        # Kimodo +Z becomes Blender -Y after retarget.
        posed[f, :, 2] += 1.8 * f / (T - 1)
    grm = np.tile(np.eye(3, dtype=np.float32), (T, len(names), 1, 1))
    path = os.path.join(tempfile.gettempdir(), "kimodo_visual_synth.npz")
    np.savez(
        path,
        posed_joints=posed[None].astype(np.float32),
        global_rot_mats=grm[None].astype(np.float32),
        joint_parents=parents,
        joint_names=np.array(names, dtype=np.str_),
        neutral_joints=neutral,
        fps=np.float32(30.0),
    )
    return path, T


def _motion_npz():
    real_path = os.environ.get("KIMODO_VISUAL_NPZ", "").strip()
    if real_path:
        data = np.load(real_path, allow_pickle=False)
        posed = np.asarray(data["posed_joints"])
        total_frames = int(posed.shape[1] if posed.ndim == 4 else posed.shape[0])
        return real_path, total_frames
    return _synthetic_npz()


def _resolve_bone(arm, name):
    key = name.lower()
    for bone in arm.data.bones:
        if bone.name.lower() == key or bone.name.split(":")[-1].lower() == key:
            return bone.name
    return None


def _import_rig(path, x_offset):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new_objs = [obj for obj in bpy.data.objects if obj not in before]
    arms = [obj for obj in new_objs if obj.type == "ARMATURE"]
    if not arms:
        return None
    arm = arms[0]
    arm.location.x += x_offset
    arm.show_in_front = True
    return arm


def _draw_path(name, start, end, color=(0.1, 1.0, 0.2, 1.0)):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = 0.025
    spl = curve.splines.new("POLY")
    spl.points.add(1)
    spl.points[0].co = (start.x, start.y, start.z, 1.0)
    spl.points[1].co = (end.x, end.y, end.z, 1.0)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.color = color
    return obj


def _add_marker(name, loc, radius, color):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=radius, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.color = color
    return obj


def _look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    npz, total_frames = _motion_npz()
    rig_paths = [
        os.path.join(RIG_DIR, "Xbot.glb"),
        os.path.join(RIG_DIR, "Soldier.glb"),
    ]
    arms = []
    for idx, path in enumerate(rig_paths):
        if not os.path.isfile(path):
            continue
        arm = _import_rig(path, x_offset=(idx - 0.5) * 3.0)
        if arm:
            arms.append(arm)

    if not arms:
        raise RuntimeError("No GLB armatures imported for visual compare")

    for arm in arms:
        preset = skeleton_detect.detect_skeleton_preset(arm) or "mixamo"
        bone_mapping = load_preset(preset)
        hips_name = _resolve_bone(arm, bone_mapping.get("Hips", "Hips")) or _resolve_bone(arm, "Hips")
        if not hips_name:
            continue
        anchor = arm.matrix_world @ arm.data.bones[hips_name].head_local
        scale = motion_space.target_height_world(arm, bone_mapping) / motion_space.DEFAULT_SOMA_HEIGHT_M
        end = anchor + motion_space.kimodo_to_blender_vec((0.0, 0.0, 1.8)) * scale
        guide_lift = Vector((0.0, 0.0, 0.08))
        _draw_path(f"{arm.name}_expected_root_path", anchor + guide_lift, end + guide_lift)
        _add_marker(f"{arm.name}_start", anchor + guide_lift, 0.08, (0.1, 0.8, 1.0, 1.0))
        _add_marker(f"{arm.name}_end", end + guide_lift, 0.08, (1.0, 0.2, 0.1, 1.0))
        bpy_retarget.retarget_sample(
            arm,
            npz,
            bone_mapping,
            action_name=f"Kimodo_visual_{arm.name}",
            action_start_frame=0,
            root_anchor_world=anchor,
            root_scale=scale,
            with_root=True,
        )

    bpy.context.scene.frame_set(total_frames - 1)
    bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
    if bpy.context.scene.world is None:
        bpy.context.scene.world = bpy.data.worlds.new("World")
    bpy.context.scene.world.color = (0.04, 0.04, 0.04)
    bpy.context.scene.display.shading.color_type = "OBJECT"
    bpy.context.scene.camera = None
    bpy.ops.object.light_add(type="AREA", location=(0, -4, 5))
    bpy.context.object.data.energy = 500
    bpy.context.object.data.size = 5
    bpy.ops.object.camera_add(location=(4.0, -7.0, 2.7))
    bpy.context.scene.camera = bpy.context.object
    _look_at(bpy.context.object, (0.0, -0.5, 1.0))
    bpy.context.scene.render.resolution_x = 1400
    bpy.context.scene.render.resolution_y = 900
    bpy.context.scene.render.filepath = OUT_PNG
    bpy.ops.render.render(write_still=True)
    print(f"VISUAL_COMPARE {OUT_PNG}")


if __name__ == "__main__":
    main()
