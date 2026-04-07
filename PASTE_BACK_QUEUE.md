# Paste-Back Queue

Use this file to track imported FileMaker objects that were edited locally and need to be pasted back into FileMaker.

## How to use

- Add one row per edited FileMaker object.
- Update the existing row if the same object is edited again.
- This file only shows pending rows (`edited`, `ready to paste back`, `queued`).
- Once an item is pasted back, remove it from this markdown queue so the file can return to empty.

## Pending

Keep local paste-back details out of commits. Before committing, clear any pending rows so this file stays a reusable template in git history.

| Entity | File Namespace | FileMaker Object | FileMaker ID | Local Path | Status | Notes |
|---|---|---|---|---|---|---|


## Suggested statuses

- `edited`
- `ready to paste back`
- `superseded`
