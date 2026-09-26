"""Account-scoped HTTP transport over the existing structured-sync API."""
from __future__ import annotations

import json
import hashlib
import re

import httpx

from .plan_models import PlanError
from .plan_source import AuthenticatedPlanSource, sync_auth_error
from .sync_records.contract import envelope_for


def _resource_response_envelope(payload):
    """Normalize the server's canonical resource projection for the ledger."""
    if (isinstance(payload, dict)
            and payload.get("schema") == "mentorflow.refactor-sync.v1"):
        return payload
    if not isinstance(payload, dict) or not isinstance(payload.get("refactor_record_id"), str):
        return payload
    record_id = payload["refactor_record_id"]
    return envelope_for("resource", record_id, {
        "material_id": record_id,
        "filename": payload.get("filename"),
        "media_type": payload.get("media_type"),
        "size": payload.get("size_bytes"),
        "sha256": payload.get("sha256"),
        "text": payload.get("text", ""),
        "product_classification": payload.get("product_classification", "UNKNOWN"),
    })


class SyncTransport(AuthenticatedPlanSource):
    def _object_headers(self, scope, token):
        return {"Authorization": "Bearer " + token, "Accept": "application/json",
                "X-MentorFlow-Organization-ID": scope.organization_id,
                "X-MentorFlow-Workspace-ID": scope.workspace_id}

    def upload_original(self, sha256: str, data: bytes):
        scope, token = self._session()
        headers = self._object_headers(scope, token)
        headers.update({"Content-Type": "application/octet-stream",
                        "X-MentorFlow-SHA256": sha256, "X-MentorFlow-Size": str(len(data))})
        try:
            with httpx.Client(verify=self._ssl, timeout=60, follow_redirects=False, transport=self._transport) as client:
                response = client.put(self._base_url + f"/api/enterprise/sync/v1/objects/{sha256}", headers=headers, content=data)
            self.require_current()
            if response.status_code in (401, 403):
                raise PlanError("REAUTH_REQUIRED", "资料原件尚未同步，请重新验证账号。")
            if response.status_code != 200:
                raise PlanError("SYNC_OBJECT_UNAVAILABLE", "资料原件暂未同步，稍后会重试。")
            result = response.json()
            if result.get("sha256") != sha256 or result.get("size") != len(data):
                raise PlanError("SYNC_RESPONSE_INVALID", "资料原件同步回执无效。")
        except PlanError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            raise PlanError("SYNC_OBJECT_UNAVAILABLE", "资料原件暂未同步，稍后会重试。") from exc
        finally:
            token = ""; headers.clear()

    def upload_history_blob(self, sha256: str, data: bytes):
        """Upload canonical JSON for an oversized account history record."""
        return self.upload_original(sha256, data)

    def download_original(self, sha256: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        scope, token = self._session()
        headers = self._object_headers(scope, token)
        try:
            with httpx.Client(verify=self._ssl, timeout=60, follow_redirects=False, transport=self._transport) as client:
                response = client.get(self._base_url + f"/api/enterprise/sync/v1/objects/{sha256}", headers=headers)
            self.require_current()
            if response.status_code in (401, 403):
                raise PlanError("REAUTH_REQUIRED", "资料原件尚未下载，请重新验证账号。")
            if response.status_code == 404:
                raise PlanError("SYNC_OBJECT_MISSING", "云端没有这份资料原件。")
            if response.status_code != 200:
                raise PlanError("SYNC_OBJECT_UNAVAILABLE", "资料原件暂时无法下载。")
            length = response.headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) > max_bytes):
                raise PlanError("SYNC_OBJECT_TOO_LARGE", "资料原件超过允许大小。")
            data = response.content
            if len(data) > max_bytes:
                raise PlanError("SYNC_OBJECT_TOO_LARGE", "资料原件超过允许大小。")
            import hashlib
            if hashlib.sha256(data).hexdigest() != sha256:
                raise PlanError("SYNC_OBJECT_INTEGRITY", "资料原件完整性校验失败。")
            return data
        except PlanError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            raise PlanError("SYNC_OBJECT_UNAVAILABLE", "资料原件暂时无法下载。") from exc
        finally:
            token = ""; headers.clear()

    def download_history_blob(self, sha256: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
        return self.download_original(sha256, max_bytes=max_bytes)

    def write(self, kind, entity_id, *, request_id, expected_revision, payload):
        return self._mutate(kind, entity_id, request_id=request_id,
                            expected_revision=expected_revision, payload=payload, deleting=False)

    def delete(self, kind, entity_id, *, request_id, expected_revision):
        """Use the existing CAS tombstone endpoint; no entity is recreated."""
        return self._mutate(kind, entity_id, request_id=request_id,
                            expected_revision=expected_revision, payload={}, deleting=True)

    def _mutate(self, kind, entity_id, *, request_id, expected_revision, payload, deleting):
        if (kind not in {"task", "history", "learning_run", "setting", "resource"}
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", entity_id)
                or type(expected_revision) is not int or expected_revision < 0):
            raise PlanError("SYNC_RECORD_INVALID", "学习记录的同步标识或修订无效。")
        body = dict(request_id=request_id, expected_revision=expected_revision)
        if not deleting:
            body["payload"] = payload
        # T07 caps the inner course payload, inclusive, using canonical UTF-8.
        # All previously supported types retain their existing byte accounting.
        course = (isinstance(payload, dict) and payload.get("schema") == "mentorflow.refactor-sync.v1"
                  and payload.get("record_type") == "course_record")
        encoded = (json.dumps(payload.get("values", {}).get("payload"), ensure_ascii=False,
                              allow_nan=False, sort_keys=True, separators=(",", ":")) if course
                   else json.dumps(payload, ensure_ascii=False, allow_nan=False))
        if len(encoded.encode()) > 256 * 1024:
            raise PlanError("SYNC_RECORD_TOO_LARGE", "单条学习记录超过同步容量，记录仍保留在本机。")
        scope, token = self._session()
        self._scope = scope
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                   "X-MentorFlow-Organization-ID": scope.organization_id,
                   "X-MentorFlow-Workspace-ID": scope.workspace_id}
        try:
            with httpx.Client(verify=self._ssl, timeout=20, follow_redirects=False, transport=self._transport) as client:
                path = f"/api/enterprise/sync/v1/entities/{kind}/{entity_id}" + ("/delete" if deleting else "")
                response = client.request("POST" if deleting else "PUT", self._base_url + path,
                                          headers=headers, json=body)
            self.require_current()
            if response.status_code in (401, 403):
                raise sync_auth_error(response, "学习记录尚未同步，请重新验证账号；本机内容仍保留。")
            if response.status_code == 409:
                raise PlanError("SYNC_CONFLICT", "另一个设备已更新这条记录，将重新读取并保留两侧修改。")
            if response.status_code == 410:
                raise PlanError("SYNC_TOMBSTONED", "云端记录已删除；未用本机旧副本恢复它。")
            if response.status_code != 200:
                raise PlanError("SYNC_UNAVAILABLE", "学习记录暂未同步，已保留本机内容，稍后会重试。")
            result = response.json()
            response_payload = (_resource_response_envelope(result.get("payload"))
                                if isinstance(result, dict) and kind == "resource"
                                else result.get("payload") if isinstance(result, dict) else None)
            if (not isinstance(result, dict) or result.get("kind") != kind or result.get("entity_id") != entity_id
                    or type(result.get("revision")) is not int or result["revision"] != expected_revision + 1
                    or result.get("deleted") is not deleting or response_payload != payload):
                raise PlanError("SYNC_RESPONSE_INVALID", "同步回执与本次记录不一致，未报告同步成功。")
            result = dict(result, payload=response_payload)
            return result
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError("SYNC_UNAVAILABLE", "学习记录暂未同步，已保留本机内容，稍后会重试。") from None
        except (ValueError, TypeError):
            raise PlanError("SYNC_RESPONSE_INVALID", "同步回执无法读取，未报告同步成功。") from None
        finally:
            token = ""
            headers.clear()
