#!/usr/bin/env python3
"""Sprawdź aktywną wersję guarda i schemat evidence w dokumentach."""
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path
from typing import Mapping


SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
VERSION_PATTERNS = {
    "hermes/README.md": re.compile(
        r"^`factory-execution-guards` v(?P<value>[0-9]+\.[0-9]+\.[0-9]+) zachowuje ",
        re.MULTILINE,
    ),
    "workflows/MODEL_ROUTING_POLICY.md": re.compile(
        r"^Software Factory profiles use profile-scoped `factory-execution-guards` v(?P<value>[0-9]+\.[0-9]+\.[0-9]+)\.",
        re.MULTILINE,
    ),
    "workflows/KANBAN_CONTRACT.md": re.compile(
        r"^Software Factory używa profile-scoped `factory-execution-guards` v(?P<value>[0-9]+\.[0-9]+\.[0-9]+)\.",
        re.MULTILINE,
    ),
    "hermes/profiles/coder-claude/SOUL.md": re.compile(
        r"^- Profil ma aktywny `factory-execution-guards` v(?P<value>[0-9]+\.[0-9]+\.[0-9]+):",
        re.MULTILINE,
    ),
    "hermes/profiles/reviewer-gpt/SOUL.md": re.compile(
        r"^- Profil ma aktywny `factory-execution-guards` v(?P<value>[0-9]+\.[0-9]+\.[0-9]+)\.",
        re.MULTILINE,
    ),
    "hermes/profiles/runtime-controller/SOUL.md": re.compile(
        r"^- Profil ma aktywny `factory-execution-guards` v(?P<value>[0-9]+\.[0-9]+\.[0-9]+)\.",
        re.MULTILINE,
    ),
}
SCHEMA_PATTERNS = {
    "hermes/README.md": re.compile(
        r"^- evidence schema v(?P<value>[0-9]+) wiąże ", re.MULTILINE
    ),
    "workflows/MODEL_ROUTING_POLICY.md": re.compile(
        r"^Software Factory profiles use profile-scoped `factory-execution-guards` v[0-9.]+\. Version [0-9.]+ preserves the reviewed v[0-9.]+ execution evidence schema v(?P<value>[0-9]+) ",
        re.MULTILINE,
    ),
    "workflows/KANBAN_CONTRACT.md": re.compile(
        r"^Software Factory używa profile-scoped `factory-execution-guards` v[0-9.]+\. Wersja [0-9.]+ zachowuje reviewed v[0-9.]+, execution evidence schema v(?P<value>[0-9]+) ",
        re.MULTILINE,
    ),
    "hermes/profiles/coder-claude/SOUL.md": re.compile(
        r"^- Guard tworzy in-process attestation i evidence schema v(?P<value>[0-9]+) ",
        re.MULTILINE,
    ),
    "hermes/profiles/reviewer-gpt/SOUL.md": re.compile(
        r"^.*schema-(?P<value>[0-9]+) execution evidence.*$", re.MULTILINE
    ),
    "hermes/profiles/runtime-controller/SOUL.md": re.compile(
        r"^.*schema-(?P<value>[0-9]+) execution evidence.*$", re.MULTILINE
    ),
}
HANDOFF_SCHEMA_PATTERNS = {
    "hermes/README.md": re.compile(
        r"^`factory-execution-guards` v[0-9.]+ .* handoff schema v(?P<value>[0-9]+):$",
        re.MULTILINE,
    ),
    "workflows/MODEL_ROUTING_POLICY.md": re.compile(
        r"^Software Factory profiles use profile-scoped `factory-execution-guards` v[0-9.]+\. Version [0-9.]+ .* handoff schema v(?P<value>[0-9]+),",
        re.MULTILINE,
    ),
    "workflows/KANBAN_CONTRACT.md": re.compile(
        r"^Software Factory używa profile-scoped `factory-execution-guards` v[0-9.]+\. Wersja [0-9.]+ .* handoff schema v(?P<value>[0-9]+),",
        re.MULTILINE,
    ),
    "hermes/profiles/coder-claude/SOUL.md": re.compile(
        r"^.*atomowo osobną handoff schema v(?P<value>[0-9]+),.*$", re.MULTILINE
    ),
    "hermes/profiles/reviewer-gpt/SOUL.md": re.compile(
        r"^.*wiąże handoff schema v(?P<value>[0-9]+),.*$", re.MULTILINE
    ),
    "hermes/profiles/runtime-controller/SOUL.md": re.compile(
        r"^.*atomowej handoff schema v(?P<value>[0-9]+) związanej.*$", re.MULTILINE
    ),
}


