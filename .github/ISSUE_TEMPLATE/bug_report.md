---
name: Bug report
about: Report a problem with EfficientAssetRipper
title: "[bug] "
labels: bug
assignees: ''
---

## Describe the bug

A clear and concise description of what's going wrong.

## Steps to reproduce

1. Go to '...'
2. Click on '...'
3. See error

## Expected behavior

What you expected to happen instead.

## Screenshots

If applicable, paste screenshots or a short clip.

## Environment

- **EfficientAssetRipper version:** (Help → About — e.g. `v0.5.0`)
- **Game being ripped:** (e.g. Star Wars Jedi: Survivor, UE 5.1)
- **Windows version:** (e.g. Windows 11 23H2)
- **Blender version:** (e.g. 4.2.1)
- **.NET runtime version:** (`dotnet --version`)
- **Everything version:** (Everything → Help → About)

## Profile JSON

Paste the relevant profile from `profiles/<game>.json` here.
**Redact your AES keys before submitting.**

```json
{
  "...": "..."
}
```

## Log file

Attach the most recent file from `logs/` (drag-and-drop into the comment
box). If the issue is reproducible, run the failing operation again first
so the log captures it cleanly.

## Additional context

Anything else that might help — error messages, stack traces, config quirks.
