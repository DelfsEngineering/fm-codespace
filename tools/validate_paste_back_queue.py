#!/usr/bin/env python3
"""
Fail if PASTE_BACK_QUEUE.md has any pending paste-back rows in the Pending table.

Committed copies of this repo should keep the queue empty (template only).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    path = root / "PASTE_BACK_QUEUE.md"
    if not path.is_file():
        print(f"error: missing {path}", file=sys.stderr)
        return 2

    lines = path.read_text(encoding="utf-8").splitlines()
    bad_rows, parse_error = _find_pending_table_data_rows(lines)
    if parse_error:
        print(f"error: {parse_error}", file=sys.stderr)
        return 2

    if bad_rows:
        print("error: PASTE_BACK_QUEUE.md has pending rows. Clear the table before committing.", file=sys.stderr)
        for line_no, line in bad_rows:
            print(f"  line {line_no}: {line.rstrip()!r}", file=sys.stderr)
        return 1

    print("ok: paste-back queue has no pending rows")
    return 0


def _find_pending_table_data_rows(
    lines: list[str],
) -> tuple[list[tuple[int, str]], str | None]:
    state = "seek_pending"
    bad: list[tuple[int, str]] = []

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        if state == "seek_pending":
            if stripped == "## Pending":
                state = "seek_table_header"
            continue

        if state == "seek_table_header":
            if stripped.startswith("|") and "Entity" in stripped and "File Namespace" in stripped:
                state = "seek_separator"
            continue

        if state == "seek_separator":
            if stripped.startswith("|") and _is_separator_row(stripped):
                state = "data_rows"
            continue

        if state == "data_rows":
            if stripped.startswith("##"):
                return bad, None
            if not stripped.startswith("|"):
                continue
            if _is_separator_row(stripped):
                continue
            if _row_has_content(stripped):
                bad.append((i, line))
            continue

    if state == "seek_pending":
        return [], "could not find ## Pending section"
    if state == "seek_table_header":
        return [], "could not find Pending table header row"
    if state == "seek_separator":
        return [], "could not find Pending table separator row"
    if state == "data_rows":
        return bad, None

    return [], "unexpected parse state"


def _is_separator_row(line: str) -> bool:
    cells = [c.strip() for c in line.strip().split("|")[1:-1]]
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells)


def _row_has_content(line: str) -> bool:
    cells = [c.strip() for c in line.strip().split("|")[1:-1]]
    return any(cells)


if __name__ == "__main__":
    raise SystemExit(main())