def _parse_plugin_manifest(path: Path) -> dict[str, str]:
    """Wczytaj proste skalarne pola manifestu pluginu bez luźnego grepa."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line[0].isspace() or raw_line.lstrip().startswith("#"):
            continue
        key, separator, raw_value = raw_line.partition(":")
        if separator and raw_value.strip():
            values[key.strip()] = raw_value.strip()
    return values


def _guard_schema(path: Path) -> int:
    """Odczytaj stałą schematu z AST, a nie z dopasowania podciągu."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "EVIDENCE_SCHEMA":
            value = ast.literal_eval(node.value)
            if type(value) is int and value > 0:
                return value
            break
    raise ValueError("EVIDENCE_SCHEMA must be one positive integer assignment")


def _handoff_schema(path: Path) -> int:
    """Odczytaj osobną stałą schematu handoff z AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "HANDOFF_SCHEMA":
            value = ast.literal_eval(node.value)
            if type(value) is int and value > 0:
                return value
            break
    raise ValueError("HANDOFF_SCHEMA must be one positive integer assignment")


def _single_marker_value(pattern: re.Pattern[str], text: str) -> str | None:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        return None
    return matches[0].group("value")


def collect_consistency_errors(
    root: Path,
    *,
    document_overrides: Mapping[str, Path] | None = None,
) -> list[str]:
    """Zwróć wszystkie niespójności aktywnej wersji i schematu."""
    root = root.resolve()
    overrides = dict(document_overrides or {})
    errors: list[str] = []

    manifest_path = root / "hermes/plugins/factory-execution-guards/plugin.yaml"
    try:
        manifest = _parse_plugin_manifest(manifest_path)
        version = manifest.get("version", "")
        if not SEMVER_RE.fullmatch(version):
            raise ValueError(f"invalid semantic version: {version!r}")
    except (OSError, ValueError) as exc:
        return [f"{manifest_path}: cannot parse current plugin version: {exc}"]

    guard_path = root / "hermes/plugins/factory-execution-guards/guard.py"
    try:
        schema = _guard_schema(guard_path)
    except (OSError, SyntaxError, ValueError) as exc:
        return [f"{guard_path}: cannot parse current evidence schema: {exc}"]
    handoff_path = root / "hermes/plugins/factory-execution-guards/handoff.py"
    try:
        handoff_schema = _handoff_schema(handoff_path)
    except (OSError, SyntaxError, ValueError) as exc:
        return [f"{handoff_path}: cannot parse current handoff schema: {exc}"]

    for relative, version_pattern in VERSION_PATTERNS.items():
        path = overrides.get(relative, root / relative)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{relative}: cannot read authoritative document: {exc}")
            continue

        document_version = _single_marker_value(version_pattern, text)
        if document_version is None:
            errors.append(
                f"{relative}: expected exactly one structured current guard version marker"
            )
        elif document_version != version:
            errors.append(
                f"{relative}: current guard version {document_version} != plugin {version}"
            )

        document_schema = _single_marker_value(SCHEMA_PATTERNS[relative], text)
        if document_schema is None:
            errors.append(
                f"{relative}: expected exactly one active evidence schema marker"
            )
        elif int(document_schema) != schema:
            errors.append(
                f"{relative}: current evidence schema {document_schema} != guard {schema}"
            )

        document_handoff_schema = _single_marker_value(
            HANDOFF_SCHEMA_PATTERNS[relative], text
        )
        if document_handoff_schema is None:
            errors.append(
                f"{relative}: expected exactly one active handoff schema marker"
            )
        elif int(document_handoff_schema) != handoff_schema:
            errors.append(
                f"{relative}: current handoff schema {document_handoff_schema} "
                f"!= implementation {handoff_schema}"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    args = parser.parse_args()
    errors = collect_consistency_errors(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    manifest = _parse_plugin_manifest(
        args.root / "hermes/plugins/factory-execution-guards/plugin.yaml"
    )
    schema = _guard_schema(
        args.root / "hermes/plugins/factory-execution-guards/guard.py"
    )
    handoff_schema = _handoff_schema(
        args.root / "hermes/plugins/factory-execution-guards/handoff.py"
    )
    print(
        "EXECUTION_GUARD_VERSION_CONSISTENCY_OK "
        f"version={manifest['version']} evidence_schema={schema} "
        f"handoff_schema={handoff_schema}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
