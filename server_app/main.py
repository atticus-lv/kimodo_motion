"""Kimodo FastAPI server — runs in a separate Python 3.10 venv, NOT inside Blender.

Launch: python main.py
Env vars: KIMODO_HOST (default 127.0.0.1), KIMODO_PORT (default 8790)
"""

import os
import sys
import time
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(title="Kimodo Motion Server", version="0.1.0")

# ── Global state ──
_model = None
_model_name = None
_load_time = None

# Live generation progress, polled by the Blender panel via GET /progress.
# phase: "idle" | "loading" (model -> device) | "sampling" (diffusion) | "done".
# Mutated from the /generate worker thread, read from the /progress thread; a
# plain dict of scalars is safe enough here (no compound invariant to protect).
_progress = {"running": False, "phase": "idle", "step": 0, "total": 0}


def _make_progress_bar():
    """Return a tqdm-shaped callable that mirrors the diffusion loop into _progress.

    Kimodo threads ``progress_bar`` down to ``for i in progress_bar(indices)``; we
    pass this instead of tqdm so each denoising step bumps the shared counter the
    panel polls. Accepts tqdm's extra args/kwargs (desc=, total=, ...) and ignores
    them.
    """

    def bar(iterable, *args, **kwargs):
        items = list(iterable)
        _progress["total"] = len(items)
        _progress["step"] = 0
        for i, item in enumerate(items):
            _progress["step"] = i + 1
            yield item

    return bar


# ── Request / Response models ──


class SegmentRequest(BaseModel):
    prompt: str
    num_frames: Optional[int] = Field(None, ge=59, le=299)
    duration: Optional[float] = Field(None, ge=2.0, le=10.0)


class GenerateRequest(BaseModel):
    prompt: str
    duration: float = Field(6.0, ge=2.0, le=10.0)
    num_frames: Optional[int] = Field(None, ge=59, le=299)  # Kimodo官方范围
    model: str = "Kimodo-SOMA-RP-v1"
    num_samples: int = Field(1, ge=1, le=8)
    seed: int = -1
    diffusion_steps: int = Field(100, ge=10, le=200)
    output_bvh: bool = True
    segments: list[SegmentRequest] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    multi_prompt: bool = False
    text_cfg: float = Field(2.0, ge=0.0, le=20.0)
    constraint_cfg: float = Field(2.0, ge=0.0, le=20.0)
    cfg_type: Optional[str] = None
    first_heading_angle: Optional[float] = None
    num_transition_frames: int = Field(5, ge=1, le=30)
    post_processing: bool = False
    root_margin: float = Field(0.04, ge=0.0, le=1.0)


class LoadRequest(BaseModel):
    model: str = "Kimodo-SOMA-RP-v1"


class GenerateResponse(BaseModel):
    bvh_path: str = ""
    npz_path: str = ""
    num_frames: int = 0
    duration: float = 0.0
    model: str = ""
    prompt: str = ""
    num_samples: int = 1
    segments: list[dict[str, Any]] = Field(default_factory=list)
    constraints_applied: int = 0
    warnings: list[str] = Field(default_factory=list)


# ── Endpoints ──


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None}


@app.get("/version")
def version():
    return {
        "server": "0.1.0",
        "model": _model_name or "none",
        "load_time": _load_time,
    }


@app.get("/progress")
def progress():
    """Live generation progress for the Blender panel's progress bar."""
    return dict(_progress)


def _load_model_sync(model_name: str) -> str:
    """Load ``model_name`` into device memory if not already current. Returns the
    resolved device string. Raises on failure. Shared by /load and /generate."""
    global _model, _model_name, _load_time
    if _model is not None and _model_name == model_name:
        return str(getattr(_model, "device", "?"))

    from kimodo import load_model

    # Device resolution: prefer CUDA, then Apple Silicon MPS (Metal), then CPU.
    # Uses the kimodo fork's device_utils when available (MPS support); falls
    # back to a plain CUDA/CPU probe on upstream kimodo. Override with KIMODO_DEVICE.
    requested = os.environ.get("KIMODO_DEVICE", "auto")
    try:
        from kimodo.device_utils import resolve_torch_device

        device = resolve_torch_device(requested)
    except Exception:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[Kimodo] Loading model: {model_name} on device '{device}' ...")
    t0 = time.time()
    _model = load_model(model_name, device=device)
    _model_name = model_name
    _load_time = round(time.time() - t0, 1)
    print(f"[Kimodo] Model loaded in {_load_time}s")
    return device


def _clamped_num_frames(num_frames: Optional[int], duration: Optional[float]) -> int:
    if num_frames is not None:
        frames = int(num_frames)
    else:
        seconds = 6.0 if duration is None else float(duration)
        frames = int(seconds * 30 - 1)
    return max(59, min(299, frames))


