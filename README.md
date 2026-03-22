# FileMaker codespace

**Make deep, complex FileMaker coding a snap.** This repo is a **Cursor-ready workspace** for working on real scripts, custom functions, and other solution objects **outside** the FileMaker Script Workspace—where you can think clearly, search and refactor, and pair with an AI that sees the whole picture.

**First time here?** In Cursor chat, say *“start the setup wizard”*—the agent will walk you through copy/paste round-trip and optional schema options. Details: [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

## Why this exists

FileMaker is brilliant at running solutions; long scripts and cross-file logic are still easier to **author and review** in a proper editor. Here you:

- **Round-trip through the clipboard** — copy objects from FileMaker into structured files, edit them with full context, then paste steps or whole snippets back where they belong.
- **Keep FileMaker as the source of truth** — these files are a working copy for editing and review, not a second system of record.
- **Use a predictable layout** so agents and humans always know where scripts, calcs, and schema snapshots live.

## How to use it

1. **Clone or copy** this repository and open the folder in **Cursor** (or your editor of choice).
2. **Import from FileMaker** using your clipboard workflow (for example the included importer under `tools/` when present) so objects land under `FILEMAKER FILES/<your file name>/…`.
3. **Edit** scripts and custom functions as text/XML; use the repo’s Cursor rules and `AGENTS.md` for conventions (step-only paste-back, naming, paste-back queue when you’re tracking edits).
4. **Paste back into FileMaker** — bring updated steps or snippets back into the live script or object in FileMaker and test there.

Typical layout:

```text
FILEMAKER FILES/<FileMaker file name>/scripts/
FILEMAKER FILES/<FileMaker file name>/custom functions/
FILEMAKER FILES/<FileMaker file name>/tables/
```

Imported solution snapshots stay **on your machine** (see `FILEMAKER FILES/README.md`); this repo ships the workflow shell, not your customer data.

## Contributing & tooling

Conventions, importer behavior, and maintainer notes live in **`AGENTS.md`**. Small helpers (for example queue validation) live under **`tools/`**.

## License

This project is released under the [MIT License](LICENSE).
