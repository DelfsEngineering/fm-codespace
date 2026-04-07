#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import sqlite3
import tempfile
import threading
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import fm_clipboard_import as importer
except ModuleNotFoundError:  # Allows importing as tools.fm_workspace_ui from workspace root.
    from tools import fm_clipboard_import as importer


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_RESULT_PATHS = 250
MAX_FILE_CONTENT_BYTES = 1_000_000
AUTO_TRACK_POLL_SECONDS = 15
STATE_PATH = Path("agent-maintained/fm_workspace_ui_state.json")
QUEUE_DB_PATH = Path("agent-maintained/fm_workspace_ui.sqlite3")
QUEUE_PATH = Path("PASTE_BACK_QUEUE.md")
# Anchor to repo root regardless of launch cwd so tree/queue paths stay stable.
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
FILEMAKER_ROOT = WORKSPACE_ROOT / importer.FILEMAKER_FILES_DIR_NAME


ENTITY_DISPLAY_NAMES = {
    "scripts": "Script",
    "custom functions": "Custom Function",
    "tables": "Table",
    "fields": "Field",
    "value lists": "Value List",
    "layouts": "Layout",
    "themes": "Theme",
    "custom menus": "Custom Menu",
    "other fm objects": "Other FM Object",
}

FM_CLIPBOARD_TYPES_BY_KIND = {
    "script": "dyn.ah62d4rv4gk8zuxnxkq",
    "script-step": "dyn.ah62d4rv4gk8zuxnxnq",
    "custom-function": "dyn.ah62d4rv4gk8zuxngm2",
    "table": "dyn.ah62d4rv4gk8zuxnykk",
    "field": "dyn.ah62d4rv4gk8zuxngku",
    "value-list": "dyn.ah62d4rv4gk8zuxn0mu",
    "layout-object-fmp12": "dyn.ah62d4rv4gk8zuxnqgk",
    "layout-object-fp7": "dyn.ah62d4rv4gk8zuxnqm6",
    "theme": "dyn.ah62d4rv4gk8zuxnyma",
    "custom-menu": "public.utf16-plain-text",
}

QUEUE_HEADER = "| Entity | File Namespace | FileMaker Object | FileMaker ID | Local Path | Status | Notes |"
QUEUE_COLUMNS = 7


@dataclass
class QueueRow:
    entity: str
    file_namespace: str
    filemaker_object: str
    filemaker_id: str
    local_path: str
    status: str
    notes: str
    line_index: int = -1
    raw_columns: list[str] | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def path_key(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(WORKSPACE_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def safe_resolve_path(raw_path: str) -> Path:
    base = Path(raw_path).expanduser()
    target = base if base.is_absolute() else (WORKSPACE_ROOT / base)
    resolved = target.resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise ValueError("Path must stay inside the workspace.") from exc
    return resolved


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 64)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "baselines": {},
        "autoTrackInitialized": False,
        "autoTrackSeeded": False,
    }


def load_state() -> dict[str, Any]:
    state_path = WORKSPACE_ROOT / STATE_PATH
    if not state_path.exists():
        return default_state()
    try:
        parsed = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_state()
    if not isinstance(parsed, dict):
        return default_state()
    if "baselines" not in parsed or not isinstance(parsed["baselines"], dict):
        parsed["baselines"] = {}
    if "autoTrackInitialized" not in parsed:
        parsed["autoTrackInitialized"] = False
    if "autoTrackSeeded" not in parsed:
        parsed["autoTrackSeeded"] = False
    return parsed


def save_state(state: dict[str, Any]) -> None:
    state_path = WORKSPACE_ROOT / STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def baseline_record_for_path(path: Path, sha256_value: str | None = None) -> dict[str, Any]:
    stat = path.stat()
    return {
        "sha256": sha256_value or sha256_file(path),
        "sizeBytes": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "updatedAt": now_iso(),
    }


def set_baselines_from_results(results: list[importer.WriteResult]) -> None:
    state = load_state()
    baselines = state.setdefault("baselines", {})
    for result in results:
        if result.action == "skip":
            continue
        key = path_key(result.path)
        if result.path.exists():
            baselines[key] = baseline_record_for_path(
                result.path,
                sha256_value=sha256_text(result.item.output_text),
            )
    state["autoTrackInitialized"] = True
    save_state(state)


