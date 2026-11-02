"""Find requirement and bug sections that mention a source file.

Only direct text mentions are returned. The surrounding numbered heading is
used as the owning reference, so unrelated numbered mentions elsewhere in a
document do not become file links.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable


HEADING = re.compile(r"^#{2,6}\s+\[([A-Z][A-Z0-9]*-[0-9]+)\]")
NUMBER = re.compile(r"\[([A-Z][A-Z0-9]*-[0-9]+)\]")


def _normal_path(value: str) -> str:
    return value.replace("\\", "/").strip("`'\" ").casefold().lstrip("./")


def build_references(
    snapshot_root: Path,
    paths: list[str],
    document_paths: list[str],
    common_names: list[str],
) -> dict[str, list[dict[str, object]]]:
    """Map each source path to numbered documentation sections citing it.

    Full relative paths always count. A basename match also counts when it is
    distinctive (not listed in ``common_names``). Results preserve the exact
    evidence line and its one-based line number for later manual checking.
    """
    root = Path(snapshot_root)
    common = {name.casefold() for name in common_names}
    basename_counts: dict[str, int] = {}
    for source in paths:
        basename = _normal_path(source).rsplit("/", 1)[-1]
        basename_counts[basename] = basename_counts.get(basename, 0) + 1
    documents: list[str] = []
    for document in document_paths:
        file_path = root / Path(document)
        if not file_path.is_file():
            continue
        documents.append(document.replace("\\", "/"))

    source_by_norm = {_normal_path(source): source for source in paths}
    unique_basenames: dict[str, str] = {}
    for source in paths:
        source_norm = _normal_path(source)
        basename = source_norm.rsplit("/", 1)[-1]
        if basename not in common and basename_counts[basename] == 1:
            unique_basenames[basename] = source

    def combined_pattern(values: Iterable[str]) -> re.Pattern[str] | None:
        alternatives = sorted(set(values), key=len, reverse=True)
        if not alternatives:
            return None
        return re.compile(
            r"(?<![\w.-])(?P<value>" + "|".join(re.escape(value) for value in alternatives) + r")(?![\w.-])",
            re.IGNORECASE,
        )

    full_pattern = combined_pattern(source_by_norm)
    basename_pattern = combined_pattern(unique_basenames)
    results: dict[str, list[dict[str, object]]] = {source: [] for source in paths}
    seen: dict[str, set[tuple[str, str, int, str]]] = {source: set() for source in paths}
    for document in documents:
        current_owner: str | None = None
        with (root / Path(document)).open("r", encoding="utf-8-sig", errors="replace") as stream:
            for line_no, raw_line in enumerate(stream, 1):
                heading = HEADING.match(raw_line)
                if heading:
                    current_owner = heading.group(1)
                    continue
                if current_owner is None:
                    continue
                text = raw_line.rstrip("\r\n")
                normalized_text = text.replace("\\", "/")
                cited: set[str] = set()
                if full_pattern:
                    for match in full_pattern.finditer(normalized_text):
                        source = source_by_norm.get(match.group("value").casefold())
                        if source:
                            cited.add(source)
                if basename_pattern:
                    for match in basename_pattern.finditer(normalized_text):
                        source = unique_basenames.get(match.group("value").casefold())
                        if source:
                            cited.add(source)
                if not cited:
                    continue
                inline_ids = [item for item in NUMBER.findall(text) if item != current_owner]
                rows = [(current_owner, "section"), *((item, "inline") for item in inline_ids)]
                for source in cited:
                    for reference_id, relation in rows:
                        key = (reference_id, relation, line_no, text.strip())
                        if key in seen[source]:
                            continue
                        seen[source].add(key)
                        results[source].append({
                            "id": reference_id,
                            "owner": current_owner,
                            "relation": relation,
                            "document": document,
                            "line": line_no,
                            "match": text.strip(),
                        })
    return results
