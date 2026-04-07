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

### FM CodeSpace (Web UI)

Start the local browser UI:

```bash
python3 "tools/fm_clipboard_web.py" --open
```

Or without auto-opening a browser:

```bash
python3 "tools/fm_clipboard_web.py"
```

The UI exposes:

- simplified import source selection: `Clipboard` or `DDR/XML Path`
- drag-and-drop DDR/XML file support for local imports
- inspect clipboard
- preview import
- import
- dump clipboard XML
- browse `FILEMAKER FILES/<file>/<entity>/...` namespaces and objects
- visual file status badges (`clean`, `edited`, `queued`, `pasted back`, `unknown`)
- paste-back queue view and status actions (for example mark selected file `pasted back`)

and uses the same importer internals as `tools/fm_clipboard_import.py`, so routing and naming behavior stays consistent between CLI and browser workflow.

Cross-platform note:

- The web app runs on macOS and Windows.
- Live clipboard capture is currently macOS-first.
- On Windows today, use **Input XML Path** in the UI (or `--input` in CLI) until a Windows clipboard adapter is added.

Queue storage note:

- The web app now uses a local SQLite queue store at `agent-maintained/fm_workspace_ui.sqlite3` for status transitions and browsing performance.
- `PASTE_BACK_QUEUE.md` remains auto-synced as a human-readable/export artifact for team visibility.

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

By default, imports are routed into file-first namespace folders:

- scripts -> `FILEMAKER FILES/<file name>/scripts/`
- custom functions -> `FILEMAKER FILES/<file name>/custom functions/`
- tables -> `FILEMAKER FILES/<file name>/tables/`
- fields -> `FILEMAKER FILES/<file name>/fields/`
- value lists -> `FILEMAKER FILES/<file name>/value lists/`
- layouts -> `FILEMAKER FILES/<file name>/layouts/`
- themes -> `FILEMAKER FILES/<file name>/themes/`
- custom menus -> `FILEMAKER FILES/<file name>/custom menus/`
- unknown FM objects -> `FILEMAKER FILES/<file name>/other fm objects/`

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

Force a specific FileMaker file namespace (recommended when clipboard hierarchy is partial):

```bash
python3 "tools/fm_clipboard_import.py" import --file-namespace "BetterForms_Master"
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
4. Imports are routed into file-first namespace folders like `FILEMAKER FILES/BetterForms_Master/scripts/` so the workspace root stays clean.

Suggested agent requests:

- `Inspect the FileMaker clipboard`
- `Preview-import the FileMaker clipboard into the right FILEMAKER FILES namespace`
- `Import the FileMaker clipboard into the right FILEMAKER FILES namespace`
- `Dump the current FileMaker clipboard XML to a temp file`

If you omit a FileMaker file namespace when asking the agent to import a copied subfolder, the preferred inference rule is:

- first try to match the copied folder path against existing file namespaces for that entity type
- if exactly one namespace is a clear match, use it
- otherwise, if only one namespace exists for that entity type, assume the same file
- if multiple namespaces are plausible, ask before importing

There are also Cursor tasks in `.vscode/tasks.json`:

- `FileMaker: Start FM CodeSpace`
- `FileMaker: Inspect Clipboard`
- `FileMaker: Preview Import Clipboard`
- `FileMaker: Import Clipboard`
- `FileMaker: Clear Tables (all namespaces)`
- `FileMaker: Clear Scripts (all namespaces)`
- `FileMaker: Clear Custom Functions (all namespaces)`
- `FileMaker: Install Clipboard Dependency`

These give you a no-UI workflow that is still fast enough for day-to-day use.

## Operator phrases

You can use these phrases directly in chat and I should understand the intended workflow:

- `inspect clipboard`
  Inspect the current FileMaker clipboard and report the detected entity type, target folder, and preview paths.

- `preview import`
  Preview where the current clipboard would land without writing files.

- `import clipboard`
  Import the current clipboard into the routed `FILEMAKER FILES/<file name>/<entity>/` folder and overwrite matching files.

- `clear tables`
  Remove everything under each namespace table folder (`FILEMAKER FILES/*/tables/`) and recreate each folder.

- `clear scripts`
  Remove everything under each namespace scripts folder (`FILEMAKER FILES/*/scripts/`) and recreate each folder.

- `clear custom functions`
  Remove everything under each namespace custom-functions folder (`FILEMAKER FILES/*/custom functions/`) and recreate each folder.

- `replace tables from clipboard`
  Clear namespace table folders (`FILEMAKER FILES/*/tables/`), then import the current table clipboard. This is the safest workflow when FileMaker is the source of truth and you copied the full table set.

- `replace scripts from clipboard`
  Clear namespace scripts folders (`FILEMAKER FILES/*/scripts/`), then import the current script clipboard. Use this only when the clipboard contains the full script set you want represented locally.

- `replace custom functions from clipboard`
  Clear namespace custom-function folders (`FILEMAKER FILES/*/custom functions/`), then import the current custom-function clipboard.

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
