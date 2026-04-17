bl_info = {
    "name": "Kimodo Motion",
    "author": "Xingxun",
    "version": (0, 1, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Kimodo",
    "description": "NVIDIA Kimodo AI text-to-motion generation for Blender",
    "category": "Animation",
}

import bpy

from . import preferences
from .ui import panels, operators, install_panel


classes = []


def _collect_classes():
    """Collect all registerable classes from submodules."""
    cls_list = []
    cls_list.extend(preferences.classes)
    cls_list.extend(operators.classes)
    cls_list.extend(panels.classes)
    cls_list.extend(install_panel.classes)
    return cls_list


def register():
    global classes
    classes = _collect_classes()
    for cls in classes:
        bpy.utils.register_class(cls)
    preferences.register_props()


def unregister():
    preferences.unregister_props()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    classes.clear()


if __name__ == "__main__":
    register()