def _normalized_generation(req: GenerateRequest) -> tuple[Any, Any, bool, list[dict[str, Any]], float]:
    if req.segments:
        prompts = [seg.prompt for seg in req.segments]
        frames = [_clamped_num_frames(seg.num_frames, seg.duration) for seg in req.segments]
        duration = sum((f + 1) / 30.0 for f in frames)
        return prompts, frames, True, [seg.dict() for seg in req.segments], duration
    frames = _clamped_num_frames(req.num_frames, req.duration)
    return req.prompt, frames, bool(req.multi_prompt), [], (frames + 1) / 30.0


def _load_constraints(constraints: list[dict[str, Any]]):
    if not constraints:
        return []
    try:
        from kimodo.constraints import load_constraints_lst
    except Exception as e:
        raise RuntimeError(f"Installed kimodo has no constraints loader: {e}") from e
    try:
        return load_constraints_lst(constraints, _model.skeleton)
    except Exception:
        import json

        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="kimodo_constraints_", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(constraints, f)
            return load_constraints_lst(tmp_path, _model.skeleton)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


def _sample_count_from_npz(npz_data: dict) -> int:
    posed = npz_data.get("posed_joints")
    if posed is not None and getattr(posed, "ndim", 0) == 4:
        return int(posed.shape[0])
    return 1


def _motion_correction_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("motion_correction") is not None


def _call_model_with_progress_fallback(kwargs: dict):
    try:
        return _model(**kwargs)
    except TypeError as te:
        # Older local installs may not expose progress_bar yet. Keep this narrow:
        # official generation/constraint kwargs should fail loudly if missing.
        if "progress_bar" not in str(te):
            raise
        retry_kwargs = dict(kwargs)
        retry_kwargs.pop("progress_bar", None)
        return _model(**retry_kwargs)


