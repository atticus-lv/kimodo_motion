# Changelog

All notable changes to the **Blender Kimodo Motion** addon.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — macOS / Apple Silicon (Metal/MPS) support

Brings the addon to macOS by removing the Autodesk FBX SDK dependency and routing
inference through Metal/MPS. The retarget core is validated on local Blender against
real Mixamo rigs + real Kimodo NPZ; full server-on-MPS end-to-end is pending a first
on-device install + generation.

### Added
- `retarget/bpy_retarget.py` — in-Blender retarget driven directly from the NPZ
  (bpy + numpy + mathutils). No Autodesk FBX SDK, no venv subprocess; works on
  Apple Silicon. Ports the validated rest-delta rotation retarget (SOMA Y-up →
  Blender Z-up basis fix; off = target rest; root trajectory scaled by body-bone
  height ratio, anchored to the first frame). One named Action per sample.
- `installer/install_mac.sh` — macOS one-click runtime installer: locate Python
  3.10–3.13, create the venv, install PyTorch (PyPI arm64 wheels ship Metal/MPS,
  with pypi.org as fallback index), kimodo (MPS fork by default), FastAPI server
  deps. No fbxsdkpy. Options via `KIMODO_VENV` / `KIMODO_PIP_MIRROR` / `KIMODO_PROXY`
  / `KIMODO_GIT_URL` / `KIMODO_HF_HOME`.
- macOS install steps folded into the unified `INSTALL.md` / `INSTALL_EN.md` (project-contained install; uninstall = delete one folder).

### Changed
- `ui/operators.py` — `_retarget_and_apply` now calls `bpy_retarget.retarget_sample`
  per sample instead of export-FBX → fbxsdkpy subprocess → re-import.
- `server_app/main.py` — device is resolved (auto → cuda > mps > cpu) via the kimodo
  fork's `device_utils`, no longer hardcoded `cuda`; override with `KIMODO_DEVICE`.
- `ui/install_panel.py` — install / download-model / open-log operators branch per
  platform (Terminal via osascript on macOS); precheck runs in the venv on macOS too
  (was hardcoded `Scripts/python.exe`); status rows are platform-aware (Apple/Metal +
  MPS; "FBX retarget: built-in" on macOS).
- `installer/precheck.py` — reports torch MPS availability alongside CUDA.
- `server/manager.py` — launches the server with `HF_HOME` next to the venv so the
  ~17GB model cache stays in the runtime folder, not `~/.cache/huggingface`.

### Review fixes (high-effort review of the macOS diff)
- Retarget height-scale measured over **mapped body bones only** (control/IK/hair
  helpers no longer skew the root-motion scale).
- Regenerating a prompt **replaces** the same-named Action instead of leaving
  `use_fake_user` `.001` orphans.
- One `view_layer.update()` per frame instead of per bone (≈22× fewer depsgraph
  evaluations); FK output unchanged.
- `sample_index` clamped to the NPZ batch size (was an opaque numpy IndexError) —
  found during local Blender verification of the operator path.
- Warnings when `Hips` is missing/unmapped (previously silent no-root-motion).

## [0.1.0] — 2026-04-17

First public release. Complete end-to-end path verified (clean-machine install → Kimodo server → 51.4 s /generate → FBX-SDK retarget → 127 KB valid FBX output → Blender Action applied).

### Added
- One-click Windows installer (`installer/install.ps1` + `install.cmd` ASCII launcher for CJK paths).
- `installer/precheck.py` — GPU / venv / PyTorch / fbxsdkpy / kimodo / HF-token / HF-cache-integrity probe.
- `retarget/fbx_bridge.py` — Blender-side bridge: armature hash cache, FBX export, subprocess dispatch, FBX re-import, Action rename.
- `retarget/fbx_runner.py` — Python 3.12 subprocess CLI wrapping `vendor/kimodo_retarget/kimodo_retarget_fbx.py`.
- `retarget/mapping.py` — SOMA77 joint hierarchy + preset loader.
- `presets/{mixamo,vroid,mmd}.json` — bone mapping presets (23 SOMA body joints each).
- `translate/` — optional zh→en translation bridge (DeepSeek / OpenRouter / Moonshot / Qwen / OpenAI-compatible).
- `server_app/main.py` — FastAPI server (`/health`, `/generate`, `/unload`) running inside `kimodo_venv`.
- `server/manager.py` + `server/client.py` — lifecycle and HTTP client used by Blender.
- UI panels: target armature status, generation params, auto-detected skeleton preset, generated Actions sub-panel.
- `ui/install_panel.py` — `[One-click install runtime]`, `[Recheck]`, `[View log]`, `[Download model]`.

### Install pipeline robustness (battle-hardened in 5 rounds)
- `Invoke-Download` PowerShell helper: BITS with fallback to `Invoke-WebRequest`, TimeoutSec=600, 3 retries, proxy-aware.
- `-ForcePython` strict mode: throws on missing path or unsupported version (no silent download fallback).
- PyTorch install hardened with `--retries 5 --timeout 120 --progress-bar on`.
- fbxsdkpy pinned to `2020.3.7.post1`; no "latest" fallback to avoid drift to 2024+ incompatible wheels.
- `--Proxy` wired end-to-end (BITS ProxyList, IWR `-Proxy`, pypi probe `-Proxy`).
- HuggingFace cache tree integrity check (`refs/main` + `snapshots/<sha>/`) in precheck.
- USERPROFILE non-ASCII character warning at install start.
- `-DryRun` now prints "DRY-RUN complete — no changes made" instead of misleading "Kimodo runtime installed".

### Fixed (3 P0 bugs discovered during real-machine acceptance test)
- `precheck.py`: changed `import fbxsdkpy` → `import fbx` (pypi name ≠ import name; Autodesk convention — package ships `fbx.pyd`).
- `installer/download_rokoko_template.py`: same `import fbxsdkpy as fbx` → `import fbx` fix; T-pose fallback now actually works.
- `install.ps1` Step 6: metadata-only check replaced with "`import fbx` success **AND** version == pin" dual verification; stale dist-info (e.g. leftover 2020.1.post2) now correctly triggers reinstall.
- `precheck.py` `_VENV_PROBE_SCRIPT`: append `os._exit(0)` to skip DLL-unload shutdown → subprocess no longer returns 0xC0000005 from fbxsdkpy's known Windows segfault.
- `precheck.py` `_probe_venv`: trust stdout JSON regardless of return code (subprocess may crash on shutdown after writing output).

### Known limitations
- `Neck1`/`Neck2` both map to a single target `Neck` bone in Mixamo/VRoid/MMD presets — one of the two rotation sources is dropped by downstream dict overwrite (design limit inherited from Kimodo's upstream mapping).
- `translate_if_needed()` runs synchronously in the UI thread; freezes Blender for up to `translate_timeout` seconds (default 15). Known; will move to background thread.
- `_get_temp_collection()` defined but never called; imported FBX objects land in scene collection and are cleaned manually afterwards. Cosmetic.
- macOS untested; fbxsdkpy has no arm64 cp312 wheel upstream.
