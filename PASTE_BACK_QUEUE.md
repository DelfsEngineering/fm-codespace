# Paste-Back Queue

Use this file to track imported FileMaker objects that were edited locally and need to be pasted back into FileMaker.

## How to use

- Add one row per edited FileMaker object.
- Update the existing row if the same object is edited again.
- Remove rows only after the user confirms the object has been pasted back or wants the queue cleared.

## Pending

**Paste-back shape:** These files are **step-only** `fmxmlsnippet` XML (`<Step …/>` children under `<fmxmlsnippet type="FMObjectList">`, **no** outer `<Script>` wrapper). Paste into the **existing** FileMaker script in FileMaker as **steps** (replace the matching step block), not as importing a whole new script object from a folder.

| Entity | File Namespace | FileMaker Object | FileMaker ID | Local Path | Status | Notes |
|---|---|---|---|---|---|---|



## Suggested statuses

- `edited`
- `ready to paste back`
- `pasted back`
- `superseded`
