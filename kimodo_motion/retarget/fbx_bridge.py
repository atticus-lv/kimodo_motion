"""Blender-side bridge for FBX SDK retarget.

Runs inside Blender. Exports the user's selected armature to a temp FBX,
invokes fbx_runner.py in the kimodo_venv subprocess, imports the resulting
animated FBX, extracts the Action, and assigns it to the user's armature.
"""

from __future__ import annotations
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from typing import Optional

import bpy


ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_PY = os.path.join(ADDON_DIR, "retarget", "fbx_runner.py")
TEMP_TARGETS_DIR = os.path.join(tempfile.gettempdir(), "kimodo_targets")
TEMP_OUTPUTS_DIR = os.path.join(tempfile.gettempdir(), "kimodo_outputs")
TEMP_MAPPINGS_DIR = os.path.join(tempfile.gettempdir(), "kimodo_mappings")
TEMP_COLLECTION = "Kimodo_Temp"


# ── armature hash + target FBX cache ──


def compute_armature_hash(arm_obj: bpy.types.Object) -> str:
    """MD5 of sorted bone names + parent indices. Changes only if structure changes."""
    h = hashlib.md5()
    bone_names = sorted(b.name for b in arm_obj.data.bones)
    h.update("\n".join(bone_names).encode("utf-8"))
    # Include parent indices (position in sorted list)
    name_to_idx = {n: i for i, n in enumerate(bone_names)}
    parents = []
    for b in arm_obj.data.bones:
        p = name_to_idx.get(b.parent.name, -1) if b.parent else -1
        parents.append(f"{name_to_idx[b.name]}:{p}")
    h.update("|".join(sorted(parents)).encode("utf-8"))
    # Scale of armature also matters for retarget
    h.update(
        f"{arm_obj.scale[0]:.6f},{arm_obj.scale[1]:.6f},{arm_obj.scale[2]:.6f}".encode(
            "utf-8"
        )
    )
    return h.hexdigest()[:12]


def export_target_fbx(arm_obj: bpy.types.Object, use_cache: bool = True) -> str:
    """Export selected armature (+ child meshes) to a temp FBX for retarget template.

    Returns the FBX path. Uses cache keyed by armature hash.
    """
    os.makedirs(TEMP_TARGETS_DIR, exist_ok=True)
    key = compute_armature_hash(arm_obj)
    fbx_path = os.path.join(TEMP_TARGETS_DIR, f"target_{key}.fbx")

    if use_cache and os.path.isfile(fbx_path) and os.path.getsize(fbx_path) > 1024:
        print(f"[Kimodo bridge] target FBX cache hit: {fbx_path}")
        return fbx_path

    # Clear any active pose (retarget template should be at rest)
    # But don't modify user data — export first, restore later would be complex.
    # For now: export as-is; the user is expected to have armature at rest.

    # Select armature + child meshes
    prev_active = bpy.context.view_layer.objects.active
    prev_selection = [o for o in bpy.context.selected_objects]
    try:
        bpy.ops.object.select_all(action="DESELECT")
        arm_obj.select_set(True)
        for child in arm_obj.children:
            if child.type == "MESH":
                child.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj

        # Ensure rest pose for export (don't bake current animation)
        bpy.ops.export_scene.fbx(
            filepath=fbx_path,
            use_selection=True,
            object_types={"ARMATURE", "MESH"},
            add_leaf_bones=False,
            bake_anim=False,
            use_armature_deform_only=False,
            mesh_smooth_type="OFF",
            use_custom_props=False,
        )
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for o in prev_selection:
            try:
                o.select_set(True)
            except Exception:
                pass
        if prev_active:
            bpy.context.view_layer.objects.active = prev_active

    if not os.path.isfile(fbx_path) or os.path.getsize(fbx_path) < 1024:
        raise RuntimeError(f"FBX export produced empty/missing file: {fbx_path}")

    print(
        f"[Kimodo bridge] exported target FBX: {fbx_path} ({os.path.getsize(fbx_path)} bytes)"
    )
    return fbx_path


# ── subprocess retarget ──


