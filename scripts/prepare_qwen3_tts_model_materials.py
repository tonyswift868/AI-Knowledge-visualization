#!/usr/bin/env python3
"""Prepare and independently verify a curated Qwen3-TTS model material root.

The source may be a normal Hugging Face checkout containing cache metadata and
documentation.  Only the built-in, byte-pinned 11-file runtime allowlist is
read.  A preparation run requires a previously nonexistent generation root and
uses no-overwrite plain-file copies.  ``--verify-only`` is read-only.

This command does not download anything, load a model, use CUDA, start a
service, or create release keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.immutable_evidence import (  # noqa: E402
    ImmutableEvidenceError,
    held_safe_ancestors,
    immutable_write,
)
from backend.app.plain_file_copy import (  # noqa: E402
    PlainFileCopyError,
    assert_no_reparse_ancestors,
    copy_plain_file_exact,
)
from backend.app.windows_safe_relative import (  # noqa: E402
    WindowsSafeRelativePathError,
    validate_windows_safe_relative,
    windows_casefold_key,
)
from scripts import build_qwen3_tts_runtime_pack as builder  # noqa: E402


INVENTORY_SCHEMA = "mentorflow.qwen-model-material-inventory.v1"
SOURCE_RECEIPT_SCHEMA = "mentorflow.trusted-huggingface-model-receipt.v1"
GENERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,79}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MODEL_DIRECTORY = "model"
EVIDENCE_DIRECTORY = "evidence"
SOURCE_RECEIPT_NAME = "SOURCE_RECEIPT.json"
INVENTORY_NAME = "MATERIAL_INVENTORY.json"


class ModelMaterialError(RuntimeError):
    """Stable fail-closed error for preparation and verification."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _windows_semantic_parts(path: Path) -> tuple[str, ...]:
    """Return a normalised, case-insensitive Windows identity path.

    ``realpath`` expands existing 8.3 aliases and resolves existing filesystem
    aliases. Reparse points are rejected separately before this helper is
    called; missing destination suffixes are retained lexically.
    """

    resolved = os.path.realpath(os.path.normpath(os.path.abspath(os.fspath(path))))
    value = os.fspath(resolved).replace("/", "\\")
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    normalised = ntpath.normcase(ntpath.normpath(value))
    return tuple(part.casefold() for part in PureWindowsPath(normalised).parts)


def _is_same_or_ancestor(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) <= len(right) and left == right[: len(left)]


