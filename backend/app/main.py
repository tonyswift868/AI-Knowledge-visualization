from __future__ import annotations

import asyncio
import inspect
import json
import logging
import mimetypes
import os
import re
import time
from threading import Lock
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import db
from .schemas import ArchiveImportIn, FeasibilityConfirmIn, GoalPatch, GoalProfile, GoalOut, LocalSourceIn, ProviderSessionCreateIn, ProviderTestIn, SourceSummaryOut, TextSourceIn, UrlSourceIn, WorkspaceDetailOut, WorkspaceIn, WorkspaceOut, WorkspacePatch
from .services import analyze, available_days, build_plan, create_source, inspect_local, parse_text, safe_path, scene_for_task, source_detail, tutor, uid, normalize
from .orm import init_orm, upgrade_orm_schema
from .ingestion_secure import cleanup_preview as cleanup_local_preview, create_preview, consume_preview, parse_file, chunks as secure_chunks
from .provider_service import get_provider, parse_conversational_payload, ProviderError
from .retrieval_service import RetrievalService
from .grounding_service import critic as grounding_critic
from .job_service import create_job, set_job
from .url_fetcher_secure import fetch_safe
from .core_errors import app_error_body, AppError
from .core_security import FileIdentity
from .security_redaction import redact
from .provider_runtime import create_provider_session, create_verified_demo_session, delete_provider_credential, delete_provider_session, generation_meta, get_provider_session, latest_verified_provider_session, provider_for_session, public_session, require_provider_session, test_provider_session
from .archive_import import ArchiveError, cleanup_expired_previews, create_preview as create_archive_preview, discard_preview, import_preview as import_archive_preview
from .story_pack import StoryPackError, list_story_packs, load_story_pack, public_character, public_scene, validate_story_pack
from .narrative_runtime import NarrativeRuntimeError, available_actions as narrative_available_actions, campaign_history, campaign_message, choose_campaign, create_campaign, get_campaign, ingest_learning_event, list_campaigns, advance_campaign, publish_learning_event
from .voice_runtime import synthesize_voice
from . import catalog_repository
from .learning_run import LearningRunError, add_run_message, create_learning_run, get_learning_run, request_hint, submit_learning_run, submit_quick_quiz
from .adaptive_narrative import AdaptiveNarrativeError, activate_blueprint, adaptive_context, analyze_task_profile, compile_adaptation, generate_dynamic_scene, generate_scene_contract, get_blueprint, get_current_contract, list_artifacts, list_contracts, list_story_objects
from .expedition_service import ExpeditionError, activate_lineup, assets as expedition_asset, awaken as expedition_awaken, battle as expedition_battle, challenge_stage, claim_milestone, claim_stage, delete_lineup, equipment as expedition_equipment, equip_equipment, equip_pet, get_ledger as expedition_ledger, get_profile as expedition_profile, gacha_banners, gacha_history, gacha_pull, hero_detail, lineups as expedition_lineups, list_heroes, milestones as expedition_milestones, pets as expedition_pets, reset_equipment, reset_slot, save_lineup, star_up, stage_definition, stages as expedition_stages, training_slots, unequip_equipment, upgrade_equipment, upgrade_slot
from .expedition_art_pack import ArtPackError, MAX_PACK_BYTES, PACK_ROOT, activate_pack, active_pack, asset_file as resolve_expedition_art_asset_file, asset_public as expedition_art_asset_public, hero_assets as expedition_hero_assets, install_preview as install_expedition_art_preview, list_packs as list_expedition_art_packs, preview_bytes as preview_expedition_art_bytes, review_asset as review_expedition_art_asset, rollback_pack
from .teacher_avatar_registry import MAX_BATCH_BYTES, MAX_BATCH_MODELS, MAX_KOIKATSU_EXPORT_BYTES, MAX_MODEL_BYTES, TeacherAvatarError, import_model as import_teacher_avatar, import_local_koikatsu_card as import_local_koikatsu_teacher_avatar, import_model_batch as import_teacher_avatar_batch, list_models as list_teacher_avatars, list_kks_character_packs, resolve_kks_character_pack, resolve_runtime_pack_asset, resolve_runtime_pack_manifest, resolve_model_file as resolve_teacher_avatar_file, today_model as todays_teacher_avatar, update_morph as update_teacher_avatar_morph
from .teacher_avatar_cards import validate_card as validate_teacher_avatar_card
from .koikatsu_card import MAX_KKS_CARD_BYTES, KoikatsuCardError, inspect_koikatsu_sunshine_card, rewrite_koikatsu_sunshine_card
from .koikatsu_teacher_registry import KoikatsuTeacherError, default_card as default_koikatsu_card, list_teachers as list_koikatsu_teachers, local_cards as list_local_koikatsu_cards, local_card_resources, resolve_card as resolve_koikatsu_teacher_card, resolve_local_card as resolve_local_koikatsu_card, resolve_local_export_asset, resolve_runtime_asset, resolve_shared_base_asset, resolve_shared_runtime_asset, resolve_web_model_asset, runtime_card_manifest, save_teacher as save_koikatsu_teacher, shared_neutral_template_manifest, validate_runtime_pack_head_contract
from .teacher_avatar_card_presets import list_card_presets as list_teacher_avatar_card_presets
from .teacher_avatar_presets import activate_preset as activate_teacher_avatar_preset, list_presets as list_teacher_avatar_presets, resolve_preview as resolve_teacher_avatar_preset_preview
from .spine_teacher_registry import MAX_ARCHIVE_BYTES as MAX_SPINE_ARCHIVE_BYTES, SpineTeacherError, active_teacher as active_spine_teacher, import_package as import_spine_teacher_package, public_manifest as spine_teacher_manifest, resolve_asset as resolve_spine_teacher_asset
from .active_teaching import (
    initialize_active_teaching_storage,
    router as active_teaching_router,
)
from .upload_limits import UploadLimitExceeded, active_upload_root, read_upload_limited
from .focus_monitoring import router as focus_monitoring_router
from .generic_knowledge_api import (
    recover_interrupted_knowledge_map_jobs,
    router as generic_knowledge_router,
)
from .video_collection_api import (
    recover_interrupted_video_collection_jobs,
    router as video_collection_router,
)
from .code_practice import router as code_practice_router
from .casual_chat import router as casual_chat_router
from .teacher_voice_pack import router as teacher_voice_pack_router
from .teacher_live_api import router as teacher_live_router
from .knowledge.map_read_api import router as verified_knowledge_map_router
from .cost_cache_control_api import router as cost_cache_control_router
from .integration_routes import (
    install_document_import,
    install_plan_product,
    install_review_product,
)
from .teaching.progress_read.api import router as progress_read_router
from .qwen_base_clone_runtime import (
    bootstrap_production_qwen_base_clone_runtime,
    shutdown_production_qwen_base_clone_runtimes,
)
from .qwen_custom_private_runtime import shutdown_private_qwen_runtime
from .conversion_adapters import ConversionRegistry, ConversionSource
from .knowledge_policy import CONVERSION_POLICY, PROVIDER_POLICY
from .open_search import OpenSearchError, search_web
from .teacher_visual_assets import (
    PRIVATE_TEACHER_ROUTE,
    private_teacher_asset_available,
    private_teacher_asset_root,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRESET_DIR = Path(
    os.getenv("MENTORFLOW_PRESET_ROOT", str(PROJECT_ROOT / "data" / "presets"))
)
PRESET_SETTINGS_FILE = Path(
    os.getenv(
        "MENTORFLOW_PRESET_SETTINGS_FILE",
        str(DEFAULT_PRESET_DIR.parent / "preset_settings.json"),
    )
)
_TASK_PROFILE_LOCK = Lock()
_LOGGER = logging.getLogger(__name__)


class _LifeAnalogyCard(BaseModel):
    """The only provider fields accepted by the private life-analogy flow."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=80)
    analogy: str = Field(min_length=1, max_length=320)
    takeaway: str = Field(min_length=1, max_length=240)


class _LifeConceptSelection(BaseModel):
    """A difficult, memorable concept selected before choosing a scene."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    anchor_index: int = Field(ge=0, le=15)
    concept_key: str = Field(min_length=2, max_length=100)
    concept_label: str = Field(min_length=2, max_length=40)
    mechanism: str = Field(min_length=4, max_length=240)
    common_confusion: str = Field(default="", max_length=240)
    memory_target: str = Field(default="", max_length=240)


class _LifeAnalogyDraft(BaseModel):
    """Provider draft with machine-checkable coherence evidence."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    concept_key: str = Field(min_length=2, max_length=100)
    concept_label: str = Field(min_length=2, max_length=40)
    concept_mechanism: str = Field(min_length=4, max_length=240)
    common_confusion: str = Field(default="", max_length=240)
    memory_target: str = Field(default="", max_length=240)
    format_key: str = Field(min_length=2, max_length=40)
    scene_family: str = Field(min_length=2, max_length=40)
    situation_key: str = Field(min_length=2, max_length=40)
    mechanism_tag: str = Field(default="", max_length=40)
    style_mode: str = Field(default="", max_length=24)
    meme_key: str = Field(default="", max_length=40)
    meme_phrase: str = Field(default="", max_length=40)
    meme_evidence: str = Field(default="", max_length=60)
    scene_signature: str = Field(min_length=2, max_length=160)
    title: str = Field(min_length=2, max_length=80)
    analogy: str = Field(min_length=30, max_length=320)
    takeaway: str = Field(min_length=12, max_length=240)
    anchor_index: int = Field(ge=0, le=15)
    mechanism_evidence: list[str] = Field(default_factory=list, max_length=5)
    twist_evidence: str = Field(default="", max_length=120)


class _LifeAnalogyEnvelope(BaseModel):
    """Optional provider envelope matching the public response contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_: str = Field(
        alias="schema",
        pattern=r"^mentorflow\.life-analogy\.v1$",
    )
    state: str = Field(pattern=r"^ready$")
    card: _LifeAnalogyCard


class _LifeAnalogyQualityReview(BaseModel):
    """Private review against the selected registered situation."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    accepted: bool
    mapping_score: int = Field(ge=1, le=5)
    relevance_score: int = Field(ge=1, le=5)
    situation_fidelity_score: int = Field(ge=1, le=5)
    logic_score: int = Field(ge=1, le=5)
    naturalness_score: int = Field(ge=1, le=5)
    sophistication_score: int = Field(ge=1, le=5)
    memorability_score: int = Field(ge=1, le=5)
    meme_fit_score: int = Field(ge=1, le=5)
    issue_code: str = Field(default="", max_length=40)


def _analyze_task_profile_safe(workspace_id: str, *, force: bool = False) -> dict[str, Any]:
    """Serialize profile writes so React StrictMode/rematch requests cannot race SQLite."""
    with _TASK_PROFILE_LOCK:
        return analyze_task_profile(workspace_id, force=force)

@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    upgrade_orm_schema()
    init_orm()
    initialize_active_teaching_storage()
    recover_interrupted_knowledge_map_jobs()
    recover_interrupted_video_collection_jobs()
    cleanup_expired_previews()
    # Missing or invalid engine release material is recorded fail-closed. The
    # bootstrap accepts only the fixed registry -> root-signed policy -> leaf
    # receipt -> signed engine chain and never starts its child/model.
    bootstrap_status = await asyncio.to_thread(
        bootstrap_production_qwen_base_clone_runtime
    )
    _app.state.qwen_voice_bootstrap = bootstrap_status
    if bootstrap_status.get("state") == "failed_closed":
        _LOGGER.warning(
            "Qwen voice runtime registration failed closed: %s",
            (bootstrap_status.get("error") or {}).get("code", "unknown"),
        )
    try:
        yield
    finally:
        document_import_host = getattr(
            _app.state,
            "document_import_host",
            None,
        )
        if document_import_host is not None:
            await asyncio.to_thread(document_import_host.close)
        # The private prerelease runtime is also an app-owned Windows Job.
        # It is lazy (normally absent), but when present must be terminated as
        # part of application shutdown rather than left behind after a reload.
        try:
            private_shutdown_report = await asyncio.to_thread(
                shutdown_private_qwen_runtime
            )
        except BaseException as exc:
            private_shutdown_report = None
            _app.state.private_qwen_voice_shutdown_error = {
                "code": "PRIVATE_VOICE_RUNTIME_SHUTDOWN_FAILED",
                "error": f"{type(exc).__name__}:{exc}",
            }
            _LOGGER.exception("Private Qwen voice runtime shutdown raised unexpectedly")
        else:
            _app.state.private_qwen_voice_shutdown_error = None
        _app.state.private_qwen_voice_shutdown = private_shutdown_report
        # The registry contains only production supervisors created from a
        # verified engine capability. Each supervisor terminates only the
        # process tree whose Windows Job handle it retained at launch.
        try:
            shutdown_report = await asyncio.to_thread(
                shutdown_production_qwen_base_clone_runtimes
            )
        except BaseException as exc:
            shutdown_error = {
                "code": "VOICE_RUNTIME_SHUTDOWN_FAILED",
                "error": f"{type(exc).__name__}:{exc}",
            }
            _app.state.qwen_voice_shutdown = None
            _app.state.qwen_voice_shutdown_error = shutdown_error
            _LOGGER.exception("Qwen voice runtime shutdown raised unexpectedly")
            raise
        _app.state.qwen_voice_shutdown = shutdown_report
        failures = list(shutdown_report.get("failures") or [])
        if failures:
            _app.state.qwen_voice_shutdown_error = {
                "code": "VOICE_RUNTIME_SHUTDOWN_INCOMPLETE",
                "failures": failures,
            }
            _LOGGER.error(
                "Qwen voice runtime shutdown completed with %d owned-runtime failure(s): %s",
                len(failures),
                failures,
            )
        else:
            _app.state.qwen_voice_shutdown_error = None

app = FastAPI(title="MentorFlow", version="2.0.0", lifespan=lifespan)
install_review_product(app)
install_plan_product(app)
install_document_import(app)
app.include_router(active_teaching_router)
app.include_router(focus_monitoring_router)
app.include_router(generic_knowledge_router)
app.include_router(video_collection_router)
app.include_router(code_practice_router)
app.include_router(casual_chat_router)
app.include_router(teacher_voice_pack_router)
app.include_router(teacher_live_router)
app.include_router(verified_knowledge_map_router)
app.include_router(cost_cache_control_router)
app.include_router(progress_read_router)
_configured_origins = [
    item.strip().rstrip("/")
    for item in os.getenv(
        "MENTORFLOW_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    import uuid
    request.state.request_id=uuid.uuid4().hex
    response=await call_next(request)
    response.headers['X-Request-ID']=request.state.request_id
    return response

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code,content=app_error_body(exc.code,exc.message,exc.details,getattr(request.state,'request_id',None)))

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors=redact([
        {
            "type": str(error.get("type") or "validation_error"),
            "loc": [str(value) for value in error.get("loc") or []],
            "msg": str(error.get("msg") or "invalid value"),
        }
        for error in exc.errors()
    ])
    return JSONResponse(status_code=422,content=app_error_body('VALIDATION_ERROR','请求参数不符合要求',{'errors':errors},getattr(request.state,'request_id',None)))

@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail=exc.detail if isinstance(exc.detail,dict) else {'message':str(exc.detail)}
    error=detail.get('error',{}) if isinstance(detail,dict) else {}
    return JSONResponse(status_code=exc.status_code,content=app_error_body(error.get('code','HTTP_ERROR'),error.get('message',detail.get('message','请求失败')),error.get('details',{}),getattr(request.state,'request_id',None)))

@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError):
    """Keep recoverable Provider failures machine-readable across every router."""
    return JSONResponse(
        status_code=exc.status,
        content=app_error_body(
            exc.code,
            exc.message,
            exc.details,
            getattr(request.state, "request_id", None),
        ),
    )

@app.exception_handler(Exception)
async def unknown_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500,content=app_error_body('INTERNAL_ERROR','服务暂时无法完成请求',{},getattr(request.state,'request_id',None)))


def fail(code: str, message: str, status: int = 400, details: dict | None = None):
    raise HTTPException(status_code=status, detail={"error": {"code": code, "message": message, "details": details or {}, "request_id": uuid4().hex}})


def _preset_scope(request: Request) -> tuple[Path, Path]:
    """Return the one approved preset root for the local data store."""
    default_root = (PRESET_SETTINGS_FILE.parent / "presets").resolve()
    return default_root, PRESET_SETTINGS_FILE


def preset_directory(request: Request, requested: str | None = None) -> Path:
    approved_root, settings_file = _preset_scope(request)
    directory = Path(requested).expanduser() if str(requested or "").strip() else approved_root
    if not requested and settings_file.exists():
        try:
            saved = json.loads(settings_file.read_text(encoding="utf-8")).get("directory")
            if saved:
                directory = Path(saved).expanduser()
        except (OSError, ValueError, TypeError):
            directory = approved_root
    try:
        resolved_root = approved_root.resolve()
        resolved_directory = directory.resolve()
        within_root = resolved_directory == resolved_root or resolved_root in resolved_directory.parents
    except OSError:
        within_root = False
        resolved_directory = directory
    if not within_root:
        fail(
            "PRESET_DIRECTORY_NOT_ALLOWED",
            "预设目录必须位于当前账户的专用数据目录内。",
        )
    resolved_directory.mkdir(parents=True, exist_ok=True)
    return resolved_directory


def preset_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name.strip() if ch.isalnum() or ch in " -_().")[:80].strip(" .")
    if not cleaned:
        fail("PRESET_NAME_REQUIRED", "预设名称不能为空")
    return f"{cleaned}.json"


@app.get("/api/presets")
def list_presets(request: Request, directory: str | None = None):
    target = preset_directory(request, directory)
    items = []
    for item in sorted(target.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(item.read_text(encoding="utf-8"))
            if payload.get("name") and isinstance(payload.get("form"), dict):
                items.append(payload)
        except (OSError, ValueError):
            continue
    return {"directory": str(target), "presets": items}


@app.post("/api/presets")
def save_preset(request: Request, body: dict[str, Any]):
    name = str(body.get("name") or "").strip()
    filename = preset_filename(name)
    form = body.get("form")
    if not isinstance(form, dict):
        fail("PRESET_FORM_REQUIRED", "预设内容不能为空")
    target = preset_directory(request, body.get("directory"))
    saved = {"name": name, "saved_at": datetime.now(timezone.utc).isoformat(), "form": form}
    (target / filename).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    _approved_root, settings_file = _preset_scope(request)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({"directory": str(target)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return saved | {"directory": str(target)}


def payload(row: dict | None) -> dict | None:
    if row and isinstance(row.get("payload"), str): row["payload"] = json.loads(row["payload"])
    return row


def require_workspace(workspace_id: str) -> dict:
    item = db.row("workspaces", "id", workspace_id)
    if not item: fail("WORKSPACE_NOT_FOUND", "学习空间不存在", 404)
    return item


def workspace_mode(workspace_id: str) -> str:
    return str(require_workspace(workspace_id).get("execution_mode") or "demo")


def session_id_from(request: Request | None, body: dict[str, Any] | None) -> str | None:
    return (body or {}).get("provider_session_id") or (request.headers.get("X-Provider-Session-ID") if request else None)


def provider_for_workspace(workspace_id: str, request: Request | None = None, body: dict[str, Any] | None = None):
    ws = require_workspace(workspace_id)
    if ws.get("execution_mode", "demo") == "demo":
        return create_verified_demo_session()
    try:
        session = require_provider_session(
            session_id_from(request, body),
        )
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)
    if session.provider == "fake":
        fail("PROVIDER_SESSION_REAL_REQUIRED", "真实 Workspace 不能使用 Fake Provider")
    if ws.get("provider_name") and ws.get("provider_name") != session.provider:
        fail("PROVIDER_SESSION_MISMATCH", "当前 Provider 会话与学习空间绑定的 Provider 不一致", 409, {"provider": ws.get("provider_name")})
    return session


def attach_workspace_provider(workspace_id: str, session) -> None:
    ws = require_workspace(workspace_id)
    if ws.get("execution_mode", "demo") == "demo":
        return
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET provider_name=?, provider_model=?, updated_at=? WHERE id=?", (session.provider, session.model, db.now(), workspace_id))
    try:
        from .orm import SessionLocal, WorkspaceORM
        with SessionLocal() as db_session:
            record = db_session.get(WorkspaceORM, workspace_id)
            if record:
                record.provider_name = session.provider
                record.provider_model = session.model
                db_session.commit()
    except Exception:
        pass


async def call_bound_provider_json(
    session,
    messages: list[dict[str, Any]],
    *,
    compact_max_tokens: int | None = None,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    provider = provider_for_session(session)
    result = (
        await provider.compact_json(
            messages,
            max_tokens=compact_max_tokens,
            temperature=temperature,
        )
        if compact_max_tokens is not None
        else await provider.json(messages, temperature)
    )
    return result, generation_meta(session, latency_ms=max(1, int((time.perf_counter() - started) * 1000)))


async def call_bound_provider_tutor(
    session,
    messages: list[dict[str, Any]],
    local_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call the tutor while tolerating a plain-text answer from a chat model.

    Planning and assessment endpoints remain strict JSON contracts.  A tutor
    response, however, is still useful when a fast model ignores the JSON
    instruction and returns normal prose.  Preserve that prose as the visible
    message instead of turning a successful teaching turn into a protocol
    error.
    """

    started = time.perf_counter()
    raw = await provider_for_session(session).text(messages)
    result, fallback_used = parse_conversational_payload(
        raw,
        text_field="message",
        fallback={
            "status": local_result.get("status", "insufficient"),
            "confidence": 0.5,
            "citations": [],
            "suggested_next_action": "",
            "progress_update": {},
            "warnings": [],
        },
    )
    if fallback_used:
        result["warnings"] = ["Provider 未遵循 JSON 格式；已保留原始教师回复。"]
    return result, generation_meta(session, latency_ms=max(1, int((time.perf_counter() - started) * 1000)))


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "MentorFlow", "demo_mode": True, "catalog": catalog_repository.health()}


