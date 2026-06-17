# Blender Kimodo Motion

> 中文说明 | [English](README.md)

Blender 5.0+ 插件：在 Blender 里用一句话生成动作。选一个角色骨架，输入 prompt，自动 retarget 到该骨架，生成一个或多个新的 Action——全程本地 GPU 推理，无需云服务。

![Blender](https://img.shields.io/badge/Blender-5.0%2B-orange) ![Platform](https://img.shields.io/badge/Windows-10%2F11%20x64-blue) ![GPU](https://img.shields.io/badge/NVIDIA-RTX%2020%2F30%2F40%2F50-green) ![License](https://img.shields.io/badge/plugin-MIT-lightgrey)

### 📦 [**点击下载 kimodo_motion.zip — 最新版**](https://github.com/Xingxun7777/blender-kimodo-motion/releases/latest/download/kimodo_motion.zip)

所有历史版本：[Releases 页](https://github.com/Xingxun7777/blender-kimodo-motion/releases)

---

## 功能

1. 选中场景里任意人形骨架（Mixamo / VRoid / MMD / 自定义）
2. 输入中英文 prompt
3. 插件调用本地 Kimodo 推理服务器 → 生成 77 关节 SOMA 动作 → 用 Autodesk FBX SDK 直接 retarget 到你的骨架
4. 得到 N 个命名为 `Kimodo_<prompt>_sNN` 的 Action，可以直接拖到 NLA 里

全部本地 GPU，不联网（除了可选的翻译 API）。

## 系统需求

| 项 | 要求 |
|----|------|
| 系统 | **Windows 10/11 x64**（NVIDIA）或 **macOS / Apple Silicon**（Metal/MPS,beta） |
| Blender | **5.0.1+**（4.x 会被 Blender 拒绝） |
| GPU | Windows: NVIDIA RTX 20/30/40/50 系 · macOS: Apple Silicon(MPS),否则回落 CPU |
| 显存/内存 | 16 GB+（LLaMA-3-8B 文本编码器需要） |
| 磁盘 | 约 25-50 GB（venv ~5GB + HF 模型 ~17GB + 余量） |
| HuggingFace | 申请并接受 [Meta-LLaMA-3-8B 协议](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) |

> **macOS 用户**:retarget 在 Blender 内完成,**不需要** fbxsdkpy/FBX SDK。
> 安装见 [INSTALL_MAC.md](./INSTALL_MAC.md);整个运行时可装进一个文件夹,删掉即卸载。

## 快速开始

```
1. Blender > Edit > Preferences > Add-ons > Install...
   选 kimodo_motion.zip > Enable.

2. N 面板 > Kimodo > Runtime 安装.
   点 [一键安装 Runtime]（或双击 installer/install.cmd）.

3. 等 10-30 分钟，PowerShell 窗口会装:
     Python 3.12 · venv · PyTorch cu128 · fbxsdkpy · kimodo + 依赖.

4. 登录 HuggingFace（首次必做）:
     <venv>\Scripts\python.exe -m huggingface_hub.commands.huggingface_cli login

5. 选中你的角色骨架.
   填 prompt，点 [生成并应用到选中骨架].
   首次会下 17GB 模型（约 30 分钟）.
   之后单次生成 8-60 秒（按 GPU 算力）.
```

详细步骤：[INSTALL.md](./INSTALL.md)

## 使用

1. 导入任意人形 rig（Mixamo X Bot / VRoid 角色 / MMD PMX 都行）
2. Object 模式选中它的 Armature——N 面板会显示：
   ```
   目标：MyCharacter_Armature · 65 骨骼 · 自动识别：mixamo
   ```
3. 填 prompt、时长 (2-10 秒)、变体数 (1-8)
4. 点 **生成并应用到选中骨架**
5. 在 **已生成的 Action** 子面板切换不同变体

Prompt 示例：
- `A person walks forward and waves the right hand.`
- `优雅地跳舞`（开启翻译模式时自动 zh→en）
- `角色做一个后空翻并落地进入战斗姿态`

## 工作原理

```
Blender UI operator
      │
      ├── 导出目标骨架为临时 FBX（按骨骼结构 hash 缓存）
      │
      ├── HTTP POST /generate → Kimodo 服务器
      │                         （LLaMA-3-8B → Kimodo 扩散 → 77 关节 NPZ）
      │
      ├── subprocess: fbx_runner.py（跑在 kimodo_venv Python 3.12）
      │                         （vendored kimodo_retarget_fbx.py 做文件级 retarget）
      │
      └── 导入 retarget 后的 FBX → 提取 Action → 赋给用户骨架
```

关键设计：
- 推理跑在独立 Python 3.12 venv 里（避免和 Blender 内嵌 Python 冲突 fbxsdkpy）
- Retarget 在 **FBX 文件层做**（不是 Blender 骨骼层）——绕开 Blender rest-pose 问题导致的 Hips 3x 抖动
- Target FBX 按"骨骼名+父子关系"MD5 缓存，重复生成几乎零开销

## License 边界（必读）

| 组件 | 协议 | 分发方式 |
|------|------|----------|
| 插件本身 (`kimodo_motion/*`) | MIT | 打包到 zip |
| Vendored retarget (`vendor/kimodo_retarget/kimodo_retarget_fbx.py`) | Apache-2.0（来自 [ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo)） | 打包，附带 `LICENSE-APACHE2.0` |
| [kimodo](https://github.com/nv-tlabs/kimodo)（NVIDIA） | Apache-2.0 | `pip install git+...` 自动装 |
| Kimodo-SOMA-RP-v1（模型权重） | NVIDIA Open Model License | 首次生成时自动从 HuggingFace 下 |
| Meta-LLaMA-3-8B-Instruct | LLaMA-3 Community License（gated） | 用户自己申请 + 登录 |
| fbxsdkpy | Autodesk FBX SDK LSA — **禁止再分发** | 安装时 pip 从 [INRIA GitLab](https://gitlab.inria.fr/mmuslam/fbxsdkpy) 拉 |
| PyTorch cu128 | BSD-3 | pip 官方源 |

安装器**永远不会**打包 fbxsdkpy 或模型权重。每台机器必须自己去原始源拉——这是法律合规要求，不是工程选择。

## 路线图

- [x] Windows 一键安装器（Python 3.12 + venv + PyTorch cu128 + fbxsdkpy + kimodo）
- [x] FBX SDK 文件级 retarget + 骨骼结构缓存
- [x] 自动骨骼预设识别（Mixamo / VRoid / MMD）
- [x] 多变体 Action 生成（每个 prompt N 个）
- [x] 翻译 API 桥接（DeepSeek / OpenRouter / Moonshot / OpenAI 兼容）
- [x] 中英双语 UI
- [x] **macOS / Apple Silicon(Metal/MPS)** — in-Blender retarget,无需 fbxsdkpy(beta,见 [INSTALL_MAC.md](./INSTALL_MAC.md))
- [ ] A-pose 骨架的完整三轴 rest 姿态匹配（T-pose 已可用）
- [ ] Linux 支持（venv 理论可行，未实测）
- [ ] Retarget 过程实时预览
- [ ] NLA track 推送开关

## 贡献

欢迎 PR。规约：
- 改 `retarget/` 或 `installer/` 必须走 Deep Review protocol
- 更新 `INSTALL.md` 的 CHANGELOG 段（日期 + 一行变更）
- **绝对不要**打包 fbxsdkpy、模型权重、任何 gated content

## 致谢

- [NVIDIA Toronto AI Lab](https://github.com/nv-tlabs) — Kimodo 模型和训练代码
- [jtydhr88/ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo) — vendored 的 `kimodo_retarget_fbx.py` 来自这个项目
- [INRIA MMUSLAM](https://gitlab.inria.fr/mmuslam/fbxsdkpy) — fbxsdkpy 是 Autodesk FBX SDK 的 Python wrapper
- Meta AI — LLaMA-3-8B-Instruct

## 作者

**Xingxun** — [github.com/Xingxun7777](https://github.com/Xingxun7777)

## 协议

MIT — 见 [LICENSE](./LICENSE)。
