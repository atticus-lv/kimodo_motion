@echo off
REM ============================================================
REM Kimodo Motion — One-click environment setup (Windows)
REM Creates Python 3.10 venv, installs PyTorch cu128 + Kimodo
REM ============================================================

setlocal

set VENV_PATH=%USERPROFILE%\.kimodo_venv
set PYTHON=python

echo.
echo === Kimodo Motion Environment Setup ===
echo.

REM Check Python version
%PYTHON% --version 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10 first.
    echo         https://www.python.org/downloads/release/python-31011/
    pause
    exit /b 1
)

REM Create venv
if not exist "%VENV_PATH%\Scripts\python.exe" (
    echo [1/5] Creating virtual environment at %VENV_PATH% ...
    %PYTHON% -m venv "%VENV_PATH%"
) else (
    echo [1/5] Virtual environment already exists at %VENV_PATH%
)

set PIP=%VENV_PATH%\Scripts\pip.exe
set VPYTHON=%VENV_PATH%\Scripts\python.exe

REM Upgrade pip
echo [2/5] Upgrading pip ...
"%VPYTHON%" -m pip install --upgrade pip --quiet

REM Install PyTorch cu128
echo [3/5] Installing PyTorch (cu128 for RTX 5090) ...
"%PIP%" install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 --quiet

REM Install Kimodo
echo [4/5] Installing Kimodo ...
"%PIP%" install "kimodo[all] @ git+https://github.com/nv-tlabs/kimodo.git" --quiet

REM Install server dependencies
echo [5/5] Installing server dependencies ...
"%PIP%" install fastapi "uvicorn[standard]" pydantic --quiet

echo.
echo === Setup Complete ===
echo.
echo Venv path: %VENV_PATH%
echo Python:    %VPYTHON%
echo.
echo IMPORTANT: You need a HuggingFace token for LLaMA 3.
echo Run:  "%VPYTHON%" -m huggingface_hub.commands.huggingface_cli login
echo.
pause