@app.get("/api/workspaces", response_model=list[WorkspaceOut], response_model_exclude_unset=True)
def list_workspaces():
    items = [dict(x) for x in db.rows("workspaces", "status", "active")]
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


@app.post("/api/workspaces", response_model=WorkspaceOut, response_model_exclude_unset=True)
def create_workspace(body: WorkspaceIn, request: Request):
    provider_name, provider_model = ("fake", "fake") if body.execution_mode == "demo" else ("", "")
    if body.provider_session_id:
        try:
            provider_session = require_provider_session(body.provider_session_id)
        except ProviderError as exc:
            fail(exc.code, exc.message, exc.status, exc.details)
        if body.execution_mode == "demo" and provider_session.provider != "fake":
            fail("DEMO_PROVIDER_INVALID", "Demo Workspace 只能使用 Fake Provider")
        if body.execution_mode == "real":
            if provider_session.provider == "fake":
                fail("PROVIDER_SESSION_REAL_REQUIRED", "真实 Workspace 不能使用 Fake Provider")
            provider_name, provider_model = provider_session.provider, provider_session.model
    item = {"id": uid("ws"), "title": body.title, "description": body.description, "status": "active", "character_id": body.character_id, "allow_external_knowledge": int(body.allow_external_knowledge), "persona_prompt": body.persona_prompt.strip(), "execution_mode": body.execution_mode, "provider_name": provider_name, "provider_model": provider_model, "created_at": db.now(), "updated_at": db.now()}
    try:
        with db.connection() as conn: conn.execute("INSERT INTO workspaces (id,title,description,status,character_id,allow_external_knowledge,persona_prompt,execution_mode,provider_name,provider_model,created_at,updated_at) VALUES (:id,:title,:description,:status,:character_id,:allow_external_knowledge,:persona_prompt,:execution_mode,:provider_name,:provider_model,:created_at,:updated_at)", item)
    except Exception:
        raise
    try:
        from .orm import SessionLocal, WorkspaceORM
        with SessionLocal() as session:
            session.add(WorkspaceORM(id=item["id"],title=item["title"],description=item["description"],character_id=item["character_id"],allow_external_knowledge=bool(item["allow_external_knowledge"]),execution_mode=item["execution_mode"],provider_name=item["provider_name"],provider_model=item["provider_model"])); session.commit()
    except Exception: pass
    return item


@app.get("/api/workspaces/{workspace_id}", response_model=WorkspaceDetailOut, response_model_exclude_unset=True)
def get_workspace(workspace_id: str):
    ws = require_workspace(workspace_id)
    ws["sources"] = [
        _public_source(
            {
                **{k: v for k, v in dict(x).items() if k != "text"},
                "chunk_count": len(db.rows("chunks", "source_id", x["id"])),
            }
        )
        for x in db.rows("sources", "workspace_id", workspace_id)
    ]
    goal = db.row("goals", "workspace_id", workspace_id); ws["goal"] = payload(goal)
    assessment = db.rows("assessments", "workspace_id", workspace_id); ws["feasibility"] = payload(assessment[-1]) if assessment else None
    plans = db.rows("plans", "workspace_id", workspace_id); ws["plans"] = [payload(x) for x in plans]
    return ws


@app.patch("/api/workspaces/{workspace_id}")
def patch_workspace(workspace_id: str, body: WorkspacePatch):
    ws = require_workspace(workspace_id); changes = body.model_dump(exclude_none=True)
    if not changes: return ws
    assignments = ", ".join(f"{k} = ?" for k in changes); values = [int(v) if isinstance(v, bool) else v for v in changes.values()]
    with db.connection() as conn: conn.execute(f"UPDATE workspaces SET {assignments}, updated_at = ? WHERE id = ?", (*values, db.now(), workspace_id))
    return require_workspace(workspace_id)


@app.get("/api/workspaces/{workspace_id}/persona")
def get_workspace_persona(workspace_id: str):
    ws = require_workspace(workspace_id)
    return {"workspace_id": workspace_id, "persona_prompt": str(ws.get("persona_prompt") or "")}


@app.patch("/api/workspaces/{workspace_id}/persona")
def patch_workspace_persona(workspace_id: str, body: dict[str, Any]):
    require_workspace(workspace_id)
    persona = str(body.get("persona_prompt") or "").strip()
    if len(persona) > 2000:
        fail("PERSONA_TOO_LONG", "鏁欏笀人设最多 2000 个字符", 422)
    with db.connection() as conn:
        conn.execute("UPDATE workspaces SET persona_prompt=?, updated_at=? WHERE id=?", (persona, db.now(), workspace_id))
    return {"workspace_id": workspace_id, "persona_prompt": persona}


