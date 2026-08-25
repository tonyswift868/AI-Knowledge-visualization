from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Self

from app.cost.budget import BudgetDecision
from app.cost.contracts import LocalCacheSavingsBaseline
from app.cost.estimation import CostEstimate
from app.cost.execution_v2.contracts import (
    BudgetedOperation,
    ExecutionSessionState,
    OperationExecutionStatus,
)
from app.cost.execution_v2.ports import RouteMetadata
from app.cost.ledger import ActualCostLedger, LedgerIngestionResult, LedgerSnapshot
from app.cost.runtime_budget import (
    BudgetGatePhase,
    BudgetReceipt,
    ExecutionBudgetEnvelope,
    ExecutionBudgetGate,
    UnknownCostPolicy,
)
from app.providers.pricing import ProviderPricingCatalog
from app.providers.pricing_deepseek import DEEPSEEK_PRICING_CATALOG
from app.providers.telemetry import ProviderUsageReceipt

from .contracts import (
    CheckpointReport,
    DurableExecutionStopped,
    DurableIdentityConflict,
    DurableOperationPermission,
    ExecutionSchemaMissing,
    ExecutionSessionRecord,
    ExecutionStoreClosed,
    ExecutionStoreCorruption,
    IntegrityCheckReport,
    OperationRecord,
    OperationState,
    ProviderStartClaim,
    ReceiptAccountingState,
    ReceiptCommitResult,
    ReceiptStageResult,
    RecoveryDecision,
    RecoveryOutcome,
    UsageReceiptRecord,
)
from .serialization import (
    baseline_from_json,
    baseline_to_json,
    canonical_json,
    estimate_from_json,
    estimate_to_json,
    receipt_from_json,
    receipt_payload_hash,
    receipt_payload_json,
)

