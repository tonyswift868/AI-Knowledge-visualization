from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath

import pytest

from backend.app.plain_file_copy import PlainFileCopyError
from scripts import build_qwen3_tts_runtime_pack as builder
from scripts import prepare_qwen3_tts_model_materials as materials


GENERATION_ID = "testgen-20260808"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, int, str | None], ...]:
    rows = []
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_file():
            rows.append(
                (relative, "file", metadata.st_size, metadata.st_mtime_ns, _sha(path.read_bytes()))
            )
        else:
            rows.append((relative, "directory", metadata.st_size, metadata.st_mtime_ns, None))
    return tuple(rows)


@pytest.fixture
def model_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    source = tmp_path / "source-checkout"
    trusted: dict[str, tuple[int, str]] = {}
    for index, relative in enumerate(sorted(builder.TRUSTED_MODEL_FILES)):
        content = f"trusted-model-fixture-{index}-{relative}\n".encode("utf-8")
        path = source.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        trusted[relative] = (len(content), _sha(content))

    # A normal Hugging Face checkout has unrelated cache/docs.  Preparation
    # must ignore them without mutating or deleting them.
    (source / ".cache" / "huggingface").mkdir(parents=True)
    (source / ".cache" / "huggingface" / "config.json.metadata").write_text(
        "cache metadata", encoding="utf-8"
    )
    (source / "README.md").write_text("checkout documentation", encoding="utf-8")

    tree = builder.calculate_allowlist_tree_sha256(source, sorted(trusted))
    monkeypatch.setattr(builder, "TRUSTED_MODEL_FILES", trusted)
    monkeypatch.setattr(builder, "TRUSTED_MODEL_TREE_SHA256", tree)
    generation = tmp_path / "materials" / "v1" / "generations" / GENERATION_ID
    return {"source": source, "generation": generation}


def test_prepare_and_verify_curated_generation(model_fixture: dict[str, Path]) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]

    prepared = materials.prepare_model_material(source, generation, GENERATION_ID)
    verified = materials.verify_model_material(generation, GENERATION_ID)

    assert prepared == verified
    assert verified["status"] == "passed"
    assert verified["file_count"] == len(builder.TRUSTED_MODEL_FILES)
    assert verified["tree_sha256"] == builder.TRUSTED_MODEL_TREE_SHA256
    assert (source / ".cache" / "huggingface" / "config.json.metadata").read_text(
        encoding="utf-8"
    ) == "cache metadata"
    model_files = {
        path.relative_to(generation / "model").as_posix()
        for path in (generation / "model").rglob("*")
        if path.is_file()
    }
    assert model_files == set(builder.TRUSTED_MODEL_FILES)
    receipt = json.loads((generation / "evidence" / "SOURCE_RECEIPT.json").read_text("utf-8"))
    assert receipt["cache_metadata_excluded"] is True
    assert receipt["tree_sha256"] == builder.TRUSTED_MODEL_TREE_SHA256


def test_prepare_rejects_missing_source_before_creating_target(
    model_fixture: dict[str, Path],
) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    missing = source.joinpath(*PurePosixPath(next(iter(builder.TRUSTED_MODEL_FILES))).parts)
    missing.unlink()

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_SOURCE_MISSING"
    assert not generation.exists()


def test_prepare_rejects_changed_source_hash_before_creating_target(
    model_fixture: dict[str, Path],
) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    changed = source.joinpath(*PurePosixPath(next(iter(builder.TRUSTED_MODEL_FILES))).parts)
    changed.write_bytes(changed.read_bytes() + b"tampered")

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_SOURCE_MISMATCH"
    assert not generation.exists()


def test_prepare_rejects_hardlinked_source_before_creating_target(
    model_fixture: dict[str, Path], tmp_path: Path
) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    selected = source.joinpath(*PurePosixPath(next(iter(builder.TRUSTED_MODEL_FILES))).parts)
    outside = tmp_path / "hardlink-source.bin"
    selected.replace(outside)
    try:
        os.link(outside, selected)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable on this filesystem: {exc}")

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_PLAIN_FILE_REQUIRED"
    assert not generation.exists()