@app.delete("/api/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str):
    require_workspace(workspace_id)
    with db.connection() as conn:
        # These two tables predate the workspace foreign-key graph.  Clean
        # their workspace-scoped rows explicitly so removing a task cannot
        # leave an orphaned chat or catalog request behind.
        for table in ("casual_chat_sessions", "catalog_curation_requests"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists:
                conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (workspace_id,))
        conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    try:
        from .orm import SessionLocal, WorkspaceORM
        with SessionLocal() as session:
            row=session.get(WorkspaceORM,workspace_id)
            if row: session.delete(row); session.commit()
    except Exception: pass
    return {"deleted": True}


@app.post("/api/local-sources/preview")
def local_preview(body: LocalSourceIn):
    try:
        return _public_local_preview(create_preview(body.path, body.allowed_root))
    except (ValueError, OSError) as exc:
        # Validation normally uses stable messages, but keep this boundary
        # safe if a filesystem implementation includes the requested path in
        # an exception (for example a temporary directory named by a user).
        # The full exception remains in internal logs for diagnosis; the HTTP
        # contract only exposes stable, path-free messages.
        _log_local_failure("local preview", exc)
        fail(
            "LOCAL_PATH_REJECTED",
            _public_local_error(exc, operation="preview"),
        )


_PUBLIC_PARSE_ERROR_OCR = "当前版本未启用 OCR，不生成假内容"
_PUBLIC_PARSE_ERROR_GENERIC = "本地文件解析失败"
_PUBLIC_LOCAL_PREVIEW_ERROR = "本地路径无法读取"
_PUBLIC_LOCAL_IMPORT_ERROR = "本地文件导入失败，请重新预览后重试"
_STABLE_LOCAL_PREVIEW_ERRORS = frozenset(
    {
        "路径必须位于允许根目录内",
        "允许的根目录不存在或不是目录",
        "拒绝使用系统目录或依赖目录作为允许根目录",
        "拒绝盘符根目录",
        "拒绝整个用户主目录",
        "拒绝系统、依赖或敏感目录",
        "没有可导入的支持格式文件",
    }
)
_STABLE_LOCAL_IMPORT_ERRORS = frozenset(
    {
        "必须选择至少一个文件",
        "Preview 不存在或已过期",
        "Preview 已过期",
        "selected_file_ids 不属于当前 Preview",
        "Preview 文件已移动或逃逸允许目录",
        "Preview 文件已发生变化，请重新预览",
    }
)


def _log_local_failure(operation: str, exc: BaseException) -> None:
    """Retain filesystem diagnostics without making them a public response."""

    _LOGGER.warning("%s failed: %s", operation, exc, exc_info=True)


def _public_local_error(exc: BaseException, *, operation: str) -> str:
    """Return a stable local-import message that cannot contain a path."""

    if isinstance(exc, OSError):
        return (
            _PUBLIC_LOCAL_PREVIEW_ERROR
            if operation == "preview"
            else _PUBLIC_LOCAL_IMPORT_ERROR
        )
    message = str(exc)
    stable = (
        _STABLE_LOCAL_PREVIEW_ERRORS
        if operation == "preview"
        else _STABLE_LOCAL_IMPORT_ERRORS
    )
    return message if message in stable else (
        _PUBLIC_LOCAL_PREVIEW_ERROR
        if operation == "preview"
        else _PUBLIC_LOCAL_IMPORT_ERROR
    )


def _public_filename(value: Any, *, fallback: str = "") -> str:
    """Return only a safe display basename for a stored/uploaded filename."""

    if value is None:
        return fallback
    raw = str(value).replace("\x00", "").strip()
    if not raw:
        return fallback
    # Handle both Windows and POSIX separators regardless of the host running
    # the API.  A drive prefix is storage syntax, not a useful display name.
    basename = raw.rstrip("/\\").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if re.match(r"^[A-Za-z]:", basename):
        basename = basename[2:].lstrip("/\\")
    if basename in {"", ".", ".."}:
        return fallback
    return basename


def _public_parse_error(value: Any) -> str:
    """Keep only stable parse outcomes; never expose parser exception text."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw == _PUBLIC_PARSE_ERROR_OCR:
        return _PUBLIC_PARSE_ERROR_OCR
    if raw == _PUBLIC_PARSE_ERROR_GENERIC:
        return _PUBLIC_PARSE_ERROR_GENERIC
    return _PUBLIC_PARSE_ERROR_GENERIC


def _public_source(source: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a persisted source without exposing storage-only metadata."""

    if source is None:
        return None
    output = dict(source)
    output.pop("local_path", None)
    if "original_filename" in output:
        output["original_filename"] = _public_filename(output.get("original_filename"))
    if "parse_error" in output:
        output["parse_error"] = _public_parse_error(output.get("parse_error"))
    # Legacy upload/local-file rows historically copied the storage path into
    # ``title`` as well.  Preserve normal user titles, but make those two
    # file-backed source types safe for old rows.
    if output.get("source_type") in {"upload", "local_file"} and output.get("title"):
        output["title"] = _public_filename(output.get("title"), fallback="未命名文件")
    if output.get("source_type") in {"archive", "archive_file"}:
        if "parent_archive_name" in output:
            output["parent_archive_name"] = _public_filename(
                output.get("parent_archive_name"), fallback="study_bundle.zip"
            )
        if "original_filename" in output:
            output["original_filename"] = _public_filename(
                output.get("original_filename"), fallback="未命名文件"
            )
        title = str(output.get("title") or "")
        if output.get("source_type") == "archive_file" and " / " in title:
            archive_name, relative = title.split(" / ", 1)
            relative = "/".join(
                part
                for part in relative.replace("\\", "/").split("/")
                if part not in {"", ".", ".."}
            )
            if not relative or Path(relative).is_absolute() or ":" in relative[:3]:
                relative = _public_filename(relative, fallback="未命名文件")
            output["title"] = f"{_public_filename(archive_name, fallback='study_bundle.zip')} / {relative}"
        elif title:
            output["title"] = _public_filename(title, fallback="study_bundle.zip")
    return output


def _public_local_preview(preview: dict[str, Any]) -> dict[str, Any]:
    """Project a local preview with relative labels and opaque file IDs only.

    ``create_preview`` intentionally retains absolute paths in its short-lived
    database record so the confirmed import can revalidate and read the files.
    That representation is internal; clients only need the stable file ID and
    a display-safe path relative to the user-selected root.
    """

    root_value = str(preview.get("root") or "")
    try:
        root = Path(os.path.abspath(os.fspath(Path(root_value).expanduser())))
    except (OSError, RuntimeError, TypeError, ValueError):
        root = None

    def project_item(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {"id": str(item)}
        output = {
            key: value
            for key, value in item.items()
            if key not in {"path", "snapshot_path", "parse_path", "identity", "snapshot_identity"}
        }
        if "name" in output:
            output["name"] = _public_filename(output.get("name"))
        if "reason" in output:
            # Preserve stable validation reasons while collapsing filesystem
            # errors that may contain an absolute path.
            output["reason"] = (
                str(output["reason"])
                if str(output["reason"]) in {
                    "超过目录深度限制",
                    "不支持的文件类型",
                    "超过单文件大小限制",
                    "超过导入数量或总大小限制",
                    "超过数量或总大小限制",
                    "敏感或隐藏文件/目录",
                    "拒绝 symlink/junction",
                    "拒绝硬链接或特殊文件",
                    "拒绝特殊文件",
                    "文件已发生变化，请重新预览",
                }
                else "文件无法读取"
            )
        raw_path = item.get("path")
        relative = item.get("relative_path")
        if root is not None and raw_path:
            try:
                raw_candidate = Path(os.path.abspath(os.fspath(Path(str(raw_path)).expanduser())))
                relative = str(raw_candidate.relative_to(root))
            except (OSError, RuntimeError, ValueError):
                relative = None
        if relative:
            # Normalize for browser clients and ensure a malformed internal
            # value cannot reintroduce an absolute path.
            relative_path = str(relative).replace("\\", "/")
            if (
                Path(relative_path).is_absolute()
                or ":" in relative_path[:3]
                or any(part == ".." for part in relative_path.split("/"))
            ):
                relative_path = _public_filename(relative_path)
            output["relative_path"] = relative_path
        elif item.get("name"):
            output["relative_path"] = _public_filename(item.get("name"))
        return output

    output = {
        key: value
        for key, value in preview.items()
        if key not in {"root", "files", "rejected"}
    }
    output["files"] = [project_item(item) for item in preview.get("files", [])]
    output["rejected"] = [project_item(item) for item in preview.get("rejected", [])]
    return output


@app.get("/api/workspaces/{workspace_id}/sources", response_model=list[SourceSummaryOut], response_model_exclude_unset=True)
def list_sources(workspace_id: str):
    require_workspace(workspace_id)
    items=[]
    for row in db.rows("sources", "workspace_id", workspace_id):
        item=dict(row); item.pop("text",None); item["chunk_count"]=len(db.rows("chunks","source_id",item["id"])); items.append(_public_source(item))
    return items


@app.get("/api/sources/{source_id}")
def get_source(source_id: str):
    source = source_detail(source_id)
    if not source: fail("SOURCE_NOT_FOUND", "资料不存在", 404)
    return _public_source(source)


@app.post("/api/workspaces/{workspace_id}/sources/text")
def add_text_source(workspace_id: str, body: TextSourceIn):
    require_workspace(workspace_id); jid=create_job(workspace_id,"source_import"); set_job(jid,status="running",stage="chunking",progress=50,message="Indexing text")
    try:
        created=create_source(workspace_id, body.title, "text", body.text, trust_level=body.trust_level); result=_public_source(source_detail(created["id"])); result["job_id"]=jid; set_job(jid,status="succeeded",stage="saving",progress=100,message="Source indexed"); return result
    except ValueError as exc:
        set_job(jid,status="failed",progress=100,error=str(exc),message="Duplicate source"); fail("DUPLICATE_SOURCE",str(exc),409)
    except Exception as exc:
        set_job(jid,status="failed",progress=100,error=str(exc),message="Source import failed"); raise


@app.post("/api/workspaces/{workspace_id}/sources/local-path")
def add_local_source(workspace_id: str, body: LocalSourceIn):
    require_workspace(workspace_id)
    jid=create_job(workspace_id,"source_import"); set_job(jid,status="running",stage="reading",progress=20,message="Reading confirmed files")
    if not body.preview_id or not body.selected_file_ids:
        set_job(jid,status="failed",progress=100,error="preview and selected files required",message="Local import rejected")
        fail("LOCAL_PREVIEW_REQUIRED", "必须先预览并确认至少一个文件")
    try: selected = consume_preview(body.preview_id, body.selected_file_ids)
    except (ValueError, OSError) as exc:
        _log_local_failure("local import preview", exc)
        message = _public_local_error(exc, operation="import")
        set_job(jid,status="failed",progress=100,error=message,message="Local import rejected")
        fail("LOCAL_IMPORT_REJECTED", message)
    imported = []
    try:
        for item in selected:
            display_name = _public_filename(item.get("path"), fallback="未命名文件")
            parse_path = item.get("parse_path") or item.get("path")
            try:
                # consume_preview pins the user-owned path and supplies an
                # exclusive private snapshot.  The parser therefore never
                # reopens a path that can be swapped by the caller.
                snapshot_identity = FileIdentity.from_dict(item.get("snapshot_identity"))
                if snapshot_identity is not None and "expected_identity" in inspect.signature(parse_file).parameters:
                    parts,status,pages=parse_file(Path(parse_path), expected_identity=snapshot_identity)
                else:
                    parts,status,pages=parse_file(Path(parse_path))
                text='\n\n'.join(x['text'] for x in parts)
                if status=='needs_ocr': imported.append({"title":display_name,"status":"needs_ocr","parse_error":_PUBLIC_PARSE_ERROR_OCR}); continue
                imported.append(_public_source(source_detail(create_source(workspace_id, display_name, "local_file", text, original_filename=display_name, local_path=item["path"], page_count=pages, trust_level=body.trust_level, parts=parts)["id"])))
            except Exception as exc:
                # Parsing failures are intentionally generic; the selected file's
                # internal absolute path is not part of the public error contract.
                _log_local_failure("local file parse", exc)
                imported.append({"title": display_name, "status": "error", "parse_error": _PUBLIC_PARSE_ERROR_GENERIC})
    finally:
        cleanup_local_preview(body.preview_id)
    set_job(jid,status="succeeded",stage="saving",progress=100,message="Local sources indexed")
    return {"preview_id":body.preview_id,"job_id":jid,"sources":imported}


@app.post("/api/workspaces/{workspace_id}/sources/upload")
async def upload_source(workspace_id: str, file: UploadFile = File(...)):
    require_workspace(workspace_id)
    jid=create_job(workspace_id,"source_import"); set_job(jid,status="running",stage="validating",progress=10,message="Validating upload")
    filename = _public_filename(file.filename, fallback="material.bin")
    try:
        data = await read_upload_limited(file, CONVERSION_POLICY.maximum_input_bytes)
    except UploadLimitExceeded as exc:
        set_job(jid,status="failed",stage="validating",progress=100,error="INPUT_BUDGET_EXCEEDED",message="Upload rejected")
        fail(
            "INPUT_BUDGET_EXCEEDED",
            "文件超过能力注册表配置的单文件限制",
            413,
            exc.details,
        )
    conversion = ConversionRegistry().convert_safe(
        ConversionSource(
            name=filename,
            data=data,
            mime_type=str(file.content_type or ""),
        )
    )
    if conversion.status != "ready" or conversion.metadata is None:
        failure = conversion.failure
        code = failure.code if failure else "CONVERSION_FAILED"
        message = failure.message if failure else "文件转换未产生可用内容"
        status = 415 if code in {"UNSUPPORTED_FORMAT", "VIDEO_UNSUPPORTED_IN_V1"} else 422
        if code in {"INPUT_BUDGET_EXCEEDED", "OUTPUT_BUDGET_EXCEEDED"}:
            status = 413
        set_job(jid,status="failed",stage="parsing",progress=100,error=code,message="Parse failed")
        fail(code, message, status, failure.details if failure else {})
    suffix = Path(filename).suffix.lower()
    dest = active_upload_root() / "legacy-sources" / f"{uuid4().hex}{suffix}"; dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    parts = [
        {
            "text": part.text,
            "page_start": part.page_start,
            "page_end": part.page_end,
            "heading": part.heading,
        }
        for part in conversion.parts
    ]
    pages = conversion.metadata.page_count
    text='\n\n'.join(part["text"] for part in parts)
    try:
        result=_public_source(source_detail(create_source(workspace_id, filename, "upload", text, original_filename=filename, local_path=str(dest), page_count=pages, parts=parts)["id"])); result["job_id"]=jid; result["conversion"]={"adapter":conversion.metadata.adapter,"adapter_version":conversion.metadata.adapter_version,"warnings":list(conversion.metadata.warnings)}; set_job(jid,status="succeeded",stage="saving",progress=100,message="Upload indexed"); return result
    except ValueError as exc:
        dest.unlink(missing_ok=True)
        set_job(jid,status="failed",progress=100,error=str(exc),message="Duplicate source")
        fail("DUPLICATE_SOURCE", str(exc), 409)


_PUBLIC_ARCHIVE_PREVIEW_FIELDS = (
    "id",
    "preview_id",
    "workspace_id",
    "archive_name",
    "archive_hash",
    "archive_size_bytes",
    "expanded_size_bytes",
    "file_count",
    "expires_at",
    "created_at",
    "tree",
    "supported_count",
    "ignored_count",
    "rejected_count",
    "warnings",
)


def _public_archive_preview(preview: dict[str, Any]) -> dict[str, Any]:
    """Project archive preview metadata without exposing temp storage paths."""

    output = {
        key: preview[key]
        for key in _PUBLIC_ARCHIVE_PREVIEW_FIELDS
        if key in preview
    }
    # Upload clients can send a path-like filename. Keep only a display label
    # so that even user-controlled names cannot echo an absolute local path.
    if "archive_name" in output:
        output["archive_name"] = _public_filename(
            output["archive_name"], fallback="study_bundle.zip"
        )
    return output


@app.post("/api/workspaces/{workspace_id}/sources/archive/preview")
async def preview_archive_source(workspace_id: str, file: UploadFile = File(...)):
    require_workspace(workspace_id)
    filename = file.filename or "study_bundle.zip"
    if Path(filename).suffix.lower() != ".zip":
        fail("ARCHIVE_INVALID_FORMAT", "资料包必须是 .zip 文件")
    data = bytearray()
    while True:
        block = await file.read(1024 * 1024)
        if not block:
            break
        data.extend(block)
        if len(data) > 100 * 1024 * 1024:
            fail("ARCHIVE_TOO_LARGE", "单 ZIP 上传不能超过 100 MB", 413)
    try:
        return _public_archive_preview(
            create_archive_preview(workspace_id, filename, bytes(data))
        )
    except ArchiveError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.post("/api/workspaces/{workspace_id}/sources/archive/import")
def import_archive_source(workspace_id: str, body: ArchiveImportIn):
    require_workspace(workspace_id)
    try:
        return import_archive_preview(workspace_id, body.preview_id, body.selected_file_ids, body.trust_level)
    except ArchiveError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.delete("/api/workspaces/{workspace_id}/sources/archive/preview/{preview_id}")
def cancel_archive_preview(workspace_id: str, preview_id: str):
    require_workspace(workspace_id)
    try:
        return {"cancelled": discard_preview(workspace_id, preview_id)}
    except ArchiveError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.post("/api/workspaces/{workspace_id}/sources/url")
async def add_url_source(workspace_id: str, body: UrlSourceIn):
    require_workspace(workspace_id)
    jid=create_job(workspace_id,"url_fetch"); set_job(jid,status="running",stage="validating",progress=10,message="Validating URL")
    try:
        fetched=await fetch_safe(body.url)
        result=_public_source(source_detail(create_source(workspace_id, body.title or fetched['title'], 'url', fetched['text'], original_url=body.url, trust_level=body.trust_level, final_url=fetched['final_url'], fetched_at=fetched['fetched_at'], content_type=fetched['content_type'], snapshot_hash=fetched['snapshot_hash'])['id'])); result['job_id']=jid; set_job(jid,status='succeeded',stage='saving',progress=100,message='URL indexed'); return result
    except ValueError as exc:
        set_job(jid,status='failed',stage='reading',progress=100,error=str(exc),message='URL fetch failed')
        fail('URL_FETCH_FAILED',str(exc))
    require_workspace(workspace_id); parsed = urlparse(body.url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname: fail("URL_REJECTED", "只允许 http/https URL")
    try:
        import ipaddress, socket
        for info in socket.getaddrinfo(parsed.hostname, None):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local: fail("SSRF_BLOCKED", "拒绝访问内网、回环或链路本地地址")
        import httpx
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client: response = await client.get(body.url)
        if response.status_code >= 400: fail("URL_FETCH_FAILED", f"网页返回 HTTP {response.status_code}")
        text = response.text[:200_000]
        from bs4 import BeautifulSoup
        text = BeautifulSoup(text, "html.parser").get_text("\n")
    except HTTPException: raise
    except Exception as exc: fail("URL_FETCH_FAILED", f"无法抓取网页：{exc}")
    return _public_source(source_detail(create_source(workspace_id, parsed.netloc, "url", text, original_url=body.url, trust_level=body.trust_level)["id"]))

def _historical_upload_root() -> Path:
    """Return the one canonical upload root used before the user-data move."""

    return (Path(__file__).resolve().parents[2] / "data" / "uploads").resolve()


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str):
    source=db.row("sources", "id", source_id)
    if not source: fail("SOURCE_NOT_FOUND", "资料不存在", 404)
    with db.connection() as conn: conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    local_path=source.get("local_path") or ""
    if local_path:
        try:
            raw_candidate = Path(local_path).expanduser()
            # Resolve the historical root at the call site so this compatibility
            # path remains the one canonical legacy location. A path may only be
            # removed when both the active and historical roots contain the
            # resolved file. Checking every component rejects symlink/reparse
            # points even when their target happens to resolve back inside a
            # trusted root.
            allowed_roots = (
                active_upload_root(),
                _historical_upload_root(),
            )
            if any(part.is_symlink() for part in (raw_candidate, *raw_candidate.parents)):
                raise OSError("upload path is a symlink")
            candidate = raw_candidate.resolve(strict=True)
            if not candidate.is_file():
                raise OSError("upload path is not a file")
            if not any(
                candidate != root and root in candidate.parents
                for root in allowed_roots
            ):
                raise OSError("upload path is outside the upload roots")
            candidate.unlink()
        except (OSError, RuntimeError):
            pass
    try:
        from .orm import SessionLocal, SourceORM
        with SessionLocal() as session:
            row=session.get(SourceORM,source_id)
            if row: session.delete(row); session.commit()
    except Exception: pass
    return {"deleted": True}


@app.post("/api/workspaces/{workspace_id}/goal", response_model=GoalOut, response_model_exclude_unset=True)
def set_goal(workspace_id: str, body: GoalProfile):
    require_workspace(workspace_id); created = db.now(); data = body.model_dump(mode="json"); gid = uid("goal")
    with db.connection() as conn:
        conn.execute("INSERT INTO goals VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET payload=excluded.payload, confirmed=0, updated_at=excluded.updated_at", (gid, workspace_id, json.dumps(data, ensure_ascii=False), 0, created, created))
    try:
        from .orm import SessionLocal, GoalORM
        with SessionLocal() as session:
            current=session.query(GoalORM).filter_by(workspace_id=workspace_id).first()
            if current:
                for key,value in data.items():
                    if hasattr(current,key): setattr(current,key,value)
                current.confirmed=False
            else:
                session.add(GoalORM(id=gid,workspace_id=workspace_id,goal_statement=data["goal_statement"],motivation=data.get("motivation",""),deadline=date.fromisoformat(data["deadline"]),minutes_per_day=data["minutes_per_day"],days_per_week=data["days_per_week"],preferred_weekdays=data.get("preferred_weekdays",[]),current_level=data["current_level"],expected_output=data["expected_output"],unavailable_dates=data.get("unavailable_dates",[]),success_criteria=data.get("success_criteria",[]),confirmed=False))
            session.commit()
    except Exception: pass
    item = db.row("goals", "workspace_id", workspace_id); return payload(item)


@app.get("/api/workspaces/{workspace_id}/goal")
def get_goal(workspace_id: str): require_workspace(workspace_id); return payload(db.row("goals", "workspace_id", workspace_id)) or {}


@app.patch("/api/workspaces/{workspace_id}/goal")
def patch_goal(workspace_id: str, body: GoalPatch):
    require_workspace(workspace_id); current = get_goal(workspace_id)
    if not current: fail("GOAL_NOT_FOUND", "请先创建目标")
    data = {**current["payload"], **body.model_dump(mode="json", exclude_none=True)}; data.pop("id", None)
    return set_goal(workspace_id, GoalProfile.model_validate(data))


@app.post("/api/workspaces/{workspace_id}/feasibility/analyze")
async def feasibility_analyze(workspace_id: str, request: Request, body: dict[str, Any] | None = None):
    require_workspace(workspace_id); goal = get_goal(workspace_id)
    if not goal: fail("GOAL_REQUIRED", "请先填写并保存目标")
    jid=create_job(workspace_id,'feasibility_analysis'); set_job(jid,status='running',stage='analyzing_goal',progress=25,message='正在分析目标')
    try:
        session = provider_for_workspace(workspace_id, request, body)
        attach_workspace_provider(workspace_id, session)
        result = analyze(workspace_id, goal["payload"])
        recommended_minutes = result.get("recommended_minutes_per_day")
        if recommended_minutes and goal["payload"].get("minutes_per_day") != recommended_minutes:
            # Analysis is a read-only proposal.  Persisting the recommendation
            # here silently changed a saved GoalProfile before the user had
            # approved it.  Keep the original goal intact and expose an
            # explicit patch for the confirmation route instead.
            result["suggested_goal_patch"] = {"minutes_per_day": recommended_minutes}
            result["minutes_per_day_applied"] = None
        if session.provider == "fake":
            result["generation_meta"] = generation_meta(session, latency_ms=0)
        else:
            _, result["generation_meta"] = await call_bound_provider_json(session, [{"role": "system", "content": "Return a concise JSON feasibility review."}, {"role": "user", "content": json.dumps({"goal": goal["payload"], "analysis": result}, ensure_ascii=False)}])
        with db.connection() as conn: conn.execute("UPDATE assessments SET payload=? WHERE id=?", (json.dumps(result, ensure_ascii=False), result.get("id")))
        try:
            from .orm import SessionLocal, FeasibilityORM
            with SessionLocal() as orm_session:
                record = orm_session.get(FeasibilityORM, result.get("id"))
                if record: record.payload = result; orm_session.commit()
        except Exception:
            pass
        set_job(jid,status='succeeded',stage='saving',progress=100,message='可行性分析完成'); result['job_id']=jid; return result
    except Exception as exc:
        set_job(jid,status='failed',progress=100,error=getattr(exc, "code", str(exc)),message='分析失败');
        if isinstance(exc, ProviderError): fail(exc.code, exc.message, exc.status, exc.details)
        raise


@app.get("/api/workspaces/{workspace_id}/feasibility")
def get_feasibility(workspace_id: str): require_workspace(workspace_id); items = db.rows("assessments", "workspace_id", workspace_id); return payload(items[-1]) if items else None


@app.post("/api/workspaces/{workspace_id}/feasibility/confirm")
def confirm_feasibility(workspace_id: str, body: FeasibilityConfirmIn):
    require_workspace(workspace_id); items = db.rows("assessments", "workspace_id", workspace_id)
    if not items: fail("FEASIBILITY_REQUIRED", "请先运行可行性分析")
    item = items[-1]; data = json.loads(item["payload"]); data["user_confirmed"] = body.confirmed
    data["selected_option"] = body.selected_option
    if body.minutes_per_day is not None:
        if body.selected_option != "increase_daily_minutes":
            fail("FEASIBILITY_OPTION_MISMATCH", "只有选择增加每日投入时才能修改每日分钟数")
        current_goal = get_goal(workspace_id)
        if not current_goal:
            fail("GOAL_REQUIRED", "请先填写目标")
        updated_goal = {**current_goal["payload"], "minutes_per_day": body.minutes_per_day}
        updated_goal.pop("id", None)
        set_goal(workspace_id, GoalProfile.model_validate(updated_goal))
        data["applied_goal_patch"] = {"minutes_per_day": body.minutes_per_day}
    with db.connection() as conn: conn.execute("UPDATE assessments SET payload=?, user_confirmed=? WHERE id=?", (json.dumps(data, ensure_ascii=False), int(body.confirmed), item["id"]))
    try:
        from .orm import SessionLocal, FeasibilityORM
        with SessionLocal() as session:
            record=session.get(FeasibilityORM,item["id"])
            if record: record.payload=data; record.confirmed=body.confirmed; session.commit()
    except Exception: pass
    return data


@app.get("/api/characters")
def characters():
    return [{"id":"warm","name":"温柔陪伴型","description":"温和、鼓励、给出小步行动","tone":"warm","pace":"steady"},{"id":"researcher","name":"严谨研究员型","description":"强调证据、定义和边界","tone":"precise","pace":"deep"},{"id":"socratic","name":"苏格拉底提问型","description":"通过追问帮助你自己想清楚","tone":"curious","pace":"reflective"},{"id":"coach","name":"高压教练型","description":"短句、明确、强调交付和节奏","tone":"direct","pace":"fast"}]


@app.post("/api/workspaces/{workspace_id}/plans/generate")
async def generate_plan(workspace_id: str, request: Request, body: dict | None = None):
    require_workspace(workspace_id); goal = get_goal(workspace_id); assessment = get_feasibility(workspace_id)
    if not goal: fail("GOAL_REQUIRED", "请先填写目标")
    if not assessment or not assessment["payload"].get("user_confirmed"): fail("FEASIBILITY_NOT_CONFIRMED", "请先确认可行性分析")
    jid=create_job(workspace_id,'plan_generation'); set_job(jid,status='running',stage='generating_tutorial',progress=30,message='正在生成结构化课程')
    try:
        session = provider_for_workspace(workspace_id, request, body)
        attach_workspace_provider(workspace_id, session)
        provider_result = None
        if session.provider == "fake":
            meta = generation_meta(session, latency_ms=0)
        else:
            from .prompt_loader import load_prompt
            provider_result, meta = await call_bound_provider_json(session, [{"role":"system","content":load_prompt("plan_generator.md")},{"role":"user","content":json.dumps({"goal":goal["payload"],"assessment":assessment["payload"]},ensure_ascii=False)}])
        plan=build_plan(workspace_id, goal["payload"], assessment["payload"])
        plan["generation_meta"] = meta
        if isinstance(provider_result,dict):
            if isinstance(provider_result.get("story_world"), dict): plan["story_world"] = provider_result["story_world"]
            if isinstance(provider_result.get("characters"), list): plan["story_characters"] = provider_result["characters"]
            daily_scenes = {str(scene.get("date")): scene for scene in (provider_result.get("daily_scenes") or []) if isinstance(scene, dict) and scene.get("date")}
            if daily_scenes:
                for day in plan.get("study_days", []):
                    ai_scene = daily_scenes.get(str(day.get("date")))
                    if not ai_scene: continue
                    for task in day.get("tasks", []):
                        task["scene"] = {**task.get("scene", {}), **{key: ai_scene[key] for key in ("location", "opening", "knowledge_focus", "objective", "choices", "character_id") if key in ai_scene}, "source": "provider"}
            plan["provider_generation"]={"provider":session.provider,"structured":True,"title":provider_result.get("title","")}
        with db.connection() as conn: conn.execute("UPDATE plans SET payload=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), plan["id"]))
    except HTTPException as exc:
        set_job(jid,status='failed',progress=100,error='PROVIDER_SESSION_REQUIRED',message='Provider 会话无效'); raise
    except ProviderError as exc:
        set_job(jid,status='failed',progress=100,error=exc.code,message='Provider 失败'); fail(exc.code,exc.message,exc.status,exc.details)
    set_job(jid,status='succeeded',stage='saving',progress=100,message='计划草稿已保存'); plan['job_id']=jid; return plan


@app.get("/api/workspaces/{workspace_id}/plans")
def list_plans(workspace_id: str): require_workspace(workspace_id); return [payload(x) for x in db.rows("plans", "workspace_id", workspace_id)]


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    item = payload(db.row("plans", "id", plan_id));
    if not item: fail("PLAN_NOT_FOUND", "计划不存在", 404)
    plan=item["payload"]
    try:
        from .orm import SessionLocal, TaskORM, DayORM
        with SessionLocal() as session:
            states={row.id:row for row in session.query(TaskORM).join(DayORM,TaskORM.day_id==DayORM.id).filter(DayORM.plan_id==plan_id).all()}
            for day in plan.get("study_days",[]):
                for task in day.get("tasks",[]):
                    row=states.get(task.get("id"))
                    if row: task.update({"status":row.status,"actual_minutes":row.actual_minutes,"completion_evidence":row.evidence})
    except Exception: pass
    return plan


@app.post("/api/plans/{plan_id}/activate")
def activate_plan(plan_id: str):
    item = db.row("plans", "id", plan_id)
    if not item: fail("PLAN_NOT_FOUND", "计划不存在", 404)
    with db.connection() as conn:
        conn.execute("UPDATE plans SET status='archived' WHERE workspace_id=? AND status='active'", (item["workspace_id"],))
        conn.execute("UPDATE plans SET status='active', activated_at=? WHERE id=?", (db.now(), plan_id))
    try:
        from .orm import SessionLocal, PlanORM
        with SessionLocal() as session:
            for old in session.query(PlanORM).filter_by(workspace_id=item["workspace_id"],status="active").all(): old.status="archived"
            current=session.get(PlanORM,plan_id)
            if current: current.status="active"
            session.commit()
    except Exception: pass
    data = json.loads(item["payload"]); data["status"] = "active"; return data


@app.post("/api/plans/{plan_id}/replan")
async def replan(plan_id: str, request: Request, body: dict | None = None):
    item = db.row("plans", "id", plan_id)
    if not item: fail("PLAN_NOT_FOUND", "计划不存在", 404)
    goal = get_goal(item["workspace_id"]); assessment = get_feasibility(item["workspace_id"])
    previous=json.loads(item["payload"]); body=body or {}
    jid=create_job(item["workspace_id"],"replan"); set_job(jid,status="running",stage="preserving_completed",progress=25,message="Preserving completed work")
    goal_data=dict(goal["payload"])
    if body.get("new_deadline"): goal_data["deadline"]=body["new_deadline"]
    if body.get("new_minutes_per_day"): goal_data["minutes_per_day"]=body["new_minutes_per_day"]
    if body.get("new_preferred_weekdays") is not None: goal_data["preferred_weekdays"]=body["new_preferred_weekdays"]
    absence_value = None
    if "absence_date" in body and body.get("absence_date") is not None:
        try:
            absence_value = date.fromisoformat(str(body["absence_date"])).isoformat()
        except (TypeError, ValueError):
            fail("ABSENCE_DATE_INVALID", "absence_date 必须是 ISO 日期")
        existing_unavailable = {
            str(value) for value in (goal_data.get("unavailable_dates") or [])
        }
        goal_data["unavailable_dates"] = sorted(existing_unavailable | {absence_value})
    if goal_data != goal["payload"]:
        set_goal(item["workspace_id"], GoalProfile.model_validate(goal_data))
    try:
        session = provider_for_workspace(item["workspace_id"], request, body)
        attach_workspace_provider(item["workspace_id"], session)
        if session.provider == "fake":
            replan_meta = generation_meta(session, latency_ms=0)
        else:
            _, replan_meta = await call_bound_provider_json(session, [{"role":"system","content":"Return a concise JSON replan risk review."},{"role":"user","content":json.dumps({"reason":body.get("reason","manual"),"goal":goal_data},ensure_ascii=False)}])
    except HTTPException:
        set_job(jid,status="failed",progress=100,error="PROVIDER_SESSION_REQUIRED",message="Provider 会话无效"); raise
    except ProviderError as exc:
        set_job(jid,status="failed",progress=100,error=exc.code,message="Provider 失败"); fail(exc.code,exc.message,exc.status,exc.details)
    new = build_plan(item["workspace_id"], goal_data, assessment["payload"])
    new["generation_meta"] = replan_meta
    def keyed_tasks(plan: dict) -> dict[str, tuple[dict, str]]:
        counts: dict[str, int] = {}
        result: dict[str, tuple[dict, str]] = {}
        for day in plan.get("study_days", []):
            for task in day.get("tasks", []):
                base = task.get("source_task_id") or task.get("stable_task_id") or task.get("title")
                if not base:
                    continue
                ordinal = counts.get(str(base), 0)
                counts[str(base)] = ordinal + 1
                key = str(base) if ordinal == 0 else f"{base}#{ordinal}"
                result[key] = (task, day.get("date"))
        return result

    old_index = keyed_tasks(previous)
    new_index = keyed_tasks(new)
    old_tasks = {key: value[0] for key, value in old_index.items()}
    new_tasks = {key: value[0] for key, value in new_index.items()}
    old_dates = {key: value[1] for key, value in old_index.items()}
    new_dates = {key: value[1] for key, value in new_index.items()}
    for key,old_task in old_tasks.items():
        if key in new_tasks and old_task.get("status")=="completed":
            preserved = {
                "status": "completed",
                "completed_at": old_task.get("completed_at"),
                "actual_minutes": old_task.get("actual_minutes", old_task.get("estimated_minutes", 0)),
                "completion_evidence": old_task.get("completion_evidence", ""),
            }
            for field in ("learning_run_passed", "scene_completed"):
                if field in old_task:
                    preserved[field] = old_task[field]
            new_tasks[key].update(preserved)
            # The legacy GET route overlays ORM task state onto the archived
            # plan payload.  Keep that state in sync as well so activating the
            # undo link cannot hide a completed task behind a stale ORM row.
            sync_task_orm(old_task)
    for task in new_tasks.values():
        sync_task_orm(task)
    failed_task_id = str(body.get("failed_task_id") or "")
    failed_task = next((task for task in new_tasks.values() if task.get("id") == failed_task_id or task.get("source_task_id") == failed_task_id or task.get("stable_task_id") == failed_task_id), None)
    if failed_task:
        failed_task["status"] = "retry_scheduled"
        failed_task["last_failure_reason"] = str(body.get("failure_reason") or "learning_run_failed")[:500]
        sync_task_orm(failed_task)
    diff={"reason":body.get("reason") or ("temporary_leave" if absence_value else "failed_attempt" if failed_task_id else "manual"),"moved_tasks":[{"task":old_tasks[key].get("title") or key,"from":old_dates[key],"to":new_dates[key]} for key in old_dates.keys() & new_dates.keys() if old_dates[key]!=new_dates[key]],"added_tasks":[t for i,t in new_tasks.items() if i not in old_tasks],"removed_tasks":[t for i,t in old_tasks.items() if i not in new_tasks],"changed_deadline":previous.get("deadline")!=new.get("deadline"),"changed_capacity":body.get("new_minutes_per_day") is not None,"changed_risk":previous.get("risk_summary")!=new.get("risk_summary"),"absence_date":absence_value,"failed_task_id":failed_task_id or None}
    new["diff"]=diff
    with db.connection() as conn:
        conn.execute("UPDATE plans SET status='archived' WHERE id=?",(plan_id,))
        conn.execute("UPDATE plans SET payload=? WHERE id=?",(json.dumps(new,ensure_ascii=False),new["id"]))
    try:
        from .orm import SessionLocal, PlanORM
        with SessionLocal() as session:
            old_orm=session.get(PlanORM,plan_id); new_orm=session.get(PlanORM,new["id"])
            if old_orm: old_orm.status="archived"; old_orm.stale=True
            if new_orm: new_orm.diff=diff
            session.commit()
    except Exception: pass
    set_job(jid,status="succeeded",stage="saving",progress=100,message="Replan saved")
    new["job_id"]=jid
    return {"plan":new,"changes":diff,"previous_plan_id":plan_id,"previous_version":previous.get("version"),"undo":{"method":"POST","path":f"/api/plans/{plan_id}/activate"}}


@app.post("/api/plans/{plan_id}/export")
def export_plan(plan_id: str): return get_plan(plan_id)


def active_plan(workspace_id: str) -> dict | None:
    items = db.rows("plans", "workspace_id", workspace_id)
    plan=json.loads(next((x["payload"] for x in reversed(items) if x["status"] in {"active", "draft"}), items[-1]["payload"]) if items else "{}")
    goal_row = db.row("goals", "workspace_id", workspace_id)
    if goal_row and plan.get("study_days"):
        goal = json.loads(goal_row["payload"])
        expected_days = available_days(date.fromisoformat(goal.get("start_date") or date.today().isoformat()), date.fromisoformat(goal["deadline"]), goal["days_per_week"], set(goal.get("unavailable_dates", [])), goal.get("preferred_weekdays"))
        existing_dates = {day.get("date") for day in plan.get("study_days", [])}
        schedule_changed = False
        for ordinal, scheduled_day in enumerate(expected_days, 1):
            day_key = scheduled_day.isoformat()
            if day_key in existing_dates:
                continue
            title = f"剧情支线：{goal['goal_statement'][:24]} · 主动回忆"
            task = {"id": uid("task"), "study_day_id": "", "lesson_id": "", "title": title, "task_type": "practice", "estimated_minutes": min(30, int(goal.get("minutes_per_day", 30))), "priority": "normal", "completion_criteria": "用自己的话复述一个关键点，并给出一个新例子", "scene": scene_for_task(title, "practice", ordinal), "status": "not_started"}
            plan.setdefault("study_days", []).append({"id": uid("day"), "date": day_key, "planned_minutes": int(goal.get("minutes_per_day", 30)), "carry_over_minutes": 0, "status": "planned", "tasks": [task]})
            schedule_changed = True
        if schedule_changed:
            plan["study_days"] = sorted(plan.get("study_days", []), key=lambda day: day.get("date", ""))
            with db.connection() as conn:
                conn.execute("UPDATE plans SET payload=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), plan.get("id")))
    scene_ordinal = 1
    for day in plan.get("study_days", []):
        for task in day.get("tasks", []):
            if not task.get("scene"):
                task["scene"] = scene_for_task(task.get("title", "今日学习任务"), task.get("task_type", "lesson"), scene_ordinal)
            scene_ordinal += 1
    try:
        from .orm import SessionLocal, TaskORM, DayORM, PlanORM
        with SessionLocal() as session:
            states={row.id:row for row in session.query(TaskORM).join(DayORM,TaskORM.day_id==DayORM.id).join(PlanORM,DayORM.plan_id==PlanORM.id).filter(PlanORM.workspace_id==workspace_id).all()}
            for day in plan.get("study_days",[]):
                for task in day.get("tasks",[]):
                    row=states.get(task.get("id"))
                    if row:
                        task.update({"status":row.status,"started_at":row.started_at.isoformat() if row.started_at else task.get("started_at"),"completed_at":row.completed_at.isoformat() if row.completed_at else task.get("completed_at"),"actual_minutes":row.actual_minutes,"completion_evidence":row.evidence})
    except Exception: pass
    return plan


@app.get("/api/workspaces/{workspace_id}/today")
def today(workspace_id: str):
    require_workspace(workspace_id); plan = active_plan(workspace_id); today_str = __import__("datetime").date.today().isoformat()
    day = next((x for x in plan.get("study_days", []) if x["date"] == today_str), None)
    if not day and plan.get("study_days"): day = next((x for x in plan["study_days"] if x["date"] >= today_str), plan["study_days"][-1])
    if day:
        lessons={lesson["id"]:lesson for milestone in plan.get("milestones",[]) for module in milestone.get("modules",[]) for lesson in module.get("lessons",[])}
        for task in day.get("tasks",[]):
            if task.get("lesson_id") in lessons: task["lesson"]=lessons[task["lesson_id"]]
    return {"date": today_str, "day": day, "plan_id": plan.get("id")}


@app.get("/api/workspaces/{workspace_id}/calendar")
def calendar(workspace_id: str): require_workspace(workspace_id); return active_plan(workspace_id).get("study_days", [])


@app.get("/api/workspaces/{workspace_id}/progress")
def progress_v2(workspace_id: str):
    require_workspace(workspace_id); plan=active_plan(workspace_id); tasks=[t for d in plan.get('study_days',[]) for t in d.get('tasks',[])]; done=[t for t in tasks if t.get('status')=='completed']; planned=sum(int(t.get('estimated_minutes',0)) for t in tasks); actual=sum(int(t.get('actual_minutes',0)) for t in done)
    mastery=None; events=[]
    try:
        from .orm import SessionLocal,MasteryEventORM,MistakeORM,ReviewORM
        with SessionLocal() as s:
            events=s.query(MasteryEventORM).filter_by(workspace_id=workspace_id).all(); mastery=round(sum(x.score for x in events)/len(events),2) if events else 0
            weak=[x.criterion for x in s.query(MistakeORM).filter_by(workspace_id=workspace_id,status='open').order_by(MistakeORM.occurrences.desc()).limit(5).all()]
            review_due=sum(1 for x in s.query(ReviewORM).filter_by(workspace_id=workspace_id,status='due').all() if x.due_date<=date.today())
    except Exception: weak=[]; review_due=0
    validated_done=[t for t in done if t.get('scene_completed') is True]
    completion=round(len(validated_done)/len(tasks),2) if tasks else 0; completed_dates={date.fromisoformat(d['date']) for d in plan.get('study_days',[]) for t in d.get('tasks',[]) if t.get('scene_completed') is True}; streak=0; cursor=date.today()
    if cursor not in completed_dates and completed_dates: cursor=max(completed_dates)
    while cursor in completed_dates: streak+=1; cursor-=timedelta(days=1)
    return {'completed_tasks':len(done),'validated_completed_tasks':len(validated_done),'total_tasks':len(tasks),'planned_minutes':planned,'actual_minutes':actual,'completion_rate':completion,'mastery':mastery if mastery is not None else 0,'weak_points':weak,'review_due':review_due,'overdue_tasks':sum(1 for d in plan.get('study_days',[]) for t in d.get('tasks',[]) if d['date']<date.today().isoformat() and t.get('scene_completed') is not True),'at_risk':bool(plan.get('risk_summary') or plan.get('capacity_status')=='insufficient'),'streak':streak,'recommendation':'继续今天的最小场景任务，完成导师回合后再提交进度'}

@app.get("/api/workspaces/{workspace_id}/progress-legacy")
def progress(workspace_id: str):
    return progress_v2(workspace_id)


@app.get("/api/workspaces/{workspace_id}/review-queue")
def review_queue(workspace_id: str):
    require_workspace(workspace_id); plan=active_plan(workspace_id); result=[{"id":t["id"],"title":t["title"],"reason":"间隔复习","due":d["date"]} for d in plan.get("study_days",[]) for t in d.get("tasks",[]) if t["task_type"]=="review"]
    try:
        from .orm import SessionLocal, ReviewORM
        with SessionLocal() as session:
            result.extend({"id":r.id,"lesson_id":r.lesson_id,"title":"Lesson review","reason":"mastery review","due":r.due_date.isoformat()} for r in session.query(ReviewORM).filter_by(workspace_id=workspace_id,status="due").all())
    except Exception: pass
    return result


def find_task(plan: dict, task_id: str) -> tuple[dict | None, dict | None]:
    for d in plan.get("study_days", []):
        for t in d.get("tasks", []):
            if t["id"] == task_id: return d, t
    return None, None

def sync_task_orm(task: dict):
    try:
        from .orm import SessionLocal, TaskORM
        with SessionLocal() as session:
            row=session.get(TaskORM,task["id"])
            if row:
                row.status=task.get("status","not_started"); row.started_at=datetime.fromisoformat(task["started_at"]) if task.get("started_at") else row.started_at; row.completed_at=datetime.fromisoformat(task["completed_at"]) if task.get("completed_at") else row.completed_at; row.actual_minutes=int(task.get("actual_minutes",0)); row.evidence=str(task.get("completion_evidence",task.get("evidence",""))); session.commit()
    except Exception: pass


@app.post("/api/tasks/{task_id}/start")
def start_task_v2(task_id: str):
    for ws in db.rows('workspaces','status','active'):
        plan=active_plan(ws['id']); day,task=find_task(plan,task_id)
        if task:
            task['status']='in_progress'; task['started_at']=db.now()
            with db.connection() as conn:conn.execute('UPDATE plans SET payload=? WHERE id=?',(json.dumps(plan,ensure_ascii=False),plan['id']))
            sync_task_orm(task)
            return task
    fail('TASK_NOT_FOUND','任务不存在',404)

@app.post("/api/tasks/{task_id}/start-legacy")
def start_task(task_id: str):
    for ws in db.rows("workspaces", "status", "active"):
        plan = active_plan(ws["id"]); day, task = find_task(plan, task_id)
        if task:
            task["status"] = "in_progress"; return task
    fail("TASK_NOT_FOUND", "任务不存在", 404)


@app.post("/api/tasks/{task_id}/complete")
def complete_task_v2(task_id: str, body: dict[str,Any] | None = None):
    body=body or {}
    if "scene_completed" in body:
        fail("CLIENT_CONTROLLED_FIELD", "scene_completed 只能由服务器根据 Learning Run 计算", 400)
    for ws in db.rows('workspaces','status','active'):
        plan=active_plan(ws['id']); day,task=find_task(plan,task_id)
        if task:
            task['status']='completed'; task['completed_at']=db.now(); task['actual_minutes']=int(body.get('actual_minutes',task.get('estimated_minutes',0))); task['completion_evidence']=str(body.get('evidence','')); task['scene_completed']=bool(task.get('scene_completed', False))
            with db.connection() as conn:conn.execute('UPDATE plans SET payload=? WHERE id=?',(json.dumps(plan,ensure_ascii=False),plan['id']))
            sync_task_orm(task)
            return task
    fail('TASK_NOT_FOUND','任务不存在',404)


def _learning_run_error(exc: LearningRunError):
    fail(exc.code, exc.message, exc.status_code, exc.details)


@app.post("/api/tasks/{task_id}/learning-runs")
def learning_run_create(task_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **create_learning_run(task_id)}
    except LearningRunError as exc: _learning_run_error(exc)


@app.get("/api/learning-runs/{run_id}")
def learning_run_get(run_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **get_learning_run(run_id)}
    except LearningRunError as exc: _learning_run_error(exc)


@app.post("/api/learning-runs/{run_id}/messages")
async def learning_run_message(run_id: str, request: Request, body: dict[str, Any]):
    try:
        result = add_run_message(run_id, body)
        # The deterministic scaffold is always created first. For a verified
        # real workspace, let the configured model rewrite only the teaching
        # prose; citations, phase order, and evaluator state stay server-owned.
        workspace = require_workspace(result["workspace_id"])
        if workspace.get("execution_mode") == "real":
            session = provider_for_workspace(result["workspace_id"], request, body)
            attach_workspace_provider(result["workspace_id"], session)
            from .prompt_loader import load_prompt
            teacher_message = next((item for item in reversed(result.get("messages", [])) if item.get("role") == "tutor"), {})
            grounding = teacher_message.get("grounding") or {}
            provider_output, meta = await call_bound_provider_json(session, [
                {"role": "system", "content": load_prompt("tutor.md")},
                {"role": "user", "content": json.dumps({"question": body.get("message", ""), "lesson": result.get("state", {}).get("lesson_title", ""), "phase": result.get("phase"), "scaffold": grounding.get("scaffold"), "evidence": grounding.get("citations", []), "persona_prompt": workspace.get("persona_prompt", ""), "teacher": result.get("state", {}).get("teacher", {})}, ensure_ascii=False)},
            ])
            if isinstance(provider_output, dict) and str(provider_output.get("message") or "").strip():
                teacher_message["content"] = str(provider_output["message"])
                grounding["provider_output"] = provider_output
                grounding["generation_meta"] = meta
                teacher_message["grounding"] = grounding
                from .orm import LearningRunMessageORM, SessionLocal
                with SessionLocal() as session_db:
                    stored = session_db.get(LearningRunMessageORM, teacher_message.get("id"))
                    if stored:
                        stored.content = teacher_message["content"]
                        stored.grounding = grounding
                        session_db.commit()
        return {"request_id": request.state.request_id, **result}
    except ProviderError as exc: fail(exc.code, exc.message, exc.status, exc.details)
    except LearningRunError as exc: _learning_run_error(exc)


@app.post("/api/learning-runs/{run_id}/quick-quiz")
def learning_run_quick_quiz(run_id: str, request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **submit_quick_quiz(run_id, body)}
    except LearningRunError as exc: _learning_run_error(exc)


@app.post("/api/learning-runs/{run_id}/submit")
def learning_run_submit(run_id: str, request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **submit_learning_run(run_id, body)}
    except LearningRunError as exc: _learning_run_error(exc)


@app.post("/api/learning-runs/{run_id}/request-hint")
def learning_run_hint(run_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **request_hint(run_id)}
    except LearningRunError as exc: _learning_run_error(exc)

@app.post("/api/tasks/{task_id}/complete-legacy")
def complete_task(task_id: str):
    for ws in db.rows("workspaces", "status", "active"):
        plan = active_plan(ws["id"]); day, task = find_task(plan, task_id)
        if task:
            task["status"] = "completed"; task["completed_at"] = db.now()
            with db.connection() as conn: conn.execute("UPDATE plans SET payload=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), plan["id"]))
            return task
    fail("TASK_NOT_FOUND", "任务不存在", 404)


@app.post("/api/workspaces/{workspace_id}/sessions")
def create_session(workspace_id: str, body: dict[str, Any]):
    require_workspace(workspace_id); sid = uid("session"); item = {"id": sid, "workspace_id": workspace_id, "lesson_id": body.get("lesson_id", ""), "provider_session_id": body.get("provider_session_id"), "messages": [], "created_at": db.now(), "updated_at": db.now()}
    with db.connection() as conn: conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (sid, workspace_id, item["lesson_id"], json.dumps(item, ensure_ascii=False), item["created_at"], item["updated_at"]))
    try:
        from .orm import SessionLocal, SessionORM
        with SessionLocal() as session:
            session.add(SessionORM(id=sid,workspace_id=workspace_id,lesson_id=item["lesson_id"])); session.commit()
    except Exception: pass
    publish_learning_event(workspace_id, 'LEARNING_SESSION_STARTED', 'system', sid, {'lesson_id': item['lesson_id']})
    return item


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    item = db.row("sessions", "id", session_id)
    if not item: fail("SESSION_NOT_FOUND", "学习会话不存在", 404)
    return json.loads(item["payload"])


@app.post("/api/sessions/{session_id}/messages")
async def message_session(session_id: str, request: Request, body: dict[str, Any]):
    item = db.row("sessions", "id", session_id)
    if not item: fail("SESSION_NOT_FOUND", "学习会话不存在", 404)
    session = json.loads(item["payload"]); user_text = str(body.get("message", "")); bound_session = provider_for_workspace(item["workspace_id"], request=request, body=body | {"provider_session_id": body.get("provider_session_id") or session.get("provider_session_id")}); attach_workspace_provider(item["workspace_id"], bound_session)
    if bound_session.provider == "fake":
        result = tutor(item["workspace_id"], item["lesson_id"], user_text, session_id)
    else:
        result = tutor(item["workspace_id"], item["lesson_id"], user_text, session_id)
    if result.get("citations"):
        result["critic"] = grounding_critic(item["workspace_id"], result["citations"], {x.get("chunk_id") for x in result["citations"]})
        if not result["critic"]["passed"]:
            result["status"] = "insufficient"; result["citations"] = []
    if bound_session.provider != "fake":
        try:
            from .prompt_loader import load_prompt
            provider_output, meta = await call_bound_provider_tutor(bound_session, [{"role":"system","content":load_prompt("tutor.md")},{"role":"user","content":json.dumps({"question":user_text,"evidence":result.get("citations",[]),"status":result.get("status"),"scaffold":result.get("scaffold"),"persona_prompt":(require_workspace(item["workspace_id"]).get("persona_prompt") or "")},ensure_ascii=False)}], result)
            result["provider_output"] = provider_output
            if provider_output.get("message"): result["message"] = str(provider_output["message"])
            result["generation_meta"] = meta
        except ProviderError as exc:
            fail(exc.code,exc.message,exc.status,exc.details)
    else:
        result["generation_meta"] = generation_meta(bound_session, latency_ms=0)
    if body.get("allow_external_knowledge") and result.get("status") == "insufficient":
        result["supplemental"] = {"label":"AI 补充，未包含在当前资料中","text":"可以在补充资料后重新提问；当前回答不把外部信息当作已验证证据。"}
    session["messages"].extend([{ "role": "user", "content": user_text }, { "role": "tutor", "content": result["message"], "grounding": result }])
    with db.connection() as conn: conn.execute("UPDATE sessions SET payload=?, updated_at=? WHERE id=?", (json.dumps(session, ensure_ascii=False), db.now(), session_id))
    try:
        from .orm import SessionLocal, SessionORM, MessageORM, CitationORM
        with SessionLocal() as orm_session:
            db_session=orm_session.get(SessionORM,session_id)
            if db_session:
                db_session.updated_at=datetime.now(timezone.utc)
                user_row=MessageORM(id=uid("message"),session_id=session_id,role="user",content=user_text,grounding={}); tutor_row=MessageORM(id=uid("message"),session_id=session_id,role="tutor",content=result["message"],grounding=result); orm_session.add_all([user_row,tutor_row]); orm_session.flush()
                for citation in result.get("citations",[]): orm_session.add(CitationORM(id=uid("citation"),message_id=tutor_row.id,source_id=citation.get("source_id",""),chunk_id=citation.get("chunk_id",""),page_start=citation.get("page_start"),page_end=citation.get("page_end"),quote=citation.get("quote","")))
                orm_session.commit()
    except Exception: pass
    return result

@app.post("/api/lessons/{lesson_id}/evaluate")
async def evaluate_lesson_v2(lesson_id: str, request: Request, body: dict[str, Any]):
    workspace_id = ""
    try:
        from .orm import SessionLocal, ProgressORM
        with SessionLocal() as session:
            progress_row = session.query(ProgressORM).filter_by(lesson_id=lesson_id).first()
            workspace_id = progress_row.workspace_id if progress_row else ""
    except Exception:
        workspace_id = ""
    bound_session = provider_for_workspace(workspace_id, request, body) if workspace_id else create_verified_demo_session()
    if workspace_id:
        attach_workspace_provider(workspace_id, bound_session)
    text=str(body.get("response","")); used_hint=bool(body.get("used_hint",False)); independent=bool(body.get("independent",True)); evidence=str(body.get("evidence", ""))
    rubric_terms=["解释","原因","例子","证据","步骤","判断","流程"]
    covered=[term for term in rubric_terms if term in text or term in evidence]
    missing=[term for term in rubric_terms[:4] if term not in covered]
    score=round(min(1.0,len(covered)/max(1,len(rubric_terms))*0.75+(0.15 if independent else 0)+(0.1 if not used_hint else 0)),2)
    result={"lesson_id":lesson_id,"score":score,"covered_criteria":covered,"missing_criteria":missing,"incorrect_claims":[],"feedback":"已覆盖关键 rubric。" if score>=.75 else "请补充缺失的 criterion，并说明依据和例子。","used_hint":used_hint,"independent":independent,"mastery_delta":round((score-.5)*.25,3),"next_step":"安排间隔复习" if score>=.75 else "在下一可用日复习","generation_meta":generation_meta(bound_session, latency_ms=0)}
    if bound_session.provider != "fake":
        try:
            _, result["generation_meta"] = await call_bound_provider_json(bound_session, [{"role":"system","content":"Return JSON evaluator feedback without changing the numeric rubric score."},{"role":"user","content":json.dumps({"response":text,"evidence":evidence,"score":score,"missing":missing},ensure_ascii=False)}])
        except ProviderError as exc:
            fail(exc.code, exc.message, exc.status, exc.details)
    try:
        from .orm import SessionLocal,ProgressORM,MasteryEventORM,MistakeORM,ReviewORM
        import uuid
        with SessionLocal() as s:
            # workspace is resolved from the lesson by the active plan; lesson-level events remain append-only.
            progress=s.query(ProgressORM).filter_by(lesson_id=lesson_id).first()
            if progress:
                progress.attempts+=1; progress.independent_attempts+=int(independent); progress.hint_attempts+=int(used_hint); progress.mastery=max(0,min(1,progress.mastery+result["mastery_delta"]))
                progress.next_review=date.today()+timedelta(days=1 if score<.75 else 3)
                review=s.query(ReviewORM).filter_by(workspace_id=progress.workspace_id,lesson_id=lesson_id,status="due").first()
                if review: review.due_date=progress.next_review; review.interval_days=1 if score<.75 else 3
                else: s.add(ReviewORM(id="review_"+uuid.uuid4().hex[:12],workspace_id=progress.workspace_id,lesson_id=lesson_id,due_date=progress.next_review,interval_days=1 if score<.75 else 3,status="due"))
            s.add(MasteryEventORM(id='event_'+uuid.uuid4().hex[:12],workspace_id=progress.workspace_id if progress else '',lesson_id=lesson_id,score=score,delta=result["mastery_delta"],used_hint=used_hint,independent=independent))
            for criterion in missing:
                if progress:
                    mistake=s.query(MistakeORM).filter_by(workspace_id=progress.workspace_id,lesson_id=lesson_id,criterion=criterion,status='open').first()
                    if mistake: mistake.occurrences+=1
                    else:s.add(MistakeORM(id='mistake_'+uuid.uuid4().hex[:12],workspace_id=progress.workspace_id,lesson_id=lesson_id,criterion=criterion))
            s.commit()
    except Exception: pass
    if workspace_id:
        publish_learning_event(workspace_id, 'INDEPENDENT_PROBLEM_PASSED' if score >= .75 and independent else 'INDEPENDENT_PROBLEM_FAILED', 'evaluator', lesson_id, {'lesson_id': lesson_id, 'score': score, 'independent': independent, 'used_hint': used_hint})
    return result

@app.post("/api/lessons/{lesson_id}/evaluate-legacy")
async def evaluate_lesson(lesson_id: str, request: Request, body: dict[str, Any]):
    return await evaluate_lesson_v2(lesson_id, request, body)


@app.post("/api/provider-sessions")
def create_provider_session_route(request: Request, body: ProviderSessionCreateIn):
    try:
        return create_provider_session(body.model_dump())
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.get("/api/provider-sessions/current")
def current_provider_session_route(request: Request):
    """Expose only the verified local handle needed by embedded web tools."""

    session = latest_verified_provider_session()
    if not session:
        fail("PROVIDER_SESSION_REQUIRED", "已保存的模型正在连接，请稍候", 428)
    return public_session(session) | {"base_url": session.base_url}


@app.get("/api/provider-sessions/{session_id}")
def get_provider_session_route(request: Request, session_id: str):
    session = get_provider_session(session_id)
    if not session:
        fail("PROVIDER_SESSION_NOT_FOUND", "Provider 会话不存在", 404)
    return public_session(session)


@app.post("/api/provider-sessions/{session_id}/test")
async def test_provider_session_route(request: Request, session_id: str):
    try:
        if not get_provider_session(session_id):
            fail("PROVIDER_SESSION_NOT_FOUND", "Provider 会话不存在", 404)
        return await test_provider_session(session_id)
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.delete("/api/provider-sessions/{session_id}")
def delete_provider_session_route(request: Request, session_id: str):
    return {"deleted": delete_provider_session(session_id)}


@app.delete("/api/provider-credentials/{credential_ref}")
def delete_provider_credential_route(request: Request, credential_ref: str):
    try:
        return {"deleted": delete_provider_credential(credential_ref)}
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.post("/api/personalized-updates/rank")
async def rank_personalized_updates(request: Request, body: dict[str, Any]):
    """Discover and rank public results only after an explicit learner request."""
    interests = str(body.get("interests") or "").strip()
    blocked = str(body.get("blocked") or "").strip()
    raw_candidates = body.get("candidates") or []
    if not interests:
        fail("NEWS_PREFERENCES_REQUIRED", "请先填写至少一个资讯兴趣", 422)
    if not isinstance(raw_candidates, list):
        fail("NEWS_CANDIDATES_INVALID", "资讯候选格式无效", 422)
    candidates: list[dict[str, str]] = []
    for item in raw_candidates[:30]:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        if link.startswith("https://") and title:
            candidates.append(
                {
                    "title": title[:300],
                    "publisher": str(item.get("publisher") or "")[:120],
                    "published": str(item.get("published") or "")[:40],
                    "link": link[:1200],
                }
            )
    if not candidates:
        try:
            discovered = await search_web(f"{interests} 最新 资讯", max_results=8)
        except OpenSearchError as exc:
            fail("NEWS_DISCOVERY_FAILED", str(exc), 502)
        for item in discovered.get("results") or []:
            link = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not link.startswith("https://") or not title:
                continue
            candidates.append(
                {
                    "title": title[:300],
                    "publisher": (urlparse(link).hostname or "公开网页")[:120],
                    "published": "",
                    "link": link[:1200],
                    "summary": str(item.get("snippet") or "")[:500],
                }
            )
    if not candidates:
        fail("NEWS_CANDIDATES_INVALID", "没有找到可验证的公开资讯", 502)
    try:
        session = require_provider_session(
            str(body.get("provider_session_id") or ""),
        )
        if session.provider == "fake":
            fail("PROVIDER_SESSION_REAL_REQUIRED", "个性化资讯需要已验证的真实模型连接", 428)
        result, meta = await call_bound_provider_json(
            session,
            [
                {
                    "role": "system",
                    "content": (
                        "Rank only the supplied public-result metadata for the learner. "
                        "Do not add sources or URLs. Also create one short, warm, interesting daily-life example "
                        "that fits the learner's stated preference without quoting or exposing their preference words. "
                        "The example must be domain-neutral, under 120 Chinese characters, and must not claim facts. "
                        "Return JSON exactly as {\"ordered_links\":[\"https://...\"],"
                        "\"life_example\":{\"title\":\"short title\",\"body\":\"short Chinese text\"}}."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"interests": interests, "blocked": blocked, "candidates": candidates},
                        ensure_ascii=False,
                    ),
                },
            ],
        )
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)
    allowed_links = {item["link"] for item in candidates}
    ordered_links = []
    for value in result.get("ordered_links") or []:
        link = str(value or "")
        if link in allowed_links and link not in ordered_links:
            ordered_links.append(link)
    if not ordered_links:
        fail("NEWS_RANKING_INVALID", "模型没有返回可验证的资讯排序", 502)
    by_link = {item["link"]: item for item in candidates}
    ranked_items = [by_link[link] for link in ordered_links if link in by_link]
    raw_example = result.get("life_example") if isinstance(result.get("life_example"), dict) else {}
    example_title = str(raw_example.get("title") or "").strip()[:80]
    example_body = str(raw_example.get("body") or "").strip()[:240]
    return {
        "ordered_links": ordered_links,
        "ranked_items": ranked_items,
        "life_example": (
            {"title": example_title, "body": example_body}
            if example_title and example_body
            else None
        ),
        "generation_meta": meta,
    }


_LIFE_ANALOGY_FORBIDDEN = re.compile(
    r"(?:"
    r"https?://|www\.|(?:[a-z0-9-]+\.)+(?:com|org|net|cn)(?:/|\b)|"
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b|\b\d{4}年\d{1,2}月(?:\d{1,2}日?)?|"
    r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b|"
    r"\b(?:19|20)\d{2}\b|"
    r"(?:百分之|\d+\s*%)|(?:排名|排行|榜单|前\s*\d+|"
    r"top\s*\d*|rank(?:ing)?)|"
    r"(?:招聘|招募|求职|简历|岗位|职位|就业|职场|薪资|新闻|资讯|时事|热点|"
    r"\b(?:job|career|hiring|recruit(?:ment)?|news)\b)|"
    r"[`*_#~]|(?:^|\n)\s*(?:[-+>]|\d+[.)])\s+|\[[^\]]+\]\([^)]*\)"
    r")",
    flags=re.IGNORECASE,
)

_LIFE_ANALOGY_LOGIC_FORBIDDEN = re.compile(
    r"(?:"
    r"我没走快递|回头看(?:了)?一眼屏幕|(?:柜子|系统|按钮|订单).{0,8}(?:记仇|报复|故意刁难)|"
    r"明明(?:是)?我输错.{0,12}(?:却|反而).{0,8}(?:让我等|锁定)|"
    r"(?:显示|提示).{0,6}(?:提交)?成功.{0,30}(?:其实没|却没|没有).{0,8}(?:提交|交上)|"
    r"为了演示.{0,12}(?:故意|特意).{0,8}(?:输错|点错|发错)"
    r")"
)

_FLY_EIGHT_CENTS_TARGET_FORBIDDEN = re.compile(
    r"(?:张顺飞|真人|父亲|母亲|爸爸|妈妈|亲属|坟墓|坟|墓地|死亡|去世|攻击|辱骂)"
)

_LIFE_ANALOGY_LOW_VALUE = re.compile(
    r"(?:"
    r"任何.{0,10}变化.{0,12}结果.{0,8}不同|"
    r"必须.{0,12}(?:逐位|仔细|认真).{0,8}(?:比对|核对|检查).{0,16}(?:发现|避免).{0,6}错误|"
    r"(?:做事|学习|工作).{0,8}(?:要|应该).{0,6}(?:认真|仔细|耐心)|"
    r"(?:总之|所以).{0,10}(?:仔细|认真).{0,8}(?:检查|核对).{0,8}(?:很重要|就好)"
    r")"
)


def _life_analogy_number_sequence_is_consistent(value: str) -> bool:
    """Reject visible queue arithmetic such as A028 -> A030 described as ten calls."""

    ticket_codes = [
        (prefix.casefold(), int(number))
        for prefix, number in re.findall(
            r"(?<![A-Za-z0-9])([A-Za-z])0*(\d{1,6})(?![A-Za-z0-9])",
            value,
        )
    ]
    claimed_gaps = [
        int(next(part for part in match if part))
        for match in re.findall(
            r"(?:(?:还有|还差|差(?:了)?|隔(?:了)?|跳过(?:了)?)\s*(\d{1,4})\s*个号|"
            r"(?:前面\s*)?(?:还有|排着|排了)\s*(\d{1,4})\s*(?:个)?人)",
            value,
        )
    ]
    if len(ticket_codes) < 2 or not claimed_gaps:
        return True
    first_prefix, first_number = ticket_codes[0]
    second_prefix, second_number = next(
        (prefix, number)
        for prefix, number in ticket_codes[1:]
        if prefix == first_prefix
    ) if any(prefix == first_prefix for prefix, _ in ticket_codes[1:]) else ("", first_number)
    if not second_prefix:
        return True
    return abs(second_number - first_number) == claimed_gaps[0]


def _life_analogy_input_tokens(value: Any) -> tuple[str, str]:
    """Return bounded private themes and normalized tokens for leak checks."""

    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
    else:
        parts = [str(value or "").strip()]
    parts = [part[:120] for part in parts[:16] if part]
    prompt_value = ", ".join(parts)[:500]
    tokens: list[str] = []
    for part in parts:
        # Keep both ordinary words and contiguous Chinese phrases.  The
        # latter is split into short n-grams so a provider cannot hide an
        # echoed private keyword inside a longer sentence.
        tokens.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_+.-]{1,}|[\u4e00-\u9fff]{2,}", part))
        for chinese in re.findall(r"[\u4e00-\u9fff]{2,}", part):
            tokens.extend(chinese[index : index + 2] for index in range(len(chinese) - 1))
    normalized = []
    seen: set[str] = set()
    for token in tokens:
        cleaned = token.casefold().strip()
        if len(cleaned) >= 2 and cleaned not in seen:
            seen.add(cleaned)
            normalized.append(cleaned)
    return prompt_value, "\n".join(normalized)


def _normalized_character_ngrams(value: str, size: int = 3) -> set[str]:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(len(normalized) - size + 1)}


