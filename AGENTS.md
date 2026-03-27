# BF Scripts Agent Guide

This workspace is used to round-trip FileMaker objects copied from the FileMaker clipboard into a local folder structure that Cursor can inspect and edit.

## Core rule

FileMaker is the source of truth.

- Imported FileMaker objects should be treated as snapshots of what is currently in FileMaker.
- Re-importing the same entity type should overwrite matching local files.
- If the clipboard contains the full set for an entity type, the preferred workflow is: clear that entity folder, then import again.

## First-time onboarding (wizard)

When the user is new or asks for setup/onboarding, follow **`docs/ONBOARDING.md`**: clipboard workflow first; then optional **OData / Data API** discussion for **table-oriented schema reference** (not scripts); **DDR** for bulk design including scripts. There is no server API that replaces clipboard/DDR for script definitions.

## Solution scope

This repo is generally meant to represent one FileMaker app, file, or solution at a time.

- Most day-to-day editable objects will be scripts and custom functions.
- Tables are primarily imported as schema/reference material so the agent can inspect names, fields, and structure.
- In normal usage, tables are not expected to be round-tripped back into FileMaker.
- Prefer a file-first folder layout for traversal: `FILEMAKER FILES/<file name>/<entity type>/...`.

## Working assumptions

Use these assumptions unless the user explicitly says otherwise:

- Edit imported FileMaker objects mainly in `FILEMAKER FILES/<file name>/scripts/` and `FILEMAKER FILES/<file name>/custom functions/`.
- In this workspace, imported script files are often used as editable step snippets that will be copied back into an existing FileMaker script, not always re-imported as full script objects.
- Unless the user explicitly asks otherwise, prepare edited FileMaker script XML for paste-back as step-only `fmxmlsnippet` content so it can be pasted back into an existing script as steps.
- After editing an imported FileMaker script, leave the saved local file in step-only `fmxmlsnippet` form by default so the edited result is immediately ready to paste back into an existing FileMaker script as step lines.
- Treat `FILEMAKER FILES/<file name>/tables/` as reference material, not as a default edit target.
- Always update `PASTE_BACK_QUEUE.md` when imported FileMaker objects are edited locally.
- Keep FileMaker file namespaces exact when provided, for example `BetterForms_Master`.
- Do not assume a partial clipboard import means sibling objects were deleted in FileMaker; it usually just means only part of the solution is in scope.
- Leave paste-back queue entries in place until the user explicitly says they were pasted back or wants the queue cleared.
- If the user does not specify a FileMaker file namespace for an import, infer it when reasonably safe:
  - First, look for an existing namespace under the relevant `FILEMAKER FILES/<file name>/...` area whose subfolder structure matches the copied folder path.
  - If exactly one namespace is a clear match, use it.
  - Otherwise, if only one namespace exists for that entity type, assume that same file.
  - If more than one namespace is plausible, ask the user instead of guessing.
- When referring to FileMaker scripts in discussion, plans, paste-back notes, or summaries, prefer the script name first, or the folder path plus script name when that is clearer. Use raw FileMaker script IDs only as secondary context, not as the primary way to identify scripts.

## Editing standards

When editing code, scripts, calculations, or imported FileMaker objects, clean things up as you go when it can be done safely.

- Prefer consistent ID naming:
  - use `id` for the current record or object's primary key
  - use a typed foreign key such as `idApp`, `idOrganization`, or `idPage` when the related entity is specific
  - use `idEntity` only when the relationship is intentionally generic or polymorphic
- Favor readable spacing:
  - separate setup, branching, data-shaping, and output steps into clear visual blocks
  - avoid dense walls of logic when a couple of blank lines make the flow easier to scan
  - when editing existing logic, improve spacing and grouping so the script flow is easy to scan quickly
