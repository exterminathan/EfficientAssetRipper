<#
.SYNOPSIS
    Backup or restore the gitignored user-data folders for EfficientAssetRipper.

.DESCRIPTION
    Git tracks the source code.  The folders below are gitignored because they
    contain machine-specific paths and potentially sensitive AES keys, or are
    just too large for a repo.  Use this script to keep them in sync with a
    second location (cloud drive, NAS, USB, another machine, etc.).

    Folders synced
    --------------
    profiles/   Per-game JSON config (game dirs, AES keys, processed-asset lists)
    cache/      Scan result cache (speeds up rescanning – safe to lose, just slow)
    outputs/    Generated .blend files
    logs/       Runtime batch logs

    The backup destination is read from the EAR_SYNC_PATH environment variable.
    Set it once per shell, e.g.:

        $env:EAR_SYNC_PATH = "D:\Backups\EAR"

    or persist it for the user:

        [Environment]::SetEnvironmentVariable("EAR_SYNC_PATH", "D:\Backups\EAR", "User")

.PARAMETER Backup
    Copy local  →  $env:EAR_SYNC_PATH.  Additive by default; pass -Force to
    mirror (which deletes archive files that no longer exist locally).

.PARAMETER Restore
    Copy $env:EAR_SYNC_PATH  →  local.  Always additive — only newer files
    are copied, nothing on the local side is ever deleted.

.PARAMETER Folders
    Comma-separated list of folders to include.
    Defaults to: profiles, cache
    Pass "all" to include outputs and logs as well.

.PARAMETER Force
    Required for destructive Backup mirroring (deletes archive files that
    are not present locally).  Ignored for Restore.

.PARAMETER WhatIf
    Print what would be copied/deleted without performing any action.

.EXAMPLE
    $env:EAR_SYNC_PATH = "D:\Backups\EAR"
    .\sync.ps1 -Backup
    .\sync.ps1 -Restore
    .\sync.ps1 -Backup -Folders all
    .\sync.ps1 -Restore -Folders profiles
    .\sync.ps1 -Backup -Force            # destructive mirror
    .\sync.ps1 -Backup -Force -WhatIf    # preview destructive mirror
#>
param(
    [switch]$Backup,
    [switch]$Restore,
    [string]$Folders = "profiles,cache",
    [switch]$Force,
    [switch]$WhatIf
)

# ── Backup location ──────────────────────────────────────────────────────────
# Read from the EAR_SYNC_PATH environment variable so no personal path is
# baked into the committed script.
$ArchivePath = $env:EAR_SYNC_PATH
if ([string]::IsNullOrWhiteSpace($ArchivePath)) {
    Write-Host ""
    Write-Host "ERROR: EAR_SYNC_PATH environment variable is not set." -ForegroundColor Red
    Write-Host ""
    Write-Host "Set it to the folder you want to back up to, e.g.:"
    Write-Host '    $env:EAR_SYNC_PATH = "D:\Backups\EAR"'
    Write-Host ""
    Write-Host "To make it persistent for the current Windows user:"
    Write-Host '    [Environment]::SetEnvironmentVariable("EAR_SYNC_PATH", "D:\Backups\EAR", "User")'
    Write-Host ""
    exit 1
}
# ─────────────────────────────────────────────────────────────────────────────

$ProjectPath = $PSScriptRoot

$AllFolders = @{
    profiles = "Per-game config (AES keys, processed-asset lists)"
    cache    = "Scan result cache"
    outputs  = "Generated .blend files  [large]"
    logs     = "Runtime batch logs      [large]"
}

# Resolve requested folder list
if ($Folders -eq "all") {
    $requested = $AllFolders.Keys
} else {
    $requested = $Folders -split "," | ForEach-Object { $_.Trim() }
}

# Validate
foreach ($f in $requested) {
    if (-not $AllFolders.ContainsKey($f)) {
        Write-Warning "Unknown folder '$f'.  Valid choices: $($AllFolders.Keys -join ', '), all"
        exit 1
    }
}

