# Kimodo Motion — 安装指南

> 中文 | [English](INSTALL_EN.md) · 涵盖 **Windows (CUDA)** 与 **macOS (Metal / MPS)**

---

## 1. 系统需求

| 项 | Windows | macOS |
|----|---------|-------|
| 加速器 | NVIDIA RTX 20/30/40/50（CUDA） | Apple Silicon（Metal / MPS），无则回落 CPU |
| Blender | **5.0.1+**（4.x 加载会被拒绝） | **5.0.1+**（arm64 原生版） |
| Python（venv） | 3.10 – 3.13（脚本自动找/装） | 3.10 – 3.13（建议 `brew install python@3.11`） |
| 显存/内存 | 16 GB+ 显存 | 建议 32 GB+ 统一内存（fp16 编码器 ~16 GB） |
| 磁盘 | ~25–50 GB（venv + 模型） | ~25–50 GB |
| 驱动 | ≥ 570（cu128）或 ≥ 528（cu121） | — |
| HuggingFace | 接受 [Meta-Llama-3-8B 协议](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct)（见 §4） | 同左 |

> retarget 现在**在 Blender 内**完成（`retarget/bpy_retarget.py`），所有平台都**不再需要** Autodesk
> FBX SDK。Windows 安装器目前仍会装 fbxsdkpy（遗留，不影响生成）；macOS 安装器不装。

---

## 2. 安装插件（扩展）

`Blender > Edit > Preferences > Get Extensions / Add-ons > Install from Disk…` 选 `kimodo_motion.zip` 并启用。
插件以 Blender 扩展（`blender_manifest.toml`）形式发布，要求 Blender 5.0+。

---

## 3. 安装运行时

运行时是一个独立 Python venv（torch + kimodo + FastAPI 服务器），与 Blender 内嵌 Python 隔离。
venv、日志、模型缓存都锚定到插件偏好里的 **venv 路径**，集中在一个文件夹，删掉即彻底卸载。

### 3.1 Windows（CUDA）

1. N 面板 > Kimodo > Runtime 安装 > 点 `[一键安装 Runtime]`（填代理可选）。
2. 等 10–30 分钟，PowerShell 窗口显示进度（装 Python venv + PyTorch cu128 + kimodo + 服务依赖）。

命令行等价：

```powershell
powershell -ExecutionPolicy Bypass -File installer\install.ps1 -Proxy http://127.0.0.1:7890 -Mirror hf-mirror
```

双击启动：`installer\install.cmd`（英文名 + chcp 65001，中文路径/用户名不乱码）。

| 参数 | 说明 |
|------|------|
| `-Proxy` | `http://127.0.0.1:7890`（Clash 默认），空 = 直连 |
| `-Mirror` | `auto` → 优先 HF，失败换 hf-mirror |
| `-PipMirror` | `auto` → 优先 pypi.org，失败换清华 |

### 3.2 macOS（Metal / MPS）

**面板方式**：N 面板 > Kimodo > Runtime 安装 > `[一键安装 Runtime]` → 打开「终端」跑 `install_mac.sh`。

**命令行方式（推荐，运行时放在可见目录，删一个目录即清）**：

```bash
cd /path/to/kimodo_motion
KIMODO_VENV="$HOME/KimodoMotionRuntime/venv" bash installer/install_mac.sh
```

装好后把插件偏好里的 **venv 路径**设为 `~/KimodoMotionRuntime/venv` —— venv、日志、**以及 ~17GB 模型缓存**都会落在 `~/KimodoMotionRuntime/` 里。

**开发 / 隔离测试**：如果你想把运行时跟当前仓库绑在一起，也可以继续用：

```bash
KIMODO_VENV="$(pwd)/.kimodo-runtime/venv" bash installer/install_mac.sh
```

安装器选项（环境变量）：

| 变量 | 作用 | 默认 |
|------|------|------|
| `KIMODO_VENV` | venv 目标路径（运行时/模型缓存放它旁边） | `~/KimodoMotionRuntime/venv` |
| `KIMODO_PIP_MIRROR` | `auto` / `pypi` / `tsinghua` / `aliyun` | `auto` |
| `KIMODO_PROXY` | HTTP(S) 代理 | 空 |
| `KIMODO_GIT_URL` | kimodo git 源**或本地目录**（须含 MPS 后端） | `https://github.com/atticus-lv/kimodo.git` |
| `KIMODO_HF_HOME` | 模型缓存位置（覆盖默认的 venv 旁） | `<venv 父目录>/hf-cache` |
| `KIMODO_DEVICE` | `auto` / `mps` / `cpu` / `cuda` | `auto`（Mac→MPS） |

### 3.3 macOS 官方后处理（可选）

一键安装默认跳过 Kimodo 的 `motion_correction` 原生扩展；生成可正常运行，插件服务端会在缺少该扩展时自动跳过官方 `post_processing`。如果你想启用官方后处理（约束修正/脚滑清理），可在完成一键安装后执行：

