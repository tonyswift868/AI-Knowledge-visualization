from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import httpx
import pytest

from desktop.account_identity import AccountIdentity
from desktop.account_metadata_sync import (
    AccountMetadataSyncError,
    AccountMetadataSyncService,
    ResourceDeviceState,
)
from desktop.api import DesktopApi
from desktop.local_asset_lifecycle import LocalAssetLifecycleError


ISSUER = "https://identity.example.test/realms/mentorflow"
RESOURCE_BYTES = b"canonical account resource revision"
RESOURCE_SHA = hashlib.sha256(RESOURCE_BYTES).hexdigest()


def _identity(subject: str) -> AccountIdentity:
    return AccountIdentity(
        issuer=ISSUER,
        subject=subject,
        display_name=subject,
        organization_id="org-1",
        workspace_id="workspace-1",
    )


def _metadata(subject: str = "account-a") -> dict[str, Any]:
    return {
        "schema_version": "mentorflow.account-core-metadata.v1",
        "context_contract": "enterprise-context.v1",
        "account": {"issuer": ISSUER, "subject_id": subject},
        "tenant": {"organization_id": "org-1", "workspace_id": "workspace-1"},
        "workspace": {
            "workspace_id": "workspace-1",
            "title": "Algebra",
            "status": "ACTIVE",
        },
        "plans": [
            {
                "plan_id": "plan-1",
                "title": "Algebra plan",
                "resource_ids": ["asset-1"],
                "version": 3,
            }
        ],
        "progress": [
            {
                "snapshot_id": "progress-1",
                "plan_id": "plan-1",
                "resource_id": "asset-1",
                "version": 7,
                "mastery": 0.6,
            }
        ],
        "learning_metadata": {
            "evidence_revision": "evidence-4",
            "resource_id": "asset-1",
        },
        "resource_manifest": {
            "schema_version": "mentorflow.asset-manifest.read.v1",
            "assets": [
                {
                    "schema_version": "mentorflow.asset-manifest.read.v1",
                    "asset_id": "asset-1",
                    "asset_revision_id": "asset-revision-1",
                    "local_asset_id": "la-device-1",
                    "filename": "lesson.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": len(RESOURCE_BYTES),
                    "sha256": RESOURCE_SHA,
                    "import_mode": "LINKED",
                    "portability": "NONPORTABLE",
                    "availability": "AVAILABLE",
                }
            ],
        },
    }


class _Availability(str, Enum):
    AVAILABLE_LOCAL = "AVAILABLE_LOCAL"
    MISSING_ON_THIS_DEVICE = "MISSING_ON_THIS_DEVICE"


@dataclass(frozen=True, slots=True)
class _Binding:
    resource_id: str
    fingerprint_sha256: str
    availability: _Availability
    local_path: Path | None


class _LocalBindings:
    """Isolated fixture implementing the existing LocalAssetLifecycle port."""

    def __init__(self) -> None:
        self.bindings: dict[str, _Binding] = {}
        self.calls: list[tuple[str, str]] = []

    def register_missing_resource(
        self,
        resource_id: str,
        *,
        fingerprint_sha256: str,
    ) -> _Binding:
        self.calls.append(("register_missing_resource", resource_id))
        binding = _Binding(
            resource_id=resource_id,
            fingerprint_sha256=fingerprint_sha256,
            availability=_Availability.MISSING_ON_THIS_DEVICE,
            local_path=None,
        )
        self.bindings[resource_id] = binding
        return binding

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def resolve_resource_binding(self, resource_id: str) -> _Binding:
        self.calls.append(("resolve_resource_binding", resource_id))
        binding = self.bindings.get(resource_id)
        if binding is None:
            raise LocalAssetLifecycleError(
                "RESOURCE_BINDING_NOT_FOUND",
                "missing local binding",
            )
        available = (
            binding.local_path is not None
            and binding.local_path.is_file()
            and self._sha256(binding.local_path) == binding.fingerprint_sha256
        )
        resolved = replace(
            binding,
            availability=(
                _Availability.AVAILABLE_LOCAL
                if available
                else _Availability.MISSING_ON_THIS_DEVICE
            ),
        )
        self.bindings[resource_id] = resolved
        return resolved

    def relink_resource(self, resource_id: str, source: Path | str) -> _Binding:
        self.calls.append(("relink_resource", resource_id))
        binding = self.bindings.get(resource_id)
        if binding is None:
            raise LocalAssetLifecycleError(
                "RESOURCE_BINDING_NOT_FOUND",
                "missing local binding",
            )
        source_path = Path(source)
        if not source_path.is_file():
            raise LocalAssetLifecycleError(
                "RESOURCE_SOURCE_UNAVAILABLE",
                "source unavailable",
            )
        if self._sha256(source_path) != binding.fingerprint_sha256:
            raise LocalAssetLifecycleError(
                "RESOURCE_FINGERPRINT_MISMATCH",
                "wrong fingerprint",
            )
        relinked = replace(
            binding,
            availability=_Availability.AVAILABLE_LOCAL,
            local_path=source_path.absolute(),
        )
        self.bindings[resource_id] = relinked
        return relinked