def make_payload(
    input_path: str | None,
    input_text: str | None = None,
    input_name: str | None = None,
) -> importer.ClipboardPayload:
    if input_text is not None:
        return importer.ClipboardPayload(
            source=input_name or "uploaded-file",
            clipboard_type="file-content",
            kind=importer.infer_kind_from_xml(input_text),
            xml_text=input_text,
            size_bytes=len(input_text.encode("utf-8")),
            available_types=["file-content"],
        )

    if input_path:
        path = safe_resolve_path(input_path)
        xml_text = path.read_text(encoding="utf-8")
        return importer.ClipboardPayload(
            source=str(path),
            clipboard_type="file",
            kind=importer.infer_kind_from_xml(xml_text),
            xml_text=xml_text,
            size_bytes=len(xml_text.encode("utf-8")),
            available_types=["file"],
        )
    try:
        return importer.read_payload_from_clipboard()
    except importer.ImporterError as exc:
        if platform.system() != "Darwin":
            raise importer.ImporterError(
                "Live clipboard import is currently macOS-first. On this OS, use Input XML Path "
                "or extend the tool with a Windows clipboard adapter."
            ) from exc
        raise


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def split_markdown_row(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|"):
        return []
    parts = [part.strip() for part in raw.strip("|").split("|")]
    return parts


def normalize_queue_local_path(local_path: str) -> str:
    stripped = local_path.strip().strip("`").strip()
    if not stripped:
        return ""
    path_obj = Path(stripped)
    target = path_obj if path_obj.is_absolute() else WORKSPACE_ROOT / path_obj
    return path_key(target.resolve())


def queue_table_lines() -> list[str]:
    queue_file = WORKSPACE_ROOT / QUEUE_PATH
    if not queue_file.exists():
        return []
    return queue_file.read_text(encoding="utf-8").splitlines(keepends=True)


def parse_queue_rows(lines: list[str]) -> list[QueueRow]:
    rows: list[QueueRow] = []
    in_table = False
    for idx, line in enumerate(lines):
        text = line.strip()
        if text == QUEUE_HEADER:
            in_table = True
            continue
        if not in_table:
            continue
        if text.startswith("|---"):
            continue
        if not text.startswith("|"):
            if rows:
                break
            continue
        columns = split_markdown_row(line)
        if len(columns) != QUEUE_COLUMNS:
            continue
        rows.append(
            QueueRow(
                entity=columns[0],
                file_namespace=columns[1],
                filemaker_object=columns[2],
                filemaker_id=columns[3],
                local_path=columns[4].strip("`"),
                status=columns[5],
                notes=columns[6],
                line_index=idx,
                raw_columns=columns,
            )
        )
    return rows


def queue_db_connection() -> sqlite3.Connection:
    db_path = WORKSPACE_ROOT / QUEUE_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def queue_meta_get(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM queue_meta WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return str(row["value"])


def queue_meta_set(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO queue_meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def ensure_queue_db() -> None:
    migrated = False
    with queue_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity TEXT NOT NULL,
                file_namespace TEXT NOT NULL,
                filemaker_object TEXT NOT NULL,
                filemaker_id TEXT NOT NULL DEFAULT '',
                local_path TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS queue_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        if queue_meta_get(connection, "migrated_from_markdown_v1") is None:
            rows = parse_queue_rows(queue_table_lines())
            timestamp = now_iso()
            for row in rows:
                local_path = normalize_queue_local_path(row.local_path)
                if not local_path:
                    continue
                connection.execute(
                    """
                    INSERT INTO queue_items (
                        entity,
                        file_namespace,
                        filemaker_object,
                        filemaker_id,
                        local_path,
                        status,
                        notes,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(local_path) DO UPDATE SET
                        entity = excluded.entity,
                        file_namespace = excluded.file_namespace,
                        filemaker_object = excluded.filemaker_object,
                        filemaker_id = excluded.filemaker_id,
                        status = excluded.status,
                        notes = excluded.notes,
                        updated_at = excluded.updated_at
                    """,
                    (
                        row.entity,
                        row.file_namespace,
                        row.filemaker_object,
                        row.filemaker_id,
                        local_path,
                        row.status,
                        row.notes,
                        timestamp,
                        timestamp,
                    ),
                )
            queue_meta_set(connection, "migrated_from_markdown_v1", timestamp)
            connection.commit()
            migrated = True

    if migrated:
        sync_markdown_from_db()


def row_to_api(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "entity": str(row["entity"]),
        "fileNamespace": str(row["file_namespace"]),
        "fileMakerObject": str(row["filemaker_object"]),
        "fileMakerId": str(row["filemaker_id"]),
        "localPath": str(row["local_path"]),
        "status": str(row["status"]),
        "notes": str(row["notes"]),
        "updatedAt": str(row["updated_at"]),
    }


def queue_rows_for_api() -> list[dict[str, Any]]:
    ensure_queue_db()
    with queue_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                entity,
                file_namespace,
                filemaker_object,
                filemaker_id,
                local_path,
                status,
                notes,
                updated_at
            FROM queue_items
            ORDER BY updated_at DESC, local_path ASC
            """
        ).fetchall()
    return [row_to_api(row) for row in rows]


def queue_rows_by_path() -> dict[str, dict[str, Any]]:
    rows = queue_rows_for_api()
    return {str(row["localPath"]): row for row in rows}


def is_pending_queue_status(status: str) -> bool:
    normalized = (status or "").strip().lower()
    return normalized in {"edited", "ready to paste back", "queued"}


def sync_markdown_from_db() -> None:
    rows = [row for row in queue_rows_for_api() if is_pending_queue_status(str(row.get("status", "")))]
    lines = [
        "# Paste-Back Queue\n",
        "\n",
        "Use this file to track imported FileMaker objects that were edited locally and need to be pasted back into FileMaker.\n",
        "\n",
        "## How to use\n",
        "\n",
        "- Add one row per edited FileMaker object.\n",
        "- Update the existing row if the same object is edited again.\n",
        "- This file only shows pending rows (`edited`, `ready to paste back`, `queued`).\n",
        "- Once an item is pasted back, remove it from this markdown queue so the file can return to empty.\n",
        "\n",
        "## Pending\n",
        "\n",
        "Keep local paste-back details out of commits. Before committing, clear any pending rows so this file stays a reusable template in git history.\n",
        "\n",
        f"{QUEUE_HEADER}\n",
        "|---|---|---|---|---|---|---|\n",
    ]
    for row in rows:
        safe_values = [
            str(row["entity"]).replace("|", "/"),
            str(row["fileNamespace"]).replace("|", "/"),
            str(row["fileMakerObject"]).replace("|", "/"),
            str(row["fileMakerId"]).replace("|", "/"),
            f"`{str(row['localPath']).replace('|', '/')}`",
            str(row["status"]).replace("|", "/"),
            str(row["notes"]).replace("|", "/"),
        ]
        lines.append(
            f"| {safe_values[0]} | {safe_values[1]} | {safe_values[2]} | {safe_values[3]} | {safe_values[4]} | {safe_values[5]} | {safe_values[6]} |\n"
        )
    lines.extend(
        [
            "\n",
            "\n",
            "## Suggested statuses\n",
            "\n",
            "- `edited`\n",
            "- `ready to paste back`\n",
            "- `superseded`\n",
        ]
    )
    (WORKSPACE_ROOT / QUEUE_PATH).write_text("".join(lines), encoding="utf-8")


def filename_object_info(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    match = re.search(r"__(fm[a-z]+-(\d+))$", stem)
    if not match:
        return stem, ""
    name = stem[: match.start()]
    return name, (match.group(2) or "")


def normalize_object_name(value: str) -> str:
    lowered = (value or "").strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def entity_name_for_queue(folder_name: str) -> str:
    return ENTITY_DISPLAY_NAMES.get(folder_name, folder_name or "Object")


def derive_queue_row_from_path(file_path: Path, status: str, notes: str) -> dict[str, str]:
    key = path_key(file_path)
    rel = Path(key)
    parts = rel.parts
    file_namespace = ""
    entity_label = "Object"
    if len(parts) >= 3 and parts[0] == importer.FILEMAKER_FILES_DIR_NAME:
        file_namespace = parts[1]
        entity_label = entity_name_for_queue(parts[2])
    object_name, object_id = filename_object_info(file_path.name)
    return {
        "entity": entity_label,
        "fileNamespace": file_namespace,
        "fileMakerObject": object_name,
        "fileMakerId": object_id,
        "localPath": key,
        "status": status,
        "notes": notes or "Updated from FM CodeSpace.",
    }


def update_queue_status(file_path: Path, status: str, notes: str | None) -> dict[str, Any]:
    ensure_queue_db()
    key = path_key(file_path)
    note_text = (notes or "").strip()
    timestamp = now_iso()
    with queue_db_connection() as connection:
        existing = connection.execute(
            """
            SELECT
                entity,
                file_namespace,
                filemaker_object,
                filemaker_id,
                local_path,
                status,
                notes,
                created_at,
                updated_at
            FROM queue_items
            WHERE local_path = ?
            """,
            (key,),
        ).fetchone()

        if existing:
            merged_notes = str(existing["notes"])
            if note_text:
                merged_notes = f"{merged_notes} {note_text}".strip()
            connection.execute(
                """
                UPDATE queue_items
                SET status = ?, notes = ?, updated_at = ?
                WHERE local_path = ?
                """,
                (status, merged_notes, timestamp, key),
            )
        else:
            derived = derive_queue_row_from_path(
                file_path=file_path,
                status=status,
                notes=note_text,
            )
            connection.execute(
                """
                INSERT INTO queue_items (
                    entity,
                    file_namespace,
                    filemaker_object,
                    filemaker_id,
                    local_path,
                    status,
                    notes,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_path) DO UPDATE SET
                    entity = excluded.entity,
                    file_namespace = excluded.file_namespace,
                    filemaker_object = excluded.filemaker_object,
                    filemaker_id = excluded.filemaker_id,
                    status = excluded.status,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (
                    derived["entity"],
                    derived["fileNamespace"],
                    derived["fileMakerObject"],
                    derived["fileMakerId"],
                    derived["localPath"],
                    derived["status"],
                    derived["notes"],
                    timestamp,
                    timestamp,
                ),
            )
        refreshed = connection.execute(
            """
            SELECT
                entity,
                file_namespace,
                filemaker_object,
                filemaker_id,
                local_path,
                status,
                notes,
                updated_at
            FROM queue_items
            WHERE local_path = ?
            """,
            (key,),
        ).fetchone()
        connection.commit()

    sync_markdown_from_db()
    if refreshed is not None:
        return row_to_api(refreshed)
    return {"localPath": key, "status": status, "notes": note_text}


def clear_script_queue_rows() -> dict[str, int]:
    ensure_queue_db()
    deleted = 0
    script_glob = f"{importer.FILEMAKER_FILES_DIR_NAME}/%/scripts/%"
    with queue_db_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM queue_items
            WHERE local_path LIKE ?
            """,
            (script_glob,),
        )
        deleted = int(cursor.rowcount or 0)
        connection.commit()
    sync_markdown_from_db()
    return {"deleted": deleted}


def auto_track_workspace_edits() -> dict[str, int | bool]:
    ensure_queue_db()
    state = load_state()
    baselines = state.setdefault("baselines", {})
    initialized = bool(state.get("autoTrackInitialized"))
    seeded = bool(state.get("autoTrackSeeded"))
    now = now_iso()

    files = [path for path in FILEMAKER_ROOT.rglob("*") if path.is_file()] if FILEMAKER_ROOT.exists() else []
    present_keys: set[str] = set()
    changed_paths: list[Path] = []
    new_paths: list[Path] = []

    for file_path in files:
        key = path_key(file_path)
        present_keys.add(key)

        baseline = baselines.get(key)
        if not isinstance(baseline, dict):
            record = baseline_record_for_path(file_path)
            baselines[key] = record
            if seeded:
                new_paths.append(file_path)
            continue

        stat = file_path.stat()
        size_matches = baseline.get("sizeBytes") == stat.st_size
        mtime_matches = baseline.get("mtimeNs") == stat.st_mtime_ns
        baseline_hash = str(baseline.get("sha256") or "")

        if size_matches and mtime_matches and baseline_hash:
            continue

        current_hash = sha256_file(file_path)
        if baseline_hash and current_hash == baseline_hash:
            baselines[key] = {
                **baseline,
                "sizeBytes": stat.st_size,
                "mtimeNs": stat.st_mtime_ns,
                "updatedAt": now,
            }
            continue

        baselines[key] = {
            "sha256": current_hash,
            "sizeBytes": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "updatedAt": now,
        }
        if initialized:
            changed_paths.append(file_path)

    removed_keys = [
        key for key in list(baselines.keys())
        if key.startswith(f"{importer.FILEMAKER_FILES_DIR_NAME}/") and key not in present_keys
    ]
    for key in removed_keys:
        baselines.pop(key, None)

    state["autoTrackInitialized"] = True
    state["autoTrackSeeded"] = True

    tracked = 0
    if initialized and (changed_paths or new_paths):
        with queue_db_connection() as connection:
            for file_path in [*changed_paths, *new_paths]:
                derived = derive_queue_row_from_path(
                    file_path=file_path,
                    status="edited",
                    notes="Auto-tracked local file edit.",
                )
                existing = connection.execute(
                    "SELECT notes FROM queue_items WHERE local_path = ?",
                    (derived["localPath"],),
                ).fetchone()
                notes = derived["notes"]
                if existing:
                    existing_notes = str(existing["notes"])
                    if "Auto-tracked local file edit." not in existing_notes:
                        notes = f"{existing_notes} Auto-tracked local file edit."
                    else:
                        notes = existing_notes

                connection.execute(
                    """
                    INSERT INTO queue_items (
                        entity,
                        file_namespace,
                        filemaker_object,
                        filemaker_id,
                        local_path,
                        status,
                        notes,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(local_path) DO UPDATE SET
                        entity = excluded.entity,
                        file_namespace = excluded.file_namespace,
                        filemaker_object = excluded.filemaker_object,
                        filemaker_id = excluded.filemaker_id,
                        status = excluded.status,
                        notes = excluded.notes,
                        updated_at = excluded.updated_at
                    """,
                    (
                        derived["entity"],
                        derived["fileNamespace"],
                        derived["fileMakerObject"],
                        derived["fileMakerId"],
                        derived["localPath"],
                        "edited",
                        notes,
                        now,
                        now,
                    ),
                )
                tracked += 1
            connection.commit()
        sync_markdown_from_db()

    result = {
        "initialized": initialized,
        "seeded": seeded,
        "trackedEdits": tracked,
        "changedCount": len(changed_paths),
        "newCount": len(new_paths),
    }
    state["lastAutoTrack"] = {**result, "ranAt": now_iso()}
    save_state(state)
    return result


def tracking_summary() -> dict[str, Any]:
    state = load_state()
    last = state.get("lastAutoTrack")
    if isinstance(last, dict):
        return {
            "initialized": bool(last.get("initialized", False)),
            "seeded": bool(last.get("seeded", False)),
            "trackedEdits": int(last.get("trackedEdits", 0)),
            "changedCount": int(last.get("changedCount", 0)),
            "newCount": int(last.get("newCount", 0)),
            "ranAt": str(last.get("ranAt", "")),
        }
    return {
        "initialized": bool(state.get("autoTrackInitialized", False)),
        "seeded": bool(state.get("autoTrackSeeded", False)),
        "trackedEdits": 0,
        "changedCount": 0,
        "newCount": 0,
        "ranAt": "",
    }


def file_status_for_path(
    file_path: Path,
    baselines: dict[str, Any],
    queue_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = path_key(file_path)
    queue_row = queue_by_path.get(key)
    queue_status = str(queue_row.get("status", "")).strip() if queue_row else ""

    baseline = baselines.get(key)
    local_status = "unknown"
    baseline_hash = ""
    if isinstance(baseline, dict) and baseline.get("sha256"):
        baseline_hash = str(baseline["sha256"])
        local_status = "clean"
        if baseline_hash != sha256_file(file_path):
            local_status = "edited"

    if queue_status:
        if queue_status.lower() == "pasted back":
            badge = "pasted back"
        else:
            badge = "queued"
    else:
        badge = local_status

    return {
        "path": key,
        "localStatus": local_status,
        "queueStatus": queue_status,
        "badge": badge,
        "isQueued": bool(queue_row),
    }


def list_namespaces() -> list[str]:
    if not FILEMAKER_ROOT.exists():
        return []
    return sorted(
        [entry.name for entry in FILEMAKER_ROOT.iterdir() if entry.is_dir()],
        key=str.lower,
    )


def list_entities(namespace: str) -> list[str]:
    ns_path = FILEMAKER_ROOT / namespace
    if not ns_path.exists() or not ns_path.is_dir():
        return []
    return sorted(
        [entry.name for entry in ns_path.iterdir() if entry.is_dir()],
        key=str.lower,
    )


def tree_children(path_value: str | None) -> list[dict[str, Any]]:
    if path_value:
        target = safe_resolve_path(path_value)
    else:
        target = FILEMAKER_ROOT

    if not target.exists():
        return []
    if not target.is_dir():
        raise ValueError("Tree path must point to a folder.")

    try:
        target.resolve().relative_to(FILEMAKER_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Tree path must stay inside FILEMAKER FILES.") from exc

    entries = sorted(
        [entry for entry in target.iterdir()],
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )
    is_filemaker_root = target.resolve() == FILEMAKER_ROOT.resolve()
    output: list[dict[str, Any]] = []
    for entry in entries:
        # In the root tree view, only show FileMaker namespaces (folders).
        if is_filemaker_root and not entry.is_dir():
            continue

        has_children = False
        if entry.is_dir():
            try:
                has_children = any(entry.iterdir())
            except OSError:
                has_children = False
        output.append(
            {
                "name": entry.name,
                "path": path_key(entry),
                "type": "folder" if entry.is_dir() else "file",
                "hasChildren": has_children,
            }
        )
    return output


def search_files(query: str, limit: int = 200) -> list[dict[str, Any]]:
    lowered = query.strip().lower()
    if not lowered or not FILEMAKER_ROOT.exists():
        return []

    queue_by_path = queue_rows_by_path()
    state = load_state()
    baselines = state.get("baselines", {})

    results: list[dict[str, Any]] = []
    for file_path in FILEMAKER_ROOT.rglob("*"):
        if not file_path.is_file():
            continue
        key = path_key(file_path)
        if lowered not in key.lower() and lowered not in file_path.name.lower():
            continue

        object_name, object_id = filename_object_info(file_path.name)
        status = file_status_for_path(file_path, baselines=baselines, queue_by_path=queue_by_path)
        results.append(
            {
                "path": key,
                "name": file_path.name,
                "objectName": object_name,
                "fileMakerId": object_id,
                "badge": status["badge"],
            }
        )
        if len(results) >= limit:
            break

    results.sort(
        key=lambda item: (
            0 if item["name"].lower().startswith(lowered) else 1,
            item["name"].lower(),
            item["path"].lower(),
        )
    )
    return results


def clipboard_status_report() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {
            "available": False,
            "light": "off",
            "message": "Live clipboard monitoring is currently macOS-first.",
            "comparison": {"status": "unknown", "matched": 0, "total": 0},
        }

    try:
        payload = importer.read_payload_from_clipboard()
    except importer.ImporterError as exc:
        return {
            "available": False,
            "light": "off",
            "message": str(exc),
            "comparison": {"status": "unknown", "matched": 0, "total": 0},
        }

    fallback_namespace = importer.FALLBACK_DIR_NAME
    try:
        items = importer.collect_import_items(
            payload.xml_text,
            fallback_name=None,
            script_format="raw",
            custom_function_format="xml",
            fallback_dir=fallback_namespace,
            filename_style="pretty-id",
        )
    except (importer.ImporterError, ET.ParseError) as exc:
        return {
            "available": True,
            "light": "warn",
            "kind": payload.kind,
            "clipboardType": payload.clipboard_type,
            "itemCount": 0,
            "message": f"Clipboard has FileMaker payload, but parsing failed: {exc}",
            "comparison": {"status": "unknown", "matched": 0, "total": 0},
        }

    routed_items = importer.route_items_to_filemaker_layout(
        items=items,
        base_root=WORKSPACE_ROOT,
        payload_kind=payload.kind,
        explicit_file_namespace=None,
        fallback_namespace=fallback_namespace,
        entity_folder_mode="auto",
    )

    entity_indexes: dict[str, set[str]] = {}
    for item in routed_items:
        if len(item.relative_parts) < 3:
            continue
        entity = item.relative_parts[2]
        if entity in entity_indexes:
            continue
        name_index: set[str] = set()
        for candidate in FILEMAKER_ROOT.glob(f"*/{entity}/**/*"):
            if not candidate.is_file():
                continue
            object_name, _ = filename_object_info(candidate.name)
            name_index.add(normalize_object_name(object_name))
        entity_indexes[entity] = name_index

    path_matches = 0
    name_matches = 0
    total = len(routed_items)
    for item in routed_items:
        local_path = WORKSPACE_ROOT.joinpath(*item.relative_parts)
        if local_path.exists():
            path_matches += 1
            continue
        entity = item.relative_parts[2] if len(item.relative_parts) > 2 else ""
        object_name = normalize_object_name(item.name)
        if entity and object_name and object_name in entity_indexes.get(entity, set()):
            name_matches += 1

    matched = path_matches + name_matches
    if total == 0:
        compare_status = "none"
    elif path_matches == total:
        compare_status = "exact"
    elif matched == total:
        compare_status = "likely"
    elif matched > 0:
        compare_status = "partial"
    else:
        compare_status = "none"

    if compare_status == "exact":
        light = "ok"
    elif compare_status in {"likely", "partial"}:
        light = "warn"
    else:
        light = "alert"
    namespaces = sorted(
        {
            item.relative_parts[1]
            for item in routed_items
            if len(item.relative_parts) > 2 and item.relative_parts[0] == importer.FILEMAKER_FILES_DIR_NAME
        }
    )
    entities = sorted(
        {
            item.relative_parts[2]
            for item in routed_items
            if len(item.relative_parts) > 2 and item.relative_parts[0] == importer.FILEMAKER_FILES_DIR_NAME
        }
    )

    return {
        "available": True,
        "light": light,
        "kind": payload.kind,
        "clipboardType": payload.clipboard_type,
        "itemCount": total,
        "namespaces": namespaces,
        "entities": entities,
        "comparison": {
            "status": compare_status,
            "matched": matched,
            "pathMatched": path_matches,
            "nameMatched": name_matches,
            "total": total,
        },
        "message": "Clipboard inspected. Matching uses path first, then object-name fallback (ID-light).",
    }


def list_entity_files(namespace: str, entity: str, query: str = "") -> list[dict[str, Any]]:
    entity_path = FILEMAKER_ROOT / namespace / entity
    if not entity_path.exists() or not entity_path.is_dir():
        return []

    queue_by_path = queue_rows_by_path()
    state = load_state()
    baselines = state.get("baselines", {})
    lowered_query = query.strip().lower()

    files: list[dict[str, Any]] = []
    for file_path in sorted([path for path in entity_path.rglob("*") if path.is_file()]):
        key = path_key(file_path)
        if lowered_query and lowered_query not in key.lower() and lowered_query not in file_path.name.lower():
            continue
        object_name, object_id = filename_object_info(file_path.name)
        status = file_status_for_path(file_path, baselines=baselines, queue_by_path=queue_by_path)
        files.append(
            {
                "path": key,
                "name": file_path.name,
                "objectName": object_name,
                "fileMakerId": object_id,
                "sizeBytes": file_path.stat().st_size,
                **status,
            }
        )
    return files


def file_detail(path_value: str) -> dict[str, Any]:
    path_obj = safe_resolve_path(path_value)
    if not path_obj.is_file():
        raise FileNotFoundError("Selected path is not a file.")

    raw_bytes = path_obj.read_bytes()
    truncated = len(raw_bytes) > MAX_FILE_CONTENT_BYTES
    preview_bytes = raw_bytes[:MAX_FILE_CONTENT_BYTES]
    content = preview_bytes.decode("utf-8", errors="replace")
    if truncated:
        content += "\n\n<!-- Truncated for UI preview -->\n"

    queue_by_path = queue_rows_by_path()
    state = load_state()
    baselines = state.get("baselines", {})
    status = file_status_for_path(path_obj, baselines=baselines, queue_by_path=queue_by_path)

    queue_row = queue_by_path.get(path_key(path_obj))
    queue_data = queue_row if queue_row else None
    return {
        "path": path_key(path_obj),
        "sizeBytes": len(raw_bytes),
        "content": content,
        "truncated": truncated,
        "queueRow": queue_data,
        **status,
    }


def copy_file_as_fm_clipboard(path_value: str) -> dict[str, Any]:
    path_obj = safe_resolve_path(path_value)
    if not path_obj.is_file():
        raise FileNotFoundError("Selected path is not a file.")

    xml_text = path_obj.read_text(encoding="utf-8")
    if not importer.looks_like_filemaker_xml(xml_text):
        raise ValueError("Selected file does not look like FileMaker XML.")

    kind = importer.infer_kind_from_xml(xml_text)
    clipboard_type = FM_CLIPBOARD_TYPES_BY_KIND.get(kind)
    if not clipboard_type:
        raise ValueError(f"Copy as FM is not supported for payload kind '{kind}'.")

    if platform.system() != "Darwin":
        raise importer.ImporterError("Copy as FM currently requires macOS.")

    try:
        from AppKit import NSPasteboard  # type: ignore
        from Foundation import NSData  # type: ignore
    except ImportError as exc:
        raise importer.ImporterError(
            "PyObjC is required for Copy as FM. Install it with "
            "`python3 -m pip install \"pyobjc==11.1\"`."
        ) from exc

    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()

    payload_bytes = xml_text.encode("utf-8")
    payload_data = NSData.dataWithBytes_length_(payload_bytes, len(payload_bytes))
    primary_ok = bool(pasteboard.setData_forType_(payload_data, clipboard_type))
    if not primary_ok:
        raise importer.ImporterError("Failed to write FileMaker payload to the clipboard.")

    pasteboard.setString_forType_(xml_text, "public.utf8-plain-text")
    pasteboard.setString_forType_(xml_text, "public.utf16-plain-text")

    return {
        "path": path_key(path_obj),
        "kind": kind,
        "clipboardType": clipboard_type,
        "fallbackTypes": ["public.utf8-plain-text", "public.utf16-plain-text"],
    }


def copy_file_as_fm_steps_clipboard(path_value: str) -> dict[str, Any]:
    path_obj = safe_resolve_path(path_value)
    if not path_obj.is_file():
        raise FileNotFoundError("Selected path is not a file.")

    xml_text = path_obj.read_text(encoding="utf-8")
    if not importer.looks_like_filemaker_xml(xml_text):
        raise ValueError("Selected file does not look like FileMaker XML.")

    kind = importer.infer_kind_from_xml(xml_text)
    if kind not in {"script", "script-step"}:
        raise ValueError("Copy as FM Steps supports script files only.")

    steps_xml = xml_text
    if kind == "script":
        root = ET.fromstring(xml_text)
        script_node = next(
            (child for child in list(root) if importer.local_tag(child.tag) == "Script"),
            None,
        )
        if script_node is None:
            raise ValueError("Script payload is missing a Script node.")
        steps_xml = importer.serialize_step_snippet(list(script_node))

    clipboard_type = FM_CLIPBOARD_TYPES_BY_KIND["script-step"]

    if platform.system() != "Darwin":
        raise importer.ImporterError("Copy as FM Steps currently requires macOS.")

    try:
        from AppKit import NSPasteboard  # type: ignore
        from Foundation import NSData  # type: ignore
    except ImportError as exc:
        raise importer.ImporterError(
            "PyObjC is required for Copy as FM Steps. Install it with "
            "`python3 -m pip install \"pyobjc==11.1\"`."
        ) from exc

    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()

    payload_bytes = steps_xml.encode("utf-8")
    payload_data = NSData.dataWithBytes_length_(payload_bytes, len(payload_bytes))
    primary_ok = bool(pasteboard.setData_forType_(payload_data, clipboard_type))
    if not primary_ok:
        raise importer.ImporterError("Failed to write FileMaker step payload to the clipboard.")

    pasteboard.setString_forType_(steps_xml, "public.utf8-plain-text")
    pasteboard.setString_forType_(steps_xml, "public.utf16-plain-text")

    return {
        "path": path_key(path_obj),
        "kind": "script-step",
        "clipboardType": clipboard_type,
        "fallbackTypes": ["public.utf8-plain-text", "public.utf16-plain-text"],
        "mode": "steps",
    }


def _queue_mark_deleted_prefix(path_prefix: str, note: str) -> int:
    ensure_queue_db()
    with queue_db_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE queue_items
            SET
                status = 'superseded',
                notes = CASE
                    WHEN notes LIKE ?
                    THEN notes
                    ELSE trim(notes || ' ' || ?)
                END,
                updated_at = ?
            WHERE local_path = ? OR local_path LIKE ?
            """,
            (
                f"%{note}%",
                note,
                now_iso(),
                path_prefix,
                f"{path_prefix}/%",
            ),
        )
        connection.commit()
        updated = int(cursor.rowcount or 0)
    if updated:
        sync_markdown_from_db()
    return updated


def delete_workspace_node(path_value: str) -> dict[str, Any]:
    path_obj = safe_resolve_path(path_value)
    if not path_obj.exists():
        raise FileNotFoundError("Selected path does not exist.")

    try:
        path_obj.resolve().relative_to(FILEMAKER_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Delete is only allowed inside FILEMAKER FILES.") from exc

    if path_obj.resolve() == FILEMAKER_ROOT.resolve():
        raise ValueError("Cannot delete FILEMAKER FILES root folder.")

    deleted_key = path_key(path_obj.resolve())
    deleted_type = "folder" if path_obj.is_dir() else "file"
    deleted_file_count = 0
    if path_obj.is_dir():
        deleted_file_count = sum(1 for child in path_obj.rglob("*") if child.is_file())
        shutil.rmtree(path_obj)
    elif path_obj.is_file():
        deleted_file_count = 1
        path_obj.unlink()
    else:
        raise ValueError("Delete target must be a file or folder.")

    state = load_state()
    baselines = state.setdefault("baselines", {})
    keys_to_remove = [
        key for key in list(baselines.keys())
        if key == deleted_key or key.startswith(f"{deleted_key}/")
    ]
    for key in keys_to_remove:
        baselines.pop(key, None)
    save_state(state)

    queue_updated_count = _queue_mark_deleted_prefix(
        path_prefix=deleted_key,
        note="Deleted in FM CodeSpace.",
    )

    return {
        "deletedPath": deleted_key,
        "deletedType": deleted_type,
        "deletedFileCount": deleted_file_count,
        "queueUpdatedCount": queue_updated_count,
    }


def run_inspect(request_data: dict[str, Any]) -> dict[str, Any]:
    payload = make_payload(
        request_data.get("input_path"),
        input_text=request_data.get("input_text"),
        input_name=request_data.get("input_name"),
    )
    fallback_name = request_data.get("name") or None
    summary = importer.inspect_payload(payload, fallback_name=fallback_name)
    return {"summary": summary}


def run_import(request_data: dict[str, Any], preview: bool) -> dict[str, Any]:
    payload = make_payload(
        request_data.get("input_path"),
        input_text=request_data.get("input_text"),
        input_name=request_data.get("input_name"),
    )
    fallback_name = request_data.get("name") or None
    fallback_namespace = request_data.get("fallback_namespace") or importer.FALLBACK_DIR_NAME
    root = safe_resolve_path(request_data.get("root") or ".")

    items = importer.collect_import_items(
        payload.xml_text,
        fallback_name=fallback_name,
        script_format=request_data.get("script_format") or "raw",
        custom_function_format=request_data.get("custom_function_format") or "xml",
        fallback_dir=fallback_namespace,
        filename_style=request_data.get("filename_style") or "pretty-id",
    )
    routed_items = importer.route_items_to_filemaker_layout(
        items=items,
        base_root=root,
        payload_kind=payload.kind,
        explicit_file_namespace=request_data.get("file_namespace") or None,
        fallback_namespace=fallback_namespace,
        entity_folder_mode=request_data.get("entity_folder_mode") or "auto",
    )
    overwrite = as_bool(request_data.get("overwrite"), default=True)
    results = importer.write_items(
        routed_items,
        root=root,
        preview=preview,
        overwrite=overwrite,
    )
    if not preview:
        set_baselines_from_results(results)

    counts = Counter(result.action for result in results)
    path_list = [str(result.path) for result in results[:MAX_RESULT_PATHS]]
    return {
        "mode": "preview" if preview else "import",
        "payload_kind": payload.kind,
        "target_count": len(results),
        "actions": {
            "create": counts.get("create", 0),
            "overwrite": counts.get("overwrite", 0),
            "skip": counts.get("skip", 0),
        },
        "paths": path_list,
        "truncated": len(results) > MAX_RESULT_PATHS,
    }


def run_dump(request_data: dict[str, Any]) -> dict[str, Any]:
    payload = make_payload(
        request_data.get("input_path"),
        input_text=request_data.get("input_text"),
        input_name=request_data.get("input_name"),
    )
    output_path = request_data.get("output_path")
    if output_path:
        path = safe_resolve_path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.xml_text, encoding="utf-8")
        return {"path": str(path), "source": payload.source}

    with tempfile.NamedTemporaryFile(prefix="fm-clipboard-", suffix=".xml", delete=False) as temp_file:
        temp_file.write(payload.xml_text.encode("utf-8"))
        return {"path": temp_file.name, "source": payload.source}


def browser_bootstrap() -> dict[str, Any]:
    return {
        "workspaceRoot": str(WORKSPACE_ROOT),
        "fileMakerRoot": str(FILEMAKER_ROOT),
        "namespaces": list_namespaces(),
        "tracking": tracking_summary(),
    }


def build_html() -> bytes:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FM CodeSpace</title>
  <style>
    :root {
      --bg: #0b1221;
      --panel: #111a2e;
      --line: #2a3659;
      --text: #e7ecff;
      --muted: #9eb0df;
      --accent: #6ea2ff;
      --danger: #ff8c8c;
    }
    body {
      margin: 0;
      padding: 12px;
      background: var(--bg);
      color: var(--text);
      font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .layout {
      max-width: 1800px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 420px minmax(600px, 1fr);
      gap: 12px;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      padding: 10px;
      min-width: 0;
    }
    h1, h2 {
      margin: 0 0 8px;
    }
    h1 { font-size: 18px; }
    h2 { font-size: 14px; color: var(--muted); }
    .muted { color: var(--muted); }
    .error {
      color: var(--danger);
      min-height: 18px;
      margin-bottom: 8px;
      font-weight: 600;
    }
    .tree {
      border: 1px solid var(--line);
      border-radius: 8px;
      max-height: 78vh;
      overflow: auto;
      background: #0a1121;
    }
    .tree-search-row {
      display: flex;
      gap: 6px;
      margin: 0 0 6px;
      align-items: center;
    }
    .tree-search-row .search-input {
      flex: 1 1 auto;
    }
    .tree-search-meta {
      margin: 0 0 8px;
      font-size: 11px;
      color: var(--muted);
      min-height: 16px;
    }
    .tree-empty {
      padding: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .tree-row {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 4px 8px;
      border-bottom: 1px solid #1a2642;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }
    .tree-row:hover { background: #101935; }
    .tree-row.selected { background: #142548; }
    .caret {
      width: 14px;
      text-align: center;
      color: var(--muted);
      flex: 0 0 14px;
    }
    .node-name {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .node-type {
      color: var(--muted);
      font-size: 11px;
      padding-left: 6px;
      flex: 0 0 auto;
    }
    .row-right {
      margin-left: auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
    }
    .trash-btn {
      border: 1px solid #6a2a2a;
      background: #2a1111;
      color: #ffb5b5;
      border-radius: 6px;
      font-size: 11px;
      line-height: 1;
      padding: 2px 5px;
      cursor: pointer;
    }
    .trash-btn:hover { background: #471919; }
    pre {
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #080f1f;
      padding: 10px;
      max-height: 80vh;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .meta {
      margin: 0 0 8px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0a1121;
      font-size: 12px;
    }
    .top-panel {
      max-width: 1800px;
      margin: 0 auto 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .top-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }
    .app-title {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0.2px;
    }
    .control-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .search-input {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0a1121;
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
      width: 100%;
    }
    .lights {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .light {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
      border: 1px solid #1f2c49;
      background: #4b5a80;
      margin-right: 5px;
      vertical-align: middle;
    }
    .light.ok { background: #57d58c; }
    .light.warn { background: #f1c36a; }
    .light.alert { background: #ff8c8c; }
    .light.off { background: #4b5a80; }
    .detect-list {
      border: 1px solid var(--line);
      border-radius: 8px;
      max-height: 150px;
      overflow: auto;
      background: #0a1121;
      display: none;
    }
    .detect-item {
      padding: 6px 8px;
      border-bottom: 1px solid #1a2642;
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    .detect-item:last-child { border-bottom: none; }
    .detect-path {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .detect-meta { color: var(--muted); font-size: 11px; }
    .drop-zone-compact {
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 6px 8px;
      font-size: 12px;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
      text-align: center;
      min-width: 170px;
    }
    .drop-zone-compact.drag {
      border-color: var(--accent);
      color: var(--text);
      background: #0e1933;
    }
    .top-button {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0a1121;
      color: var(--text);
      padding: 7px 10px;
      font: inherit;
      cursor: pointer;
      white-space: nowrap;
    }
    .top-button:hover { background: #122043; }
    .top-button.primary {
      border-color: #2f5eb0;
      background: #15305f;
    }
    .top-button.danger {
      border-color: #b34b4b;
      background: #4a1f1f;
      color: #ffd8d8;
    }
    .top-meta {
      display: flex;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      flex-wrap: wrap;
    }
    .action-output {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0a1121;
      color: var(--muted);
      padding: 8px;
      font-size: 12px;
      max-height: 120px;
      overflow: auto;
      white-space: pre-wrap;
      display: none;
    }
    .preview-actions {
      display: flex;
      gap: 6px;
      margin: 0 0 8px;
    }
    .pending-wrap {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0a1121;
      margin: 0 0 8px;
      overflow: hidden;
    }
    .pending-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      padding: 7px 9px;
      border-bottom: 1px solid #1a2642;
      color: var(--muted);
      font-size: 12px;
    }
    .pending-head-right {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .pending-list {
      max-height: 320px;
      overflow: auto;
    }
    .pending-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 7px 9px;
      border-bottom: 1px solid #1a2642;
    }
    .pending-item:last-child { border-bottom: none; }
    .pending-main {
      min-width: 0;
      flex: 1 1 auto;
    }
    .pending-name {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pending-path {
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pending-status {
      color: var(--muted);
      font-size: 11px;
      margin-top: 2px;
    }
    .pending-actions {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
    }
    .pending-empty {
      padding: 8px 9px;
      color: var(--muted);
      font-size: 12px;
    }
    .mini-btn {
      padding: 4px 7px;
      font-size: 11px;
    }
    .dismiss-btn {
      border: 1px solid #6a2a2a;
      background: #2a1111;
      color: #ffb5b5;
      border-radius: 6px;
      padding: 3px 7px;
      font-size: 11px;
      line-height: 1;
      cursor: pointer;
    }
    .dismiss-btn:hover { background: #471919; }
    .mini-danger {
      border-color: #6a2a2a;
      background: #2a1111;
      color: #ffb5b5;
    }
    .mini-danger:hover { background: #471919; }
    button {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0a1121;
      color: var(--text);
      padding: 6px 10px;
      font: inherit;
      cursor: pointer;
    }
    button:hover { background: #122043; }
  </style>
</head>
<body>
  <div class="top-panel">
    <div class="top-row">
      <div class="app-title">FM CodeSpace</div>
      <div class="lights">
        <span><span id="light_clipboard" class="light off"></span>Source Found</span>
        <span><span id="light_parse" class="light off"></span>Parsed</span>
        <span><span id="light_compare" class="light off"></span>Update Ready</span>
      </div>
    </div>
    <div class="top-row">
      <div class="muted" id="source_summary">Step 1: Copy from FileMaker or drop a DDR/XML file.</div>
      <div class="top-meta">
        <span id="queue_summary">Scripts to copy back: 0</span>
        <span id="track_summary">Watcher: idle</span>
      </div>
    </div>
    <div class="control-row">
      <input id="file_namespace" class="search-input" placeholder="File namespace (optional when using dropped DDR)" style="max-width:280px;" />
      <input id="import_file" type="file" style="display:none;" />
      <div id="drop_zone" class="drop-zone-compact">Add / Update DDR</div>
      <button id="update_local_btn" class="top-button primary" onclick="updateLocal()" disabled>Update Local</button>
      <button class="top-button" onclick="clearImportFile()">Use Clipboard</button>
    </div>
    <div class="muted" id="local_update_status">Step 2: Click Update Local (always overwrites matching local files).</div>
    <div class="muted" id="workflow_hint">Step 3: AI edits scripts. Step 4: copy back from "Recent scripts edited".</div>
    <div class="pending-wrap">
      <div class="pending-head">
        <span>Recent scripts edited</span>
        <div class="pending-head-right">
          <span id="pending_script_count">0</span>
          <button id="clear_recent_btn" class="top-button mini-btn mini-danger" type="button" onclick="clearRecentScripts()">Clear All</button>
        </div>
      </div>
      <div id="pending_scripts" class="pending-list"></div>
    </div>
    <div class="detect-list" id="detect_list"></div>
    <pre class="action-output" id="action_output"></pre>
  </div>

  <div class="layout">
    <div class="panel">
      <h2 style="margin-top:10px;">Files Tree</h2>
      <div class="tree-search-row">
        <input id="tree_search" class="search-input" placeholder="Search file names..." />
        <button id="tree_search_clear" class="top-button mini-btn" type="button">Clear</button>
      </div>
      <div id="tree_search_meta" class="tree-search-meta"></div>
      <div class="tree" id="tree" tabindex="0"></div>
    </div>

    <div class="panel">
      <h2>Preview</h2>
      <div class="error" id="error"></div>
      <div class="preview-actions">
        <button onclick="copyText()">Copy as Text</button>
        <button onclick="copyAsFm()">Copy as FM</button>
        <button onclick="copyAsFmSteps()">Copy as FM Steps</button>
        <button onclick="markSelected('edited')">Mark Edited</button>
        <button onclick="markSelected('ready to paste back')">Mark Ready</button>
        <button onclick="markSelected('pasted back')">Mark Pasted</button>
      </div>
      <div class="meta" id="meta">Select a file in the tree.</div>
      <pre id="preview">Select a file in the tree.</pre>
    </div>
  </div>

  <script>
    const ROOT_KEY = '__root__';
    const RECENT_VISIBLE_LIMIT = 30;
    const DISMISSED_RECENTS_STORAGE_KEY = 'fm_codespace_dismissed_recents_v1';
    const state = {
      selectedPath: null,
      selectedContent: '',
      pendingScripts: [],
      pendingScriptCount: 0,
      syncInFlight: false,
      scriptDisplayOrder: new Map(),
      scriptOrderCounter: 0,
      dismissedRecentKeys: new Set(),
      visibleNodes: [],
      importFile: null,
      treeSearchQuery: '',
      treeSearchResults: [],
      treeSearchRequestId: 0,
      children: new Map(),
      expanded: new Set([ROOT_KEY])
    };
    let sourceTimer = null;
    let treeSearchTimer = null;

    function byId(id) { return document.getElementById(id); }
    function keyFor(path) { return path || ROOT_KEY; }

    function buildSearchTreeChildren(results) {
      const childrenMaps = new Map();

      function parentChildrenMap(parentPath) {
        const parentKey = keyFor(parentPath);
        if (!childrenMaps.has(parentKey)) {
          childrenMaps.set(parentKey, new Map());
        }
        return childrenMaps.get(parentKey);
      }

      (results || []).forEach((item) => {
        const fullPath = String(item && item.path || '');
        const parts = fullPath.split('/').filter(Boolean);
        if (parts.length < 3 || parts[0] !== 'FILEMAKER FILES') {
          return;
        }

        let parentPath = null;
        const stack = ['FILEMAKER FILES'];
        for (let idx = 1; idx < parts.length; idx += 1) {
          const segment = parts[idx];
          stack.push(segment);
          const nodePath = stack.join('/');
          const isFile = idx === parts.length - 1;
          const childMap = parentChildrenMap(parentPath);
          if (!childMap.has(nodePath)) {
            childMap.set(nodePath, {
              name: segment,
              path: nodePath,
              type: isFile ? 'file' : 'folder',
              hasChildren: !isFile
            });
          }
          parentPath = nodePath;
        }
      });

      const output = new Map();
      childrenMaps.forEach((nodeMap, parentKey) => {
        const items = Array.from(nodeMap.values()).sort((left, right) => {
          const typeOrder = left.type === right.type ? 0 : (left.type === 'folder' ? -1 : 1);
          if (typeOrder !== 0) {
            return typeOrder;
          }
          return String(left.name || '').localeCompare(
            String(right.name || ''),
            undefined,
            { sensitivity: 'base' }
          );
        });
        output.set(parentKey, items);
      });
      return output;
    }

    function updateTreeSearchMeta() {
      const meta = byId('tree_search_meta');
      if (!meta) {
        return;
      }
      const query = String(state.treeSearchQuery || '').trim();
      if (!query) {
        meta.textContent = '';
        return;
      }
      const count = state.treeSearchResults.length;
      meta.textContent = count
        ? (String(count) + ' file match' + (count === 1 ? '' : 'es'))
        : 'No file matches';
    }

    async function runTreeSearchQuery(query) {
      const trimmed = String(query || '').trim();
      state.treeSearchQuery = trimmed;
      if (!trimmed) {
        state.treeSearchResults = [];
        updateTreeSearchMeta();
        renderTree();
        return;
      }

      const requestId = state.treeSearchRequestId + 1;
      state.treeSearchRequestId = requestId;
      try {
        const response = await api('/api/search-files', { query: trimmed });
        if (requestId !== state.treeSearchRequestId) {
          return;
        }
        const lowered = trimmed.toLowerCase();
        state.treeSearchResults = (response.results || []).filter((item) => (
          String(item && item.name || '').toLowerCase().includes(lowered)
        ));
        updateTreeSearchMeta();
        renderTree();
      } catch (error) {
        if (requestId !== state.treeSearchRequestId) {
          return;
        }
        state.treeSearchResults = [];
        updateTreeSearchMeta();
        setError(String(error));
        renderTree();
      }
    }

    function setupTreeSearch() {
      const input = byId('tree_search');
      const clearButton = byId('tree_search_clear');
      if (!input || !clearButton) {
        return;
      }

      input.addEventListener('input', () => {
        const query = String(input.value || '');
        if (treeSearchTimer) {
          clearTimeout(treeSearchTimer);
        }
        treeSearchTimer = setTimeout(() => {
          runTreeSearchQuery(query);
        }, 120);
      });

      clearButton.addEventListener('click', () => {
        if (treeSearchTimer) {
          clearTimeout(treeSearchTimer);
          treeSearchTimer = null;
        }
        input.value = '';
        runTreeSearchQuery('');
        input.focus();
      });
    }

    async function api(endpoint, payload) {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error || 'Request failed');
      }
      return body;
    }

    function setError(message) {
      byId('error').textContent = message || '';
    }

    function showActionOutput(value) {
      const output = byId('action_output');
      // Keep debug output hidden in normal workflow UI.
      output.style.display = 'none';
      output.textContent = '';
    }

    function localPathKey(value) {
      return String(value || '').trim().replace(/\\\\/g, '/').toLowerCase();
    }

    function makeDismissKey(row) {
      return localPathKey(row && row.localPath);
    }

    function loadDismissedRecents() {
      try {
        const raw = window.localStorage.getItem(DISMISSED_RECENTS_STORAGE_KEY);
        if (!raw) {
          state.dismissedRecentKeys = new Set();
          return;
        }
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          state.dismissedRecentKeys = new Set(parsed.map((item) => String(item)));
          return;
        }
      } catch (error) {
        // Ignore malformed local storage values.
      }
      state.dismissedRecentKeys = new Set();
    }

    function saveDismissedRecents() {
      try {
        window.localStorage.setItem(
          DISMISSED_RECENTS_STORAGE_KEY,
          JSON.stringify(Array.from(state.dismissedRecentKeys))
        );
      } catch (error) {
        // Ignore storage write failures.
      }
    }

    function isDismissedRecentRow(row) {
      return state.dismissedRecentKeys.has(makeDismissKey(row));
    }

    async function dismissRecentRow(row) {
      const key = makeDismissKey(row);
      if (!key || key === '::') {
        return;
      }
      state.dismissedRecentKeys.add(key);
      saveDismissedRecents();
      const targetPathKey = localPathKey(row && row.localPath);
      state.pendingScripts = (state.pendingScripts || []).filter((item) => {
        const samePath = localPathKey(item && item.localPath) === targetPathKey;
        return !samePath;
      });
      renderPendingScripts();
      await refreshQueueSummary();
    }

    async function clearRecentScripts() {
      const shown = (state.pendingScripts || []).length;
      const confirmed = window.confirm(
        'Clear all script rows from the Recent scripts edited list?\\n\\n' +
        'This removes script entries from the local queue database and updates PASTE_BACK_QUEUE.md.\\n\\n' +
        'Rows shown right now: ' + String(shown)
      );
      if (!confirmed) {
        return;
      }

      setError('');
      try {
        const response = await api('/api/queue/clear-scripts', {});
        state.dismissedRecentKeys = new Set();
        saveDismissedRecents();
        await refreshQueueSummary();
        const deleted = Number(response.deleted || 0);
        byId('local_update_status').textContent =
          'Cleared ' + String(deleted) + ' script queue row(s) from recents.';
      } catch (error) {
        setError(String(error));
      }
    }

    function clearImportFile() {
      state.importFile = null;
      byId('import_file').value = '';
      byId('drop_zone').textContent = 'Add / Update DDR';
      refreshSourceDetection();
    }

    function setupDropZone() {
      const zone = byId('drop_zone');
      const fileInput = byId('import_file');

      zone.addEventListener('click', () => fileInput.click());
      zone.addEventListener('dragover', (event) => {
        event.preventDefault();
        zone.classList.add('drag');
      });
      zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
      zone.addEventListener('drop', (event) => {
        event.preventDefault();
        zone.classList.remove('drag');
        const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
        if (file) {
          state.importFile = file;
          zone.textContent = file.name;
          refreshSourceDetection();
        }
      });
      fileInput.addEventListener('change', () => {
        const file = fileInput.files && fileInput.files[0];
        if (file) {
          state.importFile = file;
          zone.textContent = file.name;
          refreshSourceDetection();
        }
      });
    }

    async function importRequestData() {
      const fileNamespace = byId('file_namespace').value.trim();
      if (state.importFile) {
        const text = await state.importFile.text();
        return {
          input_path: null,
          input_text: text,
          input_name: state.importFile.name,
          root: '.',
          file_namespace: fileNamespace || null,
          overwrite: true
        };
      }

      return {
        input_path: null,
        input_text: null,
        input_name: null,
        root: '.',
        file_namespace: null,
        overwrite: true
      };
    }

    function normalizeWorkspacePath(rawPath) {
      const value = String(rawPath || '').trim();
      if (!value) {
        return '';
      }
      if (value.startsWith('FILEMAKER FILES/')) {
        return value;
      }
      const marker = '/FILEMAKER FILES/';
      const markerIndex = value.indexOf(marker);
      if (markerIndex >= 0) {
        return value.slice(markerIndex + 1);
      }
      return '';
    }

    async function updateLocal() {
      setError('');
      showActionOutput('');
      try {
        byId('local_update_status').textContent = 'Updating local files...';
        const body = await api('/api/import', await importRequestData());
        const actions = body.actions || {};
        const created = Number(actions.create || 0);
        const overwritten = Number(actions.overwrite || 0);
        const changed = created + overwritten;
        const kind = String(body.payload_kind || 'items');
        byId('source_summary').textContent =
          'Step 2 complete: imported ' + String(changed) + ' ' + kind + ' item(s) locally.';
        byId('local_update_status').textContent =
          'Local updated: ' + String(changed) +
          ' files (' + String(created) + ' new, ' + String(overwritten) + ' overwritten). Next: edit scripts, then copy back.';
        state.children.clear();
        await loadChildren(null);
        renderTree();
        const importedFirstPath = normalizeWorkspacePath((body.paths || [])[0]);
        if (importedFirstPath) {
          await revealPath(importedFirstPath, { forceReload: true });
          await selectFile(importedFirstPath);
        }
        await refreshQueueSummary();
        await refreshSourceDetection();
      } catch (error) {
        setError(String(error));
        byId('local_update_status').textContent = 'Update failed. Check selected source and namespace.';
      }
    }

    async function refreshQueueSummary() {
      const pendingList = byId('pending_scripts');
      const pendingScrollTop = pendingList ? pendingList.scrollTop : null;
      try {
        const response = await api('/api/queue/list', {});
        const rows = response.rows || [];
        const pendingScripts = rows.filter((row) => {
          const status = String(row.status || '').toLowerCase();
          const path = String(row.localPath || '');
          const isPending =
            status === 'edited' ||
            status === 'ready to paste back' ||
            status === 'queued';
          return isPending && path.includes('/scripts/');
        });
        const recentCopiedScripts = rows
          .filter((row) => {
            const status = String(row.status || '').toLowerCase();
            const path = String(row.localPath || '');
            return status === 'pasted back' && path.includes('/scripts/');
          })
          .sort((left, right) => String(right.updatedAt || '').localeCompare(String(left.updatedAt || '')));
        const mergedByPath = new Map();
        [...pendingScripts, ...recentCopiedScripts].forEach((row) => {
          const key = localPathKey(row.localPath);
          if (!mergedByPath.has(key)) {
            mergedByPath.set(key, row);
          }
        });
        const mergedRowsAll = Array.from(mergedByPath.values());
        const activeDismissKeys = new Set(mergedRowsAll.map((row) => makeDismissKey(row)));
        let dismissedChanged = false;
        for (const key of Array.from(state.dismissedRecentKeys)) {
          if (!activeDismissKeys.has(key)) {
            state.dismissedRecentKeys.delete(key);
            dismissedChanged = true;
          }
        }
        if (dismissedChanged) {
          saveDismissedRecents();
        }
        const mergedRows = mergedRowsAll
          .filter((row) => !isDismissedRecentRow(row))
          .slice(0, RECENT_VISIBLE_LIMIT);
        const presentKeys = new Set(mergedRows.map((row) => localPathKey(row.localPath)));
        for (const key of Array.from(state.scriptDisplayOrder.keys())) {
          if (!presentKeys.has(key)) {
            state.scriptDisplayOrder.delete(key);
          }
        }
        mergedRows.forEach((row) => {
          const key = localPathKey(row.localPath);
          if (!key) {
            return;
          }
          if (!state.scriptDisplayOrder.has(key)) {
            state.scriptDisplayOrder.set(key, state.scriptOrderCounter);
            state.scriptOrderCounter += 1;
          }
        });
        state.pendingScripts = scriptRowsByDisplayOrder(mergedRows);
        state.pendingScriptCount = pendingScripts.length;
        byId('queue_summary').textContent =
          'Scripts to copy back: ' + String(pendingScripts.length) +
          ' (queue total: ' + String(rows.length) + ')';
        renderPendingScripts();
      } catch (error) {
        byId('queue_summary').textContent = 'Scripts to copy back: ?';
        state.pendingScripts = [];
        state.pendingScriptCount = 0;
        renderPendingScripts();
      } finally {
        const refreshedPendingList = byId('pending_scripts');
        if (refreshedPendingList && pendingScrollTop !== null) {
          refreshedPendingList.scrollTop = pendingScrollTop;
          requestAnimationFrame(() => {
            refreshedPendingList.scrollTop = pendingScrollTop;
          });
        }
      }
    }

    function scriptRowsByDisplayOrder(rows) {
      return [...rows].sort((left, right) => {
        const leftKey = localPathKey(left.localPath);
        const rightKey = localPathKey(right.localPath);
        const leftOrder = state.scriptDisplayOrder.has(leftKey)
          ? Number(state.scriptDisplayOrder.get(leftKey))
          : Number.MAX_SAFE_INTEGER;
        const rightOrder = state.scriptDisplayOrder.has(rightKey)
          ? Number(state.scriptDisplayOrder.get(rightKey))
          : Number.MAX_SAFE_INTEGER;
        if (leftOrder !== rightOrder) {
          return leftOrder - rightOrder;
        }
        return String(left.fileMakerObject || leftKey).localeCompare(String(right.fileMakerObject || rightKey));
      });
    }

    async function setQueueStatus(path, status, note) {
      return api('/api/queue/set-status', {
        path,
        status,
        notes: note || 'Updated from FM CodeSpace.'
      });
    }

    function captureViewportState() {
      const pendingList = byId('pending_scripts');
      return {
        pageX: window.scrollX || 0,
        pageY: window.scrollY || 0,
        pendingScrollTop: pendingList ? pendingList.scrollTop : null
      };
    }

    function restoreViewportState(snapshot) {
      if (!snapshot) {
        return;
      }
      window.scrollTo(snapshot.pageX || 0, snapshot.pageY || 0);
      const pendingList = byId('pending_scripts');
      if (pendingList && snapshot.pendingScrollTop !== null) {
        pendingList.scrollTop = snapshot.pendingScrollTop;
      }
    }

    async function withStableViewport(action) {
      const snapshot = captureViewportState();
      try {
        await action();
      } finally {
        restoreViewportState(snapshot);
        requestAnimationFrame(() => restoreViewportState(snapshot));
      }
    }

    async function markCopiedAfterCopy(path, modeLabel) {
      const targetPath = String(path || '');
      if (!targetPath.includes('/scripts/')) {
        return;
      }
      await setQueueStatus(
        targetPath,
        'pasted back',
        'Marked pasted back after ' + modeLabel + ' copy in FM CodeSpace.'
      );
      await refreshQueueSummary();
      if (state.selectedPath === targetPath) {
        await selectFile(targetPath);
      }
    }

    function renderScriptList(targetId, countId, rows, emptyText) {
      const list = byId(targetId);
      const count = byId(countId);
      const sortedRows = scriptRowsByDisplayOrder(rows || []);
      count.textContent = String(sortedRows.length) + ' shown';
      list.innerHTML = '';

      if (!sortedRows.length) {
        const empty = document.createElement('div');
        empty.className = 'pending-empty';
        empty.textContent = emptyText;
        list.appendChild(empty);
        return;
      }

      sortedRows.forEach((row) => {
        const item = document.createElement('div');
        item.className = 'pending-item';

        const main = document.createElement('div');
        main.className = 'pending-main';

        const name = document.createElement('div');
        name.className = 'pending-name';
        const nsLabel = String(row.fileNamespace || 'unknown namespace');
        const objectLabel = String(row.fileMakerObject || row.localPath || 'Script');
        name.textContent = '[' + nsLabel + '] ' + objectLabel;
        main.appendChild(name);

        const path = document.createElement('div');
        path.className = 'pending-path';
        path.textContent = String(row.localPath || '');
        main.appendChild(path);

        const status = document.createElement('div');
        status.className = 'pending-status';
        status.textContent = 'Status: ' + String(row.status || 'unknown');
        main.appendChild(status);

        const identity = document.createElement('div');
        identity.className = 'pending-status';
        const ns = String(row.fileNamespace || 'unknown namespace');
        const id = String(row.fileMakerId || '');
        identity.textContent = 'Namespace: ' + ns + (id ? (' | ID: ' + id) : '');
        main.appendChild(identity);

        const actions = document.createElement('div');
        actions.className = 'pending-actions';

        const openBtn = document.createElement('button');
        openBtn.className = 'top-button mini-btn';
        openBtn.type = 'button';
        openBtn.textContent = 'Open';
        openBtn.onclick = async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await withStableViewport(async () => {
            await revealPath(String(row.localPath || ''), { forceReload: true });
            await selectFile(String(row.localPath || ''));
          });
        };
        actions.appendChild(openBtn);

        const copyTextBtn = document.createElement('button');
        copyTextBtn.className = 'top-button mini-btn';
        copyTextBtn.type = 'button';
        copyTextBtn.textContent = 'Copy as Text';
        copyTextBtn.onclick = async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await withStableViewport(async () => {
            await revealPath(String(row.localPath || ''), { forceReload: true });
            await selectFile(String(row.localPath || ''));
            await copyText();
          });
        };
        actions.appendChild(copyTextBtn);

        const copyFmBtn = document.createElement('button');
        copyFmBtn.className = 'top-button mini-btn';
        copyFmBtn.type = 'button';
        copyFmBtn.textContent = 'Copy as FM';
        copyFmBtn.onclick = async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await withStableViewport(async () => {
            await revealPath(String(row.localPath || ''), { forceReload: true });
            await selectFile(String(row.localPath || ''));
            await copyAsFm();
          });
        };
        actions.appendChild(copyFmBtn);

        const copyFmStepsBtn = document.createElement('button');
        copyFmStepsBtn.className = 'top-button mini-btn';
        copyFmStepsBtn.type = 'button';
        copyFmStepsBtn.textContent = 'Copy as FM Steps';
        copyFmStepsBtn.onclick = async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await withStableViewport(async () => {
            await revealPath(String(row.localPath || ''), { forceReload: true });
            await selectFile(String(row.localPath || ''));
            await copyAsFmSteps();
          });
        };
        actions.appendChild(copyFmStepsBtn);

        if (String(row.status || '').toLowerCase() === 'pasted back') {
          const readyBtn = document.createElement('button');
          readyBtn.className = 'top-button mini-btn';
          readyBtn.type = 'button';
          readyBtn.textContent = 'Mark Ready';
          readyBtn.onclick = async (event) => {
            event.preventDefault();
            event.stopPropagation();
            await withStableViewport(async () => {
              try {
                const response = await setQueueStatus(
                  String(row.localPath || ''),
                  'ready to paste back',
                  'Moved back to ready from recently copied list.'
                );
                showActionOutput(JSON.stringify(response, null, 2));
                await refreshQueueSummary();
                await revealPath(String(row.localPath || ''), { forceReload: true });
                await selectFile(String(row.localPath || ''));
              } catch (error) {
                setError(String(error));
              }
            });
          };
          actions.appendChild(readyBtn);
        }

        const dismissBtn = document.createElement('button');
        dismissBtn.className = 'dismiss-btn';
        dismissBtn.type = 'button';
        dismissBtn.textContent = 'x';
        dismissBtn.title = 'Hide from recent list';
        dismissBtn.onclick = async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await withStableViewport(async () => {
            await dismissRecentRow(row);
          });
        };
        actions.appendChild(dismissBtn);

        item.appendChild(main);
        item.appendChild(actions);
        list.appendChild(item);
      });
    }

    function renderPendingScripts() {
      renderScriptList(
        'pending_scripts',
        'pending_script_count',
        state.pendingScripts || [],
        'No scripts currently in pending/recent copy list.'
      );
    }

    async function autoSyncEditsInBackground() {
      if (state.syncInFlight) {
        return;
      }
      state.syncInFlight = true;
      try {
        const body = await api('/api/sync-edits', {});
        byId('track_summary').textContent = 'Watcher edits found: ' + String(body.trackedEdits || 0);
        await refreshQueueSummary();
      } catch (error) {
        // Keep UI responsive; background sync errors are non-fatal.
      } finally {
        state.syncInFlight = false;
      }
    }

    async function markSelected(status) {
      if (!state.selectedPath) {
        setError('Select a file first.');
        return;
      }
      try {
        const response = await setQueueStatus(state.selectedPath, status, 'Updated from FM CodeSpace.');
        showActionOutput(JSON.stringify(response, null, 2));
        await refreshQueueSummary();
        await selectFile(state.selectedPath);
      } catch (error) {
        setError(String(error));
      }
    }

    async function deleteNode(path, nodeType, nodeName) {
      const targetType = nodeType === 'folder' ? 'folder' : 'file';
      const message = targetType === 'folder'
        ? ('Delete folder and all contained files?\\n\\n' + path)
        : ('Delete file?\\n\\n' + path);
      const confirmed = window.confirm(message);
      if (!confirmed) {
        return;
      }

      try {
        const response = await api('/api/delete-node', { path });
        showActionOutput(JSON.stringify(response, null, 2));

        if (state.selectedPath && (state.selectedPath === path || state.selectedPath.startsWith(path + '/'))) {
          state.selectedPath = null;
          state.selectedContent = '';
          byId('meta').textContent = 'Select a file in the tree.';
          byId('preview').textContent = 'Select a file in the tree.';
        }

        state.children.clear();
        state.expanded = new Set([ROOT_KEY]);
        await loadChildren(null);
        renderTree();
        await refreshQueueSummary();
      } catch (error) {
        setError(String(error));
      }
    }

    function setLight(id, level) {
      byId(id).className = 'light ' + (level || 'off');
    }

    function describeDetectedKind(kind, topLevelTags) {
      const tags = topLevelTags || {};
      if (kind === 'group') {
        if (Number(tags.Script || 0) > 0 || Number(tags.Step || 0) > 0) {
          return 'folder of scripts';
        }
        return 'folder of FileMaker objects';
      }
      if (kind === 'script') return 'script object(s)';
      if (kind === 'script-step') return 'script step snippet(s)';
      if (kind === 'custom-function') return 'custom function(s)';
      if (kind === 'table') return 'table definition(s)';
      if (kind === 'field') return 'field definition(s)';
      if (kind === 'value-list') return 'value list(s)';
      if (kind === 'layout-object-fmp12' || kind === 'layout-object-fp7') return 'layout object(s)';
      return 'FileMaker object(s)';
    }

    function objectNameFromDetectedPath(pathValue) {
      const raw = String(pathValue || '');
      const fileName = raw.split('/').pop() || raw;
      const withoutExt = fileName.replace(/\.xml$/i, '');
      return withoutExt.replace(/__fm[a-z]+-\d+$/i, '');
    }

    function folderNameFromDetectedPath(pathValue) {
      const raw = String(pathValue || '');
      const parts = raw.split('/');
      if (parts.length <= 1) {
        return '';
      }
      return parts.slice(0, -1).join('/');
    }

    function renderDetectedPaths(summary) {
      const container = byId('detect_list');
      const rows = Array.isArray(summary && summary.preview_paths) ? summary.preview_paths : [];
      const itemCount = Number((summary && summary.item_count) || 0);
      const kind = String((summary && summary.kind) || 'unknown');
      const kindLabel = describeDetectedKind(kind, (summary && summary.top_level_tags) || {});
      const itemLabel = (kind === 'script' || kind === 'script-step' || kind === 'group') ? 'Script' : 'Item';
      container.innerHTML = '';
      if (!rows.length && itemCount <= 0) {
        container.style.display = 'none';
        return;
      }
      container.style.display = 'block';

      const header = document.createElement('div');
      header.className = 'detect-item';
      header.innerHTML =
        '<div class="detect-path">Detected on source: ' + kindLabel + '</div>' +
        '<div class="detect-meta">' + String(itemCount) + ' item(s)</div>';
      container.appendChild(header);

      rows.slice(0, 12).forEach((itemPath) => {
        const objectName = objectNameFromDetectedPath(itemPath);
        const folderName = folderNameFromDetectedPath(itemPath);
        const row = document.createElement('div');
        row.className = 'detect-item';
        row.innerHTML =
          '<div class="detect-path">' + itemLabel + ': ' + objectName + '</div>' +
          '<div class="detect-meta">' + (folderName ? ('Folder: ' + folderName) : 'Detected') + '</div>';
        container.appendChild(row);
      });
      if (rows.length > 12) {
        const tail = document.createElement('div');
        tail.className = 'detect-item';
        tail.innerHTML =
          '<div class="detect-path muted">+' + String(rows.length - 12) + ' more detected items</div>' +
          '<div class="detect-meta">truncated</div>';
        container.appendChild(tail);
      }
    }

    async function revealPath(path, options = {}) {
      const forceReload = Boolean(options && options.forceReload);
      const parts = String(path || '').split('/');
      if (parts.length < 2 || parts[0] !== 'FILEMAKER FILES') {
        return;
      }

      let currentPath = null;
      if (forceReload || !state.children.has(ROOT_KEY)) {
        await loadChildren(null);
      }

      for (let idx = 1; idx < parts.length - 1; idx += 1) {
        currentPath = currentPath ? (currentPath + '/' + parts[idx]) : ('FILEMAKER FILES/' + parts[idx]);
        const currentKey = keyFor(currentPath);
        state.expanded.add(currentKey);
        if (forceReload || !state.children.has(currentKey)) {
          await loadChildren(currentPath);
        }
      }
      renderTree();
    }

    function focusSelectedTreeRow() {
      const tree = byId('tree');
      const selected = tree.querySelector('.tree-row.selected');
      if (!selected) {
        return;
      }
      // Keep scroll adjustments inside the tree panel only.
      const rowTop = selected.offsetTop;
      const rowBottom = rowTop + selected.offsetHeight;
      const viewTop = tree.scrollTop;
      const viewBottom = viewTop + tree.clientHeight;
      const margin = 8;

      if (rowTop < viewTop) {
        tree.scrollTop = Math.max(0, rowTop - margin);
        return;
      }
      if (rowBottom > viewBottom) {
        tree.scrollTop = Math.max(0, rowBottom - tree.clientHeight + margin);
      }
    }

    async function refreshSourceDetection() {
      try {
        const requestData = await importRequestData();
        const inspect = await api('/api/inspect', requestData);
        const summary = inspect.summary || {};
        const itemCount = Number(summary.item_count || 0);
        const kind = String(summary.kind || 'unknown');
        const kindLabel = describeDetectedKind(kind, summary.top_level_tags || {});
        const source = state.importFile
          ? ('file ' + String(state.importFile.name || 'upload'))
          : 'clipboard';

        setLight('light_clipboard', 'ok');
        setLight('light_parse', itemCount > 0 ? 'ok' : 'warn');
        setLight('light_compare', itemCount > 0 ? 'ok' : 'off');
        byId('source_summary').textContent =
          'Step 1 complete: source looks like ' + kindLabel + ' from ' + source + ' (' + String(itemCount) + ' item(s)).';
        byId('local_update_status').textContent = itemCount > 0
          ? 'Step 2: Click Update Local to overwrite matching local files.'
          : 'No importable items detected yet.';
        byId('update_local_btn').textContent = itemCount > 0
          ? ('Update Local (' + String(itemCount) + ')')
          : 'Update Local';
        byId('update_local_btn').disabled = itemCount <= 0;
        renderDetectedPaths(summary);
      } catch (error) {
        setLight('light_clipboard', 'off');
        setLight('light_parse', 'off');
        setLight('light_compare', 'off');
        const message = String(error || '');
        if (message.includes('No supported FileMaker clipboard payload was found')) {
          byId('source_summary').textContent = 'Step 1: waiting for FileMaker clipboard payload or dropped DDR/XML file.';
        } else {
          byId('source_summary').textContent = 'Step 1: source monitor unavailable.';
        }
        byId('local_update_status').textContent = 'Step 2: Update Local is disabled until a valid source is detected.';
        byId('update_local_btn').textContent = 'Update Local';
        byId('update_local_btn').disabled = true;
        renderDetectedPaths({});
      }
    }

    async function loadChildren(path) {
      const response = await api('/api/tree', { path: path || null });
      state.children.set(keyFor(path), response.children || []);
    }

    async function toggleFolder(path) {
      const key = keyFor(path);
      if (state.expanded.has(key)) {
        state.expanded.delete(key);
        renderTree();
        return;
      }
      state.expanded.add(key);
      if (!state.children.has(key)) {
        await loadChildren(path);
      }
      renderTree();
    }

    async function selectFile(path) {
      state.selectedPath = path;
      state.selectedContent = '';
      setError('');
      try {
        const detail = await api('/api/file-detail', { path });
        byId('meta').textContent =
          detail.path + ' (' + detail.sizeBytes + ' bytes)' +
          ' | local: ' + (detail.localStatus || 'unknown') +
          ' | queue: ' + (detail.queueStatus || 'none') +
          ' | badge: ' + (detail.badge || 'unknown');
        byId('preview').textContent = detail.content || '';
        state.selectedContent = detail.content || '';
      } catch (error) {
        setError(String(error));
      }
      renderTree();
      focusSelectedTreeRow();
    }

    async function copyText() {
      if (!state.selectedContent) {
        setError('No file preview selected to copy.');
        return;
      }
      try {
        await navigator.clipboard.writeText(state.selectedContent);
        setError('');
        await markCopiedAfterCopy(state.selectedPath, 'text');
      } catch (error) {
        setError('Copy failed: ' + String(error));
      }
    }

    async function copyAsFm() {
      if (!state.selectedPath) {
        setError('Select a file first.');
        return;
      }
      setError('');
      try {
        const response = await api('/api/copy-as-fm', { path: state.selectedPath });
        showActionOutput(JSON.stringify(response, null, 2));
        await markCopiedAfterCopy(state.selectedPath, 'FM');
      } catch (error) {
        setError(String(error));
      }
    }

    async function copyAsFmSteps() {
      if (!state.selectedPath) {
        setError('Select a file first.');
        return;
      }
      setError('');
      try {
        const response = await api('/api/copy-as-fm-steps', { path: state.selectedPath });
        showActionOutput(JSON.stringify(response, null, 2));
        await markCopiedAfterCopy(state.selectedPath, 'FM steps');
      } catch (error) {
        setError(String(error));
      }
    }

    function selectedNodeIndex() {
      return state.visibleNodes.findIndex((node) => node.path === state.selectedPath);
    }

    async function moveSelection(delta) {
      if (!state.visibleNodes.length) {
        return;
      }
      const currentIndex = selectedNodeIndex();
      let nextIndex = currentIndex;
      if (nextIndex === -1) {
        nextIndex = 0;
      } else {
        nextIndex = Math.max(0, Math.min(state.visibleNodes.length - 1, nextIndex + delta));
      }
      const node = state.visibleNodes[nextIndex];
      state.selectedPath = node.path;
      if (node.type === 'file') {
        await selectFile(node.path);
      } else {
        renderTree();
      }
    }

    async function handleTreeKeyDown(event) {
      if (!state.visibleNodes.length) {
        return;
      }
      const searchActive = Boolean(String(state.treeSearchQuery || '').trim());
      const index = selectedNodeIndex();
      const node = index >= 0 ? state.visibleNodes[index] : null;

      if (event.key === 'ArrowDown') {
        event.preventDefault();
        await moveSelection(1);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        await moveSelection(-1);
        return;
      }
      if (!node) {
        return;
      }
      if (event.key === 'ArrowRight' && node.type === 'folder') {
        event.preventDefault();
        if (searchActive) {
          return;
        }
        if (!state.expanded.has(keyFor(node.path))) {
          await toggleFolder(node.path);
        }
        return;
      }
      if (event.key === 'ArrowLeft' && node.type === 'folder') {
        event.preventDefault();
        if (searchActive) {
          return;
        }
        if (state.expanded.has(keyFor(node.path))) {
          await toggleFolder(node.path);
        }
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        if (node.type === 'folder') {
          if (searchActive) {
            return;
          }
          await toggleFolder(node.path);
        } else {
          await selectFile(node.path);
        }
      }
    }

    function renderTree() {
      const tree = byId('tree');
      tree.innerHTML = '';
      state.visibleNodes = [];
      const searchActive = Boolean(String(state.treeSearchQuery || '').trim());
      const searchChildren = searchActive ? buildSearchTreeChildren(state.treeSearchResults) : null;

      function renderLevel(parentElement, path, depth) {
        const nodes = searchActive
          ? (searchChildren.get(keyFor(path)) || [])
          : (state.children.get(keyFor(path)) || []);
        nodes.forEach((node) => {
          state.visibleNodes.push(node);
          const row = document.createElement('div');
          row.className = 'tree-row';
          row.dataset.path = node.path;
          if (state.selectedPath === node.path) {
            row.classList.add('selected');
          }
          row.style.paddingLeft = String(8 + (depth * 14)) + 'px';

          const caret = document.createElement('div');
          caret.className = 'caret';
          const isFolder = node.type === 'folder';
          const isExpanded = searchActive ? true : state.expanded.has(keyFor(node.path));
          caret.textContent = isFolder ? (isExpanded ? '▾' : '▸') : '';

          const name = document.createElement('div');
          name.className = 'node-name';
          name.textContent = node.name;

          const type = document.createElement('div');
          type.className = 'node-type';
          if (depth === 0 && isFolder) {
            type.textContent = 'file namespace';
          } else if (depth === 1 && isFolder) {
            type.textContent = 'entity type';
          } else if (isFolder) {
            type.textContent = 'folder';
          } else {
            type.textContent = node.type;
          }

          const right = document.createElement('div');
          right.className = 'row-right';
          right.appendChild(type);

          const trash = document.createElement('button');
          trash.className = 'trash-btn';
          trash.title = 'Delete';
          trash.textContent = '🗑';
          trash.onclick = async (event) => {
            event.stopPropagation();
            await deleteNode(node.path, node.type, node.name);
          };
          right.appendChild(trash);

          row.appendChild(caret);
          row.appendChild(name);
          row.appendChild(right);

          row.onclick = async () => {
            state.selectedPath = node.path;
            if (isFolder) {
              if (!searchActive) {
                await toggleFolder(node.path);
              } else {
                renderTree();
              }
            } else {
              await selectFile(node.path);
            }
          };
          parentElement.appendChild(row);

          if (isFolder && isExpanded) {
            renderLevel(parentElement, node.path, depth + 1);
          }
        });
      }

      renderLevel(tree, null, 0);
      if (!state.visibleNodes.length && searchActive) {
        const empty = document.createElement('div');
        empty.className = 'tree-empty';
        empty.textContent = 'No files match this search.';
        tree.appendChild(empty);
      }
    }

    async function expandInitialTree() {
      const rootNodes = state.children.get(ROOT_KEY) || [];
      for (const namespaceNode of rootNodes) {
        if (namespaceNode.type !== 'folder') {
          continue;
        }
        state.expanded.add(keyFor(namespaceNode.path));
        if (!state.children.has(keyFor(namespaceNode.path))) {
          await loadChildren(namespaceNode.path);
        }
      }
    }

    async function init() {
      setError('');
      try {
        const bootstrap = await api('/api/bootstrap', {});
        const tracking = bootstrap.tracking || {};
        byId('track_summary').textContent = 'Watcher edits found: ' + String(tracking.trackedEdits || 0);
        await loadChildren(null);
        renderTree();
        loadDismissedRecents();
        byId('tree').addEventListener('keydown', handleTreeKeyDown);
        setupTreeSearch();
        updateTreeSearchMeta();
        setupDropZone();
        await refreshQueueSummary();
        await refreshSourceDetection();
        sourceTimer = setInterval(refreshSourceDetection, 3000);

        // Expand namespace level in the background so first render is instant.
        expandInitialTree()
          .then(() => renderTree())
          .catch((error) => setError('Tree expand warning: ' + String(error)));

        // Run edit tracking in background so initial file tree render is never blocked.
        setTimeout(() => { autoSyncEditsInBackground(); }, 0);

        // Keep queue/list in sync with filesystem edits without relying on manual actions.
        setInterval(autoSyncEditsInBackground, 15000);
      } catch (error) {
        setError(String(error));
      }
    }

    window.addEventListener('load', init);
  </script>
</body>
</html>
""".encode("utf-8")


class WorkspaceWebHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            html = build_html()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self) -> None:
        payload = self.read_json()
        try:
            if self.path == "/api/bootstrap":
                self.send_json(HTTPStatus.OK, browser_bootstrap())
                return
            if self.path == "/api/tree":
                selected = payload.get("path")
                self.send_json(
                    HTTPStatus.OK,
                    {"children": tree_children(str(selected) if selected else None)},
                )
                return
            if self.path == "/api/search-files":
                query = str(payload.get("query") or "")
                self.send_json(HTTPStatus.OK, {"results": search_files(query)})
                return
            if self.path == "/api/clipboard-status":
                self.send_json(HTTPStatus.OK, clipboard_status_report())
                return
            if self.path == "/api/sync-edits":
                self.send_json(HTTPStatus.OK, auto_track_workspace_edits())
                return
            if self.path == "/api/entities":
                namespace = payload.get("namespace") or ""
                self.send_json(HTTPStatus.OK, {"entities": list_entities(namespace)})
                return
            if self.path == "/api/files":
                namespace = payload.get("namespace") or ""
                entity = payload.get("entity") or ""
                query = payload.get("query") or ""
                self.send_json(
                    HTTPStatus.OK,
                    {"files": list_entity_files(namespace=namespace, entity=entity, query=query)},
                )
                return
            if self.path == "/api/file-detail":
                selected = payload.get("path")
                if not selected:
                    raise ValueError("Missing file path.")
                self.send_json(HTTPStatus.OK, file_detail(selected))
                return
            if self.path == "/api/copy-as-fm":
                selected = payload.get("path")
                if not selected:
                    raise ValueError("Missing file path.")
                self.send_json(HTTPStatus.OK, copy_file_as_fm_clipboard(str(selected)))
                return
            if self.path == "/api/copy-as-fm-steps":
                selected = payload.get("path")
                if not selected:
                    raise ValueError("Missing file path.")
                self.send_json(HTTPStatus.OK, copy_file_as_fm_steps_clipboard(str(selected)))
                return
            if self.path == "/api/delete-node":
                selected = payload.get("path")
                if not selected:
                    raise ValueError("Missing file path.")
                self.send_json(HTTPStatus.OK, delete_workspace_node(str(selected)))
                return
            if self.path == "/api/queue/list":
                self.send_json(HTTPStatus.OK, {"rows": queue_rows_for_api()})
                return
            if self.path == "/api/queue/clear-scripts":
                self.send_json(HTTPStatus.OK, clear_script_queue_rows())
                return
            if self.path == "/api/queue/set-status":
                selected = payload.get("path")
                status = payload.get("status")
                if not selected or not status:
                    raise ValueError("Missing path or status.")
                updated = update_queue_status(
                    file_path=safe_resolve_path(selected),
                    status=str(status),
                    notes=(payload.get("notes") or None),
                )
                self.send_json(HTTPStatus.OK, {"row": updated})
                return
            if self.path == "/api/inspect":
                self.send_json(HTTPStatus.OK, run_inspect(payload))
                return
            if self.path == "/api/preview-import":
                self.send_json(HTTPStatus.OK, run_import(payload, preview=True))
                return
            if self.path == "/api/import":
                self.send_json(HTTPStatus.OK, run_import(payload, preview=False))
                return
            if self.path == "/api/dump":
                self.send_json(HTTPStatus.OK, run_dump(payload))
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})
        except (importer.ImporterError, ET.ParseError, FileNotFoundError, PermissionError, OSError, ValueError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Unexpected error: {exc}"})

    def read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return {}
        body = self.rfile.read(int(raw_length))
        if not body:
            return {}
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("JSON payload must be an object.")
        return parsed

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_server_auto_sync(stop_event: threading.Event) -> None:
    while not stop_event.wait(AUTO_TRACK_POLL_SECONDS):
        try:
            auto_track_workspace_edits()
        except Exception:
            # Keep background sync resilient; requests/UI remain usable on transient failures.
            continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run FM CodeSpace (local FileMaker workspace UI)."
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host interface for the web server (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port for the web server (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the UI in your default browser after startup.",
    )
    parser.add_argument(
        "--sync-edits",
        action="store_true",
        help="Run edit auto-tracking once and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sync_edits:
        print(json.dumps(auto_track_workspace_edits(), indent=2))
        return 0

    url = f"http://{args.host}:{args.port}/"
    server = ThreadingHTTPServer((args.host, args.port), WorkspaceWebHandler)
    stop_event = threading.Event()
    auto_sync_thread = threading.Thread(
        target=run_server_auto_sync,
        args=(stop_event,),
        name="fm-codespace-auto-sync",
        daemon=True,
    )
    auto_sync_thread.start()
    print(f"FM CodeSpace running at {url}")
    print("Press Ctrl+C to stop.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
