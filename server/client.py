"""HTTP client for communicating with the Kimodo FastAPI server."""

import json
import urllib.request
import urllib.error
from typing import Any, Optional


class KimodoClient:
    """Lightweight HTTP client — no external deps, uses stdlib only."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    # ── Health & Status ──

    def health(self, timeout: float = 3.0) -> bool:
        try:
            resp = self._get("/health", timeout=timeout)
            return resp.get("status") == "ok"
        except Exception:
            return False

    def status(self, timeout: float = 3.0) -> Optional[dict]:
        """Full /health dict ({status, model_loaded}); None if unreachable."""
        try:
            return self._get("/health", timeout=timeout)
        except Exception:
            return None

    def version(self, timeout: float = 3.0) -> Optional[dict]:
        try:
            return self._get("/version", timeout=timeout)
        except Exception:
            return None

    def progress(self, timeout: float = 1.5) -> Optional[dict]:
        """Poll live generation progress: {running, phase, step, total}. None if unreachable."""
        try:
            return self._get("/progress", timeout=timeout)
        except Exception:
            return None

    # ── Generation ──

    def generate(
        self,
        prompt: str,
        duration: float = 6.0,
        model: str = "Kimodo-SOMA-RP-v1",
        num_samples: int = 1,
        seed: int = -1,
        diffusion_steps: int = 100,
        output_bvh: bool = False,
        num_frames: Optional[int] = None,
        segments: Optional[list[dict[str, Any]]] = None,
        constraints: Optional[list[dict[str, Any]]] = None,
        text_cfg: float = 2.0,
        constraint_cfg: float = 2.0,
        cfg_type: Optional[str] = None,
        first_heading_angle: Optional[float] = None,
        num_transition_frames: int = 5,
        post_processing: bool = False,
        root_margin: float = 0.04,
    ) -> dict:
        """Request motion generation. Returns dict with 'npz_path' / 'bvh_path' and metadata.

        Default is NPZ-only (output_bvh=False) since FBX retarget consumes NPZ.
        If num_frames is given, it takes precedence over duration (both are sent so
        server can validate).
        """
        payload = {
            "prompt": prompt,
            "duration": duration,
            "model": model,
            "num_samples": num_samples,
            "seed": seed,
            "diffusion_steps": diffusion_steps,
            "output_bvh": output_bvh,
            "constraints": constraints or [],
            "text_cfg": text_cfg,
            "constraint_cfg": constraint_cfg,
            "num_transition_frames": num_transition_frames,
            "post_processing": post_processing,
            "root_margin": root_margin,
        }
        if num_frames is not None:
            payload["num_frames"] = int(num_frames)
        if segments:
            payload["segments"] = segments
            payload["multi_prompt"] = True
        if cfg_type:
            payload["cfg_type"] = cfg_type
        if first_heading_angle is not None:
            payload["first_heading_angle"] = float(first_heading_angle)
        # Generation can take 30-180s (model load + inference). Use long timeout.
        return self._post("/generate", payload, timeout=600.0)

    def generate_status(self, job_id: str) -> dict:
        return self._get(f"/generate/status/{job_id}", timeout=5.0)

    # ── Model memory management ──

    def load_model(self, model: str = "Kimodo-SOMA-RP-v1", timeout: float = 600.0) -> dict:
        """Eagerly load a model into device memory (first load ~40-60s)."""
        return self._post("/load", {"model": model}, timeout=timeout)

    def unload_model(self) -> dict:
        return self._post("/unload", {}, timeout=30.0)

    # ── HTTP Helpers ──

    def _get(self, path: str, timeout: float = 10.0) -> dict:
        url = self.base_url + path
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(_format_http_error(e)) from e

    def _post(self, path: str, data: dict, timeout: float = 30.0) -> dict:
        url = self.base_url + path
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(_format_http_error(e)) from e


def _format_http_error(e: urllib.error.HTTPError) -> str:
    body = ""
    try:
        body = e.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        if isinstance(data, dict) and data.get("detail"):
            body = str(data["detail"])
    except Exception:
        pass
    suffix = f": {body}" if body else ""
    return f"HTTP {e.code} {e.reason}{suffix}"
