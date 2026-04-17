# Kimodo Motion — 安装指南

## 系统需求

| 项 | 要求 |
|----|------|
| 系统 | Windows 10/11 (x64) |
| GPU | NVIDIA RTX 20/30/40/50 系 |
| 显存 | 16 GB+（LLaMA-3-8B 文本编码器） |
| 磁盘 | 50 GB 可用（venv 5GB + 模型 17GB + 余量） |
| 驱动 | ≥ 570 (cu128) 或 ≥ 528 (cu121) |
| Blender | **5.0.1+**（4.x 不支持，加载 zip 会被 Blender 拒绝） |
| Python | 3.10/3.11/3.12/3.13（脚本会自动找或装） |

## 快速开始

```
Step 1. Blender > Edit > Preferences > Add-ons > Install...
        选 kimodo_motion.zip -> Enable

Step 2. N 面板 > Kimodo > Runtime 安装
        点 [一键安装 Runtime]
        填代理（可选）-> 确定

Step 3. 等 10-30 分钟（PowerShell 窗口会显示进度）

Step 4. 登录 HuggingFace（首次必做）:
        https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct  申请访问
        venv 里运行:  python -m huggingface_hub.commands.huggingface_cli login

Step 5. N 面板 > 选骨架 > 点 [生成并应用到选中骨架]
        首次生成会下 17GB 模型 (~30 分钟)
```

## 运行时结构

| 路径 | 内容 | 大小 |
|------|------|------|
| `~/.kimodo_venv/` | Python venv + 所有 pip 包 | 5 GB |
| `~/.kimodo_runtime/install.log` | 安装日志 | <1 MB |
| `~/.kimodo_runtime/python/` | portable Python（如自动装的） | 120 MB |
| `~/.kimodo_runtime/templates/` | T-pose 模板 FBX | <1 MB |
| `~/.cache/huggingface/hub/` | Kimodo + LLaMA 模型 | 17 GB |

### 网盘包（离线模型）目录结构

如果使用网盘预下载模型包，解压后**必须保留 HuggingFace 原生 cache tree**，不可只复制权重文件：

```
~/.cache/huggingface/hub/
├── models--nvidia--Kimodo-SOMA-RP-v1/
│   ├── refs/main              ← 必须保留，内容是 snapshot sha
│   ├── snapshots/<sha>/       ← 真实权重（必须是完整 sha 目录）
│   │   ├── config.json
│   │   └── *.safetensors
│   └── blobs/                 ← 可选，实际权重 blob
└── models--meta-llama--Meta-Llama-3-8B-Instruct/
    └── (同上结构)
```

**少 `refs/main` 或 `snapshots/` 子目录**，huggingface_hub 会认为没缓存，触发重新下载 17GB。
precheck.py 会校验这两个目录存在，状态栏显示模型缓存正常。

## 安装参数

在"Runtime 安装"面板点"一键安装"时可选：

| 参数 | 说明 |
|------|------|
| 代理 | `http://127.0.0.1:7890`（Clash 默认）— 空=直连 |
| 模型镜像 | `auto` → 优先 HF，失败换 hf-mirror |
| Pip 镜像 | `auto` → 优先 pypi.org，失败换清华 |

命令行跑（等价）：
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Proxy http://127.0.0.1:7890 -Mirror hf-mirror
```

双击启动（从资源管理器）：双击 `installer/install.cmd`（英文名 + chcp 65001，中文路径 / 用户名不会乱码）。
参数加在双击后的对话框里或用命令行 `install.cmd -Proxy http://127.0.0.1:7890`。

## License 边界（MUST READ）

| 组件 | 许可 | 分发方式 |
|------|------|----------|
| kimodo_motion 插件 | MIT | 打包到 zip |
| kimodo (nv-tlabs) | Apache 2.0 | `pip install git+...` |
| Kimodo-SOMA-RP-v1 | NVIDIA Open Model License | HF 自动下 |
| meta-llama 3-8B | LLaMA-3 Community License（gated） | 用户自己申请 |
| fbxsdkpy | **Autodesk FBX SDK LSA — 禁止再分发** | `pip install` 从 INRIA gitlab |
| PyTorch cu128 | BSD-3 | PyTorch 官方源 |

**fbxsdkpy 绝不会出现在插件 zip 里**。每次用户的 pip 会自己去 INRIA 的包服务器拉。这是法律合规要求，不是工程选择。

## 常见问题

### Q: PowerShell 报 "脚本未签名"
```
执行：Get-ExecutionPolicy  若是 Restricted，用 -ExecutionPolicy Bypass 参数，
     或永久：Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### Q: pip 装 torch 超时 / 卡在 xxx/3GB
```
检查: 带宽限制 / 防火墙 / 代理
重试: 带上 -Proxy http://127.0.0.1:7890
```

### Q: fbxsdkpy install 失败 "Could not find a version"
```
检查 Python 版本: venv 必须是 3.9-3.14 win_amd64（INRIA 没有 3.8 wheel）
手工重试: ~/.kimodo_venv/Scripts/pip install fbxsdkpy ^
  --extra-index-url https://gitlab.inria.fr/api/v4/projects/18692/packages/pypi/simple
```

### Q: 生成时报 "GatedRepoError: meta-llama/Meta-Llama-3-8B-Instruct"
```
1. 打开 https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct
2. 点 "Agree and access repository"
3. 创建 read token: https://huggingface.co/settings/tokens
4. 登录: ~/.kimodo_venv/Scripts/python -m huggingface_hub.commands.huggingface_cli login
```

### Q: CUDA 不可用 (cuda_available=False)
```
原因: torch 版本 ≠ 驱动
检查: nvidia-smi 看 CUDA Version
RTX 50系 (cc ≥ 12.0): 必须 cu128
RTX 40/30系: cu121 或 cu128 都行
重装: ~/.kimodo_venv/Scripts/pip install torch torchvision torchaudio ^
  --index-url https://download.pytorch.org/whl/cu128 --force-reinstall
