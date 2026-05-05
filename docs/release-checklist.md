# Release Checklist (Maintainers)

CI watches for tag pushes matching `v*.*.*`. The release pipeline picks
up the tag, builds the ZIP, extracts the matching `CHANGELOG.md` block,
and publishes a GitHub Release with the artifact attached.

## Pre-tag

1. **Run the fast suite locally**:
   ```
   py -m pytest
   ```
   Expect every test to pass. The version-drift test guards against
   `_version.py` and `CHANGELOG.md` getting out of sync.

2. **Run the opt-in real-binary smoke tests** (your machine only):
   ```powershell
   $env:BLENDER_EXE = "C:\Program Files\Blender Foundation\Blender 4.0\blender.exe"
   $env:CUE4PARSE_CLI = "cue4parse_cli\bin\publish\CUE4ParseCLI.exe"
   $env:EVERYTHING_DLL = "C:\Program Files\Everything\Everything64.dll"
   py -m pytest -m "requires_blender or requires_dotnet_cli or requires_everything"
   ```
   These don't run in CI (no game files / licensed binaries available).
   Catch real-binary regressions here instead.

3. **End-to-end smoke against a known-good profile**: launch the app,
   load a profile that's been processed before, run a small batch
   through to a `.blend`. Sanity-check the result in Blender.

4. **Bump `__version__`** in [_version.py](../_version.py).

5. **Add a `## [x.y.z] - YYYY-MM-DD` block** to
   [CHANGELOG.md](../CHANGELOG.md) under the new version. Move
   anything from `## [Unreleased]` into the new block.

6. **Commit the bump + changelog**:
   ```bash
   git commit -am "Release vX.Y.Z"
   git push
   ```

## Tag and push

```bash
git tag vX.Y.Z -m "vX.Y.Z — short summary"
git push origin vX.Y.Z
```

Pre-release tags (anything containing a `-`, e.g. `v0.6.0-rc1`) are
auto-marked as pre-release by the workflow.

## Post-tag verification

1. **Check the [Actions tab](https://github.com/exterminathan/EfficientAssetRipper/actions)** —
   the release workflow should be green.
2. **Open the new release on GitHub** — confirm:
   - The `EfficientAssetRipper-win-x64.zip` artifact is attached.
   - The CHANGELOG block for this version is rendered as the release
     body.
   - SHA256 sums and the build provenance attestation are present.
3. **Pin GitHub topics** if not already set: `unreal-engine`, `blender`,
   `pyside6`, `asset-ripper`, `windows`, `pak-extractor`.
4. **Fresh-machine smoke**: download the ZIP from the release page on
   a clean machine (no dev environment), unzip, double-click
   `EfficientAssetRipper.exe`, click through the first-run wizard.
   Verify it can scan a profile end-to-end.

## If something's wrong

- **Bad tag** (typo, wrong commit) — delete locally and remotely:
  ```bash
  git tag -d vX.Y.Z
  git push origin :refs/tags/vX.Y.Z
  ```
  Then re-tag and push. The release workflow will run again.
- **Bad artifact** — delete the GitHub release (don't delete the tag),
  fix the underlying issue, push the fix, then re-trigger the release
  workflow manually from the Actions tab on the same tag.
- **Bad CHANGELOG** — fix locally, force-push the change to the tag:
  ```bash
  git tag -f vX.Y.Z
  git push origin vX.Y.Z --force
  ```
  Force-pushing tags is fine because tags aren't shared development
  branches; consumers re-fetch them on demand.

## Cherry-pick fixes into a patch release

If `vX.Y.Z` shipped with a bad bug:

1. Branch from the tag: `git checkout -b hotfix/vX.Y.Z+1 vX.Y.Z`.
2. Cherry-pick the fix commit(s) from `main`.
3. Bump to `vX.Y.Z+1` in `_version.py` + CHANGELOG.
4. Run the pre-tag checklist above.
5. Tag and push, then merge the hotfix branch back into `main`.
