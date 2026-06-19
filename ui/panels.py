"""Kimodo Motion UI panels."""

import threading
import time

import bpy
from bpy.types import Panel

from ..preferences import get_prefs, get_server_url
from ..server.client import KimodoClient


# Server status cache (full /health dict: {status, model_loaded}). Refreshed on a
# BACKGROUND THREAD — never from draw(). draw() must stay non-blocking: a synchronous
# HTTP call there blocks the main thread on every redraw and makes the whole Blender
# UI stutter. draw() reads the last cached value instantly; a stale cache kicks off an
# async refresh that tags the View3D for redraw when it lands.
_status_cache = None
_status_time = 0.0
_status_lock = threading.Lock()
_status_refreshing = False
_STATUS_MAX_AGE = 4.0


def _tag_view3d_redraw() -> None:
    wm = bpy.context.window_manager
    if not wm:
        return None
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return None  # used as a one-shot bpy.app.timer; returning None unregisters it


def _refresh_status_async(url: str) -> None:
    global _status_refreshing
    with _status_lock:
        if _status_refreshing:
            return
        _status_refreshing = True

    def _worker() -> None:
        global _status_cache, _status_time, _status_refreshing
        client = KimodoClient(url)
        result = client.status(timeout=2.0)
        # Self-heal a stuck progress bar: if the panel still shows a generation/load
        # as 'running' but the server reports it's idle, whatever owned GEN_PROGRESS
        # (a modal/timer) must have died without clearing it — reconcile so the bar
        # and the load/unload toggle recover. Skip the brief client-only phases
        # ('retarget' runs in Blender, 'starting' precedes the server request).
        try:
            from .operators import GEN_PROGRESS

            if GEN_PROGRESS.get("running") and GEN_PROGRESS.get("phase") not in (
                "retarget",
                "starting",
            ):
                prog = client.progress(timeout=2.0) or {}
                if not prog.get("running"):
                    GEN_PROGRESS.update(running=False, phase="", step=0, total=0)
        except Exception:
            pass
        with _status_lock:
            _status_cache = result
            _status_time = time.time()
            _status_refreshing = False
        # Hop back to the main thread to request the redraw (never touch bpy here).
        try:
            bpy.app.timers.register(_tag_view3d_redraw, first_interval=0.01)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def _cached_status(url: str) -> dict | None:
    """Return the last known /health dict immediately (may be stale/None); refresh
    in the background if the cache has aged out. NEVER blocks the draw thread."""
    with _status_lock:
        age = time.time() - _status_time
        cache = _status_cache
    if age > _STATUS_MAX_AGE:
        _refresh_status_async(url)
    return cache


def _detect_preset(arm_obj) -> str:
    """Safe wrapper around skeleton_detect.detect_skeleton_preset."""
    try:
        from ..retarget import skeleton_detect

        result = skeleton_detect.detect_skeleton_preset(arm_obj)
        return result or "unknown"
    except Exception:
        return "unknown"


