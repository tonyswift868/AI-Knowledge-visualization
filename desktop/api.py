"""Small typed HTTP client for the loopback native runtime."""

from __future__ import annotations

from typing import Any
from pathlib import Path
import mimetypes

import httpx


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class DesktopApi:
    def __init__(self, origin: str):
        self.origin = origin.rstrip("/")
        self.client = httpx.Client(base_url=self.origin, timeout=60, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    @property
    def session_token(self) -> str:
        return str(self.client.cookies.get("mentorflow_session", ""))

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
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
        if response.status_code == 204:
            return None
        return response.json()

    def auth_status(self) -> dict[str, Any]:
        return self.request("GET", "/api/auth/status")

    def register(
        self,
        username: str,
        display_name: str,
        password: str,
        *,
        claim_existing_data: bool = False,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/auth/register",
            json={
                "username": username,
                "display_name": display_name,
                "password": password,
                "claim_existing_data": claim_existing_data,
            },
        )

    def login(self, username: str, password: str) -> dict[str, Any]:
        return self.request("POST", "/api/auth/login", json={"username": username, "password": password})

    def request_password_reset(self, username: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/auth/password-reset/request",
            json={"username": username},
        )

    def reset_password(self, username: str, code: str, new_password: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/auth/password-reset/confirm",
            json={"username": username, "code": code, "new_password": new_password},
        )

    def change_password(self, current_password: str, new_password: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/auth/password/change",
            json={"current_password": current_password, "new_password": new_password},
        )

    def phone_status(self) -> dict[str, Any]:
        return self.request("GET", "/api/auth/phone")

    def request_phone_binding(self, phone_e164: str, current_password: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/auth/phone/bind/request",
            json={"phone_e164": phone_e164, "current_password": current_password},
        )

    def confirm_phone_binding(self, challenge_id: str, code: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/auth/phone/bind/confirm",
            json={"challenge_id": challenge_id, "code": code},
        )

    def logout(self) -> None:
        self.request("POST", "/api/auth/logout")

    def me(self) -> dict[str, Any]:
        return self.request("GET", "/api/auth/me")

    def workspaces(self) -> list[dict[str, Any]]:
        payload = self.request("GET", "/api/workspaces")
        return payload if isinstance(payload, list) else payload.get("items", [])

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
        source = Path(path)
        with source.open("rb") as handle:
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
        force_complete: bool = False,
        verify_completion: bool = False,
        attachment_ids: list[str] | None = None,
        actual_elapsed_minutes: float = 0.0,
        activity_event_id: str = "",
        activity_kind: str = "active_learning",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": message,
            "force_complete": force_complete,
            "verify_completion": verify_completion,
            "attachment_ids": list(attachment_ids or []),
        }
        if actual_elapsed_minutes > 0:
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

    def upload_teaching_attachment(self, session_id: str, path: str | Path) -> dict[str, Any]:
        source = Path(path)
        with source.open("rb") as handle:
            return self.request(
                "POST",
                f"/api/active-teaching/sessions/{session_id}/attachments",
                files={"file": (source.name, handle, mimetypes.guess_type(source.name)[0] or "application/octet-stream")},
            )

    def transcribe_teaching_voice(self, session_id: str, path: str | Path) -> dict[str, Any]:
        source = Path(path)
        with source.open("rb") as handle:
            return self.request(
                "POST",
                f"/api/active-teaching/sessions/{session_id}/voice/transcribe",
                files={"file": (source.name, handle, mimetypes.guess_type(source.name)[0] or "application/octet-stream")},
                params={"language": "auto"},
            )
