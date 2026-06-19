"""Test suite for the Kimodo Motion add-on — runs inside Blender's Python.

    blender --background --python tests/run_tests.py

Uses Blender's bundled Python (bpy + numpy + mathutils); no pytest, no external
venv. Covers the bone-mapping logic and the in-Blender retarget math (synthetic
data + forward-kinematics oracle). Nothing here calls the model / LLM.

Exit code is non-zero if any test fails (so CI can gate on it).
"""
import os
import sys
import tempfile
import traceback

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import retarget.bpy_retarget as br  # noqa: E402
import retarget.constraints as kc  # noqa: E402
import retarget.mapping as mapping  # noqa: E402
import retarget.motion_space as ms  # noqa: E402

C3 = Matrix.Rotation(np.radians(90), 4, "X").to_3x3()  # SOMA Y-up -> Blender Z-up


# ── mapping logic ───────────────────────────────────────────────────────────

def test_hierarchy_root_and_integrity():
    roots = [n for n, p in mapping.SOMA77_HIERARCHY if p is None]
    assert roots == ["Hips"], roots
    assert mapping.SOMA_ROOT_BONE == "Hips"
    names = [n for n, _ in mapping.SOMA77_HIERARCHY]
    assert len(names) == len(set(names)), "duplicate joint names"
    nameset = set(names)
    for child, parent in mapping.SOMA77_HIERARCHY:
        assert parent != child
        if parent is not None:
            assert parent in nameset, f"{child}: missing parent {parent}"


def test_auto_match_bones():
    assert mapping.auto_match_bones(["Hips"], ["hips"]) == {"Hips": "hips"}
    r = mapping.auto_match_bones(["Hips", "LeftArm"], ["mixamorig:Hips", "mixamorig:LeftArm"])
    assert r["Hips"] == "mixamorig:Hips" and r["LeftArm"] == "mixamorig:LeftArm"
    assert mapping.auto_match_bones(["mixamorig:Hips"], ["Hips"]).get("mixamorig:Hips") == "Hips"
    assert "Tail" not in mapping.auto_match_bones(["Hips", "Tail"], ["mixamorig:Hips"])
    assert mapping.auto_match_bones([], ["x"]) == {}


def test_presets_valid():
    soma = {n for n, _ in mapping.SOMA77_HIERARCHY}
    for name in ("mixamo", "vroid", "mmd"):
        preset = mapping.load_preset(name)
        assert isinstance(preset, dict) and preset
        assert "Hips" in preset
        for key in preset:
            if not key.startswith("_"):
                assert key in soma, f"{name}: preset key {key!r} not a SOMA77 joint"
    assert mapping.load_preset("mixamo")["Hips"].lower() == "mixamorig:hips"


# ── retarget math (synthetic SOMA skeleton + FK oracle) ─────────────────────

def _synthetic_motion():
    """A small SOMA-like skeleton + a motion whose posed_joints == FK(grm, neutral)."""
    joints = [
        ("Hips", None, (0.0, 1.00, 0.0)),
        ("Spine1", "Hips", (0.0, 1.25, 0.0)),
        ("Chest", "Spine1", (0.0, 1.45, 0.0)),
        ("Head", "Chest", (0.0, 1.70, 0.0)),
        ("LeftArm", "Chest", (0.20, 1.45, 0.0)),
        ("LeftForeArm", "LeftArm", (0.50, 1.45, 0.0)),
        ("LeftHand", "LeftForeArm", (0.78, 1.45, 0.0)),
        ("LeftLeg", "Hips", (0.10, 1.00, 0.0)),
        ("LeftShin", "LeftLeg", (0.10, 0.55, 0.0)),
        ("LeftFoot", "LeftShin", (0.10, 0.08, 0.0)),
    ]
    names = [j[0] for j in joints]
    idx = {n: i for i, n in enumerate(names)}
    parents = [idx[j[1]] if j[1] else -1 for j in joints]
    neutral = np.array([j[2] for j in joints], dtype=float)
    J = len(joints)
    T = 12

    def rotz(a):
        c, s = np.cos(a), np.sin(a)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])

    grm = np.tile(np.eye(3), (T, J, 1, 1))
    for f in range(T):
        ang = 0.6 * np.sin(f / T * 2 * np.pi)
        grm[f, idx["LeftArm"]] = rotz(ang)
        grm[f, idx["LeftForeArm"]] = rotz(ang * 1.5)
        grm[f, idx["LeftLeg"]] = rotz(-ang)

    # FK: posed = neutral propagated by parent global rotation; root gets translation
    posed = np.zeros((T, J, 3))
    for f in range(T):
        posed[f, 0] = neutral[0] + np.array([0.0, 0.0, 0.4 * f / T])  # walk +Z (soma frame)
        for j in range(1, J):
            p = parents[j]
            posed[f, j] = posed[f, p] + grm[f, p] @ (neutral[j] - neutral[p])

    path = os.path.join(tempfile.gettempdir(), "kimodo_synth_motion.npz")
    np.savez(
        path,
        posed_joints=posed[None].astype(np.float32),
        global_rot_mats=grm[None].astype(np.float32),
        joint_parents=np.array(parents, dtype=np.int64),
        joint_names=np.array(names),
        neutral_joints=neutral.astype(np.float32),
        fps=np.float32(30.0),
    )
    return path, names, parents, neutral, posed, T


