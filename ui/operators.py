"""Kimodo Motion operators — generation + in-Blender retarget to selected armature."""

import logging
import os
import json
import threading

import bpy
from bpy.types import Operator

from ..preferences import get_prefs, get_server_url
from ..server import manager
from ..server.client import KimodoClient

log = logging.getLogger(__name__)


# Live generation progress, written by the modal operator below and read by the
# Kimodo panel to draw a progress bar. Mirrors the server's GET /progress, plus a
# client-side "retarget" phase for the in-Blender stage that runs after sampling.
GEN_PROGRESS = {"running": False, "phase": "", "step": 0, "total": 0}


def _tag_view3d_redraw() -> None:
    """Force the N-panel to repaint so the progress bar advances between modal ticks."""
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _watch_thread_until_done(get_thread) -> None:
    """Repaint while a worker thread runs, then clear GEN_PROGRESS when it ends.

    Uses bpy.app.timers (fires on Blender's main loop regardless of UI events or
    operator/modal state), which is far more reliable than waiting inside a modal
    operator started from a panel button — that could miss its final tick and leave
    the progress bar stuck on 'loading' forever.
    """

    def _tick():
        t = get_thread()
        if t is not None and t.is_alive():
            _tag_view3d_redraw()
            return 0.4  # keep polling
        GEN_PROGRESS.update(running=False, phase="", step=0, total=0)
        _tag_view3d_redraw()
        return None  # thread done — stop the timer

    try:
        bpy.app.timers.register(_tick, first_interval=0.3)
    except Exception:
        # Timer registration failed — fail safe by not leaving the bar stuck.
        GEN_PROGRESS.update(running=False, phase="", step=0, total=0)


# ── Server Control ──


class KIMODO_OT_start_server(Operator):
    bl_idname = "kimodo.start_server"
    bl_label = "启动服务器"
    bl_description = "启动 Kimodo 推理服务器"

    def execute(self, context):
        prefs = get_prefs()
        try:
            manager.start_server(prefs.venv_path, prefs.server_host, prefs.server_port)
            self.report({"INFO"}, "Kimodo 服务器已启动")
        except FileNotFoundError as e:
            self.report({"ERROR"}, str(e))
        except Exception as e:
            self.report({"ERROR"}, f"启动失败: {e}")
        return {"FINISHED"}


class KIMODO_OT_stop_server(Operator):
    bl_idname = "kimodo.stop_server"
    bl_label = "停止服务器"
    bl_description = "停止 Kimodo 推理服务器"

    def execute(self, context):
        manager.stop_server()
        self.report({"INFO"}, "Kimodo 服务器已停止")
        return {"FINISHED"}


# ── Translation API operators ──


class KIMODO_OT_translate_fetch_models(Operator):
    bl_idname = "kimodo.translate_fetch_models"
    bl_label = "拉取模型列表"
    bl_description = "从当前 API 地址拉取可用模型列表（/v1/models）"

    def execute(self, context):
        from ..translate import api_list_models

        prefs = get_prefs()
        if not prefs.translate_api_url or not prefs.translate_api_key:
            prefs.translate_status = "请先填 API URL 和 Key"
            self.report({"ERROR"}, prefs.translate_status)
            return {"CANCELLED"}
        models, err = api_list_models(
            prefs.translate_api_url,
            prefs.translate_api_key,
            timeout=float(prefs.translate_timeout),
        )
        if models:
            prefs.translate_model_cache = "|".join(models)
            prefs.translate_status = f"拉取成功，共 {len(models)} 个模型"
            self.report({"INFO"}, prefs.translate_status)
            return {"FINISHED"}
        prefs.translate_model_cache = ""
        prefs.translate_status = f"拉取失败: {err}"
        self.report({"ERROR"}, prefs.translate_status)
        return {"CANCELLED"}


class KIMODO_OT_translate_test(Operator):
    bl_idname = "kimodo.translate_test"
    bl_label = "测试翻译"
    bl_description = "用一句中文测试 API 连通性（向'优雅地跳舞'发请求）"

    def execute(self, context):
        from ..translate import api_translate, normalize_humanml3d

        prefs = get_prefs()
        test_zh = "优雅地跳舞"
        en, err = api_translate(
            test_zh,
            prefs.translate_api_url,
            prefs.translate_api_key,
            prefs.translate_model,
            timeout=float(prefs.translate_timeout),
        )
        if en:
            final = normalize_humanml3d(en)
            prefs.translate_status = f"OK: '{test_zh}' → {final!r}"
            self.report({"INFO"}, prefs.translate_status)
            return {"FINISHED"}
        prefs.translate_status = f"测试失败: {err}"
        self.report({"ERROR"}, prefs.translate_status)
        return {"CANCELLED"}


