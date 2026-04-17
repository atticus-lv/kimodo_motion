<#
.SYNOPSIS
    Kimodo Motion — One-click runtime installer (Windows, idempotent).

.DESCRIPTION
    Bootstraps a complete Kimodo inference runtime:
      1. Detect / install Python 3.12
      2. Create ~/.kimodo_venv
      3. Auto-pick CUDA channel (cu128 for sm_89+, cu121 for older)
      4. Install PyTorch, kimodo, fbxsdkpy (from INRIA gitlab), fastapi+uvicorn
      5. Run precheck.py for final verification
      6. Write log to ~/.kimodo_runtime/install.log

    LICENSE NOTE: fbxsdkpy (Autodesk FBX SDK wrapper) is NEVER bundled.
    It is pip-downloaded on demand from Inria's public PyPI registry.

.PARAMETER Proxy
    Optional HTTP/HTTPS proxy, e.g. http://127.0.0.1:7890 (Clash default).

.PARAMETER Mirror
    Model mirror channel: hf | hf-mirror | modelscope. Default: auto-detect.
    Affects HF_ENDPOINT used when kimodo first downloads models.

.PARAMETER PipMirror
    Pip mirror channel: pypi | tsinghua | aliyun. Default: auto-detect.
    Does NOT apply to PyTorch channel (always uses official) or INRIA.

.PARAMETER SkipTorch
    Skip PyTorch install (only for dev iteration).

.PARAMETER DryRun
    Print all actions without executing.

.PARAMETER ForcePython
    Force-use a specific Python 3.12 interpreter (bypasses auto-detect).

.EXAMPLE
    # Normal install (auto-detect everything)
    powershell -ExecutionPolicy Bypass -File install.ps1

.EXAMPLE
    # With Clash proxy + force HF-Mirror
    powershell -ExecutionPolicy Bypass -File install.ps1 -Proxy http://127.0.0.1:7890 -Mirror hf-mirror

.EXAMPLE
    # Dry-run to see what would happen
    powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun
#>

[CmdletBinding()]
param(
    [string]$Proxy = "",
    [ValidateSet("auto", "hf", "hf-mirror", "modelscope")]
    [string]$Mirror = "auto",
    [ValidateSet("auto", "pypi", "tsinghua", "aliyun")]
    [string]$PipMirror = "auto",
    [switch]$SkipTorch,
    [switch]$DryRun,
    [string]$ForcePython = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ─── Constants ────────────────────────────────────────────────
$RUNTIME_DIR = Join-Path $env:USERPROFILE ".kimodo_runtime"
$VENV_PATH   = Join-Path $env:USERPROFILE ".kimodo_venv"
$LOG_PATH    = Join-Path $RUNTIME_DIR "install.log"
$PYTHON_DIR  = Join-Path $RUNTIME_DIR "python"
$PY_VER      = "3.12.8"
$PY_INSTALLER_URL = "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-amd64.exe"
$INRIA_INDEX = "https://gitlab.inria.fr/api/v4/projects/18692/packages/pypi/simple"
$FBXSDKPY_VERSION = "2020.3.7.post1"   # Latest confirmed 2026-04-14

# ─── Logging ──────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $RUNTIME_DIR | Out-Null
function Write-Log {
    param([string]$Msg, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts][$Level] $Msg"
    Add-Content -Path $LOG_PATH -Value $line -Encoding UTF8
    $color = switch ($Level) {
        "ERROR"  { "Red" }
        "WARN"   { "Yellow" }
        "OK"     { "Green" }
        "STEP"   { "Cyan" }
        default  { "Gray" }
    }
    Write-Host $line -ForegroundColor $color
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Log "=== $Name ===" -Level STEP
    if ($DryRun) {
        Write-Log "[DRY-RUN] skip body of $Name"
        return
    }
    try {
        & $Body
    }
    catch {
        Write-Log "FAILED: $Name -> $($_.Exception.Message)" -Level ERROR
        Write-Log $_.ScriptStackTrace -Level ERROR
        throw
    }
}

# Helper: run a process, capture stdout+stderr, return (exitCode, output).
# Two footguns being sidestepped here:
#   1. `$Args` is a PS automatic variable — using it as param name collides
#      with the auto-var, so @Args splat can bind to empty. Renamed to CmdArgs.
#   2. Even `2>&1` triggers NativeCommandError when strictmode+Stop sees any
#      stderr output from a native exe. Temporarily relax ErrorActionPreference
#      around the call so normal stderr (banners, warnings) doesn't explode.
function Invoke-Exe {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [string[]]$CmdArgs = @()
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $File @CmdArgs 2>&1 | Out-String
    }
    finally {
        $ErrorActionPreference = $prev
    }
    return [PSCustomObject]@{
        ExitCode = $LASTEXITCODE
        Output = ($out -as [string])
    }
}

