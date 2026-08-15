"""Loopback authentication API used by the native desktop client."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body, HTTPException, Request, Response

from . import local_auth


router = APIRouter(prefix="/api/auth", tags=["local-auth"])
COOKIE_NAME = "mentorflow_session"
_LOGGER = logging.getLogger(__name__)
_SILENT_PASSWORD_RESET_ERRORS = {
    "PHONE_CODE_COOLDOWN",
    "SMS_PROVIDER_NOT_CONFIGURED",
    "SMS_GATEWAY_INSECURE",
    "SMS_PROVIDER_UNAVAILABLE",
    "SMS_PROVIDER_REJECTED",
    "SMS_CREDENTIAL_REQUIRED",
    "CREDENTIAL_STORE_UNAVAILABLE",
    "CREDENTIAL_NOT_FOUND",
}


def _error(error: local_auth.LocalAuthError, status: int = 400) -> None:
    if error.code == "USERNAME_EXISTS":
        status = 409
    elif error.code == "EXISTING_DATA_CLAIM_FORBIDDEN":
        status = 403
    elif error.code in {"SMS_PROVIDER_NOT_CONFIGURED", "SMS_GATEWAY_INSECURE", "SMS_PROVIDER_UNAVAILABLE", "SMS_PROVIDER_REJECTED"}:
        status = 503
    raise HTTPException(
        status_code=status,
        detail={"error": {"code": error.code, "message": error.message, "details": {}}},
    )


def _set_cookie(response: Response, token: str) -> None:
    secure = str(os.getenv("MENTORFLOW_COOKIE_SECURE", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
    }
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=local_auth.SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


@router.get("/status")
def auth_status() -> dict[str, object]:
    count = local_auth.user_count()
    return {
        "registration_required": count == 0,
        "registration_allowed": True,
        "account_mode": "local_multi_user",
        "user_count": count,
    }


@router.post("/register", status_code=201)
def register(response: Response, body: dict = Body(default_factory=dict)) -> dict[str, object]:
    try:
        session = local_auth.register(
            username=str(body.get("username") or ""),
            display_name=str(body.get("display_name") or ""),
            password=str(body.get("password") or ""),
            claim_existing_data=bool(body.get("claim_existing_data", False)),
        )
    except local_auth.LocalAuthError as exc:
        _error(exc)
    _set_cookie(response, session.token)
    return {
        "user": {
            "id": session.user_id,
            "username": session.username,
            "display_name": session.display_name,
        },
        "expires_at": session.expires_at,
    }


@router.post("/login")
def login(response: Response, body: dict = Body(default_factory=dict)) -> dict[str, object]:
    try:
        session = local_auth.login(
            username=str(body.get("username") or ""),
            password=str(body.get("password") or ""),
        )
    except local_auth.LocalAuthError as exc:
        _error(exc, 401)
    _set_cookie(response, session.token)
    return {
        "user": {
            "id": session.user_id,
            "username": session.username,
            "display_name": session.display_name,
        },
        "expires_at": session.expires_at,
    }


@router.post("/password/change")
def change_password(request: Request, response: Response, body: dict = Body(default_factory=dict)) -> dict[str, object]:
    user = getattr(request.state, "local_user", None)
    if not user:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_REQUIRED", "message": "请先登录。", "details": {}}})
    try:
        session = local_auth.change_password(
            user_id=str(user["user_id"]),
            current_password=str(body.get("current_password") or ""),
            new_password=str(body.get("new_password") or ""),
        )
    except local_auth.LocalAuthError as exc:
        _error(exc, 401 if exc.code == "INVALID_CREDENTIALS" else 400)
    _set_cookie(response, session.token)
    return {
        "user": {
            "id": session.user_id,
            "username": session.username,
            "display_name": session.display_name,
        },
        "expires_at": session.expires_at,
        "message": "密码已更新，其他设备会话已失效。",
    }


@router.get("/phone")
def phone_status(request: Request) -> dict[str, object]:
    user = getattr(request.state, "local_user", None)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "AUTH_REQUIRED", "message": "请先登录。", "details": {}}},
        )
    return local_auth.phone_status(str(user["user_id"]))


@router.post("/phone/bind/request")
def request_phone_binding(request: Request, body: dict = Body(default_factory=dict)) -> dict[str, object]:
    user = getattr(request.state, "local_user", None)
    if not user:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_REQUIRED", "message": "请先登录。", "details": {}}})
    try:
        return local_auth.request_phone_binding(
            user_id=str(user["user_id"]),
            phone=str(body.get("phone_e164") or ""),
            current_password=str(body.get("current_password") or ""),
        )
    except local_auth.LocalAuthError as exc:
        _error(exc, 401 if exc.code == "INVALID_CREDENTIALS" else 400)


@router.post("/phone/bind/confirm")
def confirm_phone_binding(request: Request, body: dict = Body(default_factory=dict)) -> dict[str, object]:
    user = getattr(request.state, "local_user", None)
    if not user:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_REQUIRED", "message": "请先登录。", "details": {}}})
    try:
        return local_auth.confirm_phone_binding(
            user_id=str(user["user_id"]),
            challenge_id=str(body.get("challenge_id") or ""),
            code=str(body.get("code") or ""),
        )
    except local_auth.LocalAuthError as exc:
        _error(exc)


@router.post("/password-reset/request", status_code=202)
def request_password_reset(body: dict = Body(default_factory=dict)) -> dict[str, object]:
    try:
        local_auth.request_password_reset(username=str(body.get("username") or ""))
    except local_auth.LocalAuthError as exc:
        # Keep the public response identical for unknown users, unbound users,
        # cooldowns and provider failures.  Otherwise an attacker can infer
        # whether a username has a verified phone by observing a 503/4xx.
        if exc.code not in _SILENT_PASSWORD_RESET_ERRORS:
            _LOGGER.warning("password reset request rejected: %s", exc.code)
    # Do not disclose whether an account exists or has a verified phone.
    return {"accepted": True, "message": "如果账号已绑定手机号且短信服务可用，验证码会发送到该手机号。"}


@router.post("/password-reset/confirm")
def confirm_password_reset(body: dict = Body(default_factory=dict)) -> dict[str, object]:
    try:
        local_auth.reset_password_with_phone_code(
            username=str(body.get("username") or ""),
            code=str(body.get("code") or ""),
            new_password=str(body.get("new_password") or ""),
        )
    except local_auth.LocalAuthError as exc:
        _error(exc)
    return {"reset": True, "message": "密码已更新，请使用新密码重新登录。"}


@router.get("/me")
def me(request: Request) -> dict[str, object]:
    user = getattr(request.state, "local_user", None)
    if not user:
        raise HTTPException(status_code=401, detail={"error": {"code": "AUTH_REQUIRED", "message": "请先登录。", "details": {}}})
    return {"user": user}


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        local_auth.logout(token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