def _normalized_evidence_position(text: str, evidence: str) -> int:
    """Locate evidence while tolerating harmless punctuation/spacing differences."""

    normalized_text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.casefold())
    normalized_evidence = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", evidence.casefold())
    if len(normalized_evidence) < 2:
        return -1
    return normalized_text.find(normalized_evidence)


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").casefold())


def _life_analogy_recent_records(value: Any) -> list[dict[str, str]]:
    """Normalize both legacy string history and the structured v3 history."""

    if not isinstance(value, list):
        return []
    records: list[dict[str, str]] = []
    limit = PROVIDER_POLICY.life_analogy_history_limit
    for item in value[-limit:]:
        if isinstance(item, dict):
            record = {
                field: str(item.get(field) or "").strip()[:500]
                for field in (
                    "summary",
                    "concept_key",
                    "concept_label",
                    "mechanism",
                    "format_key",
                    "scene_family",
                    "situation_key",
                    "mechanism_tag",
                    "style_mode",
                    "meme_key",
                    "scene_signature",
                )
            }
        else:
            record = {
                "summary": str(item or "").strip()[:500],
                "concept_key": "",
                "concept_label": "",
                "mechanism": "",
                "format_key": "",
                "scene_family": "",
                "situation_key": "",
                "scene_signature": "",
            }
        if record["summary"] or record["concept_key"]:
            records.append(record)
    return records