class _Api:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = copy.deepcopy(dict(payload))
        self.metadata_calls = 0
        self.progress_payloads: list[dict[str, Any]] = []

    def account_metadata(self) -> Mapping[str, Any]:
        self.metadata_calls += 1
        return copy.deepcopy(self.payload)

    def put_account_progress(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        self.progress_payloads.append(copy.deepcopy(payload))
        return {**copy.deepcopy(payload), "updated_at": "2026-09-04T00:00:00+00:00"}


def _service(
    *,
    subject: str = "account-a",
    payload: Mapping[str, Any] | None = None,
    bindings: _LocalBindings | None = None,
) -> tuple[AccountMetadataSyncService, _Api, _LocalBindings]:
    api = _Api(payload or _metadata(subject))
    local = bindings or _LocalBindings()
    return (
        AccountMetadataSyncService(
            api,
            local,
            account_identity=_identity(subject),
        ),
        api,
        local,
    )


def test_metadata_sync_hydrates_server_state_and_fresh_device_is_missing() -> None:
    service, api, local = _service()

    snapshot = service.sync()

    assert snapshot.workspace["workspace_id"] == "workspace-1"
    assert snapshot.plans[0]["resource_ids"] == ["asset-1"]
    assert snapshot.progress[0]["resource_id"] == "asset-1"
    assert snapshot.learning_metadata == {
        "evidence_revision": "evidence-4",
        "resource_id": "asset-1",
    }
    assert snapshot.resource("asset-1").state is ResourceDeviceState.MISSING_ON_THIS_DEVICE
    assert local.bindings["asset-1"].local_path is None
    assert local.calls == [
        ("resolve_resource_binding", "asset-1"),
        ("register_missing_resource", "asset-1"),
    ]
    assert api.metadata_calls == 1
    assert api.progress_payloads == []
    projected = snapshot.device_projection()
    assert projected["resource_manifest"]["assets"][0]["device_state"] == (
        "MISSING_ON_THIS_DEVICE"
    )
    assert "local_path" not in json.dumps(projected, sort_keys=True)


def test_missing_existing_file_projects_missing_on_this_device(tmp_path: Path) -> None:
    local = _LocalBindings()
    local.bindings["asset-1"] = _Binding(
        resource_id="asset-1",
        fingerprint_sha256=RESOURCE_SHA,
        availability=_Availability.AVAILABLE_LOCAL,
        local_path=tmp_path / "deleted.pdf",
    )
    service, _api, _local = _service(bindings=local)

    snapshot = service.sync()

    assert snapshot.resource("asset-1").state is ResourceDeviceState.MISSING_ON_THIS_DEVICE
    assert local.bindings["asset-1"].availability is _Availability.MISSING_ON_THIS_DEVICE


def test_wrong_hash_relink_is_rejected_without_binding_change(tmp_path: Path) -> None:
    service, _api, local = _service()
    service.sync()
    before = local.bindings["asset-1"]
    wrong = tmp_path / "wrong.pdf"
    wrong.write_bytes(b"different revision")

    with pytest.raises(AccountMetadataSyncError) as raised:
        service.relink(
            resource_id="asset-1",
            expected_fingerprint_sha256=RESOURCE_SHA,
            selected_file=wrong,
        )

    assert raised.value.code == "RESOURCE_FINGERPRINT_MISMATCH"
    assert local.bindings["asset-1"] == before
    assert service.snapshot is not None
    assert service.snapshot.resource("asset-1").state is (
        ResourceDeviceState.MISSING_ON_THIS_DEVICE
    )


def test_correct_relink_preserves_resource_id_plan_progress_and_learning_metadata(
    tmp_path: Path,
) -> None:
    service, api, local = _service()
    before = service.sync()
    selected = tmp_path / "lesson-relinked.pdf"
    selected.write_bytes(RESOURCE_BYTES)

    result = service.relink(
        resource_id="asset-1",
        expected_fingerprint_sha256=RESOURCE_SHA,
        selected_file=selected,
    )

    assert result.resource.resource_id == "asset-1"
    assert result.resource.state is ResourceDeviceState.RELINKED
    assert result.snapshot.plans == before.plans
    assert result.snapshot.progress == before.progress
    assert result.snapshot.learning_metadata == before.learning_metadata
    assert result.snapshot.server_metadata == before.server_metadata
    assert local.bindings["asset-1"].local_path == selected.absolute()
    assert api.metadata_calls == 1
    assert api.progress_payloads == []
    projection = result.snapshot.device_projection()
    assert projection["resource_manifest"]["assets"][0]["asset_id"] == "asset-1"
    assert projection["resource_manifest"]["assets"][0]["device_state"] == "RELINKED"
    assert str(selected) not in json.dumps(projection, sort_keys=True)


def test_account_local_bindings_are_isolated_between_a_and_b(tmp_path: Path) -> None:
    local_a = _LocalBindings()
    local_b = _LocalBindings()
    service_a, _api_a, _ = _service(subject="account-a", bindings=local_a)
    service_b, _api_b, _ = _service(subject="account-b", bindings=local_b)
    service_a.sync()
    service_b.sync()
    selected = tmp_path / "account-a.pdf"
    selected.write_bytes(RESOURCE_BYTES)

    service_a.relink(
        resource_id="asset-1",
        expected_fingerprint_sha256=RESOURCE_SHA,
        selected_file=selected,
    )

    assert service_b.sync().resource("asset-1").state is (
        ResourceDeviceState.MISSING_ON_THIS_DEVICE
    )
    assert local_b.bindings["asset-1"].local_path is None
    assert service_a.sync().resource("asset-1").state is ResourceDeviceState.AVAILABLE
    assert local_a.bindings["asset-1"].local_path == selected.absolute()


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("local_path", r"C:\\Users\\A\\lesson.pdf", "LOCAL_PATH_FORBIDDEN"),
        ("cache_location", r"D:\\MentorFlow\\cache", "LOCAL_PATH_FORBIDDEN"),
        ("raw_bytes", RESOURCE_BYTES, "RAW_RESOURCE_BYTES_FORBIDDEN"),
    ],
)
def test_sync_rejects_paths_and_raw_bytes_before_local_binding_changes(
    field: str,
    value: Any,
    expected_code: str,
) -> None:
    payload = _metadata()
    payload["resource_manifest"]["assets"][0][field] = value
    service, _api, local = _service(payload=payload)

    with pytest.raises(AccountMetadataSyncError) as raised:
        service.sync()

    assert raised.value.code == expected_code
    assert local.bindings == {}
    assert local.calls == []


