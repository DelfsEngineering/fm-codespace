# First-time setup wizard (for the agent)

Use this document when the user is **new to the repo**, says **setup / wizard / onboarding / start here / first time**, or has **no `FILEMAKER FILES/<file name>/` namespace yet** (only the README). Walk them through in order; do not skip security or consent around server access.

## What this workspace does (one paragraph)

FileMaker stays authoritative. The user **copies** scripts and objects from FileMaker into files here, **edits** with full context (and optional AI help), then **pastes** changes back into FileMaker. Tables and schema can be **supplemented** from a server API or a DDR, but **script source** does not come from a live API.

## Wizard flow — follow in phases

### Phase 1 — Clipboard workflow (always; no server required)

1. Confirm they have **FileMaker Pro** (or appropriate client) and this repo open in **Cursor**.
2. Explain the loop: **Copy in FileMaker** → **import clipboard** into `FILEMAKER FILES/<file name>/…` → **edit** → **paste back** into the script (or replace steps) in FileMaker.
3. Point to `FILEMAKER FILES/README.md` for folder layout (`<file name>` = their `.fmp12` file or agreed namespace).
4. If `tools/fm_clipboard_import.py` exists, explain **inspect clipboard** / **import clipboard**; otherwise say they can paste importer output manually into the right folder.
5. Mention `PASTE_BACK_QUEUE.md` only as “track what you still need to paste back”—not as a public doc dump.

### Phase 2 — Schema reference (optional; choose one path)

**Reality check for the user:**

| Source | What you get | Scripts / script steps |
|--------|----------------|-------------------------|
| **Clipboard imports** | Whatever they copy | Yes, for copied objects |
| **OData** (FileMaker Server) | Data model, `$metadata` entities, often table/field names for OData-published tables | **No** — not a script API |
| **FileMaker Data API** | Records, layouts list, some metadata | **No** script definitions |
| **DDR** (Database Design Report) | Broad design export including scripts (XML) | **Yes**, when included in DDR |

So: **OData or Data API can help refresh table/field-oriented reference** under something like `FILEMAKER FILES/<file name>/tables/` **if** they want automation—but **cannot** replace clipboard/DDR for script text.

**If they want server-backed schema:**

1. **Do not** ask them to paste passwords into chat. Prefer **environment variables**, `.env` gitignored, or OS keychain—never commit secrets.
2. Ask what they run: **FileMaker Server** with OData enabled and which files/tables are published.
3. Set expectations: OData reflects **published** tables; not every DDR nuance (calculations, privileges) may appear.
4. If they only need a **one-time** full picture including scripts, recommend **DDR export** from FileMaker and import that into the workspace instead of or in addition to OData.

**If they decline server access:** Phase 1 only; use clipboard (and optional DDR they export themselves).

### Phase 3 — Scripts at scale

1. State clearly: **there is no supported “download all scripts” REST/OData call** on FileMaker Server for production files.
2. Options: **per-script clipboard** (fine-grained), or **DDR** for bulk script XML to align with this repo’s editing model.
3. Offer next steps: pick a script to round-trip first; add namespace folder; track paste-backs if they use the queue.

## Completion

Confirm they know: **where files go**, **that FileMaker is source of truth**, and **how paste-back works**. Offer to stay on the first script with them.
