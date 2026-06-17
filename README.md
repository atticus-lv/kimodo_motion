# Blender Kimodo Motion

> [中文说明](README_CN.md) | English

A Blender 5.0+ addon that brings **NVIDIA Kimodo** text-to-motion generation inside Blender. Type a prompt, pick a character, get an animated Action — no external tools, no manual retargeting.

![Status](https://img.shields.io/badge/Blender-5.0%2B-orange) ![Platform](https://img.shields.io/badge/Windows-10%2F11%20x64-blue) ![GPU](https://img.shields.io/badge/NVIDIA-RTX%2020%2F30%2F40%2F50-green) ![License](https://img.shields.io/badge/plugin-MIT-lightgrey)

### 📦 [**Download kimodo_motion.zip — Latest Release**](https://github.com/Xingxun7777/blender-kimodo-motion/releases/latest/download/kimodo_motion.zip)

All versions: [Releases page](https://github.com/Xingxun7777/blender-kimodo-motion/releases)

---

## What it does

1. You select any humanoid armature in Blender (Mixamo / VRoid / MMD / custom).
2. You type a prompt in plain English or Chinese.
3. The addon calls a local Kimodo inference server, generates a 77-joint SOMA motion, and retargets it directly onto your armature via Autodesk FBX SDK.
4. You get one (or N) Action data-blocks named `Kimodo_<prompt>_sNN`, ready for the NLA editor.

Everything runs locally on your GPU — no cloud, no API keys (except the optional translation API).

## Requirements

| Item | Minimum |
|------|---------|
| OS | Windows 10 / 11 x64 |
| Blender | **5.0.1+** (4.x is rejected at addon load) |
| GPU | NVIDIA RTX 20/30/40/50 series |
| VRAM | 16 GB (for the LLaMA-3-8B text encoder) |
| Disk | ~50 GB free (5 GB venv + 17 GB HF models + overhead) |
| HuggingFace | Account with accepted [Meta-LLaMA-3-8B license](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) |

## Quick start

```
1. Blender > Edit > Preferences > Add-ons > Install...
   Pick kimodo_motion.zip > Enable.

2. Open N-panel > Kimodo > Runtime Install.
   Click [One-click install runtime] (or double-click installer/install.cmd).

3. Wait 10–30 min while the PowerShell window installs:
     Python 3.12 · venv · PyTorch cu128 · fbxsdkpy · kimodo + deps.

4. Log in to HuggingFace (first time only):
     <venv>\Scripts\python.exe -m huggingface_hub.commands.huggingface_cli login

5. Select your character armature in 3D Viewport.
   Type a prompt, click [Generate and apply to selected armature].
   First generation downloads ~17 GB of models (~30 min).
   Subsequent generations take 8–60 seconds depending on GPU.
```

Detailed guide: [INSTALL.md](./INSTALL.md) (Chinese + English snippets).

## Usage

1. Import any humanoid rig (Mixamo X Bot, a VRoid character, an MMD PMX, etc.).
2. Select its armature in Object Mode — the N-panel will show:
   ```
   Target: MyCharacter_Armature · 65 bones · auto-detected: mixamo
   ```
3. Fill in prompt, duration (2–10 s), variants (1–8).
4. Click **Generate and apply to selected armature**.
5. Switch between variants in the **Generated Actions** sub-panel.

Prompt examples:
- `A person walks forward and waves the right hand.`
- `优雅地跳舞` (auto-translated to English when translate mode is on).
- `The character performs a backflip and lands in a fighting stance.`

## How it works

```
Blender UI operator
      │
      ├── export target armature → temp FBX (cached by bone-structure hash)
      │
      ├── HTTP POST /generate → Kimodo server
      │                          (LLaMA-3-8B → Kimodo diffusion → 77-joint NPZ)
      │
      ├── subprocess: fbx_runner.py (runs inside kimodo_venv Python 3.12)
      │                          (vendored kimodo_retarget_fbx.py does FBX-level retarget)
      │
      └── import retargeted FBX → extract Action → assign to user armature
```

Key design decisions:
- Inference runs in a separate Python 3.12 venv (Blender's embedded Python would conflict with fbxsdkpy).
- Retarget happens at **FBX file level** (not Blender bone-level) because it avoids Blender rest-pose quirks that cause 3× jitter on Hips.
- Target FBX is cached by MD5 of bone-name + parent-index, so exporting is near-free on repeat generations.

## License boundary (IMPORTANT)

| Component | License | Distribution |
|-----------|---------|--------------|
| This addon (`kimodo_motion/*`) | MIT | bundled in zip |
| Vendored retarget (`vendor/kimodo_retarget/kimodo_retarget_fbx.py`) | Apache-2.0 (from [ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo)) | bundled, with `LICENSE-APACHE2.0` |
| [kimodo](https://github.com/nv-tlabs/kimodo) (NVIDIA) | Apache-2.0 | pip-installed from git |
| Kimodo-SOMA-RP-v1 (model weights) | NVIDIA Open Model License | auto-downloaded from HuggingFace |
| Meta-LLaMA-3-8B-Instruct | LLaMA-3 Community License (gated) | user accepts + HF token |
| fbxsdkpy | Autodesk FBX SDK LSA — **redistribution forbidden** | pip-installed from [INRIA GitLab](https://gitlab.inria.fr/mmuslam/fbxsdkpy) |
| PyTorch cu128 | BSD-3 | pip-installed from pytorch.org |

The installer never bundles fbxsdkpy or model weights. Each user's machine pulls them from their original sources — this is a **legal requirement**, not an engineering choice.

## Roadmap

- [x] One-click Windows installer (Python 3.12 + venv + PyTorch cu128 + fbxsdkpy + kimodo)
- [x] FBX SDK retarget with bone-structure caching
- [x] Auto skeleton preset detection (Mixamo / VRoid / MMD)
- [x] Multi-sample Actions (N variants per prompt)
- [x] Translation API bridge (DeepSeek / OpenRouter / Moonshot / OpenAI-compatible)
- [x] Chinese / English UI
- [x] **macOS / Apple Silicon (Metal/MPS)** — in-Blender retarget, no fbxsdkpy needed (beta; see [INSTALL_MAC.md](./INSTALL_MAC.md))
- [ ] Full 3-axis rest-pose matching for A-pose rigs (T-pose already works)
- [ ] Linux support (venv should already work; untested)
- [ ] Per-viewport live preview during retarget
- [ ] NLA push-to-track toggle

## Contributing

PRs welcome. Please:
- Run the Deep Review protocol on any changes touching `retarget/` or `installer/`.
- Update `INSTALL.md`'s CHANGELOG section with date + one line per change.
- Do NOT bundle fbxsdkpy, model weights, or any gated content.

## Acknowledgements

- [NVIDIA Toronto AI Lab](https://github.com/nv-tlabs) — Kimodo model and training code
- [jtydhr88/ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo) — the `kimodo_retarget_fbx.py` retarget logic we vendored
- [INRIA MMUSLAM](https://gitlab.inria.fr/mmuslam/fbxsdkpy) — fbxsdkpy Python wrapper for Autodesk FBX SDK
- Meta AI — LLaMA-3-8B-Instruct (gated)

## Author

**Xingxun** — [github.com/Xingxun7777](https://github.com/Xingxun7777)

## License

MIT — see [LICENSE](./LICENSE).