- Follow "useful comments only" rules:
  - keep comments only when they explain intent, business rules, non-obvious constraints, or gotchas
  - remove comments that merely restate the code or script step
  - ensure comments are truthful and current; when logic changes, update or remove any stale/misleading comments immediately
  - add brief comments where intent, business rules, or constraints are not obvious, but do not comment every line or every obvious step
  - when editing a FileMaker script, always inspect the top header doc block and normalize it when that can be done safely, similar to how `PASTE_BACK_QUEUE.md` is maintained for imported object edits
  - use this standard fenced doc-block shape for script headers: `CONCERN`, `PARAMETERS`, optional `CONTEXT`, `RETURNS`, optional `NOTES`, optional `REVISIONS`
  - convert legacy labels into the standard where easy, for example `PURPOSE` -> `CONCERN`, `PARAMS` or `ACCEPTED PARAMETERS` -> `PARAMETERS`, and `HISTORY` -> `REVISIONS`
  - remove stale header noise when possible, including `AUTHOR`, `CREATED BY`, email lines, generator-version boilerplate, and placeholder instructions; if a legacy note still carries active business or operational value, condense it into `NOTES`
  - add or extend **`REVISIONS`** with a **short one-line summary** when the change is substantive (what changed and why), using `YYYY-MM-DD - why-focused summary`; skip logging every trivial tweak
  - do **not** maintain a long **`WORKFLOW`** section in the header unless the team explicitly wants that level of detail in the doc block; prefer body `SECTION` comments for flow details
- Prefer small, easy-to-scan logical chunks over clever compactness.
- When touching an existing area, leave it a little clearer than you found it without doing unrelated rewrites.
- For FileMaker scripts, move hard-coded constants to editable variables near the top of the script whenever reasonably possible (for example project IDs, cluster IDs, base URLs, feature flags, and reusable literal values), and reference those variables in logic instead of repeating literals inline.

## Current workspace layout

- `FILEMAKER FILES/<file name>/scripts/`
 Imported FileMaker scripts.
- `FILEMAKER FILES/<file name>/custom functions/`
 Imported FileMaker custom functions.
- `FILEMAKER FILES/<file name>/tables/`
 Imported FileMaker table definitions.
- `FILEMAKER FILES/<file name>/fields/`
 Imported FileMaker field clipboard payloads.
- `FILEMAKER FILES/<file name>/value lists/`
 Imported FileMaker value list payloads.
- `FILEMAKER FILES/<file name>/layouts/`
 Imported FileMaker layout objects.
- `FILEMAKER FILES/<file name>/themes/`
 Imported FileMaker themes.
- `FILEMAKER FILES/<file name>/custom menus/`
 Imported FileMaker custom menus.
- `FILEMAKER FILES/<file name>/other fm objects/`
 Fallback for supported but not-yet-special-cased FileMaker clipboard objects.
- `legacy/`
  Older manually curated exports that were moved aside before importer testing.
- `tools/fm_clipboard_import.py`
  Main clipboard importer.
- `tools/fm_clipboard_import.md`
  Operator notes for the importer.

## Importer behavior

The importer reads FileMaker clipboard XML from the macOS pasteboard and routes the payload by detected entity type.

Current routing:

- scripts -> `FILEMAKER FILES/<file name>/scripts/`
- script steps -> `FILEMAKER FILES/<file name>/scripts/`
- custom functions -> `FILEMAKER FILES/<file name>/custom functions/`
- tables -> `FILEMAKER FILES/<file name>/tables/`
- fields -> `FILEMAKER FILES/<file name>/fields/`
- value lists -> `FILEMAKER FILES/<file name>/value lists/`
- layouts -> `FILEMAKER FILES/<file name>/layouts/`
- themes -> `FILEMAKER FILES/<file name>/themes/`
- custom menus -> `FILEMAKER FILES/<file name>/custom menus/`
- unknown FileMaker objects -> `FILEMAKER FILES/<file name>/other fm objects/`

## Fidelity rules

Round-trip fidelity is more important than pretty filenames.

- Script imports may exist in either of these valid forms depending on how they will be pasted back:
  - full script-object XML, for example `<fmxmlsnippet><Script ...>...</Script></fmxmlsnippet>`
  - step-only XML, for example `<fmxmlsnippet><Step .../>...</fmxmlsnippet>`
- Prefer preserving whichever shape matches the user’s paste-back workflow for that file. Do not assume full `<Script>` wrappers are always correct.
- Custom function imports default to raw XML snippets, not calc-only output.
- Table imports are primarily for local reference and analysis, not for export-back workflows.
- Divider scripts named `-` are preserved as files.
- Filenames include stable FileMaker-scoped IDs by default, for example:
  - `Some Script__fmscript-1234.xml`
  - `HAM_Config__fmcf-84.xml`
- The XML content is the source of truth. The filename is a stable transport name.

## Important implementation note

When validating imports, do not assume the filename is enough. Spot-check that the embedded XML object matches the filename suffix and expected name.

For scripts, a healthy imported file can look like either of these:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<fmxmlsnippet type="FMObjectList">
<Script id="1234" name="Example Script">
...
</Script>
</fmxmlsnippet>
```

or, for step-paste workflows:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<fmxmlsnippet type="FMObjectList">
  <Step enable="True" id="89" name="# (comment)"/>
  <Step enable="True" id="141" name="Set Variable">
    ...
  </Step>
</fmxmlsnippet>
```

