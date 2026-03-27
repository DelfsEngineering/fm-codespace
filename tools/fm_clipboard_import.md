# FileMaker Clipboard Importer

`tools/fm_clipboard_import.py` is a first-pass CLI for pulling FileMaker clipboard XML into this workspace.

## What it does

- Reads FileMaker clipboard XML directly from the macOS pasteboard with PyObjC
- Supports `inspect`, `dump`, and `import` commands
- Rebuilds folder hierarchy when the clipboard XML contains `Group` nodes
- Overwrites existing files by default and reports every overwrite
- Handles large payloads by reading raw clipboard bytes and writing files directly

## Install

Live clipboard access needs PyObjC:

```bash
python3 -m pip install "pyobjc==11.1"
```

On this machine, installing `pyobjc-framework-AppKit` directly did not resolve correctly with the Apple Command Line Tools Python, but the pinned umbrella package `pyobjc==11.1` worked.

If you just want to test parsing/import behavior, use `--input path/to/file.xml` and no extra packages are required.

## Commands

Inspect the current FileMaker clipboard:

```bash
python3 "tools/fm_clipboard_import.py" inspect
```

Dump the raw clipboard XML to a temp file:

```bash
python3 "tools/fm_clipboard_import.py" dump
```

Preview an import without writing anything:

```bash
python3 "tools/fm_clipboard_import.py" import --preview --root "."
```

By default, imports are routed into entity-specific folders under the workspace root:

- scripts -> `current scripts/`
- custom functions -> `current custom functions/`
- tables -> `current tables/`
- fields -> `current fields/`
- value lists -> `current value lists/`
- layouts -> `current layouts/`
- themes -> `current themes/`
- custom menus -> `current custom menus/`
- unknown FM objects -> `current other fm objects/`

By default, imported script and custom-function filenames include a stable FileMaker-scoped ID suffix for safer round-tripping, for example:

- `Save Site Unified - Sub__fmscript-1234.xml`
- `HAM_Config__fmcf-203`

Import from a saved XML file instead of the clipboard:

```bash
python3 "tools/fm_clipboard_import.py" import \
  --input "/path/to/filemaker.xml" \
  --preview \
  --root "."
```

When importing script-step snippets instead of full scripts, pass a fallback name:

```bash
python3 "tools/fm_clipboard_import.py" import --name "My Script"
```

## Cursor workflow

If you want to keep this lightweight for now, the easiest workflow in Cursor is:

1. Copy scripts or folders in FileMaker.
2. In Cursor, either run a task from the Command Palette or ask the agent to run one of the importer commands.
3. Review the preview output before doing the real import when the payload is large.
4. Imports are routed into entity-specific folders like `current scripts/` and `current custom functions/` so the workspace root stays clean.

Suggested agent requests:

- `Inspect the FileMaker clipboard`
- `Preview-import the FileMaker clipboard into the right current folder`
- `Import the FileMaker clipboard into the right current folder`
- `Dump the current FileMaker clipboard XML to a temp file`

If you omit a FileMaker file namespace when asking the agent to import a copied subfolder, the preferred inference rule is:

- first try to match the copied folder path against existing file namespaces for that entity type
- if exactly one namespace is a clear match, use it
- otherwise, if only one namespace exists for that entity type, assume the same file
- if multiple namespaces are plausible, ask before importing

There are also Cursor tasks in `.vscode/tasks.json`:

- `FileMaker: Inspect Clipboard`
- `FileMaker: Preview Import Clipboard`
- `FileMaker: Import Clipboard`
- `FileMaker: Clear Current Tables`
- `FileMaker: Clear Current Scripts`
- `FileMaker: Clear Current Custom Functions`
- `FileMaker: Install Clipboard Dependency`

These give you a no-UI workflow that is still fast enough for day-to-day use.

## Operator phrases

You can use these phrases directly in chat and I should understand the intended workflow:

- `inspect clipboard`
  Inspect the current FileMaker clipboard and report the detected entity type, target folder, and preview paths.

- `preview import`
  Preview where the current clipboard would land without writing files.

- `import clipboard`
  Import the current clipboard into the routed current folder and overwrite matching files.

- `clear tables`
  Remove everything under `current tables/` and recreate the folder.

- `clear scripts`
  Remove everything under `current scripts/` and recreate the folder.

- `clear custom functions`
  Remove everything under `current custom functions/` and recreate the folder.

- `replace tables from clipboard`
  Clear `current tables/`, then import the current table clipboard. This is the safest workflow when FileMaker is the source of truth and you copied the full table set.

- `replace scripts from clipboard`
  Clear `current scripts/`, then import the current script clipboard. Use this only when the clipboard contains the full script set you want represented locally.

- `replace custom functions from clipboard`
  Clear `current custom functions/`, then import the current custom-function clipboard.

## Source of truth

FileMaker is the source of truth for clipboard imports.

- Re-importing the same entity type should overwrite matching local files.
- If you want local contents to fully mirror the copied FileMaker set, clear that entity folder first and then import.
- For tables in particular, `replace tables from clipboard` is the preferred pattern when you copied the full table set.

## Output behavior

### Scripts

Default: saved as raw `<Script>` XML snippets to preserve script wrapper metadata such as FileMaker IDs and script-level attributes.

Use step-only XML snippets instead:

```bash
python3 "tools/fm_clipboard_import.py" import --script-format steps
```

### Custom functions

Default: saved as raw XML snippets to preserve custom-function metadata.

Use plain calculation files instead:

```bash
python3 "tools/fm_clipboard_import.py" import --custom-function-format calc
```

### Filename style

Default: `pretty-id`

This keeps names readable while appending stable FileMaker IDs for uniqueness and round-trip safety.

Use plain readable names without ID suffixes instead:

```bash
python3 "tools/fm_clipboard_import.py" import --filename-style pretty
```

If you want to bypass entity-specific routing and write directly into the root path you provide:

```bash
python3 "tools/fm_clipboard_import.py" import --root "." --entity-folder-mode off
```

## Current caveats

- This depends on FileMaker actually putting folder hierarchy into the clipboard XML. The `inspect` command will tell you if `Group` nodes are present.
- If you copy a single script and FileMaker does not include its parent folder path, the importer falls back to `"_Clipboard Imports"` unless you later add a smarter mapping layer.
- Filename sanitizing removes path separators like `/`, `\`, and `:` so invalid path characters do not accidentally create broken or misleading filenames.
- Divider scripts named `-` are preserved. Their local filenames fall back to labels like `Divider 2918__fmscript-2918.xml`.
- Because FileMaker can contain same-name scripts in the same folder, the default filename style appends a stable FileMaker object ID suffix.
- For better fidelity, raw script and custom-function imports preserve the original child XML snippets from the clipboard rather than rebuilding those objects from parsed XML.
