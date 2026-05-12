# ─────────────────────────────────────────────────────────────────────────────
# Atlas CLI Installer — Windows (PowerShell)
#
# Usage (run in PowerShell as normal user):
#   irm https://atlas.alpheric.ai/install.ps1 | iex
#
# What this does:
#   1. Installs Bun for Windows (if not already installed)
#   2. Downloads the Atlas CLI bundle to %USERPROFILE%\.atlas-cli\
#   3. Creates atlas.cmd in %USERPROFILE%\.atlas-cli\bin\ and adds to PATH
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"
$BASE_URL   = "https://atlas.alpheric.ai"
$INSTALL_DIR = Join-Path $env:USERPROFILE ".atlas-cli"
$BIN_DIR     = Join-Path $INSTALL_DIR "bin"

Write-Host ""
Write-Host "  ╭────────────────────────────────────────────────╮" -ForegroundColor DarkGray
Write-Host "  │                                                │" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host " ██████╗ ████████╗██╗      █████╗ ███████╗ " -NoNewline -ForegroundColor Green;  Write-Host " │" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host "██╔══██╗╚══██╔══╝██║     ██╔══██╗██╔════╝ " -NoNewline -ForegroundColor Green;  Write-Host " │" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host "███████║   ██║   ██║     ███████║███████╗  " -NoNewline -ForegroundColor Cyan;   Write-Host "│" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host "██╔══██║   ██║   ██║     ██╔══██║╚════██║  " -NoNewline -ForegroundColor Cyan;   Write-Host "│" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host "██║  ██║   ██║   ███████╗██║  ██║███████║  " -NoNewline -ForegroundColor Cyan;   Write-Host "│" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host "╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝ " -NoNewline -ForegroundColor Cyan;   Write-Host " │" -ForegroundColor DarkGray
Write-Host "  │                                                │" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host "          ✦  by Alpheric AI  ✦           " -NoNewline -ForegroundColor Cyan;     Write-Host "  │" -ForegroundColor DarkGray
Write-Host "  │  " -NoNewline -ForegroundColor DarkGray; Write-Host " Agentic AI coding assistant for the terminal" -NoNewline -ForegroundColor DarkGray; Write-Host " │" -ForegroundColor DarkGray
Write-Host "  │                                                │" -ForegroundColor DarkGray
Write-Host "  ╰────────────────────────────────────────────────╯" -ForegroundColor DarkGray
Write-Host ""

# ── 1. Bun ────────────────────────────────────────────────────────────────────
$bunCmd  = Get-Command bun -ErrorAction SilentlyContinue
$bunPath = if ($bunCmd) { $bunCmd.Source } else { $null }
if (-not $bunPath) {
    $bunBin = Join-Path $env:USERPROFILE ".bun\bin\bun.exe"
    if (-not (Test-Path $bunBin)) {
        Write-Host "→ Installing Bun..."
        irm bun.sh/install.ps1 | iex
    }
    $bunPath = Join-Path $env:USERPROFILE ".bun\bin\bun.exe"
} else {
    Write-Host "✓ Bun already installed"
}

# ── 2. Download Atlas CLI ─────────────────────────────────────────────────────
# Download individual files directly instead of using tar. Not every
# Windows install ships with tar, and the matching `tar -xzf` flow is
# fragile across versions. The atlas.js + yoga.wasm pair is everything
# the runtime needs.
Write-Host "→ Downloading Atlas CLI..."
$DistDir = Join-Path $INSTALL_DIR "dist"
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $BIN_DIR     | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir     | Out-Null

Write-Host "→ Installing to $INSTALL_DIR..."
Invoke-WebRequest "$BASE_URL/downloads/atlas.js"  -OutFile (Join-Path $DistDir "atlas.js")
Invoke-WebRequest "$BASE_URL/downloads/yoga.wasm" -OutFile (Join-Path $DistDir "yoga.wasm")

# Stamp version so the auto-updater can compare against the published version
try {
    $remoteVer = (Invoke-WebRequest "$BASE_URL/downloads/version.txt" -UseBasicParsing).Content.Trim()
    Set-Content -Path (Join-Path $INSTALL_DIR "version.txt") -Value $remoteVer -NoNewline
} catch {}

# ── 3. Create atlas.cmd wrapper ───────────────────────────────────────────────
$jsPath  = Join-Path $INSTALL_DIR "dist\atlas.js"
$cmdPath = Join-Path $BIN_DIR "atlas.cmd"

@"
@echo off
"$bunPath" run "$jsPath" %*
"@ | Set-Content $cmdPath

# ── 4. Add to PATH (user scope) ───────────────────────────────────────────────
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -notlike "*$BIN_DIR*") {
    [Environment]::SetEnvironmentVariable("PATH", "$BIN_DIR;$currentPath", "User")
    $env:PATH = "$BIN_DIR;$env:PATH"
    Write-Host "✓ Added $BIN_DIR to PATH"
}

Write-Host ""
Write-Host "  ✓ Atlas CLI installed!" -ForegroundColor Green
Write-Host ""

# ── 5. Configure API key ─────────────────────────────────────────────────────
$ConfigDir  = "$env:USERPROFILE\.config\atlas-cli"
$ConfigFile = "$ConfigDir\config.json"
New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null

$ExistingKey = ""
if (Test-Path $ConfigFile) {
    try {
        $cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        if ($cfg.apiKey) { $ExistingKey = $cfg.apiKey }
    } catch {}
}

$EnvKey = if ($env:ATLAS_API_KEY) { $env:ATLAS_API_KEY } elseif ($env:ALPHERIC_API_KEY) { $env:ALPHERIC_API_KEY } else { "" }

if ($EnvKey) {
    Write-Host "  ✓ Using API key from environment (ATLAS_API_KEY)" -ForegroundColor Green
} elseif ($ExistingKey) {
    Write-Host "  ✓ API key already configured at $ConfigFile" -ForegroundColor Green
} else {
    Write-Host "  Enter your Atlas API key (starts with sk-atlas-) — or press Enter to skip:" -ForegroundColor Cyan
    $UserKey = Read-Host "  >"
    if ($UserKey) {
        $cfg = @{
            apiKey  = $UserKey.Trim()
            baseUrl = "https://atlas.alpheric.ai/v1"
            model   = "atlas-code"
            stream  = $true
        }
        $cfg | ConvertTo-Json | Set-Content -Path $ConfigFile -Encoding UTF8
        Write-Host "  ✓ API key saved to $ConfigFile" -ForegroundColor Green
    } else {
        Write-Host "  i  Skipped. Configure later with: atlas config set apiKey <your-key>" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "  Next steps (open a new terminal):" -ForegroundColor DarkCyan
Write-Host "    cd your-project" -ForegroundColor Gray
Write-Host "    atlas" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Don't have a key? Get one at https://atlas.alpheric.ai" -ForegroundColor DarkGray
Write-Host ""
