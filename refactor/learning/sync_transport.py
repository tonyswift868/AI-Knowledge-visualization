"""Account-scoped HTTP transport over the existing structured-sync API."""
from __future__ import annotations

import json
import hashlib
import re
from contextlib import nullcontext

import httpx

from refactor.learning.sync_identity import PlanError
from refactor.learning.sync_source import AuthenticatedPlanSource, SNAPSHOT_PATH, MAX_SNAPSHOT_BYTES, validate_snapshot
from refactor.learning.sync_source import sync_auth_error
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
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # One window-owned pool; credentials remain per-request and are never
        # retained in client defaults. See https://www.python-httpx.org/advanced/clients/
        self._client = httpx.Client(verify=self._ssl, timeout=20,
                                   follow_redirects=False, transport=self._transport)

    def close(self):
        super().close()
        self._client.close()

    def read_snapshot(self):
        scope, token = self._session()
        self._scope = scope
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'X-MentorFlow-Organization-ID': scope.organization_id, 'X-MentorFlow-Workspace-ID': scope.workspace_id}
        try:
            with nullcontext(self._client) as client:
                with client.stream('GET', self._base_url + SNAPSHOT_PATH, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise sync_auth_error(response, '计划读取的登录状态已失效，请使用账号入口重新验证。')
                    if response.status_code != 200:
                        raise PlanError('SOURCE_UNAVAILABLE', '服务器计划接口暂不可用，当前计划尚未确认，请稍后重试。')
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        self.require_current()
                        raw.extend(chunk)
                        if len(raw) > MAX_SNAPSHOT_BYTES:
                            raise PlanError('SNAPSHOT_TOO_LARGE', '服务器计划快照超过既有接口上限，未截断后显示。')
            self.require_current()

            def unique_pairs(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError('duplicate field')
                    value[key] = item
                return value
            snapshot = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
            validate_snapshot(snapshot, scope)
            self.require_current()
            return snapshot
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError('SOURCE_UNAVAILABLE', '计划读取失败，请检查网络或服务器状态后重试。') from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise PlanError('INVALID_SNAPSHOT', '计划服务返回的数据无法验证。') from None
        finally:
            token = ''
            headers.clear()

    def read_changes(self, *, since="0", kind="learning_run", limit=100):
        """Authenticated, bounded page; only an explicit 404 permits fallback."""
        if (not isinstance(since, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", since)
                or int(since) > 2**63 - 1 or kind != "learning_run"
                or type(limit) is not int or not 1 <= limit <= 500):
            raise ValueError("invalid changes cursor, kind or limit")
        scope, token = self._session()
        self._scope = scope
        headers = self._object_headers(scope, token)
        try:
            with nullcontext(self._client) as client:
                with client.stream("GET", self._base_url + "/api/enterprise/sync/v1/changes",
                                   headers=headers, params=dict(since=since, kind=kind, limit=limit)) as response:
                    self.require_current()
                    if response.status_code in (401, 403):
                        raise sync_auth_error(response, "登录已失效，本机内容仍保留，请重新登录。")
                    if response.status_code == 404:
                        raise PlanError("SYNC_CHANGES_UNSUPPORTED", "服务器暂未提供增量同步。")
                    if response.status_code != 200:
                        raise PlanError("SYNC_UNAVAILABLE", "学习记录暂未同步，稍后会重试。")
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65536):
                        raw.extend(chunk)
                        if len(raw) > 160 * 1024 * 1024:
                            raise ValueError("changes page too large")
            self.require_current()
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate JSON key")
                    result[key] = value
                return result
            page = json.loads(raw, object_pairs_hook=unique)
            if (page.get("schema") != "mentorflow.structured-sync-changes.v1"
                    or type(page.get("contract_version")) is not int or page["contract_version"] != 1
                    or page.get("scope") != scope.snapshot_scope() or page.get("kind") != kind
                    or page.get("since") != since or type(page.get("has_more")) is not bool
                    or not isinstance(page.get("entities"), list) or len(page["entities"]) > limit):
                raise ValueError("changes page contract mismatch")
            last = int(since)
            for row in page["entities"]:
                if (not isinstance(row, dict) or row.get("kind") != kind
                        or not isinstance(row.get("entity_id"), str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", row["entity_id"])
                        or type(row.get("revision")) is not int or row["revision"] < 1
                        or type(row.get("deleted")) is not bool or not isinstance(row.get("payload"), dict)
                        or type(row.get("change_seq")) is not int or not last < row["change_seq"] <= 2**63 - 1):
                    raise ValueError("invalid or unordered change")
                last = row["change_seq"]
            if page.get("next_cursor") != str(last) or page["has_more"] and not page["entities"]:
                raise ValueError("invalid next cursor")
            return page
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError("SYNC_UNAVAILABLE", "学习记录暂未同步，稍后会重试。") from None
        except (ValueError, TypeError, AttributeError):
            raise PlanError("SYNC_RESPONSE_INVALID", "增量同步回执无法验证，未推进游标。") from None
        finally:
            token = ""
            headers.clear()

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
            with nullcontext(self._client) as client:
                response = client.put(self._base_url + f"/api/enterprise/sync/v1/objects/{sha256}", headers=headers, content=data, timeout=60)
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
            with nullcontext(self._client) as client:
                response = client.get(self._base_url + f"/api/enterprise/sync/v1/objects/{sha256}", headers=headers, timeout=60)
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
            with nullcontext(self._client) as client:
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
            error = PlanError("SYNC_UNAVAILABLE", "学习记录暂未同步，已保留本机内容，稍后会重试。")
            error.network_unavailable = True
            raise error from None
        except (ValueError, TypeError):
            raise PlanError("SYNC_RESPONSE_INVALID", "同步回执无法读取，未报告同步成功。") from None
        finally:
            token = ""
            headers.clear()