def test_progress_push_allows_metadata_only_and_rejects_device_data() -> None:
    service, api, _local = _service()
    progress = {
        "schema_version": "mentorflow.account-progress-snapshot.v1",
        "snapshot_id": "progress-1",
        "version": 8,
        "plan_id": "plan-1",
        "material_id": "asset-1",
        "normalized_name": "algebra",
        "name": "Algebra",
        "mastery": 0.7,
        "completed_task_count": 3,
        "completed_task_minutes": 30,
        "actual_elapsed_minutes": 34.5,
        "completed_phases": ["learn"],
        "status": "ACTIVE",
    }

    response = service.push_progress(progress)

    assert response["version"] == 8
    assert api.progress_payloads == [progress]
    for forbidden in (
        {**progress, "local_path": r"C:\\Users\\A\\lesson.pdf"},
        {**progress, "raw_bytes": RESOURCE_BYTES},
    ):
        with pytest.raises(AccountMetadataSyncError):
            service.push_progress(forbidden)
    assert api.progress_payloads == [progress]


def test_desktop_api_uses_existing_account_metadata_routes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_metadata())
        return httpx.Response(200, json={"snapshot_id": "progress-1", "version": 1})

    api = DesktopApi("https://desktop.example.test")
    api.client.close()
    api.client = httpx.Client(
        base_url=api.origin,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert api.account_metadata()["schema_version"] == (
            "mentorflow.account-core-metadata.v1"
        )
        assert api.put_account_progress({"snapshot_id": "progress-1", "version": 1}) == {
            "snapshot_id": "progress-1",
            "version": 1,
        }
    finally:
        api.close()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/api/enterprise/account/metadata"),
        ("PUT", "/api/enterprise/account/metadata/progress"),
    ]
    assert json.loads(requests[1].content) == {
        "snapshot_id": "progress-1",
        "version": 1,
    }
    assert requests[0].content == b""
