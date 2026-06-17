"""中文 → 英文翻译：L1 瞬时词典 + L2 OpenAI 兼容 API（DeepSeek 等）。

client-side 实现，在 Blender 插件进程内运行，不依赖服务器。
API key 保存在 Blender preferences，不离开本机。

用法（插件内部，相对导入）：
    from ..translate import translate_if_needed
    final_en, note = translate_if_needed(prompt, mode="API", api_url=..., api_key=..., model=...)
"""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from typing import Optional


# ── CJK detection ────────────────────────────────────────────
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


# ── L1 瞬时动作词典（对齐 HumanML3D 训练语料）──────────────────
DICT: dict[str, str] = {
    # 移动
    "走路": "walks forward",
    "向前走": "walks forward",
    "向后走": "walks backward",
    "走": "walks",
    "行走": "walks forward",
    "散步": "walks casually",
    "快走": "walks briskly",
    "慢走": "walks slowly",
    "奔跑": "runs forward",
    "跑步": "runs forward",
    "跑": "runs",
    "快跑": "runs fast",
    "慢跑": "jogs",
    "冲刺": "sprints forward",
    "倒退": "walks backward",
    "后退": "steps backward",
    "侧移": "steps sideways",
    "左走": "walks to the left",
    "右走": "walks to the right",
    "转身": "turns around",
    # 跳跃
    "跳": "jumps up",
    "跳跃": "jumps high",
    "起跳": "jumps up",
    "高跳": "jumps high",
    "蹦": "hops up",
    "蹦跳": "hops",
    "跳绳": "jumps rope",
    "跨跳": "leaps forward",
    # 舞蹈
    "跳舞": "dances",
    "舞蹈": "dances",
    "跳街舞": "does a hip hop dance",
    "街舞": "does a hip hop dance",
    "芭蕾": "performs a ballet dance",
    "民族舞": "performs a traditional dance",
    "迪斯科": "dances disco",
    # 格斗
    "打架": "fights",
    "出拳": "punches forward",
    "挥拳": "throws a punch",
    "踢腿": "kicks forward",
    "踢": "kicks",
    "闪避": "dodges to the side",
    "阻挡": "blocks with arms",
    "防御": "blocks with arms",
    # 姿态
    "站立": "stands still",
    "站": "stands still",
    "坐下": "sits down",
    "坐着": "is sitting",
    "蹲下": "crouches down",
    "蹲": "crouches",
    "跪下": "kneels down",
    "趴下": "lies down on the ground",
    "躺下": "lies down",
    "弯腰": "bends over",
    "鞠躬": "takes a bow",
    # 上肢
    "挥手": "waves a hand",
    "挥舞": "waves",
    "招手": "waves a hand",
    "拍手": "claps hands",
    "鼓掌": "claps hands",
    "叉腰": "puts hands on hips",
    "抱臂": "crosses arms",
    "指向": "points forward",
    "点头": "nods the head",
    "摇头": "shakes the head",
    # 复合
    "走猫步": "walks on a catwalk",
    "猫步": "walks on a catwalk like a model",
    "爬行": "crawls forward",
    "攀爬": "climbs up",
    "游泳": "pretends to swim",
    "游": "pretends to swim",
    "投掷": "throws something forward",
    "扔": "throws something",
    "拉": "pulls something",
    "推": "pushes something forward",
    "举起": "lifts something up",
    "举": "lifts arms up",
}

ADV: dict[str, str] = {
    "快速": "quickly",
    "快": "quickly",
    "缓慢": "slowly",
    "慢慢": "slowly",
    "慢": "slowly",
    "轻松": "casually",
    "悠闲": "casually",
    "随意": "casually",
    "优雅": "gracefully",
    "用力": "forcefully",
    "开心地": "happily",
    "开心": "happily",
    "愤怒地": "angrily",
    "生气": "angrily",
    "紧张": "nervously",
    "激动": "excitedly",
    "疲惫地": "wearily",
    "疲惫": "wearily",
    "性感": "sensually",
}


def try_dict_match(zh: str) -> Optional[str]:
    """L1 最长匹配，命中返回 '动作 [副词]'，未命中返回 None。"""
    zh_clean = zh.strip()
    if not zh_clean:
        return None
    action_en = None
    consumed = ""
    for k in sorted(DICT.keys(), key=len, reverse=True):
        if k in zh_clean:
            action_en = DICT[k]
            consumed = k
            break
    if action_en is None:
        return None
    remaining = zh_clean.replace(consumed, "", 1)
    for k in sorted(ADV.keys(), key=len, reverse=True):
        if k in remaining:
            return f"{action_en} {ADV[k]}"
    return action_en