# Robust file download with BITS -> Invoke-WebRequest fallback, timeout, retry.
# Two real-world failures this handles:
#   1. Invoke-WebRequest hangs indefinitely on dead sockets — add -TimeoutSec.
#   2. Single-attempt download fails on flaky links — retry with backoff.
function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [int]$TimeoutSec = 600,
        [int]$Retries = 3,
        [string]$ProxyUrl = ""
    )
    $attempt = 0
    while ($attempt -lt $Retries) {
        $attempt++
        $start = Get-Date
        Write-Log "Download attempt $attempt/$Retries  $Uri -> $OutFile"
        try {
            $bits = Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue
            if ($bits) {
                # BITS: resume-capable, built-in progress.
                # winhttp proxy is separate from HTTP_PROXY env; must pass -ProxyList.
                $bitsArgs = @{
                    Source      = $Uri
                    Destination = $OutFile
                    DisplayName = "Kimodo Installer Download"
                    Description = "$Uri"
                    ErrorAction = "Stop"
                }
                if ($ProxyUrl) {
                    $bitsArgs["ProxyUsage"] = "Override"
                    $bitsArgs["ProxyList"]  = $ProxyUrl
                }
                Start-BitsTransfer @bitsArgs
            } else {
                # Fallback: IWR with timeout + progress + explicit proxy
                $oldPref = $ProgressPreference
                $ProgressPreference = 'Continue'
                try {
                    $iwrArgs = @{
                        Uri             = $Uri
                        OutFile         = $OutFile
                        UseBasicParsing = $true
                        TimeoutSec      = $TimeoutSec
                    }
                    if ($ProxyUrl) { $iwrArgs["Proxy"] = $ProxyUrl }
                    Invoke-WebRequest @iwrArgs
                } finally {
                    $ProgressPreference = $oldPref
                }
            }
            if (-not (Test-Path $OutFile)) {
                throw "Download completed but file missing: $OutFile"
            }
            $size = (Get-Item $OutFile).Length
            if ($size -lt 1024) {
                throw "Downloaded file too small ($size bytes) — likely error page"
            }
            $elapsed = ((Get-Date) - $start).TotalSeconds
            Write-Log ("Downloaded {0:N1} MB in {1:N1}s" -f ($size / 1MB), $elapsed) -Level OK
            return
        } catch {
            Write-Log "Attempt $attempt failed: $($_.Exception.Message)" -Level WARN
            if (Test-Path $OutFile) {
                Remove-Item $OutFile -Force -ErrorAction SilentlyContinue
            }
            if ($attempt -ge $Retries) {
                throw "Download failed after $Retries attempts: $Uri"
            }
            $wait = 5 * $attempt
            Write-Log "Retrying in ${wait}s..."
            Start-Sleep -Seconds $wait
        }
    }
}

# Probe a module inside a python interpreter without triggering PS error flow.
function Test-PyImport {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$Module
    )
    $code = "import $Module; ver=getattr($Module,'__version__',None);" +
            "import sys; " +
            "print(ver) if ver else (__import__('importlib.metadata', fromlist=['version']).version('$Module') if True else '')"
    $r = Invoke-Exe -File $PythonExe -CmdArgs @('-c', $code)
    if ($r.ExitCode -eq 0 -and $r.Output.Trim()) {
        return $r.Output.Trim().Split("`n")[0].Trim()
    }
    return $null
}