@app.post("/load")
def load(req: LoadRequest):
    """Eagerly load a model into memory so the next /generate skips the load wait."""
    if _model is not None and _model_name == req.model:
        return {"message": "already loaded", "model": _model_name,
                "load_time": _load_time, "model_loaded": True}
    _progress.update(running=True, phase="loading", step=0, total=0)
    try:
        device = _load_model_sync(req.model)
    except Exception as e:
        _progress.update(running=False, phase="idle")
        raise HTTPException(status_code=500, detail=f"Model load failed: {e}")
    _progress.update(running=False, phase="done")
    return {"message": "loaded", "model": _model_name, "device": device,
            "load_time": _load_time, "model_loaded": True}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    global _model, _model_name, _load_time

    # Translation happens client-side (Blender plugin); server gets English prompt as-is.

    # Only report a "loading" phase when the model actually has to be loaded. A warm
    # model (already in memory from a prior /load or /generate) goes straight to
    # "sampling" so the panel doesn't flash "加载模型到设备…" on every generation.
    needs_load = _model is None or _model_name != req.model
    _progress.update(
        running=True,
        phase="loading" if needs_load else "sampling",
        step=0,
        total=req.diffusion_steps,
    )

    # Lazy load model
    if needs_load:
        try:
            _load_model_sync(req.model)
        except Exception as e:
            _progress.update(running=False, phase="idle")
            raise HTTPException(status_code=500, detail=f"Model load failed: {e}")

    # Generate
    _progress.update(phase="sampling", step=0, total=req.diffusion_steps)
    generation_warnings = []
    try:
        prompts, num_frames, multi_prompt, segment_meta, duration = _normalized_generation(req)
        constraint_lst = _load_constraints(req.constraints)
        post_processing = bool(req.post_processing)
        if post_processing and not _motion_correction_available():
            generation_warnings.append(
                "post_processing skipped: motion_correction is unavailable in this runtime"
            )
            print(f"[Kimodo] WARN: {generation_warnings[-1]}")
            post_processing = False
        kwargs = {
            "prompts": prompts,  # Kimodo accepts str or list[str]
            "num_frames": num_frames,
            "num_denoising_steps": req.diffusion_steps,
            "num_samples": req.num_samples,
            "multi_prompt": multi_prompt,
            "constraint_lst": constraint_lst,
            "cfg_weight": [req.text_cfg, req.constraint_cfg],
            "num_transition_frames": req.num_transition_frames,
            "post_processing": post_processing,
            "root_margin": req.root_margin,
            # Drives GET /progress; ignored shape-wise (tqdm-compatible callable).
            "progress_bar": _make_progress_bar(),
        }
        if req.cfg_type:
            kwargs["cfg_type"] = req.cfg_type
        if req.first_heading_angle is not None:
            kwargs["first_heading_angle"] = req.first_heading_angle
        if req.seed >= 0:
            import torch

            torch.manual_seed(req.seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed(req.seed)

        try:
            output = _call_model_with_progress_fallback(kwargs)
        except Exception as model_e:
            msg = str(model_e)
            if post_processing and "motion_correction" in msg:
                generation_warnings.append(
                    "post_processing skipped: motion_correction failed to load in this runtime"
                )
                print(f"[Kimodo] WARN: {generation_warnings[-1]}")
                kwargs = dict(kwargs)
                kwargs["post_processing"] = False
                _progress.update(phase="sampling", step=0, total=req.diffusion_steps)
                output = _call_model_with_progress_fallback(kwargs)
            else:
                raise
        _progress.update(running=False, phase="done")
    except Exception as e:
        _progress.update(running=False, phase="idle")
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    # Save output
    output_dir = os.path.join(tempfile.gettempdir(), "kimodo_outputs")
    os.makedirs(output_dir, exist_ok=True)
    stem = os.path.join(output_dir, f"kimodo_{uuid.uuid4().hex[:8]}")

    bvh_path = ""
    npz_path = f"{stem}.npz"

    try:
        # Save NPZ (always)
        import numpy as np
        import torch

        npz_data = {}
        for k, v in output.items():
            if isinstance(v, torch.Tensor):
                npz_data[k] = v.cpu().numpy()
            elif hasattr(v, "shape"):
                npz_data[k] = v

        # Attach skeleton metadata so downstream FBX retarget can run standalone
        try:
            skel = _model.skeleton
            num_joints = npz_data["posed_joints"].shape[-2]
            skel_for_meta = skel
            if hasattr(skel, "somaskel77") and num_joints == 77:
                skel_for_meta = skel.somaskel77
            elif hasattr(skel, "somaskel30") and num_joints == 30:
                skel_for_meta = skel.somaskel30 if hasattr(skel, "somaskel30") else skel

            parents_t = skel_for_meta.joint_parents
            npz_data["joint_parents"] = (
                parents_t.cpu().numpy()
                if hasattr(parents_t, "cpu")
                else np.asarray(parents_t)
            )

            bone_names = getattr(skel_for_meta, "bone_order_names", None)
            if bone_names:
                npz_data["joint_names"] = np.array(list(bone_names), dtype=np.str_)

            nj = getattr(skel_for_meta, "neutral_joints", None)
            if nj is not None:
                npz_data["neutral_joints"] = (
                    nj.cpu().numpy() if hasattr(nj, "cpu") else np.asarray(nj)
                )

            npz_data["fps"] = float(_model.fps)
            npz_data["skeleton_name"] = str(getattr(skel, "name", "soma"))
        except Exception as meta_e:
            print(f"[Kimodo] WARN: skeleton metadata save failed: {meta_e}")

        sample_count = _sample_count_from_npz(npz_data)
        np.savez(npz_path, **npz_data)

        # Save BVH if requested (SOMA models only)
        if req.output_bvh:
            try:
                from kimodo.exports.bvh import save_motion_bvh
                from kimodo.skeleton import SOMASkeleton30

                skeleton = _model.skeleton
                if isinstance(skeleton, SOMASkeleton30):
                    skeleton = skeleton.somaskel77.to(_model.device)

                # Output already contains local_rot_mats and root_positions
                # Shape: (frames, joints, 3, 3) and (frames, 3) — no batch dim
                local_rots = output["local_rot_mats"]
                root_pos = output["root_positions"]
                if not isinstance(local_rots, torch.Tensor):
                    local_rots = torch.from_numpy(local_rots)
                if not isinstance(root_pos, torch.Tensor):
                    root_pos = torch.from_numpy(root_pos)
                if local_rots.ndim == 5:
                    local_rots = local_rots[0]
                if root_pos.ndim == 3:
                    root_pos = root_pos[0]

                bvh_path = f"{stem}.bvh"
                save_motion_bvh(
                    bvh_path,
                    local_rots.to(_model.device),
                    root_pos.to(_model.device),
                    skeleton=skeleton,
                    fps=_model.fps,
                )
                print(f"[Kimodo] BVH saved: {bvh_path}")
            except Exception as e:
                print(f"[Kimodo] BVH export failed: {e}")
                import traceback

                traceback.print_exc()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Output save failed: {e}")

    return GenerateResponse(
        bvh_path=bvh_path,
        npz_path=npz_path,
        num_frames=sum(num_frames) if isinstance(num_frames, list) else num_frames,
        duration=duration,
        model=req.model,
        prompt=" | ".join(prompts) if isinstance(prompts, list) else req.prompt,
        num_samples=sample_count,
        segments=segment_meta,
        constraints_applied=len(req.constraints),
        warnings=generation_warnings,
    )


@app.post("/unload")
def unload():
    global _model, _model_name, _load_time
    if _model is not None:
        del _model
        _model = None
        _model_name = None
        _load_time = None
        import torch

        torch.cuda.empty_cache()
        return {"message": "Model unloaded, VRAM released"}
    return {"message": "No model loaded"}


# ── Entry point ──

if __name__ == "__main__":
    host = os.environ.get("KIMODO_HOST", "127.0.0.1")
    port = int(os.environ.get("KIMODO_PORT", "8790"))
    print(f"[Kimodo Server] Starting on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
