# `FILEMAKER FILES/`

This directory is the **on-disk workspace** for objects imported from the FileMaker clipboard (scripts, custom functions, tables, layouts, and other supported entity types).

## Rules

- **FileMaker is the source of truth.** These files are local snapshots for editing and review in the IDE, not the authoritative copy of your solution.
- **Imported content is not committed to git.** Everything under this folder except **this README** is listed in `.gitignore` so customer data and solution internals stay on your machine.
- **Layout:** use a **file-first** namespace: `FILEMAKER FILES/<FileMaker file or app name>/<entity type>/…` (for example `scripts/`, `tables/`, `custom functions/`).

Re-importing from the clipboard overwrites matching files locally when you run your import workflow.
