"""User-defined vault record types (merged into schema + Curator prompt context).

Custom types are declared under ``vault.custom_record_types`` in the unified
``config.yaml``. The Curator daemon and agent ``alfred vault`` subprocesses
receive the same definitions via ``ALFRED_CUSTOM_TYPES_JSON`` so validation
matches the active configuration even when the working directory differs.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import schema

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class CustomTypeSpec:
    """Normalized custom record type definition."""

    id: str
    directory: str
    statuses: frozenset[str] | None  # None = unrestricted (same as built-in ``event``)
    name_field: str


def _parse_one(raw: dict[str, Any], index: int) -> CustomTypeSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"custom_record_types[{index}] must be a mapping, got {type(raw).__name__}")
    tid = raw.get("id") or raw.get("type")
    if not tid or not isinstance(tid, str):
        raise ValueError(f"custom_record_types[{index}]: missing string 'id' (or 'type')")
    tid = tid.strip().lower()
    if tid in schema.BUILTIN_KNOWN_TYPES:
        raise ValueError(f"custom_record_types[{index}]: id '{tid}' conflicts with a built-in type")
    if not _ID_RE.match(tid):
        raise ValueError(
            f"custom_record_types[{index}]: id '{tid}' must match "
            r"^[a-z][a-z0-9_-]*$ (lowercase slug)"
        )
    directory = raw.get("directory")
    if directory is None or directory == "":
        directory = tid
    if not isinstance(directory, str) or not directory.strip():
        raise ValueError(f"custom_record_types[{index}]: invalid directory for '{tid}'")
    directory = directory.strip().replace("\\", "/").strip("/")

    name_field = raw.get("name_field") or raw.get("title_field") or "name"
    if not isinstance(name_field, str) or not name_field.strip():
        raise ValueError(f"custom_record_types[{index}]: name_field must be a non-empty string")
    name_field = name_field.strip()

    st = raw.get("statuses", raw.get("status"))
    if st is None:
        statuses: frozenset[str] | None = None
    elif st == []:
        statuses = frozenset()
    elif isinstance(st, (list, tuple)):
        statuses = frozenset(str(x).strip() for x in st if str(x).strip())
    else:
        raise ValueError(f"custom_record_types[{index}]: 'statuses' must be a list or omitted, for '{tid}'")

    return CustomTypeSpec(id=tid, directory=directory, statuses=statuses, name_field=name_field)


def parse_custom_type_specs(items: list[Any] | None) -> list[CustomTypeSpec]:
    """Parse YAML-loaded ``custom_record_types`` list."""
    if not items:
        return []
    if not isinstance(items, list):
        raise ValueError("vault.custom_record_types must be a list")
    return [_parse_one(x, i) for i, x in enumerate(items)]


def extract_custom_type_specs_from_raw(raw: dict[str, Any] | None) -> list[CustomTypeSpec]:
    if not raw:
        return []
    vault = raw.get("vault")
    if not isinstance(vault, dict):
        return []
    items = vault.get("custom_record_types")
    if items is None:
        return []
    return parse_custom_type_specs(items)


def apply_custom_type_specs(specs: list[CustomTypeSpec]) -> None:
    """Register custom types on the shared schema (runtime mutation)."""
    for s in specs:
        schema.KNOWN_TYPES.add(s.id)
        schema.TYPE_DIRECTORY[s.id] = s.directory
        if s.statuses is None:
            schema.STATUS_BY_TYPE[s.id] = set()
        else:
            schema.STATUS_BY_TYPE[s.id] = set(s.statuses)
        if s.name_field != "name":
            schema.NAME_FIELD_BY_TYPE[s.id] = s.name_field
        else:
            schema.NAME_FIELD_BY_TYPE.pop(s.id, None)


def specs_to_jsonable(specs: list[CustomTypeSpec]) -> list[dict[str, Any]]:
    """Serialize specs for ``ALFRED_CUSTOM_TYPES_JSON``."""
    out: list[dict[str, Any]] = []
    for s in specs:
        d: dict[str, Any] = {
            "id": s.id,
            "directory": s.directory,
            "name_field": s.name_field,
        }
        if s.statuses is None:
            d["statuses"] = None
        else:
            d["statuses"] = sorted(s.statuses)
        out.append(d)
    return out


def custom_types_json_for_raw(raw: dict[str, Any] | None) -> str:
    specs = extract_custom_type_specs_from_raw(raw)
    return custom_types_env_json(specs)


def extract_learn_types_from_raw(raw: dict[str, Any] | None) -> list[str]:
    if not raw:
        return []
    distiller = raw.get("distiller")
    if not isinstance(distiller, dict):
        return []
    extraction = distiller.get("extraction")
    if not isinstance(extraction, dict):
        return []
    items = extraction.get("learn_types")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            v = item.strip().lower()
            if v:
                out.append(v)
    return out


def learn_types_json_for_raw(raw: dict[str, Any] | None) -> str:
    learn_types = extract_learn_types_from_raw(raw)
    if not learn_types:
        return "[]"
    return json.dumps(sorted(set(learn_types)), separators=(",", ":"))


def extract_learn_subfolder_from_raw(raw: dict[str, Any] | None) -> str:
    if not raw:
        return ""
    vault = raw.get("vault")
    if not isinstance(vault, dict):
        return ""
    val = vault.get("learn_subfolder", "")
    if not isinstance(val, str):
        return ""
    return val.strip().replace("\\", "/").strip("/")


def custom_types_env_json(specs: list[CustomTypeSpec]) -> str:
    if not specs:
        return "[]"
    return json.dumps(specs_to_jsonable(specs), separators=(",", ":"))


def apply_from_json(payload: str) -> None:
    """Apply types from ``ALFRED_CUSTOM_TYPES_JSON`` (same shape as specs_to_jsonable output)."""
    payload = (payload or "").strip()
    if not payload or payload == "[]":
        return
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("ALFRED_CUSTOM_TYPES_JSON must be a JSON array")
    specs: list[CustomTypeSpec] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"ALFRED_CUSTOM_TYPES_JSON[{i}] must be an object")
        fake_raw = {
            "id": item.get("id"),
            "directory": item.get("directory"),
            "name_field": item.get("name_field"),
            "statuses": item.get("statuses"),
        }
        specs.append(_parse_one(fake_raw, i))
    apply_custom_type_specs(specs)


def apply_from_unified_config(raw: dict[str, Any] | None) -> None:
    specs = extract_custom_type_specs_from_raw(raw)
    apply_custom_type_specs(specs)
    learn_types = extract_learn_types_from_raw(raw)
    if learn_types:
        schema.LEARN_TYPES.update(learn_types)
    learn_subfolder = extract_learn_subfolder_from_raw(raw)
    if learn_subfolder:
        learn_set = set(learn_types) if learn_types else set(schema.LEARN_TYPES)
        for rec_type in learn_set:
            schema.TYPE_DIRECTORY[rec_type] = f"{learn_subfolder}/{rec_type}"


def install_custom_types_for_process(raw: dict[str, Any] | None) -> None:
    """Apply schema mutations and export ``ALFRED_CUSTOM_TYPES_JSON`` for subprocesses."""
    apply_from_unified_config(raw)
    os.environ["ALFRED_CUSTOM_TYPES_JSON"] = custom_types_json_for_raw(raw)
    os.environ["ALFRED_LEARN_TYPES_JSON"] = learn_types_json_for_raw(raw)
    os.environ["ALFRED_LEARN_SUBFOLDER"] = extract_learn_subfolder_from_raw(raw)


def bootstrap_schema_from_environment() -> None:
    """Load custom types from env or config file (for ``alfred vault`` CLI).

    Order:
    1. ``ALFRED_CUSTOM_TYPES_JSON`` if set and non-empty.
    2. Unified config: ``ALFRED_CONFIG_PATH`` or ``config.yaml`` under cwd.
    """
    env_json = os.environ.get("ALFRED_CUSTOM_TYPES_JSON", "").strip()
    if env_json and env_json != "[]":
        apply_from_json(env_json)
        learn_types_env = os.environ.get("ALFRED_LEARN_TYPES_JSON", "").strip()
        if learn_types_env and learn_types_env != "[]":
            try:
                data = json.loads(learn_types_env)
                if isinstance(data, list):
                    schema.LEARN_TYPES.update(
                        str(x).strip().lower() for x in data if str(x).strip()
                    )
            except json.JSONDecodeError:
                pass
        learn_subfolder_env = os.environ.get("ALFRED_LEARN_SUBFOLDER", "").strip()
        if learn_subfolder_env:
            sub = learn_subfolder_env.replace("\\", "/").strip("/")
            if sub:
                for rec_type in set(schema.LEARN_TYPES):
                    schema.TYPE_DIRECTORY[rec_type] = f"{sub}/{rec_type}"
        return
    cfg_name = os.environ.get("ALFRED_CONFIG_PATH", "config.yaml")
    path = Path(cfg_name)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        return
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return
    apply_from_unified_config(raw if isinstance(raw, dict) else {})


def build_curator_prompt_appendix(specs: list[CustomTypeSpec]) -> str:
    """Extra Curator instructions (ASCII only for Windows cp1252 consoles)."""
    if not specs:
        return ""
    lines = [
        "",
        "---",
        "### Custom record types (from config)",
        "",
        "These types are valid for `alfred vault create` and must use the listed folder",
        "under the vault root. Treat them like first-class entity types (same as note, event, etc.).",
        "",
    ]
    for s in specs:
        st = "any status allowed" if s.statuses is None else ("statuses: " + ", ".join(sorted(s.statuses)))
        nf = f"name field: {s.name_field}"
        lines.append(f"- type `{s.id}` -> ./{s.directory}/   ({st}; {nf})")
    lines.append("")
    lines.append("When inbox content clearly matches one of these domains, create that type")
    lines.append("in its folder instead of defaulting to `note`.")
    lines.append("")
    return "\n".join(lines)


def write_template_files(vault_path: Path, specs: list[CustomTypeSpec]) -> list[str]:
    """Write minimal ``_templates/{id}.md`` stubs for each custom type. Returns paths written."""
    tmpl_dir = vault_path / "_templates"
    tmpl_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for s in specs:
        rel = f"_templates/{s.id}.md"
        path = tmpl_dir / f"{s.id}.md"
        if path.exists():
            continue
        status_comment = (
            "# any status"
            if s.statuses is None
            else "# " + " | ".join(sorted(s.statuses))
        )
        lines = [
            "---",
            f"type: {s.id}",
            "status: ",
            status_comment,
        ]
        if s.name_field != "name":
            lines.append(f"{s.name_field}: ")
        lines.extend(
            [
                "description:",
                "related: []",
                "relationships: []",
                'created: "{{date}}"',
                "tags: []",
                "---",
                "",
                "# {{title}}",
                "",
            ]
        )
        path.write_text("\n".join(lines), encoding="utf-8")
        written.append(rel)
    return written
