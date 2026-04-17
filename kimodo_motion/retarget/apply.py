"""BVH import — original NVIDIA SOMA skeleton, no modifications."""

import bpy
from typing import Optional

GENERATED_COLLECTION = "Kimodo 生成"


def _get_collection() -> bpy.types.Collection:
    """Get or create the Kimodo generation collection (always visible)."""
    col = bpy.data.collections.get(GENERATED_COLLECTION)
    if col is None:
        col = bpy.data.collections.new(GENERATED_COLLECTION)
        bpy.context.scene.collection.children.link(col)
    for vl_col in bpy.context.view_layer.layer_collection.children:
        if vl_col.collection == col:
            vl_col.exclude = False
            break
    return col


def import_bvh_as_source(
    bvh_path: str,
    display_name: str = "",
) -> Optional[bpy.types.Object]:
    """Import Kimodo BVH as original SOMA skeleton.

    No scale conversion, no rest pose modification.
    Animation plays correctly on the imported skeleton.
    """
    col = _get_collection()

    before = set(o.name for o in bpy.data.objects if o.type == "ARMATURE")
    try:
        bpy.ops.import_anim.bvh(
            filepath=bvh_path,
            target="ARMATURE",
            global_scale=1.0,
            rotate_mode="QUATERNION",
            axis_forward="-Z",
            axis_up="Y",
        )
    except Exception as e:
        print(f"[Kimodo] BVH import failed: {e}")
        return None

    after = set(o.name for o in bpy.data.objects if o.type == "ARMATURE")
    new_names = after - before
    if not new_names:
        return None

    arm_name = new_names.pop()
    arm_obj = bpy.data.objects[arm_name]

    # Rename
    if display_name:
        safe_name = display_name[:40].replace(" ", "_")
        arm_obj.name = f"Kimodo_{safe_name}"
    else:
        arm_obj.name = f"Kimodo_{arm_obj.name}"

    # Move to collection
    for c in arm_obj.users_collection:
        c.objects.unlink(arm_obj)
    col.objects.link(arm_obj)

    return arm_obj
