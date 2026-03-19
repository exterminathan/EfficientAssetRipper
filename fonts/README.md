# Custom Fonts

Drop `.ttf` or `.otf` font files into this directory.

They will be loaded automatically when the application starts.

To activate a custom font, edit `gui/theme.py` and set:

- `CUSTOM_FONT_FAMILY` — for the main UI font (e.g. `"Inter"`)
- `CUSTOM_MONO_FONT_FAMILY` — for the monospace / log font (e.g. `"JetBrains Mono"`)

The value must match the **family name** registered by the font file.
After loading, the console log will print the detected family names.
