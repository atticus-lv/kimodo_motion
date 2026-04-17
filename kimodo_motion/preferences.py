import bpy
from bpy.types import AddonPreferences
from bpy.props import (
    StringProperty,
    IntProperty,
    BoolProperty,
    EnumProperty,
)
import os


# ── Translation API provider presets ──
# Tuple: (id, label, base_url, default_model)
TRANSLATE_PROVIDERS = [
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
    (
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "deepseek/deepseek-chat",
    ),
    ("moonshot", "Moonshot (Kimi)", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    (
        "qwen",
        "Qwen (DashScope)",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-turbo",
    ),
    ("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("custom", "自定义", "", ""),
]


def _apply_provider_preset(prefs):
    """When user picks a provider from dropdown, auto-fill base_url + model."""
    for pid, _label, url, default_model in TRANSLATE_PROVIDERS:
        if prefs.translate_provider == pid:
            if pid != "custom" and url:
                prefs.translate_api_url = url
            if pid != "custom" and default_model and not prefs.translate_model.strip():
                prefs.translate_model = default_model
            # Auto-fill model even if blank; user can override
            if pid != "custom" and default_model:
                prefs.translate_model = default_model
            break


def _get_fetched_models_items(self, context):
    """EnumProperty items callback — parses cache string into dropdown items."""
    cache = self.translate_model_cache or ""
    if not cache:
        return [("", "(未拉取)", "先点 Fetch 拉取模型列表")]
    items = []
    for i, m in enumerate(cache.split("|")):
        m = m.strip()
        if m:
            items.append((m, m, ""))
    return items or [("", "(空)", "API 未返回任何模型")]


def _on_model_pick(self, context):
    """When user picks from dropdown, copy into the editable model field."""
    if self.translate_model_pick:
        self.translate_model = self.translate_model_pick


# ── Duration ↔ Frames 双向同步（Kimodo 官方 fps=30, 公式 frames = secs * 30 - 1）──
# 用 _syncing 哨兵避免 callback 互相触发死循环
KIMODO_FPS = 30
_syncing = False


def secs_to_frames(s: float) -> int:
    return max(59, min(299, int(round(s * KIMODO_FPS - 1))))


def frames_to_secs(f: int) -> float:
    return round((f + 1) / KIMODO_FPS, 2)


def _sync_from_duration(self, context):
    global _syncing
    if _syncing:
        return
    _syncing = True
    try:
        self.kimodo_num_frames = secs_to_frames(self.kimodo_duration)
    finally:
        _syncing = False


def _sync_from_frames(self, context):
    global _syncing
    if _syncing:
        return
    _syncing = True
    try:
        self.kimodo_duration = frames_to_secs(self.kimodo_num_frames)
    finally:
        _syncing = False


class KimodoPreferences(AddonPreferences):
    bl_idname = "kimodo_motion"

    server_host: StringProperty(
        name="服务器地址",
        default="127.0.0.1",
        description="Kimodo 服务器地址（保持 localhost 确保安全）",
    )
    server_port: IntProperty(
        name="端口",
        default=8790,
        min=1024,
        max=65535,
        description="Kimodo 服务器端口",
    )
    venv_path: StringProperty(
        name="虚拟环境路径",
        default=os.path.join(os.path.expanduser("~"), ".kimodo_venv"),
        subtype="DIR_PATH",
        description="Kimodo Python 虚拟环境路径",
    )
    auto_start_server: BoolProperty(
        name="自动启动服务器",
        default=True,
        description="生成时自动启动 Kimodo 服务器",
    )
    model_name: EnumProperty(
        name="模型",
        items=[
            (
                "Kimodo-SOMA-RP-v1",
                "SOMA RP (完整 700h)",
                "最高质量，完整 Rigplay 数据集",
            ),
            ("Kimodo-SOMA-SEED-v1", "SOMA SEED (288h)", "公开数据集"),
        ],
        default="Kimodo-SOMA-RP-v1",
        description="Kimodo 模型变体",
    )
    vram_mode: EnumProperty(
        name="显存模式",
        items=[
            ("WARM", "常驻（保持加载）", "模型留在显存中，加速下次生成"),
            (
                "COLD",
                "卸载（生成后释放）",
                "生成后卸载模型，释放显存",
            ),
        ],
        default="WARM",
        description="显存管理策略",
    )
    fbx_retarget_timeout: IntProperty(
        name="重定向超时（秒）",
        default=60,
        min=10,
        max=600,
        description="FBX 重定向 subprocess 超时（每个 sample）",
    )
    keep_retarget_temp: BoolProperty(
        name="保留临时文件",
        default=False,
        description="调试用：保留临时 FBX 和映射文件（否则生成后删除）",
    )

    # ── 中文翻译（生成时自动 zh→en）──
    translate_mode: EnumProperty(
        name="中文翻译",
        items=[
            ("OFF", "关闭", "prompt 原样提交（Kimodo 英文效果最佳）"),
            (
                "DICT",
                "仅词典（瞬时）",
                "内置 ~60 条常见动作词典，命中瞬时匹配，无 API 依赖",
            ),
            (
                "API",
                "词典 + AI（推荐）",
                "词典优先，未命中走 OpenAI 兼容 API（DeepSeek 等）",
            ),
        ],
        default="DICT",
        description="如何处理中文 prompt",
    )
    translate_provider: EnumProperty(
        name="API 预设",
        items=[
            ("deepseek", "DeepSeek", "https://api.deepseek.com/v1"),
            ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
            ("moonshot", "Moonshot (Kimi)", "https://api.moonshot.cn/v1"),
            ("qwen", "Qwen (DashScope)", "https://dashscope.aliyuncs.com"),
            ("openai", "OpenAI", "https://api.openai.com/v1"),
            ("custom", "自定义", "手动填写 Base URL"),
        ],
        default="deepseek",
        description="选择后自动填入 Base URL 和推荐模型（可 Fetch 拉取完整列表）",
        update=lambda self, ctx: _apply_provider_preset(self),
    )
    translate_api_url: StringProperty(
        name="API Base URL",
        default="https://api.deepseek.com/v1",
        description="OpenAI 兼容的 API 基础地址",
    )
    translate_api_key: StringProperty(
        name="API Key",
        default="",
        subtype="PASSWORD",
        description="从对应服务商控制台获取",
    )
    translate_model: StringProperty(
        name="模型",
        default="deepseek-chat",
        description="模型名；点 Fetch 从 API 拉取可用列表",
    )
    translate_model_cache: StringProperty(
        name="_cache",
        default="",
        options={"HIDDEN"},
        description="Fetched model list cache (| separated)",
    )
    translate_model_pick: EnumProperty(
        name="选择模型",
        items=_get_fetched_models_items,
        description="从 API 返回的模型列表里选",
        update=lambda self, ctx: _on_model_pick(self, ctx),
    )
    translate_timeout: IntProperty(
        name="API 超时 (秒)",
        default=15,
        min=3,
        max=120,
    )
    translate_status: StringProperty(
        name="_status",
        default="",
        options={"HIDDEN"},
        description="Last test/fetch status (shown in draw)",
    )

    def draw(self, context):
        layout = self.layout

        box = layout.box()
        box.label(text="服务器设置", icon="WORLD")
        row = box.row()
        row.prop(self, "server_host")
        row.prop(self, "server_port")
        box.prop(self, "auto_start_server")
        box.prop(self, "venv_path")

        box = layout.box()
        box.label(text="生成设置", icon="ARMATURE_DATA")
        box.prop(self, "model_name")
        box.prop(self, "vram_mode")

        box = layout.box()
        box.label(text="重定向设置", icon="CON_ARMATURE")
        box.prop(self, "fbx_retarget_timeout")
        box.prop(self, "keep_retarget_temp")

        box = layout.box()
        box.label(text="中文翻译", icon="OUTLINER_DATA_FONT")
        box.prop(self, "translate_mode")
        if self.translate_mode == "API":
            row = box.row()
            row.prop(self, "translate_provider")
            sub = box.column(align=True)
            sub.prop(self, "translate_api_url")
            sub.prop(self, "translate_api_key")
            row = sub.row(align=True)
            row.prop(self, "translate_model")
            row.operator(
                "kimodo.translate_fetch_models", text="Fetch", icon="FILE_REFRESH"
            )
            # Fetched-models picker (只在有缓存时显示)
            if self.translate_model_cache:
                sub.prop(self, "translate_model_pick", text="从列表选")
            row = box.row(align=True)
            row.operator("kimodo.translate_test", icon="CHECKMARK")
            row.prop(self, "translate_timeout")
            if self.translate_status:
                box.label(text=self.translate_status, icon="INFO")
            box.label(
                text="填完 API Key 后请点右下角菜单 > Save Preferences (重启不丢)",
                icon="FILE_TICK",
            )

        box = layout.box()
        box.label(text="状态", icon="INFO")
        box.label(text=f"服务器: http://{self.server_host}:{self.server_port}")


def get_prefs() -> KimodoPreferences:
    return bpy.context.preferences.addons["kimodo_motion"].preferences


def get_server_url() -> str:
    prefs = get_prefs()
    return f"http://{prefs.server_host}:{prefs.server_port}"


# ── Scene properties for generation parameters ──


def register_props():
    bpy.types.Scene.kimodo_retarget_preset = EnumProperty(
        name="骨骼映射",
        items=[
            ("AUTO", "自动匹配", "按名称自动匹配（不区分大小写）"),
            ("vroid", "VRoid", "VRoid Studio / VRM 模型"),
            ("mmd", "MMD", "MikuMikuDance 模型"),
            ("mixamo", "Mixamo", "Mixamo 骨架模型"),
        ],
        default="AUTO",
        description="重定向骨骼映射预设",
    )
    bpy.types.Scene.kimodo_prompt = StringProperty(
        name="提示词",
        default="A person walks forward.",
        description="描述要生成的动作（英文效果更好）",
    )
    # Kimodo 官方约束：fps=30 固定, duration ∈ [2.0, 10.0]s, 即 num_frames ∈ [59, 299]
    bpy.types.Scene.kimodo_length_mode = EnumProperty(
        name="时长输入",
        items=[
            ("DURATION", "按秒", "按秒输入（自动换算帧数 = 秒 × 30 − 1）"),
            ("FRAMES", "按帧数", "直接输入帧数（自动换算秒 = 帧数 ÷ 30）"),
        ],
        default="DURATION",
        description="用秒还是帧数控制动画长度",
    )
    bpy.types.Scene.kimodo_duration = bpy.props.FloatProperty(
        name="时长(秒)",
        default=6.0,
        min=2.0,
        max=10.0,
        step=10,
        precision=2,
        description="动画时长（Kimodo 官方范围 2-10 秒，默认 6s）",
        update=_sync_from_duration,
    )
    bpy.types.Scene.kimodo_num_frames = IntProperty(
        name="帧数",
        default=179,  # 6.0s × 30 − 1
        min=59,  # 2.0s × 30 − 1
        max=299,  # 10.0s × 30 − 1
        description="总帧数（Kimodo 官方 59-299，默认 179 = 约 6s @ 30fps）",
        update=_sync_from_frames,
    )
    bpy.types.Scene.kimodo_num_samples = IntProperty(
        name="变体数",
        default=1,
        min=1,
        max=8,
        description="生成多个不同的动作变体",
    )
    bpy.types.Scene.kimodo_seed = IntProperty(
        name="种子",
        default=-1,
        min=-1,
        description="随机种子（-1 = 随机）",
    )
    bpy.types.Scene.kimodo_diffusion_steps = IntProperty(
        name="扩散步数",
        default=100,
        min=10,
        max=200,
        description="去噪步数（越高质量越好，速度越慢）",
    )
    bpy.types.Scene.kimodo_translation_note = StringProperty(
        name="_translation_note",
        default="",
        description="上次生成时的翻译结果/状态（只读显示）",
    )


def unregister_props():
    props = [
        "kimodo_retarget_preset",
        "kimodo_prompt",
        "kimodo_length_mode",
        "kimodo_duration",
        "kimodo_num_frames",
        "kimodo_num_samples",
        "kimodo_seed",
        "kimodo_diffusion_steps",
        "kimodo_translation_note",
    ]
    for p in props:
        if hasattr(bpy.types.Scene, p):
            delattr(bpy.types.Scene, p)


classes = [KimodoPreferences]
