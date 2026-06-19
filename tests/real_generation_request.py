"""Run one real Kimodo generation through the local FastAPI server.

This is intentionally not a unit test: it loads the real Kimodo model/runtime and
requests a constrained generation. Output metadata is written to
/private/tmp/kimodo_real_generation_result.json for the follow-up Blender visual
retarget pass.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.client import KimodoClient  # noqa: E402

BASE_URL = os.environ.get("KIMODO_TEST_SERVER", "http://127.0.0.1:8791")
OUT_JSON = Path("/private/tmp/kimodo_real_generation_result.json")


def wait_for_server(client: KimodoClient, timeout_s: float = 120.0) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        status = client.status(timeout=2.0)
        if status and status.get("status") == "ok":
            return
        time.sleep(1.0)
    raise TimeoutError(f"Kimodo server did not become ready: {BASE_URL}")


def main() -> None:
    client = KimodoClient(BASE_URL)
    wait_for_server(client)
    t0 = time.time()
    result = client.generate(
        prompt="A person walks forward with steady steps.",
        duration=2.0,
        num_frames=59,
        model="Kimodo-SOMA-RP-v1",
        num_samples=1,
        seed=1234,
        diffusion_steps=50,
        output_bvh=False,
        constraints=[
            {
                "type": "root2d",
                "frame_indices": [0, 29, 58],
                "smooth_root_2d": [[0.0, 0.0], [0.0, 0.75], [0.0, 1.5]],
            }
        ],
        post_processing=True,
        text_cfg=2.0,
        constraint_cfg=2.5,
        root_margin=0.04,
    )
    result["elapsed_s"] = round(time.time() - t0, 2)
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"REAL_GENERATION_RESULT {OUT_JSON}")


if __name__ == "__main__":
    main()

