"""Local multi-user authentication for the native MentorFlow client."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from argon2 import PasswordHasher, exceptions, profiles

from . import db, orm


USERNAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,31}$")
SESSION_DAYS = 30
LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_LOCKOUT = timedelta(minutes=15)
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
_PASSWORD_HASHER = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
_DUMMY_HASH = _PASSWORD_HASHER.hash("mentorflow-dummy-password-for-timing")


class LocalAuthError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AuthenticatedSession:
    token: str
    session_id: str
    user_id: str
    username: str
    display_name: str
    expires_at: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(orm.ORM_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def normalize_username(username: str) -> str:
    display = unicodedata.normalize("NFKC", str(username or "")).strip()
    if not USERNAME_PATTERN.fullmatch(display):
        raise LocalAuthError(
            "USERNAME_INVALID",
            "账号需为 3–32 位，以英文字母开头，只能包含字母、数字、点、横线或下划线。",
        )
    return display.casefold()


def validate_password(password: str) -> None:
    value = str(password or "")
    if len(value) < PASSWORD_MIN_LENGTH:
        raise LocalAuthError("PASSWORD_TOO_SHORT", f"密码至少需要 {PASSWORD_MIN_LENGTH} 个字符。")
    if len(value) > PASSWORD_MAX_LENGTH:
        raise LocalAuthError("PASSWORD_TOO_LONG", f"密码不能超过 {PASSWORD_MAX_LENGTH} 个字符。")
    categories = sum(
        bool(re.search(pattern, value))
        for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
    )
    if categories < 3:
        raise LocalAuthError("PASSWORD_TOO_WEAK", "密码需至少包含大小写字母、数字、符号中的三类。")


def user_count() -> int:
    with _connect() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM orm_local_users").fetchone()[0])


def first_user_id() -> str | None:
    """Return the immutable data-owner account used for the initial import.

    The desktop build imports the existing learning data only once.  Keeping
    the first account identifiable lets other account-scoped stores preserve
    that user's legacy data without exposing it to later accounts.
    """

    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM orm_local_users ORDER BY created_at ASC, id ASC LIMIT 1"
        ).fetchone()
    return str(row[0]) if row else None


def _existing_workspace_ids(connection: sqlite3.Connection) -> set[str]:
    workspace_ids = {
        str(row[0]) for row in connection.execute("SELECT id FROM orm_workspaces")
    }
    legacy_path = Path(db.DB_PATH)
    if legacy_path.is_file():
        legacy = sqlite3.connect(f"file:{legacy_path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            workspace_ids.update(str(row[0]) for row in legacy.execute("SELECT id FROM workspaces"))
        finally:
            legacy.close()
    # Active-teaching workspaces may exist without an orm_workspaces row.
    for table in ("active_receipts", "active_materials", "active_plans"):
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            workspace_ids.update(
                str(row[0])
                for row in connection.execute(
                    f'SELECT DISTINCT workspace_id FROM "{table}" WHERE workspace_id IS NOT NULL'
                )
            )
    return {item for item in workspace_ids if item}


def _create_session(
    connection: sqlite3.Connection,
    user: sqlite3.Row,
) -> AuthenticatedSession:
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session_id = f"localsession_{uuid4().hex}"
    now = _now()
    expires = now + timedelta(days=SESSION_DAYS)
    connection.execute(
        """INSERT INTO orm_local_sessions
           (id,user_id,token_hash,client_kind,created_at,expires_at,last_seen_at)
           VALUES (?,?,?,?,?,?,?)""",
        (session_id, user["id"], token_hash, "desktop", _iso(now), _iso(expires), _iso(now)),
    )
    connection.execute(
        "UPDATE orm_local_users SET last_login_at=?,updated_at=? WHERE id=?",
        (_iso(now), _iso(now), user["id"]),
    )
    return AuthenticatedSession(
        token=token,
        session_id=session_id,
        user_id=str(user["id"]),
        username=str(user["username_display"]),
        display_name=str(user["display_name"]),
        expires_at=_iso(expires),
    )


def register(
    *,
    username: str,
    display_name: str,
    password: str,
    claim_existing_data: bool,
) -> AuthenticatedSession:
    normalized = normalize_username(username)
    shown = unicodedata.normalize("NFKC", username).strip()
    display = unicodedata.normalize("NFKC", str(display_name or "")).strip()
    if not 1 <= len(display) <= 60:
        raise LocalAuthError("DISPLAY_NAME_INVALID", "显示名称需为 1–60 个字符。")
    validate_password(password)
    password_hash = _PASSWORD_HASHER.hash(password)
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        first_user = int(connection.execute("SELECT COUNT(*) FROM orm_local_users").fetchone()[0]) == 0
        existing_workspace_ids = _existing_workspace_ids(connection) if first_user else set()
        if first_user and existing_workspace_ids and not claim_existing_data:
            raise LocalAuthError(
                "INITIAL_DATA_CLAIM_REQUIRED",
                "首次创建账户必须认领当前已有的学习数据；否则数据会保持未归属状态。",
            )
        if claim_existing_data and not first_user:
            raise LocalAuthError("EXISTING_DATA_CLAIM_FORBIDDEN", "只有第一个本地账户可以认领现有数据。")
        user_id = f"localuser_{uuid4().hex}"
        now = _iso(_now())
        try:
            connection.execute(
                """INSERT INTO orm_local_users
                   (id,username_normalized,username_display,display_name,password_hash,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,'active',?,?)""",
                (user_id, normalized, shown, display, password_hash, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise LocalAuthError("USERNAME_EXISTS", "这个账号已经存在。") from exc
        if claim_existing_data:
            for workspace_id in sorted(existing_workspace_ids or _existing_workspace_ids(connection)):
                connection.execute(
                    """INSERT OR IGNORE INTO orm_workspace_ownership
                       (workspace_id,user_id,ownership_kind,claim_source,claimed_at)
                       VALUES (?,?,'owner','first_user_claim',?)""",
                    (workspace_id, user_id, now),
                )
        user = connection.execute("SELECT * FROM orm_local_users WHERE id=?", (user_id,)).fetchone()
        session = _create_session(connection, user)
        connection.commit()
        return session


def login(*, username: str, password: str) -> AuthenticatedSession:
    try:
        normalized = normalize_username(username)
    except LocalAuthError:
        normalized = "invalid"
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT * FROM orm_local_users WHERE username_normalized=?", (normalized,)
        ).fetchone()
        stored_hash = str(user["password_hash"]) if user else _DUMMY_HASH
        now = _now()
        locked = False
        if user and user["locked_until"]:
            try:
                locked = datetime.fromisoformat(str(user["locked_until"])) > now
            except ValueError:
                locked = False
        try:
            verified = _PASSWORD_HASHER.verify(stored_hash, str(password or ""))
        except (exceptions.VerifyMismatchError, exceptions.VerificationError, exceptions.InvalidHashError):
            verified = False
        if locked:
            verified = False
        if not user or not verified or str(user["status"]) != "active":
            if user and str(user["status"]) == "active":
                failed = int(user["failed_login_count"] or 0) + 1
                locked_until = _iso(now + LOGIN_LOCKOUT) if failed >= LOGIN_MAX_FAILED_ATTEMPTS else None
                connection.execute(
                    "UPDATE orm_local_users SET failed_login_count=?,locked_until=?,updated_at=? WHERE id=?",
                    (failed, locked_until, _iso(now), str(user["id"])),
                )
            connection.commit()
            raise LocalAuthError("INVALID_CREDENTIALS", "账号或密码不正确。")
        if _PASSWORD_HASHER.check_needs_rehash(stored_hash):
            connection.execute(
                "UPDATE orm_local_users SET password_hash=?,updated_at=? WHERE id=?",
                (_PASSWORD_HASHER.hash(password), _iso(now), user["id"]),
            )
        connection.execute(
            "UPDATE orm_local_users SET failed_login_count=0,locked_until=NULL,updated_at=? WHERE id=?",
            (_iso(now), str(user["id"])),
        )
        session = _create_session(connection, user)
        connection.commit()
        return session


def change_password(
    *,
    user_id: str,
    current_password: str,
    new_password: str,
) -> AuthenticatedSession:
    """Change a password after re-authentication and rotate every session.

    The caller receives a fresh session so a password change cannot leave the
    browser carrying the session that was authenticated before the change.
    """

    validate_password(new_password)
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = _verify_current_password(connection, user_id, current_password)
        try:
            if _PASSWORD_HASHER.verify(str(user["password_hash"]), str(new_password)):
                raise LocalAuthError("PASSWORD_REUSE", "新密码不能与当前密码相同。")
        except (exceptions.VerifyMismatchError, exceptions.VerificationError, exceptions.InvalidHashError):
            pass
        now = _iso(_now())
        connection.execute(
            """UPDATE orm_local_users
               SET password_hash=?,failed_login_count=0,locked_until=NULL,updated_at=?
               WHERE id=?""",
            (_PASSWORD_HASHER.hash(new_password), now, str(user_id)),
        )
        connection.execute(
            "UPDATE orm_local_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (now, str(user_id)),
        )
        refreshed = connection.execute(
            "SELECT * FROM orm_local_users WHERE id=? AND status='active'", (str(user_id),)
        ).fetchone()
        if not refreshed:
            connection.rollback()
            raise LocalAuthError("AUTH_REQUIRED", "当前账户不可用。")
        session = _create_session(connection, refreshed)
        connection.commit()
        return session


def authenticate(token: str, *, touch: bool = False) -> dict[str, str] | None:
    value = str(token or "")
    if not value:
        return None
    token_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
    now = _now()
    with _connect() as connection:
        row = connection.execute(
            """SELECT s.id AS session_id,s.token_hash,s.expires_at,u.id AS user_id,
                      u.username_display,u.display_name,u.status
               FROM orm_local_sessions s JOIN orm_local_users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.revoked_at IS NULL""",
            (token_hash,),
        ).fetchone()
        if not row or not hmac.compare_digest(str(row["token_hash"]), token_hash):
            return None
        try:
            expires = datetime.fromisoformat(str(row["expires_at"]))
        except ValueError:
            return None
        if expires <= now or str(row["status"]) != "active":
            return None
        if touch:
            connection.execute(
                "UPDATE orm_local_sessions SET last_seen_at=? WHERE id=?",
                (_iso(now), row["session_id"]),
            )
            connection.commit()
        return {
            "session_id": str(row["session_id"]),
            "user_id": str(row["user_id"]),
            "username": str(row["username_display"]),
            "display_name": str(row["display_name"]),
            "expires_at": str(row["expires_at"]),
        }


def logout(token: str) -> None:
    token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()
    with _connect() as connection:
        connection.execute(
            "UPDATE orm_local_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
            (_iso(_now()), token_hash),
        )
        connection.commit()


PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
PHONE_CODE_PATTERN = re.compile(r"^\d{6}$")
PHONE_CODE_TTL = timedelta(minutes=10)
PHONE_CODE_COOLDOWN = timedelta(seconds=60)
PHONE_CODE_MAX_ATTEMPTS = 5


def normalize_phone(phone: str) -> str:
    """Normalize an explicit E.164 number; no country is guessed locally."""

    value = unicodedata.normalize("NFKC", str(phone or "")).strip()
    value = re.sub(r"[\s().-]", "", value)
    if not PHONE_PATTERN.fullmatch(value):
        raise LocalAuthError("PHONE_INVALID", "手机号必须使用带国家码的 E.164 格式，例如 +8613812345678。")
    return value


def _mask_phone(phone: str | None) -> str:
    value = str(phone or "")
    if not value:
        return ""
    return value[:3] + "*" * max(2, len(value) - 7) + value[-4:]


def phone_status(user_id: str) -> dict[str, object]:
    with _connect() as connection:
        user = connection.execute(
            "SELECT phone_e164,phone_verified_at FROM orm_local_users WHERE id=? AND status='active'",
            (str(user_id),),
        ).fetchone()
    verified = bool(user and user["phone_e164"] and user["phone_verified_at"])
    return {"bound": verified, "phone_masked": _mask_phone(user["phone_e164"] if verified else None)}


def _verify_current_password(connection: sqlite3.Connection, user_id: str, password: str) -> sqlite3.Row:
    user = connection.execute(
        "SELECT * FROM orm_local_users WHERE id=? AND status='active'", (str(user_id),)
    ).fetchone()
    if not user:
        raise LocalAuthError("AUTH_REQUIRED", "当前账户不可用。")
    try:
        verified = _PASSWORD_HASHER.verify(str(user["password_hash"]), str(password or ""))
    except (exceptions.VerifyMismatchError, exceptions.VerificationError, exceptions.InvalidHashError):
        verified = False
    if not verified:
        raise LocalAuthError("INVALID_CREDENTIALS", "当前密码不正确。")
    return user


def _phone_code_hash(*, user_id: str, phone: str, purpose: str, code: str) -> str:
    value = f"mentorflow-phone-otp-v1:{purpose}:{user_id}:{phone}:{code}"
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _parse_timestamp(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise LocalAuthError("PHONE_CODE_INVALID", "验证码无效或已过期。") from exc


def _new_phone_challenge(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    phone: str,
    purpose: str,
) -> tuple[str, str, datetime]:
    now = _now()
    previous = connection.execute(
        """SELECT created_at FROM orm_local_phone_challenges
           WHERE user_id=? AND purpose=? AND consumed_at IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (str(user_id), purpose),
    ).fetchone()
    if previous and now - _parse_timestamp(previous["created_at"]) < PHONE_CODE_COOLDOWN:
        raise LocalAuthError("PHONE_CODE_COOLDOWN", "验证码发送过于频繁，请稍后再试。")
    challenge_id = f"phonechallenge_{uuid4().hex}"
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = now + PHONE_CODE_TTL
    connection.execute(
        """INSERT INTO orm_local_phone_challenges
           (id,user_id,phone_e164,purpose,code_hash,created_at,expires_at,attempt_count)
           VALUES (?,?,?,?,?,?,?,0)""",
        (
            challenge_id,
            str(user_id),
            phone,
            purpose,
            _phone_code_hash(user_id=str(user_id), phone=phone, purpose=purpose, code=code),
            _iso(now),
            _iso(expires),
        ),
    )
    return challenge_id, code, expires


