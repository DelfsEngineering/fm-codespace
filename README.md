# BF Scripts (FileMaker workspace base)

This repository holds **workflow and agent guidance** for editing FileMaker objects that are round-tripped through the clipboard. **FileMaker remains the source of truth** for solution objects.

## What gets committed

- `AGENTS.md` — agent and editing conventions
- `.cursor/rules/` — Cursor rules for this workspace
- `PASTE_BACK_QUEUE.md` — **template only**: must have **no pending rows** when you commit
- `tools/` — small validation helpers

## What does not get committed

- `FILEMAKER FILES/` — local snapshots of imported scripts, tables, etc. (ignored)
- `agent-maintained/` — local scratch copies (ignored)

See `.gitignore` for the full list.

## Layout

Imported artifacts live under a file-first namespace:

```text
FILEMAKER FILES/<file name>/scripts/
FILEMAKER FILES/<file name>/tables/
FILEMAKER FILES/<file name>/custom functions/
```

## Before every commit

1. **Clear** the pending table in `PASTE_BACK_QUEUE.md` (leave the header row and separator only).
2. Run validation locally:

   ```bash
   python3 tools/validate_paste_back_queue.py
   ```

CI runs the same check on push and pull requests.

## License

This project is released under the [MIT License](LICENSE).

## Creating the GitHub repo

From this folder (after `git init` and first commit):

```bash
gh repo create <your-org-or-user>/bf-scripts --private --source=. --remote=origin --push
```

Or create an empty repo in the GitHub UI and:

```bash
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```
