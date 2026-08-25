from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.sources.document import DocumentSourcePolicy
from app.sources.document.ingestion import (
    DoclingConversionRunner,
    DoclingExecutionEnvironment,
    DoclingWorkerExecutionError,
    DocumentConversionWorker,
    DocumentConversionWorkerPool,
    DocumentWorkerState,
    WindowsDoclingCompatibilityPolicy,
)


FIXTURE = Path(__file__).parent / "fixtures" / "document_worker_process.py"


def _environment(tmp_path: Path, *, timeout: float = 2) -> DoclingExecutionEnvironment:
    return DoclingExecutionEnvironment(
        python_executable=Path(sys.executable).resolve(),
        scratch_root=(tmp_path / "workers").resolve(),
        timeout_seconds=timeout,
        startup_timeout_seconds=20,
        shutdown_timeout_seconds=2,
        worker_count=1,
    )


def _compatibility() -> WindowsDoclingCompatibilityPolicy:
    return WindowsDoclingCompatibilityPolicy(
        platform_name="win32",
        isolate_all_document_formats=True,
        msvc_path=None,
    )


def _command(mode: str):
    def build(config_path: Path) -> tuple[str, ...]:
        return (
            str(Path(sys.executable).resolve()),
            "-X",
            "utf8",
            str(FIXTURE),
            "--config",
            str(config_path),
            "--mode",
            mode,
        )

    return build


def test_real_markdown_worker_probes_and_shuts_down_cleanly(tmp_path: Path) -> None:
    source = tmp_path / "lesson.md"
    source.write_text("# Lesson\n\nQuestion: What is local?", encoding="utf-8")
    runner = DoclingConversionRunner(
        DocumentSourcePolicy(input_root=tmp_path.resolve()),
        execution_environment=_environment(tmp_path),
        compatibility_policy=_compatibility(),
    )

    with runner:
        conversion = tuple(runner.convert_all((source,), raises_on_error=False))[0]

    assert conversion.status.value == "success"
    assert conversion.document is not None
    assert len(runner.runtime_receipts) == 1
    runtime = runner.runtime_receipts[0]
    assert runtime.worker_id is not None
    assert runtime.utf8_mode is True
    assert runtime.network_attempts == 0
    assert runtime.model_download_attempts == 0
    assert len(runner.platform_receipts) == 1
    probe = runner.platform_receipts[0]
    assert probe.state is DocumentWorkerState.STOPPED
    assert probe.startup_succeeded is True
    assert probe.clean_shutdown is True
    assert probe.jobs_completed == 1
    assert probe.jobs_failed == 0
    assert not list((tmp_path / "workers").glob("mentorflow-docling-worker-*"))


@pytest.mark.parametrize(
    ("mode", "expected_code", "expected_state"),
    (
        ("crash", "DOCLING_WORKER_CRASHED", DocumentWorkerState.CRASHED),
        ("timeout", "DOCLING_WORKER_TIMEOUT", DocumentWorkerState.TIMED_OUT),
    ),
)
def test_real_process_crash_and_timeout_are_contained(
    tmp_path: Path,
    mode: str,
    expected_code: str,
    expected_state: DocumentWorkerState,
) -> None:
    environment = _environment(tmp_path, timeout=0.2 if mode == "timeout" else 2)
    worker = DocumentConversionWorker(
        DocumentSourcePolicy(input_root=tmp_path.resolve()),
        execution_environment=environment,
        compatibility_policy=_compatibility(),
        worker_index=0,
        command_factory=_command(mode),
    )
    worker.start()

    with pytest.raises(DoclingWorkerExecutionError) as exc_info:
        worker.convert((tmp_path / "source.md",), {"raises_on_error": False})

    assert exc_info.value.code == expected_code
    receipt = worker.close()
    assert receipt.state is expected_state
    assert receipt.clean_shutdown is False
    assert receipt.jobs_started == 1
    assert receipt.jobs_failed == 1
    assert receipt.crashed is (mode == "crash")
    assert receipt.timed_out is (mode == "timeout")
    assert not list((tmp_path / "workers").glob("mentorflow-docling-worker-*"))


def test_startup_rejects_worker_artifact_probe_mismatch(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    model_root = artifacts / "docling-project--docling-layout-heron"
    model_root.mkdir(parents=True)
    (model_root / "model.safetensors").write_bytes(b"local-heron-fixture")
    worker = DocumentConversionWorker(
        DocumentSourcePolicy(
            input_root=tmp_path.resolve(),
            pdf_artifacts_path=artifacts.resolve(),
        ),
        execution_environment=_environment(tmp_path),
        compatibility_policy=_compatibility(),
        worker_index=0,
        command_factory=_command("echo"),
    )

    with pytest.raises(DoclingWorkerExecutionError) as exc_info:
        worker.start()

    assert exc_info.value.code == "DOCLING_WORKER_ARTIFACT_MISMATCH"
    receipt = worker.close()
    assert receipt.state is DocumentWorkerState.FAILED
    assert receipt.clean_shutdown is False
    assert not list((tmp_path / "workers").glob("mentorflow-docling-worker-*"))


def test_pool_replaces_crashed_lane_and_preserves_other_documents(
    tmp_path: Path,
) -> None:
    policy = DocumentSourcePolicy(input_root=tmp_path.resolve())
    environment = _environment(tmp_path)
    compatibility = _compatibility()

    def factory(worker_index: int) -> DocumentConversionWorker:
        return DocumentConversionWorker(
            policy,
            execution_environment=environment,
            compatibility_policy=compatibility,
            worker_index=worker_index,
            command_factory=_command("crash" if worker_index == 0 else "echo"),
        )

    sources = tuple(tmp_path / f"doc-{index}.md" for index in range(4))
    pool = DocumentConversionWorkerPool(worker_count=2, worker_factory=factory)

    outcomes = pool.convert_all(sources, {"raises_on_error": False})
    receipts = pool.close()

    assert outcomes[0].failure_code == "DOCLING_WORKER_CRASHED"
    assert all(outcome.succeeded for outcome in outcomes[1:])
    assert len({outcome.worker_id for outcome in outcomes[1:]}) == 2
    assert any(receipt.state is DocumentWorkerState.CRASHED for receipt in receipts)
    assert sum(receipt.jobs_completed for receipt in receipts) == 3
    assert all(receipt.network_attempts == 0 for receipt in receipts)
    assert all(receipt.model_download_attempts == 0 for receipt in receipts)
    assert not list((tmp_path / "workers").glob("mentorflow-docling-worker-*"))
