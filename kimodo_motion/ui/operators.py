"""Kimodo Motion operators — generation + FBX SDK retarget to selected armature."""

import os
import threading

import bpy
from bpy.types import Operator

from ..preferences import get_prefs, get_server_url
from ..server import manager
from ..server.client import KimodoClient


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

        cls = KIMODO_OT_generate
        cls._result = None
        cls._error = None
        cls._target_arm_name = target_arm.name
        cls._prompt = final_prompt  # used later for Action naming
        cls._num_samples = int(scene.kimodo_num_samples)

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
            ),
            daemon=True,
        )
        cls._thread.start()

        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({"INFO"}, f"生成中: {final_prompt}")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        cls = KIMODO_OT_generate
        if cls._thread and cls._thread.is_alive():
            return {"PASS_THROUGH"}

        # Thread finished — stop timer, run retarget stage synchronously
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None

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
            actions = self._retarget_and_apply(
                context, target_arm, npz_path, cls._prompt, cls._num_samples
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

        # Optional cold unload
        prefs = get_prefs()
        if prefs.vram_mode == "COLD":
            try:
                KimodoClient(get_server_url()).unload_model()
            except Exception:
                pass

        msg = (
            f"完成: {len(actions)} 个 Action 已应用到 {target_arm.name}"
            if len(actions) > 1
            else f"完成: Action '{first_action.name}' 已应用到 {target_arm.name}"
        )
        self.report({"INFO"}, msg)
        return {"FINISHED"}

    def cancel(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)

    @staticmethod
    def _run_generation(
        url, prompt, duration, num_frames, model, num_samples, seed, steps
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
            )
        except Exception as e:
            KIMODO_OT_generate._error = str(e)

    @staticmethod
    def _retarget_and_apply(context, target_arm, npz_path, prompt, num_samples):
        """Run retarget loop: export target → N samples → import N actions."""
        from ..retarget import fbx_bridge
        from ..retarget.mapping import load_preset

        prefs = get_prefs()
        scene = context.scene
        venv_python = manager.get_venv_python(prefs.venv_path)

        # Determine mapping preset (UI override or auto-detect)
        ui_preset = scene.kimodo_retarget_preset
        if ui_preset == "AUTO":
            preset_name = fbx_bridge.detect_skeleton_preset(target_arm)
            if preset_name is None:
                # Fall back to mixamo as a best guess; log warning
                print(
                    f"[Kimodo] 骨架预设自动识别失败，默认使用 mixamo。"
                    f"骨骼示例: {[b.name for b in target_arm.data.bones[:5]]}"
                )
                preset_name = "mixamo"
        else:
            preset_name = ui_preset

        print(f"[Kimodo] Using bone mapping preset: {preset_name}")
        bone_mapping = load_preset(preset_name)

        # Export target armature to temp FBX (cached)
        target_fbx = fbx_bridge.export_target_fbx(target_arm, use_cache=True)

        # Make sure output dir exists
        os.makedirs(fbx_bridge.TEMP_OUTPUTS_DIR, exist_ok=True)

        safe_prompt = fbx_bridge._sanitize_name(prompt)
        created_actions = []

        for i in range(num_samples):
            output_fbx = os.path.join(
                fbx_bridge.TEMP_OUTPUTS_DIR,
                f"retargeted_{safe_prompt}_s{i + 1:02d}.fbx",
            )
            fbx_bridge.run_fbx_retarget(
                venv_python=venv_python,
                npz_path=npz_path,
                target_fbx=target_fbx,
                output_fbx=output_fbx,
                bone_mapping=bone_mapping,
                sample_index=i,
                yaw_offset=0.0,
                timeout=prefs.fbx_retarget_timeout,
            )

            action_name = f"Kimodo_{safe_prompt}_s{i + 1:02d}"
            # Only assign as active on first sample; keep others as fake_user orphans
            action = fbx_bridge.import_action_from_fbx(
                fbx_path=output_fbx,
                target_arm=target_arm,
                action_name=action_name,
                assign_as_active=(i == 0),
            )
            created_actions.append(action)

            if not prefs.keep_retarget_temp:
                try:
                    os.remove(output_fbx)
                except OSError:
                    pass

        return created_actions


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


class KIMODO_OT_unload_model(Operator):
    bl_idname = "kimodo.unload_model"
    bl_label = "卸载模型"
    bl_description = "从显存中卸载 Kimodo 模型"

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
    KIMODO_OT_switch_action,
    KIMODO_OT_delete_action,
    KIMODO_OT_unload_model,
]