def _write_mapping_json(mapping: dict) -> str:
    """Write bone mapping dict to a temp JSON file."""
    os.makedirs(TEMP_MAPPINGS_DIR, exist_ok=True)
    # Filter out keys starting with '_' (comments)
    clean = {k: v for k, v in mapping.items() if not k.startswith("_")}
    # Unique filename so concurrent generations don't collide
    path = os.path.join(TEMP_MAPPINGS_DIR, f"mapping_{uuid.uuid4().hex[:8]}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, ensure_ascii=False)
    return path


def run_fbx_retarget(
    venv_python: str,
    npz_path: str,
    target_fbx: str,
    output_fbx: str,
    bone_mapping: dict,
    sample_index: int = 0,
    yaw_offset: float = 0.0,
    timeout: int = 60,
) -> None:
    """Invoke fbx_runner.py in the kimodo_venv subprocess.

    Raises RuntimeError with stderr on failure.
    """
    if not os.path.isfile(venv_python):
        raise FileNotFoundError(f"venv python not found: {venv_python}")
    if not os.path.isfile(RUNNER_PY):
        raise FileNotFoundError(f"runner script missing: {RUNNER_PY}")
    if not os.path.isfile(npz_path):
        raise FileNotFoundError(f"NPZ missing: {npz_path}")
    if not os.path.isfile(target_fbx):
        raise FileNotFoundError(f"target FBX missing: {target_fbx}")

    os.makedirs(os.path.dirname(output_fbx) or ".", exist_ok=True)
    mapping_json = _write_mapping_json(bone_mapping)

    cmd = [
        venv_python,
        RUNNER_PY,
        "--npz",
        npz_path,
        "--target-fbx",
        target_fbx,
        "--out",
        output_fbx,
        "--mapping-json",
        mapping_json,
        "--sample",
        str(sample_index),
        "--yaw",
        str(yaw_offset),
    ]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    print(
        f"[Kimodo bridge] subprocess: {' '.join(cmd[:2])} ... (sample={sample_index})"
    )
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Retarget subprocess timed out after {timeout}s")

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    # fbxsdkpy has a known Windows DLL-unload segfault at process exit
    # (exit code 0xC0000005 = 3221225477). The actual work completes first and
    # writes the output FBX. So: trust the output file, not the exit code.
    output_ok = os.path.isfile(output_fbx) and os.path.getsize(output_fbx) >= 1024

    if not output_ok:
        raise RuntimeError(
            f"Retarget subprocess produced no valid FBX (exitcode {result.returncode}):\n"
            f"CMD: {' '.join(cmd)}\n"
            f"STDERR:\n{stderr}\n"
            f"STDOUT (last 500):\n{stdout[-500:]}"
        )

    # Log non-zero exit codes that did still produce output (the segfault case)
    if result.returncode != 0:
        print(
            f"[Kimodo bridge] subprocess exited {result.returncode} (likely "
            f"fbxsdkpy DLL unload segfault) but FBX written OK — continuing."
        )

    # Cleanup mapping temp file
    try:
        os.remove(mapping_json)
    except OSError:
        pass

    print(
        f"[Kimodo bridge] retarget OK: {output_fbx} ({os.path.getsize(output_fbx)} bytes)"
    )


# ── import + action transfer ──


def _get_temp_collection() -> bpy.types.Collection:
    """Get or create isolation collection for temp imports."""
    col = bpy.data.collections.get(TEMP_COLLECTION)
    if col is None:
        col = bpy.data.collections.new(TEMP_COLLECTION)
        bpy.context.scene.collection.children.link(col)
    # Hide from outliner by default
    for vl_col in bpy.context.view_layer.layer_collection.children:
        if vl_col.collection == col:
            vl_col.exclude = False
            vl_col.hide_viewport = True
            break
    return col


def _sanitize_name(s: str, max_len: int = 40) -> str:
    """Make a string safe for Blender data-block name."""
    result = []
    for ch in s:
        if ch.isalnum() or ch in "_-":
            result.append(ch)
        elif ch == " ":
            result.append("_")
    out = "".join(result)[:max_len].strip("_")
    return out or "motion"


def import_action_from_fbx(
    fbx_path: str,
    target_arm: bpy.types.Object,
    action_name: str,
    assign_as_active: bool = True,
) -> bpy.types.Action:
    """Import retargeted FBX, extract Action, assign to target armature, cleanup temp.

    Returns the Action data-block (now owned by target_arm or just stored with fake user).
    """
    if not os.path.isfile(fbx_path):
        raise FileNotFoundError(fbx_path)

    # Snapshot existing objects + actions to detect new ones
    before_objs = set(o.name for o in bpy.data.objects)
    before_actions = set(a.name for a in bpy.data.actions)

    # Import into temp collection (move imported objects there afterwards)
    try:
        bpy.ops.import_scene.fbx(
            filepath=fbx_path,
            use_anim=True,
            automatic_bone_orientation=False,
            use_custom_normals=True,
        )
    except Exception as e:
        raise RuntimeError(f"FBX import failed: {e}")

    new_obj_names = set(o.name for o in bpy.data.objects) - before_objs
    new_action_names = set(a.name for a in bpy.data.actions) - before_actions

    imported_arm = None
    imported_objs = []
    for name in new_obj_names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        imported_objs.append(obj)
        if obj.type == "ARMATURE":
            imported_arm = obj

    if imported_arm is None:
        # Cleanup any partial import, then error
        for obj in imported_objs:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        for a_name in new_action_names:
            a = bpy.data.actions.get(a_name)
            if a is not None:
                bpy.data.actions.remove(a)
        raise RuntimeError(f"Imported FBX had no armature: {fbx_path}")

    if (
        imported_arm.animation_data is None
        or imported_arm.animation_data.action is None
    ):
        # Cleanup and error
        for obj in imported_objs:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except Exception:
                pass
        for a_name in new_action_names:
            a = bpy.data.actions.get(a_name)
            if a is not None:
                bpy.data.actions.remove(a)
        raise RuntimeError(f"Imported armature has no action: {imported_arm.name}")

    action = imported_arm.animation_data.action

    # Rename action to stable, user-facing name (with uniqueness suffix)
    desired = action_name
    final_name = desired
    if final_name in bpy.data.actions and bpy.data.actions[final_name] is not action:
        final_name = f"{desired}_{uuid.uuid4().hex[:4]}"
    action.name = final_name
    action.use_fake_user = True  # keep even if not assigned anywhere

    # Transfer to target armature
    if assign_as_active:
        if target_arm.animation_data is None:
            target_arm.animation_data_create()
        target_arm.animation_data.action = action
        target_arm.animation_data.use_nla = False

    # Cleanup: remove imported armature + child meshes (action survives via fake_user)
    for obj in imported_objs:
        try:
            # Detach from all parents to avoid orphan warnings
            obj.parent = None
        except Exception:
            pass
    for obj in list(imported_objs):
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception as e:
            print(f"[Kimodo bridge] warning: failed to remove {obj.name}: {e}")

    # Remove any leftover armature datablocks the import created
    for name in list(bpy.data.armatures.keys()):
        arm_data = bpy.data.armatures.get(name)
        if arm_data is not None and arm_data.users == 0:
            bpy.data.armatures.remove(arm_data)
    for name in list(bpy.data.meshes.keys()):
        mesh_data = bpy.data.meshes.get(name)
        if mesh_data is not None and mesh_data.users == 0:
            bpy.data.meshes.remove(mesh_data)

    print(f"[Kimodo bridge] imported action '{action.name}' → {target_arm.name}")
    return action


# ── skeleton preset detection ──


def detect_skeleton_preset(arm_obj: bpy.types.Object) -> Optional[str]:
    """Return 'mixamo' | 'vroid' | 'mmd' | None based on bone naming."""
    names_lower = [b.name.lower() for b in arm_obj.data.bones]
    joined = " ".join(names_lower)

    if "mixamorig:" in joined:
        return "mixamo"
    if "j_bip_" in joined or "vroid_" in joined:
        return "vroid"
    # MMD uses Japanese characters; check a few distinctive ones
    for b_name in names_lower:
        for kw in ("全ての親", "上半身", "下半身", "センター", "左足", "右足"):
            if kw in b_name:
                return "mmd"
    return None