def test_prepare_rejects_reparse_signal_before_creating_target(
    model_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    selected = source.joinpath(*PurePosixPath(next(iter(builder.TRUSTED_MODEL_FILES))).parts)
    original = materials.assert_no_reparse_ancestors

    def injected(path: Path, boundary: Path) -> None:
        if Path(path) == selected:
            raise PlainFileCopyError("COPY_REPARSE_FORBIDDEN", "injected reparse point")
        original(path, boundary)

    monkeypatch.setattr(materials, "assert_no_reparse_ancestors", injected)

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_REPARSE_FORBIDDEN"
    assert not generation.exists()


def test_prepare_rejects_unsafe_trusted_path(
    model_fixture: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    generation = model_fixture["generation"]
    source = model_fixture["source"]
    trusted = dict(builder.TRUSTED_MODEL_FILES)
    trusted["../escape.bin"] = (1, _sha(b"x"))
    monkeypatch.setattr(builder, "TRUSTED_MODEL_FILES", trusted)

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_PATH_UNSAFE"
    assert not generation.exists()


def test_prepare_never_overwrites_existing_generation(model_fixture: dict[str, Path]) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    generation.mkdir(parents=True)
    marker = generation / "belongs-to-another-run.txt"
    marker.write_bytes(b"preserve me")
    before = marker.stat().st_mtime_ns

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_TARGET_EXISTS"
    assert marker.read_bytes() == b"preserve me"
    assert marker.stat().st_mtime_ns == before
    assert list(generation.iterdir()) == [marker]


def test_prepare_rejects_generation_inside_source_with_zero_writes(
    model_fixture: dict[str, Path],
) -> None:
    source = model_fixture["source"]
    generation = source / GENERATION_ID
    before = _tree_snapshot(source)

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_ROOT_OVERLAP"
    assert not generation.exists()
    assert _tree_snapshot(source) == before


def test_prepare_rejects_source_inside_generation_with_zero_writes(
    model_fixture: dict[str, Path], tmp_path: Path
) -> None:
    original_source = model_fixture["source"]
    generation = tmp_path / "nested-source-case" / GENERATION_ID
    generation.mkdir(parents=True)
    source = generation / "source-checkout"
    original_source.replace(source)
    before = _tree_snapshot(generation)

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_ROOT_OVERLAP"
    assert not (generation / "model").exists()
    assert not (generation / "evidence").exists()
    assert _tree_snapshot(generation) == before


def test_prepare_rejects_equal_source_and_generation_with_zero_writes(
    model_fixture: dict[str, Path],
) -> None:
    source = model_fixture["source"]
    before = _tree_snapshot(source)

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, source, source.name)

    assert caught.value.code == "MODEL_ROOT_OVERLAP"
    assert _tree_snapshot(source) == before


def test_prepare_rejects_case_and_dotdot_alias_with_zero_writes(
    model_fixture: dict[str, Path],
) -> None:
    source = model_fixture["source"]
    source_alias = Path(str(source).swapcase()) if os.name == "nt" else source
    generation = source_alias / "unused-alias-segment" / ".." / GENERATION_ID
    effective_target = source / GENERATION_ID
    before = _tree_snapshot(source)

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.prepare_model_material(source, generation, GENERATION_ID)

    assert caught.value.code == "MODEL_ROOT_OVERLAP"
    assert not effective_target.exists()
    assert _tree_snapshot(source) == before


def test_verify_rejects_model_tamper_without_rewriting_evidence(
    model_fixture: dict[str, Path],
) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    materials.prepare_model_material(source, generation, GENERATION_ID)
    inventory = generation / "evidence" / "MATERIAL_INVENTORY.json"
    before = (inventory.read_bytes(), inventory.stat().st_mtime_ns)
    relative = next(iter(builder.TRUSTED_MODEL_FILES))
    selected = (generation / "model").joinpath(*PurePosixPath(relative).parts)
    selected.write_bytes(selected.read_bytes() + b"tampered")

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.verify_model_material(generation, GENERATION_ID)

    assert caught.value.code == "MODEL_MATERIAL_MISMATCH"
    assert (inventory.read_bytes(), inventory.stat().st_mtime_ns) == before


def test_verify_rejects_evidence_tamper(model_fixture: dict[str, Path]) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    materials.prepare_model_material(source, generation, GENERATION_ID)
    receipt = generation / "evidence" / "SOURCE_RECEIPT.json"
    receipt.write_bytes(receipt.read_bytes() + b" ")

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.verify_model_material(generation, GENERATION_ID)

    assert caught.value.code == "MODEL_EVIDENCE_MISMATCH"


def test_verify_rejects_hardlinked_material(
    model_fixture: dict[str, Path], tmp_path: Path
) -> None:
    source = model_fixture["source"]
    generation = model_fixture["generation"]
    materials.prepare_model_material(source, generation, GENERATION_ID)
    relative = next(iter(builder.TRUSTED_MODEL_FILES))
    selected = (generation / "model").joinpath(*PurePosixPath(relative).parts)
    outside = tmp_path / "material-hardlink-source.bin"
    selected.replace(outside)
    try:
        os.link(outside, selected)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable on this filesystem: {exc}")

    with pytest.raises(materials.ModelMaterialError) as caught:
        materials.verify_model_material(generation, GENERATION_ID)

    assert caught.value.code == "MODEL_PLAIN_FILE_REQUIRED"
