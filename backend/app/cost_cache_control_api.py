from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.cost.cache_observatory import (
    CacheMetricsSchemaMissing,
    CacheMetricsStore,
    CacheMetricsStoreClosed,
    CacheMetricsStoreCorruption,
    CostCacheReadService,
)
from app.cost.cache_observatory.control_center_delivery import (
    CONTROL_CENTER_DELIVERY_ID,
    CONTROL_CENTER_INTEGRATION_MANIFEST,
)
from app.cost.cache_observatory.demo import build_demo_control_center_fixture
from app.cost.durable_execution import (
    ExecutionSchemaMissing,
    ExecutionStoreClosed,
    ExecutionStoreCorruption,
    SQLiteExecutionStore,
)

router = APIRouter(
    prefix="/api/cost-cache-control-center",
    tags=["cost-cache-control-center"],
)

_DURABLE_ENV = "MENTORFLOW_DURABLE_EXECUTION_DB"
_CACHE_ENV = "MENTORFLOW_CACHE_METRICS_DB"


class MetricsConfigurationError(RuntimeError):
    pass


def _configured_database_path(environment_name: str) -> Path:
    raw = str(os.getenv(environment_name) or "").strip()
    if not raw:
        raise MetricsConfigurationError("required metrics database is not configured")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise MetricsConfigurationError("metrics database path must be absolute")
    resolved = path.resolve(strict=False)
    if not resolved.is_file():
        raise MetricsConfigurationError("configured metrics database is unavailable")
    return resolved


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "COST_CACHE_METRICS_UNAVAILABLE",
            "message": "成本与缓存指标暂时不可用",
        },
    )


@router.get("")
def read_cost_cache_control_center(
    demo: bool = Query(default=False),
    session_id: str | None = Query(default=None, min_length=1, max_length=256),
) -> dict[str, object]:
    if demo:
        payload = build_demo_control_center_fixture().to_public_dict()
        payload["delivery_id"] = CONTROL_CENTER_DELIVERY_ID
        return payload

    execution_store: SQLiteExecutionStore | None = None
    cache_store: CacheMetricsStore | None = None
    try:
        execution_store = SQLiteExecutionStore(
            _configured_database_path(_DURABLE_ENV),
            auto_bootstrap=False,
        )
        cache_store = CacheMetricsStore(
            _configured_database_path(_CACHE_ENV),
            auto_bootstrap=False,
        )
        payload = (
            CostCacheReadService(
                execution_store=execution_store,
                cache_store=cache_store,
            )
            .read(session_id=session_id)
            .to_public_dict()
        )
        payload["delivery_id"] = CONTROL_CENTER_DELIVERY_ID
        return payload
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "COST_CACHE_SESSION_NOT_FOUND",
                "message": "未找到指定的成本会话",
            },
        ) from exc
    except (
        CacheMetricsSchemaMissing,
        CacheMetricsStoreClosed,
        CacheMetricsStoreCorruption,
        ExecutionSchemaMissing,
        ExecutionStoreClosed,
        ExecutionStoreCorruption,
        OSError,
        MetricsConfigurationError,
        ValueError,
    ) as exc:
        raise _unavailable() from exc
    finally:
        if execution_store is not None:
            execution_store.close(checkpoint=False)
        if cache_store is not None:
            cache_store.close()


@router.get("/integration-manifest")
def cost_cache_control_center_integration_manifest() -> dict[str, object]:
    return CONTROL_CENTER_INTEGRATION_MANIFEST.to_public_dict()