Write-Log "========== Kimodo Runtime Installer =========="
Write-Log "Args: Proxy=$Proxy Mirror=$Mirror PipMirror=$PipMirror SkipTorch=$SkipTorch DryRun=$DryRun"
Write-Log "Runtime dir: $RUNTIME_DIR"
Write-Log "Venv path:   $VENV_PATH"

# Warn on non-ASCII user profile paths. pip / setuptools / fbxsdkpy DLL load
# on Python 3.12 generally handles Unicode, but edge cases still occur.
$profileBytes = [System.Text.Encoding]::UTF8.GetBytes($env:USERPROFILE)
$hasNonAscii = $false
foreach ($b in $profileBytes) { if ($b -gt 127) { $hasNonAscii = $true; break } }
if ($hasNonAscii) {
    Write-Log "USERPROFILE contains non-ASCII chars: $env:USERPROFILE" -Level WARN
    Write-Log "  If wheel extract / venv creation fails, workaround:" -Level WARN
    Write-Log "  create English-named user, or pass `$env:TEMP=C:\Temp before install" -Level WARN
}

# ─── Proxy ────────────────────────────────────────────────────
if ($Proxy) {
    $env:HTTP_PROXY  = $Proxy
    $env:HTTPS_PROXY = $Proxy
    Write-Log "HTTP(S)_PROXY set to $Proxy"
}