def _consume_if_send_failed(challenge_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE orm_local_phone_challenges SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
            (_iso(_now()), str(challenge_id)),
        )
        connection.commit()


def request_phone_binding(*, user_id: str, phone: str, current_password: str) -> dict[str, str]:
    from .sms_gateway import SmsGatewayError, send_verification_code

    normalized_phone = normalize_phone(phone)
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = _verify_current_password(connection, user_id, current_password)
        existing = connection.execute(
            "SELECT id FROM orm_local_users WHERE phone_e164=? AND phone_verified_at IS NOT NULL AND id<>?",
            (normalized_phone, str(user["id"])),
        ).fetchone()
        if existing:
            raise LocalAuthError("PHONE_IN_USE", "这个手机号已经绑定了其他本地账户。")
        challenge_id, code, expires = _new_phone_challenge(
            connection, user_id=str(user["id"]), phone=normalized_phone, purpose="bind"
        )
        connection.commit()
    try:
        send_verification_code(
            phone_e164=normalized_phone,
            code=code,
            purpose="绑定手机号",
            challenge_id=challenge_id,
        )
    except SmsGatewayError as exc:
        _consume_if_send_failed(challenge_id)
        raise LocalAuthError(exc.code, exc.message) from exc
    return {"challenge_id": challenge_id, "expires_at": _iso(expires)}


def _invalid_phone_code(connection: sqlite3.Connection, candidate: sqlite3.Row | None) -> None:
    if candidate:
        attempts = int(candidate["attempt_count"] or 0) + 1
        consumed = _iso(_now()) if attempts >= PHONE_CODE_MAX_ATTEMPTS else None
        connection.execute(
            "UPDATE orm_local_phone_challenges SET attempt_count=?,consumed_at=COALESCE(consumed_at,?) WHERE id=?",
            (attempts, consumed, str(candidate["id"])),
        )
    connection.commit()
    raise LocalAuthError("PHONE_CODE_INVALID", "验证码无效或已过期。")


def confirm_phone_binding(*, user_id: str, challenge_id: str, code: str) -> dict[str, object]:
    if not PHONE_CODE_PATTERN.fullmatch(str(code or "").strip()):
        raise LocalAuthError("PHONE_CODE_INVALID", "验证码无效或已过期。")
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        candidate = connection.execute(
            """SELECT * FROM orm_local_phone_challenges
               WHERE id=? AND user_id=? AND purpose='bind' AND consumed_at IS NULL""",
            (str(challenge_id), str(user_id)),
        ).fetchone()
        if not candidate or _parse_timestamp(candidate["expires_at"]) <= _now():
            _invalid_phone_code(connection, candidate)
        expected = _phone_code_hash(
            user_id=str(user_id), phone=str(candidate["phone_e164"]), purpose="bind", code=str(code).strip()
        )
        if not hmac.compare_digest(str(candidate["code_hash"]), expected):
            _invalid_phone_code(connection, candidate)
        conflict = connection.execute(
            "SELECT id FROM orm_local_users WHERE phone_e164=? AND phone_verified_at IS NOT NULL AND id<>?",
            (str(candidate["phone_e164"]), str(user_id)),
        ).fetchone()
        if conflict:
            raise LocalAuthError("PHONE_IN_USE", "这个手机号已经绑定了其他本地账户。")
        now = _iso(_now())
        connection.execute(
            "UPDATE orm_local_users SET phone_e164=?,phone_verified_at=?,updated_at=? WHERE id=?",
            (str(candidate["phone_e164"]), now, now, str(user_id)),
        )
        connection.execute(
            "UPDATE orm_local_phone_challenges SET consumed_at=? WHERE id=?",
            (now, str(candidate["id"])),
        )
        connection.commit()
    return {"bound": True, "phone_masked": _mask_phone(str(candidate["phone_e164"]))}


def request_password_reset(*, username: str) -> None:
    """Send an OTP only to a previously verified phone; never disclose lookup state."""

    from .sms_gateway import SmsGatewayError, send_verification_code

    try:
        normalized = normalize_username(username)
    except LocalAuthError:
        return
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            """SELECT * FROM orm_local_users
               WHERE username_normalized=? AND status='active'
                 AND phone_e164 IS NOT NULL AND phone_verified_at IS NOT NULL""",
            (normalized,),
        ).fetchone()
        if not user:
            connection.commit()
            return
        challenge_id, code, _expires = _new_phone_challenge(
            connection, user_id=str(user["id"]), phone=str(user["phone_e164"]), purpose="reset"
        )
        connection.commit()
    try:
        send_verification_code(
            phone_e164=str(user["phone_e164"]),
            code=code,
            purpose="重置密码",
            challenge_id=challenge_id,
        )
    except SmsGatewayError as exc:
        _consume_if_send_failed(challenge_id)
        raise LocalAuthError(exc.code, exc.message) from exc


def reset_password_with_phone_code(*, username: str, code: str, new_password: str) -> None:
    validate_password(new_password)
    if not PHONE_CODE_PATTERN.fullmatch(str(code or "").strip()):
        raise LocalAuthError("PHONE_CODE_INVALID", "验证码无效或已过期。")
    try:
        normalized = normalize_username(username)
    except LocalAuthError as exc:
        raise LocalAuthError("PHONE_CODE_INVALID", "验证码无效或已过期。") from exc
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            """SELECT * FROM orm_local_users
               WHERE username_normalized=? AND status='active'
                 AND phone_e164 IS NOT NULL AND phone_verified_at IS NOT NULL""",
            (normalized,),
        ).fetchone()
        candidate = None
        if user:
            candidate = connection.execute(
                """SELECT * FROM orm_local_phone_challenges
                   WHERE user_id=? AND phone_e164=? AND purpose='reset' AND consumed_at IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (str(user["id"]), str(user["phone_e164"])),
            ).fetchone()
        if not candidate or _parse_timestamp(candidate["expires_at"]) <= _now():
            _invalid_phone_code(connection, candidate)
        expected = _phone_code_hash(
            user_id=str(user["id"]), phone=str(user["phone_e164"]), purpose="reset", code=str(code).strip()
        )
        if not hmac.compare_digest(str(candidate["code_hash"]), expected):
            _invalid_phone_code(connection, candidate)
        now = _iso(_now())
        connection.execute(
            "UPDATE orm_local_users SET password_hash=?,updated_at=? WHERE id=?",
            (_PASSWORD_HASHER.hash(new_password), now, str(user["id"])),
        )
        connection.execute(
            "UPDATE orm_local_phone_challenges SET consumed_at=? WHERE id=?",
            (now, str(candidate["id"])),
        )
        connection.execute(
            "UPDATE orm_local_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (now, str(user["id"])),
        )
        connection.commit()


def owned_workspace_ids(user_id: str) -> list[str]:
    with _connect() as connection:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT workspace_id FROM orm_workspace_ownership WHERE user_id=? ORDER BY workspace_id",
                (user_id,),
            )
        ]


def workspace_owner_id(workspace_id: str) -> str | None:
    """Return the sole owner of a workspace without leaking any user details."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT user_id FROM orm_workspace_ownership WHERE workspace_id=?",
            (str(workspace_id),),
        ).fetchone()
        return str(row[0]) if row else None


def workspace_exists(workspace_id: str) -> bool:
    """Check all durable stores that can independently contain a workspace."""
    workspace_id = str(workspace_id)
    with _connect() as connection:
        if workspace_id in _existing_workspace_ids(connection):
            return True
    return False


def claim_workspace(user_id: str, workspace_id: str, *, claim_source: str) -> bool:
    """Atomically reserve an unowned workspace for one user.

    Returns True when this call created the ownership row and False when the
    same user already owned it. A different owner is always rejected.
    """
    workspace_id = str(workspace_id).strip()
    if not workspace_id:
        raise LocalAuthError("WORKSPACE_ID_INVALID", "学习空间标识不能为空。")
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        owner = connection.execute(
            "SELECT user_id FROM orm_workspace_ownership WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        if owner:
            if str(owner[0]) != str(user_id):
                raise LocalAuthError("WORKSPACE_ACCESS_DENIED", "这个学习空间属于其他账户。")
            return False
        connection.execute(
            """INSERT INTO orm_workspace_ownership
               (workspace_id,user_id,ownership_kind,claim_source,claimed_at)
               VALUES (?,?,'owner',?,?)""",
            (workspace_id, str(user_id), str(claim_source), _iso(_now())),
        )
        connection.commit()
        return True


def release_empty_workspace_claim(user_id: str, workspace_id: str) -> None:
    """Undo a request reservation only when no workspace data was created."""
    if workspace_exists(workspace_id):
        return
    with _connect() as connection:
        connection.execute(
            """DELETE FROM orm_workspace_ownership
               WHERE workspace_id=? AND user_id=?
                 AND claim_source IN ('request_reservation','workspace_create')""",
            (str(workspace_id), str(user_id)),
        )
        connection.commit()


def resolve_resource_workspace(path: str) -> str | None:
    """Resolve non-workspace resource URLs to their durable workspace scope."""
    patterns = (
        (r"^/api/plans/([^/]+)", "plan"),
        (r"^/api/tasks/([^/]+)", "task"),
        (r"^/api/sessions/([^/]+)", "session"),
        (r"^/api/active-teaching/sessions/([^/]+)", "active_session"),
        (r"^/api/active-teaching/attachments/([^/]+)", "attachment"),
        (r"^/api/active-teaching/knowledge-map/jobs/([^/]+)", "map_job"),
        (r"^/api/sources/([^/]+)", "source"),
        (r"^/api/jobs/([^/]+)", "job"),
        (r"^/api/learning-runs/([^/]+)", "learning_run"),
        (r"^/api/conflicts/([^/]+)", "conflict"),
        (r"^/api/campaigns/([^/]+)", "campaign"),
        (r"^/api/active-teaching/focus-monitoring/([^/]+)", "focus_session"),
        (r"^/api/casual-chat/sessions/([^/]+)", "casual_session"),
    )
    kind = resource_id = None
    for pattern, candidate_kind in patterns:
        match = re.match(pattern, path)
        if match:
            kind, resource_id = candidate_kind, match.group(1)
            break
    if not kind or not resource_id:
        return None

    orm_queries = {
        "plan": (
            "SELECT workspace_id FROM orm_plans WHERE id=? UNION ALL "
            "SELECT workspace_id FROM active_plans WHERE id=? LIMIT 1",
            (resource_id, resource_id),
        ),
        "task": (
            "SELECT workspace_id FROM orm_plan_execution_tasks WHERE id=? UNION ALL "
            "SELECT workspace_id FROM active_plan_tasks WHERE id=? LIMIT 1",
            (resource_id, resource_id),
        ),
        "active_session": (
            "SELECT workspace_id FROM active_teaching_sessions WHERE id=? LIMIT 1",
            (resource_id,),
        ),
        "attachment": (
            "SELECT workspace_id FROM active_teaching_attachments WHERE id=? LIMIT 1",
            (resource_id,),
        ),
        "map_job": (
            "SELECT workspace_id FROM orm_knowledge_map_jobs WHERE id=? LIMIT 1",
            (resource_id,),
        ),
        "learning_run": (
            "SELECT workspace_id FROM orm_learning_runs WHERE id=? LIMIT 1",
            (resource_id,),
        ),
        "conflict": (
            "SELECT workspace_id FROM orm_conflicts WHERE id=? LIMIT 1",
            (resource_id,),
        ),
        "campaign": (
            "SELECT workspace_id FROM orm_narrative_campaigns WHERE id=? LIMIT 1",
            (resource_id,),
        ),
        "focus_session": (
            "SELECT workspace_id FROM active_focus_sessions WHERE id=? LIMIT 1",
            (resource_id,),
        ),
    }
    if kind in orm_queries:
        query, params = orm_queries[kind]
        with _connect() as connection:
            try:
                row = connection.execute(query, params).fetchone()
            except sqlite3.OperationalError:
                row = None
            if row:
                return str(row[0])

    legacy_queries = {
        "plan": ("SELECT workspace_id FROM plans WHERE id=?", (resource_id,)),
        "task": (
            """SELECT p.workspace_id FROM tasks t
               JOIN study_days d ON d.id=t.study_day_id
               JOIN plans p ON p.id=d.plan_id WHERE t.id=?""",
            (resource_id,),
        ),
        "session": ("SELECT workspace_id FROM sessions WHERE id=?", (resource_id,)),
        "source": ("SELECT workspace_id FROM sources WHERE id=?", (resource_id,)),
        "job": ("SELECT workspace_id FROM jobs WHERE id=?", (resource_id,)),
        "casual_session": (
            "SELECT workspace_id FROM casual_chat_sessions WHERE id=?",
            (resource_id,),
        ),
    }
    if kind in legacy_queries and Path(db.DB_PATH).is_file():
        query, params = legacy_queries[kind]
        connection = sqlite3.connect(db.DB_PATH)
        try:
            try:
                row = connection.execute(query, params).fetchone()
            except sqlite3.OperationalError:
                row = None
            return str(row[0]) if row else None
        finally:
            connection.close()
    return None
