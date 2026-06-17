# Blender Kimodo Motion

> [中文说明](README_CN.md) | English

A Blender add-on that brings **NVIDIA Kimodo** text-to-motion generation into Blender.
Type a prompt, select a character, and receive an animated Action — generated locally on
your GPU and retargeted onto your rig without any external tools.

![Blender](https://img.shields.io/badge/Blender-5.0%2B-orange)
![Platform](https://img.shields.io/badge/Windows%20%7C%20macOS-blue)
![Accelerator](https://img.shields.io/badge/CUDA%20%7C%20Metal%20(MPS)-green)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

---

## Overview

1. Select any humanoid armature in Blender (Mixamo, VRoid, MMD, or a custom rig).
2. Enter a prompt in English or Chinese.
3. The add-on calls a local Kimodo inference server, generates a 77-joint SOMA motion,
   and retargets it onto your armature **inside Blender** (no Autodesk FBX SDK required).
4. You receive one or more Action data-blocks named `Kimodo_<prompt>_sNN`, ready for the
   NLA editor.

All inference runs locally on your GPU. No cloud service and no API keys are required
(aside from the optional prompt-translation API).

## Compatibility

| | Windows | macOS |
|---|---|---|
| Accelerator | NVIDIA RTX 20/30/40/50 (CUDA) | Apple Silicon (Metal / MPS) |
| Blender | 5.0+ | 5.0+ |
| Runtime Python (venv) | 3.10 – 3.13 | 3.10 – 3.13 |
| Memory | 16 GB+ VRAM | 32 GB+ unified recommended (16 GB text encoder, fp16) |
| Disk | ~25–50 GB (venv + Hugging Face models) | ~25–50 GB |
| Retargeting | In-Blender (`retarget/bpy_retarget.py`) | In-Blender (`retarget/bpy_retarget.py`) |
| Status | Stable | Beta — see [INSTALL_MAC.md](./INSTALL_MAC.md) |

Retargeting runs entirely inside Blender on every platform, so the Autodesk FBX SDK is no
longer required. T-pose target rigs (Mixamo, most VRChat-ready VRM, Ready Player Me) are
fully supported; full 3-axis rest-pose matching for A-pose rigs is in progress.

## Installation

The add-on ships as a Blender Extension (`blender_manifest.toml`). Install the
`kimodo_motion` package as an extension, then install the inference runtime from the
add-on's **Runtime Install** panel:

- **Windows** — installs Python venv + PyTorch (CUDA) + kimodo + the FastAPI server.
- **macOS** — installs Python venv + PyTorch (Metal/MPS) + kimodo + the FastAPI server.

Detailed steps: [INSTALL.md](./INSTALL.md) · macOS: [INSTALL_MAC.md](./INSTALL_MAC.md).

The runtime (venv + model cache) is kept in one relocatable folder anchored to the
add-on's `venv path` preference, so it never pollutes the system Python; deleting that
folder fully removes it.

## Text encoder model (LLaMA-3-8B)

The LLM2Vec text encoder uses **Meta-Llama-3-8B-Instruct** as its base (~16 GB):

1. **Official (default, clean license).** `meta-llama/Meta-Llama-3-8B-Instruct` is gated by
   Meta — request access on the [model page](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct),
   then `hf auth login`. Downloaded automatically on first generation.
2. **Ungated mirror (development convenience).** `NousResearch/Meta-Llama-3-8B-Instruct` is
   a byte-identical re-upload requiring no request or login. Download it and repoint the
   LLM2Vec adapters' base:

   ```bash
   export HF_HOME="<sibling of venv>/hf-cache"   # the cache the server reads, next to the venv
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

> The weights remain governed by the **Meta Llama-3 Community License** regardless of
> download source. The mirror is a development convenience; distribute via the official
> gated channel. The macOS / Metal end-to-end run was validated with the mirror.

## Usage

1. Import a humanoid rig (Mixamo X Bot, a VRoid character, an MMD PMX, etc.).
2. Select its armature in Object Mode. The N-panel shows the detected target and preset:
   `Target: MyCharacter_Armature · 65 bones · auto-detected: mixamo`.
3. Enter a prompt, duration (2–10 s), and number of variants (1–8).
4. Click **Generate and apply to selected armature**.
5. Switch between variants in the **Generated Actions** sub-panel.

Prompt examples:

- `A person walks forward and waves the right hand.`
- `优雅地跳舞` (auto-translated to English when translation mode is enabled).
- `The character performs a backflip and lands in a fighting stance.`

## How it works

```
Blender operator
      │
      ├── (optional) translate prompt → POST /generate to the local Kimodo server
      │         LLM2Vec (LLaMA-3-8B) text encoder → Kimodo diffusion → 77-joint SOMA NPZ
      │
      ├── in-Blender retarget (retarget/bpy_retarget.py)
      │         map SOMA → target bones; apply per-frame rotations + scaled root motion
      │
      └── one or more named Action data-blocks assigned to your armature
```

Design notes:

- Inference runs in a dedicated Python venv (kept separate from Blender's embedded Python).
- Retargeting is performed directly on the armature with `mathutils`, using a numerically
  validated rest-delta rotation transfer (SOMA Y-up → Blender Z-up). This is portable to
  every platform Blender runs on and requires no FBX round-trip.
- The legacy FBX-SDK retarget path (`retarget/fbx_bridge.py`, `fbx_runner.py`) remains in
  the tree for reference but is no longer used.

## License boundary

| Component | License | Distribution |
|-----------|---------|--------------|
| This add-on (`kimodo_motion/*`) | **GPL-3.0-or-later** | bundled in the extension |
| Vendored retarget reference (`vendor/kimodo_retarget/*`) | Apache-2.0 (from [ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo)) | bundled, with `LICENSE-APACHE2.0` |
| [kimodo](https://github.com/nv-tlabs/kimodo) (NVIDIA) | Apache-2.0 | pip-installed at runtime |
| Kimodo-SOMA-RP-v1 (model weights) | NVIDIA Open Model License | auto-downloaded from Hugging Face |
| Meta-Llama-3-8B-Instruct | Meta Llama-3 Community License (gated) | user accepts + Hugging Face token |
| PyTorch | BSD-3-Clause | pip-installed |

The installer never bundles model weights or gated content; each machine fetches them from
their original sources. This is a legal requirement, not an engineering choice.

## Credits and attribution

This project began as a fork of
**[Xingxun7777/blender-kimodo-motion](https://github.com/Xingxun7777/blender-kimodo-motion)**
by **Xingxun** (originally MIT-licensed), and has since been extended with macOS / Apple
Silicon (Metal/MPS) support, in-Blender retargeting, project-contained runtime, and Blender
Extension packaging. It is redistributed under GPL-3.0-or-later (compatible with the
original MIT terms); the original copyright and authorship are retained.

- [NVIDIA Toronto AI Lab](https://github.com/nv-tlabs) — Kimodo model and training code
- [jtydhr88/ComfyUI-Kimodo](https://github.com/jtydhr88/ComfyUI-Kimodo) — original FBX
  retarget logic referenced during development
- McGill-NLP — LLM2Vec text-encoder adapters
- Meta AI — Llama-3-8B-Instruct (gated)

## Authors

- **Xingxun** — original add-on — [github.com/Xingxun7777](https://github.com/Xingxun7777)
- **Atticus** — macOS / Metal port, in-Blender retarget, Extension packaging —
  [github.com/atticus-lv](https://github.com/atticus-lv)

## License

GPL-3.0-or-later — see [LICENSE](./LICENSE). Portions derive from the MIT-licensed
upstream project noted above.
