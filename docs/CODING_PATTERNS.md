# Coding Patterns

This file is a committable reference for common coding and FileMaker script patterns used in this workspace.

## Core principles

- Prefer simple, readable code over dense or clever code.
- Keep setup, branching, data shaping, and output visually separated with whitespace.
- Add comments when they explain intent, business rules, or constraints. Remove comments that only restate the code.
- When touching an area, leave it a little clearer than you found it.

## FileMaker script headers

Use this standard header shape when updating or creating FileMaker scripts:

- `CONCERN:`
- `PARAMETERS:`
- optional `CONTEXT:`
- `RETURNS:`
- optional `NOTES:`
- optional `REVISIONS:`

Guidelines:

- Convert older labels like `PURPOSE` or `PARAMS` to the standard shape when safe.
- Remove stale author, generator, and boilerplate metadata unless it still carries operational value.
- Keep `REVISIONS` short and why-focused using `YYYY-MM-DD - summary`.

## API-first hook design

For BetterForms hooksets, prefer an API-first structure:

- Keep the hook entry script thin.
- Route by hook name or `options.type`.
- Move real business logic and data loading into dedicated helper scripts.
- Treat helpers like small internal APIs with clear responsibilities.

Good pattern:

1. `BF - onUtility - ...` reads `options.type`
2. dispatcher calls a dedicated helper script
3. helper reads data, normalizes output, and writes into `$$BF_Model`, `$$BF_App`, or `$$BF_Actions`

This keeps hook routing readable and makes helpers easier to test, reuse, and replace.

## Single-loop error trap

Prefer the single-loop error trap pattern for non-trivial FileMaker scripts:

1. `Set Error Capture [ On ]`
2. start one outer `Loop`
3. run the script body inside that loop
4. use `Exit Loop If [ 1 ]` for the one normal exit path
5. end the loop
6. `Exit Script`

Why:

- gives one predictable exit path
- makes future error handling easier to add
- avoids scattered early exits across the script

Inner loops are still fine when the script needs iteration. The pattern means one outer control loop for top-level flow and error handling.

## Comments and whitespace

Use body comments to mark major phases:

- `SECTION: Resolve inputs`
- `SECTION: Load source data`
- `SECTION: Normalize output`
- `SECTION: Write response`

Guidelines:

- Add blank comment rows around major sections in FileMaker scripts so they are easy to scan.
- Add extra blank comment rows when a section is dense or long enough to benefit from more breathing room.
- Within a major section, use short subsection labels when they make the flow easier to scan, for example `Basic inputs`, `Transport defaults`, or `Prompt rendering`.
- Add brief comments before dense calculations or fallback logic.
- Do not comment every step.

For imported FileMaker XML script files:

- Keep the XML indentation consistent and shallow so the step structure is easy to scan in raw XML form.
- Format multi-line `Calculation` blocks with visible line breaks and aligned `Let`, `Case`, and `JSONSetElement` entries instead of packing everything into one dense block.
- Prefer one visual chunk per concern: inputs, validation, data load, prompt rendering, tool resolution, transport, and output.
- When a script uses the single-loop error trap pattern, keep validation gates visually separated from the main success-path work.

## IDs and naming

- Use `id` for the current record or object primary key.
- Use typed foreign keys such as `idApp`, `idOrganization`, or `idPage` when the relationship is specific.
- Use BetterForms-owned ids for app-level concepts when possible.
- Do not use provider-native thread ids as the main persisted identifier for assistant history.

## Assistant session pattern

For assistant session history:

- keep history loading lightweight
- return summaries only, not full transcripts
- use BetterForms-owned session ids such as `SES_...`
- keep full session fetch separate from history listing
- prefer a helper-first implementation over embedding long logic directly in the hook dispatcher

## Paste-back workflow

Unless full script-object output is explicitly needed, keep edited FileMaker scripts as step-only `fmxmlsnippet` content.

For new scripts that callers depend on:

1. create or paste the new callee script first
2. re-import it if you want the assigned FileMaker id captured locally
3. then update and paste caller scripts

Do not assume a placeholder local script id for a brand-new callee will match the final FileMaker id assigned in FileMaker.