class KIMODO_PT_main(Panel):
    bl_label = "Kimodo 动作生成"
    bl_idname = "KIMODO_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kimodo"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # ── Server status ──
        from .operators import GEN_PROGRESS

        url = get_server_url()
        status = _cached_status(url)
        server_alive = bool(status and status.get("status") == "ok")
        box = layout.box()
        row = box.row()
        if server_alive:
            row.label(text="服务器: 在线", icon="CHECKMARK")
            row.operator("kimodo.stop_server", text="", icon="CANCEL")
            # ── Model load/unload toggle (manual, below the online row) ──
            trow = box.row()
            if GEN_PROGRESS.get("running") and GEN_PROGRESS.get("phase") == "loading":
                trow.enabled = False
                trow.label(text="模型加载中…", icon="SORTTIME")
            elif status.get("model_loaded"):
                trow.operator("kimodo.unload_model", text="卸载模型", icon="TRASH")
            else:
                trow.operator("kimodo.load_model", text="加载模型进内存", icon="IMPORT")
        else:
            row.label(text="服务器: 离线", icon="ERROR")
            row.operator("kimodo.start_server", text="", icon="PLAY")

        # ── Target armature status ──
        box = layout.box()
        box.label(text="目标角色", icon="ARMATURE_DATA")
        target = context.active_object
        if target is None or target.type != "ARMATURE":
            box.label(text="请选中一个骨架 (Armature)", icon="ERROR")
        elif len(target.data.bones) < 15:
            box.label(
                text=f"骨架骨骼过少 ({len(target.data.bones)}): 非人形 rig?",
                icon="ERROR",
            )
        else:
            preset = _detect_preset(target)
            row = box.row()
            row.label(text=f"{target.name}", icon="OUTLINER_OB_ARMATURE")
            row.label(text=f"{len(target.data.bones)} 骨骼")
            if preset == "unknown":
                box.label(text="骨骼命名未识别，需手动选预设", icon="QUESTION")
            else:
                box.label(text=f"自动识别: {preset}", icon="CHECKMARK")
            box.prop(scene, "kimodo_retarget_preset", text="映射预设")

        # ── Generation parameters ──
        box = layout.box()
        box.label(text="生成参数", icon="MOD_WAVE")
        box.prop(scene, "kimodo_prompt", text="")
        # 翻译模式指示 + 上次翻译结果
        prefs = get_prefs()
        note = getattr(scene, "kimodo_translation_note", "") or ""
        if prefs.translate_mode != "OFF":
            mode_label = {
                "DICT": "词典",
                "API": f"AI ({prefs.translate_provider})",
            }.get(prefs.translate_mode, prefs.translate_mode)
            box.label(text=f"中文翻译: {mode_label}", icon="OUTLINER_DATA_FONT")
        if note:
            icon = "ERROR" if note.startswith("warning") else "CHECKMARK"
            box.label(text=note, icon=icon)
        # 时长（Kimodo 官方 2-10s @ 30fps；帧数后台自动算）
        row = box.row(align=True)
        row.prop(scene, "kimodo_duration")
        row.label(text=f"({scene.kimodo_num_frames} 帧)")
        row = box.row(align=True)
        row.prop(scene, "kimodo_num_samples")
        row.prop(scene, "kimodo_diffusion_steps")
        box.prop(scene, "kimodo_seed")

        # ── Generate: progress bar while running, button otherwise ──
        if GEN_PROGRESS.get("running"):
            phase = GEN_PROGRESS.get("phase", "")
            step = GEN_PROGRESS.get("step", 0) or 0
            total = GEN_PROGRESS.get("total", 0) or 0
            col = layout.column()
            col.scale_y = 1.5
            if phase in ("starting", "loading") or total <= 0:
                # Model load / pre-sampling / retarget: no step count to show yet.
                label = {
                    "starting": "正在启动…",
                    "loading": "加载模型到设备…",
                    "retarget": "重定向到骨架…",
                }.get(phase, "生成中…")
                col.progress(factor=0.0, text=label, type="BAR")
            else:
                frac = max(0.0, min(1.0, step / total))
                col.progress(factor=frac, text=f"扩散 {step}/{total}", type="BAR")
        else:
            sub = layout.column()
            sub.scale_y = 1.5
            sub.operator("kimodo.generate", icon="MOD_WAVE")
            # Show why button is disabled (if poll fails)
            if not (
                target and target.type == "ARMATURE" and len(target.data.bones) >= 15
            ):
                sub.enabled = False