# ── Motion Generation + Retarget ──


def _is_mixamo_like_armature(arm_obj) -> bool:
    """Quick check: armature has enough bones to be a full humanoid rig."""
    if arm_obj is None or arm_obj.type != "ARMATURE":
        return False
    return len(arm_obj.data.bones) >= 15


class KIMODO_OT_generate(Operator):
    bl_idname = "kimodo.generate"
    bl_label = "生成并应用到选中骨架"
    bl_description = "从文字生成动作，自动 retarget 到当前选中的骨架，创建为新 Action"
    bl_options = {"REGISTER", "UNDO"}

    _thread: threading.Thread = None
    _result: dict = None
    _error: str = None
    _timer = None
    _target_arm_name: str = ""
    _prompt: str = ""
    _num_samples: int = 1
    _constraints: list = []
    _root_anchor_world = None
    _root_scale = None
    _action_start_frame: int = 0
    _segments: list = []

    @classmethod
    def poll(cls, context):
        return _is_mixamo_like_armature(context.active_object)

    def execute(self, context):
        scene = context.scene
        target_arm = context.active_object
        if not _is_mixamo_like_armature(target_arm):
            self.report({"ERROR"}, "请先选中目标角色骨架（Armature）")
            return {"CANCELLED"}

        prefs = get_prefs()
        url = get_server_url()

        # Auto-start server if needed
        if not manager.is_server_running(url):
            if prefs.auto_start_server:
                try:
                    manager.start_server(
                        prefs.venv_path, prefs.server_host, prefs.server_port
                    )
                except Exception as e:
                    self.report({"ERROR"}, f"服务器启动失败: {e}")
                    return {"CANCELLED"}
            else:
                self.report({"ERROR"}, "Kimodo 服务器未启动，请先点启动")
                return {"CANCELLED"}

        # ── zh→en translation (client-side) ──
        from ..translate import translate_if_needed

        final_prompt, note = translate_if_needed(
            scene.kimodo_prompt,
            mode=prefs.translate_mode,
            api_url=prefs.translate_api_url,
            api_key=prefs.translate_api_key,
            model=prefs.translate_model,
            timeout=float(prefs.translate_timeout),
        )
        if note:
            scene.kimodo_translation_note = note
            # ERROR/warning uses WARNING report, success uses INFO
            level = "WARNING" if note.startswith("warning") else "INFO"
            self.report({level}, note)
        else:
            scene.kimodo_translation_note = ""

        segments_payload = []
        enabled_segments = sorted(
            [seg for seg in scene.kimodo_motion_segments if seg.enabled],
            key=lambda seg: int(seg.start_frame),
        )
        if enabled_segments:
            translated_prompts = []
            expected_start = int(enabled_segments[0].start_frame)
            for seg in enabled_segments:
                start_frame = int(seg.start_frame)
                end_frame = int(seg.end_frame)
                if start_frame != expected_start:
                    self.report(
                        {"ERROR"},
                        f"多段动作必须连续: '{seg.prompt}' 应从第 {expected_start} 帧开始",
                    )
                    return {"CANCELLED"}
                frames = end_frame - start_frame + 1
                if frames < 59 or frames > 299:
                    self.report({"ERROR"}, f"分段帧数需为 59-299: {seg.prompt}")
                    return {"CANCELLED"}
                seg_prompt, _seg_note = translate_if_needed(
                    seg.prompt,
                    mode=prefs.translate_mode,
                    api_url=prefs.translate_api_url,
                    api_key=prefs.translate_api_key,
                    model=prefs.translate_model,
                    timeout=float(prefs.translate_timeout),
                )
                translated_prompts.append(seg_prompt)
                segments_payload.append({"prompt": seg_prompt, "num_frames": frames})
                expected_start = end_frame + 1
            final_prompt = " | ".join(translated_prompts)

        constraint_payload = []
        root_anchor_world = None
        root_scale = None
        action_start_frame = (
            int(enabled_segments[0].start_frame)
            if enabled_segments
            else int(scene.kimodo_action_start_frame)
        )
        if scene.kimodo_enable_constraints and scene.kimodo_motion_constraints:
            try:
                from ..retarget import constraints as kimodo_constraints
                from ..retarget.mapping import load_preset
                from ..retarget import skeleton_detect

                ui_preset = scene.kimodo_retarget_preset
                preset_name = (
                    skeleton_detect.detect_skeleton_preset(target_arm)
                    if ui_preset == "AUTO"
                    else ui_preset
                ) or "mixamo"
                build = kimodo_constraints.build_constraints_json(
                    scene.kimodo_motion_constraints,
                    scene,
                    target_arm=target_arm,
                    bone_mapping=load_preset(preset_name),
                    action_start_frame=action_start_frame,
                    auto_canonicalize=bool(scene.kimodo_auto_canonicalize),
                )
                constraint_payload = build.constraints
                if constraint_payload:
                    root_anchor_world = tuple(build.anchor_world) if build.anchor_world else None
                    root_scale = float(build.root_scale)
                    scene.kimodo_constraint_json_preview = json.dumps(
                        constraint_payload,
                        indent=2,
                    )
            except Exception as e:
                self.report({"ERROR"}, f"约束构建失败: {e}")
                return {"CANCELLED"}

        cls = KIMODO_OT_generate
        cls._result = None
        cls._error = None
        cls._target_arm_name = target_arm.name
        cls._prompt = final_prompt  # used later for Action naming
        cls._num_samples = int(scene.kimodo_num_samples)
        cls._constraints = constraint_payload
        cls._root_anchor_world = root_anchor_world
        cls._root_scale = root_scale
        cls._action_start_frame = action_start_frame
        cls._segments = segments_payload

        cls._thread = threading.Thread(
            target=cls._run_generation,
            args=(
                url,
                final_prompt,  # translated prompt sent to Kimodo
                scene.kimodo_duration,
                int(scene.kimodo_num_frames),  # explicit frame count (authoritative)
                prefs.model_name,
                int(scene.kimodo_num_samples),
                int(scene.kimodo_seed),
                int(scene.kimodo_diffusion_steps),
                constraint_payload,
                bool(scene.kimodo_post_processing and constraint_payload),
                float(scene.kimodo_text_cfg),
                float(scene.kimodo_constraint_cfg),
                int(scene.kimodo_num_transition_frames),
                float(scene.kimodo_root_margin),
                segments_payload,
            ),
            daemon=True,
        )
        cls._thread.start()

        GEN_PROGRESS.update(running=True, phase="starting", step=0, total=0)
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, f"生成中: {final_prompt}")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        cls = KIMODO_OT_generate
        if cls._thread and cls._thread.is_alive():
            # Poll the server's diffusion progress for the panel bar (localhost GET,
            # ~ms; failures are non-fatal — the bar just won't advance this tick).
            p = KimodoClient(get_server_url()).progress(timeout=1.0)
            if p:
                GEN_PROGRESS.update(
                    running=True,
                    phase=p.get("phase", "") or "sampling",
                    step=int(p.get("step", 0) or 0),
                    total=int(p.get("total", 0) or 0),
                )
            _tag_view3d_redraw()
            return {"PASS_THROUGH"}

        # Thread finished — stop timer, run retarget stage synchronously
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        # Sampling done; show the in-Blender retarget stage in the bar.
        GEN_PROGRESS.update(running=True, phase="retarget", step=0, total=0)
        _tag_view3d_redraw()

        try:
            return self._apply_result(context, cls)
        finally:
            # Whatever the outcome, clear the bar and repaint so the button returns.
            GEN_PROGRESS.update(running=False, phase="", step=0, total=0)
            _tag_view3d_redraw()

    def _apply_result(self, context, cls):
        """Stage 2 (main thread): validate the NPZ, retarget onto the armature, and
        set the scene frame range. Returns a Blender operator status set. GEN_PROGRESS
        is cleared by modal()'s finally regardless of which branch returns."""
        if cls._error:
            self.report({"ERROR"}, f"生成失败: {cls._error}")
            return {"CANCELLED"}

        if not (cls._result and "npz_path" in cls._result):
            self.report({"ERROR"}, "服务器未返回 NPZ 路径")
            return {"CANCELLED"}

        npz_path = cls._result["npz_path"]
        if not os.path.isfile(npz_path):
            self.report({"ERROR"}, f"NPZ 文件未找到: {npz_path}")
            return {"CANCELLED"}

        # ── Stage 2: retarget ──
        target_arm = bpy.data.objects.get(cls._target_arm_name)
        if target_arm is None or target_arm.type != "ARMATURE":
            self.report({"ERROR"}, f"目标骨架丢失: {cls._target_arm_name}")
            return {"CANCELLED"}

        try:
            num_samples = int(cls._result.get("num_samples") or cls._num_samples)
            actions = self._retarget_and_apply(
                context,
                target_arm,
                npz_path,
                cls._prompt,
                num_samples,
                action_start_frame=cls._action_start_frame,
                root_anchor_world=cls._root_anchor_world,
                root_scale=cls._root_scale,
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.report({"ERROR"}, f"重定向失败: {e}")
            return {"CANCELLED"}

        if not actions:
            self.report({"ERROR"}, "重定向未生成任何 Action")
            return {"CANCELLED"}

        # Set scene frame range + fps from first action (Kimodo 固定 30 fps)
        first_action = actions[0]
        fr = first_action.frame_range
        context.scene.frame_start = int(fr[0])
        context.scene.frame_end = int(fr[1])
        context.scene.frame_current = int(fr[0])
        context.scene.render.fps = 30
        context.scene.render.fps_base = 1.0

        msg = (
            f"完成: {len(actions)} 个 Action 已应用到 {target_arm.name}"
            if len(actions) > 1
            else f"完成: Action '{first_action.name}' 已应用到 {target_arm.name}"
        )
        for warning in cls._result.get("warnings") or []:
            self.report({"WARNING"}, str(warning))
        self.report({"INFO"}, msg)
        return {"FINISHED"}

    def cancel(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
        GEN_PROGRESS.update(running=False, phase="", step=0, total=0)
        _tag_view3d_redraw()

    @staticmethod
    def _run_generation(
        url,
        prompt,
        duration,
        num_frames,
        model,
        num_samples,
        seed,
        steps,
        constraints,
        post_processing,
        text_cfg,
        constraint_cfg,
        num_transition_frames,
        root_margin,
        segments,
    ):
        """Background thread — NO bpy access here."""
        client = KimodoClient(url)
        try:
            KIMODO_OT_generate._result = client.generate(
                prompt=prompt,
                duration=duration,
                num_frames=num_frames,
                model=model,
                num_samples=num_samples,
                seed=seed,
                diffusion_steps=steps,
                output_bvh=False,
                constraints=constraints,
                post_processing=post_processing,
                text_cfg=text_cfg,
                constraint_cfg=constraint_cfg,
                num_transition_frames=num_transition_frames,
                root_margin=root_margin,
                segments=segments,
            )
        except Exception as e:
            KIMODO_OT_generate._error = str(e)

    @staticmethod
    def _retarget_and_apply(
        context,
        target_arm,
        npz_path,
        prompt,
        num_samples,
        action_start_frame=0,
        root_anchor_world=None,
        root_scale=None,
    ):
        """Run retarget loop: in-Blender bpy retarget → N Actions on target_arm.

        Cross-platform (incl. Apple Silicon / Metal): no Autodesk FBX SDK and no venv
        subprocess — the motion is applied directly from the NPZ inside Blender. See
        retarget/bpy_retarget.py. The legacy fbxsdkpy path (fbx_bridge/fbx_runner) is
        kept in the tree for reference but excluded from the shipped extension.
        """
        from ..retarget import bpy_retarget, skeleton_detect
        from ..retarget.mapping import load_preset

        scene = context.scene

        # Determine mapping preset (UI override or auto-detect)
        ui_preset = scene.kimodo_retarget_preset
        if ui_preset == "AUTO":
            preset_name = skeleton_detect.detect_skeleton_preset(target_arm)
            if preset_name is None:
                log.warning(
                    "骨架预设自动识别失败，默认使用 mixamo。骨骼示例: %s",
                    [b.name for b in target_arm.data.bones[:5]],
                )
                preset_name = "mixamo"
        else:
            preset_name = ui_preset

        log.info("Using bone mapping preset: %s", preset_name)
        bone_mapping = load_preset(preset_name)

        safe_prompt = skeleton_detect.sanitize_name(prompt)
        created_actions = []

        for i in range(num_samples):
            action_name = f"Kimodo_{safe_prompt}_s{i + 1:02d}"
            action = bpy_retarget.retarget_sample(
                target_arm=target_arm,
                npz_path=npz_path,
                bone_mapping=bone_mapping,
                sample_index=i,
                action_name=action_name,
                with_root=True,
                action_start_frame=action_start_frame,
                root_anchor_world=root_anchor_world,
                root_scale=root_scale,
            )
            created_actions.append(action)

        # First sample stays active on the rig; the rest are fake-user orphans
        if created_actions and target_arm.animation_data:
            target_arm.animation_data.action = created_actions[0]

        return created_actions


# ── Constraint authoring ──


def _unique_object_name(base: str) -> str:
    if base not in bpy.data.objects:
        return base
    i = 1
    while f"{base}_{i:02d}" in bpy.data.objects:
        i += 1
    return f"{base}_{i:02d}"


class KIMODO_OT_sample_curve_as_waypoints(Operator):
    bl_idname = "kimodo.sample_curve_as_waypoints"
    bl_label = "采样曲线路径"
    bl_description = "将 Bezier/NURBS 曲线按弧长采样为 Root XZ 约束路点"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        curve = scene.kimodo_path_curve
        if curve is None or curve.type != "CURVE":
            self.report({"ERROR"}, "请先选择路径曲线")
            return {"CANCELLED"}
        if scene.kimodo_path_start_frame >= scene.kimodo_path_end_frame:
            self.report({"ERROR"}, "路径结束帧必须大于开始帧")
            return {"CANCELLED"}

        from ..retarget.constraints import sample_curve_arc_length

        depsgraph = context.evaluated_depsgraph_get()
        positions = sample_curve_arc_length(
            curve,
            int(scene.kimodo_path_waypoints),
            depsgraph,
        )
        if not positions:
            self.report({"ERROR"}, "曲线没有可采样点")
            return {"CANCELLED"}

        # Replace previous path-generated waypoints so resampling is deterministic.
        remove_indices = []
        for idx, item in enumerate(scene.kimodo_motion_constraints):
            obj = item.marker_object
            if obj and obj.get("kimodo_path_waypoint"):
                bpy.data.objects.remove(obj, do_unlink=True)
                remove_indices.append(idx)
        for idx in reversed(remove_indices):
            scene.kimodo_motion_constraints.remove(idx)

        saved_active = context.active_object
        saved_selected = list(context.selected_objects)
        bpy.ops.object.select_all(action="DESELECT")

        start_f = int(scene.kimodo_path_start_frame)
        end_f = int(scene.kimodo_path_end_frame)
        count = len(positions)
        for i, pos in enumerate(positions):
            frame = round(start_f + (end_f - start_f) * i / max(count - 1, 1))
            bpy.ops.object.empty_add(type="ARROWS", location=pos)
            empty = context.active_object
            empty.name = _unique_object_name(f"Kimodo_Path_{i + 1:02d}")
            empty.empty_display_size = 0.15
            empty.show_name = True
            empty["kimodo_constraint"] = True
            empty["kimodo_path_waypoint"] = True

            item = scene.kimodo_motion_constraints.add()
            item.constraint_type = "root2d"
            item.frame = int(frame)
            item.marker_object = empty
            item.enabled = True
            item.label = empty.name

        bpy.ops.object.select_all(action="DESELECT")
        for obj in saved_selected:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if saved_active and saved_active.name in bpy.data.objects:
            context.view_layer.objects.active = saved_active

        scene.kimodo_enable_constraints = True
        scene.kimodo_action_start_frame = start_f
        self.report({"INFO"}, f"已添加 {count} 个路径路点")
        return {"FINISHED"}


class KIMODO_OT_add_constraint_marker(Operator):
    bl_idname = "kimodo.add_constraint_marker"
    bl_label = "添加约束标记"
    bl_description = "在 3D Cursor 位置添加 Kimodo 约束 Empty"
    bl_options = {"REGISTER", "UNDO"}

    constraint_type: bpy.props.StringProperty(default="root2d")

    def execute(self, context):
        scene = context.scene
        location = context.scene.cursor.location
        bpy.ops.object.empty_add(type="ARROWS", location=location)
        empty = context.active_object
        empty.name = _unique_object_name(f"Kimodo_{self.constraint_type}_{scene.frame_current}")
        empty.empty_display_size = 0.18
        empty.show_name = True
        empty["kimodo_constraint"] = True

        item = scene.kimodo_motion_constraints.add()
        item.constraint_type = self.constraint_type
        item.frame = int(scene.frame_current)
        item.marker_object = empty
        item.enabled = True
        item.label = empty.name
        scene.kimodo_constraint_index = len(scene.kimodo_motion_constraints) - 1
        scene.kimodo_enable_constraints = True
        self.report({"INFO"}, f"已添加约束: {empty.name}")
        return {"FINISHED"}


class KIMODO_OT_clear_constraints(Operator):
    bl_idname = "kimodo.clear_constraints"
    bl_label = "清空约束"
    bl_description = "清除 Kimodo 约束列表和由插件创建的约束 Empty"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        for item in scene.kimodo_motion_constraints:
            obj = item.marker_object
            if obj and obj.get("kimodo_constraint"):
                bpy.data.objects.remove(obj, do_unlink=True)
        scene.kimodo_motion_constraints.clear()
        scene.kimodo_constraint_json_preview = ""
        self.report({"INFO"}, "Kimodo 约束已清空")
        return {"FINISHED"}


class KIMODO_OT_preview_constraints_json(Operator):
    bl_idname = "kimodo.preview_constraints_json"
    bl_label = "预览约束 JSON"
    bl_description = "构建并缓存当前 Kimodo 官方约束 JSON"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        target_arm = context.active_object if context.active_object and context.active_object.type == "ARMATURE" else None
        try:
            from ..retarget import constraints as kimodo_constraints
            from ..retarget.mapping import load_preset
            from ..retarget import skeleton_detect

            preset_name = scene.kimodo_retarget_preset
            if preset_name == "AUTO" and target_arm:
                preset_name = skeleton_detect.detect_skeleton_preset(target_arm) or "mixamo"
            elif preset_name == "AUTO":
                preset_name = "mixamo"
            result = kimodo_constraints.build_constraints_json(
                scene.kimodo_motion_constraints,
                scene,
                target_arm=target_arm,
                bone_mapping=load_preset(preset_name),
                action_start_frame=int(scene.kimodo_action_start_frame),
                auto_canonicalize=bool(scene.kimodo_auto_canonicalize),
            )
            scene.kimodo_constraint_json_preview = json.dumps(result.constraints, indent=2)
            self.report({"INFO"}, f"约束 JSON: {len(result.constraints)} 个 block")
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"约束 JSON 构建失败: {e}")
            return {"CANCELLED"}


class KIMODO_OT_create_soma_proxy(Operator):
    bl_idname = "kimodo.create_soma_proxy"
    bl_label = "创建 SOMA 约束骨架"
    bl_description = "创建用于 fullbody/手脚约束的 SOMA proxy armature"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..retarget.constraints import create_soma_proxy_armature

        arm = create_soma_proxy_armature(context)
        context.view_layer.objects.active = arm
        self.report({"INFO"}, f"已创建 {arm.name}")
        return {"FINISHED"}


class KIMODO_OT_add_motion_segment(Operator):
    bl_idname = "kimodo.add_motion_segment"
    bl_label = "添加动作分段"
    bl_description = "按当前 prompt 和帧范围添加一个 multi-prompt 分段"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        seg = scene.kimodo_motion_segments.add()
        if len(scene.kimodo_motion_segments) > 1:
            start_frame = max(
                int(scene.kimodo_motion_segments[i].end_frame)
                for i in range(len(scene.kimodo_motion_segments) - 1)
            ) + 1
        else:
            start_frame = int(scene.kimodo_action_start_frame)
        seg.prompt = scene.kimodo_prompt
        seg.start_frame = start_frame
        seg.end_frame = start_frame + int(scene.kimodo_num_frames) - 1
        seg.enabled = True
        seg.seed = int(scene.kimodo_seed)
        scene.kimodo_segment_index = len(scene.kimodo_motion_segments) - 1
        self.report({"INFO"}, "已添加动作分段")
        return {"FINISHED"}


class KIMODO_OT_clear_motion_segments(Operator):
    bl_idname = "kimodo.clear_motion_segments"
    bl_label = "清空动作分段"
    bl_description = "清空 multi-prompt 动作分段"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.kimodo_motion_segments.clear()
        self.report({"INFO"}, "动作分段已清空")
        return {"FINISHED"}


# ── Action management (for KIMODO_PT_actions panel) ──


class KIMODO_OT_switch_action(Operator):
    bl_idname = "kimodo.switch_action"
    bl_label = "切换到此 Action"
    bl_description = "把选中的 Kimodo Action 设为当前激活"
    bl_options = {"REGISTER", "UNDO"}

    action_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == "ARMATURE"

    def execute(self, context):
        arm = context.active_object
        action = bpy.data.actions.get(self.action_name)
        if action is None:
            self.report({"ERROR"}, f"Action 不存在: {self.action_name}")
            return {"CANCELLED"}
        if arm.animation_data is None:
            arm.animation_data_create()
        arm.animation_data.action = action
        arm.animation_data.use_nla = False
        fr = action.frame_range
        context.scene.frame_start = int(fr[0])
        context.scene.frame_end = int(fr[1])
        self.report({"INFO"}, f"已切换到 {self.action_name}")
        return {"FINISHED"}


class KIMODO_OT_delete_action(Operator):
    bl_idname = "kimodo.delete_action"
    bl_label = "删除 Action"
    bl_description = "从 Blender 数据中删除此 Action"
    bl_options = {"REGISTER", "UNDO"}

    action_name: bpy.props.StringProperty()

    def execute(self, context):
        action = bpy.data.actions.get(self.action_name)
        if action is None:
            self.report({"WARNING"}, f"Action 已不存在: {self.action_name}")
            return {"CANCELLED"}

        # If currently active on any armature, clear it
        for obj in bpy.data.objects:
            if obj.animation_data and obj.animation_data.action == action:
                obj.animation_data.action = None

        bpy.data.actions.remove(action)
        self.report({"INFO"}, f"已删除 {self.action_name}")
        return {"FINISHED"}


# ── Unload ──


class KIMODO_OT_load_model(Operator):
    bl_idname = "kimodo.load_model"
    bl_label = "加载模型进内存"
    bl_description = "把 Kimodo 模型加载到设备内存（首次约 40-60s）。加载后生成无需再等加载。"

    _thread: threading.Thread = None
    _error: str = None

    def execute(self, context):
        prefs = get_prefs()
        url = get_server_url()

        # Auto-start server if needed (same policy as generate).
        if not manager.is_server_running(url):
            if prefs.auto_start_server:
                try:
                    manager.start_server(prefs.venv_path, prefs.server_host, prefs.server_port)
                except Exception as e:
                    self.report({"ERROR"}, f"服务器启动失败: {e}")
                    return {"CANCELLED"}
            else:
                self.report({"ERROR"}, "Kimodo 服务器未启动，请先点启动")
                return {"CANCELLED"}

        cls = KIMODO_OT_load_model
        cls._error = None
        model = prefs.model_name

        def _worker(u, m):
            try:
                KimodoClient(u).load_model(m)
            except Exception as e:
                KIMODO_OT_load_model._error = str(e)

        cls._thread = threading.Thread(target=_worker, args=(url, model), daemon=True)
        cls._thread.start()

        # Drive the progress bar + clear it via a reliable app timer instead of a
        # modal: the operator finishes immediately and the bar self-clears when the
        # background load completes, even with no further UI events.
        GEN_PROGRESS.update(running=True, phase="loading", step=0, total=0)
        _watch_thread_until_done(lambda: KIMODO_OT_load_model._thread)
        self.report({"INFO"}, f"加载模型: {model}（约 40-60s，完成后按钮变为卸载）")
        return {"FINISHED"}


class KIMODO_OT_unload_model(Operator):
    bl_idname = "kimodo.unload_model"
    bl_label = "卸载模型"
    bl_description = "把 Kimodo 模型从设备内存中卸载，释放显存/内存"

    def execute(self, context):
        client = KimodoClient(get_server_url())
        try:
            resp = client.unload_model()
            self.report({"INFO"}, f"模型已卸载: {resp.get('message', 'ok')}")
        except Exception as e:
            self.report({"ERROR"}, f"卸载失败: {e}")
        return {"FINISHED"}


classes = [
    KIMODO_OT_start_server,
    KIMODO_OT_stop_server,
    KIMODO_OT_translate_fetch_models,
    KIMODO_OT_translate_test,
    KIMODO_OT_generate,
    KIMODO_OT_sample_curve_as_waypoints,
    KIMODO_OT_add_constraint_marker,
    KIMODO_OT_clear_constraints,
    KIMODO_OT_preview_constraints_json,
    KIMODO_OT_create_soma_proxy,
    KIMODO_OT_add_motion_segment,
    KIMODO_OT_clear_motion_segments,
    KIMODO_OT_switch_action,
    KIMODO_OT_delete_action,
    KIMODO_OT_load_model,
    KIMODO_OT_unload_model,
]