def _assert_disjoint_model_roots(source_root: Path, generation_root: Path) -> None:
    """Reject source/output equality or containment before any output write."""

    source_root = _absolute(source_root)
    generation_root = _absolute(generation_root)
    destinations = {
        "generation root": generation_root,
        "model root": generation_root / MODEL_DIRECTORY,
        "evidence root": generation_root / EVIDENCE_DIRECTORY,
    }
    _assert_plain_chain(source_root, label="source root")
    source_parts = _windows_semantic_parts(source_root)
    for label, destination in destinations.items():
        # This rejects an existing junction/symlink alias and checks every
        # existing ancestor without creating a missing destination directory.
        _assert_plain_chain(destination, label=label)
        destination_parts = _windows_semantic_parts(destination)
        if _is_same_or_ancestor(source_parts, destination_parts) or _is_same_or_ancestor(
            destination_parts, source_parts
        ):
            raise ModelMaterialError(
                "MODEL_ROOT_OVERLAP",
                f"source root and {label} must be disjoint under Windows path semantics: "
                f"{source_root} vs {destination}",
            )


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ModelMaterialError(
            "MODEL_FILE_READ_FAILED", f"cannot hash model material file: {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _safe_relative(value: str, *, field: str) -> str:
    try:
        return validate_windows_safe_relative(value).as_posix()
    except WindowsSafeRelativePathError as exc:
        raise ModelMaterialError(
            "MODEL_PATH_UNSAFE", f"{field} is not an exact Windows-safe relative path: {value!r}"
        ) from exc


def _anchor(path: Path) -> Path:
    anchor = Path(path.anchor)
    if not path.is_absolute() or not anchor.anchor:
        raise ModelMaterialError("MODEL_PATH_INVALID", f"path must be absolute: {path}")
    return anchor


def _assert_plain_chain(path: Path, *, label: str) -> None:
    try:
        assert_no_reparse_ancestors(path, _anchor(path))
    except PlainFileCopyError as exc:
        code = (
            "MODEL_REPARSE_FORBIDDEN"
            if exc.code == "COPY_REPARSE_FORBIDDEN"
            else "MODEL_PATH_INVALID"
        )
        raise ModelMaterialError(code, f"{label} path is unsafe: {path}: {exc}") from exc


def _plain_directory(path: Path, *, label: str) -> Path:
    path = _absolute(path)
    _assert_plain_chain(path, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ModelMaterialError(
            "MODEL_DIRECTORY_MISSING", f"{label} is missing: {path}: {exc}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ModelMaterialError(
            "MODEL_DIRECTORY_INVALID", f"{label} must be one plain directory: {path}"
        )
    return path


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(getattr(metadata, "st_nlink", 1) or 1),
        int(metadata.st_mtime_ns),
    )


def _plain_file_record(path: Path, relative: str, *, label: str) -> dict[str, Any]:
    _assert_plain_chain(path, label=label)
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise ModelMaterialError(
            "MODEL_SOURCE_MISSING" if label == "source" else "MODEL_MATERIAL_MISSING",
            f"{label} file is missing: {path}",
        ) from exc
    except OSError as exc:
        raise ModelMaterialError(
            "MODEL_FILE_STAT_FAILED", f"cannot stat {label} file: {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(before.st_mode) or int(getattr(before, "st_nlink", 1) or 1) != 1:
        raise ModelMaterialError(
            "MODEL_PLAIN_FILE_REQUIRED",
            f"{label} must be one regular non-reparse, non-hardlinked file: {path}",
        )
    digest = _sha256_file(path)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ModelMaterialError(
            "MODEL_FILE_CHANGED", f"{label} changed while it was hashed: {path}: {exc}"
        ) from exc
    if _file_identity(after) != _file_identity(before):
        raise ModelMaterialError(
            "MODEL_FILE_CHANGED", f"{label} changed while it was hashed: {path}"
        )
    return {"path": relative, "bytes": int(before.st_size), "sha256": digest}


def _trusted_records(root: Path, *, label: str) -> tuple[list[dict[str, Any]], str]:
    root = _plain_directory(root, label=f"{label} root")
    records: list[dict[str, Any]] = []
    folded: set[str] = set()
    for raw_relative, (expected_bytes, expected_sha256) in sorted(
        builder.TRUSTED_MODEL_FILES.items()
    ):
        relative = _safe_relative(raw_relative, field="trusted model path")
        key = windows_casefold_key(relative)
        if key in folded:
            raise ModelMaterialError(
                "MODEL_TRUST_SET_INVALID", f"trusted model paths collide on Windows: {relative}"
            )
        folded.add(key)
        source = root.joinpath(*PurePosixPath(relative).parts)
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ModelMaterialError(
                "MODEL_PATH_ESCAPE", f"trusted model path escapes its root: {relative}"
            ) from exc
        record = _plain_file_record(source, relative, label=label)
        if (
            record["bytes"] != expected_bytes
            or record["sha256"] != expected_sha256
            or not SHA256.fullmatch(record["sha256"])
        ):
            raise ModelMaterialError(
                "MODEL_SOURCE_MISMATCH" if label == "source" else "MODEL_MATERIAL_MISMATCH",
                f"{label} file differs from the trusted bytes: {relative}",
            )
        records.append(record)
    try:
        tree = builder.calculate_allowlist_tree_sha256(
            root, sorted(builder.TRUSTED_MODEL_FILES)
        )
    except builder.BuildError as exc:
        raise ModelMaterialError(exc.code, str(exc)) from exc
    if tree != builder.TRUSTED_MODEL_TREE_SHA256:
        raise ModelMaterialError(
            "MODEL_SOURCE_MISMATCH" if label == "source" else "MODEL_MATERIAL_MISMATCH",
            f"{label} allowlist tree differs from the trusted tree",
        )
    return records, tree


def _expected_receipt(records: list[dict[str, Any]], tree: str) -> dict[str, Any]:
    return {
        "schema": SOURCE_RECEIPT_SCHEMA,
        "repository": builder.TRUSTED_MODEL_REPOSITORY,
        "revision": builder.TRUSTED_MODEL_REVISION,
        "license": "Apache-2.0",
        "tree_sha256": tree,
        "cache_metadata_excluded": True,
        "files": records,
    }


def _expected_inventory(
    generation_id: str,
    records: list[dict[str, Any]],
    tree: str,
    receipt_raw: bytes,
) -> dict[str, Any]:
    return {
        "schema": INVENTORY_SCHEMA,
        "status": "passed",
        "generation_id": generation_id,
        "repository": builder.TRUSTED_MODEL_REPOSITORY,
        "revision": builder.TRUSTED_MODEL_REVISION,
        "model_root": MODEL_DIRECTORY,
        "selection_policy": "trusted-exact-11-allowlist",
        "file_count": len(records),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "tree_sha256": tree,
        "files": records,
        "source_receipt": {
            "path": f"{EVIDENCE_DIRECTORY}/{SOURCE_RECEIPT_NAME}",
            "bytes": len(receipt_raw),
            "sha256": _sha256_bytes(receipt_raw),
            "schema": SOURCE_RECEIPT_SCHEMA,
        },
    }


def _walk_generation(generation_root: Path) -> tuple[set[str], dict[str, Path]]:
    generation_root = _plain_directory(generation_root, label="generation root")
    directories: set[str] = set()
    files: dict[str, Path] = {}
    folded: dict[str, str] = {}
    for current_raw, child_directories, child_files in os.walk(
        generation_root, topdown=True, followlinks=False
    ):
        current = Path(current_raw)
        _assert_plain_chain(current, label="generation directory")
        for name in tuple(child_directories):
            child = current / name
            _assert_plain_chain(child, label="generation directory")
            try:
                metadata = child.lstat()
            except OSError as exc:
                raise ModelMaterialError(
                    "MODEL_DIRECTORY_INVALID", f"cannot stat generation directory: {child}: {exc}"
                ) from exc
            if not stat.S_ISDIR(metadata.st_mode):
                raise ModelMaterialError(
                    "MODEL_DIRECTORY_INVALID", f"generation child is not a directory: {child}"
                )
            relative = _safe_relative(
                child.relative_to(generation_root).as_posix(), field="generation directory"
            )
            key = windows_casefold_key(relative)
            previous = folded.setdefault(key, relative)
            if previous != relative:
                raise ModelMaterialError(
                    "MODEL_PATH_COLLISION", f"Windows path collision: {previous!r} vs {relative!r}"
                )
            directories.add(relative)
        for name in child_files:
            child = current / name
            relative = _safe_relative(
                child.relative_to(generation_root).as_posix(), field="generation file"
            )
            key = windows_casefold_key(relative)
            previous = folded.setdefault(key, relative)
            if previous != relative:
                raise ModelMaterialError(
                    "MODEL_PATH_COLLISION", f"Windows path collision: {previous!r} vs {relative!r}"
                )
            _plain_file_record(child, relative, label="material")
            files[relative] = child
    return directories, files


def _read_exact_bytes(path: Path, expected: bytes, *, label: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ModelMaterialError(
            "MODEL_EVIDENCE_READ_FAILED", f"cannot read {label}: {path}: {exc}"
        ) from exc
    if raw != expected:
        raise ModelMaterialError(
            "MODEL_EVIDENCE_MISMATCH", f"{label} bytes do not match the canonical evidence"
        )
    return raw


def _validate_generation_id(generation_id: str) -> str:
    if not GENERATION_ID.fullmatch(generation_id):
        raise ModelMaterialError(
            "MODEL_GENERATION_INVALID",
            "generation id must be 8-80 ASCII letters, digits, underscore, or hyphen",
        )
    return generation_id


def verify_model_material(generation_root: Path, generation_id: str) -> dict[str, Any]:
    """Read-only independent verification of one completed generation."""

    generation_id = _validate_generation_id(generation_id)
    generation_root = _absolute(generation_root)
    if generation_root.name != generation_id:
        raise ModelMaterialError(
            "MODEL_GENERATION_PATH_INVALID",
            f"generation root leaf must equal generation id: {generation_root}",
        )
    directories, files = _walk_generation(generation_root)
    expected_directories = {
        MODEL_DIRECTORY,
        f"{MODEL_DIRECTORY}/speech_tokenizer",
        EVIDENCE_DIRECTORY,
    }
    expected_files = {
        *(f"{MODEL_DIRECTORY}/{relative}" for relative in builder.TRUSTED_MODEL_FILES),
        f"{EVIDENCE_DIRECTORY}/{SOURCE_RECEIPT_NAME}",
        f"{EVIDENCE_DIRECTORY}/{INVENTORY_NAME}",
    }
    if directories != expected_directories:
        raise ModelMaterialError(
            "MODEL_DIRECTORY_SET_MISMATCH",
            f"generation directories differ; extra={sorted(directories - expected_directories)}, "
            f"missing={sorted(expected_directories - directories)}",
        )
    if set(files) != expected_files:
        raise ModelMaterialError(
            "MODEL_FILE_SET_MISMATCH",
            f"generation files differ; extra={sorted(set(files) - expected_files)}, "
            f"missing={sorted(expected_files - set(files))}",
        )

    records, tree = _trusted_records(generation_root / MODEL_DIRECTORY, label="material")
    receipt = _expected_receipt(records, tree)
    receipt_raw = _canonical_json(receipt)
    receipt_path = files[f"{EVIDENCE_DIRECTORY}/{SOURCE_RECEIPT_NAME}"]
    _read_exact_bytes(receipt_path, receipt_raw, label="source receipt")
    inventory = _expected_inventory(generation_id, records, tree, receipt_raw)
    inventory_raw = _canonical_json(inventory)
    inventory_path = files[f"{EVIDENCE_DIRECTORY}/{INVENTORY_NAME}"]
    _read_exact_bytes(inventory_path, inventory_raw, label="material inventory")

    return {
        "schema": INVENTORY_SCHEMA,
        "status": "passed",
        "generation_id": generation_id,
        "generation_root": str(generation_root),
        "model_root": str(generation_root / MODEL_DIRECTORY),
        "file_count": len(records),
        "total_bytes": sum(int(record["bytes"]) for record in records),
        "tree_sha256": tree,
        "source_receipt": {
            "path": str(receipt_path),
            "bytes": len(receipt_raw),
            "sha256": _sha256_bytes(receipt_raw),
        },
        "inventory": {
            "path": str(inventory_path),
            "bytes": len(inventory_raw),
            "sha256": _sha256_bytes(inventory_raw),
        },
    }


def _map_copy_error(exc: PlainFileCopyError) -> ModelMaterialError:
    if exc.code == "COPY_TARGET_EXISTS":
        code = "MODEL_COPY_TARGET_EXISTS"
    elif exc.code in {"COPY_REPARSE_FORBIDDEN", "COPY_PATH_ESCAPE"}:
        code = "MODEL_REPARSE_FORBIDDEN"
    elif exc.code == "COPY_PLAIN_FILE_REQUIRED":
        code = "MODEL_PLAIN_FILE_REQUIRED"
    elif exc.code == "COPY_SOURCE_MISMATCH":
        code = "MODEL_SOURCE_MISMATCH"
    else:
        code = "MODEL_COPY_FAILED"
    return ModelMaterialError(code, str(exc))


def prepare_model_material(
    source_root: Path,
    generation_root: Path,
    generation_id: str,
) -> dict[str, Any]:
    """Create a new generation without overwriting any existing path."""

    generation_id = _validate_generation_id(generation_id)
    source_root = _absolute(source_root)
    generation_root = _absolute(generation_root)
    if generation_root.name != generation_id:
        raise ModelMaterialError(
            "MODEL_GENERATION_PATH_INVALID",
            f"generation root leaf must equal generation id: {generation_root}",
        )

    _assert_disjoint_model_roots(source_root, generation_root)
    records, tree = _trusted_records(source_root, label="source")
    _assert_plain_chain(generation_root.parent, label="generation parent")
    try:
        with held_safe_ancestors(generation_root.parent):
            if os.path.lexists(generation_root):
                raise ModelMaterialError(
                    "MODEL_TARGET_EXISTS",
                    f"generation root must not exist before preparation: {generation_root}",
                )
            try:
                os.mkdir(generation_root)
            except FileExistsError as exc:
                raise ModelMaterialError(
                    "MODEL_TARGET_EXISTS",
                    f"generation root was created concurrently: {generation_root}",
                ) from exc
    except ImmutableEvidenceError as exc:
        raise ModelMaterialError(exc.code, str(exc)) from exc

    try:
        with held_safe_ancestors(generation_root):
            model_root = generation_root / MODEL_DIRECTORY
            evidence_root = generation_root / EVIDENCE_DIRECTORY
            os.mkdir(model_root)
            os.mkdir(evidence_root)
            for record in records:
                relative = str(record["path"])
                source = source_root.joinpath(*PurePosixPath(relative).parts)
                destination = model_root.joinpath(*PurePosixPath(relative).parts)
                try:
                    # Hold both directory chains without delete sharing so a
                    # selected source parent or destination parent cannot be
                    # swapped between the plain-path checks and CopyFileW.
                    with held_safe_ancestors(source.parent), held_safe_ancestors(
                        destination.parent
                    ):
                        copy_plain_file_exact(
                            source,
                            destination,
                            expected_bytes=int(record["bytes"]),
                            expected_sha256=str(record["sha256"]),
                            source_root=source_root,
                            destination_root=generation_root,
                        )
                except PlainFileCopyError as exc:
                    raise _map_copy_error(exc) from exc
                except ImmutableEvidenceError as exc:
                    raise ModelMaterialError(exc.code, str(exc)) from exc

            receipt_raw = _canonical_json(_expected_receipt(records, tree))
            try:
                immutable_write(evidence_root / SOURCE_RECEIPT_NAME, receipt_raw)
                inventory_raw = _canonical_json(
                    _expected_inventory(generation_id, records, tree, receipt_raw)
                )
                immutable_write(evidence_root / INVENTORY_NAME, inventory_raw)
            except ImmutableEvidenceError as exc:
                raise ModelMaterialError(exc.code, str(exc)) from exc
    except ImmutableEvidenceError as exc:
        raise ModelMaterialError(exc.code, str(exc)) from exc
    except ModelMaterialError:
        raise
    except OSError as exc:
        raise ModelMaterialError(
            "MODEL_PREPARATION_FAILED",
            f"model material generation failed and was retained for audit: {generation_root}: {exc}",
        ) from exc

    return verify_model_material(generation_root, generation_id)


def _require_e_drive(path: Path, *, label: str) -> Path:
    absolute = _absolute(path)
    if not absolute.is_absolute() or absolute.drive.casefold() != "e:":
        raise ModelMaterialError(
            "MODEL_E_DRIVE_REQUIRED", f"{label} must be an absolute E: path: {path}"
        )
    return absolute


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--generation-root", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        generation_root = _require_e_drive(
            arguments.generation_root, label="generation root"
        )
        if arguments.verify_only:
            result = verify_model_material(generation_root, arguments.generation_id)
        else:
            if arguments.source_root is None:
                raise ModelMaterialError(
                    "MODEL_SOURCE_REQUIRED", "--source-root is required unless --verify-only is used"
                )
            source_root = _require_e_drive(arguments.source_root, label="source root")
            result = prepare_model_material(
                source_root, generation_root, arguments.generation_id
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except ModelMaterialError as exc:
        print(
            json.dumps(
                {"status": "error", "code": exc.code, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