```bash
brew install cmake simde pybind11 eigen

KIMODO_VENV="$HOME/KimodoMotionRuntime/venv"  # 或替换为插件偏好里的实际 venv 路径
"$KIMODO_VENV/bin/python" -m pip install --force-reinstall --no-deps \
  "kimodo @ git+https://github.com/atticus-lv/kimodo.git"

"$KIMODO_VENV/bin/python" -c "import motion_correction; from motion_correction import motion_postprocess; print('motion_correction OK')"
```

验证通过后，Blender 面板里的“官方后处理”开关会真正生效；如果验证失败，保持关闭即可，生成不会因此失败。

**与 Windows 的差异**：不装 fbxsdkpy；不受 Python 3.12 限制；PyTorch 来自 PyPI（arm64 轮子自带 MPS）；设备 `auto → cuda > mps > cpu`。

---

## 4. 文本编码器模型（LLaMA-3-8B，**跨平台**）

LLM2Vec 文本编码器需要 **Meta-Llama-3-8B-Instruct** 作为 base（约 16 GB）。两种获取方式，**Windows 与 macOS 通用**：

### ① 官方（默认，许可干净）

`meta-llama/Meta-Llama-3-8B-Instruct` 是 Meta 门控模型：

1. 打开 [模型页](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) → "Agree and access repository" 申请通过。
2. 创建 read token：<https://huggingface.co/settings/tokens>。
3. 在 venv 里登录后，首次生成时自动下载：
   - macOS / Linux：`<venv>/bin/hf auth login`
   - Windows：`<venv>\Scripts\hf.exe auth login`

### ② 非门控镜像（开发省事，免申请免登录）

`NousResearch/Meta-Llama-3-8B-Instruct` 是字节相同的二次上传。下载后把 LLM2Vec adapter 的 base 指过去——
**与平台无关**（仅 `hf` 路径不同）：

```bash
# macOS / Linux： HF="<venv>/bin/hf"        ；   Windows： HF="<venv>\Scripts\hf.exe"
export HF_HOME="<venv 同级目录>/hf-cache"   # 与服务器读取的缓存一致（venv 旁边）
"$HF" download NousResearch/Meta-Llama-3-8B-Instruct --exclude "original/*"
python - <<'PY'
import os, json, glob
hub = os.path.join(os.environ["HF_HOME"], "hub")
for repo in ("models--McGill-NLP--LLM2Vec-Meta-Llama-3-8B-Instruct-mntp",
             "models--McGill-NLP--LLM2Vec-Meta-Llama-3-8B-Instruct-mntp-supervised"):
    for cfg in glob.glob(os.path.join(hub, repo, "snapshots", "*", "adapter_config.json")):
        d = json.load(open(os.path.realpath(cfg)))
        if d.get("base_model_name_or_path") == "meta-llama/Meta-Llama-3-8B-Instruct":
            d["base_model_name_or_path"] = "NousResearch/Meta-Llama-3-8B-Instruct"
            if os.path.islink(cfg):
                os.unlink(cfg)
            json.dump(d, open(cfg, "w"), indent=2)
            print("patched", repo)
PY
```

> 权重本身仍受 **Meta Llama-3 Community License** 约束（许可跟着权重走，与下载源无关）。镜像仅为
> 开发便利；对外分发请走官方门控渠道。

---

## 5. 运行时结构与卸载

| 路径（以默认 macOS 安装为例） | 内容 | 大小 |
|------|------|------|
| `<venv>/` | Python venv + 所有 pip 包 | ~5 GB |
| `<venv 旁>/hf-cache/` | HuggingFace 模型缓存（Kimodo + LLaMA-3-8B） | ~17 GB |
| `<venv 旁>/runtime/install.log` | 安装日志 | <1 MB |

Windows 默认装在 `~/.kimodo_venv`，模型在 `~/.cache/huggingface`（可设 `HF_HOME` 搬家）。
macOS 默认装在 `~/KimodoMotionRuntime/venv`，模型和日志同在 `~/KimodoMotionRuntime/`。
**卸载**：删除 venv 所在的运行时文件夹即可（macOS 默认删 `~/KimodoMotionRuntime/`；项目内开发安装删 `<repo>/.kimodo-runtime/`）；
Windows 也可 `powershell -File installer\uninstall.ps1`。系统 Python 全程不被改动。

### 离线模型包（网盘预下载）

如使用预下载模型包，解压后**必须保留 HuggingFace 原生 cache tree**，不可只复制权重：

```
<HF_HOME>/hub/
├── models--nvidia--Kimodo-SOMA-RP-v1/
│   ├── refs/main              ← 必须保留（snapshot sha）
│   ├── snapshots/<sha>/       ← 完整 sha 目录（config.json + *.safetensors）
│   └── blobs/
└── models--NousResearch--Meta-Llama-3-8B-Instruct/   （或 meta-llama，按 §4 选择）
```