def _concept_seen_recently(selection: _LifeConceptSelection, recent_records: list[dict[str, str]]) -> bool:
    key = _normalized_text(selection.concept_key)
    label_ngrams = _normalized_character_ngrams(selection.concept_label, 2)
    mechanism_ngrams = _normalized_character_ngrams(selection.mechanism, 3)
    for record in recent_records:
        if key and key == _normalized_text(record.get("concept_key", "")):
            return True
        recent_text = " ".join(
            (record.get("concept_label", ""), record.get("mechanism", ""), record.get("summary", ""))
        )
        recent_label_ngrams = _normalized_character_ngrams(recent_text, 2)
        if label_ngrams and len(label_ngrams & recent_label_ngrams) / len(label_ngrams) >= 0.5:
            return True
        recent_mechanism = _normalized_character_ngrams(record.get("mechanism", ""), 3)
        union = mechanism_ngrams | recent_mechanism
        if union and len(mechanism_ngrams & recent_mechanism) / len(union) >= 0.68:
            return True
    return False


def _choose_life_analogy_format(recent_records: list[dict[str, str]], variation_seed: int) -> str:
    formats = list(PROVIDER_POLICY.life_analogy_formats)
    recently_used = {
        record.get("format_key", "")
        for record in recent_records[-min(len(formats) - 1, 8):]
        if record.get("format_key")
    }
    start = variation_seed % len(formats)
    ordered = formats[start:] + formats[:start]
    return next((item for item in ordered if item not in recently_used), ordered[0])


def _choose_life_analogy_humor_cue(variation_seed: int) -> str:
    cues = list(PROVIDER_POLICY.life_analogy_humor_cues)
    return cues[variation_seed % len(cues)]


def _choose_life_analogy_style(variation_seed: int) -> tuple[str, dict[str, str]]:
    """Alternate a safe meme card with a naturally flowing everyday card."""

    if variation_seed % 2:
        return "smooth_life", {}
    cues = [dict(value) for value in PROVIDER_POLICY.life_analogy_meme_cues]
    cue = cues[(variation_seed // 2) % len(cues)]
    return "safe_meme", {
        "key": str(cue.get("key") or ""),
        "phrase": str(cue.get("phrase") or ""),
        "placement": str(cue.get("placement") or ""),
        "usage": str(cue.get("usage") or ""),
    }


def _life_analogy_meme_is_integrated(analogy: str, meme: dict[str, str]) -> bool:
    """Enforce policy-declared placement without hard-coding any meme phrase."""

    phrase = str(meme.get("phrase") or "").strip()
    if not phrase or analogy.count(phrase) != 1:
        return False
    if str(meme.get("placement") or "").strip() != "embedded":
        return True
    before, after = analogy.split(phrase, 1)
    # An embedded aside must belong to a real scene: there is setup before it
    # and a visible response/consequence after it.  Punctuation and quote marks
    # do not count as narrative context.
    before_cjk = re.findall(r"[\u4e00-\u9fff]", before)
    after_cjk = re.findall(r"[\u4e00-\u9fff]", after)
    return len(before_cjk) >= 8 and len(after_cjk) >= 8


def _allowed_life_analogy_situations(
    recent_records: list[dict[str, str]], variation_seed: int
) -> list[dict[str, Any]]:
    families = [dict(value) for value in PROVIDER_POLICY.life_analogy_scene_families]
    recently_used = {
        record.get("scene_family", "")
        for record in recent_records[-min(len(families) - 1, 6):]
        if record.get("scene_family")
    }
    start = variation_seed % len(families)
    ordered = families[start:] + families[:start]
    ordered = [item for item in ordered if str(item.get("key") or "") not in recently_used] + [
        item for item in ordered if str(item.get("key") or "") in recently_used
    ]
    choices: list[dict[str, Any]] = []
    for family in ordered[:8]:
        for situation in family.get("situations", []):
            choices.append(
                {
                    "scene_family": str(family.get("key") or ""),
                    "scene_label": str(family.get("label") or ""),
                    "cues": [str(value) for value in family.get("cues", [])],
                    "situation_key": str(situation.get("key") or ""),
                    "situation": str(situation.get("prompt") or ""),
                    "mechanism_tags": [str(value) for value in situation.get("mechanism_tags", [])],
                    "required_groups": [
                        [str(term) for term in group if str(term).strip()]
                        for group in situation.get("required_groups", [])
                    ],
                }
            )
    if not choices:
        raise ValueError("LIFE_ANALOGY_SCENE_POLICY_INVALID")
    # Keep the provider's choice set compact: one rotated scene family (with
    # its registered situations) is enough to preserve variation while
    # leaving room in the bounded prompt for the exact trigger/response/
    # consequence groups that the deterministic validator enforces.
    first_family = choices[0].get("scene_family")
    return [item for item in choices if item.get("scene_family") == first_family]


def _validate_life_analogy(
    result: Any,
    preference_tokens: str,
    *,
    selected_format: str,
    required_style_mode: str,
    selected_meme: dict[str, str],
    allowed_situations: list[dict[str, Any]],
    direction_items: list[str],
    recent_records: list[dict[str, str]],
) -> tuple[_LifeAnalogyCard, dict[str, str]]:
    """Reject fluent-looking cards that lack a coherent structural mapping."""

    try:
        candidate = dict(result) if isinstance(result, dict) else result
        if isinstance(candidate, dict):
            candidate.setdefault("concept_label", candidate.get("concept_key") or "")
            candidate.setdefault("concept_key", candidate.get("concept_label") or "")
            candidate.setdefault("concept_mechanism", candidate.get("takeaway") or "")
            candidate.setdefault("common_confusion", "")
            candidate.setdefault("memory_target", candidate.get("takeaway") or "")
            candidate.setdefault("format_key", selected_format)
            candidate.setdefault("scene_family", "")
            candidate.setdefault("situation_key", "")
            candidate.setdefault("mechanism_tag", "")
            candidate.setdefault("style_mode", "")
            candidate.setdefault("meme_key", "")
            candidate.setdefault("meme_phrase", "")
            candidate.setdefault("meme_evidence", "")
            candidate.setdefault("scene_signature", candidate.get("title") or "")
            candidate.setdefault("twist_evidence", "")
            try:
                candidate["anchor_index"] = max(0, min(int(candidate.get("anchor_index") or 0), 15))
            except (TypeError, ValueError):
                candidate["anchor_index"] = 0
            candidate["mechanism_evidence"] = list(candidate.get("mechanism_evidence") or [])[:3]
            limits = {
                "concept_key": 100,
                "concept_label": 40,
                "concept_mechanism": 240,
                "common_confusion": 240,
                "memory_target": 240,
                "format_key": 40,
                "scene_family": 40,
                "situation_key": 40,
                "mechanism_tag": 40,
                "style_mode": 24,
                "meme_key": 40,
                "meme_phrase": 40,
                "meme_evidence": 60,
                "scene_signature": 160,
                "title": 80,
                "analogy": 320,
                "takeaway": 240,
                "twist_evidence": 120,
            }
            for field, limit in limits.items():
                candidate[field] = str(candidate.get(field) or "").strip()[:limit]
        draft = _LifeAnalogyDraft.model_validate(candidate)
    except Exception as exc:
        raise ValueError("LIFE_ANALOGY_SCHEMA_INVALID") from exc

    # This is private provenance metadata, not learner-visible truth. Models
    # sometimes count a supplied list from one; clamp the bookkeeping index
    # rather than rejecting an otherwise useful card.
    effective_anchor_index = min(draft.anchor_index, max(0, len(direction_items) - 1))
    selection = _LifeConceptSelection(
        anchor_index=effective_anchor_index,
        concept_key=draft.concept_key,
        concept_label=draft.concept_label,
        mechanism=draft.concept_mechanism,
        common_confusion=draft.common_confusion,
        memory_target=draft.memory_target,
    )
    if _concept_seen_recently(selection, recent_records):
        raise ValueError("LIFE_ANALOGY_CONCEPT_REPEATED")
    if any(
        _normalized_text(selection.concept_label) == _normalized_text(direction)
        for direction in direction_items
    ):
        raise ValueError("LIFE_ANALOGY_CONCEPT_TOO_BROAD")
    if draft.format_key != selected_format:
        raise ValueError("LIFE_ANALOGY_FORMAT_DRIFT")
    if draft.style_mode != required_style_mode:
        raise ValueError("LIFE_ANALOGY_STYLE_DRIFT")
    if required_style_mode == "safe_meme":
        expected_key = str(selected_meme.get("key") or "")
        expected_phrase = str(selected_meme.get("phrase") or "")
        if (
            not expected_key
            or not expected_phrase
            or draft.meme_key != expected_key
            or draft.meme_phrase != expected_phrase
            or draft.meme_evidence != expected_phrase
            or expected_phrase not in draft.analogy
        ):
            raise ValueError("LIFE_ANALOGY_MEME_MISSING")
        if not _life_analogy_meme_is_integrated(draft.analogy, selected_meme):
            raise ValueError("LIFE_ANALOGY_MEME_FORCED")
    elif any((draft.meme_key, draft.meme_phrase, draft.meme_evidence)):
        raise ValueError("LIFE_ANALOGY_MEME_UNREQUESTED")
    selected_situation = next(
        (
            value
            for value in allowed_situations
            if draft.scene_family == str(value.get("scene_family") or "")
            and draft.situation_key == str(value.get("situation_key") or "")
        ),
        None,
    )
    if selected_situation is None:
        raise ValueError("LIFE_ANALOGY_SITUATION_NOT_ALLOWED")
    allowed_mechanism_tags = {
        _normalized_text(value)
        for value in selected_situation.get("mechanism_tags", [])
        if _normalized_text(value)
    }
    if not allowed_mechanism_tags or _normalized_text(draft.mechanism_tag) not in allowed_mechanism_tags:
        raise ValueError("LIFE_ANALOGY_MECHANISM_NOT_ALLOWED")
    combined = "\n".join((draft.title, draft.analogy, draft.takeaway))
    if any(not re.search(r"[\u4e00-\u9fff]", value) for value in (draft.title, draft.analogy, draft.takeaway)):
        raise ValueError("LIFE_ANALOGY_LANGUAGE_INVALID")
    if _LIFE_ANALOGY_FORBIDDEN.search(combined):
        raise ValueError("LIFE_ANALOGY_CONTENT_BLOCKED")
    if _LIFE_ANALOGY_LOGIC_FORBIDDEN.search(draft.analogy):
        raise ValueError("LIFE_ANALOGY_LOGIC_CONTRADICTION")
    if not _life_analogy_number_sequence_is_consistent(draft.analogy):
        raise ValueError("LIFE_ANALOGY_NUMBER_SEQUENCE_INVALID")
    if (
        draft.meme_key == "fly_eight_cents"
        and _FLY_EIGHT_CENTS_TARGET_FORBIDDEN.search(draft.analogy)
    ):
        raise ValueError("LIFE_ANALOGY_MEME_TARGETED")
    unfamiliar_terms = [
        str(value).strip()
        for value in PROVIDER_POLICY.life_analogy_unfamiliar_actor_terms
        if str(value).strip()
    ]
    if any(term in draft.title or term in draft.analogy for term in unfamiliar_terms):
        raise ValueError("LIFE_ANALOGY_SCENE_UNFAMILIAR")
    forbidden_scene_terms = [
        str(value).strip()
        for value in PROVIDER_POLICY.life_analogy_scene_forbidden_terms
        if str(value).strip()
    ]
    if any(term in draft.title or term in draft.analogy for term in forbidden_scene_terms):
        raise ValueError("LIFE_ANALOGY_SCENE_TECHNICAL")
    inanimate_terms = [
        re.escape(str(value).strip())
        for value in PROVIDER_POLICY.life_analogy_inanimate_actor_terms
        if str(value).strip()
    ]
    speech_verbs = [
        re.escape(str(value).strip())
        for value in PROVIDER_POLICY.life_analogy_speech_verbs
        if str(value).strip()
    ]
    if inanimate_terms and speech_verbs and re.search(
        rf"(?:{'|'.join(inanimate_terms)})[^。！？]{{0,8}}(?:{'|'.join(speech_verbs)})",
        draft.title + draft.analogy,
    ):
        raise ValueError("LIFE_ANALOGY_INANIMATE_ACTOR")
    scene_cues = [
        _normalized_text(value)
        for value in selected_situation.get("cues", [])
        if _normalized_text(value)
    ]
    normalized_scene = _normalized_text(draft.title + draft.analogy)
    if not scene_cues or not any(cue in normalized_scene for cue in scene_cues):
        raise ValueError("LIFE_ANALOGY_SCENE_UNGROUNDED")
    required_groups = selected_situation.get("required_groups", [])
    ordered_scene = _normalized_text(draft.analogy)
    ordered_positions: list[int] = []
    for group in required_groups:
        positions = [
            ordered_scene.find(_normalized_text(term))
            for term in group
            if _normalized_text(term) and ordered_scene.find(_normalized_text(term)) >= 0
        ]
        if not positions:
            raise ValueError("LIFE_ANALOGY_SITUATION_FACTS_MISSING")
        ordered_positions.append(min(positions))
    if not ordered_positions or any(
        later <= earlier for earlier, later in zip(ordered_positions, ordered_positions[1:])
    ):
        raise ValueError("LIFE_ANALOGY_SITUATION_FACTS_MISSING")
    # A useful analogy must remain semantically connected to the user's
    # themes.  Do not reject an ordinary word merely because it also appeared
    # in the private input; privacy here means never returning the saved list
    # or describing it as settings/keywords.  The desktop renders only the
    # generated card, never ``preference_tokens`` itself.
    if re.search(r"(?:你的|用户的).{0,8}(?:关键词|提示词|偏好设置)|(?:关键词|提示词)列表", combined):
        raise ValueError("LIFE_ANALOGY_PRIVACY_INVALID")
    visible_scene = _normalized_text(draft.title + draft.analogy)
    concept_label = _normalized_text(selection.concept_label)
    if concept_label and concept_label in visible_scene:
        raise ValueError("LIFE_ANALOGY_SCENE_TOO_LITERAL")
    concept_bigrams = _normalized_character_ngrams(selection.concept_label, 2)
    scene_bigrams = _normalized_character_ngrams(draft.title + draft.analogy, 2)
    if concept_bigrams and len(concept_bigrams & scene_bigrams) / len(concept_bigrams) >= 0.4:
        raise ValueError("LIFE_ANALOGY_CONCEPT_IS_SCENE_LABEL")

    evidence_positions: list[int] = []
    for evidence in draft.mechanism_evidence:
        cleaned = str(evidence or "").strip()
        position = _normalized_evidence_position(draft.analogy, cleaned)
        if len(cleaned) >= 2 and position >= 0:
            evidence_positions.append(position)
    # Evidence strings are diagnostic metadata. Harmless punctuation or
    # paraphrase differences must not become a user-facing loading failure.

    generic_takeaway = re.compile(
        r"(?:先看对象.{0,8}再看条件|先确认对象.{0,8}再决定下一步|总之.{0,8}很重要)"
    )
    if generic_takeaway.search(draft.takeaway):
        raise ValueError("LIFE_ANALOGY_INSIGHT_MISSING")
    if _LIFE_ANALOGY_LOW_VALUE.search(draft.takeaway):
        raise ValueError("LIFE_ANALOGY_TOO_ELEMENTARY")
    # The public takeaway is already length-, language-, privacy- and generic-
    # phrase checked. Requiring literal n-gram overlap with private diagnostic
    # wording rejected sound paraphrases and caused unnecessary second calls.

    candidate_ngrams = _normalized_character_ngrams(draft.title + draft.analogy)
    normalized_title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", draft.title.casefold())
    normalized_analogy = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", draft.analogy.casefold())
    for record in recent_records:
        recent = record.get("summary", "")
        if (
            draft.scene_signature
            and _normalized_text(draft.scene_signature)
            == _normalized_text(record.get("scene_signature", ""))
        ):
            raise ValueError("LIFE_ANALOGY_NOVELTY_LOW")
        normalized_recent = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", recent.casefold())
        if (
            normalized_title
            and normalized_analogy
            and normalized_title in normalized_recent
            and normalized_analogy in normalized_recent
        ):
            raise ValueError("LIFE_ANALOGY_NOVELTY_LOW")
        recent_ngrams = _normalized_character_ngrams(recent)
        union = candidate_ngrams | recent_ngrams
        if union and len(candidate_ngrams & recent_ngrams) / len(union) >= 0.72:
            raise ValueError("LIFE_ANALOGY_NOVELTY_LOW")
    return (
        _LifeAnalogyCard(
            title=draft.title,
            analogy=draft.analogy,
            takeaway=draft.takeaway,
        ),
        {
            "concept_key": selection.concept_key,
            "concept_label": selection.concept_label,
            "mechanism": selection.mechanism,
            "format_key": selected_format,
            "scene_family": draft.scene_family,
            "situation_key": draft.situation_key,
            "mechanism_tag": draft.mechanism_tag,
            "style_mode": draft.style_mode,
            "meme_key": draft.meme_key,
            "scene_signature": draft.scene_signature,
        },
    )


@app.post("/api/personalized-updates/life-analogy")
async def create_life_analogy(request: Request, body: dict[str, Any]):
    """Create one private, provider-verified everyday analogy.

    This flow is deliberately separate from public-update discovery.  It does
    not call search, does not expose the input keywords, and accepts only a
    strict card-shaped provider response.
    """

    raw_keywords = body.get("keywords")
    if raw_keywords is None:
        raw_keywords = body.get("preferences")
    if raw_keywords is None:
        raw_keywords = body.get("interest_keywords")
    prompt_keywords, preference_tokens = _life_analogy_input_tokens(raw_keywords)
    if not prompt_keywords:
        fail("LIFE_ANALOGY_KEYWORDS_REQUIRED", "请先提供用于生成生活类比的关键词", 422)
    direction_items = (
        [str(value).strip()[:120] for value in raw_keywords if str(value).strip()][:16]
        if isinstance(raw_keywords, list)
        else [prompt_keywords]
    )
    recent_records = _life_analogy_recent_records(body.get("recent_cards"))
    try:
        variation_seed = max(0, min(int(body.get("variation_seed") or 0), 1_000_000))
    except (TypeError, ValueError):
        variation_seed = 0
    selected_format = _choose_life_analogy_format(recent_records, variation_seed)
    selected_humor_cue = _choose_life_analogy_humor_cue(variation_seed)
    required_style_mode, selected_meme = _choose_life_analogy_style(variation_seed)
    allowed_situations = _allowed_life_analogy_situations(recent_records, variation_seed)
    try:
        session = require_provider_session(
            str(body.get("provider_session_id") or ""),
        )
        if not bool(getattr(session, "verified", True)):
            fail("PROVIDER_SESSION_UNVERIFIED", "Provider 尚未验证，请先测试连接", 428)
        if str(getattr(session, "provider", "")).strip().casefold().replace("-", "_") == "fake":
            fail("PROVIDER_SESSION_REAL_REQUIRED", "生活类比需要已验证的真实模型连接", 428)
        async def generate_validated_card() -> tuple[_LifeAnalogyCard, dict[str, str]]:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Choose one specific difficult concept or mechanism from the learner's broad private directions, "
                        "then compare the allowed_familiar_situations and select the one whose cause, state transition, "
                        "boundary and consequence genuinely match it. If no situation fits the first concept precisely, "
                        "discard that concept and choose another; never force a superficial resemblance. Express the "
                        "result as one compact Simplified-Chinese memory "
                        "card using exactly the required_format. Explore the breadth of the directions; do not simply pick the most famous "
                        "The audience is an adult technical learner. Reject schoolbook morals such as 'be careful', "
                        "'check every digit', or 'do things in order'. The takeaway must expose a non-obvious failure "
                        "mode, boundary, trade-off or state transition that the learner could reuse in real reasoning. "
                        "concept. A field name is not a concept. Every concept, synonym and mechanism represented in "
                        "recent_history is forbidden. The format is a real structural constraint, not a decorative label. Do not "
                        "default to a person making a mistake and discovering the lesson; dialogue, comparison, layout, "
                        "experiment, rule or observation must behave differently when requested. The title and analogy "
                        "must be non-technical and must not name the concept, course, profession or private direction. "
                        "Put the conceptual bridge only in takeaway. Build the story inside the selected registered situation: it "
                        "must feel like a moment the learner or an ordinary friend has personally experienced. Use a "
                        "normal human as the viewpoint, a familiar action, and an immediately recognizable annoyance, "
                        "choice or consequence. Never use random animals, fantasy, sentient objects or a fairy-tale "
                        "world as the foundation. Humor is optional garnish after recognition, never the premise. The "
                        "scene must be physically and socially plausible today: never stitch together rules from two "
                        "different places (for example, do not invent queue numbers for an ordinary elevator). Silently ask whether a friend could actually encounter "
                        "the described action; rewrite it if not. "
                        "Use the selected allowed situation as the factual backbone; keep its ordinary real-world rules intact and "
                        "map the concept through details inside that situation rather than inventing a new service rule. "
                        "The viewpoint person must behave naturally and try to solve their problem; never make them "
                        "repeat an obvious mistake on purpose merely to demonstrate a mechanism. Silently draft two "
                        "possible concept-to-scene mappings and return only the one with matching causes, state changes "
                        "and consequences. Reject a mapping that shares only a label, shape or vague feeling. The title "
                        "and analogy must contain only the everyday scene: no technical terms and no sentence that says "
                        "what the computing concept is. Put every technical explanation in takeaway only. "
                        "The scene may follow required_humor_cue only when it remains natural. The mechanism must still "
                        "match the selected concept precisely. A meme-like phrase is welcome only when it fits; never "
                        "explain why the joke is funny. Avoid the "
                        "generic cast of 老板、顾客、管理员、服务员、厨师 unless the required format truly needs one. "
                        "Prefer an oddly specific object, rule, price, movement or internet-comment-style line. When it "
                        "fits naturally, include one short quotable detail; never insert absurdity merely to satisfy the "
                        "style note. Do not end "
                        "the analogy with a character explaining the technical lesson; the takeaway owns that job. "
                        "Silently check causal consistency before returning: no character may both perform and omit "
                        "the same action, and the consequence must follow from the stated action or missing condition. "
                        "Every visible ticket number, order number, quantity, price and elapsed duration must be "
                        "arithmetically consistent with the sentence that compares it. For example, A028 to A030 is "
                        "a difference of two, never ten. Omit exact numbers when they are not needed. "
                        "The three registered required_groups represent trigger, response and consequence in that exact "
                        "order. The analogy must contain one phrase from every group in that order. A person must react "
                        "as an ordinary person would: when the rule says to wait, they wait; when the result is uncertain, "
                        "they check it; never invent an irrelevant action or treat an expected rule as a surprising motive. "
                        "Echo required_style_mode exactly as style_mode. In safe_meme mode, use the exact selected_meme "
                        "phrase once inside the analogy, naturally at the moment described by its usage; echo meme_key, "
                        "meme_phrase and meme_evidence, with meme_evidence equal to that exact phrase. Do not explain the "
                        "meme. In smooth_life mode, leave all three meme fields empty and make the scene memorable through "
                        "rhythm, specificity and a clean cause-to-effect flow instead. "
                        "Analogy must be 50-190 Chinese characters with a setup, an action, a consequence and a "
                        "memorable twist. Takeaway must be 30-120 characters and explain the same causal structure. "
                        "Provide 2-3 mechanism_evidence snippets and one twist_evidence; every item must be an exact "
                        "substring of analogy and together they must show the mechanism rather than merely say it is "
                        "similar. Avoid current events, claims, URLs, jobs, statistics, Markdown, victims, "
                        "protected groups, self-harm and cruelty. JSON keys exactly: anchor_index, concept_key, "
                        "concept_label, concept_mechanism, common_confusion, memory_target, format_key, scene_family, situation_key, mechanism_tag, style_mode, "
                        "meme_key, meme_phrase, meme_evidence, scene_signature, title, analogy, takeaway, mechanism_evidence, twist_evidence. concept_key must be stable "
                        "and concise. Echo required_format exactly as format_key, plus the chosen registered "
                        "scene_family and situation_key exactly. Echo exactly one mechanism_tag from that situation's "
                        "mechanism_tags and choose a concept whose mechanism truly has that tag; neighboring mechanisms "
                        "are invalid even when the story sounds fluent. The title or analogy must "
                        "naturally contain at least one supplied scene cue. "
                        "scene_signature briefly names the "
                        "unique cast/object/action/structure. "
                        f"OPTIONAL STYLE NOTE FOR THIS CARD: {selected_humor_cue}"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "private_directions": [
                                {"index": index, "direction": value}
                                for index, value in enumerate(direction_items)
                            ],
                            "required_format": selected_format,
                            "required_style_mode": required_style_mode,
                            "selected_meme": selected_meme,
                            "allowed_familiar_situations": allowed_situations,
                            "required_humor_cue": selected_humor_cue,
                            "forbidden_concepts": [
                                record.get("concept_label") or record.get("concept_key")
                                for record in recent_records
                                if record.get("concept_label") or record.get("concept_key")
                            ],
                            "recent_history": [
                                {
                                    "concept_key": record.get("concept_key", ""),
                                    "concept_label": record.get("concept_label", ""),
                                    "mechanism": record.get("mechanism", ""),
                                    "format_key": record.get("format_key", ""),
                                    "scene_family": record.get("scene_family", ""),
                                    "situation_key": record.get("situation_key", ""),
                                    "mechanism_tag": record.get("mechanism_tag", ""),
                                    "style_mode": record.get("style_mode", ""),
                                    "meme_key": record.get("meme_key", ""),
                                    "scene_signature": record.get("scene_signature", ""),
                                    "summary": record.get("summary", "")[:260],
                                }
                                for record in recent_records
                            ],
                            "variation_seed": variation_seed,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            last_error: ValueError | None = None
            for attempt in range(3):
                try:
                    result, _meta = await call_bound_provider_json(
                        session,
                        messages,
                        compact_max_tokens=PROVIDER_POLICY.life_analogy_max_tokens,
                        temperature=0.65 if attempt == 0 else 0.45,
                    )
                except ProviderError as exc:
                    if attempt == 0 and exc.code in {
                        "INVALID_PROVIDER_JSON",
                        "PROVIDER_OUTPUT_TRUNCATED",
                        "PROVIDER_SCHEMA_INVALID",
                    }:
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "The previous response was not valid compact JSON. Retry once with exactly the "
                                    "requested keys, no Markdown, no commentary, and a shorter analogy. Keep the same "
                                    "required format and familiar scene family."
                                ),
                            }
                        )
                        continue
                    raise
                try:
                    card, memory = _validate_life_analogy(
                        result,
                        preference_tokens,
                        selected_format=selected_format,
                        required_style_mode=required_style_mode,
                        selected_meme=selected_meme,
                        allowed_situations=allowed_situations,
                        direction_items=direction_items,
                        recent_records=recent_records,
                    )
                    registered_situation = next(
                        value
                        for value in allowed_situations
                        if value.get("scene_family") == memory.get("scene_family")
                        and value.get("situation_key") == memory.get("situation_key")
                    )
                    # The deterministic checks catch malformed or ungrounded
                    # drafts.  A compact semantic review is still required for
                    # the one thing string rules cannot prove: whether the
                    # technical mechanism truly matches the everyday cause and
                    # consequence (for example, a duplicate transfer is not
                    # automatically an example of idempotency).
                    review_result, _review_meta = await call_bound_provider_json(
                        session,
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Judge the proposed Chinese analogy against its registered real-life situation. "
                                    "Return compact JSON only with accepted, "
                                    "mapping_score, relevance_score, situation_fidelity_score, logic_score, "
                                    "naturalness_score, sophistication_score, memorability_score, meme_fit_score "
                                    "(integers 1-5), issue_code. Reject wrong or neighboring technical mechanisms, "
                                    "invented real-world rules, reversed cause/result, child-level morals, forced memes, "
                                    "and generic advice. Accept only if every score is at least 4. issue_code: OK, "
                                    "FICTIONAL_RULE, WRONG_CONCEPT, SURFACE_SIMILARITY, REVERSED_LOGIC, WRONG_RESULT, "
                                    "UNNATURAL_ACTION, FORCED_MEME."
                                ),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "registered_situation": registered_situation,
                                        "technical_concept": memory.get("concept_label", ""),
                                        "technical_mechanism": memory.get("mechanism", ""),
                                        "mechanism_tag": memory.get("mechanism_tag", ""),
                                        "style_mode": required_style_mode,
                                        "meme": selected_meme,
                                        "card": card.model_dump(),
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                        compact_max_tokens=150,
                        temperature=0.0,
                    )
                    quality_review = _LifeAnalogyQualityReview.model_validate(review_result)
                    scores = (
                        quality_review.mapping_score,
                        quality_review.relevance_score,
                        quality_review.situation_fidelity_score,
                        quality_review.logic_score,
                        quality_review.naturalness_score,
                        quality_review.sophistication_score,
                        quality_review.memorability_score,
                        quality_review.meme_fit_score,
                    )
                    if not quality_review.accepted or min(scores) < 4:
                        raise ValueError(
                            f"LIFE_ANALOGY_REVIEW_{quality_review.issue_code or 'REJECTED'}"
                        )
                    return card, memory
                except ValueError as exc:
                    last_error = exc
                    _LOGGER.warning(
                        "life_analogy_candidate_rejected code=%s attempt=%s",
                        str(exc)[:80],
                        attempt + 1,
                    )
                    if attempt < 2:
                        selected_retry_situation = next(
                            (
                                value
                                for value in allowed_situations
                                if isinstance(result, dict)
                                and str(result.get("scene_family") or "")
                                == str(value.get("scene_family") or "")
                                and str(result.get("situation_key") or "")
                                == str(value.get("situation_key") or "")
                            ),
                            None,
                        )
                        required_group_hint = ""
                        if selected_retry_situation:
                            groups = selected_retry_situation.get("required_groups") or []
                            required_group_hint = (
                                " For the registered situation, copy at least one exact phrase from each "
                                "trigger, response, consequence group in this order: "
                                + "；".join("、".join(str(term) for term in group) for group in groups)
                                + ". Keep those phrases inside natural readable prose."
                            )
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "The previous draft failed the private quality gate with code "
                                    f"{str(exc)[:80]}. The rejected concept label was "
                                    f"{str(result.get('concept_label') if isinstance(result, dict) else '')[:40]!r}. "
                                    "Choose a different concept not present in recent_history, produce a completely "
                                    "new familiar human scene, and obey the required format and registered situation. Do not "
                                    "quote the previous draft."
                                    + required_group_hint
                                ),
                            }
                        )
            raise last_error or ValueError("LIFE_ANALOGY_CONTENT_INVALID")

        card, memory = await asyncio.wait_for(
            generate_validated_card(),
            timeout=PROVIDER_POLICY.life_analogy_timeout_seconds,
        )
    except asyncio.TimeoutError:
        fail(
            "LIFE_ANALOGY_TIMEOUT",
            "这张卡片生成得有点久，上一张已保留，请稍后再试",
            504,
        )
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)
    except ValueError:
        # Do not return Pydantic details: they can contain provider text or a
        # private keyword.  Keep this code stable for desktop callers.
        fail("LIFE_ANALOGY_CONTENT_INVALID", "模型没有返回可用的生活类比卡片", 502)
    return {
        "schema": "mentorflow.life-analogy.v1",
        "state": "ready",
        "card": card.model_dump(),
        "memory": memory,
    }


@app.post("/api/providers/test")
async def test_provider(request: Request, body: ProviderTestIn):
    try:
        session = create_provider_session(body.model_dump())
        result = await test_provider_session(session["provider_session_id"])
        return {**result, "message": "Provider 真实连接已验证；服务仅使用 Windows Credential Locker 引用。"}
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)

@app.get("/api/workspaces/{workspace_id}/retrieval/search")
def retrieval_search(workspace_id: str, q: str, top_k: int = 5):
    require_workspace(workspace_id)
    return {"query":q,"results":RetrievalService().search(workspace_id,q,top_k=max(1,min(top_k,20)))}

@app.get("/api/workspaces/{workspace_id}/conflicts")
def conflicts(workspace_id: str):
    require_workspace(workspace_id); sources=db.rows("sources","workspace_id",workspace_id); result=[]; seen=set()
    source_titles={source["id"]: source.get("title", source["id"]) for source in sources}
    chunks=[c for src in sources for c in db.rows("chunks","source_id",src["id"])]
    for i,a in enumerate(chunks):
        for b in chunks[i+1:]:
            if a["source_id"]==b["source_id"]:continue
            ah=a.get("heading") or ""; bh=b.get("heading") or ""
            if ah and bh and ah.lower()==bh.lower() and normalize(a["text"])!=normalize(b["text"]):
                pair=(a["source_id"],b["source_id"],ah.lower())
                if pair in seen: continue
                seen.add(pair)
                cid=f"conflict_{a['id']}_{b['id']}"
                try:
                    from .orm import SessionLocal, ConflictORM
                    with SessionLocal() as session:
                        if not session.get(ConflictORM,cid): session.add(ConflictORM(id=cid,workspace_id=workspace_id,topic=ah,source_a_id=a["source_id"],chunk_a_id=a["id"],source_b_id=b["source_id"],chunk_b_id=b["id"],summary="Different sources provide conflicting evidence for the same heading",evidence_a=a["text"][:500],evidence_b=b["text"][:500])); session.commit()
                except Exception: pass
                status="unresolved"; selected=""
                try:
                    with SessionLocal() as session:
                        record=session.get(ConflictORM,cid)
                        if record: status=record.status; selected=record.selected_source_id
                except Exception: pass
                result.append({"id":cid,"topic":ah,"status":status,"selected_source_id":selected,"source_a_id":a["source_id"],"source_a_title":source_titles.get(a["source_id"],a["source_id"]),"chunk_a_id":a["id"],"page_a":a.get("page"),"heading_a":a.get("heading"),"source_b_id":b["source_id"],"source_b_title":source_titles.get(b["source_id"],b["source_id"]),"chunk_b_id":b["id"],"page_b":b.get("page"),"heading_b":b.get("heading"),"summary":"不同来源对同一标题提供了不同表述","evidence_a":a["text"][:500],"evidence_b":b["text"][:500]})
    return result