_SCHEMA_VERSION = 1
_TERMINAL_SESSION_STATES = {
    ExecutionSessionState.STOPPED,
    ExecutionSessionState.CANCELLED,
    ExecutionSessionState.COMPLETED,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(normalized) > 256 or any(ord(character) < 32 for character in normalized):
        raise ValueError(
            f"{name} must be an opaque identifier of at most 256 characters"
        )
    return normalized


def _optional_text(value: object) -> str | None:
    return str(value).strip() or None if value is not None else None


def _json_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    decoded = json.loads(value)
    return tuple(str(item) for item in decoded)


def _remote_windows_drive(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        root = path.anchor
        if not root:
            return False
        return int(ctypes.windll.kernel32.GetDriveTypeW(root)) == 4
    except (AttributeError, OSError, ValueError):
        return False


def _validated_database_path(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value).strip()
    if not raw or raw == ":memory:" or raw.casefold().startswith("file:"):
        raise ValueError("database_path must be an injected local filesystem path")
    if raw.startswith(("\\\\", "//")):
        raise ValueError("network share SQLite paths are not supported")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("database_path must be absolute")
    resolved = path.resolve(strict=False)
    if str(resolved).startswith(("\\\\", "//")) or _remote_windows_drive(resolved):
        raise ValueError("network share SQLite paths are not supported")
    return resolved


class SQLiteExecutionStore:
    """Single-machine durable execution store with serialized writers.

    A connection is opened per operation. WAL permits concurrent readers while
    the application lock deliberately admits one writer at a time. This is not
    a claim of unlimited SQLite write concurrency.
    """

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        busy_timeout_ms: int = 5_000,
        use_wal: bool = True,
        auto_bootstrap: bool = True,
        catalog: ProviderPricingCatalog = DEEPSEEK_PRICING_CATALOG,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        if isinstance(busy_timeout_ms, bool) or int(busy_timeout_ms) < 1:
            raise ValueError("busy_timeout_ms must be a positive integer")
        self._path = _validated_database_path(database_path)
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._use_wal = bool(use_wal)
        self._catalog = catalog
        self._failure_injector = failure_injector
        self._writer_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self._journal_mode: str | None = None
        if auto_bootstrap:
            self.bootstrap_schema()

    @property
    def database_path(self) -> Path:
        return self._path

    @property
    def journal_mode(self) -> str | None:
        return self._journal_mode

    @property
    def busy_timeout_ms(self) -> int:
        return self._busy_timeout_ms

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_open(self) -> None:
        if self._closed:
            raise ExecutionStoreClosed("execution store is closed")

    def _connect(self) -> sqlite3.Connection:
        self._check_open()
        try:
            connection = sqlite3.connect(
                str(self._path),
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except sqlite3.DatabaseError as exc:
            raise ExecutionStoreCorruption(
                "unable to open the durable execution database", original=exc
            ) from exc

    @contextmanager
    def _reader(self, *, require_schema: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if require_schema:
                self._require_schema(connection)
            yield connection
        except sqlite3.DatabaseError as exc:
            raise ExecutionStoreCorruption(
                "durable execution database read failed", original=exc
            ) from exc
        finally:
            connection.close()

    @contextmanager
    def _writer(self, *, require_schema: bool = True) -> Iterator[sqlite3.Connection]:
        with self._writer_lock:
            connection = self._connect()
            try:
                if require_schema:
                    self._require_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise DurableIdentityConflict(str(exc)) from exc
            except sqlite3.DatabaseError as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise ExecutionStoreCorruption(
                    "durable execution database transaction failed", original=exc
                ) from exc
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def _inject(self, phase: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(phase)

    @staticmethod
    def _require_schema(connection: sqlite3.Connection) -> None:
        try:
            row = connection.execute(
                "SELECT value FROM durable_metadata WHERE key='schema_version'"
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).casefold():
                raise ExecutionSchemaMissing(
                    "durable execution schema is missing; bootstrap it explicitly"
                ) from exc
            raise
        if row is None:
            raise ExecutionSchemaMissing("durable execution schema version is missing")
        if int(row["value"]) != _SCHEMA_VERSION:
            raise ExecutionStoreCorruption(
                f"unsupported durable execution schema version: {row['value']}"
            )

    def bootstrap_schema(self) -> None:
        self._check_open()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        statements = (
            """
            CREATE TABLE IF NOT EXISTS durable_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS execution_sessions (
                session_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                currency TEXT NOT NULL,
                user_max_budget TEXT NOT NULL,
                warning_threshold TEXT NOT NULL,
                hard_stop_threshold TEXT NOT NULL,
                unknown_cost_policy TEXT NOT NULL,
                job_start_estimate_json TEXT,
                stop_reason TEXT,
                budget_anomalies_json TEXT NOT NULL DEFAULT '[]',
                ledger_snapshot_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS execution_operations (
                session_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                retry_of_attempt_id TEXT,
                state TEXT NOT NULL,
                chargeable INTEGER NOT NULL,
                provider_called INTEGER,
                routing_available INTEGER NOT NULL,
                selected_provider TEXT,
                selected_model TEXT,
                estimate_json TEXT,
                local_cache_baseline_json TEXT,
                receipt_id TEXT,
                execution_status TEXT,
                error_class TEXT,
                budget_decision TEXT,
                budget_reasons_json TEXT NOT NULL DEFAULT '[]',
                provider_started_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (session_id, attempt_id),
                UNIQUE (session_id, idempotency_key),
                UNIQUE (receipt_id),
                FOREIGN KEY (session_id) REFERENCES execution_sessions(session_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS operation_lineage_idx
            ON execution_operations(session_id, operation_id, retry_of_attempt_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS usage_receipts (
                receipt_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                local_cache_baseline_json TEXT,
                accounting_state TEXT NOT NULL,
                actual_cost TEXT,
                currency TEXT,
                cost_status TEXT,
                unavailable_reason TEXT,
                pricing_version TEXT,
                breakdown_json TEXT,
                staged_at TEXT NOT NULL,
                accounted_at TEXT,
                UNIQUE (session_id, attempt_id),
                FOREIGN KEY (session_id, attempt_id)
                    REFERENCES execution_operations(session_id, attempt_id)
            )
            """,
        )
        with self._writer_lock:
            connection = self._connect()
            try:
                if self._use_wal:
                    self._journal_mode = str(
                        connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    ).upper()
                else:
                    self._journal_mode = str(
                        connection.execute("PRAGMA journal_mode").fetchone()[0]
                    ).upper()
                connection.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    INSERT INTO durable_metadata(key, value) VALUES('schema_version', ?)
                    ON CONFLICT(key) DO NOTHING
                    """,
                    (str(_SCHEMA_VERSION),),
                )
                self._require_schema(connection)
                connection.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise ExecutionStoreCorruption(
                    "durable execution schema bootstrap failed", original=exc
                ) from exc
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def create_session(
        self,
        *,
        session_id: str,
        run_id: str,
        currency: str,
        user_max_budget: Decimal,
        warning_threshold: Decimal,
        hard_stop_threshold: Decimal,
        job_start_estimate: CostEstimate | None,
        unknown_cost_policy: UnknownCostPolicy = UnknownCostPolicy.STOP,
        initial_state: ExecutionSessionState = ExecutionSessionState.ACTIVE,
        stop_reason: str | None = None,
        budget_anomalies: tuple[str, ...] = (),
    ) -> ExecutionSessionRecord:
        session_id = _identifier("session_id", session_id)
        run_id = _identifier("run_id", run_id)
        currency = _identifier("currency", currency)
        for name, value in (
            ("user_max_budget", user_max_budget),
            ("warning_threshold", warning_threshold),
            ("hard_stop_threshold", hard_stop_threshold),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a non-negative Decimal")
        if not warning_threshold <= hard_stop_threshold <= user_max_budget:
            raise ValueError(
                "budget amounts must satisfy warning <= hard stop <= user max"
            )
        if job_start_estimate is not None and job_start_estimate.currency != currency:
            raise ValueError("job estimate currency must match session currency")
        policy = (
            unknown_cost_policy
            if isinstance(unknown_cost_policy, UnknownCostPolicy)
            else UnknownCostPolicy(str(unknown_cost_policy))
        )
        state = (
            initial_state
            if isinstance(initial_state, ExecutionSessionState)
            else ExecutionSessionState(str(initial_state))
        )
        now = _utc_now()
        immutable_signature = (
            run_id,
            currency,
            str(user_max_budget),
            str(warning_threshold),
            str(hard_stop_threshold),
            policy.value,
            estimate_to_json(job_start_estimate),
        )
        with self._writer() as connection:
            existing = connection.execute(
                "SELECT * FROM execution_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if existing is not None:
                existing_signature = (
                    existing["run_id"],
                    existing["currency"],
                    existing["user_max_budget"],
                    existing["warning_threshold"],
                    existing["hard_stop_threshold"],
                    existing["unknown_cost_policy"],
                    existing["job_start_estimate_json"],
                )
                if existing_signature != immutable_signature:
                    raise DurableIdentityConflict(
                        "session_id is already bound to different durable configuration"
                    )
                return self._session_from_row(existing)
            connection.execute(
                """
                INSERT INTO execution_sessions(
                    session_id, run_id, state, currency, user_max_budget,
                    warning_threshold, hard_stop_threshold, unknown_cost_policy,
                    job_start_estimate_json, stop_reason, budget_anomalies_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    *immutable_signature[:1],
                    state.value,
                    *immutable_signature[1:],
                    _optional_text(stop_reason),
                    canonical_json(list(budget_anomalies)),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM execution_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return self._session_from_row(row)

    def get_session(self, session_id: str) -> ExecutionSessionRecord | None:
        normalized = _identifier("session_id", session_id)
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM execution_sessions WHERE session_id=?", (normalized,)
            ).fetchone()
            return self._session_from_row(row) if row is not None else None

    def list_sessions(self) -> tuple[ExecutionSessionRecord, ...]:
        """Return durable sessions without mutating budget or execution state."""

        with self._reader() as connection:
            rows = connection.execute(
                "SELECT * FROM execution_sessions ORDER BY created_at, session_id"
            ).fetchall()
            return tuple(self._session_from_row(row) for row in rows)

    def authorize_operation(
        self,
        *,
        session_id: str,
        operation: BudgetedOperation,
        route: RouteMetadata,
    ) -> DurableOperationPermission:
        session_id = _identifier("session_id", session_id)
        if not isinstance(operation, BudgetedOperation):
            raise TypeError("operation must be BudgetedOperation")
        if not isinstance(route, RouteMetadata):
            raise TypeError("route must be RouteMetadata")
        with self._writer() as connection:
            session_row = self._required_session_row(connection, session_id)
            session = self._session_from_row(session_row)
            existing = connection.execute(
                "SELECT * FROM execution_operations WHERE session_id=? AND attempt_id=?",
                (session_id, operation.attempt_id),
            ).fetchone()
            if existing is not None:
                record = self._operation_from_row(existing)
                self._verify_operation_identity(record, operation, route)
                return DurableOperationPermission(
                    operation=record,
                    budget_receipt=self._evaluate_operation_budget(
                        connection, session, record, BudgetGatePhase.PRE_EXECUTION
                    ),
                    newly_authorized=False,
                )
            if session.state in _TERMINAL_SESSION_STATES:
                raise DurableExecutionStopped(
                    session.stop_reason or f"SESSION_{session.state.value}",
                    session_state=session.state,
                )
            self._validate_retry(connection, session_id, operation)
            provisional = OperationRecord(
                session_id=session_id,
                operation_id=operation.operation_id,
                attempt_id=operation.attempt_id,
                idempotency_key=operation.idempotency_key,
                retry_of_attempt_id=operation.retry_of_attempt_id,
                state=OperationState.AUTHORIZED,
                chargeable=operation.chargeable,
                provider_called=False if operation.local_result_cache_hit else None,
                routing_available=route.routing_available,
                selected_provider=route.selected_provider,
                selected_model=route.selected_model,
                estimate=operation.estimate,
                local_cache_baseline=operation.local_cache_baseline,
                receipt_id=None,
                execution_status=None,
                error_class=None,
                budget_decision=None,
                budget_reasons=(),
                created_at="",
                updated_at="",
            )
            budget = self._evaluate_operation_budget(
                connection, session, provisional, BudgetGatePhase.PRE_EXECUTION
            )
            state = (
                OperationState.STOPPED
                if budget.decision == BudgetDecision.STOP
                else OperationState.AUTHORIZED
            )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO execution_operations(
                    session_id, operation_id, attempt_id, idempotency_key,
                    retry_of_attempt_id, state, chargeable, provider_called,
                    routing_available, selected_provider, selected_model,
                    estimate_json, local_cache_baseline_json, budget_decision,
                    budget_reasons_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    operation.operation_id,
                    operation.attempt_id,
                    operation.idempotency_key,
                    operation.retry_of_attempt_id,
                    state.value,
                    int(operation.chargeable),
                    0 if operation.local_result_cache_hit else None,
                    int(route.routing_available),
                    route.selected_provider,
                    route.selected_model,
                    estimate_to_json(operation.estimate),
                    baseline_to_json(operation.local_cache_baseline),
                    budget.decision.value,
                    canonical_json(list(budget.reasons)),
                    now,
                    now,
                ),
            )
            if budget.decision in {BudgetDecision.WARN, BudgetDecision.STOP}:
                next_state = (
                    ExecutionSessionState.STOPPED
                    if budget.decision == BudgetDecision.STOP
                    else ExecutionSessionState.WARN
                )
                connection.execute(
                    """
                    UPDATE execution_sessions
                    SET state=?, stop_reason=?, budget_anomalies_json=?, updated_at=?
                    WHERE session_id=?
                    """,
                    (
                        next_state.value,
                        ";".join(budget.reasons)
                        if next_state == ExecutionSessionState.STOPPED
                        else session.stop_reason,
                        canonical_json([item.code.value for item in budget.anomalies]),
                        now,
                        session_id,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM execution_operations WHERE session_id=? AND attempt_id=?",
                (session_id, operation.attempt_id),
            ).fetchone()
            return DurableOperationPermission(
                operation=self._operation_from_row(row),
                budget_receipt=budget,
                newly_authorized=True,
            )

    def claim_provider_start(
        self, *, session_id: str, attempt_id: str
    ) -> ProviderStartClaim:
        session_id = _identifier("session_id", session_id)
        attempt_id = _identifier("attempt_id", attempt_id)
        with self._writer() as connection:
            session = self._session_from_row(
                self._required_session_row(connection, session_id)
            )
            row = self._required_operation_row(connection, session_id, attempt_id)
            record = self._operation_from_row(row)
            if not record.chargeable:
                raise ValueError(
                    "a local-cache operation cannot claim a Provider start"
                )
            if record.state == OperationState.PROVIDER_STARTED:
                return ProviderStartClaim(operation=record, claimed=False)
            if record.state != OperationState.AUTHORIZED:
                return ProviderStartClaim(operation=record, claimed=False)
            if session.state in _TERMINAL_SESSION_STATES:
                raise DurableExecutionStopped(
                    session.stop_reason or f"SESSION_{session.state.value}",
                    session_state=session.state,
                )
            now = _utc_now()
            changed = connection.execute(
                """
                UPDATE execution_operations
                SET state=?, provider_called=1, provider_started_at=?, updated_at=?
                WHERE session_id=? AND attempt_id=? AND state=?
                """,
                (
                    OperationState.PROVIDER_STARTED.value,
                    now,
                    now,
                    session_id,
                    attempt_id,
                    OperationState.AUTHORIZED.value,
                ),
            ).rowcount
            row = self._required_operation_row(connection, session_id, attempt_id)
            return ProviderStartClaim(
                operation=self._operation_from_row(row), claimed=changed == 1
            )

    def stage_receipt(
        self,
        *,
        receipt_id: str,
        session_id: str,
        attempt_id: str,
        receipt: ProviderUsageReceipt,
        idempotency_key: str | None = None,
        local_cache_baseline: LocalCacheSavingsBaseline | None = None,
        execution_status: OperationExecutionStatus | None = None,
        error_class: str | None = None,
    ) -> ReceiptStageResult:
        receipt_id = _identifier("receipt_id", receipt_id)
        session_id = _identifier("session_id", session_id)
        attempt_id = _identifier("attempt_id", attempt_id)
        if not isinstance(receipt, ProviderUsageReceipt):
            raise TypeError("receipt must be ProviderUsageReceipt")
        status = (
            execution_status
            if isinstance(execution_status, OperationExecutionStatus)
            else OperationExecutionStatus(str(execution_status))
            if execution_status is not None
            else None
        )
        normalized_error = _optional_text(error_class)
        payload_json = receipt_payload_json(receipt)
        payload_hash = receipt_payload_hash(receipt)
        with self._writer() as connection:
            operation_row = self._required_operation_row(
                connection, session_id, attempt_id
            )
            operation = self._operation_from_row(operation_row)
            key = _identifier(
                "idempotency_key", idempotency_key or operation.idempotency_key
            )
            baseline = local_cache_baseline or operation.local_cache_baseline
            self._validate_receipt_contract(operation, receipt)
            existing = connection.execute(
                "SELECT * FROM usage_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if existing is None:
                existing = connection.execute(
                    "SELECT * FROM usage_receipts WHERE session_id=? AND attempt_id=?",
                    (session_id, attempt_id),
                ).fetchone()
            if existing is not None:
                record = self._receipt_from_row(existing)
                if (
                    record.receipt_id != receipt_id
                    or record.session_id != session_id
                    or record.attempt_id != attempt_id
                    or record.operation_id != operation.operation_id
                    or record.payload_hash != payload_hash
                    or record.idempotency_key != key
                    or record.local_cache_baseline != baseline
                ):
                    raise DurableIdentityConflict(
                        "receipt identity is already bound to different durable data"
                    )
                if (
                    status is not None
                    and operation.execution_status is not None
                    and operation.execution_status != status
                ) or (
                    normalized_error is not None
                    and operation.error_class is not None
                    and operation.error_class != normalized_error
                ):
                    raise DurableIdentityConflict(
                        "staged receipt execution outcome conflicts with durable data"
                    )
                if (status is not None and operation.execution_status is None) or (
                    normalized_error is not None and operation.error_class is None
                ):
                    connection.execute(
                        """
                        UPDATE execution_operations
                        SET execution_status=COALESCE(execution_status, ?),
                            error_class=COALESCE(error_class, ?), updated_at=?
                        WHERE session_id=? AND attempt_id=?
                        """,
                        (
                            status.value,
                            normalized_error,
                            _utc_now(),
                            session_id,
                            attempt_id,
                        ),
                    )
                return ReceiptStageResult(record=record, accepted=False, duplicate=True)
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO usage_receipts(
                    receipt_id, session_id, operation_id, attempt_id, payload_hash,
                    payload_json, idempotency_key, local_cache_baseline_json,
                    accounting_state, staged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    session_id,
                    operation.operation_id,
                    attempt_id,
                    payload_hash,
                    payload_json,
                    key,
                    baseline_to_json(baseline),
                    ReceiptAccountingState.STAGED.value,
                    now,
                ),
            )
            self._inject("STAGE_AFTER_RECEIPT_INSERT")
            connection.execute(
                """
                UPDATE execution_operations
                SET state=?, provider_called=?, receipt_id=?, execution_status=?,
                    error_class=?, updated_at=?
                WHERE session_id=? AND attempt_id=?
                """,
                (
                    OperationState.RECEIPT_STAGED.value,
                    int(receipt.provider_called),
                    receipt_id,
                    status.value if status is not None else None,
                    normalized_error,
                    now,
                    session_id,
                    attempt_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM usage_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            return ReceiptStageResult(
                record=self._receipt_from_row(row), accepted=True, duplicate=False
            )

    def commit_receipt(
        self,
        *,
        receipt_id: str,
        execution_status: OperationExecutionStatus,
        error_class: str | None = None,
    ) -> ReceiptCommitResult:
        receipt_id = _identifier("receipt_id", receipt_id)
        status = (
            execution_status
            if isinstance(execution_status, OperationExecutionStatus)
            else OperationExecutionStatus(str(execution_status))
        )
        with self._writer() as connection:
            receipt_row = connection.execute(
                "SELECT * FROM usage_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if receipt_row is None:
                raise KeyError(f"unknown receipt_id: {receipt_id}")
            durable_receipt = self._receipt_from_row(receipt_row)
            session = self._session_from_row(
                self._required_session_row(connection, durable_receipt.session_id)
            )
            operation = self._operation_from_row(
                self._required_operation_row(
                    connection, durable_receipt.session_id, durable_receipt.attempt_id
                )
            )
            normalized_error = _optional_text(error_class)
            if (
                operation.execution_status is not None
                and operation.execution_status != status
            ) or (
                normalized_error is not None
                and operation.error_class is not None
                and operation.error_class != normalized_error
            ):
                raise DurableIdentityConflict(
                    "staged or accounted receipt outcome cannot be changed"
                )
            effective_error = normalized_error or operation.error_class
            ledger = self._restore_ledger_connection(
                connection,
                session,
                include_receipt_id=(
                    receipt_id
                    if durable_receipt.accounting_state
                    == ReceiptAccountingState.ACCOUNTED
                    else None
                ),
            )
            newly_accounted = (
                durable_receipt.accounting_state == ReceiptAccountingState.STAGED
            )
            if newly_accounted:
                ingestion = ledger.ingest(
                    run_id=session.run_id,
                    operation_id=operation.operation_id,
                    receipt=durable_receipt.receipt,
                    idempotency_key=durable_receipt.idempotency_key,
                    local_cache_baseline=durable_receipt.local_cache_baseline,
                )
            else:
                ingestion = self._existing_ingestion(
                    ledger, session, operation, durable_receipt
                )
            budget = self._evaluate_operation_budget(
                connection,
                session,
                operation,
                BudgetGatePhase.IN_EXECUTION,
                ledger=ledger,
            )
            run_snapshot = ingestion.run_snapshot
            if session.state in _TERMINAL_SESSION_STATES:
                next_session_state = session.state
            elif budget.decision == BudgetDecision.STOP:
                next_session_state = ExecutionSessionState.STOPPED
            elif budget.decision == BudgetDecision.WARN:
                next_session_state = ExecutionSessionState.WARN
            else:
                next_session_state = session.state
            breakdown = ingestion.breakdown
            now = _utc_now()
            if newly_accounted:
                connection.execute(
                    """
                    UPDATE usage_receipts
                    SET accounting_state=?, actual_cost=?, currency=?, cost_status=?,
                        unavailable_reason=?, pricing_version=?, breakdown_json=?,
                        accounted_at=?
                    WHERE receipt_id=? AND accounting_state=?
                    """,
                    (
                        ReceiptAccountingState.ACCOUNTED.value,
                        str(breakdown.total_cost)
                        if breakdown.total_cost is not None
                        else None,
                        breakdown.currency,
                        breakdown.status.value,
                        breakdown.reason.value
                        if breakdown.reason is not None
                        else None,
                        breakdown.pricing_version,
                        canonical_json(
                            {
                                "status": breakdown.status.value,
                                "reason": (
                                    breakdown.reason.value
                                    if breakdown.reason is not None
                                    else None
                                ),
                                "total_cost": (
                                    str(breakdown.total_cost)
                                    if breakdown.total_cost is not None
                                    else None
                                ),
                                "currency": breakdown.currency,
                                "provider_cache_savings": (
                                    str(breakdown.provider_cache_savings)
                                    if breakdown.provider_cache_savings is not None
                                    else None
                                ),
                                "local_cache_savings": (
                                    str(breakdown.local_cache_savings)
                                    if breakdown.local_cache_savings is not None
                                    else None
                                ),
                                "savings_classification": breakdown.savings_classification,
                                "pricing_version": breakdown.pricing_version,
                            }
                        ),
                        now,
                        receipt_id,
                        ReceiptAccountingState.STAGED.value,
                    ),
                )
                self._inject("COMMIT_AFTER_LEDGER_UPDATE")
                connection.execute(
                    """
                    UPDATE execution_operations
                    SET state=?, execution_status=?, error_class=?, budget_decision=?,
                        budget_reasons_json=?, updated_at=?
                    WHERE session_id=? AND attempt_id=?
                    """,
                    (
                        OperationState.ACCOUNTED.value,
                        status.value,
                        effective_error,
                        budget.decision.value,
                        canonical_json(list(budget.reasons)),
                        now,
                        durable_receipt.session_id,
                        durable_receipt.attempt_id,
                    ),
                )
                anomaly_codes = tuple(
                    dict.fromkeys(
                        (
                            *session.budget_anomalies,
                            *(item.code.value for item in budget.anomalies),
                        )
                    )
                )
                stop_reason = (
                    ";".join(budget.reasons)
                    if next_session_state == ExecutionSessionState.STOPPED
                    and session.state != ExecutionSessionState.STOPPED
                    else session.stop_reason
                )
                connection.execute(
                    """
                    UPDATE execution_sessions
                    SET state=?, stop_reason=?, budget_anomalies_json=?,
                        ledger_snapshot_json=?, updated_at=?
                    WHERE session_id=?
                    """,
                    (
                        next_session_state.value,
                        stop_reason,
                        canonical_json(list(anomaly_codes)),
                        self._ledger_snapshot_json(run_snapshot),
                        now,
                        durable_receipt.session_id,
                    ),
                )
            receipt_row = connection.execute(
                "SELECT * FROM usage_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            operation_row = self._required_operation_row(
                connection, durable_receipt.session_id, durable_receipt.attempt_id
            )
            session_row = self._required_session_row(
                connection, durable_receipt.session_id
            )
            return ReceiptCommitResult(
                receipt=self._receipt_from_row(receipt_row),
                operation=self._operation_from_row(operation_row),
                session=self._session_from_row(session_row),
                budget_receipt=budget,
                ledger_snapshot=run_snapshot,
                newly_accounted=newly_accounted,
            )

    def mark_unresolved_provider_attempt(
        self,
        *,
        session_id: str,
        attempt_id: str,
        reason: str,
    ) -> OperationRecord:
        session_id = _identifier("session_id", session_id)
        attempt_id = _identifier("attempt_id", attempt_id)
        reason = _identifier("reason", reason)
        with self._writer() as connection:
            operation = self._operation_from_row(
                self._required_operation_row(connection, session_id, attempt_id)
            )
            if operation.state == OperationState.ACCOUNTED:
                return operation
            now = _utc_now()
            state = (
                OperationState.PROVIDER_STARTED
                if operation.chargeable
                else OperationState.STOPPED
            )
            connection.execute(
                """
                UPDATE execution_operations
                SET state=?, error_class=?, updated_at=?
                WHERE session_id=? AND attempt_id=?
                """,
                (state.value, reason, now, session_id, attempt_id),
            )
            connection.execute(
                """
                UPDATE execution_sessions
                SET state=?, stop_reason=?, updated_at=? WHERE session_id=?
                """,
                (ExecutionSessionState.STOPPED.value, reason, now, session_id),
            )
            return self._operation_from_row(
                self._required_operation_row(connection, session_id, attempt_id)
            )

    def cancel_operation(
        self, *, session_id: str, attempt_id: str, reason: str = "OPERATION_CANCELLED"
    ) -> OperationRecord:
        session_id = _identifier("session_id", session_id)
        attempt_id = _identifier("attempt_id", attempt_id)
        reason = _identifier("reason", reason)
        with self._writer() as connection:
            operation = self._operation_from_row(
                self._required_operation_row(connection, session_id, attempt_id)
            )
            if operation.state == OperationState.ACCOUNTED:
                return operation
            next_state = (
                OperationState.PROVIDER_STARTED
                if operation.state == OperationState.PROVIDER_STARTED
                else OperationState.CANCELLED
            )
            now = _utc_now()
            connection.execute(
                """
                UPDATE execution_operations
                SET state=?, execution_status=?, error_class=?, updated_at=?
                WHERE session_id=? AND attempt_id=?
                """,
                (
                    next_state.value,
                    OperationExecutionStatus.CANCELLED.value,
                    reason,
                    now,
                    session_id,
                    attempt_id,
                ),
            )
            return self._operation_from_row(
                self._required_operation_row(connection, session_id, attempt_id)
            )

    def transition_session(
        self,
        *,
        session_id: str,
        state: ExecutionSessionState,
        reason: str | None = None,
    ) -> ExecutionSessionRecord:
        session_id = _identifier("session_id", session_id)
        state = (
            state
            if isinstance(state, ExecutionSessionState)
            else ExecutionSessionState(str(state))
        )
        with self._writer() as connection:
            current = self._session_from_row(
                self._required_session_row(connection, session_id)
            )
            if current.state in _TERMINAL_SESSION_STATES and state != current.state:
                raise DurableIdentityConflict(
                    "a terminal durable session cannot transition to another state"
                )
            if state == ExecutionSessionState.COMPLETED:
                active_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM execution_operations
                    WHERE session_id=? AND state IN (?, ?, ?)
                    """,
                    (
                        session_id,
                        OperationState.AUTHORIZED.value,
                        OperationState.PROVIDER_STARTED.value,
                        OperationState.RECEIPT_STAGED.value,
                    ),
                ).fetchone()[0]
                if active_count:
                    raise RuntimeError(
                        "cannot complete a durable session with unresolved operations"
                    )
            now = _utc_now()
            connection.execute(
                "UPDATE execution_sessions SET state=?, stop_reason=?, updated_at=? WHERE session_id=?",
                (state.value, _optional_text(reason), now, session_id),
            )
            return self._session_from_row(
                self._required_session_row(connection, session_id)
            )

    def get_operation(
        self, *, session_id: str, attempt_id: str
    ) -> OperationRecord | None:
        session_id = _identifier("session_id", session_id)
        attempt_id = _identifier("attempt_id", attempt_id)
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM execution_operations WHERE session_id=? AND attempt_id=?",
                (session_id, attempt_id),
            ).fetchone()
            return self._operation_from_row(row) if row is not None else None

    def list_operations(self, session_id: str) -> tuple[OperationRecord, ...]:
        session_id = _identifier("session_id", session_id)
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM execution_operations
                WHERE session_id=? ORDER BY created_at, attempt_id
                """,
                (session_id,),
            ).fetchall()
            return tuple(self._operation_from_row(row) for row in rows)

    def get_receipt(self, receipt_id: str) -> UsageReceiptRecord | None:
        receipt_id = _identifier("receipt_id", receipt_id)
        with self._reader() as connection:
            row = connection.execute(
                "SELECT * FROM usage_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            return self._receipt_from_row(row) if row is not None else None

    def list_receipts(self, session_id: str) -> tuple[UsageReceiptRecord, ...]:
        session_id = _identifier("session_id", session_id)
        with self._reader() as connection:
            rows = connection.execute(
                """
                SELECT * FROM usage_receipts
                WHERE session_id=? ORDER BY staged_at, receipt_id
                """,
                (session_id,),
            ).fetchall()
            return tuple(self._receipt_from_row(row) for row in rows)

    def recover_operation(
        self,
        *,
        session_id: str,
        attempt_id: str,
        operation_id: str | None = None,
    ) -> RecoveryDecision:
        session_id = _identifier("session_id", session_id)
        attempt_id = _identifier("attempt_id", attempt_id)
        with self._reader() as connection:
            session_row = connection.execute(
                "SELECT * FROM execution_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            operation_row = connection.execute(
                "SELECT * FROM execution_operations WHERE session_id=? AND attempt_id=?",
                (session_id, attempt_id),
            ).fetchone()
            if operation_row is None:
                if session_row is not None:
                    session = self._session_from_row(session_row)
                    if session.state in _TERMINAL_SESSION_STATES:
                        return RecoveryDecision(
                            session_id,
                            operation_id,
                            attempt_id,
                            None,
                            RecoveryOutcome.STOPPED,
                            session.stop_reason or f"SESSION_{session.state.value}",
                        )
                return RecoveryDecision(
                    session_id,
                    operation_id,
                    attempt_id,
                    None,
                    RecoveryOutcome.RETRY_SAFE,
                    "OPERATION_NOT_AUTHORIZED",
                )
            operation = self._operation_from_row(operation_row)
            if operation_id is not None and operation.operation_id != operation_id:
                raise DurableIdentityConflict(
                    "attempt_id is bound to a different operation_id"
                )
            receipt_row = (
                connection.execute(
                    "SELECT * FROM usage_receipts WHERE receipt_id=?",
                    (operation.receipt_id,),
                ).fetchone()
                if operation.receipt_id is not None
                else None
            )
            if receipt_row is not None:
                receipt = self._receipt_from_row(receipt_row)
                if receipt.accounting_state == ReceiptAccountingState.ACCOUNTED:
                    outcome = RecoveryOutcome.ALREADY_ACCOUNTED
                    reason = "USAGE_RECEIPT_ALREADY_ACCOUNTED"
                else:
                    outcome = RecoveryOutcome.RECEIPT_REPLAY_REQUIRED
                    reason = "DURABLE_RECEIPT_AWAITS_LEDGER_COMMIT"
            elif (
                operation.state == OperationState.PROVIDER_STARTED
                and operation.chargeable
            ):
                outcome = RecoveryOutcome.DO_NOT_RETRY_CHARGEABLE_CALL
                reason = "PROVIDER_MAY_HAVE_CHARGED_WITHOUT_DURABLE_RECEIPT"
            elif (
                session_row is not None
                and self._session_from_row(session_row).state
                in _TERMINAL_SESSION_STATES
            ) or operation.state in {OperationState.STOPPED, OperationState.CANCELLED}:
                outcome = RecoveryOutcome.STOPPED
                reason = (
                    self._session_from_row(session_row).stop_reason
                    if session_row is not None
                    else None
                ) or f"OPERATION_{operation.state.value}"
            else:
                outcome = RecoveryOutcome.RETRY_SAFE
                reason = "AUTHORIZED_WITHOUT_PROVIDER_START"
            return RecoveryDecision(
                session_id=session_id,
                operation_id=operation.operation_id,
                attempt_id=attempt_id,
                receipt_id=operation.receipt_id,
                outcome=outcome,
                reason=reason,
            )

    def restore_ledger(self, session_id: str) -> ActualCostLedger:
        session_id = _identifier("session_id", session_id)
        with self._reader() as connection:
            session = self._session_from_row(
                self._required_session_row(connection, session_id)
            )
            return self._restore_ledger_connection(connection, session)

    def integrity_check(self) -> IntegrityCheckReport:
        self._check_open()
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            rows = connection.execute("PRAGMA integrity_check").fetchall()
            messages = tuple(str(row[0]) for row in rows)
            return IntegrityCheckReport(
                ok=bool(messages)
                and all(message.casefold() == "ok" for message in messages),
                messages=messages,
            )
        except (sqlite3.DatabaseError, ExecutionStoreCorruption) as exc:
            return IntegrityCheckReport(ok=False, messages=(str(exc),))
        finally:
            if connection is not None:
                connection.close()

    def checkpoint(self, mode: str = "PASSIVE") -> CheckpointReport:
        normalized = str(mode or "PASSIVE").strip().upper()
        if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError("unsupported WAL checkpoint mode")
        with self._writer_lock, self._reader() as connection:
            row = connection.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
            return CheckpointReport(
                mode=normalized,
                busy=int(row[0]),
                log_frames=int(row[1]),
                checkpointed_frames=int(row[2]),
            )

    def close(self, *, checkpoint: bool = True) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            if checkpoint and self._path.exists():
                try:
                    self.checkpoint("TRUNCATE")
                except (ExecutionSchemaMissing, ExecutionStoreCorruption):
                    pass
            self._closed = True

    def __enter__(self) -> Self:
        self._check_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> ExecutionSessionRecord:
        return ExecutionSessionRecord(
            session_id=row["session_id"],
            run_id=row["run_id"],
            state=ExecutionSessionState(row["state"]),
            currency=row["currency"],
            user_max_budget=Decimal(row["user_max_budget"]),
            warning_threshold=Decimal(row["warning_threshold"]),
            hard_stop_threshold=Decimal(row["hard_stop_threshold"]),
            unknown_cost_policy=UnknownCostPolicy(row["unknown_cost_policy"]),
            job_start_estimate=estimate_from_json(row["job_start_estimate_json"]),
            stop_reason=row["stop_reason"],
            budget_anomalies=_json_tuple(row["budget_anomalies_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
        return OperationRecord(
            session_id=row["session_id"],
            operation_id=row["operation_id"],
            attempt_id=row["attempt_id"],
            idempotency_key=row["idempotency_key"],
            retry_of_attempt_id=row["retry_of_attempt_id"],
            state=OperationState(row["state"]),
            chargeable=bool(row["chargeable"]),
            provider_called=(
                bool(row["provider_called"])
                if row["provider_called"] is not None
                else None
            ),
            routing_available=bool(row["routing_available"]),
            selected_provider=row["selected_provider"],
            selected_model=row["selected_model"],
            estimate=estimate_from_json(row["estimate_json"]),
            local_cache_baseline=baseline_from_json(row["local_cache_baseline_json"]),
            receipt_id=row["receipt_id"],
            execution_status=(
                OperationExecutionStatus(row["execution_status"])
                if row["execution_status"] is not None
                else None
            ),
            error_class=row["error_class"],
            budget_decision=(
                BudgetDecision(row["budget_decision"])
                if row["budget_decision"] is not None
                else None
            ),
            budget_reasons=_json_tuple(row["budget_reasons_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> UsageReceiptRecord:
        return UsageReceiptRecord(
            receipt_id=row["receipt_id"],
            session_id=row["session_id"],
            operation_id=row["operation_id"],
            attempt_id=row["attempt_id"],
            payload_hash=row["payload_hash"],
            idempotency_key=row["idempotency_key"],
            receipt=receipt_from_json(row["payload_json"]),
            local_cache_baseline=baseline_from_json(row["local_cache_baseline_json"]),
            accounting_state=ReceiptAccountingState(row["accounting_state"]),
            actual_cost=(
                Decimal(row["actual_cost"]) if row["actual_cost"] is not None else None
            ),
            currency=row["currency"],
            cost_status=row["cost_status"],
            unavailable_reason=row["unavailable_reason"],
            pricing_version=row["pricing_version"],
            staged_at=row["staged_at"],
            accounted_at=row["accounted_at"],
        )

    @staticmethod
    def _required_session_row(
        connection: sqlite3.Connection, session_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM execution_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown session_id: {session_id}")
        return row

    @staticmethod
    def _required_operation_row(
        connection: sqlite3.Connection, session_id: str, attempt_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM execution_operations WHERE session_id=? AND attempt_id=?",
            (session_id, attempt_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown attempt_id: {attempt_id}")
        return row

    @staticmethod
    def _verify_operation_identity(
        record: OperationRecord,
        operation: BudgetedOperation,
        route: RouteMetadata,
    ) -> None:
        signature = (
            record.operation_id,
            record.attempt_id,
            record.idempotency_key,
            record.retry_of_attempt_id,
            record.chargeable,
            record.estimate,
            record.local_cache_baseline,
            record.routing_available,
            record.selected_provider,
            record.selected_model,
        )
        proposed = (
            operation.operation_id,
            operation.attempt_id,
            operation.idempotency_key,
            operation.retry_of_attempt_id,
            operation.chargeable,
            operation.estimate,
            operation.local_cache_baseline,
            route.routing_available,
            route.selected_provider,
            route.selected_model,
        )
        if signature != proposed:
            raise DurableIdentityConflict(
                "attempt_id is already bound to different operation data"
            )

    @staticmethod
    def _validate_retry(
        connection: sqlite3.Connection,
        session_id: str,
        operation: BudgetedOperation,
    ) -> None:
        parent_id = operation.retry_of_attempt_id
        if parent_id is None:
            return
        parent_row = connection.execute(
            "SELECT * FROM execution_operations WHERE session_id=? AND attempt_id=?",
            (session_id, parent_id),
        ).fetchone()
        if parent_row is None:
            raise ValueError("retry parent must be a durable attempt")
        parent = SQLiteExecutionStore._operation_from_row(parent_row)
        if parent.operation_id != operation.operation_id:
            raise ValueError("retry parent belongs to a different operation")
        if parent.execution_status in {
            OperationExecutionStatus.SUCCEEDED,
            OperationExecutionStatus.LOCAL_CACHE_HIT,
        }:
            raise ValueError("a successful durable operation cannot be retried")
        if parent.state not in {
            OperationState.ACCOUNTED,
            OperationState.CANCELLED,
            OperationState.STOPPED,
        }:
            raise ValueError("retry parent has not reached a durable terminal state")

    @staticmethod
    def _validate_receipt_contract(
        operation: OperationRecord, receipt: ProviderUsageReceipt
    ) -> None:
        if not operation.chargeable:
            if receipt.provider_called or receipt.local_result_cache_hit is not True:
                raise ValueError(
                    "local cache receipt must report provider_called=false and cache hit"
                )
        else:
            if operation.state not in {
                OperationState.PROVIDER_STARTED,
                OperationState.RECEIPT_STAGED,
                OperationState.ACCOUNTED,
            }:
                raise ValueError(
                    "a chargeable receipt requires a durable Provider-start claim"
                )

    def _restore_ledger_connection(
        self,
        connection: sqlite3.Connection,
        session: ExecutionSessionRecord,
        *,
        include_receipt_id: str | None = None,
    ) -> ActualCostLedger:
        ledger = ActualCostLedger(catalog=self._catalog)
        rows = connection.execute(
            """
            SELECT * FROM usage_receipts
            WHERE session_id=? AND accounting_state=?
            ORDER BY accounted_at, receipt_id
            """,
            (session.session_id, ReceiptAccountingState.ACCOUNTED.value),
        ).fetchall()
        for row in rows:
            record = self._receipt_from_row(row)
            ledger.ingest(
                run_id=session.run_id,
                operation_id=record.operation_id,
                receipt=record.receipt,
                idempotency_key=record.idempotency_key,
                local_cache_baseline=record.local_cache_baseline,
            )
        if include_receipt_id is not None and not any(
            row["receipt_id"] == include_receipt_id for row in rows
        ):
            row = connection.execute(
                "SELECT * FROM usage_receipts WHERE receipt_id=?",
                (include_receipt_id,),
            ).fetchone()
            if row is not None:
                record = self._receipt_from_row(row)
                ledger.ingest(
                    run_id=session.run_id,
                    operation_id=record.operation_id,
                    receipt=record.receipt,
                    idempotency_key=record.idempotency_key,
                    local_cache_baseline=record.local_cache_baseline,
                )
        return ledger

    def _evaluate_operation_budget(
        self,
        connection: sqlite3.Connection,
        session: ExecutionSessionRecord,
        operation: OperationRecord,
        phase: BudgetGatePhase,
        *,
        ledger: ActualCostLedger | None = None,
    ) -> BudgetReceipt:
        active_ledger = ledger or self._restore_ledger_connection(connection, session)
        envelope = ExecutionBudgetEnvelope(
            run_id=session.run_id,
            operation_id=operation.operation_id,
            currency=session.currency,
            user_max_budget=session.user_max_budget,
            warning_threshold=session.warning_threshold,
            hard_stop_threshold=session.hard_stop_threshold,
            estimate=operation.estimate,
            provider_call_required=operation.chargeable,
            routing_available=operation.routing_available,
            selected_provider=operation.selected_provider,
            selected_model=operation.selected_model,
            local_result_cache_hit=not operation.chargeable,
            unknown_cost_policy=session.unknown_cost_policy,
            phase=phase,
        )
        return ExecutionBudgetGate().evaluate(envelope, active_ledger)

    @staticmethod
    def _existing_ingestion(
        ledger: ActualCostLedger,
        session: ExecutionSessionRecord,
        operation: OperationRecord,
        receipt: UsageReceiptRecord,
    ) -> LedgerIngestionResult:
        return ledger.ingest(
            run_id=session.run_id,
            operation_id=operation.operation_id,
            receipt=receipt.receipt,
            idempotency_key=receipt.idempotency_key,
            local_cache_baseline=receipt.local_cache_baseline,
        )

    @staticmethod
    def _ledger_snapshot_json(snapshot: LedgerSnapshot) -> str:
        return canonical_json(
            {
                "run_id": snapshot.run_id,
                "operation_count": snapshot.operation_count,
                "receipt_count": snapshot.receipt_count,
                "provider_calls": snapshot.provider_calls,
                "unknown_receipts": snapshot.unknown_receipts,
                "unpriced_receipts": snapshot.unpriced_receipts,
                "known_costs": [
                    {"currency": item.currency, "amount": str(item.amount)}
                    for item in snapshot.known_costs
                ],
                "actual_cost_complete": snapshot.actual_cost_complete,
                "provider_cache_savings": str(snapshot.provider_cache_savings),
                "local_cache_savings": str(snapshot.local_cache_savings),
                "estimated_savings": str(snapshot.estimated_savings),
                "savings_classification": snapshot.savings_classification,
                "pricing_versions": list(snapshot.pricing_versions),
            }
        )
