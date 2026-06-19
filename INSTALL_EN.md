# Kimodo Motion — Installation Guide

> [中文](INSTALL.md) | English · covers **Windows (CUDA)** and **macOS (Metal / MPS)**

---

## 1. Requirements

| Item | Windows | macOS |
|------|---------|-------|
| Accelerator | NVIDIA RTX 20/30/40/50 (CUDA) | Apple Silicon (Metal / MPS); else CPU fallback |
| Blender | **5.0.1+** (4.x is rejected) | **5.0.1+** (native arm64) |
| Python (venv) | 3.10 – 3.13 (auto-located/installed) | 3.10 – 3.13 (`brew install python@3.11`) |
| Memory | 16 GB+ VRAM | 32 GB+ unified recommended (~16 GB fp16 encoder) |
| Disk | ~25–50 GB (venv + models) | ~25–50 GB |
| Driver | ≥ 570 (cu128) or ≥ 528 (cu121) | — |
| Hugging Face | Accept the [Meta-Llama-3-8B license](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) (see §4) | same |

> Retargeting now runs **inside Blender** (`retarget/bpy_retarget.py`), so the Autodesk FBX SDK
> is no longer required on any platform. The Windows installer still installs fbxsdkpy (legacy,
> unused); the macOS installer does not.

---

## 2. Install the add-on (extension)

`Blender > Edit > Preferences > Get Extensions / Add-ons > Install from Disk…`, choose
`kimodo_motion.zip`, and enable it. The add-on is a Blender Extension
(`blender_manifest.toml`) and requires Blender 5.0+.

---

## 3. Install the runtime

The runtime is a self-contained Python venv (torch + kimodo + the FastAPI server), kept
separate from Blender's embedded Python. The venv, logs, and model cache are all anchored to
the add-on's **venv path** preference and live in one folder — delete it to fully uninstall.

### 3.1 Windows (CUDA)

1. N-panel > Kimodo > Runtime Install > **One-click install runtime** (proxy optional).
2. Wait 10–30 min; a PowerShell window shows progress (Python venv + PyTorch cu128 + kimodo + server deps).

Command line:

```powershell
powershell -ExecutionPolicy Bypass -File installer\install.ps1 -Proxy http://127.0.0.1:7890 -Mirror hf-mirror
```

Double-click launcher: `installer\install.cmd`.

| Flag | Meaning |
|------|---------|
| `-Proxy` | e.g. `http://127.0.0.1:7890`; empty = direct |
| `-Mirror` | `auto` → Hugging Face first, fall back to hf-mirror |
| `-PipMirror` | `auto` → pypi.org first, fall back to TUNA |

### 3.2 macOS (Metal / MPS)

**Panel:** N-panel > Kimodo > Runtime Install > **One-click install runtime** → opens Terminal and runs `install_mac.sh`.

**Command line (recommended — keep the runtime inside the project folder; delete one dir to remove):**

```bash
cd /path/to/kimodo_motion
KIMODO_VENV="$(pwd)/.kimodo-runtime/venv" bash installer/install_mac.sh
```

Then set the add-on's **venv path** preference to `<repo>/.kimodo-runtime/venv` — the venv, logs,
**and the ~17 GB model cache** all land under `<repo>/.kimodo-runtime/`.

Installer options (environment variables):

| Variable | Purpose | Default |
|----------|---------|---------|
| `KIMODO_VENV` | venv target (runtime/model cache sit next to it) | `~/.kimodo_venv` |
| `KIMODO_PIP_MIRROR` | `auto` / `pypi` / `tsinghua` / `aliyun` | `auto` |
| `KIMODO_PROXY` | HTTP(S) proxy | empty |
| `KIMODO_GIT_URL` | kimodo git URL **or local directory** (must include MPS backend) | `https://github.com/atticus-lv/kimodo.git` |
| `KIMODO_HF_HOME` | model cache location (overrides the default next to the venv) | `<venv parent>/hf-cache` |
| `KIMODO_DEVICE` | `auto` / `mps` / `cpu` / `cuda` | `auto` (Mac → MPS) |

**Differences from Windows:** no fbxsdkpy; no Python 3.12 constraint; PyTorch comes from PyPI
(arm64 wheels ship MPS); device resolves `auto → cuda > mps > cpu`.

---

## 4. Text encoder model (LLaMA-3-8B, **cross-platform**)

The LLM2Vec text encoder needs **Meta-Llama-3-8B-Instruct** as its base (~16 GB). Two options,
**identical on Windows and macOS**:

### 1. Official (default, clean license)

`meta-llama/Meta-Llama-3-8B-Instruct` is gated by Meta:

1. Open the [model page](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) → "Agree and access repository".
2. Create a read token: <https://huggingface.co/settings/tokens>.
3. Log in inside the venv (downloaded automatically on first generation):
   - macOS / Linux: `<venv>/bin/hf auth login`
   - Windows: `<venv>\Scripts\hf.exe auth login`

### 2. Ungated mirror (development convenience, no request/login)

`NousResearch/Meta-Llama-3-8B-Instruct` is a byte-identical re-upload. Download it and repoint
the LLM2Vec adapters' base — **platform-independent** (only the `hf` path differs):

