"""Kimodo Motion — Runtime installer panel (in-Blender UI).

Philosophy:
    - Blender UI only SHOWS state and TRIGGERS actions.
    - All heavy work (subprocess, network, logging) is in installer/*.
    - We refresh state via precheck.py run every N seconds or on button
      click — no blocking calls from draw().

Presented when:
    - User enables addon and venv/torch/kimodo/fbxsdkpy missing
    - From "工具" panel as "Install Runtime" button

UI elements:
    - Status box (GPU / venv / torch / kimodo / fbxsdkpy / model / HF token)
    - "一键安装" button (opens PowerShell window with install.ps1)
    - "下载模型" button (runs download_model.py in venv)
    - "重新检查" button (re-runs precheck)
    - "查看日志" (opens install.log in explorer)
    - Proxy input (writes scene / preference)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel

# ─── State cache (module-level, read-only from draw()) ────────────
# Key used on WindowManager so panels can react to updates.
_PRECHECK_CACHE: dict[str, Any] = {}
_PRECHECK_LAST_TS: float = 0.0
_PRECHECK_LOCK = threading.Lock()


def _addon_dir() -> Path:
    """Return the kimodo_motion/ root on disk."""
    return Path(__file__).resolve().parent.parent


def _installer_dir() -> Path:
    return _addon_dir() / "installer"


def _runtime_dir() -> Path:
    if sys.platform == "darwin":
        try:
            from ..preferences import default_venv_path, get_prefs

            venv_path = get_prefs().venv_path or default_venv_path()
        except Exception:  # noqa: BLE001
            from ..preferences import default_venv_path

            venv_path = default_venv_path()
        return Path(venv_path).expanduser().parent / "runtime"
    return Path.home() / ".kimodo_runtime"


def _install_log_path() -> Path:
    return _runtime_dir() / "install.log"


# ─── Precheck runner (thread-safe) ────────────────────────────────


def _run_precheck_sync() -> dict[str, Any]:
    """Run precheck.py in the VENV interpreter if available, else in the
    current (Blender) interpreter WITHOUT probing venv internals.

    Returns empty dict on error so draw() never crashes.
    """
    precheck = _installer_dir() / "precheck.py"
    if not precheck.is_file():
        return {
            "errors": [f"precheck.py missing at {precheck}"],
            "next_action": "run_install",
        }

    from ..preferences import default_venv_path, get_prefs  # lazy to avoid import-time cycles
    from ..server import manager  # cross-platform venv python (bin/python on macOS)

    try:
        venv_path = get_prefs().venv_path
    except Exception:  # noqa: BLE001
        venv_path = default_venv_path()
    venv_py = Path(manager.get_venv_python(venv_path))

    # Prefer running INSIDE the venv so we can introspect torch/kimodo (and MPS on macOS).
    interp = str(venv_py) if venv_py.is_file() else sys.executable

    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW
        p = subprocess.run(
            [interp, str(precheck), "--venv", venv_path],
            capture_output=True,
            text=True,
            timeout=45,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        if p.returncode != 0:
            return {
                "errors": [f"precheck rc={p.returncode}: {(p.stderr or '')[:300]}"],
                "next_action": "run_install",
            }
        return json.loads(p.stdout or "{}")
    except subprocess.TimeoutExpired:
        return {"errors": ["precheck timeout (45s)"], "next_action": "run_install"}
    except Exception as e:  # noqa: BLE001
        return {"errors": [f"precheck exec error: {e}"], "next_action": "run_install"}


def _refresh_precheck_async() -> None:
    """Kick off precheck in a background thread; update module cache."""

    def _worker() -> None:
        result = _run_precheck_sync()
        with _PRECHECK_LOCK:
            global _PRECHECK_CACHE, _PRECHECK_LAST_TS
            _PRECHECK_CACHE = result
            _PRECHECK_LAST_TS = time.time()
        # Request UI redraw on main thread
        try:
            bpy.app.timers.register(_tag_redraw, first_interval=0.01)
        except Exception:  # noqa: BLE001
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _tag_redraw() -> None:
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return None  # don't reschedule


def _get_cached_precheck(max_age: float = 10.0) -> dict[str, Any]:
    """Return cached precheck, auto-refreshing if stale."""
    with _PRECHECK_LOCK:
        age = time.time() - _PRECHECK_LAST_TS
        cache = dict(_PRECHECK_CACHE)
    if age > max_age or not cache:
        _refresh_precheck_async()
    return cache


# ─── Operators ────────────────────────────────────────────────────


class KIMODO_OT_install_runtime(Operator):
    bl_idname = "kimodo.install_runtime"
    bl_label = "一键安装 Runtime"
    bl_description = (
        "打开终端窗口运行安装脚本（Windows: install.ps1 / macOS: install_mac.sh）。"
        "会装 Python venv + PyTorch + kimodo + fastapi 服务依赖。"
        "耗时 10-30 分钟，占用 5GB+ 磁盘。"
    )

    proxy: StringProperty(
        name="代理",
        description="HTTP 代理，如 http://127.0.0.1:7890（留空则不用）",
        default="",
    )
    mirror: EnumProperty(
        name="模型镜像",
        items=[
            ("auto", "自动", "优先直连 HF，失败换 hf-mirror"),
            ("hf", "HuggingFace", "直连 huggingface.co（需代理）"),
            ("hf-mirror", "HF-Mirror", "国内镜像 hf-mirror.com"),
        ],
        default="auto",
    )
    pip_mirror: EnumProperty(
        name="Pip 镜像",
        items=[
            ("auto", "自动", "优先 pypi.org，失败换清华"),
            ("pypi", "官方 PyPI", "pypi.org/simple"),
            ("tsinghua", "清华", "pypi.tuna.tsinghua.edu.cn"),
            ("aliyun", "阿里云", "mirrors.aliyun.com/pypi"),
        ],
        default="auto",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "proxy")
        layout.prop(self, "mirror")
        layout.prop(self, "pip_mirror")
        box = layout.box()
        term = "终端 (Terminal)" if sys.platform == "darwin" else "PowerShell"
        box.label(text=f"会打开新 {term} 窗口显示进度", icon="INFO")
        box.label(text="关窗 = 取消；进度同时写到 install.log", icon="CONSOLE")
        if sys.platform == "darwin":
            box.label(text="macOS: 走 Metal/MPS，无需 fbxsdkpy", icon="INFO")

    def execute(self, context):
        if sys.platform == "win32":
            script = _installer_dir() / "install.ps1"
            if not script.is_file():
                self.report({"ERROR"}, f"install.ps1 not found at {script}")
                return {"CANCELLED"}
            args = [
                "powershell.exe",
                "-NoExit",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Mirror",
                self.mirror,
                "-PipMirror",
                self.pip_mirror,
            ]
            if self.proxy.strip():
                args += ["-Proxy", self.proxy.strip()]
            try:
                subprocess.Popen(
                    args,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=str(_installer_dir()),
                )
            except Exception as e:  # noqa: BLE001
                self.report({"ERROR"}, f"无法启动 PowerShell: {e}")
                return {"CANCELLED"}

        elif sys.platform == "darwin":
            import os
            import shlex

            script = _installer_dir() / "install_mac.sh"
            if not script.is_file():
                self.report({"ERROR"}, f"install_mac.sh not found at {script}")
                return {"CANCELLED"}
            try:
                os.chmod(script, 0o755)
            except OSError:
                pass
            # Pass options to the script via env vars (cleaner than arg-quoting through osascript).
            # KIMODO_VENV anchors the install (venv + runtime + HF model cache) to the
            # addon's venv_path preference, so everything stays in one chosen folder.
            from ..preferences import get_prefs

            env_prefix = (
                f"KIMODO_VENV={shlex.quote(get_prefs().venv_path)} "
                f"KIMODO_PIP_MIRROR={shlex.quote(self.pip_mirror)} "
            )
            if self.proxy.strip():
                env_prefix += f"KIMODO_PROXY={shlex.quote(self.proxy.strip())} "
            shell_cmd = (
                f"cd {shlex.quote(str(_installer_dir()))} && "
                f"{env_prefix}bash {shlex.quote(str(script))}"
            )
            # Open Terminal.app running the command so the user sees live progress.
            esc = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
            try:
                subprocess.Popen([
                    "osascript",
                    "-e", f'tell application "Terminal" to do script "{esc}"',
                    "-e", 'tell application "Terminal" to activate',
                ])
            except Exception as e:  # noqa: BLE001
                self.report({"ERROR"}, f"无法启动 Terminal: {e}")
                return {"CANCELLED"}

        else:
            self.report({"ERROR"}, "暂不支持该平台（仅 Windows / macOS）")
            return {"CANCELLED"}

        self.report({"INFO"}, "已打开安装窗口。装完后点'重新检查'刷新状态")
        # Kick off a poller so the panel auto-refreshes every 15s during install
        _schedule_install_watcher()
        return {"FINISHED"}


class KIMODO_OT_refresh_precheck(Operator):
    bl_idname = "kimodo.refresh_precheck"
    bl_label = "重新检查"
    bl_description = "重新运行 precheck.py 刷新 Runtime 状态"

    def execute(self, context):
        _refresh_precheck_async()
        self.report({"INFO"}, "Precheck 已在后台执行…")
        return {"FINISHED"}


class KIMODO_OT_open_install_log(Operator):
    bl_idname = "kimodo.open_install_log"
    bl_label = "查看日志"
    bl_description = "在资源管理器中定位 install.log"

    def execute(self, context):
        log = _install_log_path()
        if not log.is_file():
            self.report({"WARNING"}, f"日志不存在: {log}")
            return {"CANCELLED"}
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(log)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(log)])
            else:
                subprocess.Popen(["xdg-open", str(log.parent)])
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"打不开文件位置: {e}")
            return {"CANCELLED"}
        return {"FINISHED"}


class KIMODO_OT_download_model(Operator):
    bl_idname = "kimodo.download_model"
    bl_label = "下载 Kimodo 模型"
    bl_description = (
        "在新 PowerShell 窗口运行 download_model.py。"
        "需要 HF token 才能下 LLaMA-3-8B（约 16GB）"
    )

    mirror: EnumProperty(
        name="镜像",
        items=[
            ("auto", "自动", ""),
            ("hf", "HuggingFace", ""),
            ("hf-mirror", "HF-Mirror", ""),
        ],
        default="auto",
    )
    skip_llama: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="跳过 LLaMA (仅下载 Kimodo 1.1GB 主模型)",
        default=False,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "mirror")
        layout.prop(self, "skip_llama")
        box = layout.box()
        box.label(text="LLaMA-3-8B 是 gated model", icon="LOCKED")
        box.label(text="需先 https://huggingface.co/meta-llama 申请", icon="URL")

    def execute(self, context):
        from ..preferences import get_prefs
        from ..server import manager

        prefs = get_prefs()
        venv_py = Path(manager.get_venv_python(prefs.venv_path))  # cross-platform venv python
        if not venv_py.is_file():
            self.report({"ERROR"}, f"venv python 不存在: {venv_py}")
            return {"CANCELLED"}

        script = _installer_dir() / "download_model.py"
        if not script.is_file():
            self.report({"ERROR"}, f"download_model.py 不存在: {script}")
            return {"CANCELLED"}

        extra = " --skip-llama" if self.skip_llama else ""
        try:
            if sys.platform == "win32":
                args = [
                    "powershell.exe",
                    "-NoExit",
                    "-Command",
                    f"& '{venv_py}' '{script}' --mirror {self.mirror}{extra}",
                ]
                subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)
            elif sys.platform == "darwin":
                import shlex

                shell_cmd = (
                    f"{shlex.quote(str(venv_py))} {shlex.quote(str(script))} "
                    f"--mirror {shlex.quote(self.mirror)}{extra}"
                )
                esc = shell_cmd.replace("\\", "\\\\").replace('"', '\\"')
                subprocess.Popen([
                    "osascript",
                    "-e", f'tell application "Terminal" to do script "{esc}"',
                    "-e", 'tell application "Terminal" to activate',
                ])
            else:
                subprocess.Popen([str(venv_py), str(script), "--mirror", self.mirror]
                                 + (["--skip-llama"] if self.skip_llama else []))
        except Exception as e:  # noqa: BLE001
            self.report({"ERROR"}, f"无法启动下载: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, "下载已在新窗口中开始…")
        return {"FINISHED"}


class KIMODO_OT_set_venv_to_default(Operator):
    bl_idname = "kimodo.set_venv_to_default"
    bl_label = "应用默认 venv 路径"
    bl_description = "把 venv_path 设回当前平台默认路径"

    def execute(self, context):
        from ..preferences import default_venv_path, get_prefs

        default = default_venv_path()
        get_prefs().venv_path = default
        self.report({"INFO"}, f"venv_path = {default}")
        return {"FINISHED"}


# ─── Watcher (auto-refresh every 15s during install) ──────────────


def _schedule_install_watcher() -> None:
    # Re-register a timer that refreshes precheck every 15s for 10 minutes
    state = {"count": 0}

    def tick() -> float | None:
        state["count"] += 1
        _refresh_precheck_async()
        if state["count"] >= 40:  # 40 * 15s = 10 min
            return None
        return 15.0

    try:
        bpy.app.timers.register(tick, first_interval=15.0)
    except Exception:  # noqa: BLE001
        pass


# ─── Panels ───────────────────────────────────────────────────────


class KIMODO_PT_install(Panel):
    bl_label = "Runtime 安装"
    bl_idname = "KIMODO_PT_install"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kimodo"
    bl_parent_id = "KIMODO_PT_main"
    # Auto-open when runtime is not ready; collapse once OK
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        pc = _get_cached_precheck()

        # ── Status summary ──
        box = layout.box()
        box.label(text="Runtime 状态", icon="SYSTEM")

        if not pc:
            box.label(text="检查中…", icon="SORTTIME")
            return

        def _row(
            label: str,
            ok: bool,
            detail: str = "",
            icon_ok: str = "CHECKMARK",
            icon_bad: str = "ERROR",
        ) -> None:
            r = box.row()
            r.label(text=label, icon=(icon_ok if ok else icon_bad))
            if detail:
                r.label(text=detail)

        is_mac = sys.platform == "darwin"
        gpu = pc.get("gpu")
        pyt = pc.get("pytorch")
        if gpu:
            _row(
                f"GPU: {gpu['name']}",
                True,
                f"cc={gpu['compute_cap']} drv={gpu['driver']}",
            )
        elif is_mac:
            if pyt is None or "error" in (pyt or {}):
                # torch not installed yet → MPS can't be probed. Don't cry wolf.
                _row("GPU: Apple / Metal", True, "MPS 待装 PyTorch 后检测", icon_ok="INFO")
            else:
                mps = bool(pyt.get("mps_available"))
                _row("GPU: Apple / Metal", mps, "MPS 可用" if mps else "MPS 不可用，将用 CPU")
        else:
            _row("GPU: 未检测", False, "需要 NVIDIA 驱动")

        _row("venv", pc.get("venv_ready", False), pc.get("venv_exe") or "未创建")

        if pyt and "error" not in (pyt or {}):
            # On macOS the relevant backend is MPS (Metal); on Windows it's CUDA.
            backend_ok = bool(pyt.get("mps_available") if is_mac else pyt.get("cuda_available"))
            backend = "mps" if is_mac else "cuda"
            backend_val = pyt.get("mps_available") if is_mac else pyt.get("cuda_available")
            _row("PyTorch", backend_ok, f"{pyt.get('version')} {backend}={backend_val}")
        else:
            _row("PyTorch", False, (pyt or {}).get("error", "未安装")[:40])

        # Retargeting runs inside Blender on every platform now, so the Autodesk FBX
        # SDK (fbxsdkpy) is never needed.
        _row("FBX retarget", True, "Blender 内置（无需 fbxsdkpy）")

        km = pc.get("kimodo", {})
        _row("kimodo", km.get("installed", False), km.get("version") or "未安装")

        _row(
            f"HF 模型缓存 ({km.get('cache_size_gb', 0)} GB)",
            km.get("model_cached", False),
            "已缓存" if km.get("model_cached") else "未下载",
        )

        hf = pc.get("hf_token", {})
        _row(
            "HF Token",
            hf.get("present", False),
            "已登录" if hf.get("present") else "未登录（LLaMA 下载会失败）",
        )

        free = pc.get("disk_free_gb", 0)
        _row(
            f"磁盘剩余 {free} GB", free >= 30, "≥30GB OK" if free >= 30 else "不足 30GB"
        )

        # ── Errors / warnings ──
        for err in pc.get("errors", []):
            box.label(text=err, icon="CANCEL")
        for warn in pc.get("warnings", []):
            box.label(text=warn, icon="ERROR")

        # ── Action buttons ──
        next_action = pc.get("next_action")
        col = layout.column(align=True)

        if next_action == "ok":
            col.label(text="Runtime 就绪 ✓", icon="CHECKMARK")
        else:
            sub = col.column(align=True)
            sub.scale_y = 1.5
            sub.operator("kimodo.install_runtime", icon="IMPORT")

        row = col.row(align=True)
        row.operator("kimodo.refresh_precheck", icon="FILE_REFRESH")
        row.operator("kimodo.open_install_log", icon="TEXT")

        if next_action == "download_model":
            col.operator("kimodo.download_model", icon="URL")

        if pc.get("venv_ready") and pc.get("venv_exe"):
            # Let user 1-click apply to preferences
            from ..preferences import get_prefs

            try:
                current = get_prefs().venv_path
                from ..preferences import default_venv_path

                expected = default_venv_path()
                if os.path.normcase(os.path.normpath(current)) != os.path.normcase(
                    os.path.normpath(expected)
                ):
                    col.operator("kimodo.set_venv_to_default", icon="PREFERENCES")
            except Exception:  # noqa: BLE001
                pass


classes = [
    KIMODO_OT_install_runtime,
    KIMODO_OT_refresh_precheck,
    KIMODO_OT_open_install_log,
    KIMODO_OT_download_model,
    KIMODO_OT_set_venv_to_default,
    KIMODO_PT_install,
]