# ─── Step 0: GPU check ────────────────────────────────────────
$CudaChannel = "cu128"
$Arch = "Unknown"
Invoke-Step "[0/8] Detect GPU" {
    $smiPath = (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    if (-not $smiPath) {
        Write-Log "nvidia-smi not found — Kimodo REQUIRES NVIDIA GPU" -Level ERROR
        throw "No NVIDIA driver detected"
    }
    $gpuCsv = & nvidia-smi --query-gpu=name,compute_cap,driver_version --format=csv,noheader
    Write-Log "GPU raw: $gpuCsv" -Level OK
    $parts = $gpuCsv.Split(",").Trim()
    $script:Arch = $parts[0]
    $cc = [double]$parts[1]
    $driver = $parts[2]
    # Channel selection:
    #   compute_cap >= 12.0 (Blackwell / RTX 50) -> cu128 mandatory
    #   compute_cap >= 8.9  (Ada / RTX 40)       -> cu128 preferred
    #   compute_cap >= 7.5  (Turing/Ampere)      -> cu121 fallback
    if ($cc -lt 7.5) {
        Write-Log "Compute cap $cc too old (<7.5). Kimodo needs RTX 20xx+" -Level ERROR
        throw "GPU too old"
    }
    if ($cc -ge 8.9) { $script:CudaChannel = "cu128" }
    else             { $script:CudaChannel = "cu121" }
    Write-Log "GPU=$Arch  cc=$cc  driver=$driver  -> channel=$($script:CudaChannel)" -Level OK
}

# ─── Step 1: Python 3.12 ──────────────────────────────────────
$PythonExe = $null
$SupportedPyVers = @("3.10","3.11","3.12","3.13")

function Get-PyVer {
    param([string]$Exe)
    $r = Invoke-Exe -File $Exe -CmdArgs @('-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if ($r.ExitCode -ne 0) { return $null }
    return $r.Output.Trim()
}

Invoke-Step "[1/8] Locate Python 3.12" {
    # Strict -ForcePython mode: if user passes it, we MUST use it.
    # Never silently fall back to downloading — that's what burned users.
    if ($ForcePython) {
        if (-not (Test-Path $ForcePython)) {
            throw "-ForcePython '$ForcePython' does not exist"
        }
        $ver = Get-PyVer $ForcePython
        if (-not $ver) {
            throw "-ForcePython '$ForcePython' failed to run"
        }
        if ($ver -notin $SupportedPyVers) {
            throw "-ForcePython '$ForcePython' is Python $ver; need one of $($SupportedPyVers -join ',')"
        }
        $script:PythonExe = $ForcePython
        Write-Log "Using -ForcePython: $ForcePython (Python $ver)" -Level OK
        return
    }

    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python313\python.exe",
        "$env:USERPROFILE\.pyenv\pyenv-win\versions\3.12.8\python.exe",
        "$env:USERPROFILE\.pyenv\pyenv-win\versions\3.12.4\python.exe",
        "F:\anaconda\python.exe",
        "$PYTHON_DIR\python.exe"
    )
    # Also check `py -3.12`
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $r = Invoke-Exe -File "py" -CmdArgs @('-3.12', '-c', 'import sys; print(sys.executable)')
        if ($r.ExitCode -eq 0 -and $r.Output.Trim()) {
            $pyExe = $r.Output.Trim()
            if ($pyExe -and (Test-Path $pyExe)) { $candidates += $pyExe }
        }
    }

    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $ver = Get-PyVer $c
            if ($ver -and ($ver -in $SupportedPyVers)) {
                $script:PythonExe = $c
                Write-Log "Found Python $ver at $c" -Level OK
                return
            }
        }
    }

    # Not found — install silently to $PYTHON_DIR
    Write-Log "Python 3.10-3.13 not found. Downloading $PY_VER installer..." -Level WARN
    $installer = Join-Path $env:TEMP "python-$PY_VER-amd64.exe"
    if (-not (Test-Path $installer)) {
        try {
            Invoke-Download -Uri $PY_INSTALLER_URL -OutFile $installer `
                -TimeoutSec 600 -Retries 3 -ProxyUrl $Proxy
        } catch {
            Write-Log "Python installer 下载失败. Try:" -Level ERROR
            Write-Log "  1. 加代理重跑: install.cmd -Proxy http://127.0.0.1:7890" -Level ERROR
            Write-Log "  2. 手动下载 $PY_INSTALLER_URL 放到 $installer 再重跑" -Level ERROR
            throw
        }
    }
    # Silent per-user install into RUNTIME_DIR/python
    # Using /passive so user can see progress; if we want fully silent use /quiet
    New-Item -ItemType Directory -Force -Path $PYTHON_DIR | Out-Null
    $installArgs = @(
        "/passive",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_launcher=0",
        "Include_test=0",
        "Include_doc=0",
        "Shortcuts=0",
        "TargetDir=$PYTHON_DIR"
    )
    Write-Log "Running: $installer $($installArgs -join ' ')"
    $proc = Start-Process -FilePath $installer -ArgumentList $installArgs -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "Python installer exited with code $($proc.ExitCode)"
    }
    $script:PythonExe = Join-Path $PYTHON_DIR "python.exe"
    if (-not (Test-Path $script:PythonExe)) {
        throw "Python installed but python.exe not found at $($script:PythonExe)"
    }
    Write-Log "Installed Python to $($script:PythonExe)" -Level OK
}

# ─── Step 2: Create venv ──────────────────────────────────────
Invoke-Step "[2/8] Create virtual environment" {
    $vpy = Join-Path $VENV_PATH "Scripts\python.exe"
    if (Test-Path $vpy) {
        $ver = (& $vpy -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
        Write-Log "venv already exists at $VENV_PATH (python $ver) — reusing" -Level OK
        return
    }
    Write-Log "Creating venv at $VENV_PATH via $PythonExe"
    & $PythonExe -m venv $VENV_PATH
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    Write-Log "venv created" -Level OK
}

$VPy  = Join-Path $VENV_PATH "Scripts\python.exe"
$VPip = Join-Path $VENV_PATH "Scripts\pip.exe"

# ─── Step 3: Pick pip index ───────────────────────────────────
$script:PipIndex = "https://pypi.org/simple"
Invoke-Step "[3/8] Pick pip mirror" {
    if ($PipMirror -eq "auto") {
        # Probe pypi.org first; if >3s failure, fall back to tsinghua
        $ok = $false
        try {
            $iwrProbe = @{
                Uri             = "https://pypi.org/simple/pip/"
                UseBasicParsing = $true
                TimeoutSec      = 5
            }
            if ($Proxy) { $iwrProbe["Proxy"] = $Proxy }
            $null = Invoke-WebRequest @iwrProbe
            $ok = $true
        } catch { $ok = $false }
        if ($ok) {
            Write-Log "pypi.org reachable — using official" -Level OK
            $script:PipIndex = "https://pypi.org/simple"
        } else {
            Write-Log "pypi.org unreachable — falling back to TUNA" -Level WARN
            $script:PipIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
        }
    } elseif ($PipMirror -eq "tsinghua") {
        $script:PipIndex = "https://pypi.tuna.tsinghua.edu.cn/simple"
    } elseif ($PipMirror -eq "aliyun") {
        $script:PipIndex = "https://mirrors.aliyun.com/pypi/simple"
    } else {
        $script:PipIndex = "https://pypi.org/simple"
    }
    Write-Log "Pip index: $($script:PipIndex)" -Level OK
}

# ─── Step 4: Upgrade pip ──────────────────────────────────────
Invoke-Step "[4/8] Upgrade pip + setuptools + wheel" {
    & $VPy -m pip install --upgrade pip setuptools wheel --index-url $script:PipIndex
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
}

# ─── Step 5: PyTorch ──────────────────────────────────────────
if (-not $SkipTorch) {
    Invoke-Step "[5/8] Install PyTorch ($CudaChannel)" {
        # Check if already installed with matching CUDA
        $r = Invoke-Exe -File $VPy -CmdArgs @('-c', 'import torch; print(torch.__version__)')
        $existing = if ($r.ExitCode -eq 0) { $r.Output.Trim() } else { $null }
        if ($existing -and $existing.Contains("+$CudaChannel")) {
            Write-Log "torch $existing already installed (matches $CudaChannel) — skip" -Level OK
            return
        }
        if ($existing) {
            Write-Log "torch $existing installed but doesn't match $CudaChannel — reinstalling" -Level WARN
        }
        $torchUrl = "https://download.pytorch.org/whl/$CudaChannel"
        Write-Log "Installing torch torchvision torchaudio from $torchUrl (~3GB)..."
        # pip 内置 retries/timeout — 避免网络抖动导致 3GB 白下
        & $VPip install torch torchvision torchaudio `
            --index-url $torchUrl `
            --retries 5 --timeout 120 --progress-bar on
        if ($LASTEXITCODE -ne 0) {
            Write-Log "torch 下载失败. 检查: 网络 / 代理 / download.pytorch.org 是否可达" -Level ERROR
            throw "torch install failed (cu channel $CudaChannel)"
        }
    }
} else {
    Write-Log "[5/8] Skip PyTorch (--SkipTorch)" -Level WARN
}

# ─── Step 6: fbxsdkpy (INRIA) ─────────────────────────────────
Invoke-Step "[6/8] Install fbxsdkpy from INRIA (LICENSE: Autodesk FBX LSA)" {
    # INRIA pypi package `fbxsdkpy` installs as `import fbx` (Autodesk
    # convention: ships fbx.pyd + FbxCommon.py). We must probe BOTH:
    #   1. actual import works (not just dist-info residue)
    #   2. version matches the pin (old dist-info + new expected → force reinstall)
    $probeCode = "import fbx`nfrom importlib.metadata import version`nprint(version('fbxsdkpy'))"
    $r = Invoke-Exe -File $VPy -CmdArgs @('-c', $probeCode)
    $installedVer = if ($r.ExitCode -eq 0) { $r.Output.Trim() } else { "" }
    if ($installedVer -eq $FBXSDKPY_VERSION) {
        Write-Log "fbxsdkpy $installedVer already installed (import OK, version matches) — skip" -Level OK
        return
    }
    if ($installedVer) {
        Write-Log "fbxsdkpy $installedVer installed but pin is $FBXSDKPY_VERSION — reinstalling" -Level WARN
    } elseif ($r.ExitCode -ne 0) {
        Write-Log "fbxsdkpy metadata may exist but 'import fbx' failed — reinstalling" -Level WARN
    }
    Write-Log "fbxsdkpy is Autodesk-licensed — pulled directly from Inria (NOT bundled)" -Level WARN
    # Pinned only: latest fallback had drifted to 2024+ wheels that broke retarget.
    # Codex 场景 8 P1: 去掉 latest fallback.
    & $VPip install "fbxsdkpy==$FBXSDKPY_VERSION" `
        --index-url $script:PipIndex `
        --extra-index-url $INRIA_INDEX `
        --retries 5 --timeout 60
    if ($LASTEXITCODE -ne 0) {
        Write-Log "fbxsdkpy==$FBXSDKPY_VERSION 下载失败. 检查: INRIA GitLab 可达性 / 代理 / cp312 wheel" -Level ERROR
        throw "fbxsdkpy install failed"
    }
}

# ─── Step 7: kimodo + server deps ─────────────────────────────
Invoke-Step "[7/8] Install kimodo + server deps" {
    # kimodo from git (pypi package name might not exist or be stale)
    $r = Invoke-Exe -File $VPy -CmdArgs @('-c', 'import kimodo; print(getattr(kimodo, "__version__", "unknown"))')
    if ($r.ExitCode -eq 0 -and $r.Output.Trim()) {
        Write-Log "kimodo $($r.Output.Trim()) already installed — skip" -Level OK
    } else {
        Write-Log "Installing kimodo[all] from github (this will take a while)"
        & $VPip install "kimodo[all] @ git+https://github.com/nv-tlabs/kimodo.git" `
            --index-url $script:PipIndex `
            --retries 5 --timeout 120
        if ($LASTEXITCODE -ne 0) { throw "kimodo install failed" }
    }

    # Always ensure server deps present
    Write-Log "Installing fastapi/uvicorn/pydantic/scipy/bvhio"
    & $VPip install scipy fastapi "uvicorn[standard]" pydantic bvhio requests `
        --index-url $script:PipIndex `
        --retries 5 --timeout 60
    if ($LASTEXITCODE -ne 0) { throw "server deps install failed" }
}

# ─── Step 8: Verify ───────────────────────────────────────────
Invoke-Step "[8/8] Run precheck.py" {
    $addonRoot = Split-Path -Parent $PSScriptRoot  # kimodo_motion/
    $precheck = Join-Path $PSScriptRoot "precheck.py"
    if (-not (Test-Path $precheck)) {
        Write-Log "precheck.py not found at $precheck — skipping verify" -Level WARN
        return
    }
    Write-Log "Running precheck via venv interpreter"
    & $VPy $precheck --pretty
    if ($LASTEXITCODE -ne 0) {
        Write-Log "precheck returned non-zero (partial install)" -Level WARN
    }
}

# ─── Post-install hints ───────────────────────────────────────
Write-Log ""
Write-Log "=============================================="
if ($DryRun) {
    Write-Log "DRY-RUN complete — no changes made." -Level OK
    Write-Log "Remove -DryRun to actually install." -Level STEP
} else {
    Write-Log "Kimodo runtime installed." -Level OK
    Write-Log "Venv:   $VENV_PATH" -Level OK
    Write-Log "Python: $VPy" -Level OK
    Write-Log "Log:    $LOG_PATH" -Level OK
    Write-Log ""
    Write-Log "Next steps:" -Level STEP
    Write-Log "  1. Open Blender, enable 'Kimodo Motion' addon"
    Write-Log "  2. Edit > Preferences > Add-ons > Kimodo Motion > venv path = $VENV_PATH"
    Write-Log "  3. First generation will download ~16GB LLaMA-3-8B text encoder"
    Write-Log "     (requires HuggingFace account with access to meta-llama/Meta-Llama-3-8B-Instruct)"
    Write-Log "     Run: $VPy -m huggingface_hub.commands.huggingface_cli login"
    Write-Log ""
    Write-Host "Press Enter to close..." -ForegroundColor Cyan
    [void][System.Console]::ReadLine()
}
