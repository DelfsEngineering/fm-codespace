#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


KNOWN_CLIPBOARD_TYPES = {
    "dyn.ah62d4rv4gk8zuxnykk": "table",
    "dyn.ah62d4rv4gk8zuxngku": "field",
    "dyn.ah62d4rv4gk8zuxnxkq": "script",
    "dyn.ah62d4rv4gk8zuxnxnq": "script-step",
    "dyn.ah62d4rv4gk8zuxngm2": "custom-function",
    "dyn.ah62d4rv4gk8zuxnqm6": "layout-object-fp7",
    "dyn.ah62d4rv4gk8zuxnqgk": "layout-object-fmp12",
    "dyn.ah62d4rv4gk8zuxn0mu": "value-list",
    "dyn.agk8u": "theme",
    "dyn.ah62d4rv4gk8zuxnyma": "theme",
    "public.utf16-plain-text": "custom-menu",
}
KNOWN_DYNAMIC_PREFIX = "dyn.ah62d4rv4gk8zuxn"
FMXMLSNIPPET_HEADER = "<fmxmlsnippet"
FALLBACK_DIR_NAME = "_Clipboard Imports"
FILEMAKER_FILES_DIR_NAME = "FILEMAKER FILES"
ENTITY_SUBFOLDERS = {
    "script": "scripts",
    "script-step": "scripts",
    "custom-function": "custom functions",
    "table": "tables",
    "field": "fields",
    "value-list": "value lists",
    "layout-object-fp7": "layouts",
    "layout-object-fmp12": "layouts",
    "theme": "themes",
    "custom-menu": "custom menus",
    "unknown": "other fm objects",
    "unknown-filemaker-object": "other fm objects",
}
OBJECT_TAG_KIND = {
    "Script": "script",
    "CustomFunction": "custom-function",
    "BaseTable": "table",
    "Field": "field",
    "ValueList": "value-list",
    "Layout": "layout-object-fmp12",
}


class ImporterError(RuntimeError):
    pass


@dataclass
class ClipboardPayload:
    source: str
    clipboard_type: str
    kind: str
    xml_text: str
    size_bytes: int
    available_types: list[str]


@dataclass
class ImportItem:
    kind: str
    name: str
    relative_parts: list[str]
    output_text: str
    original_tag: str


@dataclass
class WriteResult:
    path: Path
    action: str
    item: ImportItem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect, dump, and import FileMaker clipboard XML into this workspace."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("inspect", "dump", "import"):
        sub = subparsers.add_parser(name)
        add_common_source_args(sub)

    inspect_parser = subparsers.choices["inspect"]
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the inspect result as JSON.",
    )

    dump_parser = subparsers.choices["dump"]
    dump_parser.add_argument(
        "--output",
        type=Path,
        help="Write the raw clipboard XML to this file. Defaults to a temp file.",
    )

    import_parser = subparsers.choices["import"]
    import_parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Base workspace root to write imported files into.",
    )
    import_parser.add_argument(
        "--entity-folder-mode",
        choices=("auto", "off"),
        default="auto",
        help="Route imports into FILEMAKER FILES/<file name>/<entity type>/ folders.",
    )
    import_parser.add_argument(
        "--fallback-dir",
        default=FALLBACK_DIR_NAME,
        help="Fallback file namespace when clipboard XML has no file namespace metadata.",
    )
    import_parser.add_argument(
        "--file-namespace",
        help="Force a specific file namespace under FILEMAKER FILES/ for imported items.",
    )
    import_parser.add_argument(
        "--script-format",
        choices=("steps", "raw"),
        default="raw",
        help="Save script files as step-only snippets or raw <Script> wrappers.",
    )
    import_parser.add_argument(
        "--custom-function-format",
        choices=("calc", "xml"),
        default="xml",
        help="Save custom functions as calculation text or raw XML snippets.",
    )
    import_parser.add_argument(
        "--preview",
        action="store_true",
        help="Show what would be written without touching the filesystem.",
    )
    import_parser.add_argument(
        "--filename-style",
        choices=("pretty", "pretty-id"),
        default="pretty-id",
        help="Use human-readable names only, or append stable FileMaker IDs for round-trip safety.",
    )
    import_parser.add_argument(
        "--overwrite",
        action="store_true",
        default=True,
        help="Overwrite existing files and report them. Enabled by default.",
    )
    import_parser.add_argument(
        "--no-overwrite",
        action="store_false",
        dest="overwrite",
        help="Do not overwrite existing files.",
    )

    return parser


