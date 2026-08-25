from __future__ import annotations

import sys
import time
from pathlib import Path

from app.sources.document import DocumentSourcePolicy
from app.sources.document.ingestion import (
    DoclingExecutionEnvironment,
    DocumentConversionWorker,
    DocumentConversionWorkerPool,
    DocumentWorkerPoolBenchmarkObservation,
    WindowsDoclingCompatibilityPolicy,
    run_document_worker_pool_benchmark,
)


FIXTURE = Path(__file__).parent / "fixtures" / "document_worker_process.py"


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


def test_100_document_pool_benchmark_covers_1_2_4_workers_crash_and_replay(
    tmp_path: Path,
) -> None:
    policy = DocumentSourcePolicy(input_root=tmp_path.resolve())
    compatibility = WindowsDoclingCompatibilityPolicy(
        platform_name="win32",
        isolate_all_document_formats=True,
        msvc_path=None,
    )

    def run_case(
        worker_count: int,
        document_count: int,
    ) -> DocumentWorkerPoolBenchmarkObservation:
        scratch_root = (tmp_path / f"workers-{worker_count}").resolve()
        environment = DoclingExecutionEnvironment(
            python_executable=Path(sys.executable).resolve(),
            scratch_root=scratch_root,
            timeout_seconds=2,
            startup_timeout_seconds=10,
            shutdown_timeout_seconds=2,
            worker_count=worker_count,
        )

        def primary_factory(worker_index: int) -> DocumentConversionWorker:
            return DocumentConversionWorker(
                policy,
                execution_environment=environment,
                compatibility_policy=compatibility,
                worker_index=worker_index,
                command_factory=_command(
                    "crash" if worker_index == 0 else "echo"
                ),
            )

        sources = tuple(tmp_path / f"document-{index}.md" for index in range(document_count))
        started = time.perf_counter()
        pool = DocumentConversionWorkerPool(
            worker_count=worker_count,
            worker_factory=primary_factory,
        )
        outcomes = pool.convert_all(sources, {"raises_on_error": False})
        receipts = pool.close()
        failures = tuple(outcome.source for outcome in outcomes if not outcome.succeeded)

        replay_environment = DoclingExecutionEnvironment(
            python_executable=Path(sys.executable).resolve(),
            scratch_root=scratch_root,
            timeout_seconds=2,
            startup_timeout_seconds=10,
            shutdown_timeout_seconds=2,
            worker_count=1,
        )

        def replay_factory(worker_index: int) -> DocumentConversionWorker:
            return DocumentConversionWorker(
                policy,
                execution_environment=replay_environment,
                compatibility_policy=compatibility,
                worker_index=1000 + worker_index,
                command_factory=_command("echo"),
            )

        replay_pool = DocumentConversionWorkerPool(
            worker_count=1,
            worker_factory=replay_factory,
        )
        replayed = replay_pool.convert_all(
            failures,
            {"raises_on_error": False},
        )
        replay_receipts = replay_pool.close()
        all_receipts = (*receipts, *replay_receipts)
        succeeded = sum(outcome.succeeded for outcome in outcomes)
        replay_success = sum(outcome.succeeded for outcome in replayed)
        return DocumentWorkerPoolBenchmarkObservation(
            elapsed_seconds=max(0.0, time.perf_counter() - started),
            succeeded_documents=succeeded,
            failed_documents=len(failures),
            replayed_documents=replay_success,
            completed_after_replay=succeeded + replay_success,
            peak_active_workers=worker_count,
            network_attempts=sum(
                receipt.network_attempts for receipt in all_receipts
            ),
            model_download_attempts=sum(
                receipt.model_download_attempts for receipt in all_receipts
            ),
        )

    report = run_document_worker_pool_benchmark(run_case)

    assert [sample.worker_count for sample in report.samples] == [1, 2, 4]
    assert all(sample.document_count == 100 for sample in report.samples)
    assert all(sample.succeeded_documents == 99 for sample in report.samples)
    assert all(sample.failed_documents == 1 for sample in report.samples)
    assert all(sample.replayed_documents == 1 for sample in report.samples)
    assert all(sample.completed_after_replay == 100 for sample in report.samples)
    assert all(sample.failure_isolated for sample in report.samples)
    assert all(sample.bounded for sample in report.samples)
    assert all(sample.network_attempts == 0 for sample in report.samples)
    assert all(sample.model_download_attempts == 0 for sample in report.samples)
    assert not list(tmp_path.rglob("mentorflow-docling-worker-*"))