class KIMODO_PT_actions(Panel):
    bl_label = "已生成的 Action"
    bl_idname = "KIMODO_PT_actions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kimodo"
    bl_parent_id = "KIMODO_PT_main"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == "ARMATURE"

    def draw(self, context):
        layout = self.layout
        arm = context.active_object

        kimodo_actions = [a for a in bpy.data.actions if a.name.startswith("Kimodo_")]
        if not kimodo_actions:
            layout.label(text="还没有生成过 Action", icon="INFO")
            return

        active_action = (
            arm.animation_data.action
            if arm and arm.animation_data and arm.animation_data.action
            else None
        )

        for action in sorted(kimodo_actions, key=lambda a: a.name):
            row = layout.row(align=True)
            is_active = action == active_action
            icon = "RADIOBUT_ON" if is_active else "RADIOBUT_OFF"

            op_switch = row.operator(
                "kimodo.switch_action",
                text=action.name,
                icon=icon,
                emboss=False,
            )
            op_switch.action_name = action.name

            end_f = int(action.frame_range[1])
            row.label(text=f"{end_f}f")

            op_del = row.operator("kimodo.delete_action", text="", icon="TRASH")
            op_del.action_name = action.name


class KIMODO_PT_constraints(Panel):
    bl_label = "路径与官方约束"
    bl_idname = "KIMODO_PT_constraints"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kimodo"
    bl_parent_id = "KIMODO_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.prop(scene, "kimodo_enable_constraints")
        row = box.row(align=True)
        row.prop(scene, "kimodo_action_start_frame")
        row.prop(scene, "kimodo_auto_canonicalize", text="规范原点")
        row = box.row(align=True)
        row.prop(scene, "kimodo_post_processing")
        row.prop(scene, "kimodo_root_margin")
        row = box.row(align=True)
        row.prop(scene, "kimodo_text_cfg")
        row.prop(scene, "kimodo_constraint_cfg")

        box = layout.box()
        box.label(text="曲线路径", icon="CURVE_BEZCURVE")
        box.prop(scene, "kimodo_path_curve")
        row = box.row(align=True)
        row.prop(scene, "kimodo_path_waypoints")
        row.prop(scene, "kimodo_path_start_frame")
        row.prop(scene, "kimodo_path_end_frame")
        box.operator("kimodo.sample_curve_as_waypoints", icon="IPO_BEZIER")

        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"约束标记 ({len(scene.kimodo_motion_constraints)})", icon="EMPTY_ARROWS")
        row.operator("kimodo.preview_constraints_json", text="", icon="TEXT")
        row.operator("kimodo.clear_constraints", text="", icon="TRASH")
        quick = box.row(align=True)
        for ctype, label in (
            ("root2d", "Root"),
            ("left_hand", "L.Hand"),
            ("right_hand", "R.Hand"),
            ("left_foot", "L.Foot"),
            ("right_foot", "R.Foot"),
        ):
            op = quick.operator("kimodo.add_constraint_marker", text=label)
            op.constraint_type = ctype
        box.operator("kimodo.create_soma_proxy", icon="ARMATURE_DATA")

        for idx, item in enumerate(scene.kimodo_motion_constraints):
            row = box.row(align=True)
            row.prop(item, "enabled", text="")
            row.prop(item, "constraint_type", text="")
            row.prop(item, "frame", text="")
            row.prop(item, "marker_object", text="")
            if item.constraint_type == "root2d":
                row.prop(item, "include_heading", text="")


class KIMODO_PT_segments(Panel):
    bl_label = "多段动作"
    bl_idname = "KIMODO_PT_segments"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kimodo"
    bl_parent_id = "KIMODO_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        row = layout.row(align=True)
        row.operator("kimodo.add_motion_segment", icon="ADD")
        row.operator("kimodo.clear_motion_segments", text="", icon="TRASH")
        layout.prop(scene, "kimodo_num_transition_frames")
        for seg in scene.kimodo_motion_segments:
            box = layout.box()
            row = box.row(align=True)
            row.prop(seg, "enabled", text="")
            row.prop(seg, "start_frame")
            row.prop(seg, "end_frame")
            box.prop(seg, "prompt", text="")
            box.prop(seg, "seed")


classes = [
    KIMODO_PT_main,
    KIMODO_PT_constraints,
    KIMODO_PT_segments,
    KIMODO_PT_actions,
]
