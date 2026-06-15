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
from typing import Optional

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


# ── Request / Response models ──


class GenerateRequest(BaseModel):
    prompt: str
    duration: float = Field(6.0, ge=2.0, le=10.0)
    num_frames: Optional[int] = Field(None, ge=59, le=299)  # Kimodo官方范围
    model: str = "Kimodo-SOMA-RP-v1"
    num_samples: int = Field(1, ge=1, le=8)
    seed: int = -1
    diffusion_steps: int = Field(100, ge=10, le=200)
    output_bvh: bool = True


class GenerateResponse(BaseModel):
    bvh_path: str = ""
    npz_path: str = ""
    num_frames: int = 0
    duration: float = 0.0
    model: str = ""
    prompt: str = ""


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


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    global _model, _model_name, _load_time

    # Translation happens client-side (Blender plugin); server gets English prompt as-is.

    # Lazy load model
    if _model is None or _model_name != req.model:
        try:
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
            print(f"[Kimodo] Loading model: {req.model} on device '{device}' ...")
            t0 = time.time()
            _model = load_model(req.model, device=device)
            _model_name = req.model
            _load_time = round(time.time() - t0, 1)
            print(f"[Kimodo] Model loaded in {_load_time}s")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Model load failed: {e}")

    # Generate
    try:
        # Kimodo 官方 demo 用 `duration * fps - 1` 作为 num_frames；新客户端可直接传 num_frames
        if req.num_frames is not None:
            num_frames = int(req.num_frames)
        else:
            num_frames = int(req.duration * 30 - 1)
        # Clamp to Kimodo's valid range
        num_frames = max(59, min(299, num_frames))
        kwargs = {
            "prompts": req.prompt,  # Kimodo accepts str or list[str]
            "num_frames": num_frames,
            "num_denoising_steps": req.diffusion_steps,
        }
        if req.seed >= 0:
            import torch

            torch.manual_seed(req.seed)
            torch.cuda.manual_seed(req.seed)

        output = _model(**kwargs)
    except Exception as e:
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
                npz_data["joint_names"] = np.array(list(bone_names), dtype=object)

            nj = getattr(skel_for_meta, "neutral_joints", None)
            if nj is not None:
                npz_data["neutral_joints"] = (
                    nj.cpu().numpy() if hasattr(nj, "cpu") else np.asarray(nj)
                )

            npz_data["fps"] = float(_model.fps)
            npz_data["skeleton_name"] = str(getattr(skel, "name", "soma"))
        except Exception as meta_e:
            print(f"[Kimodo] WARN: skeleton metadata save failed: {meta_e}")

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
        num_frames=num_frames,
        duration=req.duration,
        model=req.model,
        prompt=req.prompt,
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