def _build_target_armature(names, parents, neutral):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    head_b = [C3 @ Vector(neutral[j]) for j in range(len(names))]
    children = {i: [c for c in range(len(names)) if parents[c] == i] for i in range(len(names))}
    arm_data = bpy.data.armatures.new("tgt")
    arm = bpy.data.objects.new("Target", arm_data)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = arm_data.edit_bones
    eb = {}
    for j, name in enumerate(names):
        b = ebs.new(name)
        b.head = head_b[j]
        b.tail = head_b[children[j][0]] if children[j] else head_b[j] + Vector((0, 0, 0.08))
        eb[name] = b
    for j, name in enumerate(names):
        if parents[j] >= 0:
            eb[name].parent = eb[names[parents[j]]]
            eb[name].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def test_retarget_fk_matches_ground_truth():
    npz, names, parents, neutral, posed, T = _synthetic_motion()
    arm = _build_target_armature(names, parents, neutral)
    mapping_id = {n: n for n in names}  # identity (target == source skeleton)

    action = br.retarget_sample(arm, npz, mapping_id, sample_index=0,
                                action_name="synth", with_root=True)
    assert action is not None

    sc = bpy.context.scene
    hips = names.index("Hips")
    fixed = lambda v: np.array(C3 @ Vector(v))  # noqa: E731

    pose_err, root_err = [], []
    for f in range(T):
        sc.frame_set(f)
        bpy.context.view_layer.update()
        P = {n: np.array(arm.matrix_world @ arm.pose.bones[n].head) for n in names}
        hp = P["Hips"]
        for j, n in enumerate(names):
            got = P[n] - hp
            exp = fixed(posed[f, j] - posed[f, hips])
            pose_err.append(float(np.linalg.norm(got - exp)))
        root_got = P["Hips"] - np.array(arm.matrix_world @ Vector((C3 @ Vector(neutral[hips]))))
        # compare root trajectory vs source displacement from frame 0
        if f == 0:
            base = P["Hips"]
        root_err.append(float(np.linalg.norm((P["Hips"] - base) - fixed(posed[f, hips] - posed[0, hips]))))

    pmax, rmax = max(pose_err), max(root_err)
    print(f"  [retarget] pose_err_max={pmax:.2e} m  root_err_max={rmax:.2e} m  (T={T})")
    assert pmax < 1e-3, f"pose FK mismatch {pmax}"
    assert rmax < 1e-3, f"root FK mismatch {rmax}"


def test_motion_space_path_roundtrip():
    ctx = ms.MotionSpaceContext.for_target(None, (1.0, 2.0, 0.0), root_scale=2.0)
    target = Vector((1.0, -1.0, 0.0))
    root2d = ctx.world_point_to_kimodo_2d(target)
    back = ctx.kimodo_2d_to_world_point(root2d)
    err = (back - target).length
    print(f"  [motion_space] root2d={root2d} roundtrip_err={err:.2e} m")
    assert err < 1e-6
    assert abs(root2d[0]) < 1e-6 and abs(root2d[1] - 1.5) < 1e-6


def test_constraints_json_root2d_frames_and_scale():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = 30
    sc.render.fps_base = 1.0
    bpy.ops.object.empty_add(type="ARROWS", location=(0, 0, 0))
    a = bpy.context.object
    bpy.ops.object.empty_add(type="ARROWS", location=(0, -2, 0))
    b = bpy.context.object

    class Item:
        def __init__(self, obj, frame):
            self.constraint_type = "root2d"
            self.frame = frame
            self.marker_object = obj
            self.enabled = True
            self.include_heading = False
            self.heading_angle = 0.0

    result = kc.build_constraints_json([Item(a, 10), Item(b, 40)], sc, action_start_frame=10)
    block = result.constraints[0]
    print(f"  [constraints] {block}")
    assert block["type"] == "root2d"
    assert block["frame_indices"] == [0, 30]
    assert np.allclose(block["smooth_root_2d"], [[0.0, 0.0], [0.0, 2.0]])


def test_retarget_root_anchor_scale_and_start_frame():
    npz, names, parents, neutral, posed, T = _synthetic_motion()
    arm = _build_target_armature(names, parents, neutral)
    mapping_id = {n: n for n in names}
    anchor = Vector((1.0, 2.0, 0.0))
    action = br.retarget_sample(
        arm,
        npz,
        mapping_id,
        sample_index=0,
        action_name="anchored",
        with_root=True,
        action_start_frame=20,
        root_anchor_world=anchor,
        root_scale=2.0,
    )
    assert int(action.frame_range[0]) == 20
    assert int(action.frame_range[1]) == 20 + T - 1

    sc = bpy.context.scene
    sc.frame_set(20)
    bpy.context.view_layer.update()
    root0 = arm.matrix_world @ arm.pose.bones["Hips"].head
    err0 = (root0 - anchor).length
    sc.frame_set(20 + T - 1)
    bpy.context.view_layer.update()
    root1 = arm.matrix_world @ arm.pose.bones["Hips"].head
    expected = anchor + (C3 @ Vector(posed[-1, 0] - posed[0, 0])) * 2.0
    err1 = (root1 - expected).length
    print(f"  [retarget_anchor] start_err={err0:.2e} end_err={err1:.2e}")
    assert err0 < 1e-3
    assert err1 < 1e-3


def test_retarget_legacy_object_joint_names_npz():
    npz, names, parents, neutral, _posed, _T = _synthetic_motion()
    data = dict(np.load(npz, allow_pickle=False))
    legacy_path = os.path.join(tempfile.gettempdir(), "kimodo_synth_legacy_names.npz")
    data["joint_names"] = np.array(names, dtype=object)
    np.savez(legacy_path, **data)

    arm = _build_target_armature(names, parents, neutral)
    action = br.retarget_sample(
        arm,
        legacy_path,
        {n: n for n in names},
        sample_index=0,
        action_name="legacy_names",
        with_root=True,
    )
    assert action is not None


# ── harness ─────────────────────────────────────────────────────────────────

def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
