<#
.SYNOPSIS
    Backup or restore the gitignored user-data folders for EfficientAssetRipper.

.DESCRIPTION
    Git tracks the source code.  The folders below are gitignored because they
    contain machine-specific paths and potentially sensitive AES keys, or are
    just too large for a repo.  Use this script to keep them in sync with a
    second location (Google Drive, NAS, USB, another machine, etc.).

    Folders synced
    --------------
    profiles/   Per-game JSON config (game dirs, AES keys, processed-asset lists)
    cache/      Scan result cache (speeds up rescanning – safe to lose, just slow)
    outputs/    Generated .blend files
    logs/       Runtime batch logs

.PARAMETER Backup
    Copy local  →  $ArchivePath   (overwrites older files, mirrors deletions)

.PARAMETER Restore
    Copy $ArchivePath  →  local   (overwrites older files, mirrors deletions)

.PARAMETER Folders
    Comma-separated list of folders to include.
    Defaults to: profiles, cache
    Pass "all" to include outputs and logs as well.

.EXAMPLE
    .\sync.ps1 -Backup
    .\sync.ps1 -Restore
    .\sync.ps1 -Backup  -Folders all
    .\sync.ps1 -Restore -Folders profiles
#>
param(
    [switch]$Backup,
    [switch]$Restore,
    [string]$Folders = "profiles,cache"
)

# ── CONFIGURE THIS ────────────────────────────────────────────────────────────
# Destination for -Backup / source for -Restore.
# Default points at the Google Drive project folder; change it to suit.
$ArchivePath = "G:\My Drive\Personal Storage\Project Contents\EfficientAssetRipper\_sync"
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
  .\sync.ps1 -Backup  [-Folders <list|all>]
  .\sync.ps1 -Restore [-Folders <list|all>]

Folders (default: profiles,cache):
"@
    foreach ($k in $AllFolders.Keys) {
        Write-Host ("  {0,-12} {1}" -f $k, $AllFolders[$k])
    }
    exit 0
}

$direction = if ($Backup) { "Backup  (local → archive)" } else { "Restore (archive → local)" }
Write-Host ""
Write-Host "=== EfficientAssetRipper sync — $direction ===" -ForegroundColor Cyan
Write-Host "Archive : $ArchivePath"
Write-Host ""

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

    # /MIR  – mirror (adds new, deletes removed)
    # /NP   – no progress percentage (cleaner output)
    # /NFL  – no file list (quieter)
    # /NDL  – no dir list
    # /R:2  – retry twice on locked files
    # /W:1  – wait 1s between retries
    robocopy $src $dst /MIR /NP /NFL /NDL /R:2 /W:1 | Out-Null

    $rc = $LASTEXITCODE
    # robocopy exit codes: 0=no change, 1=files copied, 2=extra files removed,
    # 3=both, >=8 = error
    if ($rc -ge 8) {
        Write-Warning "  robocopy reported an error for '$folder' (exit code $rc)"
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan
