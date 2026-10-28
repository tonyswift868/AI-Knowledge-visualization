"""Private account profile and normalized avatar endpoints for account-core."""
from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from refactor.server.core_errors import app_error_body
from refactor.server.enterprise.account_workspaces import authenticate_account
from refactor.server.enterprise.identity.oidc import IdentityError

PREFIX = "/api/enterprise/account/profile"
PROFILE_PATH = PREFIX
AVATAR_PATH = PREFIX + "/avatar"
PROFILE_PATHS = frozenset({PROFILE_PATH, AVATAR_PATH})
MAX_TEXT_BODY_BYTES = 4096
MAX_AVATAR_BYTES = 262_144
MAX_AVATAR_EDGE = 512
_SCOPE_SETTING = "mentorflow.rag_account_scope"
_PROFILE_ETAG = re.compile(r'^"profile-([1-9][0-9]{0,18})"$')
_MAX_VERSION = (1 << 63) - 1


class UpdateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nickname: str
    signature: str


class BoundedProfileBodyRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded_handler(request: Request):
            if request.url.path == AVATAR_PATH and request.method == "PUT":
                limit = MAX_AVATAR_BYTES
            elif request.url.path == PROFILE_PATH and request.method == "PATCH":
                limit = MAX_TEXT_BODY_BYTES
            else:
                return await original(request)
            receive = request.receive
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    body = message.get("body", b"")
                    if len(body) > limit - received:
                        raise HTTPException(status_code=413, detail="Request body too large")
                    received += len(body)
                return message

            request._receive = bounded_receive
            return await original(request)

        return bounded_handler


router = APIRouter(route_class=BoundedProfileBodyRoute)


class AccountProfileError(ValueError):
    def __init__(self, code: str, status: int):
        self.code = code
        self.status = status
        super().__init__(code)


def account_scope(issuer: str, subject: str) -> str:
    return hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest()


def _normalize_image(raw: bytes, content_type: str) -> tuple[bytes, int, int]:
    if not raw or len(raw) > MAX_AVATAR_BYTES:
        raise AccountProfileError("PROFILE_AVATAR_SIZE_INVALID", 413)
    if content_type not in {"image/jpeg", "image/png"}:
        raise AccountProfileError("PROFILE_AVATAR_FORMAT_INVALID", 415)
    try:
        from PIL import Image
        from PIL import ImageOps

        with io.BytesIO(raw) as stream:
            with Image.open(stream) as source:
                expected_format = {"image/jpeg": "JPEG", "image/png": "PNG"}[content_type]
                if source.format != expected_format or getattr(source, "n_frames", 1) != 1:
                    raise AccountProfileError("PROFILE_AVATAR_FORMAT_INVALID", 415)
                if source.width < 1 or source.height < 1 or source.width != source.height:
                    raise AccountProfileError("PROFILE_AVATAR_DIMENSIONS_INVALID", 422)
                if source.width > MAX_AVATAR_EDGE or source.height > MAX_AVATAR_EDGE:
                    raise AccountProfileError("PROFILE_AVATAR_DIMENSIONS_INVALID", 422)
                source.verify()
            stream.seek(0)
            with Image.open(stream) as source:
                image = ImageOps.exif_transpose(source).convert("RGBA")
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                for quality in (85, 75, 65, 55, 45):
                    output = io.BytesIO()
                    background.save(output, format="JPEG", quality=quality, optimize=True)
                    payload = output.getvalue()
                    if len(payload) <= MAX_AVATAR_BYTES:
                        return payload, background.width, background.height
                raise AccountProfileError("PROFILE_AVATAR_SIZE_INVALID", 413)
    except AccountProfileError:
        raise
    except Exception:
        raise AccountProfileError("PROFILE_AVATAR_IMAGE_INVALID", 422) from None


