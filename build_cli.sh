#!/usr/bin/env bash
set -e

echo "============================================"
echo " Building CUE4ParseCLI..."
echo "============================================"
echo

if ! command -v dotnet &> /dev/null; then
    echo "ERROR: .NET SDK not found. Install from https://dotnet.microsoft.com/download"
    echo "Requires .NET 8.0 SDK or later."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/cue4parse_cli"

echo "[1/3] Restoring NuGet packages..."
dotnet restore

echo
echo "[2/3] Building Release..."
dotnet publish -c Release -o "$SCRIPT_DIR/cue4parse_cli/bin/publish"

echo
echo "[3/3] Done!"
echo
echo "Output: $SCRIPT_DIR/cue4parse_cli/bin/publish/CUE4ParseCLI"
echo
echo "To use: Set the CLI path in EfficientAssetRipper Settings."
