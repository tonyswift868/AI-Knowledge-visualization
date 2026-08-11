from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
READY_STATUS = "ready"
AWAITING_STATUS = "awaiting_authorized_samples"
REQUIRED_STYLES = frozenset({"lively", "relaxed"})
RUNTIME_URL_ENV = "MENTORFLOW_TEACHER_VOICE_RUNTIME_URL"
PROTECTED_ASSETS_ENV = "MENTORFLOW_TEACHER_VOICE_PROTECTED_PATHS"
APPROVED_AUTHORIZATION_STATUSES = frozenset(
    {"explicit_permission", "licensed", "public_domain"}
)
REQUIRED_AUTHORIZATION_COVERAGE = frozenset({"training", "synthesis"})
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,127}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_STYLE_ALLOWED_FIELDS = frozenset(
    {"style_id", "identity_id", "description", "reference_audio"}
)


class TeacherVoiceReleaseError(ValueError):
    """A stable, machine-readable validation failure for a release manifest."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class VoiceReference:
    path: Path
    transcript: str
    language: str
    authorization_id: str


@dataclass(frozen=True)
class VoiceStyle:
    style_id: str
    identity_id: str
    description: str
    references: tuple[VoiceReference, ...]


@dataclass(frozen=True)
class TeacherVoiceReleaseSlot:
    manifest_path: Path
    slot_id: str
    status: str
    identity_id: str
    display_name: str
    authorization_status: str
    runtime_url: str
    release_root: Path
    training_output_root: Path
    gpt_weight: Path | None
    sovits_weight: Path | None
    styles: Mapping[str, VoiceStyle]

    @property
    def ready(self) -> bool:
        return self.status == READY_STATUS


def default_manifest_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "voice"
        / "teacher_voice_release_slot.v1.json"
    )


def _fail(code: str, message: str) -> None:
    raise TeacherVoiceReleaseError(code, message)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_manifest", f"{field} must be an object")
    return value


def _nonempty_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        _fail("invalid_manifest", f"{field} must be a non-empty string")
    return text


def _safe_id(value: Any, field: str) -> str:
    text = _nonempty_string(value, field)
    if not _SAFE_ID.fullmatch(text):
        _fail("invalid_manifest", f"{field} is not a portable identifier")
    return text


def _resolve_relative_path(base: Path, value: Any, field: str) -> Path:
    text = _nonempty_string(value, field)
    relative = Path(text)
    if relative.is_absolute() or relative.drive:
        _fail("non_portable_path", f"{field} must be relative")
    return (base / relative).resolve()


def _resolve_release_asset(release_root: Path, value: Any, field: str) -> Path:
    candidate = _resolve_relative_path(release_root, value, field)
    if not candidate.is_relative_to(release_root):
        _fail("asset_outside_release", f"{field} escapes the release root")
    return candidate


def _validate_loopback_runtime_url(value: Any) -> str:
    url = _nonempty_string(value, "runtime URL").rstrip("/")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        _fail("invalid_runtime_url", str(exc))
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        _fail("non_loopback_runtime", "the voice runtime must use local HTTP loopback")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        _fail("invalid_runtime_url", "credentials, query strings and fragments are forbidden")
    if parsed.path not in {"", "/"}:
        _fail("invalid_runtime_url", "the runtime URL must not include an API path")
    if port is None or not 1 <= port <= 65535:
        _fail("invalid_runtime_url", "the runtime URL must include a valid port")
    return url


def _collect_protected_paths(
    environ: Mapping[str, str], protected_paths: Iterable[str | Path]
) -> tuple[Path, ...]:
    values: list[str | Path] = list(protected_paths)
    configured = str(environ.get(PROTECTED_ASSETS_ENV, "") or "").strip()
    if configured:
        values.extend(part for part in configured.split(os.pathsep) if part.strip())
    return tuple(Path(value).expanduser().resolve() for value in values)


def _reject_protected_collision(candidate: Path, protected: Iterable[Path], field: str) -> None:
    for protected_path in protected:
        if candidate == protected_path or candidate.is_relative_to(protected_path):
            _fail(
                "protected_asset_collision",
                f"{field} collides with protected training asset {protected_path}",
            )


def _require_existing_file(path: Path, field: str) -> None:
    if not path.is_file():
        _fail("missing_release_file", f"{field} does not exist: {path}")


def load_teacher_voice_release_slot(
    manifest_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    protected_paths: Iterable[str | Path] = (),
    require_ready: bool = False,
) -> TeacherVoiceReleaseSlot:
    """Load and validate one immutable, portable teacher-voice release slot.

    This function is intentionally read-only.  It never starts a runtime, creates
    a training job, downloads media, or writes model weights.
    """

    path = Path(manifest_path or default_manifest_path()).expanduser().resolve()
    if not path.is_file():
        _fail("missing_manifest", f"release manifest does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("invalid_manifest", f"cannot read release manifest: {exc}")
    return validate_teacher_voice_release_slot(
        payload,
        path,
        environ=environ,
        protected_paths=protected_paths,
        require_ready=require_ready,
    )


def validate_teacher_voice_release_slot(
    payload: Any,
    manifest_path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    protected_paths: Iterable[str | Path] = (),
    require_ready: bool = False,
) -> TeacherVoiceReleaseSlot:
    manifest = _mapping(payload, "manifest")
    path = Path(manifest_path).expanduser().resolve()
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _fail("unsupported_schema", f"schema_version must be {SCHEMA_VERSION}")

    slot_id = _safe_id(manifest.get("slot_id"), "slot_id")
    status = _nonempty_string(manifest.get("status"), "status")
    if status not in {READY_STATUS, AWAITING_STATUS}:
        _fail("invalid_status", f"unsupported release status: {status}")

    identity = _mapping(manifest.get("identity"), "identity")
    identity_id = _safe_id(identity.get("identity_id"), "identity.identity_id")
    if identity.get("kind") != "original_generic":
        _fail("non_generic_identity", "identity.kind must be original_generic")
    if identity.get("impersonates") is not False or identity.get("source_identity") not in {None, ""}:
        _fail("impersonating_identity", "the release slot cannot target an existing identity")
    display_name = _nonempty_string(identity.get("display_name"), "identity.display_name")

    if "authorization" not in manifest or not isinstance(manifest.get("authorization"), Mapping):
        _fail("missing_authorization", "authorization metadata is required")
    authorization = _mapping(manifest.get("authorization"), "authorization")
    authorization_status = _nonempty_string(
        authorization.get("status"), "authorization.status"
    )
    evidence_value = authorization.get("evidence")
    if not isinstance(evidence_value, list):
        _fail("missing_authorization", "authorization.evidence must be a list")

    runtime = _mapping(manifest.get("runtime"), "runtime")
    if runtime.get("url_env") != RUNTIME_URL_ENV:
        _fail("invalid_runtime_env", f"runtime.url_env must be {RUNTIME_URL_ENV}")
    active_environment = os.environ if environ is None else environ
    runtime_value = active_environment.get(RUNTIME_URL_ENV) or runtime.get("default_url")
    if runtime_value:
        runtime_url = _validate_loopback_runtime_url(runtime_value)
    elif status == AWAITING_STATUS:
        runtime_url = ""
    else:
        _fail(
            "missing_runtime_url",
            f"a ready release requires {RUNTIME_URL_ENV} or runtime.default_url",
        )

    storage = _mapping(manifest.get("storage"), "storage")
    if storage.get("output_mode") != "create_new_immutable_release":
        _fail("unsafe_output_policy", "storage.output_mode must create a new immutable release")
    if storage.get("overwrite_existing") is not False:
        _fail("overwrite_forbidden", "storage.overwrite_existing must be false")
    release_root = _resolve_relative_path(
        path.parent, storage.get("release_root"), "storage.release_root"
    )
    training_output_root = _resolve_release_asset(
        release_root,
        storage.get("training_output_root"),
        "storage.training_output_root",
    )
    protected = _collect_protected_paths(active_environment, protected_paths)
    _reject_protected_collision(training_output_root, protected, "training output root")

    styles_value = _mapping(manifest.get("styles"), "styles")
    if set(styles_value) != REQUIRED_STYLES:
        _fail("invalid_styles", "styles must contain exactly lively and relaxed")

    evidence_ids: set[str] = set()
    if status == READY_STATUS:
        if authorization_status not in APPROVED_AUTHORIZATION_STATUSES or not evidence_value:
            _fail("missing_authorization", "a ready release requires verified training and synthesis rights")
        for index, raw_evidence in enumerate(evidence_value):
            evidence = _mapping(raw_evidence, f"authorization.evidence[{index}]")
            evidence_id = _safe_id(
                evidence.get("authorization_id"),
                f"authorization.evidence[{index}].authorization_id",
            )
            if evidence_id in evidence_ids:
                _fail("invalid_authorization", f"duplicate authorization id: {evidence_id}")
            coverage = evidence.get("covers")
            if not isinstance(coverage, list) or not REQUIRED_AUTHORIZATION_COVERAGE.issubset(
                {str(item) for item in coverage}
            ):
                _fail("missing_authorization", f"{evidence_id} must cover training and synthesis")
            evidence_path = _resolve_release_asset(
                release_root,
                evidence.get("evidence_path"),
                f"authorization.evidence[{index}].evidence_path",
            )
            _require_existing_file(evidence_path, f"authorization evidence {evidence_id}")
            evidence_ids.add(evidence_id)
    elif authorization_status != AWAITING_STATUS:
        _fail(
            "misleading_authorization_status",
            "an awaiting release must remain awaiting_authorized_samples",
        )

    models_value = manifest.get("models")
    gpt_weight: Path | None = None
    sovits_weight: Path | None = None
    if status == READY_STATUS:
        models = _mapping(models_value, "models")
        gpt_weight = _resolve_release_asset(
            release_root, models.get("gpt_weight"), "models.gpt_weight"
        )
        sovits_weight = _resolve_release_asset(
            release_root, models.get("sovits_weight"), "models.sovits_weight"
        )
        for field, model_path in (
            ("models.gpt_weight", gpt_weight),
            ("models.sovits_weight", sovits_weight),
        ):
            _reject_protected_collision(model_path, protected, field)
            _require_existing_file(model_path, field)
    elif models_value is not None:
        _fail(
            "inactive_slot_contains_active_assets",
            "an awaiting release cannot declare active model weights",
        )

    parsed_styles: dict[str, VoiceStyle] = {}
    for style_id in sorted(REQUIRED_STYLES):
        raw_style = _mapping(styles_value.get(style_id), f"styles.{style_id}")
        unexpected_fields = set(raw_style) - _STYLE_ALLOWED_FIELDS
        if unexpected_fields:
            _fail(
                "style_identity_override",
                f"styles.{style_id} cannot define identity fields: {sorted(unexpected_fields)}",
            )
        declared_style_id = _safe_id(raw_style.get("style_id"), f"styles.{style_id}.style_id")
        if declared_style_id != style_id or declared_style_id == identity_id:
            _fail("style_identity_override", f"styles.{style_id} is not a style-only record")
        style_identity = _safe_id(
            raw_style.get("identity_id"), f"styles.{style_id}.identity_id"
        )
        if style_identity != identity_id:
            _fail(
                "style_identity_override",
                f"styles.{style_id} must reuse the release identity",
            )
        description = _nonempty_string(
            raw_style.get("description"), f"styles.{style_id}.description"
        )
        references_value = raw_style.get("reference_audio")
        if not isinstance(references_value, list):
            _fail("invalid_references", f"styles.{style_id}.reference_audio must be a list")
        if status == READY_STATUS and not references_value:
            _fail("missing_release_file", f"styles.{style_id} requires at least one reference")
        if status == AWAITING_STATUS and references_value:
            _fail(
                "inactive_slot_contains_active_assets",
                f"styles.{style_id} cannot activate references while awaiting authorization",
            )
        references: list[VoiceReference] = []
        for index, raw_reference in enumerate(references_value):
            reference = _mapping(raw_reference, f"styles.{style_id}.reference_audio[{index}]")
            reference_path = _resolve_release_asset(
                release_root,
                reference.get("path"),
                f"styles.{style_id}.reference_audio[{index}].path",
            )
            authorization_id = _safe_id(
                reference.get("authorization_id"),
                f"styles.{style_id}.reference_audio[{index}].authorization_id",
            )
            if authorization_id not in evidence_ids:
                _fail(
                    "missing_authorization",
                    f"reference {reference_path.name} has no matching authorization evidence",
                )
            _reject_protected_collision(reference_path, protected, "style reference")
            _require_existing_file(reference_path, "style reference")
            references.append(
                VoiceReference(
                    path=reference_path,
                    transcript=_nonempty_string(
                        reference.get("transcript"),
                        f"styles.{style_id}.reference_audio[{index}].transcript",
                    ),
                    language=_nonempty_string(
                        reference.get("language"),
                        f"styles.{style_id}.reference_audio[{index}].language",
                    ),
                    authorization_id=authorization_id,
                )
            )
        parsed_styles[style_id] = VoiceStyle(
            style_id=style_id,
            identity_id=identity_id,
            description=description,
            references=tuple(references),
        )

    slot = TeacherVoiceReleaseSlot(
        manifest_path=path,
        slot_id=slot_id,
        status=status,
        identity_id=identity_id,
        display_name=display_name,
        authorization_status=authorization_status,
        runtime_url=runtime_url,
        release_root=release_root,
        training_output_root=training_output_root,
        gpt_weight=gpt_weight,
        sovits_weight=sovits_weight,
        styles=parsed_styles,
    )
    if require_ready and not slot.ready:
        _fail(
            "release_not_ready",
            "authorized samples, immutable weights and both style references are still required",
        )
    return slot


def validate_new_training_output_path(
    slot: TeacherVoiceReleaseSlot,
    relative_path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    protected_paths: Iterable[str | Path] = (),
) -> Path:
    """Validate a future output without creating it or touching another run."""

    candidate = _resolve_release_asset(
        slot.training_output_root, relative_path, "training output path"
    )
    active_environment = os.environ if environ is None else environ
    protected = _collect_protected_paths(active_environment, protected_paths)
    _reject_protected_collision(candidate, protected, "training output path")
    if candidate.exists():
        _fail("existing_output_collision", f"training output already exists: {candidate}")
    return candidate