缺 `refs/main` 或 `snapshots/` 子目录，huggingface_hub 会判定未缓存并重下 17GB；precheck 会校验这两个目录。

---

## 6. 常见问题

**生成报 `GatedRepoError: meta-llama/...`** — 走 §4 ①（申请 + 登录），或改用 §4 ② 非门控镜像。

**PowerShell 报"脚本未签名"（Windows）** — 用 `-ExecutionPolicy Bypass`，或 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

**pip 装 torch 超时 / 卡在 x/3GB** — 检查带宽/防火墙/代理；带 `-Proxy`（Win）或 `KIMODO_PROXY`（Mac）重试。重跑是幂等的，不会重下已完成的大包。

**CUDA 不可用（Windows，`cuda_available=False`）** — torch 版本 ≠ 驱动。`nvidia-smi` 看 CUDA Version；RTX 50 系（cc ≥ 12.0）必须 cu128；重装 `pip install torch ... --index-url https://download.pytorch.org/whl/cu128 --force-reinstall`。

**macOS 显示 MPS 不可用** — 需 Apple Silicon + arm64 原生 Blender/Python；否则回落 CPU（慢）。可 `KIMODO_DEVICE=cpu` 显式走 CPU。

**kimodo 在 arm64 编译失败（MotionCorrection / cmake）** — 一键安装默认 `SKIP_MOTION_CORRECTION_IN_SETUP=1` 跳过该可选扩展。若要启用官方后处理，按 §3.3 安装 `cmake simde pybind11 eigen` 后重新安装 kimodo；否则插件服务端会自动跳过 `post_processing`，避免生成失败。

**磁盘不够 / 想搬模型** — 设 `HF_HOME=<目标盘>/hf-cache`（所有 HF 工具会认；macOS 项目内安装默认已在 venv 旁）。

**故障排查工作流** — N 面板 > Runtime 安装 > `[重新检查]` 看哪项不绿 > `[查看日志]` 打开 `install.log` > 把最后 100 行 + precheck 输出贴出来。

---

## 7. 手工安装（高级 / Linux）

```bash
# 1. venv
python3.11 -m venv ~/.kimodo_venv
source ~/.kimodo_venv/bin/activate            # Windows: .\.kimodo_venv\Scripts\Activate.ps1

# 2. PyTorch（macOS/Linux: 默认 PyPI 即含 MPS/CPU；Windows CUDA 用 --index-url cu128）
pip install torch torchvision torchaudio      # Windows: --index-url https://download.pytorch.org/whl/cu128

# 3. kimodo（MPS fork；默认跳过可选 motion_correction 后处理扩展）
SKIP_MOTION_CORRECTION_IN_SETUP=1 pip install "kimodo @ git+https://github.com/atticus-lv/kimodo.git"

# 4. 服务器依赖
pip install fastapi "uvicorn[standard]" pydantic scipy bvhio requests

# 5. 登录 HF（或按 §4 ② 用非门控镜像）
python -m huggingface_hub.commands.huggingface_cli login
```

---

## 8. 版本记录

- **2026-06 v2.0：macOS / Apple Silicon（Metal/MPS）支持 + 重构**
  - retarget 改为 **Blender 内完成**（`retarget/bpy_retarget.py`），所有平台不再依赖 Autodesk FBX SDK
  - 新增 `install_mac.sh`（venv + PyTorch/MPS + kimodo + 服务依赖，无 fbxsdkpy）
  - 服务器设备解析 `auto → cuda > mps > cpu`（`KIMODO_DEVICE` 可覆盖）
  - 运行时容器化：macOS 默认 `~/KimodoMotionRuntime/`，开发可选项目内 `.kimodo-runtime/`
  - 打包为 Blender 扩展（`blender_manifest.toml`）；协议改为 GPL-3.0-or-later
  - 文档合并为单一 INSTALL（中/英），文本编码器"非门控镜像"方案跨平台通用
- 2026-04-17 v1.5：fbxsdkpy import 名修正（`fbx`）、precheck 双重校验 + DLL-unload 段错误规避，install→precheck 全绿验收
- 2026-04-17 v1.4：`-DryRun` 文案、precheck `--no-venv-probe` 不虚报 missing
- 2026-04-16 v1.3：`-Proxy` 端到端、precheck 校验 HF cache tree、离线包结构说明、非 ASCII 路径警告
- 2026-04-16 v1.2：`Invoke-Download`（BITS+IWR 回退/重试）、Python 安装器不再静默 hang、各 pip 加 retries/timeout
- 2026-04-16 v1.1：`install.cmd` 英文启动器、修 fbx_runner 版本不一致
- 2026-04-14 v1.0：初版安装链路（install.ps1 + precheck.py + Blender UI 面板）
