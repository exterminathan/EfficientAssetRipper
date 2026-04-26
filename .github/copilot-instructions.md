# GitHub Copilot Instructions for EfficientAssetRipper

## CUE4Parse CLI — Build Rules

Whenever any file under `cue4parse_cli/` is modified, you **must** rebuild the project before considering the task complete.

### Why temp folder?

Some checkout locations (network shares, cloud-synced folders) hold file locks
that break `dotnet publish` in-place (obj/bin files get locked). Always build
into a temp directory first, then copy the published output back.

### Build command

Run from the repository root:

```powershell
dotnet publish "cue4parse_cli\CUE4ParseCLI.csproj" `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    -p:PublishSingleFile=true `
    -p:GenerateAssemblyInfo=false `
    -p:GenerateTargetFrameworkAttribute=false `
    --output "$env:TEMP\CUE4ParseCLI_build"
```

### After a successful build

Copy the published output back into the repo so the Python code can find it:

```powershell
Copy-Item "$env:TEMP\CUE4ParseCLI_build\*" `
    "cue4parse_cli\bin\publish" `
    -Recurse -Force
```

### Summary checklist

1. Edit files under `cue4parse_cli/` as needed.
2. Run the `dotnet publish` command above (temp folder, avoids file locks).
3. Copy the finished build back to `cue4parse_cli\bin\publish\`.
4. Verify the binary exists and the task is complete.