class AccountProfileService:
    """Small SQL operations constrained by server-derived account_scope."""

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _bind(connection, scope):
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT set_config('mentorflow.rag_account_scope', :scope, true)"),
                               {"scope": scope})

    @staticmethod
    def _table(connection):
        return "public.account_profiles" if connection.dialect.name == "postgresql" else "account_profiles"

    @staticmethod
    def _row(connection, scope, *, lock=False):
        suffix = " FOR UPDATE" if lock and connection.dialect.name == "postgresql" else ""
        statement = text(f"""SELECT nickname, signature, avatar_etag, avatar_width,
            avatar_height, version, avatar_bytes FROM {AccountProfileService._table(connection)}
            WHERE account_scope=:scope""" + suffix)
        return connection.execute(statement, {"scope": scope}).mappings().first()

    @staticmethod
    def _metadata(row):
        if row is None:
            return {"nickname": "", "signature": "", "version": 1, "avatar": None}
        avatar = None
        if row["avatar_etag"]:
            avatar = {"etag": f'"{row["avatar_etag"]}"', "width": row["avatar_width"],
                      "height": row["avatar_height"]}
        return {"nickname": row["nickname"], "signature": row["signature"],
                "version": int(row["version"]), "avatar": avatar}

    def get(self, issuer, subject):
        scope = account_scope(issuer, subject)
        with self.engine.connect() as connection:
            with connection.begin():
                self._bind(connection, scope)
                return self._metadata(self._row(connection, scope))

    def update(self, issuer, subject, nickname, signature, expected):
        scope = account_scope(issuer, subject)
        with self.engine.begin() as connection:
            self._bind(connection, scope)
            table = self._table(connection)
            connection.execute(text(f"INSERT INTO {table}(account_scope) VALUES (:scope) "
                                    "ON CONFLICT (account_scope) DO NOTHING"), {"scope": scope})
            row = self._row(connection, scope, lock=True)
            current = int(row["version"])
            if current != expected:
                raise AccountProfileError("PROFILE_VERSION_CONFLICT", 412)
            connection.execute(text(f"""UPDATE {table}
                SET nickname=:nickname, signature=:signature, version=version+1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE account_scope=:scope AND version=:version"""),
                {"nickname": nickname, "signature": signature, "scope": scope,
                 "version": current})
            return self._metadata(self._row(connection, scope))

    def avatar(self, issuer, subject):
        scope = account_scope(issuer, subject)
        with self.engine.connect() as connection:
            with connection.begin():
                self._bind(connection, scope)
                row = self._row(connection, scope)
                if row is None or not row["avatar_etag"]:
                    return None
                return bytes(row["avatar_bytes"]), row["avatar_etag"]

    def save_avatar(self, issuer, subject, image, etag, width, height, expected):
        scope = account_scope(issuer, subject)
        with self.engine.begin() as connection:
            self._bind(connection, scope)
            table = self._table(connection)
            connection.execute(text(f"INSERT INTO {table}(account_scope) VALUES (:scope) "
                                    "ON CONFLICT (account_scope) DO NOTHING"), {"scope": scope})
            row = self._row(connection, scope, lock=True)
            current = int(row["version"])
            if current != expected:
                raise AccountProfileError("PROFILE_VERSION_CONFLICT", 412)
            connection.execute(text(f"""UPDATE {table} SET
                avatar_bytes=:image, avatar_etag=:etag, avatar_width=:width,
                avatar_height=:height, version=version+1, updated_at=CURRENT_TIMESTAMP
                WHERE account_scope=:scope AND version=:version"""),
                {"image": image, "etag": etag, "width": width, "height": height,
                 "scope": scope, "version": current})
            return self._metadata(self._row(connection, scope))

    def delete_avatar(self, issuer, subject, expected):
        scope = account_scope(issuer, subject)
        with self.engine.begin() as connection:
            self._bind(connection, scope)
            row = self._row(connection, scope, lock=True)
            if row is None:
                if expected != 1:
                    raise AccountProfileError("PROFILE_VERSION_CONFLICT", 412)
                return self._metadata(None)
            current = int(row["version"])
            if current != expected:
                raise AccountProfileError("PROFILE_VERSION_CONFLICT", 412)
            if row["avatar_etag"]:
                connection.execute(text(f"""UPDATE {self._table(connection)} SET
                    avatar_bytes=NULL, avatar_etag=NULL, avatar_width=NULL, avatar_height=NULL,
                    version=version+1, updated_at=CURRENT_TIMESTAMP
                    WHERE account_scope=:scope AND version=:version"""),
                    {"scope": scope, "version": current})
                row = self._row(connection, scope)
            return self._metadata(row)


def _expected_version(request):
    value = request.headers.get("If-Match", "")
    if not value:
        raise AccountProfileError("PROFILE_IF_MATCH_REQUIRED", 428)
    match = _PROFILE_ETAG.fullmatch(value)
    if not match:
        raise AccountProfileError("ACCOUNT_PROFILE_IF_MATCH_INVALID", 400)
    try:
        version = int(match.group(1))
    except (ValueError, OverflowError):
        raise AccountProfileError("ACCOUNT_PROFILE_IF_MATCH_INVALID", 400) from None
    if version > _MAX_VERSION:
        raise AccountProfileError("ACCOUNT_PROFILE_IF_MATCH_INVALID", 400)
    return version


def _write_error_response(request, exc):
    body = {"code": exc.code}
    if exc.code == "ACCOUNT_PROFILE_IF_MATCH_INVALID":
        body = app_error_body(exc.code, "资料版本标记无效",
                              request_id=getattr(request.state, "request_id", None))
    return JSONResponse(body, status_code=exc.status,
                        headers={"Cache-Control": "private, no-cache"})


def _principal(request):
    principal = getattr(request.state, "account_principal", None)
    if principal is None:
        raise IdentityError("ENTERPRISE_AUTH_REQUIRED", "A verified account is required")
    return principal


def _service(request):
    service = getattr(request.app.state, "account_profile_service", None)
    if service is None:
        raise AccountProfileError("ACCOUNT_PROFILE_UNAVAILABLE", 503)
    return service


async def _run(request, method, *args):
    principal = _principal(request)
    try:
        return await run_in_threadpool(getattr(_service(request), method),
                                       principal.issuer, principal.subject, *args)
    except AccountProfileError:
        raise
    except SQLAlchemyError:
        raise AccountProfileError("ACCOUNT_PROFILE_UNAVAILABLE", 503) from None


def _profile_response(value):
    return JSONResponse(value, headers={"Cache-Control": "private, no-cache",
                                        "ETag": f'"profile-{value["version"]}"'})


def _parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        model = UpdateProfile.model_validate(value)
        model.nickname.encode("utf-8")
        model.signature.encode("utf-8")
        if len(model.nickname) > 30 or len(model.signature) > 60:
            raise ValueError("text too long")
        if any(unicodedata.category(char) == "Cc"
               for char in model.nickname + model.signature):
            raise ValueError("control character")
        return model
    except (ValueError, ValidationError, TypeError, UnicodeError):
        raise AccountProfileError("PROFILE_INPUT_INVALID", 422) from None


async def _bounded_body(request, limit):
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > limit:
                raise AccountProfileError("PROFILE_BODY_TOO_LARGE", 413)
            raw.extend(chunk)
        return bytes(raw)
    finally:
        raw[:] = b"\0" * len(raw)


@router.get(PROFILE_PATH)
async def get_profile(request: Request):
    try:
        return _profile_response(await _run(request, "get"))
    except AccountProfileError as exc:
        return JSONResponse({"code": exc.code}, status_code=exc.status,
                            headers={"Cache-Control": "private, no-cache"})


@router.patch(PROFILE_PATH)
async def patch_profile(request: Request):
    try:
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise AccountProfileError("PROFILE_INPUT_INVALID", 415)
        body = _parse_json(await _bounded_body(request, MAX_TEXT_BODY_BYTES))
        result = await _run(request, "update", body.nickname, body.signature,
                            _expected_version(request))
        return _profile_response(result)
    except AccountProfileError as exc:
        return _write_error_response(request, exc)


def _if_none_match(header, etag):
    if not header:
        return False
    return any(item.strip() == "*" or item.strip().removeprefix("W/").strip() == f'"{etag}"'
               for item in header.split(","))


@router.get(AVATAR_PATH)
async def get_avatar(request: Request):
    try:
        found = await _run(request, "avatar")
        headers = {"Cache-Control": "private, no-cache"}
        if found is None:
            return Response(status_code=404, headers=headers)
        payload, etag = found
        quoted = f'"{etag}"'
        headers["ETag"] = quoted
        if _if_none_match(request.headers.get("If-None-Match", ""), etag):
            return Response(status_code=304, headers=headers)
        return Response(payload, media_type="image/jpeg", headers=headers)
    except AccountProfileError as exc:
        return JSONResponse({"code": exc.code}, status_code=exc.status,
                            headers={"Cache-Control": "private, no-cache"})


@router.put(AVATAR_PATH)
async def put_avatar(request: Request):
    try:
        content_type = request.headers.get("content-type", "").split(";")[0].lower()
        raw = await _bounded_body(request, MAX_AVATAR_BYTES)
        image, width, height = await run_in_threadpool(
            _normalize_image, raw, content_type)
        etag = hashlib.sha256(image).hexdigest()
        result = await _run(request, "save_avatar", image, etag, width, height,
                            _expected_version(request))
        return _profile_response(result)
    except AccountProfileError as exc:
        return _write_error_response(request, exc)


@router.delete(AVATAR_PATH)
async def delete_avatar(request: Request):
    try:
        result = await _run(request, "delete_avatar", _expected_version(request))
        return _profile_response(result)
    except AccountProfileError as exc:
        return _write_error_response(request, exc)


async def authenticate_profile_account(request: Request, runtime):
    await authenticate_account(request, runtime)
