# Kimodo Motion — macOS 安装指南（Apple Silicon / Metal）

> macOS 走 **Metal/MPS** 推理,retarget 在 **Blender 内**完成,**不需要** Autodesk FBX SDK
> (fbxsdkpy)、不需要独立 FBX 子进程。整个运行时(venv + 模型缓存)可装进**一个文件夹**,
> 删掉即彻底卸载,不污染系统 Python、不散落 `~/.cache`。

## 系统需求

| 项 | 要求 |
|----|------|
| 系统 | macOS（Apple Silicon / arm64 推荐；Intel 会回落 CPU） |
| GPU | Apple Silicon（Metal / MPS）。无 MPS 时自动用 CPU(慢) |
| 内存 | 统一内存 16 GB+（LLaMA-3-8B 文本编码器,fp16） |
| 磁盘 | ~25 GB（venv ~5GB + 模型 ~17GB + 余量） |
| Blender | **5.0.1+**（arm64 原生版） |
| Python | 3.10 / 3.11 / 3.12 / 3.13（建议 `brew install python@3.11`） |
| HuggingFace | 申请并接受 [Meta-Llama-3-8B 协议](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) |

## 快速开始

### 1. 安装插件

Blender > Edit > Preferences > Add-ons > Install… 选 `kimodo_motion.zip` > Enable。

### 2. 安装运行时（venv + PyTorch/MPS + kimodo + 服务依赖）

**面板方式**:N 面板 > Kimodo > Runtime > 点 `[一键安装 Runtime]`。
会打开"终端 (Terminal)"窗口跑 `install_mac.sh`,装到偏好里 `venv 路径` 指向的位置。

**命令行方式(推荐,可控装哪)**:把整个运行时装进**项目文件夹内**(gitignore,删一个文件夹即清):

```bash
cd /path/to/blender-kimodo-motion-metal
KIMODO_VENV="$(pwd)/.kimodo-runtime/venv" bash kimodo_motion/installer/install_mac.sh
```

装好后,把插件偏好里的 **venv 路径**设为 `<repo>/.kimodo-runtime/venv`。
这样 venv、日志、**以及 17GB 模型缓存**都会落在 `<repo>/.kimodo-runtime/` 里。

安装器选项(环境变量):

| 变量 | 作用 | 默认 |
|------|------|------|
| `KIMODO_VENV` | venv 目标路径(运行时/模型缓存放它旁边) | `~/.kimodo_venv` |
| `KIMODO_PIP_MIRROR` | `auto` / `pypi` / `tsinghua` / `aliyun` | `auto` |
| `KIMODO_PROXY` | HTTP(S) 代理,如 `http://127.0.0.1:7890` | 空 |
| `KIMODO_GIT_URL` | kimodo git 源(含 MPS 后端的 fork) | `https://github.com/atticus-lv/kimodo.git` |
| `KIMODO_HF_HOME` | 模型缓存位置(覆盖默认的 venv 旁) | `<venv 父目录>/hf-cache` |
| `KIMODO_SKIP_TORCH` | `1` 跳过 torch(仅开发调试) | `0` |

### 3. 登录 HuggingFace（首次必做）

```bash
<repo>/.kimodo-runtime/venv/bin/python -m huggingface_hub.commands.huggingface_cli login
```

### 4. 生成

N 面板 > 选中你的人形骨架 > 填 prompt > `[生成并应用到选中骨架]`。
首次生成会下 ~17GB 模型(落在 `.kimodo-runtime/hf-cache`,不是 `~/.cache`)。
文本编码器 + 扩散都走 Metal(MPS);可用 `KIMODO_DEVICE=cpu` 强制 CPU。

## 运行时结构（项目内安装时）

| 路径 | 内容 | 大小 |
|------|------|------|
| `<repo>/.kimodo-runtime/venv/` | Python venv + 所有 pip 包(torch/kimodo/…) | ~5 GB |
| `<repo>/.kimodo-runtime/hf-cache/` | HuggingFace 模型缓存(Kimodo + LLaMA-3-8B) | ~17 GB |
| `<repo>/.kimodo-runtime/runtime/install.log` | 安装日志 | <1 MB |

**卸载**:删除 `<repo>/.kimodo-runtime/` 整个文件夹即可。系统 Python 全程未被改动。

## 与 Windows 的差异

- **不装 fbxsdkpy / FBX SDK**:retarget 在 Blender 内用 `bpy` 完成(`retarget/bpy_retarget.py`)。
- **不需要 Python 3.12 限制**:fbxsdkpy 的 cp312 限制不再适用,3.10–3.13 均可。
- **PyTorch 来自 PyPI**(arm64 轮子自带 Metal/MPS),无独立 CUDA channel。
- **设备**:`auto → cuda > mps > cpu`,Mac 上即 MPS;`KIMODO_DEVICE` 可覆盖。

## 已知限制（macOS,beta）

- retarget 对 **T-pose 骨架**(Mixamo / 多数 VRChat-VRM / Ready Player Me)已验证正确;
  真正的 **A-pose 骨架**需要完整三轴 rest 姿态匹配(下个迭代)。
- 端到端(服务器在 MPS 上生成)随首次安装+生成验证;retarget 核心已在本机 Blender 验证。
- 文本编码器(LLaMA-3-8B,fp16)默认走 MPS、占统一内存;显存吃紧可设 `KIMODO_DEVICE=cpu` 让它走 CPU。