def add_common_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input",
        type=Path,
        help="Read FileMaker XML from a file instead of the macOS clipboard.",
    )
    parser.add_argument(
        "--name",
        help="Fallback name for script-step clipboard content that has no script name.",
    )


def read_payload(args: argparse.Namespace) -> ClipboardPayload:
    if args.input:
        xml_text = args.input.read_text(encoding="utf-8")
        return ClipboardPayload(
            source=str(args.input),
            clipboard_type="file",
            kind=infer_kind_from_xml(xml_text),
            xml_text=xml_text,
            size_bytes=len(xml_text.encode("utf-8")),
            available_types=["file"],
        )
    return read_payload_from_clipboard()


def read_payload_from_clipboard() -> ClipboardPayload:
    try:
        from AppKit import NSPasteboard  # type: ignore
    except ImportError as exc:
        raise ImporterError(
            "PyObjC is required for live clipboard access. Install it with "
            "`python3 -m pip install \"pyobjc==11.1\"` or use --input."
        ) from exc

    pasteboard = NSPasteboard.generalPasteboard()
    raw_types = pasteboard.types() or []
    available_types = [str(item) for item in raw_types]

    for clipboard_type in available_types:
        kind = classify_clipboard_type(clipboard_type)
        if kind is None:
            continue

        data = pasteboard.dataForType_(clipboard_type)
        if data is None:
            continue

        raw_bytes = bytes(data)
        if not raw_bytes:
            continue

        xml_text = decode_clipboard_bytes(clipboard_type, raw_bytes)
        if not looks_like_filemaker_xml(xml_text):
            continue

        return ClipboardPayload(
            source="clipboard",
            clipboard_type=clipboard_type,
            kind=kind,
            xml_text=xml_text,
            size_bytes=len(raw_bytes),
            available_types=available_types,
        )

    joined = ", ".join(available_types) if available_types else "(none)"
    raise ImporterError(
        "No supported FileMaker clipboard payload was found. Clipboard types seen: "
        f"{joined}"
    )


def classify_clipboard_type(clipboard_type: str) -> str | None:
    if clipboard_type in KNOWN_CLIPBOARD_TYPES:
        return KNOWN_CLIPBOARD_TYPES[clipboard_type]
    if clipboard_type.startswith(KNOWN_DYNAMIC_PREFIX):
        return "unknown-filemaker-object"
    return None


def decode_clipboard_bytes(clipboard_type: str, raw_bytes: bytes) -> str:
    if clipboard_type == "public.utf16-plain-text":
        return raw_bytes.decode("utf-16-le", errors="replace")
    return raw_bytes.decode("utf-8", errors="replace")


def looks_like_filemaker_xml(xml_text: str) -> bool:
    stripped = xml_text.lstrip("\ufeff\r\n\t ")
    return stripped.startswith("<?xml") or FMXMLSNIPPET_HEADER in stripped or "<FMObjectTransfer " in stripped


