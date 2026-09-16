"""Small typed HTTP client for the loopback native runtime."""

from __future__ import annotations

from typing import Any
from pathlib import Path
import mimetypes
import hashlib
import json
from uuid import uuid4
from contextlib import contextmanager, ExitStack

from backend.app.plain_file_copy import open_resource_source, PlainFileCopyError

import httpx


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@contextmanager
def _upload_source(path):
    with ExitStack() as stack:
        try:
            source = stack.enter_context(open_resource_source(path))
        except FileNotFoundError as exc:
            raise ApiError(404, "RESOURCE_SOURCE_MISSING", "所选资料文件不存在或已被移动，请重新选择。") from exc
        except (ValueError, PlainFileCopyError) as exc:
            raise ApiError(400, "RESOURCE_SOURCE_UNSAFE", "所选资料路径不安全，请选择实际的本机文件。") from exc
        yield source


class DesktopApi:
    def __init__(self, origin: str):
        self.origin = origin.rstrip("/")
        self.client = httpx.Client(base_url=self.origin, timeout=60, follow_redirects=True)
        # Embedded legacy pages still probe this attribute while they are
        # migrated to the local single-user contract.  Keep it inert: no
        # cookie is read, written, or sent by the API client.
        self.session_token = ""
        self._pending_commands = {}

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        fingerprint = None
        if method.upper() in {'POST','PUT','PATCH','DELETE'} and path.startswith((
            '/api/active-teaching/', '/api/plan-product/', '/api/review/', '/api/product-resources', '/api/product-settings',
            '/api/workspaces', '/api/plans/', '/api/tasks/', '/api/sources/', '/api/sessions/', '/api/learning-runs/')):
            digest = hashlib.sha256((method.upper()+'\0'+path).encode())
            digest.update(json.dumps({k:kwargs[k] for k in ('json','data','params') if k in kwargs},sort_keys=True,default=str).encode())
            for key,value in (kwargs.get('files') or {}).items():
                filename, stream, *metadata = value if isinstance(value,tuple) else ('',value)
                digest.update(str((key,filename,metadata)).encode())
                if hasattr(stream,'read'):
                    offset = stream.tell()
                    try:
                        while chunk := stream.read(1024*1024): digest.update(chunk)
                    finally: stream.seek(offset)
                else: digest.update(stream.encode() if isinstance(stream,str) else stream)
            fingerprint = digest.hexdigest()
            identity = self._pending_commands.setdefault(fingerprint,kwargs.get('headers',{}).get('X-MentorFlow-Command-ID') or uuid4().hex)
            kwargs['headers'] = {**kwargs.get('headers',{}),'X-MentorFlow-Command-ID':identity}
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                error = response.json().get("error", {})
            except ValueError:
                error = {}
            raise ApiError(
                response.status_code,
                str(error.get("code") or "HTTP_ERROR"),
                str(error.get("message") or response.text or "请求失败"),
            )
        if fingerprint:
            self._pending_commands.pop(fingerprint,None)
        if response.status_code == 204:
            return None
        return response.json()

    def workspaces(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/api/workspaces")
        return payload if isinstance(payload, list) else payload.get("items", [])

    def account_metadata(self) -> dict[str, Any]:
        """Fetch the authenticated account's pathless canonical metadata."""

        return self.request("GET", "/api/enterprise/account/metadata")

    def admin_overview(self) -> dict[str, Any]:
        """Fetch aggregate administrator metrics; no identity PII is returned."""
        return self.request("GET", "/api/enterprise/auth/admin/overview")

    def put_account_progress(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist one metadata-only progress snapshot for the current account."""

        return self.request(
            "PUT",
            "/api/enterprise/account/metadata/progress",
            json=payload,
        )

    def active_workspaces(self, *, include_archived: bool = False) -> dict[str, Any]:
        params = {"compact": "true"}
        if include_archived:
            params["include_archived"] = "true"
        return self.request("GET", "/api/active-teaching/workspaces", params=params)

    def archive_workspace(self, workspace_id: str, *, archived: bool) -> dict[str, Any]:
        """Archive or restore an active learning task without deleting its history."""
        return self.request(
            "PATCH",
            f"/api/active-teaching/workspaces/{workspace_id}",
            json={"archived": bool(archived)},
        )

    def delete_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Delete a legacy workspace when the caller explicitly requests it."""
        return self.request("DELETE", f"/api/workspaces/{workspace_id}")

    def delete_active_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Delete an unstarted active-learning task and its local records."""
        return self.request("DELETE", f"/api/active-teaching/workspaces/{workspace_id}")

    def teacher_personas(self) -> dict[str, Any]:
        return self.request("GET", "/api/active-teaching/teacher-personas")

    def apply_teacher_persona(self, workspace_id: str, persona_id: str) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/api/active-teaching/workspaces/{workspace_id}/teacher-persona",
            json={"teacher_persona_id": persona_id},
        )

    def knowledge_map(self, workspace_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map",
            params={"revision": "active"},
        )

    def create_provider_session(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        credential_ref: str,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/provider-sessions",
            json={
                "provider": provider,
                "base_url": base_url,
                "model": model,
                "credential_ref": credential_ref,
            },
        )

    def test_provider_session(self, session_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/provider-sessions/{session_id}/test")

    def rank_personalized_updates(
        self,
        provider_session_id: str,
        interests: str,
        blocked: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Ask the verified model to rank only the supplied official-source metadata."""
        return self.request(
            "POST",
            "/api/personalized-updates/rank",
            json={
                "provider_session_id": provider_session_id,
                "interests": interests,
                "blocked": blocked,
                "candidates": candidates,
            },
        )

    def generate_life_analogy(
        self,
        provider_session_id: str,
        keywords: str | list[str],
        *,
        recent_cards: list[dict[str, str]] | list[str] | None = None,
        variation_seed: int = 0,
    ) -> dict[str, Any]:
        """Generate one varied card around a private, user-selected direction."""
        return self.request(
            "POST",
            "/api/personalized-updates/life-analogy",
            json={
                "provider_session_id": provider_session_id,
                "keywords": keywords,
                "recent_cards": list(recent_cards or [])[-48:],
                "variation_seed": max(0, int(variation_seed)),
            },
        )

    # Keep the shorter name convenient for callers that treat this as a
    # card-producing API rather than a generic generation operation.
    def life_analogy(
        self,
        provider_session_id: str,
        keywords: str | list[str],
    ) -> dict[str, Any]:
        return self.generate_life_analogy(provider_session_id, keywords)

    def create_life_analogy(
        self,
        provider_session_id: str,
        keywords: str | list[str],
    ) -> dict[str, Any]:
        return self.generate_life_analogy(provider_session_id, keywords)

    def list_provider_presets(self) -> dict[str, Any]:
        """Return locally stored provider presets without exposing secrets."""
        return self.request("GET", "/api/presets")

    def save_provider_preset(self, name: str, form: dict[str, Any]) -> dict[str, Any]:
        """Persist provider metadata and a Credential Locker reference only."""
        return self.request("POST", "/api/presets", json={"name": name, "form": form})

    def materials(self, workspace_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        params = {"include_archived": "true"} if include_archived else None
        payload = self.request(
            "GET", f"/api/active-teaching/workspaces/{workspace_id}/materials", params=params
        )
        return payload if isinstance(payload, list) else payload.get("items", [])

    def archive_material(self, workspace_id: str, material_id: str, *, archived: bool) -> dict[str, Any]:
        """Archive or restore one material without deleting its source evidence."""
        return self.request(
            "PATCH",
            f"/api/active-teaching/workspaces/{workspace_id}/materials/{material_id}",
            json={"archived": bool(archived)},
        )

    def delete_material(
        self,
        workspace_id: str,
        material_id: str,
        *,
        physical: bool = True,
        confirm_unreferenced: bool = True,
    ) -> dict[str, Any]:
        """Permanently delete an unreferenced material after explicit confirmation.

        The API defaults a bare DELETE to reversible archive.  Supplying both
        guards keeps the desktop button's irreversible wording honest while
        allowing the server to reject materials still referenced by a plan or
        map.
        """
        return self.request(
            "DELETE",
            f"/api/active-teaching/workspaces/{workspace_id}/materials/{material_id}",
            json={"physical": bool(physical), "confirm_unreferenced": bool(confirm_unreferenced)},
        )

    def upload_material(self, workspace_id: str, path: str | Path) -> dict[str, Any]:
        with _upload_source(path) as (source, handle):
            return self.request(
                "POST",
                f"/api/active-teaching/workspaces/{workspace_id}/materials",
                files={"file": (source.name, handle, mimetypes.guess_type(source.name)[0] or "application/octet-stream")},
            )

    def material_capabilities(self) -> dict[str, Any]:
        return self.request("GET", "/api/active-teaching/material-capabilities")

    def create_knowledge_map_job(self, workspace_id: str, provider_session_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs",
            json={"provider_session_id": provider_session_id},
        )

    def knowledge_map_job(self, job_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/active-teaching/knowledge-map/jobs/{job_id}")

    def runtime_policy(self) -> dict[str, Any]:
        return self.request("GET", "/api/active-teaching/runtime-policy")

    def receipt(self, workspace_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/active-teaching/workspaces/{workspace_id}/receipt")

    def save_receipt(self, workspace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", f"/api/active-teaching/workspaces/{workspace_id}/receipt", json=payload)

    def preview_mastery_plan(self, workspace_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/preview", json={})

    def activate_mastery_plan(self, workspace_id: str, plan_id: str, *, confirm_extension: bool) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/{plan_id}/activate",
            json={"confirm_extension": confirm_extension},
        )

    def current_mastery_plan(self, workspace_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/current")

    def current_legacy_plan(self, workspace_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/active-teaching/workspaces/{workspace_id}/plans/current")

    def start_teaching_session(self, workspace_id: str, provider_session_id: str = "") -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/active-teaching/workspaces/{workspace_id}/sessions/start",
            json={"provider_session_id": provider_session_id} if provider_session_id else {},
        )

    def teaching_session(self, session_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/active-teaching/sessions/{session_id}")

    def advance_teaching_session(
        self,
        session_id: str,
        *,
        message: str,
        provider_session_id: str = "",
        interaction_mode: str = "learning_evidence",
        free_chat_prompt: str = "",
        force_complete: bool = False,
        verify_completion: bool = False,
        attachment_ids: list[str] | None = None,
        actual_elapsed_minutes: float = 0.0,
        activity_event_id: str = "",
        activity_kind: str = "active_learning",
    ) -> dict[str, Any]:
        mode = str(interaction_mode or "learning_evidence").strip() or "learning_evidence"
        payload: dict[str, Any] = {
            "message": message,
            "interaction_mode": mode,
            "attachment_ids": list(attachment_ids or []),
        }
        if mode == "learning_evidence":
            payload["force_complete"] = force_complete
            payload["verify_completion"] = verify_completion
        elif free_chat_prompt:
            payload["free_chat_prompt"] = str(free_chat_prompt)[:6000]
        if mode == "learning_evidence" and actual_elapsed_minutes > 0:
            payload["actual_elapsed_minutes"] = round(float(actual_elapsed_minutes), 3)
            payload["activity_event_id"] = activity_event_id
            payload["activity_kind"] = activity_kind
        if provider_session_id:
            payload["provider_session_id"] = provider_session_id
        return self.request("POST", f"/api/active-teaching/sessions/{session_id}/advance", json=payload)

    def knowledge_map_candidates(self, workspace_id: str) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/candidates",
            params={"revision": "active"},
        )

    def decide_knowledge_map_candidate(
        self,
        workspace_id: str,
        candidate_id: str,
        *,
        decision: str,
        reason: str = "",
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/candidates/{candidate_id}/decision",
            json={"decision": decision, "reason": reason},
        )

    def decide_knowledge_map_candidates(
        self,
        workspace_id: str,
        candidate_ids: list[str],
        *,
        decision: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Apply one atomic decision to a set of knowledge-map candidates.

        The server validates the candidate revision and either decides every
        supplied id or leaves the revision untouched.  Keep this separate from
        the legacy single-candidate method so older native callers remain
        compatible while the desktop review UI can avoid stale-id loops.
        """

        ids: list[str] = []
        seen: set[str] = set()
        for candidate_id in candidate_ids:
            value = str(candidate_id or "").strip()
            if value and value not in seen:
                seen.add(value)
                ids.append(value)
        return self.request(
            "POST",
            f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/candidates/decisions",
            json={"candidate_ids": ids, "decision": decision, "reason": reason},
        )

    def upload_teaching_attachment(self, session_id: str, path: str | Path) -> dict[str, Any]:
        with _upload_source(path) as (source, handle):
            return self.request(
                "POST",
                f"/api/active-teaching/sessions/{session_id}/attachments",
                files={"file": (source.name, handle, mimetypes.guess_type(source.name)[0] or "application/octet-stream")},
            )

    def transcribe_teaching_voice(self, session_id: str, path: str | Path) -> dict[str, Any]:
        with _upload_source(path) as (source, handle):
            return self.request(
                "POST",
                f"/api/active-teaching/sessions/{session_id}/voice/transcribe",
                files={"file": (source.name, handle, mimetypes.guess_type(source.name)[0] or "application/octet-stream")},
                params={"language": "auto"},
            )