```bash
# macOS / Linux: HF="<venv>/bin/hf"        ;   Windows: HF="<venv>\Scripts\hf.exe"
export HF_HOME="<sibling of venv>/hf-cache"   # the cache the server reads, next to the venv
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

> The weights remain governed by the **Meta Llama-3 Community License** regardless of download
> source. The mirror is a development convenience; distribute via the official gated channel.

---

## 5. Runtime layout and uninstall

| Path (project-local macOS example) | Contents | Size |
|------|----------|------|
| `<venv>/` | Python venv + all pip packages | ~5 GB |
| `<venv sibling>/hf-cache/` | Hugging Face model cache (Kimodo + LLaMA-3-8B) | ~17 GB |
| `<venv sibling>/runtime/install.log` | install log | <1 MB |

Windows defaults to `~/.kimodo_venv` with models in `~/.cache/huggingface` (set `HF_HOME` to relocate).
**Uninstall:** delete the runtime folder (macOS project-local: `<repo>/.kimodo-runtime/`), or run
`powershell -File installer\uninstall.ps1` on Windows. The system Python is never modified.

### Offline model pack

If you pre-download a model pack, keep the **native Hugging Face cache tree** — do not copy weights alone:

```
<HF_HOME>/hub/
├── models--nvidia--Kimodo-SOMA-RP-v1/
│   ├── refs/main              ← required (snapshot sha)
│   ├── snapshots/<sha>/       ← full sha dir (config.json + *.safetensors)
│   └── blobs/
└── models--NousResearch--Meta-Llama-3-8B-Instruct/   (or meta-llama, per §4)
```

A missing `refs/main` or `snapshots/` makes huggingface_hub treat the model as uncached and re-download 17 GB.

---

## 6. Troubleshooting

**`GatedRepoError: meta-llama/...` at generation** — use §4 option 1 (request + login) or switch to §4 option 2 (ungated mirror).

**PowerShell "script not signed" (Windows)** — use `-ExecutionPolicy Bypass`, or `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**torch download times out / stalls at x/3 GB** — check bandwidth/firewall/proxy; retry with `-Proxy` (Win) or `KIMODO_PROXY` (Mac). Re-running is idempotent; completed large packages are skipped.

**CUDA unavailable (Windows, `cuda_available=False`)** — torch ≠ driver. Check `nvidia-smi`; RTX 50 (cc ≥ 12.0) needs cu128; reinstall `pip install torch ... --index-url https://download.pytorch.org/whl/cu128 --force-reinstall`.

**MPS unavailable (macOS)** — needs Apple Silicon + native arm64 Blender/Python; otherwise CPU fallback. Force with `KIMODO_DEVICE=cpu`.

**kimodo fails to build on arm64 (MotionCorrection / cmake)** — that C++ extension is x86-SSE only; the macOS installer auto-sets `SKIP_MOTION_CORRECTION_IN_SETUP=1`. The add-on server skips official `post_processing` when `motion_correction` is unavailable so generation does not fail; enable official post-processing in the panel only after installing a compatible build.

**Out of disk / move models** — set `HF_HOME=<drive>/hf-cache` (all HF tools honor it; macOS project-local install already keeps it next to the venv).

**Workflow** — N-panel > Runtime Install > **Refresh** to see which row is red > **Open log** for `install.log` > share the last 100 lines + precheck output.

---

## 7. Manual install (advanced / Linux)

```bash
# 1. venv
python3.11 -m venv ~/.kimodo_venv
source ~/.kimodo_venv/bin/activate            # Windows: .\.kimodo_venv\Scripts\Activate.ps1

# 2. PyTorch (macOS/Linux: default PyPI ships MPS/CPU; Windows CUDA uses --index-url cu128)
pip install torch torchvision torchaudio      # Windows: --index-url https://download.pytorch.org/whl/cu128

# 3. kimodo (MPS fork; skip the C++ extension on arm64)
SKIP_MOTION_CORRECTION_IN_SETUP=1 pip install "kimodo @ git+https://github.com/atticus-lv/kimodo.git"

# 4. server deps
pip install fastapi "uvicorn[standard]" pydantic scipy bvhio requests

# 5. log in to HF (or use the ungated mirror per §4 option 2)
python -m huggingface_hub.commands.huggingface_cli login
```

---

## 8. Changelog

- **2026-06 v2.0 — macOS / Apple Silicon (Metal/MPS) support + refactor**
  - In-Blender retargeting (`retarget/bpy_retarget.py`); no Autodesk FBX SDK on any platform
  - New `install_mac.sh` (venv + PyTorch/MPS + kimodo + server deps, no fbxsdkpy)
  - Server device resolution `auto → cuda > mps > cpu` (`KIMODO_DEVICE` override)
  - Contained runtime: venv + logs + model cache anchored to the venv path
  - Packaged as a Blender Extension (`blender_manifest.toml`); relicensed GPL-3.0-or-later
  - Docs merged into a single bilingual INSTALL; the ungated-mirror option is cross-platform
- 2026-04-17 v1.5 — fbxsdkpy import-name fix (`fbx`), precheck dual verification + DLL-unload segfault guard
- 2026-04-17 v1.4 — `-DryRun` wording; precheck `--no-venv-probe` no longer reports false missing
- 2026-04-16 v1.3 — end-to-end `-Proxy`; precheck HF cache-tree check; offline-pack layout; non-ASCII path warning
- 2026-04-16 v1.2 — `Invoke-Download` (BITS+IWR fallback/retry); installer no longer hangs silently; pip retries/timeout
- 2026-04-16 v1.1 — `install.cmd` launcher; fbx_runner version fix
- 2026-04-14 v1.0 — initial install chain (install.ps1 + precheck.py + Blender UI panel)