def infer_kind_from_xml(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return "unknown"

    child_tags = [local_tag(child.tag) for child in root]
    counts = Counter(child_tags)
    if counts.get("Group"):
        return "group"
    if counts.get("Script"):
        return "script"
    if counts.get("Step") and len(counts) == 1:
        return "script-step"
    if counts.get("CustomFunction"):
        return "custom-function"
    if counts.get("BaseTable"):
        return "table"
    if counts.get("Field"):
        return "field"
    if counts.get("ValueList"):
        return "value-list"
    if counts.get("Layout"):
        return "layout-object-fmp12"
    return "unknown"


def inspect_payload(payload: ClipboardPayload, fallback_name: str | None) -> dict:
    root = ET.fromstring(payload.xml_text)
    child_tags = [local_tag(child.tag) for child in root]
    child_counts = Counter(child_tags)

    items = collect_import_items(
        payload.xml_text,
        fallback_name=fallback_name,
        script_format="steps",
        custom_function_format="calc",
        fallback_dir=FALLBACK_DIR_NAME,
        filename_style="pretty-id",
    )
    preview_paths = ["/".join(item.relative_parts) for item in items[:20]]

    return {
        "source": payload.source,
        "clipboard_type": payload.clipboard_type,
        "kind": payload.kind,
        "entity_folder": f"{FILEMAKER_FILES_DIR_NAME}/<file name>/{entity_subfolder(payload.kind)}",
        "size_bytes": payload.size_bytes,
        "available_types": payload.available_types,
        "xml_root": local_tag(root.tag),
        "top_level_tags": dict(sorted(child_counts.items())),
        "item_count": len(items),
        "preview_paths": preview_paths,
        "notes": inspect_notes(root, items, fallback_name),
    }


def inspect_notes(root: ET.Element, items: list[ImportItem], fallback_name: str | None) -> list[str]:
    notes: list[str] = []
    top_tags = {local_tag(child.tag) for child in root}
    if "Group" in top_tags:
        notes.append("Clipboard payload contains Group nodes, so folder hierarchy can be reconstructed.")
    if top_tags == {"Step"}:
        notes.append("Clipboard payload looks like script steps, not full script objects.")
        if not fallback_name:
            notes.append("Use --name when importing script-step payloads so the file can be named clearly.")
    if not items:
        notes.append("No importable FileMaker object nodes were detected.")
    return notes


def collect_import_items(
    xml_text: str,
    fallback_name: str | None,
    script_format: str,
    custom_function_format: str,
    fallback_dir: str,
    filename_style: str,
) -> list[ImportItem]:
    root = ET.fromstring(xml_text)
    items: list[ImportItem] = []
    supported_top_level = False
    importable_tags = set(OBJECT_TAG_KIND.keys())
    raw_object_snippets = {
        tag: iter(extract_raw_element_snippets(xml_text, {tag}))
        for tag in importable_tags
    }

    for child in root:
        tag = local_tag(child.tag)
        if tag in {"Group"} | importable_tags:
            supported_top_level = True
            items.extend(
                collect_items_from_node(
                    child,
                    parent_parts=[],
                    script_format=script_format,
                    custom_function_format=custom_function_format,
                    filename_style=filename_style,
                    raw_object_snippets=raw_object_snippets,
                )
            )

    if items:
        return dedupe_relative_paths(items)

    child_tags = [local_tag(child.tag) for child in root]
    if child_tags and all(tag == "Step" for tag in child_tags):
        script_name = fallback_name or "Clipboard Script Steps"
        item = ImportItem(
            kind="script",
            name=script_name,
            relative_parts=[fallback_dir, script_filename(script_name, filename_style=filename_style)],
            output_text=xml_text,
            original_tag="Step",
        )
        return [item]

    if not supported_top_level:
        raise ImporterError(
            "Clipboard XML did not contain importable FileMaker object nodes."
        )
    return dedupe_relative_paths(items)


def collect_items_from_node(
    node: ET.Element,
    parent_parts: list[str],
    script_format: str,
    custom_function_format: str,
    filename_style: str,
    raw_object_snippets: dict[str, Iterator[str]],
) -> list[ImportItem]:
    tag = local_tag(node.tag)
    if tag == "Group":
        group_name = object_path_name(
            raw_name=node.attrib.get("name", "Unnamed Group"),
            object_kind="group",
            filemaker_id=node.attrib.get("id"),
        )
        next_parts = parent_parts + [group_name]
        items: list[ImportItem] = []
        for child in node:
            child_tag = local_tag(child.tag)
            if child_tag in {"Group"} | set(OBJECT_TAG_KIND.keys()):
                items.extend(
                    collect_items_from_node(
                        child,
                        parent_parts=next_parts,
                        script_format=script_format,
                        custom_function_format=custom_function_format,
                        filename_style=filename_style,
                        raw_object_snippets=raw_object_snippets,
                    )
                )
        return items

    if tag == "Script":
        name = node.attrib.get("name", "Untitled Script")
        filename = script_filename(
            name,
            filemaker_id=node.attrib.get("id"),
            filename_style=filename_style,
        )
        if script_format == "steps":
            output_text = serialize_step_snippet(list(node))
        else:
            output_text = wrap_raw_object_snippet(next(raw_object_snippets["Script"]))
        return [
            ImportItem(
                kind="script",
                name=name,
                relative_parts=parent_parts + [filename],
                output_text=output_text,
                original_tag="Script",
            )
        ]

    if tag == "CustomFunction":
        name = node.attrib.get("name", "Untitled Custom Function")
        if custom_function_format == "calc":
            calc = node.find("./Calculation")
            output_text = (calc.text or "").rstrip() + "\n"
            filename = object_path_name(
                raw_name=name,
                object_kind="custom-function",
                filemaker_id=node.attrib.get("id"),
                filename_style=filename_style,
            )
        else:
            output_text = wrap_raw_object_snippet(
                next(raw_object_snippets["CustomFunction"])
            )
            filename = (
                object_path_name(
                    raw_name=name,
                    object_kind="custom-function",
                    filemaker_id=node.attrib.get("id"),
                    filename_style=filename_style,
                )
                + ".xml"
            )
        return [
            ImportItem(
                kind="custom-function",
                name=name,
                relative_parts=parent_parts + [filename],
                output_text=output_text,
                original_tag="CustomFunction",
            )
        ]

    if tag in {"BaseTable", "Field", "ValueList", "Layout"}:
        object_kind = OBJECT_TAG_KIND[tag]
        name = node.attrib.get("name", f"Untitled {tag}")
        filename = (
            object_path_name(
                raw_name=name,
                object_kind=object_kind,
                filemaker_id=node.attrib.get("id"),
                filename_style=filename_style,
            )
            + ".xml"
        )
        output_text = wrap_raw_object_snippet(next(raw_object_snippets[tag]))
        return [
            ImportItem(
                kind=object_kind,
                name=name,
                relative_parts=parent_parts + [filename],
                output_text=output_text,
                original_tag=tag,
            )
        ]

    return []


def dedupe_relative_paths(items: list[ImportItem]) -> list[ImportItem]:
    used: Counter[str] = Counter()
    output: list[ImportItem] = []
    for item in items:
        base_parts = list(item.relative_parts)
        key = "/".join(base_parts)
        used[key] += 1
        if used[key] == 1:
            output.append(item)
            continue

        stem, suffix = split_filename(base_parts[-1])
        updated_name = f"{stem}__{used[key]}{suffix}"
        base_parts[-1] = updated_name
        output.append(
            ImportItem(
                kind=item.kind,
                name=item.name,
                relative_parts=base_parts,
                output_text=item.output_text,
                original_tag=item.original_tag,
            )
        )
    return output


def write_items(
    items: Iterable[ImportItem],
    root: Path,
    preview: bool,
    overwrite: bool,
) -> list[WriteResult]:
    results: list[WriteResult] = []
    for item in items:
        target = root.joinpath(*item.relative_parts)
        action = "overwrite" if target.exists() else "create"
        if action == "overwrite" and not overwrite:
            action = "skip"

        if not preview and action != "skip":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.output_text, encoding="utf-8")

        results.append(WriteResult(path=target, action=action, item=item))
    return results


