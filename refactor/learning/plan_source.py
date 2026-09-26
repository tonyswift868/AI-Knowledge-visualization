"""Read the existing authenticated snapshot without restoring the old runtime.

The account shell remains the sole token/refresh owner. session_provider returns
its current validated login result, or None. Every completion rechecks the same
account and tenant; close() fences late responses after switch/logout.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import ssl
from threading import Event
from urllib.parse import quote, urlsplit

import httpx

from .plan_models import AccountPlanScope, PlanError, PlanScope, project_snapshot

SNAPSHOT_PATH = "/api/enterprise/sync/v1/snapshot"
KNOWLEDGE_MAP_PATH = "/api/active-teaching/workspaces/{workspace_id}/knowledge-map"
KNOWLEDGE_MAP_JOBS_PATH = "/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs"
KNOWLEDGE_MAP_JOB_PATH = KNOWLEDGE_MAP_JOBS_PATH + "/{job_id}"
KNOWLEDGE_MAP_ACTIVATE_PATH = KNOWLEDGE_MAP_PATH + "/revisions/{revision}/activate"
MAX_SNAPSHOT_BYTES = 160 * 1024 * 1024
MAX_KNOWLEDGE_MAP_BYTES = 16 * 1024 * 1024
PLANNING_ASSISTANT_PATH = "/api/active-teaching/workspaces/{workspace_id}/planning-assistant"
MAX_PLANNING_ASSISTANT_BYTES = 4 * 1024 * 1024


def validate_planning_assistant_response(payload, workspace_id):
    """Project only the non-mutating, user-reviewable assistant response."""
    try:
        if (not isinstance(payload, dict) or payload.get("workspace_id") != workspace_id
                or not isinstance(payload.get("reply"), str)
                or not payload["reply"].strip() or len(payload["reply"]) > 6000):
            raise ValueError()
        summary = payload.get("goal_summary", "")
        questions = payload.get("missing_questions", [])
        if (not isinstance(summary, str) or len(summary) > 1200
                or not isinstance(questions, list) or len(questions) > 20
                or any(not isinstance(item, str) or len(item) > 500 for item in questions)):
            raise ValueError()
        raw_queries = payload.get("queries", payload.get("search_queries", []))
        if raw_queries is None:
            raw_queries = []
        if not isinstance(raw_queries, list) or len(raw_queries) > 8:
            raise ValueError()
        queries = []
        for item in raw_queries:
            if not isinstance(item, dict):
                raise ValueError()
            query, reason = item.get("query"), item.get("reason", "")
            if (not isinstance(query, str) or not query.strip() or len(query) > 500
                    or not isinstance(reason, str) or len(reason) > 500):
                raise ValueError()
            queries.append({"query": query.strip(), "reason": reason.strip()})
        return {"reply": payload["reply"].strip(), "goal_summary": summary.strip(),
                "missing_questions": list(questions), "queries": queries}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PlanError("INVALID_PLANNING_ASSISTANT_RESPONSE", "规划助手返回的数据无法验证，未写入计划。") from None


def validate_knowledge_map(graph, scope):
    """Validate the authenticated workspace graph without synthesizing labels."""
    try:
        if (not isinstance(graph, dict)
                or graph.get("schema") != "mentorflow.knowledge-map.v1"
                or graph.get("workspace_id") != scope.workspace_id
                or not isinstance(graph.get("nodes"), list)
                or not isinstance(graph.get("edges"), list)):
            raise ValueError()
        nodes = graph["nodes"]
        edges = graph["edges"]
        node_ids = set()
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError()
            node_id, name = node.get("id"), node.get("name")
            if (not isinstance(node_id, str) or not node_id.strip()
                    or node_id in node_ids or not isinstance(name, str) or not name.strip()):
                raise ValueError()
            node_ids.add(node_id)
        edge_ids = set()
        for edge in edges:
            if not isinstance(edge, dict):
                raise ValueError()
            edge_id = edge.get("id")
            source, target = edge.get("source_id"), edge.get("target_id")
            if (not isinstance(edge_id, str) or not edge_id.strip() or edge_id in edge_ids
                    or source not in node_ids or target not in node_ids):
                raise ValueError()
            edge_ids.add(edge_id)
        revision = graph.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, (str, int)):
            raise ValueError()
        if isinstance(revision, str) and not revision.strip():
            raise ValueError()
        return graph
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PlanError("INVALID_KNOWLEDGE_MAP", "服务器返回的知识地图无法验证，未显示未确认的数据。") from None


def validate_knowledge_map_job(payload, scope):
    """Validate the durable generation job envelope before exposing it to UI.

    A job is deliberately separate from a map revision: a queued/failed job
    must never be rendered as if it were an active graph.  Keep the provider
    session and source identifiers as metadata only; credentials are never
    returned to callers.
    """
    try:
        if (not isinstance(payload, dict)
                or payload.get("workspace_id") != scope.workspace_id
                or not isinstance(payload.get("id"), str)
                or not payload["id"].strip()
                or not isinstance(payload.get("status"), str)
                or not payload["status"].strip()):
            raise ValueError()
        material_ids = payload.get("material_ids", [])
        if (not isinstance(material_ids, list) or len(material_ids) > 20
                or any(not isinstance(value, str) or not value.strip() for value in material_ids)
                or len(set(material_ids)) != len(material_ids)):
            raise ValueError()
        progress = payload.get("progress", {})
        if not isinstance(progress, dict):
            raise ValueError()
        if "percent" in progress:
            percent = progress["percent"]
            if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
                raise ValueError()
        blockers = payload.get("activation_blockers", [])
        if not isinstance(blockers, list) or len(blockers) > 200 or any(not isinstance(item, dict) for item in blockers):
            raise ValueError()
        return payload
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PlanError("INVALID_KNOWLEDGE_MAP_JOB", "服务器返回的知识地图任务无法验证。") from None


def validate_snapshot(snapshot, scope):
    """Validate provenance independently of the old learning-ready label.

    Unknown/partially supported historical formats remain in the snapshot.
    They must not block supported records or be mistaken for an empty account.
    """
    try:
        if (snapshot.get("schema") != "mentorflow.structured-sync.v1"
                or snapshot.get("contract_version") != 3
                or snapshot.get("complete") is not True
                or snapshot.get("snapshot_authority") != "POSTGRESQL_SINGLE_STATEMENT_MVCC"
                or snapshot.get("snapshot_scope") != scope.snapshot_scope()
                or not re.fullmatch(r"pg-snapshot:\d+:\d+:(?:\d+(?:,\d+)*)?", snapshot.get("snapshot_revision", ""))
                or snapshot["observation"].get("source") != "SERVER_CONFIRMED"
                or snapshot["observation"].get("snapshot_revision") != snapshot["snapshot_revision"]):
            raise ValueError()
        seen = set()
        if not isinstance(snapshot["entities"], list):
            raise ValueError()
        for entity in snapshot["entities"]:
            key = (entity["kind"], entity["entity_id"])
            if (key in seen or not all(isinstance(v, str) and v for v in key)
                    or type(entity["revision"]) is not int or entity["revision"] < 1
                    or type(entity["deleted"]) is not bool or not isinstance(entity["payload"], dict)):
                raise ValueError()
            seen.add(key)
        return snapshot
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PlanError("SCOPE_OR_AUTHORITY_MISMATCH", "学习记录的账号、空间或完整性不匹配，已停止同步。") from None


def authenticated_account_scope(context):
    """Account-only check shared by local planning; never tests learning readiness."""
    def value(obj, name):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    try:
        result = context["session_provider"]()
        identity, state = value(result, "identity"), value(result, "server_state")
        issuer, subject = value(identity, "issuer"), value(identity, "subject")
        account = value(identity, "account_id")
        scope = AccountPlanScope(account, issuer, subject)
        if (account != context["account_id"] or state["authenticated"] is not True
                or state["account"]["account_id"] != account
                or state["account"]["subject_id"] != subject):
            raise ValueError()
        return scope
    except (AttributeError, KeyError, TypeError, ValueError):
        raise PlanError(
            "SESSION_UNAVAILABLE",
            "当前账号或空间正在更新或已切换，请稍后重试；当前账号数据未被替换。",
        ) from None


class AuthenticatedPlanSource:
    def __init__(self, *, account_id, session_provider, api_base_url, ca_bundle: Path, http_transport=None):
        url = urlsplit(api_base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("计划服务必须使用已配置的 HTTPS 账号地址")
        self.account_id = account_id
        self._session_provider = session_provider
        self._base_url = api_base_url.rstrip("/")
        self._ssl = ssl.create_default_context(cafile=str(ca_bundle))
        self._transport = http_transport
        self._closed = Event()
        self._scope = None

    def close(self):
        self._closed.set()

    def _session(self):
        if self._closed.is_set():
            raise PlanError("CANCELLED", "账号计划会话已关闭。")
        result = self._session_provider()
        try:
            identity, state = result.identity, result.server_state
            account, context = state["account"], state["context"]
            scope = PlanScope(identity.account_id, identity.issuer, identity.subject, identity.organization_id, identity.workspace_id)
            if (scope.account_id != self.account_id or state["authenticated"] is not True
                    or account["account_id"] != scope.account_id or account["subject_id"] != scope.subject_id
                    or context["organization_id"] != scope.organization_id or context["workspace_id"] != scope.workspace_id
                    or any(not isinstance(value, str) or not value or any(ord(c) < 32 for c in value) for value in scope.snapshot_scope().values())
                    or not isinstance(result.access_token, str) or not result.access_token):
                raise ValueError()
            if self._scope is not None and scope != self._scope:
                raise ValueError()
            return scope, result.access_token
        except (AttributeError, KeyError, TypeError, ValueError):
            raise PlanError(
                "SESSION_UNAVAILABLE",
                "当前账号或空间正在更新或已切换，请稍后重试；当前计划数据未被替换。",
            ) from None

    def require_current(self):
        return self._session()[0]

    def load(self):
        snapshot = self.read_snapshot()
        return project_snapshot(snapshot, self.require_current())

    def read_snapshot(self):
        scope, token = self._session()
        self._scope = scope
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                   "X-MentorFlow-Organization-ID": scope.organization_id,
                   "X-MentorFlow-Workspace-ID": scope.workspace_id}
        try:
            with httpx.Client(verify=self._ssl, timeout=20, follow_redirects=False, transport=self._transport) as client:
                with client.stream("GET", self._base_url + SNAPSHOT_PATH, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise PlanError("REAUTH_REQUIRED", "计划读取的登录状态已失效，请使用账号入口重新验证。")
                    if response.status_code != 200:
                        raise PlanError("SOURCE_UNAVAILABLE", "服务器计划接口暂不可用，当前计划尚未确认，请稍后重试。")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        self.require_current()
                        raw.extend(chunk)
                        if len(raw) > MAX_SNAPSHOT_BYTES:
                            raise PlanError("SNAPSHOT_TOO_LARGE", "服务器计划快照超过既有接口上限，未截断后显示。")
            self.require_current()
            def unique_pairs(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate field")
                    value[key] = item
                return value
            snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
            validate_snapshot(snapshot, scope)
            self.require_current()
            return snapshot
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError("SOURCE_UNAVAILABLE", "计划读取失败，请检查网络或服务器状态后重试。") from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise PlanError("INVALID_SNAPSHOT", "计划服务返回的数据无法验证。") from None
        finally:
            token = ""
            headers.clear()

    def read_knowledge_map(self):
        """Read only the active map for the authenticated tenant workspace."""
        scope, token = self._session()
        self._scope = scope
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                   "X-MentorFlow-Organization-ID": scope.organization_id,
                   "X-MentorFlow-Workspace-ID": scope.workspace_id}
        path = KNOWLEDGE_MAP_PATH.format(workspace_id=quote(scope.workspace_id, safe=""))
        try:
            with httpx.Client(verify=self._ssl, timeout=20, follow_redirects=False, transport=self._transport) as client:
                with client.stream("GET", self._base_url + path, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise PlanError("REAUTH_REQUIRED", "知识地图读取的登录状态已失效，请使用账号入口重新验证。")
                    if response.status_code != 200:
                        raise PlanError("KNOWLEDGE_MAP_UNAVAILABLE", "当前工作区没有可读取的知识地图，或服务器暂不可用。")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        self.require_current()
                        raw.extend(chunk)
                        if len(raw) > MAX_KNOWLEDGE_MAP_BYTES:
                            raise PlanError("KNOWLEDGE_MAP_TOO_LARGE", "服务器知识地图超过读取上限。")
            self.require_current()

            def unique_pairs(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate field")
                    value[key] = item
                return value

            graph = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
            validate_knowledge_map(graph, scope)
            self.require_current()
            return graph
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError("KNOWLEDGE_MAP_UNAVAILABLE", "知识地图读取失败，请检查网络或服务器状态后重试。") from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise PlanError("INVALID_KNOWLEDGE_MAP", "服务器返回的知识地图无法验证。") from None
        finally:
            token = ""
            headers.clear()

    def _knowledge_map_json_request(self, method, path, *, body=None, max_bytes=MAX_KNOWLEDGE_MAP_BYTES):
        """Issue one authenticated knowledge-map request and parse bounded JSON."""
        scope, token = self._session()
        self._scope = scope
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "X-MentorFlow-Organization-ID": scope.organization_id,
            "X-MentorFlow-Workspace-ID": scope.workspace_id,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            self.require_current()
            with httpx.Client(verify=self._ssl, timeout=40, follow_redirects=False, transport=self._transport) as client:
                with client.stream(method, self._base_url + path, headers=headers, json=body) as response:
                    if response.status_code in (401, 403):
                        raise PlanError("REAUTH_REQUIRED", "知识地图操作的登录状态已失效，请通过账号入口重新验证。")
                    if response.status_code == 409:
                        raise PlanError("KNOWLEDGE_MAP_CONFLICT", "知识地图状态已变化，请刷新当前工作区后重试。")
                    if response.status_code == 428:
                        raise PlanError("PROVIDER_SESSION_REQUIRED", "生成知识地图需要先验证模型连接。")
                    if response.status_code != 200 and response.status_code != 202:
                        raise PlanError("KNOWLEDGE_MAP_UNAVAILABLE", "当前工作区的知识地图操作暂不可用。")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        self.require_current()
                        raw.extend(chunk)
                        if len(raw) > max_bytes:
                            raise PlanError("KNOWLEDGE_MAP_TOO_LARGE", "服务器知识地图响应超过读取上限。")
            self.require_current()
            def unique_pairs(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate field")
                    value[key] = item
                return value
            return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError("KNOWLEDGE_MAP_UNAVAILABLE", "知识地图请求失败，请检查网络或服务器状态后重试。") from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise PlanError("INVALID_KNOWLEDGE_MAP_RESPONSE", "服务器知识地图响应无法验证。") from None
        finally:
            token = ""
            headers.clear()
            if isinstance(body, dict):
                body.clear()

    def create_knowledge_map_job(
        self,
        *,
        provider_session_id,
        material_ids,
        anchor_material_id=None,
        supporting_material_ids=(),
        scope_policy=None,
        role_provenance=None,
        source_fingerprints=None,
        source_revisions=(),
    ):
        """Queue a provider-grounded map for the authenticated workspace."""
        if not isinstance(provider_session_id, str) or not provider_session_id.strip() or len(provider_session_id) > 256:
            raise PlanError("PROVIDER_SESSION_REQUIRED", "生成知识地图需要已验证的服务器模型会话。")
        if (not isinstance(material_ids, (list, tuple)) or not material_ids
                or len(material_ids) > 20
                or any(not isinstance(value, str) or not value.strip() for value in material_ids)):
            raise PlanError("KNOWLEDGE_SOURCE_REQUIRED", "请至少选择一份可追溯资料生成知识地图。")
        material_ids = list(dict.fromkeys(str(value).strip() for value in material_ids))
        anchor = str(anchor_material_id or material_ids[0]).strip()
        if anchor not in material_ids:
            raise PlanError("KNOWLEDGE_SCOPE_INVALID", "知识地图锚点资料必须属于所选资料范围。")
        supporting = list(dict.fromkeys(str(value).strip() for value in (supporting_material_ids or ()) if str(value).strip()))
        if any(value not in material_ids or value == anchor for value in supporting):
            raise PlanError("KNOWLEDGE_SCOPE_INVALID", "知识地图支持资料必须属于所选资料范围且不能重复锚点。")
        body = {
            "provider_session_id": provider_session_id.strip(),
            "material_ids": material_ids,
            "anchor_material_id": anchor,
            "supporting_material_ids": supporting,
            "scope_schema": "mentorflow.knowledge-map-scope.v1",
            "scope_policy": dict(scope_policy or {}),
            "role_provenance": dict(role_provenance or {}),
            "source_fingerprints": dict(source_fingerprints or {}),
            "source_revisions": list(source_revisions or ()),
        }
        scope = self.require_current()
        path = KNOWLEDGE_MAP_JOBS_PATH.format(workspace_id=quote(scope.workspace_id, safe=""))
        payload = self._knowledge_map_json_request("POST", path, body=body)
        return validate_knowledge_map_job(payload, scope)

    def read_knowledge_map_job(self, job_id):
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 256:
            raise PlanError("KNOWLEDGE_MAP_JOB_INVALID", "知识地图任务标识无效。")
        scope = self.require_current()
        path = KNOWLEDGE_MAP_JOB_PATH.format(
            workspace_id=quote(scope.workspace_id, safe=""), job_id=quote(job_id.strip(), safe="")
        )
        return validate_knowledge_map_job(self._knowledge_map_json_request("GET", path), scope)

    def activate_knowledge_map_revision(self, revision, *, expected_current_revision_id=""):
        if not isinstance(revision, (str, int)) or isinstance(revision, bool) or not str(revision).strip():
            raise PlanError("KNOWLEDGE_MAP_REVISION_INVALID", "知识地图 revision 无效。")
        body = {}
        if expected_current_revision_id:
            if not isinstance(expected_current_revision_id, str) or len(expected_current_revision_id) > 256:
                raise PlanError("KNOWLEDGE_MAP_REVISION_INVALID", "当前知识地图 revision 标识无效。")
            body["expected_current_revision_id"] = expected_current_revision_id
        scope = self.require_current()
        path = KNOWLEDGE_MAP_ACTIVATE_PATH.format(
            workspace_id=quote(scope.workspace_id, safe=""), revision=quote(str(revision).strip(), safe="")
        )
        graph = self._knowledge_map_json_request("POST", path, body=body)
        return validate_knowledge_map(graph, scope)

    def planning_assistant(self, *, message, conversation, provider_session_id, cancel=None):
        """Ask the verified server Provider; never send local model credentials."""
        scope, token = self._session()
        self._scope = scope
        if not isinstance(provider_session_id, str) or not provider_session_id.strip() or len(provider_session_id) > 256:
            raise PlanError("PROVIDER_SESSION_REQUIRED", "规划助手需要已验证的服务器模型会话。")
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise PlanError("PLANNING_MESSAGE_INVALID", "需求消息不能为空且不能超过 4000 个字符。")
        if not isinstance(conversation, list) or len(conversation) > 8:
            raise PlanError("PLANNING_MESSAGE_INVALID", "需求对话记录无效。")
        turns = []
        for item in conversation:
            if (not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}
                    or not isinstance(item.get("content"), str) or len(item["content"]) > 4000):
                raise PlanError("PLANNING_MESSAGE_INVALID", "需求对话记录无效。")
            turns.append({"role": item["role"], "content": item["content"]})
        body = {"message": message.strip(), "conversation": turns,
                "provider_session_id": provider_session_id.strip(),
                "resource_search_authorized": False}
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json",
                   "Content-Type": "application/json",
                   "X-MentorFlow-Organization-ID": scope.organization_id,
                   "X-MentorFlow-Workspace-ID": scope.workspace_id}
        path = PLANNING_ASSISTANT_PATH.format(workspace_id=quote(scope.workspace_id, safe=""))
        try:
            self.require_current()
            if cancel is not None and cancel.is_set():
                raise PlanError("CANCELLED", "规划助手请求已取消。")
            with httpx.Client(verify=self._ssl, timeout=40, follow_redirects=False, transport=self._transport) as client:
                with client.stream("POST", self._base_url + path, headers=headers, json=body) as response:
                    if response.status_code in (401, 403):
                        raise PlanError("REAUTH_REQUIRED", "规划助手登录状态已失效，请通过账号入口重新验证。")
                    if response.status_code == 428:
                        raise PlanError("PROVIDER_SESSION_REQUIRED", "请先验证模型连接，再使用服务器规划助手。")
                    if response.status_code == 409:
                        raise PlanError("PLANNING_SESSION_STALE", "规划会话已更新，请重试当前需求。")
                    if response.status_code == 422:
                        raise PlanError("PLANNING_REQUEST_REJECTED", "服务器拒绝了当前规划请求，请检查输入后重试。")
                    if response.status_code != 200:
                        raise PlanError("PLANNING_ASSISTANT_UNAVAILABLE", "服务器规划助手暂不可用，计划未创建。")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        self.require_current()
                        if cancel is not None and cancel.is_set():
                            raise PlanError("CANCELLED", "规划助手请求已取消。")
                        raw.extend(chunk)
                        if len(raw) > MAX_PLANNING_ASSISTANT_BYTES:
                            raise PlanError("PLANNING_RESPONSE_TOO_LARGE", "服务器规划助手响应超出读取上限。")
            self.require_current()
            def unique_pairs(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("duplicate field")
                    value[key] = item
                return value
            decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
            return validate_planning_assistant_response(decoded, scope.workspace_id)
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError("PLANNING_ASSISTANT_UNAVAILABLE", "规划助手连接失败，请检查网络后重试。") from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise PlanError("INVALID_PLANNING_ASSISTANT_RESPONSE", "规划助手返回的数据无法验证，未写入计划。") from None
        finally:
            token = ""
            headers.clear()
            body.clear()
