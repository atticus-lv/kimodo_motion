# Blender Kimodo Motion

> 中文说明 | [English](README.md)

将 **NVIDIA Kimodo** 文本生成动作能力带入 Blender 的插件。输入一句 prompt、选中角色骨架，
即可在本地 GPU 上生成动作并自动 retarget 到你的骨架，得到可直接使用的 Action——无需任何外部工具。

![Blender](https://img.shields.io/badge/Blender-5.0%2B-orange)
![Platform](https://img.shields.io/badge/Windows%20%7C%20macOS-blue)
![Accelerator](https://img.shields.io/badge/CUDA%20%7C%20Metal%20(MPS)-green)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

---

## 概览

1. 选中场景里任意人形骨架（Mixamo / VRoid / MMD / 自定义）。
2. 输入中文或英文 prompt。
3. 插件调用本地 Kimodo 推理服务器，生成 77 关节 SOMA 动作，并**在 Blender 内**直接 retarget
   到你的骨架（**不需要** Autodesk FBX SDK）。
4. 得到一个或多个命名为 `Kimodo_<prompt>_sNN` 的 Action，可直接拖入 NLA。

全程本地 GPU 推理，不联网（除可选的 prompt 翻译 API 外）。

## 兼容性

| | Windows | macOS |
|---|---|---|
| 加速器 | NVIDIA RTX 20/30/40/50（CUDA） | Apple Silicon（Metal / MPS） |
| Blender | 5.0+ | 5.0+ |
| 运行时 Python（venv） | 3.10 – 3.13 | 3.10 – 3.13 |
| 显存/内存 | 16 GB+ 显存 | 建议 32 GB+ 统一内存（fp16 编码器约 16 GB） |
| 磁盘 | 约 25–50 GB（venv + HF 模型） | 约 25–50 GB |
| Retarget | Blender 内（`retarget/bpy_retarget.py`） | Blender 内（`retarget/bpy_retarget.py`） |
| 状态 | 稳定 | Beta，见 [INSTALL_MAC.md](./INSTALL_MAC.md) |

所有平台的 retarget 都在 Blender 内完成，因此**不再需要 Autodesk FBX SDK**。T-pose 目标骨架
（Mixamo、多数 VRChat-ready VRM、Ready Player Me）已完整支持；A-pose 骨架的完整三轴 rest
姿态匹配正在开发中。

## 安装

插件以 Blender 扩展（`blender_manifest.toml`）形式发布。将 `kimodo_motion` 作为扩展安装后，
在插件的 **Runtime 安装** 面板里安装推理运行时：

- **Windows** — 安装 Python venv + PyTorch（CUDA）+ kimodo + FastAPI 服务器。
- **macOS** — 安装 Python venv + PyTorch（Metal/MPS）+ kimodo + FastAPI 服务器。

详细步骤：[INSTALL.md](./INSTALL.md) · macOS 见 [INSTALL_MAC.md](./INSTALL_MAC.md)。

运行时（venv + 模型缓存）锚定到插件偏好里的 `venv 路径`，全部收在一个可重定位的文件夹里，
**不污染系统 Python**；删除该文件夹即彻底卸载。

## 文本编码器模型（LLaMA-3-8B）

文本编码器走 LLM2Vec，需要 **Meta-Llama-3-8B-Instruct** 作为 base（约 16 GB）：

1. **官方（默认，许可干净）**：`meta-llama/Meta-Llama-3-8B-Instruct` 是 Meta 门控模型——先在
   [模型页](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct)申请通过，再 `hf auth login`，
   首次生成时自动下载。
2. **非门控镜像（开发省事，免申请免登录）**：`NousResearch/Meta-Llama-3-8B-Instruct` 是字节相同的
   二次上传。下载后把 LLM2Vec adapter 的 base 指过去：

   ```bash
   export HF_HOME="<venv 同级目录>/hf-cache"   # 与服务器读取的缓存一致（venv 旁边）
   HF="<venv>/bin/hf"
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
> 开发便利；对外分发请走官方门控渠道。macOS / Metal 的端到端验证即用镜像跑通。

## 使用

1. 导入任意人形 rig（Mixamo X Bot、VRoid 角色、MMD PMX 等）。
2. Object 模式选中其 Armature，N 面板会显示识别到的目标与预设：
   `目标：MyCharacter_Armature · 65 骨骼 · 自动识别：mixamo`。
3. 填写 prompt、时长（2–10 秒）、变体数（1–8）。
4. 点 **生成并应用到选中骨架**。
5. 在 **已生成的 Action** 子面板切换不同变体。

Prompt 示例：

- `A person walks forward and waves the right hand.`
- `优雅地跳舞`（开启翻译模式时自动 zh→en）
- `角色做一个后空翻并落地进入战斗姿态`

## 工作原理

```
Blender operator
      │
      ├── （可选）翻译 prompt → POST /generate 到本地 Kimodo 服务器
      │         LLM2Vec（LLaMA-3-8B）文本编码器 → Kimodo 扩散 → 77 关节 SOMA NPZ
      │
      ├── Blender 内 retarget（retarget/bpy_retarget.py）
      │         SOMA → 目标骨骼映射；逐帧旋转 + 按比例缩放的根位移直接施加到骨架
      │
      └── 一个或多个具名 Action 赋给你的骨架
```

设计要点：

- 推理跑在独立 Python venv 里（与 Blender 内嵌 Python 隔离）。
- Retarget 直接用 `mathutils` 在骨架上完成，采用经数值验证的 rest-delta 旋转传递
  （SOMA Y-up → Blender Z-up），可移植到任何 Blender 平台，无需 FBX 往返。
- 旧的 FBX-SDK retarget 路径（`retarget/fbx_bridge.py`、`fbx_runner.py`）仍保留在仓库中作参考，已不再使用。

## License 边界

| 组件 | 协议 | 分发方式 |
|------|------|----------|
| 插件本身（`kimodo_motion/*`） | **GPL-3.0-or-later** | 打包进扩展 |
| Vendored retarget 参考（`vendor/kimodo_retarget/*`） | Apache-2.0（来自 [ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo)） | 打包，附 `LICENSE-APACHE2.0` |
| [kimodo](https://github.com/nv-tlabs/kimodo)（NVIDIA） | Apache-2.0 | 运行时 pip 安装 |
| Kimodo-SOMA-RP-v1（模型权重） | NVIDIA Open Model License | 首次生成时自动从 HuggingFace 下载 |
| Meta-Llama-3-8B-Instruct | Meta Llama-3 Community License（门控） | 用户申请 + HF token |
| PyTorch | BSD-3-Clause | pip 安装 |

安装器永远不会打包模型权重或任何 gated content；每台机器都从原始源拉取——这是法律合规要求，不是工程选择。

## 来源与署名

本项目源自 **[Xingxun7777/blender-kimodo-motion](https://github.com/Xingxun7777/blender-kimodo-motion)**
（作者 **Xingxun**，原 MIT 协议）的 fork，在此基础上新增了 macOS / Apple Silicon（Metal/MPS）支持、
Blender 内 retarget、项目内自包含运行时，并打包为 Blender 扩展。本仓库以 GPL-3.0-or-later 再分发
（与原 MIT 条款兼容），保留原始版权与署名。

- [NVIDIA Toronto AI Lab](https://github.com/nv-tlabs) — Kimodo 模型与训练代码
- [jtydhr88/ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo) — 开发期参考的原始 FBX retarget 逻辑
- McGill-NLP — LLM2Vec 文本编码器 adapter
- Meta AI — Llama-3-8B-Instruct（门控）

## 作者

- **Xingxun** — 原始插件 — [github.com/Xingxun7777](https://github.com/Xingxun7777)
- **Atticus** — macOS / Metal 移植、Blender 内 retarget、扩展打包 —
  [github.com/atticus-lv](https://github.com/atticus-lv)

## 协议

GPL-3.0-or-later — 见 [LICENSE](./LICENSE)。部分代码源自上述上游项目（原 MIT 协议）。