def serialize_step_snippet(step_nodes: list[ET.Element]) -> str:
    root = ET.Element("fmxmlsnippet", {"type": "FMObjectList"})
    for child in step_nodes:
        root.append(deep_copy_element(child))
    return serialize_xml(root)


def serialize_single_node_snippet(node: ET.Element) -> str:
    root = ET.Element("fmxmlsnippet", {"type": "FMObjectList"})
    root.append(deep_copy_element(node))
    return serialize_xml(root)


def wrap_raw_object_snippet(raw_snippet: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<fmxmlsnippet type="FMObjectList">\n'
        f"{raw_snippet}\n"
        "</fmxmlsnippet>\n"
    )


def deep_copy_element(node: ET.Element) -> ET.Element:
    clone = ET.fromstring(ET.tostring(node, encoding="utf-8"))
    return clone


def serialize_xml(root: ET.Element) -> str:
    indent_xml(root)
    xml_body = ET.tostring(root, encoding="unicode", short_empty_elements=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n"


def indent_xml(element: ET.Element, level: int = 0) -> None:
    indent = "\n" + ("  " * level)
    child_indent = "\n" + ("  " * (level + 1))
    children = list(element)
    if children:
        if not element.text or not element.text.strip():
            element.text = child_indent
        for child in children:
            indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = child_indent
        children[-1].tail = indent
    elif level and (not element.tail or not element.tail.strip()):
        element.tail = indent


def script_filename(
    name: str,
    filemaker_id: str | None = None,
    filename_style: str = "pretty-id",
) -> str:
    sanitized = object_path_name(
        raw_name=name,
        object_kind="script",
        filemaker_id=filemaker_id,
        filename_style=filename_style,
    )
    if sanitized.endswith(".xml"):
        return sanitized
    return f"{sanitized}.xml"


def sanitize_path_component(name: str) -> str:
    cleaned = name.strip()
    replacements = {
        "/": " ",
        ":": " ",
        "\\": " ",
        "\0": "",
        "\n": " ",
        "\r": " ",
        "\t": " ",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s*-\s*-\s*", " - ", cleaned)
    cleaned = cleaned.strip(" .-_")
    if not cleaned or not any(char.isalnum() for char in cleaned):
        return ""
    return cleaned


def object_path_name(
    raw_name: str,
    object_kind: str,
    filemaker_id: str | None = None,
    filename_style: str = "pretty-id",
) -> str:
    display_name = object_display_name(
        raw_name=raw_name,
        object_kind=object_kind,
        filemaker_id=filemaker_id,
    )
    if filename_style == "pretty-id" and filemaker_id and object_kind in {
        "script",
        "custom-function",
        "table",
        "field",
        "value-list",
        "layout-object-fmp12",
    }:
        return f"{display_name}__{object_kind_token(object_kind)}-{filemaker_id}"
    return display_name


def object_display_name(
    raw_name: str,
    object_kind: str,
    filemaker_id: str | None = None,
) -> str:
    sanitized = sanitize_path_component(raw_name)
    if sanitized:
        return sanitized

    fallback_labels = {
        "script": "Divider" if raw_name.strip() == "-" else "Script",
        "custom-function": "Custom Function",
        "group": "Group",
    }
    label = fallback_labels.get(object_kind, "Untitled")
    if filemaker_id:
        return f"{label} {filemaker_id}"
    return label


def object_kind_token(object_kind: str) -> str:
    tokens = {
        "script": "fmscript",
        "custom-function": "fmcf",
        "table": "fmtable",
        "field": "fmfield",
        "value-list": "fmvl",
        "layout-object-fmp12": "fmlayout",
    }
    return tokens.get(object_kind, "fmobj")


def extract_raw_element_snippets(xml_text: str, target_tags: set[str]) -> list[str]:
    snippets: list[str] = []
    stack: list[tuple[str, int, bool]] = []
    i = 0
    length = len(xml_text)

    while i < length:
        if xml_text.startswith("<![CDATA[", i):
            end = xml_text.find("]]>", i + 9)
            if end == -1:
                raise ImporterError("Malformed XML: unterminated CDATA section.")
            i = end + 3
            continue
        if xml_text.startswith("<!--", i):
            end = xml_text.find("-->", i + 4)
            if end == -1:
                raise ImporterError("Malformed XML: unterminated comment.")
            i = end + 3
            continue
        if xml_text.startswith("<?", i):
            end = xml_text.find("?>", i + 2)
            if end == -1:
                raise ImporterError("Malformed XML: unterminated processing instruction.")
            i = end + 2
            continue
        if xml_text[i] != "<":
            i += 1
            continue

        end = find_tag_close(xml_text, i)
        token = xml_text[i + 1 : end].strip()
        if not token:
            i = end + 1
            continue

        if token.startswith("!"):
            i = end + 1
            continue

        if token.startswith("/"):
            tag_name = parse_tag_name(token[1:])
            if not stack:
                raise ImporterError("Malformed XML: unexpected closing tag.")
            open_tag, start, is_target = stack.pop()
            if open_tag != tag_name:
                raise ImporterError(
                    f"Malformed XML: closing tag </{tag_name}> did not match <{open_tag}>."
                )
            if is_target:
                snippets.append(xml_text[start : end + 1])
            i = end + 1
            continue

        self_closing = token.endswith("/")
        tag_name = parse_tag_name(token[:-1] if self_closing else token)
        parent_tag = stack[-1][0] if stack else None
        is_target = tag_name in target_tags and parent_tag in {"fmxmlsnippet", "Group"}
        if self_closing:
            if is_target:
                snippets.append(xml_text[i : end + 1])
        else:
            stack.append((tag_name, i, is_target))
        i = end + 1

    if stack:
        raise ImporterError("Malformed XML: unclosed tag while extracting raw snippets.")
    return snippets


def find_tag_close(xml_text: str, start_index: int) -> int:
    quote_char = ""
    i = start_index + 1
    length = len(xml_text)
    while i < length:
        char = xml_text[i]
        if quote_char:
            if char == quote_char:
                quote_char = ""
        else:
            if char in {'"', "'"}:
                quote_char = char
            elif char == ">":
                return i
        i += 1
    raise ImporterError("Malformed XML: unterminated tag.")


def parse_tag_name(token: str) -> str:
    match = re.match(r"([^\s/>]+)", token.strip())
    if not match:
        raise ImporterError("Malformed XML: could not parse tag name.")
    return match.group(1).rsplit("}", 1)[-1]


def split_filename(filename: str) -> tuple[str, str]:
    path = Path(filename)
    return path.stem, path.suffix


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def print_inspect_summary(summary: dict) -> None:
    print(f"Source: {summary['source']}")
    print(f"Clipboard type: {summary['clipboard_type']}")
    print(f"Kind: {summary['kind']}")
    print(f"Entity folder: {summary['entity_folder']}")
    print(f"Size: {summary['size_bytes']} bytes")
    print(f"XML root: {summary['xml_root']}")
    print("Top-level tags:")
    for tag, count in summary["top_level_tags"].items():
        print(f"  - {tag}: {count}")
    print(f"Importable items: {summary['item_count']}")
    if summary["preview_paths"]:
        print("Preview paths:")
        for path in summary["preview_paths"]:
            print(f"  - {path}")
    if summary["notes"]:
        print("Notes:")
        for note in summary["notes"]:
            print(f"  - {note}")


def print_import_summary(results: list[WriteResult], preview: bool) -> None:
    counter = Counter(result.action for result in results)
    mode = "Preview" if preview else "Import"
    print(
        f"{mode} summary: {counter.get('create', 0)} created, "
        f"{counter.get('overwrite', 0)} overwritten, {counter.get('skip', 0)} skipped."
    )
    for result in results:
        print(f"[{result.action}] {result.path}")


def entity_subfolder(payload_kind: str) -> str:
    return ENTITY_SUBFOLDERS.get(payload_kind, ENTITY_SUBFOLDERS["unknown"])


def infer_file_namespace_and_parts(
    relative_parts: list[str],
    known_namespaces: set[str],
    explicit_file_namespace: str | None,
    fallback_namespace: str,
) -> tuple[str, list[str]]:
    if explicit_file_namespace:
        if relative_parts and relative_parts[0] == explicit_file_namespace:
            return explicit_file_namespace, relative_parts[1:]
        return explicit_file_namespace, relative_parts

    if not relative_parts:
        return fallback_namespace, []

    if len(relative_parts) == 1:
        return fallback_namespace, relative_parts

    first_part = relative_parts[0]
    if first_part in known_namespaces:
        return first_part, relative_parts[1:]

    if len(known_namespaces) == 1:
        return next(iter(known_namespaces)), relative_parts

    return first_part, relative_parts[1:]


def route_items_to_filemaker_layout(
    items: list[ImportItem],
    base_root: Path,
    payload_kind: str,
    explicit_file_namespace: str | None,
    fallback_namespace: str,
    entity_folder_mode: str,
) -> list[ImportItem]:
    if entity_folder_mode == "off":
        return items

    filemaker_root = base_root / FILEMAKER_FILES_DIR_NAME
    known_namespaces = {
        path.name
        for path in filemaker_root.iterdir()
        if path.is_dir()
    } if filemaker_root.exists() else set()
    routed_items: list[ImportItem] = []
    for item in items:
        # Route by each item's own kind so grouped/mixed payloads
        # still land in the correct entity folders (e.g. scripts -> scripts/).
        target_subfolder = entity_subfolder(item.kind or payload_kind)
        file_namespace, trimmed_parts = infer_file_namespace_and_parts(
            relative_parts=item.relative_parts,
            known_namespaces=known_namespaces,
            explicit_file_namespace=explicit_file_namespace,
            fallback_namespace=fallback_namespace,
        )
        routed_items.append(
            ImportItem(
                kind=item.kind,
                name=item.name,
                relative_parts=[
                    FILEMAKER_FILES_DIR_NAME,
                    file_namespace,
                    target_subfolder,
                    *trimmed_parts,
                ],
                output_text=item.output_text,
                original_tag=item.original_tag,
            )
        )
    return routed_items


def command_inspect(args: argparse.Namespace) -> int:
    payload = read_payload(args)
    summary = inspect_payload(payload, fallback_name=args.name)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print_inspect_summary(summary)
    return 0


def command_dump(args: argparse.Namespace) -> int:
    payload = read_payload(args)
    output_path = args.output
    if output_path is None:
        fd, temp_path = tempfile.mkstemp(prefix="fm-clipboard-", suffix=".xml")
        try:
            os.close(fd)
        except OSError:
            pass
        Path(temp_path).write_text(payload.xml_text, encoding="utf-8")
        print(temp_path)
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload.xml_text, encoding="utf-8")
    print(output_path)
    return 0


def command_import(args: argparse.Namespace) -> int:
    payload = read_payload(args)
    items = collect_import_items(
        payload.xml_text,
        fallback_name=args.name,
        script_format=args.script_format,
        custom_function_format=args.custom_function_format,
        fallback_dir=args.fallback_dir,
        filename_style=args.filename_style,
    )
    routed_items = route_items_to_filemaker_layout(
        items=items,
        base_root=args.root.resolve(),
        payload_kind=payload.kind,
        explicit_file_namespace=args.file_namespace,
        fallback_namespace=args.fallback_dir,
        entity_folder_mode=args.entity_folder_mode,
    )
    results = write_items(
        routed_items,
        root=args.root.resolve(),
        preview=args.preview,
        overwrite=args.overwrite,
    )
    print_import_summary(results, preview=args.preview)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "inspect":
            return command_inspect(args)
        if args.command == "dump":
            return command_dump(args)
        if args.command == "import":
            return command_import(args)
        parser.error(f"Unknown command: {args.command}")
    except ImporterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ET.ParseError as exc:
        print(f"Error: Invalid XML payload: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