if (-not $Backup -and -not $Restore) {
    Write-Host @"
Usage:
  .\sync.ps1 -Backup  [-Folders <list|all>] [-Force] [-WhatIf]
  .\sync.ps1 -Restore [-Folders <list|all>] [-WhatIf]

Folders (default: profiles,cache):
"@
    foreach ($k in $AllFolders.Keys) {
        Write-Host ("  {0,-12} {1}" -f $k, $AllFolders[$k])
    }
    Write-Host ""
    Write-Host "Restore is non-destructive: only newer files are pulled in, nothing"
    Write-Host "on the local side is ever deleted."
    Write-Host ""
    Write-Host "Backup is additive by default.  Pass -Force to mirror (which deletes"
    Write-Host "archive files that no longer exist locally)."
    exit 0
}

if ($Backup -and $Restore) {
    Write-Warning "Pass -Backup OR -Restore, not both."
    exit 1
}

$direction = if ($Backup) { "Backup  (local → archive)" } else { "Restore (archive → local)" }
Write-Host ""
Write-Host "=== EfficientAssetRipper sync — $direction ===" -ForegroundColor Cyan
Write-Host "Archive : $ArchivePath"
if ($WhatIf) {
    Write-Host "Mode    : DRY RUN (no files will be copied or deleted)" -ForegroundColor Yellow
}
if ($Backup -and $Force) {
    Write-Host "Mode    : MIRROR (archive files missing locally will be DELETED)" -ForegroundColor Yellow
}
Write-Host ""

# Common robocopy flags:
# /NP   – no progress percentage (cleaner output)
# /NFL  – no file list (quieter)
# /NDL  – no dir list
# /R:2  – retry twice on locked files
# /W:1  – wait 1s between retries
# /XO   – only copy newer source files (skip if dest is newer or same)
# /MIR  – mirror (additionally deletes dest files missing from source)
# /E    – include subdirectories, even empty ones
# /L    – list only (dry run)
$commonFlags = @("/NP", "/NFL", "/NDL", "/R:2", "/W:1")

foreach ($folder in $requested) {
    if ($Backup) {
        $src = Join-Path $ProjectPath $folder
        $dst = Join-Path $ArchivePath  $folder
        if (-not (Test-Path $src)) {
            Write-Host "  SKIP  $folder  (folder does not exist locally)" -ForegroundColor Yellow
            continue
        }
    } else {
        $src = Join-Path $ArchivePath  $folder
        $dst = Join-Path $ProjectPath  $folder
        if (-not (Test-Path $src)) {
            Write-Host "  SKIP  $folder  (not found in archive)" -ForegroundColor Yellow
            continue
        }
    }

    Write-Host "  SYNC  $folder" -ForegroundColor Green

    # Build flag set per direction:
    #   Restore        → /E /XO   (additive, only newer files)
    #   Backup         → /E /XO   (additive, only newer files)
    #   Backup -Force  → /MIR     (full mirror, deletes orphans in archive)
    if ($Backup -and $Force) {
        $modeFlags = @("/MIR")
    } else {
        $modeFlags = @("/E", "/XO")
    }
    if ($WhatIf) {
        $modeFlags += "/L"
    }

    $robocopyArgs = @($src, $dst) + $modeFlags + $commonFlags
    & robocopy @robocopyArgs | Out-Null

    $rc = $LASTEXITCODE
    # robocopy exit codes:
    #   0  no change
    #   1  files copied
    #   2  extra files in destination (not in source)
    #   3  files copied + extras
    #   >=8 error
    # We only physically delete extras when -Force /MIR is set.  For the
    # additive paths (/E /XO) a code 2 means the archive has files the local
    # side doesn't — surface that as a warning rather than silently leaving
    # them in place, and require -Force if the user wants them removed.
    if ($rc -ge 8) {
        Write-Warning "  robocopy reported an error for '$folder' (exit code $rc)"
    }
    elseif ($Backup -and -not $Force -and ($rc -band 2)) {
        Write-Warning "  '$folder': archive has extra files not present locally."
        Write-Warning "  Re-run with -Force to mirror (and DELETE those archive files)."
    }
    elseif ($Restore -and ($rc -band 2)) {
        # For restore, "extras" are local files not in the archive — perfectly
        # fine, nothing to warn about, but log so the user understands the
        # additive-restore semantics.
        Write-Host "        (local has $folder files not in archive — left untouched)" -ForegroundColor DarkGray
    }
}

Write-Host ""
if ($WhatIf) {
    Write-Host "Dry run complete — nothing was copied or deleted." -ForegroundColor Cyan
} else {
    Write-Host "Done." -ForegroundColor Cyan
}