```

### Q: 磁盘不够
```
模型 17GB 装到 ~/.cache/huggingface  默认在 C: 盘
搬家: 设环境变量 HF_HOME=D:\hf_cache  (所有 HF 工具会认)
```

### Q: 网断了再跑会重新下 3GB 吗？
```
不会。安装脚本所有大包都有 idempotent 检测:
  Step 2 venv     : 存在就复用 (line 346-353)
  Step 5 torch    : 已装且 CUDA 通道匹配 -> 跳过 (line 457-463)
  Step 6 fbxsdkpy : importlib.metadata 检查 -> 跳过 (line 478-482)
  Step 7 kimodo   : import kimodo 检查 -> 跳过 (line 497-500)
所以 install.cmd 重跑只会装未完成的部分。
```

### Q: 我想完全卸载
```
powershell -File installer/uninstall.ps1
```

## 完全手工安装（高级用户 / Linux 用户）

```bash
# 1. Python 3.12 venv
python3.12 -m venv ~/.kimodo_venv
source ~/.kimodo_venv/bin/activate   # Windows: .\.kimodo_venv\Scripts\Activate.ps1

# 2. PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

# 3. FBX SDK wrapper (INRIA — 法律合规必走)
pip install fbxsdkpy --extra-index-url https://gitlab.inria.fr/api/v4/projects/18692/packages/pypi/simple

# 4. kimodo
pip install "kimodo[all] @ git+https://github.com/nv-tlabs/kimodo.git"

# 5. 服务器依赖
pip install fastapi "uvicorn[standard]" pydantic scipy bvhio

# 6. HuggingFace 登录
python -m huggingface_hub.commands.huggingface_cli login

# 7. (可选) 提前下模型
python ~/addons/kimodo_motion/installer/download_model.py
```

## 故障排查工作流

```
出问题 -> N 面板 > Runtime 安装 > 点 [重新检查]
     -> 看状态列表哪项不绿
     -> 点 [查看日志] 在资源管理器里打开 install.log
     -> 把最后 100 行 + precheck 输出发给我（或 issue）
```

## 版本记录

- 2026-04-14 v1: 初版安装链路（install.ps1 + precheck.py + Blender UI 面板）
- fbxsdkpy 锁 `2020.3.7.post1`（INRIA 最新稳定 cp312 wheel）
- Python 3.12 优先（anaconda / python.org installer / portable 都支持）
- 2026-04-16 v1.1: 加 install.cmd 英文名启动器 + 修 fbx_runner.py 版本不一致
- 2026-04-16 v1.2: 修 3 个 Codex 沙盒仿真发现的 P0
  - 加 `Invoke-Download` helper (BITS + IWR 回退, TimeoutSec=600, 重试 3 次)
  - Python 3.12.8 安装器下载不再静默 hang, 失败会明确报错并提示加代理
  - `-ForcePython` 严格模式: 路径不存在 / 版本非 3.10-3.13 → 直接 throw, 不再 fallback 到下载
  - PyTorch 3GB 下载加 `--retries 5 --timeout 120 --progress-bar on`
  - fbxsdkpy 去掉 latest 回退 (避免拉到 2024+ 不兼容大版本)
  - kimodo / server deps 加 `--retries 5 --timeout 60`
- 2026-04-16 v1.3: 堵 5 个 P1
  - `-Proxy` 端到端: `Invoke-Download` + pypi 探测都加 `-ProxyUrl`, BITS 用 ProxyList, IWR 用 -Proxy
  - precheck.py 加 HF cache tree 完整性校验 (`refs/main` + `snapshots/<sha>/`)
  - INSTALL.md 加网盘预下载包目录结构说明 + idempotent 重跑说明
  - install.ps1 启动时警告 USERPROFILE 含非 ASCII 字符
  - Blender 5.0+ 要求在文档中加粗强调
- 2026-04-17 v1.4: Deep Review 补漏
  - install.ps1 `-DryRun` 末尾不再打印 "Kimodo runtime installed."，改为 "DRY-RUN complete — no changes made."
  - precheck.py `--no-venv-probe` 不再虚报 pytorch/fbxsdkpy/kimodo missing（没探过的东西不算 error）
- 2026-04-17 v1.5: 真实客户验收发现 + 修 3 个 P0
  - **INRIA 的 pypi 包名 `fbxsdkpy` 但 import 名是 `fbx`**（Autodesk 惯例: 装 fbx.pyd + FbxCommon.py）
  - precheck.py 原 `import fbxsdkpy` 永远 False，改为 `import fbx` + metadata.version('fbxsdkpy')
  - download_rokoko_template.py T-pose fallback 同样改 `import fbx`（之前新用户无 rig 时没有 fallback）
  - install.ps1 Step 6 原只校验 dist-info 存在，改为双重校验：`import fbx` 成功 + 版本 == `$FBXSDKPY_VERSION`；旧版本 metadata 残留会触发重装
  - precheck.py _VENV_PROBE_SCRIPT 末尾加 `os._exit(0)` 避免 fbxsdkpy DLL-unload segfault 让 rc!=0，probe 退化
  - precheck.py _probe_venv 只要 stdout 能 parse 就 trust（不以 rc 为准）
  - **验收结果**: install → precheck 全链路 next_action=ok 全绿通过
