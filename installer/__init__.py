"""Kimodo Motion installer package.

This package contains the one-click install / uninstall / diagnostics
tooling for the Kimodo runtime (Python venv + torch/cu128 + kimodo
+ fbxsdkpy + fastapi server deps).

Design:
  - install.ps1 / uninstall.ps1  — PowerShell driver (user-facing)
  - precheck.py                  — Pure stdlib diagnostics, called from
                                   Blender UI and from install.ps1 verify
  - download_model.py            — HF / HF-Mirror / ModelScope fallback
  - download_rokoko_template.py  — Fetch free Rokoko T-pose template
  - bootstrap_python.ps1         — Silent Python 3.12 install fallback

The Blender UI (ui/install_panel.py) delegates to install.ps1
via subprocess + CREATE_NEW_CONSOLE so the user sees real progress.

License note:
  - Autodesk FBX SDK (fbxsdkpy) is NEVER bundled in the addon zip.
    It is always downloaded on demand from the INRIA GitLab simple
    index (https://gitlab.inria.fr/api/v4/projects/18692/packages/pypi/simple).
  - Kimodo is Apache-2.0 and LLaMA-3-8B is gated; user must supply
    their own HuggingFace token.
"""

__all__ = ["precheck"]