# ── 后处理：对齐 HumanML3D 风格 ───────────────────────────────
_APERSON_RE = re.compile(r"^\s*(?:a|the|one)\s+(?:person|man|woman|guy|human)\b", re.I)


def normalize_humanml3d(en: str, max_words: int = 20) -> str:
    text = (en or "").strip().rstrip(".")
    if not text:
        return "A person stands still"
    if not _APERSON_RE.match(text):
        text = "A person " + text[0].lower() + text[1:]
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
    return text


# ── L2: OpenAI 兼容 Chat Completions ─────────────────────────

# Prompt 精调：few-shot 引导 LLM 输出 HumanML3D 风格短句
_TRANSLATE_SYSTEM = (
    "You are a motion-prompt translator. Translate Chinese to concise English suitable for "
    "a text-to-motion model (HumanML3D style). Rules:\n"
    '1. Always start with "A person".\n'
    "2. Describe only physical body actions. Ignore emotion/scene/environment/objects unless "
    "they change the motion.\n"
    "3. Use simple verbs + optional adverb. Keep under 15 words.\n"
    "4. Output the English sentence only, no quotes, no explanation.\n\n"
    "Examples:\n"
    "优雅地走路 -> A person walks gracefully\n"
    "性感地跳舞 -> A person dances sensually\n"
    "在雨中缓缓走过 -> A person walks slowly forward\n"
    "愤怒地挥拳 -> A person throws a punch angrily\n"
    "表演一个武术动作 -> A person performs a martial arts move\n"
)


def _http_post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_translate(
    zh: str,
    api_url: str,
    api_key: str,
    model: str,
    timeout: float = 15.0,
) -> tuple[Optional[str], Optional[str]]:
    """Call OpenAI-compatible /chat/completions. Returns (english, error_msg)."""
    if not api_url or not api_key or not model:
        return None, "API 配置不完整"
    base = api_url.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = base + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user", "content": zh},
        ],
        "temperature": 0.1,
        "max_tokens": 80,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = _http_post_json(url, body, headers, timeout)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")[:200]
        except Exception:
            err_body = ""
        return None, f"HTTP {e.code}: {err_body}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    try:
        en = resp["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        return None, f"响应解析失败: {resp}"
    return en, None


def api_list_models(
    api_url: str, api_key: str, timeout: float = 15.0
) -> tuple[Optional[list[str]], Optional[str]]:
    """GET /v1/models — returns sorted list of model ids, or (None, error)."""
    if not api_url or not api_key:
        return None, "API 地址或 key 为空"
    base = api_url.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = base + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = _http_get_json(url, headers, timeout)
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")[:200]
        except Exception:
            err_body = ""
        return None, f"HTTP {e.code}: {err_body}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    # OpenAI format: {"data": [{"id": "gpt-4o-mini", ...}, ...]}
    items = resp.get("data") or resp.get("models") or []
    ids = []
    for it in items:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("name")
            if mid:
                ids.append(str(mid))
        elif isinstance(it, str):
            ids.append(it)
    if not ids:
        return None, "API 返回空列表或格式不支持"
    return sorted(set(ids)), None


# ── 顶层 API ──────────────────────────────────────────────────
def translate_if_needed(
    prompt: str,
    mode: str = "DICT",
    api_url: str = "",
    api_key: str = "",
    model: str = "",
    timeout: float = 15.0,
) -> tuple[str, Optional[str]]:
    """
    Args:
        mode: "OFF" | "DICT" | "API"
    Returns:
        (final_en_prompt, note) — note 非 None 表示做了翻译/转换
    """
    if not contains_cjk(prompt):
        return prompt, None

    if mode == "OFF":
        return prompt, "warning: 中文原文提交（翻译已关闭，Kimodo 英文效果最佳）"

    # L1
    l1 = try_dict_match(prompt)
    if l1:
        final = normalize_humanml3d(l1)
        return final, f"zh→en 词典: {final}"

    # DICT-only 模式，未命中 → 原文
    if mode == "DICT":
        return prompt, "warning: 词典未命中，原文提交（建议开启 AI 翻译）"

    # API
    en, err = api_translate(prompt, api_url, api_key, model, timeout)
    if en:
        final = normalize_humanml3d(en)
        return final, f"zh→en AI: {final}"

    # API failed → 原文
    return prompt, f"warning: API 翻译失败 ({err})，原文提交"
