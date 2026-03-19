# GitHub Copilot Instructions for EfficientAssetRipper

## CUE4Parse CLI — Build Rules

Whenever any file under `cue4parse_cli/` is modified, you **must** rebuild the project before considering the task complete.

### Why temp folder?

The project lives on Google Drive, which holds file locks that break `dotnet publish` (obj/bin files get locked). Always build into a temp directory outside of Google Drive.

### Build command

```powershell
dotnet publish "g:\My Drive\Personal Storage\Project Contents\EfficientAssetRipper\cue4parse_cli\CUE4ParseCLI.csproj" `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    -p:PublishSingleFile=true `
    -p:GenerateAssemblyInfo=false `
    -p:GenerateTargetFrameworkAttribute=false `
    --output "$env:TEMP\CUE4ParseCLI_build"
```

### After a successful build

Copy the published output back to the Google Drive project folder so the Python code can find it:

```powershell
Copy-Item "$env:TEMP\CUE4ParseCLI_build\*" `
    "g:\My Drive\Personal Storage\Project Contents\EfficientAssetRipper\cue4parse_cli\bin\publish" `
    -Recurse -Force
```

### Summary checklist

1. Edit files under `cue4parse_cli/` as needed.
2. Run the `dotnet publish` command above (temp folder, avoids Drive locks).
3. Copy the finished build back to `cue4parse_cli/bin/publish/` in the Google Drive workspace.
4. Verify the binary exists and the task is complete.