@app.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: str, body: dict):
    persist_resolution=True
    if body.get("selected_source_id") in {body.get("source_a_id"),body.get("source_b_id")}:
        try:
            from .orm import SessionLocal, ConflictORM
            with SessionLocal() as session:
                record=session.get(ConflictORM,conflict_id)
                if record: record.status="resolved"; record.selected_source_id=body["selected_source_id"]; record.resolution="User selected this source"; record.resolved_at=datetime.now(timezone.utc); session.commit()
        except Exception: pass
    if body.get("selected_source_id") not in {body.get("source_a_id"),body.get("source_b_id")}:
        fail("CONFLICT_SOURCE_INVALID","必须选择冲突双方之一")
    return {"id":conflict_id,"status":"resolved","selected_source_id":body["selected_source_id"],"resolution":"用户已选择采用该来源"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        from .orm import SessionLocal,JobORM
        with SessionLocal() as s:
            job=s.get(JobORM,job_id)
            if job:return {"id":job.id,"workspace_id":job.workspace_id,"job_type":job.job_type,"status":job.status,"stage":job.stage,"progress":job.progress,"message":job.message,"error":job.error,"created_at":job.created_at.isoformat()}
    except Exception: pass
    item = db.row("jobs", "id", job_id)
    if not item: fail("JOB_NOT_FOUND", "任务不存在", 404)
    return json.loads(item["payload"])


def _narrative_error(exc: NarrativeRuntimeError):
    fail(exc.code, exc.message, exc.status_code, exc.details)


def _adaptive_error(exc: AdaptiveNarrativeError):
    fail(exc.code, exc.message, exc.status_code, exc.details)


@app.post("/api/workspaces/{workspace_id}/task-profile/analyze")
def task_profile_analyze(workspace_id: str, request: Request, body: dict[str, Any] | None = None):
    try:
        return {"request_id": request.state.request_id, **_analyze_task_profile_safe(workspace_id, force=bool((body or {}).get("force")))}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/workspaces/{workspace_id}/task-profile")
def task_profile_get(workspace_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **_analyze_task_profile_safe(workspace_id)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.post("/api/workspaces/{workspace_id}/task-profile/regenerate")
def task_profile_regenerate(workspace_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **_analyze_task_profile_safe(workspace_id, force=True)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/workspaces/{workspace_id}/learning-artifacts")
def workspace_learning_artifacts(workspace_id: str, request: Request, campaign_id: str | None = None):
    try:
        return {"request_id": request.state.request_id, "artifacts": list_artifacts(workspace_id, campaign_id)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/story-packs")
def story_packs(request: Request):
    return {"request_id": request.state.request_id, "story_packs": list_story_packs()}


@app.get("/api/story-packs/{story_pack_id}")
def story_pack_detail(story_pack_id: str, request: Request):
    try:
        pack = load_story_pack(story_pack_id)
        return {"request_id": request.state.request_id, "story_pack": {"story_pack_id": story_pack_id, "manifest": {key: value for key, value in pack["manifest"].items() if key not in {"checksum", "provenance_notes"}}, "validation": validate_story_pack(pack), "characters": [public_character(value) for value in pack["characters"].values()], "scenes": [{"scene_id": scene_id, "scene_type": scene.get("scene_type"), "story_order": scene.get("story_order"), "immutable": scene.get("immutable", False)} for scene_id, scene in pack["scenes"].items()]}}
    except StoryPackError as exc:
        fail(exc.code, exc.message, 400, exc.details)


@app.get("/api/story-packs/{story_pack_id}/assets/{asset_id}")
def story_pack_asset(story_pack_id: str, asset_id: str):
    try:
        pack = load_story_pack(story_pack_id)
        asset = next((item for item in pack["asset_manifest"].get("assets", []) if item.get("asset_id") == asset_id), None)
        if not asset or asset.get("status") != "ready" or not asset.get("path"):
            fail("STORY_PACK_ASSET_NOT_AVAILABLE", "资产不可用", 404)
        root = Path(pack["root"]).resolve(); path = (root / asset["path"]).resolve()
        if root not in path.parents or not path.is_file():
            fail("STORY_PACK_ASSET_NOT_AVAILABLE", "资产不可用", 404)
        return FileResponse(path)
    except StoryPackError as exc:
        fail(exc.code, exc.message, 400, exc.details)


@app.get("/api/story-catalog/health")
def story_catalog_health(request: Request):
    try:
        return {"request_id": request.state.request_id, **catalog_repository.health()}
    except catalog_repository.CatalogError as exc:
        fail(exc.code, exc.message, exc.status_code, exc.details)


@app.post("/api/story-catalog/search")
def story_catalog_search(request: Request, body: dict[str, Any]):
    try:
        return {"request_id": request.state.request_id, **catalog_repository.search(body)}
    except catalog_repository.CatalogError as exc:
        fail(exc.code, exc.message, exc.status_code, exc.details)


@app.post("/api/story-catalog/match")
def story_catalog_match(request: Request, body: dict[str, Any]):
    try:
        payload = dict(body)
        workspace_id = str(payload.get("workspace_id") or "")
        if workspace_id:
            payload["task_profile"] = _analyze_task_profile_safe(workspace_id, force=True)
        return {"request_id": request.state.request_id, **catalog_repository.match(payload)}
    except catalog_repository.CatalogError as exc:
        fail(exc.code, exc.message, exc.status_code, exc.details)
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/story-catalog/{catalog_id}")
def story_catalog_record(catalog_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, "record": catalog_repository.get_record(catalog_id)}
    except catalog_repository.CatalogError as exc:
        fail(exc.code, exc.message, exc.status_code, exc.details)


@app.get("/api/story-catalog/{catalog_id}/story-pack-status")
def story_catalog_pack_status(catalog_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **catalog_repository.story_pack_status(catalog_id)}
    except catalog_repository.CatalogError as exc:
        fail(exc.code, exc.message, exc.status_code, exc.details)


@app.post("/api/story-catalog/{catalog_id}/curation-request")
def story_catalog_curation_request(catalog_id: str, request: Request, body: dict[str, Any] | None = None):
    try:
        return {"request_id": request.state.request_id, **catalog_repository.request_curation(catalog_id, str((body or {}).get("workspace_id") or ""), dict((body or {}).get("task_profile") or {}))}
    except catalog_repository.CatalogError as exc:
        fail(exc.code, exc.message, exc.status_code, exc.details)


@app.get("/api/workspaces/{workspace_id}/campaigns")
def workspace_campaigns(workspace_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, "campaigns": list_campaigns(workspace_id)}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


@app.post("/api/workspaces/{workspace_id}/campaigns")
def workspace_campaign_create(workspace_id: str, request: Request, body: dict[str, Any] | None = None):
    try:
        result = create_campaign(workspace_id, str((body or {}).get("story_pack_id") or "dev_fixture_minimal"))
        result["adaptation_blueprint"] = compile_adaptation(result["campaign_id"])
        return {"request_id": request.state.request_id, **result}
    except (NarrativeRuntimeError, AdaptiveNarrativeError) as exc:
        if isinstance(exc, AdaptiveNarrativeError):
            _adaptive_error(exc)
        _narrative_error(exc)


@app.get("/api/campaigns/{campaign_id}")
def campaign_get(campaign_id: str, request: Request):
    try:
        result = get_campaign(campaign_id)
        result["adaptation_blueprint"] = get_blueprint(campaign_id)
        result["story_objects"] = list_story_objects(campaign_id)
        result["current_scene_contract"] = get_current_contract(campaign_id)
        return {"request_id": request.state.request_id, **result}
    except (NarrativeRuntimeError, AdaptiveNarrativeError) as exc:
        if isinstance(exc, AdaptiveNarrativeError):
            _adaptive_error(exc)
        _narrative_error(exc)


@app.post("/api/campaigns/{campaign_id}/advance")
def campaign_advance(campaign_id: str, request: Request, body: dict[str, Any] | None = None):
    try:
        return {"request_id": request.state.request_id, **advance_campaign(campaign_id, body or {})}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


@app.post("/api/campaigns/{campaign_id}/choices")
def campaign_choice(campaign_id: str, request: Request, body: dict[str, Any]):
    try:
        return {"request_id": request.state.request_id, **choose_campaign(campaign_id, body)}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


@app.post("/api/campaigns/{campaign_id}/messages")
def campaign_messages(campaign_id: str, request: Request, body: dict[str, Any]):
    try:
        return {"request_id": request.state.request_id, **campaign_message(campaign_id, body)}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


@app.post("/api/campaigns/{campaign_id}/learning-events")
def campaign_learning_event(campaign_id: str, request: Request, body: dict[str, Any]):
    if os.getenv("MENTORFLOW_ENABLE_TEST_LEARNING_EVENTS", "0") != "1":
        fail("INTERNAL_ONLY", "学习通过事件只能由服务器内部 Tutor/Evaluator/Task 服务发布", 404)
    try:
        return {"request_id": request.state.request_id, **ingest_learning_event(campaign_id, body)}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


@app.get("/api/campaigns/{campaign_id}/history")
def campaign_history_route(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, "events": campaign_history(campaign_id)}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


@app.get("/api/campaigns/{campaign_id}/available-actions")
def campaign_actions(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **narrative_available_actions(campaign_id)}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


@app.post("/api/campaigns/{campaign_id}/compile-adaptation")
def campaign_compile_adaptation(campaign_id: str, request: Request, body: dict[str, Any] | None = None):
    try:
        return {"request_id": request.state.request_id, **compile_adaptation(campaign_id, force=bool((body or {}).get("force")))}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/campaigns/{campaign_id}/adaptation-blueprint")
def campaign_adaptation_blueprint(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **get_blueprint(campaign_id)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.post("/api/campaigns/{campaign_id}/adaptation-blueprint/regenerate")
def campaign_adaptation_regenerate(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **compile_adaptation(campaign_id, force=True)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.post("/api/campaigns/{campaign_id}/adaptation-blueprint/activate")
def campaign_adaptation_activate(campaign_id: str, request: Request, body: dict[str, Any]):
    try:
        return {"request_id": request.state.request_id, **activate_blueprint(campaign_id, str(body.get("blueprint_id") or ""))}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/campaigns/{campaign_id}/scene-contracts/current")
def campaign_scene_contract_current(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **get_current_contract(campaign_id)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.post("/api/campaigns/{campaign_id}/scene-contracts/generate")
def campaign_scene_contract_generate(campaign_id: str, request: Request, body: dict[str, Any] | None = None):
    try:
        contract = generate_scene_contract(campaign_id, force=bool((body or {}).get("force")))
        if (body or {}).get("generate_scene"):
            contract["generated_scene"] = generate_dynamic_scene(campaign_id, contract["contract_id"])
        return {"request_id": request.state.request_id, **contract}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/campaigns/{campaign_id}/scene-contracts/history")
def campaign_scene_contract_history(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, "contracts": list_contracts(campaign_id)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/campaigns/{campaign_id}/story-objects")
def campaign_story_objects(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, "story_objects": list_story_objects(campaign_id)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.get("/api/campaigns/{campaign_id}/adaptive-context")
def campaign_adaptive_context(campaign_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **adaptive_context(campaign_id)}
    except AdaptiveNarrativeError as exc:
        _adaptive_error(exc)


@app.post("/api/campaigns/{campaign_id}/voice/synthesize")
def campaign_voice(campaign_id: str, request: Request, body: dict[str, Any]):
    try:
        get_campaign(campaign_id)
        result = synthesize_voice(str(body.get("text") or ""), str(body.get("voice_profile_id") or ""), str(body.get("provider") or "fake"), str(body.get("style") or "neutral"))
        return {"request_id": request.state.request_id, **result}
    except NarrativeRuntimeError as exc:
        _narrative_error(exc)


def _expedition_error(exc: ExpeditionError):
    fail(exc.code, exc.message, exc.status_code, exc.details)


def _art_pack_error(exc: ArtPackError):
    fail(exc.code, exc.message, exc.status, exc.details)


@app.get("/api/expedition/profile")
def expedition_profile_get(request: Request):
    try: return {"request_id": request.state.request_id, **expedition_profile()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/ledger")
def expedition_ledger_get(request: Request):
    try: return {"request_id": request.state.request_id, **expedition_ledger()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/heroes")
def expedition_heroes_get(request: Request):
    try: return {"request_id": request.state.request_id, **list_heroes()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/heroes/{hero_id}")
def expedition_hero_get(hero_id: str, request: Request):
    try: return {"request_id": request.state.request_id, "item": hero_detail(hero_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/heroes/{hero_id}/assets")
def expedition_hero_assets_get(hero_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **expedition_hero_assets(hero_id)}
    except ArtPackError as exc: _art_pack_error(exc)


@app.post("/api/expedition/heroes/{hero_id}/star-up")
def expedition_hero_star(hero_id: str, request: Request):
    try: return {"request_id": request.state.request_id, "item": star_up(hero_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/heroes/{hero_id}/awaken")
def expedition_hero_awaken(hero_id: str, request: Request):
    try: return {"request_id": request.state.request_id, "item": expedition_awaken(hero_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/training-slots")
def expedition_training_get(request: Request):
    try: return {"request_id": request.state.request_id, **training_slots()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/training-slots/{slot_id}/upgrade")
def expedition_training_upgrade(slot_id: str, request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **upgrade_slot(slot_id, body)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/training-slots/{slot_id}/reset")
def expedition_training_reset(slot_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **reset_slot(slot_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/lineups")
def expedition_lineups_get(request: Request):
    try: return {"request_id": request.state.request_id, **expedition_lineups()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/lineups")
def expedition_lineup_post(request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **save_lineup(body)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.put("/api/expedition/lineups/{lineup_id}")
def expedition_lineup_put(lineup_id: str, request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **save_lineup(body, lineup_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.delete("/api/expedition/lineups/{lineup_id}")
def expedition_lineup_delete(lineup_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **delete_lineup(lineup_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/lineups/{lineup_id}/activate")
def expedition_lineup_activate(lineup_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **activate_lineup(lineup_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/equipment")
def expedition_equipment_get(request: Request):
    try: return {"request_id": request.state.request_id, **expedition_equipment()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/equipment/{instance_id}/equip")
def expedition_equipment_equip(instance_id: str, request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **equip_equipment(instance_id, str(body.get("hero_id") or ""))}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/equipment/{instance_id}/unequip")
def expedition_equipment_unequip(instance_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **unequip_equipment(instance_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/equipment/{instance_id}/upgrade")
def expedition_equipment_upgrade(instance_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **upgrade_equipment(instance_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/equipment/{instance_id}/reset")
def expedition_equipment_reset(instance_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **reset_equipment(instance_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/pets")
def expedition_pets_get(request: Request):
    try: return {"request_id": request.state.request_id, **expedition_pets()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/pets/{pet_id}/equip")
def expedition_pet_equip(pet_id: str, request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **equip_pet(pet_id, str(body.get("hero_id") or ""))}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/pets/{pet_id}/unequip")
def expedition_pet_unequip(pet_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **equip_pet(pet_id, None)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/gacha/banners")
def expedition_gacha_banners(request: Request):
    try: return {"request_id": request.state.request_id, **gacha_banners()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/gacha/pull")
def expedition_gacha_pull(request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, **gacha_pull(body)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/gacha/history")
def expedition_gacha_history(request: Request):
    try: return {"request_id": request.state.request_id, **gacha_history()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/stages/current")
def expedition_stages_current(request: Request):
    try: return {"request_id": request.state.request_id, **expedition_stages()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/stages/{stage_number}")
def expedition_stage_get(stage_number: int, request: Request):
    try: return {"request_id": request.state.request_id, **stage_definition(stage_number)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/stages/{stage_number}/challenge")
def expedition_stage_challenge(stage_number: int, request: Request, body: dict[str, Any] | None = None):
    try: return {"request_id": request.state.request_id, **challenge_stage(stage_number, body or {})}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/stages/{stage_number}/claim")
def expedition_stage_claim(stage_number: int, request: Request):
    try: return {"request_id": request.state.request_id, **claim_stage(stage_number)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/battles/{battle_id}")
def expedition_battle_get(battle_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **expedition_battle(battle_id)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.get("/api/expedition/battles/{battle_id}/events")
def expedition_battle_events(battle_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **expedition_battle(battle_id, events=True)}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/art-packs/preview")
async def expedition_art_pack_preview(request: Request, file: UploadFile = File(...)):
    try:
        content = await read_upload_limited(file, MAX_PACK_BYTES)
        return {"request_id": request.state.request_id, **preview_expedition_art_bytes(content, file.filename or "art-pack.zip")}
    except UploadLimitExceeded as exc:
        raise ArtPackError("ART_PACK_TOO_LARGE", "美术包超过 100 MB 限制", 413, exc.details) from exc
    except ArtPackError as exc: _art_pack_error(exc)


@app.post("/api/expedition/art-packs/install")
def expedition_art_pack_install(request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, "item": install_expedition_art_preview(str(body.get("preview_id") or ""), bool(body.get("confirm")))}
    except ArtPackError as exc: _art_pack_error(exc)


@app.get("/api/expedition/art-packs")
def expedition_art_pack_list(request: Request):
    try: return {"request_id": request.state.request_id, **list_expedition_art_packs()}
    except ArtPackError as exc: _art_pack_error(exc)


@app.get("/api/expedition/art-packs/active")
def expedition_art_pack_active(request: Request):
    try: return {"request_id": request.state.request_id, **active_pack()}
    except ArtPackError as exc: _art_pack_error(exc)


@app.post("/api/expedition/art-packs/{pack_id}/{version}/activate")
def expedition_art_pack_activate(pack_id: str, version: str, request: Request):
    try: return {"request_id": request.state.request_id, "item": activate_pack(pack_id, version)}
    except ArtPackError as exc: _art_pack_error(exc)


@app.post("/api/expedition/art-packs/{pack_id}/{version}/rollback")
def expedition_art_pack_rollback(pack_id: str, version: str, request: Request):
    try: return {"request_id": request.state.request_id, "item": rollback_pack(pack_id, version)}
    except ArtPackError as exc: _art_pack_error(exc)


@app.post("/api/expedition/art-packs/{pack_id}/{version}/reviews/{hero_id}/{asset_kind}")
def expedition_art_pack_review(pack_id: str, version: str, hero_id: str, asset_kind: str, request: Request, body: dict[str, Any]):
    try: return {"request_id": request.state.request_id, "item": review_expedition_art_asset(pack_id, version, hero_id, asset_kind, str(body.get("review_status") or "PENDING"), str(body.get("review_note") or ""))}
    except ArtPackError as exc: _art_pack_error(exc)


@app.get("/api/expedition/art-assets/{asset_id:path}/file")
def expedition_art_asset_file(asset_id: str):
    try:
        path = resolve_expedition_art_asset_file(asset_id)
        return FileResponse(path, media_type="image/webp" if path.suffix.lower() == ".webp" else "image/png")
    except ArtPackError as exc: _art_pack_error(exc)


@app.get("/api/expedition/art-assets/{asset_id:path}")
def expedition_art_asset_metadata(asset_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **expedition_art_asset_public(asset_id)}
    except ArtPackError as exc: _art_pack_error(exc)


@app.get("/api/expedition/assets/{asset_id:path}")
def expedition_asset_get(asset_id: str, request: Request):
    try:
        if asset_id.startswith("art:"):
            return {"request_id": request.state.request_id, **expedition_art_asset_public(asset_id)}
        return {"request_id": request.state.request_id, **expedition_asset(asset_id)}
    except (ExpeditionError, ArtPackError) as exc:
        _art_pack_error(exc) if isinstance(exc, ArtPackError) else _expedition_error(exc)


@app.get("/api/expedition/milestones")
def expedition_milestones_get(request: Request):
    try: return {"request_id": request.state.request_id, **expedition_milestones()}
    except ExpeditionError as exc: _expedition_error(exc)


@app.post("/api/expedition/milestones/{milestone_id}/claim")
def expedition_milestone_claim(milestone_id: str, request: Request):
    try: return {"request_id": request.state.request_id, **claim_milestone(milestone_id)}
    except ExpeditionError as exc: _expedition_error(exc)


def _teacher_avatar_error(
    exc: TeacherAvatarError,
    *,
    details: dict[str, object] | None = None,
) -> None:
    # Route all avatar failures through the canonical application envelope.
    # The generic HTTPException handler expects ``detail.error`` and would
    # otherwise collapse these domain codes to HTTP_ERROR.
    fail(exc.code, exc.message, exc.status_code, details or {})


def _spine_teacher_error(exc: SpineTeacherError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _teacher_avatar_upload_limit(filename: str) -> int:
    """Keep the route-level cap aligned with the registry's format limits."""

    return (
        MAX_KOIKATSU_EXPORT_BYTES
        if Path(filename or "").suffix.casefold() == ".zip"
        else MAX_MODEL_BYTES
    )


@app.get("/api/spine-teachers/active")
def spine_teacher_active(request: Request):
    try:
        return {"request_id": request.state.request_id, "item": active_spine_teacher()}
    except SpineTeacherError as exc:
        _spine_teacher_error(exc)


@app.post("/api/spine-teachers/import")
async def spine_teacher_import(
    request: Request,
    file: UploadFile = File(...),
    display_name: str = Form(""),
    creator: str = Form(""),
    spine_editor_license_confirmed: bool = Form(False),
    character_rights_confirmed: bool = Form(False),
    adult_character_confirmed: bool = Form(False),
):
    try:
        content = await read_upload_limited(file, MAX_SPINE_ARCHIVE_BYTES)
        item = import_spine_teacher_package(
            file.filename or "teacher.zip",
            content,
            display_name=display_name,
            creator=creator,
            spine_editor_license_confirmed=spine_editor_license_confirmed,
            character_rights_confirmed=character_rights_confirmed,
            adult_character_confirmed=adult_character_confirmed,
        )
        return {"request_id": request.state.request_id, "item": item}
    except UploadLimitExceeded as exc:
        _spine_teacher_error(
            SpineTeacherError(
                "SPINE_PACKAGE_SIZE_INVALID",
                "Spine 教师 ZIP 为空或超过 100 MB。",
                413,
            )
        )
    except SpineTeacherError as exc:
        _spine_teacher_error(exc)


@app.get("/api/spine-teachers/{teacher_id}/manifest")
def spine_teacher_manifest_get(teacher_id: str, request: Request):
    try:
        return {"request_id": request.state.request_id, **spine_teacher_manifest(teacher_id)}
    except SpineTeacherError as exc:
        _spine_teacher_error(exc)


@app.get("/api/spine-teachers/{teacher_id}/assets/{asset_name:path}")
def spine_teacher_asset_get(teacher_id: str, asset_name: str):
    try:
        path, media_type = resolve_spine_teacher_asset(teacher_id, asset_name)
        safe_name = path.name.encode("ascii", "ignore").decode() or "asset"
        return FileResponse(path, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{safe_name}"'})
    except SpineTeacherError as exc:
        _spine_teacher_error(exc)


@app.get("/api/teacher-avatars")
def teacher_avatar_list(request: Request):
    try:
        return {"request_id": request.state.request_id, **list_teacher_avatars()}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/teacher-avatar-character-packs")
def teacher_avatar_character_pack_list(request: Request):
    try:
        return {"request_id": request.state.request_id, **list_kks_character_packs()}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/teacher-avatar-character-packs/{pack_id}/download")
def teacher_avatar_character_pack_download(pack_id: str):
    try:
        path = resolve_kks_character_pack(pack_id)
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
        )
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/koikatsu-runtime-packs/{pack_id}/manifest")
def koikatsu_runtime_pack_manifest(pack_id: str):
    try:
        path = resolve_runtime_pack_manifest(pack_id)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        return validate_runtime_pack_head_contract(manifest, path.parent.parent)
    except (TeacherAvatarError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, TeacherAvatarError):
            _teacher_avatar_error(exc)
        raise HTTPException(status_code=503, detail={"code": "RUNTIME_PACK_INVALID", "message": "runtime pack manifest is unreadable"}) from exc


@app.get("/api/koikatsu-runtime-packs/{pack_id}/asset/{asset_path:path}")
def koikatsu_runtime_pack_asset(pack_id: str, asset_path: str):
    try:
        path = resolve_runtime_pack_asset(pack_id, asset_path)
        return FileResponse(path, filename=path.name)
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/teacher-avatars/today")
def teacher_avatar_today(request: Request, workspace_id: str = "default"):
    try:
        return {"request_id": request.state.request_id, **todays_teacher_avatar(workspace_id)}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/teacher-avatar-presets")
def teacher_avatar_preset_list(
    request: Request,
    q: str = "",
    tag: str = "",
    limit: int = 48,
    cursor: int = 0,
):
    try:
        return {
            "request_id": request.state.request_id,
            **list_teacher_avatar_presets(query=q, tags=[tag] if tag else None, limit=limit, cursor=cursor),
        }
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.post("/api/teacher-avatar-presets/{preset_id}/activate")
def teacher_avatar_preset_activate(preset_id: str, request: Request, body: dict[str, Any]):
    try:
        item = activate_teacher_avatar_preset(
            preset_id,
            adult_character_confirmed=body.get("adult_character_confirmed") is True,
            attribution_confirmed=body.get("attribution_confirmed") is True,
        )
        return {"request_id": request.state.request_id, "item": item}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/teacher-avatar-presets/{preset_id}/preview")
def teacher_avatar_preset_preview(preset_id: str):
    try:
        preview = resolve_teacher_avatar_preset_preview(preset_id)
        return FileResponse(
            preview,
            media_type="image/webp" if preview.suffix.lower() == ".webp" else "image/png",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.post("/api/teacher-avatar-cards/validate")
async def teacher_avatar_card_validate(request: Request, file: UploadFile = File(...)):
    try:
        async with _KOIKATSU_CPU_LIMITER:
            content = await _read_bounded_koikatsu_card(file)
            card = await run_in_threadpool(
                validate_teacher_avatar_card,
                file.filename or "teacher-card.json",
                content,
            )
        return {"request_id": request.state.request_id, "card": card}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.post("/api/koikatsu-cards/inspect")
async def koikatsu_card_inspect(file: UploadFile = File(...)):
    try:
        if not (file.filename or "").lower().endswith(".png"):
            raise HTTPException(status_code=415, detail={"code": "CARD_FORMAT_UNSUPPORTED", "message": "请选择恋活角色卡 PNG 文件。"})
        content = await read_upload_limited(file, MAX_KKS_CARD_BYTES)
        return {"filename": file.filename or "character.png", "card": inspect_koikatsu_sunshine_card(content)}
    except UploadLimitExceeded as exc:
        raise HTTPException(status_code=413, detail={"code": "KOIKATSU_CARD_TOO_LARGE", "message": "恋活角色卡超过 96 MB 限制。", "details": exc.details}) from exc
    except KoikatsuCardError as exc:
        raise HTTPException(status_code=415, detail={"code": "KOIKATSU_CARD_INVALID", "message": f"无法读取恋活 Sunshine 角色卡：{exc}"}) from exc


@app.post("/api/koikatsu-cards/export")
async def koikatsu_card_export(
    file: UploadFile = File(...),
    body_values: str = Form("{}"),
    face_values: str = Form("{}"),
):
    try:
        parsed_body = json.loads(body_values)
        parsed_face = json.loads(face_values)
        if not isinstance(parsed_body, dict) or not isinstance(parsed_face, dict):
            raise ValueError("slider values must be objects")
        body = {str(key): float(value) for key, value in parsed_body.items()}
        face = {int(key): float(value) for key, value in parsed_face.items()}
        content = await read_upload_limited(file, MAX_KKS_CARD_BYTES)
        result = rewrite_koikatsu_sunshine_card(content, body_values=body, face_values=face)
        output_name = f"{Path(file.filename or 'koikatsu-character').stem}-edited.png"
        return Response(content=result, media_type="image/png", headers={"Content-Disposition": f'attachment; filename="{output_name}"'})
    except UploadLimitExceeded as exc:
        raise HTTPException(status_code=413, detail={"code": "KOIKATSU_CARD_TOO_LARGE", "message": "恋活角色卡超过 96 MB 限制。", "details": exc.details}) from exc
    except (KoikatsuCardError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=415, detail={"code": "KOIKATSU_CARD_INVALID", "message": f"无法导出恋活角色卡：{exc}"}) from exc


def _koikatsu_teacher_error(exc: KoikatsuTeacherError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


@app.get("/api/koikatsu-cards/default")
def koikatsu_card_default():
    try:
        path = default_koikatsu_card()
        return FileResponse(path, media_type="image/png", filename=path.name)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/local")
def koikatsu_local_card_list(q: str = "", limit: int = 24, offset: int = 0, asset_status: str = ""):
    return list_local_koikatsu_cards(query=q, limit=limit, offset=offset, asset_status=asset_status)


@app.get("/api/koikatsu-cards/local/{card_id}")
def koikatsu_local_card(card_id: str):
    try:
        path = resolve_local_koikatsu_card(card_id)
        return FileResponse(path, media_type="image/png", filename=path.name)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/local/{card_id}/resources")
def koikatsu_local_card_resources(card_id: str):
    try:
        return local_card_resources(card_id)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/local/{card_id}/runtime-manifest")
def koikatsu_local_card_runtime_manifest(card_id: str):
    try:
        return runtime_card_manifest(card_id)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/local/{card_id}/runtime/{asset_path:path}")
def koikatsu_local_card_runtime_asset(card_id: str, asset_path: str):
    try:
        path = resolve_runtime_asset(card_id, asset_path)
        return FileResponse(path, filename=path.name)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/runtime-shared/{asset_path:path}")
def koikatsu_shared_runtime_asset(asset_path: str):
    try:
        path = resolve_shared_runtime_asset(asset_path)
        return FileResponse(path, filename=path.name)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/shared-base/neutral-template")
def koikatsu_shared_neutral_template():
    try:
        return shared_neutral_template_manifest()
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/shared-base/{asset_name:path}")
def koikatsu_shared_base_asset(asset_name: str):
    try:
        path = resolve_shared_base_asset(asset_name)
        return FileResponse(path, filename=path.name)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/local/{card_id}/model/{asset_name:path}")
def koikatsu_local_export_asset(card_id: str, asset_name: str):
    try:
        path = resolve_local_export_asset(card_id, asset_name)
        return FileResponse(path, filename=path.name)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-cards/local/{card_id}/web/{asset_path:path}")
def koikatsu_local_web_model_asset(card_id: str, asset_path: str):
    try:
        path = resolve_web_model_asset(card_id, asset_path)
        media_type = "model/gltf-binary" if path.suffix.lower() == ".glb" else None
        return FileResponse(path, filename=path.name, media_type=media_type)
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-teachers")
def koikatsu_teacher_list():
    try:
        return list_koikatsu_teachers()
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.post("/api/koikatsu-teachers")
async def koikatsu_teacher_save(
    file: UploadFile = File(...),
    teacher_id: str = Form(""),
    display_name: str = Form(""),
    persona: str = Form(""),
    teaching_style: str = Form(""),
):
    try:
        item = await _read_and_run_bounded_koikatsu(
            file,
            save_koikatsu_teacher,
            teacher_id=teacher_id,
            display_name=display_name,
            persona=persona,
            teaching_style=teaching_style,
        )
        return {"item": item}
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/koikatsu-teachers/{teacher_id}/card")
def koikatsu_teacher_card(teacher_id: str):
    try:
        return FileResponse(resolve_koikatsu_teacher_card(teacher_id), media_type="image/png", filename=f"{teacher_id}.png")
    except KoikatsuTeacherError as exc:
        _koikatsu_teacher_error(exc)


@app.get("/api/teacher-avatar-card-presets")
def teacher_avatar_card_preset_list(request: Request):
    try:
        return {"request_id": request.state.request_id, **list_teacher_avatar_card_presets()}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.post("/api/teacher-avatars/import")
async def teacher_avatar_import(
    request: Request,
    file: UploadFile = File(...),
    display_name: str = Form(""),
    creator: str = Form(""),
    source_url: str = Form(""),
    license_name: str = Form(""),
    license_url: str = Form(""),
    rights_confirmed: bool = Form(False),
    adult_character_confirmed: bool = Form(False),
):
    try:
        content = await read_upload_limited(file, _teacher_avatar_upload_limit(file.filename or "avatar.vrm"))
        item = import_teacher_avatar(
            file.filename or "avatar.vrm",
            content,
            display_name=display_name,
            creator=creator,
            source_url=source_url,
            license_name=license_name,
            license_url=license_url,
            rights_confirmed=rights_confirmed,
            adult_character_confirmed=adult_character_confirmed,
        )
        return {"request_id": request.state.request_id, "item": item}
    except UploadLimitExceeded as exc:
        code = "RUNTIME_PACK_SIZE_INVALID" if Path(file.filename or "").suffix.casefold() == ".zip" else "MODEL_SIZE_INVALID"
        message = "runtime pack is empty or too large" if code.startswith("RUNTIME") else "模型必须非空且不超过 100MB。"
        _teacher_avatar_error(TeacherAvatarError(code, message, 413))
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.post("/api/teacher-avatars/import/koikatsu-local/{card_id}")
async def teacher_avatar_import_koikatsu_local(
    request: Request,
    card_id: str,
    display_name: str = Form(""),
    creator: str = Form(""),
    source_url: str = Form(""),
    license_name: str = Form(""),
    license_url: str = Form(""),
    rights_confirmed: bool = Form(False),
    adult_character_confirmed: bool = Form(False),
):
    try:
        item = import_local_koikatsu_teacher_avatar(
            card_id,
            display_name=display_name,
            creator=creator,
            source_url=source_url,
            license_name=license_name,
            license_url=license_url,
            rights_confirmed=rights_confirmed,
            adult_character_confirmed=adult_character_confirmed,
        )
        return {"request_id": request.state.request_id, "item": item}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.post("/api/teacher-avatars/import-batch")
async def teacher_avatar_import_batch(
    request: Request,
    files: list[UploadFile] = File(..., alias="file"),
    creator: str = Form(""),
    source_url: str = Form(""),
    license_name: str = Form(""),
    license_url: str = Form(""),
    rights_confirmed: bool = Form(False),
    adult_character_confirmed: bool = Form(False),
):
    try:
        if len(files) > MAX_BATCH_MODELS:
            _teacher_avatar_error(
                TeacherAvatarError(
                    "BATCH_LIMIT",
                    f"一次最多导入 {MAX_BATCH_MODELS} 个模型。",
                    413,
                )
            )
        payload: list[tuple[str, bytes]] = []
        aggregate_used = 0
        for file in files:
            filename = file.filename or "avatar.vrm"
            try:
                content = await read_upload_limited(
                    file,
                    _teacher_avatar_upload_limit(filename),
                    aggregate_limit=MAX_BATCH_BYTES,
                    aggregate_used=aggregate_used,
                )
            except UploadLimitExceeded as exc:
                if exc.aggregate:
                    _teacher_avatar_error(
                        TeacherAvatarError(
                            "BATCH_SIZE_LIMIT",
                            "本次导入的模型总大小超过 500MB。",
                            413,
                        ),
                        details=exc.details,
                    )
                code = "RUNTIME_PACK_SIZE_INVALID" if Path(filename).suffix.casefold() == ".zip" else "MODEL_SIZE_INVALID"
                message = (
                    "runtime pack 必须非空且单文件不超过 500MB。"
                    if code.startswith("RUNTIME")
                    else "模型必须非空且不超过 100MB。"
                )
                _teacher_avatar_error(
                    TeacherAvatarError(code, message, 413),
                    details=exc.details,
                )
            payload.append((filename, content))
            aggregate_used += len(content)
        result = import_teacher_avatar_batch(
            payload,
            creator=creator,
            source_url=source_url,
            license_name=license_name,
            license_url=license_url,
            rights_confirmed=rights_confirmed,
            adult_character_confirmed=adult_character_confirmed,
        )
        return {"request_id": request.state.request_id, **result}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/teacher-avatars/{model_id}/file")
def teacher_avatar_file(model_id: str):
    try:
        path = resolve_teacher_avatar_file(model_id)
        # PMX/GLB are loaded by the renderer, while image sidecars are loaded
        # through <img>/TextureLoader.  `filename=` makes Starlette emit an
        # attachment disposition, which browser clients may block as a
        # download instead of decoding as an image. Keep the model endpoint
        # inline as well so every local asset follows the same rule.
        safe_name = path.name.encode("ascii", "ignore").decode() or "asset"
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", headers={"Content-Disposition": f'inline; filename="{safe_name}"'})
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.get("/api/teacher-avatars/{model_id}/file/{asset_name:path}")
def teacher_avatar_asset_file(model_id: str, asset_name: str):
    try:
        path = resolve_teacher_avatar_file(model_id, asset_name)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_name = path.name.encode("ascii", "ignore").decode() or "asset"
        return FileResponse(path, media_type=media_type, headers={"Content-Disposition": f'inline; filename="{safe_name}"'})
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


@app.patch("/api/teacher-avatars/{model_id}/morph")
def teacher_avatar_morph(model_id: str, request: Request, body: dict[str, Any]):
    try:
        return {"request_id": request.state.request_id, "item": update_teacher_avatar_morph(model_id, body)}
    except TeacherAvatarError as exc:
        _teacher_avatar_error(exc)


STATIC = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")
    # Private teacher files live in durable user data instead of the public
    # installer. Mount the exact local subtree first so program updates can
    # replace application files without deleting or replacing the learner's
    # explicitly selected model.
    if private_teacher_asset_available():
        app.mount(
            PRIVATE_TEACHER_ROUTE,
            StaticFiles(directory=private_teacher_asset_root()),
            name="teacher2d-private-local",
        )
    teacher2d_static = STATIC / "teacher2d"
    if teacher2d_static.exists():
        app.mount(
            "/teacher2d",
            StaticFiles(directory=teacher2d_static),
            name="teacher2d",
        )
    vendor_static = STATIC / "vendor"
    if vendor_static.exists():
        # Live2D Cubism Core is a runtime script rather than a bundled ESM
        # dependency.  Mount vendor assets explicitly so the SPA fallback does
        # not return index.html for the script URL.
        app.mount(
            "/vendor",
            StaticFiles(directory=vendor_static),
            name="vendor",
        )

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"): fail("NOT_FOUND", "API 路由不存在", 404)
        return FileResponse(STATIC / "index.html")