For tables, imported files should preserve nested fields inside `<BaseTable ...>` nodes.

## Known workflow phrases

These phrases are intentional shorthand for future prompts:

- `inspect clipboard`
- `preview import`
- `import clipboard`
- `clear tables`
- `clear scripts`
- `clear custom functions`
- `replace tables from clipboard`
- `replace scripts from clipboard`
- `replace custom functions from clipboard`

Interpret them as follows:

- `inspect clipboard`
  Report clipboard type, routed entity folder, and preview paths.
- `preview import`
  Show where the current clipboard would land without writing.
- `import clipboard`
  Import current clipboard into the routed entity folder, overwriting matching files.
- `clear ...`
  Remove everything inside that current entity folder and recreate it.
- `replace ... from clipboard`
  Clear the target entity folder first, then import from clipboard.
- `import ... from clipboard` without a namespace
  Try to infer the namespace from existing imported folder structure; if there is no clear single match, ask.

## Git and GitHub

This workspace can be versioned as a **workflow base** only.

- Do **not** commit imported snapshots under `FILEMAKER FILES/` or scratch under `agent-maintained/` (see `.gitignore`).
- Before each commit, **clear** the pending rows in `PASTE_BACK_QUEUE.md` so the committed file stays a template.
- Run `python3 tools/validate_paste_back_queue.py` before committing; CI runs the same check.

## Paste-back tracking

When imported FileMaker objects are edited locally, keep a running queue of what needs to be pasted back into FileMaker.

Tracker file:

- `PASTE_BACK_QUEUE.md`

Rules:

- If an edit changes any file under `FILEMAKER FILES/`, add or update an entry in `PASTE_BACK_QUEUE.md`.
- Track the local path, FileMaker object name, entity type, file namespace, and a short note about what changed.
- For scripts, note whether the file should be pasted back as:
  - `steps into existing script`, or
  - `full script object/import`
- For edited scripts, default that paste-back mode to `steps into existing script` unless the user explicitly wants full script-object output.
- Do not remove entries automatically just because a file was edited again; update the existing entry instead.
- Only clear entries when the user explicitly says the object has been pasted back or wants the queue cleared.

Useful future prompt patterns:

- `show paste-back queue`
- `clear paste-back queue`
- `mark pasted back`
- `what do I need to paste back?`

## Recommended testing flow

1. Copy a FileMaker object set.
2. Run `inspect clipboard`.
3. Run `preview import`.
4. If the payload is the full source-of-truth set for that entity type, run `replace ... from clipboard`.
5. Spot-check imported files for embedded IDs and names.
6. Before paste-back, verify the XML shape matches the intended workflow:
   - use full `<Script ...>` form when importing/replacing whole scripts
   - use step-only `<Step ...>` form when pasting steps into an existing script

## Multi-file solution convention

Some FileMaker solutions span multiple `.fmp12` files. Namespace imported objects by file name so entities from different files do not mix.

Recommended pattern:

- `FILEMAKER FILES/<file name>/scripts/...`
- `FILEMAKER FILES/<file name>/custom functions/...`
- `FILEMAKER FILES/<file name>/tables/...`
- `FILEMAKER FILES/<file name>/fields/...`

Examples:

- `FILEMAKER FILES/Main App/scripts/BF - onLogin__fmscript-1334.xml`
- `FILEMAKER FILES/Helper File/scripts/BF - onLogin__fmscript-77.xml`
- `FILEMAKER FILES/Main App/tables/Apps.xml`
- `FILEMAKER FILES/Reporting File/tables/Apps.xml`

Operational rule:

- Even when only one FileMaker file is in scope, prefer the same file-first layout for consistency.
- Put each FileMaker file in its own namespace folder under `FILEMAKER FILES/`.
- Do not mix objects from multiple FileMaker files into the same namespace folder unless the user explicitly wants that.

## BrightFin / BetterForms context

This repo contains BetterForms / BrightFin FileMaker artifacts and related notes.

Key system context:

- BrightFin is a FileMaker-based SaaS system.
- Important business entities include Apps, Organizations, Subscriptions, Plans, Pages, Sites, People, and related HAM authorization data.
- Imported FileMaker tables can be used to inspect real schema details such as field names and field definitions.

For broader system context, see `ARCHITECTURE.md`.
