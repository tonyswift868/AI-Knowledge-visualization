from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from app.sources.document import (
    DocumentEvidenceBuilder,
    DocumentSourceMetadata,
    DocumentSourcePolicy,
    DocumentSourcePreflight,
    build_docling_converter,
    probe_docling_dependency,
)
from app.sources.document.ingestion import (
    DoclingCompileMode,
    DoclingConversionRunner,
    DoclingExecutionEnvironment,
    DoclingRuntimeStatus,
    DocumentBatchRequest,
    DocumentExerciseCandidateStatus,
    DocumentIngestionApplication,
    DocumentIngestionStatus,
    WindowsDoclingCompatibilityPolicy,
    build_document_pipeline_handoff,
)
from app.sources.document.ingestion.worker import _configure_compile_mode


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _approved_artifacts() -> Path:
    value = os.environ.get("MENTORFLOW_TEST_DOCLING_ARTIFACTS")
    if not value:
        pytest.skip("approved local Docling artifact wrapper was not supplied")
    path = Path(value).resolve()
    model = path / "docling-project--docling-layout-heron" / "model.safetensors"
    if not model.is_file():
        pytest.skip("approved local Heron model is unavailable")
    return path


def _create_two_page_pdf(path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    first = document.new_page()
    first.insert_text((72, 72), "MENTORFLOW_PAGE_ONE_ALPHA_20260825")
    first.insert_text((72, 110), "Question: Which code appears on page one?")
    first.insert_text((72, 140), "Answer: ALPHA is the page-one code.")
    second = document.new_page()
    second.insert_text((72, 72), "MENTORFLOW_PAGE_TWO_OMEGA_20260825")
    second.insert_text((72, 110), "Exercise: Identify the page-two code.")
    second.insert_text((72, 140), "Explanation: OMEGA is the page-two code.")
    document.save(path)
    document.close()


class _FakePort:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def submit(self, handoff: Any) -> str:
        self.calls.append(handoff)
        return handoff.handoff_id


def test_execution_environment_is_process_local_utf8_and_offline(tmp_path: Path) -> None:
    keys = (
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_DATASETS_OFFLINE",
        "PIP_NO_INDEX",
    )
    before = {key: os.environ.get(key) for key in keys}
    execution = DoclingExecutionEnvironment(
        python_executable=Path(sys.executable).resolve(),
        scratch_root=tmp_path.resolve(),
    )

    child = execution.child_environment(backend_root=Path(__file__).parents[1])

    assert child["PYTHONUTF8"] == "1"
    assert child["PYTHONIOENCODING"] == "utf-8"
    assert child["HF_HUB_OFFLINE"] == "1"
    assert child["TRANSFORMERS_OFFLINE"] == "1"
    assert child["HF_DATASETS_OFFLINE"] == "1"
    assert child["PIP_NO_INDEX"] == "1"
    assert {key: os.environ.get(key) for key in keys} == before


def test_windows_policy_isolates_only_pdf_and_records_no_msvc_fallback() -> None:
    policy = WindowsDoclingCompatibilityPolicy(
        platform_name="win32",
        msvc_path=None,
    )

    assert policy.requires_isolation((Path("paper.pdf"),)) is True
    assert policy.requires_isolation((Path("notes.md"),)) is False
    assert policy.compile_mode is DoclingCompileMode.DISABLED_UNCOMPILED_HERON
    assert policy.msvc_available is False
    assert policy.fallback_reason == "WINDOWS_MSVC_COMPILER_UNAVAILABLE"


def test_runner_rejects_source_outside_input_root_before_conversion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    runner = DoclingConversionRunner(
        DocumentSourcePolicy(input_root=root.resolve()),
        compatibility_policy=WindowsDoclingCompatibilityPolicy(
            platform_name="win32",
            msvc_path=None,
        ),
    )

    with pytest.raises(ValueError, match="inside input_root"):
        tuple(runner.convert_all((outside,), raises_on_error=False))


def test_compile_disabled_configuration_does_not_change_docling_global_setting(
    tmp_path: Path,
) -> None:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.settings import settings

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    before = settings.inference.compile_torch_models
    converter = build_docling_converter(
        DocumentSourcePolicy(
            input_root=tmp_path.resolve(),
            pdf_artifacts_path=artifacts.resolve(),
        )
    )

    mode = _configure_compile_mode(converter, disabled=True)

    pipeline = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert mode is DoclingCompileMode.DISABLED_UNCOMPILED_HERON
    assert pipeline.layout_options.engine_options.compile_model is False
    assert settings.inference.compile_torch_models is before


def test_real_windows_runner_preserves_raw_docling_page_provenance(
    tmp_path: Path,
) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is unavailable")
    artifacts = _approved_artifacts()
    pdf = tmp_path / "raw-provenance.pdf"
    _create_two_page_pdf(pdf)
    model = artifacts / "docling-project--docling-layout-heron" / "model.safetensors"
    artifact_before = _sha256(model)
    policy = DocumentSourcePolicy(
        input_root=tmp_path.resolve(),
        pdf_artifacts_path=artifacts,
    )
    runner = DoclingConversionRunner(
        policy,
        execution_environment=DoclingExecutionEnvironment(
            python_executable=Path(sys.executable).resolve(),
            scratch_root=(tmp_path / "workers").resolve(),
        ),
    )

    with runner:
        conversion = tuple(runner.convert_all((pdf,), raises_on_error=False))[0]

    assert conversion.status.value == "success"
    assert conversion.document is not None
    raw = {
        item.text: tuple(provenance.page_no for provenance in item.prov)
        for item, _level in conversion.document.iterate_items(with_groups=True)
        if getattr(item, "text", None)
    }
    assert raw["Question: Which code appears on page one?"] == (1,)
    assert raw["Answer: ALPHA is the page-one code."] == (1,)
    assert raw["Exercise: Identify the page-two code."] == (2,)
    assert raw["Explanation: OMEGA is the page-two code."] == (2,)
    runtime = runner.runtime_receipts[0]
    assert runtime.status is DoclingRuntimeStatus.SUCCESS
    assert runtime.process_isolated is True
    assert runtime.utf8_mode is True
    assert runtime.default_encoding.casefold() == "utf-8"
    assert runtime.compile_mode is DoclingCompileMode.DISABLED_UNCOMPILED_HERON
    assert runtime.msvc_available is False
    assert runtime.network_attempts == 0
    assert runtime.model_download_attempts == 0
    assert runtime.artifact_hash_unchanged is True
    assert len(runner.platform_receipts) == 1
    assert runner.platform_receipts[0].clean_shutdown is True
    assert _sha256(model) == artifact_before


def test_real_application_maps_pages_handoff_and_t0_exercises(tmp_path: Path) -> None:
    dependency = probe_docling_dependency()
    if not dependency.available:
        pytest.skip("Docling is unavailable")
    artifacts = _approved_artifacts()
    pdf = tmp_path / "application-provenance.pdf"
    _create_two_page_pdf(pdf)
    port = _FakePort()
    model = artifacts / "docling-project--docling-layout-heron" / "model.safetensors"
    artifact_before = _sha256(model)
    parent_utf8 = os.environ.get("PYTHONUTF8")
    application = DocumentIngestionApplication(
        DocumentSourcePolicy(
            input_root=tmp_path.resolve(),
            pdf_artifacts_path=artifacts,
        ),
        pipeline_port=port,
        execution_environment=DoclingExecutionEnvironment(
            python_executable=Path(sys.executable).resolve(),
            scratch_root=(tmp_path / "workers").resolve(),
        ),
    )

    result = application.ingest(
        DocumentBatchRequest(
            request_id="real-windows-pdf",
            selected_documents=(pdf,),
            conversion_config_identity=(
                f"docling-{dependency.version}:windows-utf8-uncompiled-heron"
            ),
            metadata_by_path={pdf: DocumentSourceMetadata(page_count=2)},
        )
    )

    assert result.receipts[0].status is DocumentIngestionStatus.LIMITED
    evidence_by_content = {item.content: item for item in result.bundles[0].evidence}
    for content in (
        "Question: Which code appears on page one?",
        "Answer: ALPHA is the page-one code.",
    ):
        item = evidence_by_content[content]
        assert tuple(provenance.page_no for provenance in item.provenance) == (1,)
        assert item.to_generic_evidence()["page_start"] == 1
        assert item.to_generic_evidence()["page_end"] == 1
    for content in (
        "Exercise: Identify the page-two code.",
        "Explanation: OMEGA is the page-two code.",
    ):
        item = evidence_by_content[content]
        assert tuple(provenance.page_no for provenance in item.provenance) == (2,)
        assert item.to_generic_evidence()["page_start"] == 2
        assert item.to_generic_evidence()["page_end"] == 2
    assert len(port.calls) == 1
    assert port.calls[0].source_revision == result.bundles[0].source_sha256
    assert len(result.exercise_candidates) == 2
    assert all(
        candidate.status is DocumentExerciseCandidateStatus.T0_CANDIDATE
        for candidate in result.exercise_candidates
    )
    assert {candidate.question_page_numbers for candidate in result.exercise_candidates} == {
        (1,),
        (2,),
    }
    assert {candidate.answer.page_numbers for candidate in result.exercise_candidates} == {
        (1,),
        (2,),
    }
    assert result.runtime_receipts[0].status is DoclingRuntimeStatus.SUCCESS
    assert result.runtime_receipts[0].artifact_hash_unchanged is True
    assert _sha256(model) == artifact_before
    assert os.environ.get("PYTHONUTF8") == parent_utf8


def test_pdf_preflight_still_requires_caller_supplied_local_artifacts(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\nfixture")
    preflight = DocumentSourcePreflight(
        DocumentSourcePolicy(input_root=tmp_path.resolve()),
        dependency_probe=probe_docling_dependency,
    ).inspect(pdf)

    assert preflight.conversion_ready is False
    assert "PDF_LOCAL_ARTIFACTS_UNAVAILABLE" in preflight.limitations
