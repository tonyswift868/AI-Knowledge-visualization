from __future__ import annotations

import json
import mimetypes
import os
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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .schemas import ArchiveImportIn, FeasibilityConfirmIn, GoalPatch, GoalProfile, LocalSourceIn, ProviderSessionCreateIn, ProviderTestIn, TextSourceIn, UrlSourceIn, WorkspaceIn, WorkspacePatch
from .services import analyze, available_days, build_plan, create_source, inspect_local, parse_text, safe_path, scene_for_task, source_detail, tutor, uid, normalize
from .orm import init_orm
from .ingestion_secure import create_preview, consume_preview, parse_file, chunks as secure_chunks
from .provider_service import get_provider, ProviderError
from .retrieval_service import RetrievalService
from .grounding_service import critic as grounding_critic
from .job_service import create_job, set_job
from .url_fetcher_secure import fetch_safe
from .core_errors import app_error_body, AppError
from .provider_runtime import create_provider_session, create_verified_demo_session, delete_provider_session, generation_meta, get_provider_session, provider_for_session, public_session, require_provider_session, test_provider_session
from .archive_import import ArchiveError, cleanup_expired_previews, create_preview as create_archive_preview, discard_preview, import_preview as import_archive_preview
from .story_pack import StoryPackError, list_story_packs, load_story_pack, public_character, public_scene, validate_story_pack
from .narrative_runtime import NarrativeRuntimeError, available_actions as narrative_available_actions, campaign_history, campaign_message, choose_campaign, create_campaign, get_campaign, ingest_learning_event, list_campaigns, advance_campaign, publish_learning_event
from .voice_runtime import synthesize_voice
from . import catalog_repository
from .learning_run import LearningRunError, add_run_message, create_learning_run, get_learning_run, request_hint, submit_learning_run, submit_quick_quiz
from .adaptive_narrative import AdaptiveNarrativeError, activate_blueprint, adaptive_context, analyze_task_profile, compile_adaptation, generate_dynamic_scene, generate_scene_contract, get_blueprint, get_current_contract, list_artifacts, list_contracts, list_story_objects
from .expedition_service import ExpeditionError, activate_lineup, assets as expedition_asset, awaken as expedition_awaken, battle as expedition_battle, challenge_stage, claim_milestone, claim_stage, delete_lineup, equipment as expedition_equipment, equip_equipment, equip_pet, get_ledger as expedition_ledger, get_profile as expedition_profile, gacha_banners, gacha_history, gacha_pull, hero_detail, lineups as expedition_lineups, list_heroes, milestones as expedition_milestones, pets as expedition_pets, reset_equipment, reset_slot, save_lineup, star_up, stage_definition, stages as expedition_stages, training_slots, unequip_equipment, upgrade_equipment, upgrade_slot
from .expedition_art_pack import ArtPackError, PACK_ROOT, activate_pack, active_pack, asset_file as resolve_expedition_art_asset_file, asset_public as expedition_art_asset_public, hero_assets as expedition_hero_assets, install_preview as install_expedition_art_preview, list_packs as list_expedition_art_packs, preview_bytes as preview_expedition_art_bytes, review_asset as review_expedition_art_asset, rollback_pack
from .teacher_avatar_registry import TeacherAvatarError, import_model as import_teacher_avatar, import_local_koikatsu_card as import_local_koikatsu_teacher_avatar, import_model_batch as import_teacher_avatar_batch, list_models as list_teacher_avatars, list_kks_character_packs, resolve_kks_character_pack, resolve_runtime_pack_asset, resolve_runtime_pack_manifest, resolve_model_file as resolve_teacher_avatar_file, today_model as todays_teacher_avatar, update_morph as update_teacher_avatar_morph
from .teacher_avatar_cards import validate_card as validate_teacher_avatar_card
from .koikatsu_card import KoikatsuCardError, inspect_koikatsu_sunshine_card, rewrite_koikatsu_sunshine_card
from .koikatsu_teacher_registry import KoikatsuTeacherError, default_card as default_koikatsu_card, list_teachers as list_koikatsu_teachers, local_cards as list_local_koikatsu_cards, local_card_resources, resolve_card as resolve_koikatsu_teacher_card, resolve_local_card as resolve_local_koikatsu_card, resolve_local_export_asset, resolve_runtime_asset, resolve_shared_base_asset, resolve_shared_runtime_asset, resolve_web_model_asset, runtime_card_manifest, save_teacher as save_koikatsu_teacher, shared_neutral_template_manifest, validate_runtime_pack_head_contract
from .teacher_avatar_card_presets import list_card_presets as list_teacher_avatar_card_presets
from .teacher_avatar_presets import activate_preset as activate_teacher_avatar_preset, list_presets as list_teacher_avatar_presets, resolve_preview as resolve_teacher_avatar_preset_preview
from .spine_teacher_registry import SpineTeacherError, active_teacher as active_spine_teacher, import_package as import_spine_teacher_package, public_manifest as spine_teacher_manifest, resolve_asset as resolve_spine_teacher_asset
from .active_teaching import router as active_teaching_router
from .generic_knowledge_api import router as generic_knowledge_router
from .code_practice import router as code_practice_router

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRESET_DIR = PROJECT_ROOT / "data" / "presets"
PRESET_SETTINGS_FILE = PROJECT_ROOT / "data" / "preset_settings.json"
_TASK_PROFILE_LOCK = Lock()


def _analyze_task_profile_safe(workspace_id: str, *, force: bool = False) -> dict[str, Any]:
    """Serialize profile writes so React StrictMode/rematch requests cannot race SQLite."""
    with _TASK_PROFILE_LOCK:
        return analyze_task_profile(workspace_id, force=force)

@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    init_orm()
    cleanup_expired_previews()
    yield

app = FastAPI(title="MentorFlow", version="2.0.0", lifespan=lifespan)
app.include_router(active_teaching_router)
app.include_router(generic_knowledge_router)
app.include_router(code_practice_router)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


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
    errors=json.loads(json.dumps(exc.errors(),default=str))
    return JSONResponse(status_code=422,content=app_error_body('VALIDATION_ERROR','请求参数不符合要求',{'errors':errors},getattr(request.state,'request_id',None)))

@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail=exc.detail if isinstance(exc.detail,dict) else {'message':str(exc.detail)}
    error=detail.get('error',{}) if isinstance(detail,dict) else {}
    return JSONResponse(status_code=exc.status_code,content=app_error_body(error.get('code','HTTP_ERROR'),error.get('message',detail.get('message','请求失败')),error.get('details',{}),getattr(request.state,'request_id',None)))

@app.exception_handler(Exception)
async def unknown_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500,content=app_error_body('INTERNAL_ERROR','服务暂时无法完成请求',{},getattr(request.state,'request_id',None)))


def fail(code: str, message: str, status: int = 400, details: dict | None = None):
    raise HTTPException(status_code=status, detail={"error": {"code": code, "message": message, "details": details or {}, "request_id": uuid4().hex}})


def preset_directory(requested: str | None = None) -> Path:
    directory = Path(requested).expanduser() if requested else DEFAULT_PRESET_DIR
    if not requested and PRESET_SETTINGS_FILE.exists():
        try:
            saved = json.loads(PRESET_SETTINGS_FILE.read_text(encoding="utf-8")).get("directory")
            if saved:
                directory = Path(saved).expanduser()
        except (OSError, ValueError, TypeError):
            directory = DEFAULT_PRESET_DIR
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def preset_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name.strip() if ch.isalnum() or ch in " -_().")[:80].strip(" .")
    if not cleaned:
        fail("PRESET_NAME_REQUIRED", "预设名称不能为空")
    return f"{cleaned}.json"


@app.get("/api/presets")
def list_presets(directory: str | None = None):
    target = preset_directory(directory)
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
def save_preset(body: dict[str, Any]):
    name = str(body.get("name") or "").strip()
    filename = preset_filename(name)
    form = body.get("form")
    if not isinstance(form, dict):
        fail("PRESET_FORM_REQUIRED", "预设内容不能为空")
    target = preset_directory(body.get("directory"))
    saved = {"name": name, "saved_at": datetime.now(timezone.utc).isoformat(), "form": form}
    (target / filename).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    PRESET_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESET_SETTINGS_FILE.write_text(json.dumps({"directory": str(target)}, ensure_ascii=False, indent=2), encoding="utf-8")
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
        session = require_provider_session(session_id_from(request, body))
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


async def call_bound_provider_json(session, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    result = await provider_for_session(session).json(messages)
    return result, generation_meta(session, latency_ms=max(1, int((time.perf_counter() - started) * 1000)))


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "MentorFlow", "demo_mode": True, "catalog": catalog_repository.health()}


@app.get("/api/workspaces")
def list_workspaces():
    items = [dict(x) for x in db.rows("workspaces", "status", "active")]
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


@app.post("/api/workspaces")
def create_workspace(body: WorkspaceIn):
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
    with db.connection() as conn: conn.execute("INSERT INTO workspaces (id,title,description,status,character_id,allow_external_knowledge,persona_prompt,execution_mode,provider_name,provider_model,created_at,updated_at) VALUES (:id,:title,:description,:status,:character_id,:allow_external_knowledge,:persona_prompt,:execution_mode,:provider_name,:provider_model,:created_at,:updated_at)", item)
    try:
        from .orm import SessionLocal, WorkspaceORM
        with SessionLocal() as session:
            session.add(WorkspaceORM(id=item["id"],title=item["title"],description=item["description"],character_id=item["character_id"],allow_external_knowledge=bool(item["allow_external_knowledge"]),execution_mode=item["execution_mode"],provider_name=item["provider_name"],provider_model=item["provider_model"])); session.commit()
    except Exception: pass
    return item


@app.get("/api/workspaces/{workspace_id}")
def get_workspace(workspace_id: str):
    ws = require_workspace(workspace_id)
    ws["sources"] = [{**{k:v for k,v in dict(x).items() if k != "text"}, "chunk_count":len(db.rows("chunks","source_id",x["id"]))} for x in db.rows("sources", "workspace_id", workspace_id)]
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
    with db.connection() as conn: conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    try:
        from .orm import SessionLocal, WorkspaceORM
        with SessionLocal() as session:
            row=session.get(WorkspaceORM,workspace_id)
            if row: session.delete(row); session.commit()
    except Exception: pass
    return {"deleted": True}


@app.post("/api/local-sources/preview")
def local_preview(body: LocalSourceIn):
    try: return create_preview(body.path, body.allowed_root)
    except (ValueError, OSError) as exc: fail("LOCAL_PATH_REJECTED", str(exc))


@app.get("/api/workspaces/{workspace_id}/sources")
def list_sources(workspace_id: str):
    require_workspace(workspace_id)
    items=[]
    for row in db.rows("sources", "workspace_id", workspace_id):
        item=dict(row); item.pop("text",None); item["chunk_count"]=len(db.rows("chunks","source_id",item["id"])); items.append(item)
    return items


@app.get("/api/sources/{source_id}")
def get_source(source_id: str):
    source = source_detail(source_id)
    if not source: fail("SOURCE_NOT_FOUND", "资料不存在", 404)
    return source


@app.post("/api/workspaces/{workspace_id}/sources/text")
def add_text_source(workspace_id: str, body: TextSourceIn):
    require_workspace(workspace_id); jid=create_job(workspace_id,"source_import"); set_job(jid,status="running",stage="chunking",progress=50,message="Indexing text")
    try:
        created=create_source(workspace_id, body.title, "text", body.text, trust_level=body.trust_level); result=source_detail(created["id"]); result["job_id"]=jid; set_job(jid,status="succeeded",stage="saving",progress=100,message="Source indexed"); return result
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
    except (ValueError, OSError) as exc: fail("LOCAL_IMPORT_REJECTED", str(exc))
    imported = []
    for item in selected:
        try:
            parts,status,pages=parse_file(Path(item["path"]))
            text='\n\n'.join(x['text'] for x in parts)
            if status=='needs_ocr': imported.append({"title":Path(item["path"]).name,"status":"needs_ocr","parse_error":"当前版本未启用 OCR，不生成假内容"}); continue
            imported.append(source_detail(create_source(workspace_id, Path(item["path"]).name, "local_file", text, original_filename=Path(item["path"]).name, local_path=item["path"], page_count=pages, trust_level=body.trust_level, parts=parts)["id"]))
        except Exception as exc:
            imported.append({"title": Path(item["path"]).name, "status": "error", "parse_error": str(exc)})
    set_job(jid,status="succeeded",stage="saving",progress=100,message="Local sources indexed")
    return {"preview_id":body.preview_id,"job_id":jid,"sources":imported}


@app.post("/api/workspaces/{workspace_id}/sources/upload")
async def upload_source(workspace_id: str, file: UploadFile = File(...)):
    require_workspace(workspace_id)
    jid=create_job(workspace_id,"source_import"); set_job(jid,status="running",stage="validating",progress=10,message="Validating upload")
    suffix = Path(file.filename or "").suffix.lower()
    allowed_mimes={"","application/octet-stream","application/pdf","application/vnd.openxmlformats-officedocument.wordprocessingml.document","text/markdown","text/plain","text/html"}
    if (file.content_type or "") not in allowed_mimes: fail("UNSUPPORTED_MIME", "文件 MIME 类型与扩展名不受支持")
    if suffix not in {".pdf", ".docx", ".md", ".txt", ".html", ".htm"}: fail("UNSUPPORTED_FILE", "只支持 PDF、DOCX、Markdown、TXT 和 HTML")
    dest = Path(__file__).resolve().parents[2] / "data" / "uploads" / f"{uuid4().hex}{suffix}"; dest.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if len(data) > 5_000_000: fail("FILE_TOO_LARGE", "单文件不能超过 5 MB")
    dest.write_bytes(data)
    try:
        parts,status,pages=parse_file(dest)
        if status=='needs_ocr':
            dest.unlink(missing_ok=True)
            set_job(jid,status="succeeded",stage="parsing",progress=100,message="OCR required")
            return {"title":file.filename or dest.name,"status":"needs_ocr","job_id":jid,"parse_error":"当前版本未启用 OCR，不生成假内容"}
        text='\n\n'.join(x['text'] for x in parts)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        set_job(jid,status="failed",stage="parsing",progress=100,error=str(exc),message="Parse failed")
        fail("PARSE_FAILED", f"文件解析失败：{exc}")
    try:
        result=source_detail(create_source(workspace_id, file.filename or dest.name, "upload", text, original_filename=file.filename or "", local_path=str(dest), page_count=pages, parts=parts)["id"]); result["job_id"]=jid; set_job(jid,status="succeeded",stage="saving",progress=100,message="Upload indexed"); return result
    except ValueError as exc:
        dest.unlink(missing_ok=True)
        set_job(jid,status="failed",progress=100,error=str(exc),message="Duplicate source")
        fail("DUPLICATE_SOURCE", str(exc), 409)


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
        return create_archive_preview(workspace_id, filename, bytes(data))
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
        result=source_detail(create_source(workspace_id, body.title or fetched['title'], 'url', fetched['text'], original_url=body.url, trust_level=body.trust_level, final_url=fetched['final_url'], fetched_at=fetched['fetched_at'], content_type=fetched['content_type'], snapshot_hash=fetched['snapshot_hash'])['id']); result['job_id']=jid; set_job(jid,status='succeeded',stage='saving',progress=100,message='URL indexed'); return result
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
    return source_detail(create_source(workspace_id, parsed.netloc, "url", text, original_url=body.url, trust_level=body.trust_level)["id"])


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str):
    source=db.row("sources", "id", source_id)
    if not source: fail("SOURCE_NOT_FOUND", "资料不存在", 404)
    with db.connection() as conn: conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    local_path=source.get("local_path") or ""
    upload_root=(Path(__file__).resolve().parents[2]/"data"/"uploads").resolve()
    if local_path:
        try:
            candidate=Path(local_path).resolve()
            if candidate.is_file() and upload_root in candidate.parents: candidate.unlink()
        except OSError: pass
    try:
        from .orm import SessionLocal, SourceORM
        with SessionLocal() as session:
            row=session.get(SourceORM,source_id)
            if row: session.delete(row); session.commit()
    except Exception: pass
    return {"deleted": True}


@app.post("/api/workspaces/{workspace_id}/goal")
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
            allocated_goal = {**goal["payload"], "minutes_per_day": recommended_minutes}
            set_goal(workspace_id, GoalProfile.model_validate(allocated_goal))
            result["minutes_per_day_applied"] = recommended_minutes
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
    old_tasks={t.get("title"):t for d in previous.get("study_days",[]) for t in d.get("tasks",[]) if t.get("title")}; new_tasks={t.get("title"):t for d in new.get("study_days",[]) for t in d.get("tasks",[]) if t.get("title")}
    old_dates={t.get("title"):d.get("date") for d in previous.get("study_days",[]) for t in d.get("tasks",[]) if t.get("title")}
    new_dates={t.get("title"):d.get("date") for d in new.get("study_days",[]) for t in d.get("tasks",[]) if t.get("title")}
    for key,old_task in old_tasks.items():
        if key in new_tasks and old_task.get("status")=="completed":
            new_tasks[key].update({"status":"completed","completed_at":old_task.get("completed_at"),"actual_minutes":old_task.get("actual_minutes",old_task.get("estimated_minutes",0)),"completion_evidence":old_task.get("completion_evidence","")})
    for task in new_tasks.values():
        sync_task_orm(task)
    diff={"reason":body.get("reason","manual"),"moved_tasks":[{"task":key,"from":old_dates[key],"to":new_dates[key]} for key in old_dates.keys() & new_dates.keys() if old_dates[key]!=new_dates[key]],"added_tasks":[t for i,t in new_tasks.items() if i not in old_tasks],"removed_tasks":[t for i,t in old_tasks.items() if i not in new_tasks],"changed_deadline":previous.get("deadline")!=new.get("deadline"),"changed_capacity":body.get("new_minutes_per_day") is not None,"changed_risk":previous.get("risk_summary")!=new.get("risk_summary")}
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
    return {"plan":new,"changes":diff,"previous_version":previous.get("version")}


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
async def message_session(session_id: str, body: dict[str, Any]):
    item = db.row("sessions", "id", session_id)
    if not item: fail("SESSION_NOT_FOUND", "学习会话不存在", 404)
    session = json.loads(item["payload"]); user_text = str(body.get("message", "")); bound_session = provider_for_workspace(item["workspace_id"], body=body | {"provider_session_id": body.get("provider_session_id") or session.get("provider_session_id")}); attach_workspace_provider(item["workspace_id"], bound_session)
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
            provider_output, meta = await call_bound_provider_json(bound_session, [{"role":"system","content":load_prompt("tutor.md")},{"role":"user","content":json.dumps({"question":user_text,"evidence":result.get("citations",[]),"status":result.get("status"),"scaffold":result.get("scaffold"),"persona_prompt":(require_workspace(item["workspace_id"]).get("persona_prompt") or "")},ensure_ascii=False)}])
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
def create_provider_session_route(body: ProviderSessionCreateIn):
    try:
        return create_provider_session(body.model_dump())
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.get("/api/provider-sessions/{session_id}")
def get_provider_session_route(session_id: str):
    session = get_provider_session(session_id)
    if not session:
        fail("PROVIDER_SESSION_NOT_FOUND", "Provider 会话不存在", 404)
    return public_session(session)


@app.post("/api/provider-sessions/{session_id}/test")
async def test_provider_session_route(session_id: str):
    try:
        return await test_provider_session(session_id)
    except ProviderError as exc:
        fail(exc.code, exc.message, exc.status, exc.details)


@app.delete("/api/provider-sessions/{session_id}")
def delete_provider_session_route(session_id: str):
    return {"deleted": delete_provider_session(session_id)}


@app.post("/api/providers/test")
async def test_provider(body: ProviderTestIn):
    try:
        session = create_provider_session(body.model_dump())
        result = await test_provider_session(session["provider_session_id"])
        return {**result, "message": "Provider 真实连接已验证；API Key 仅保存在当前后端进程内存。"}
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
        return {"request_id": request.state.request_id, **preview_expedition_art_bytes(await file.read(), file.filename or "art-pack.zip")}
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


def _teacher_avatar_error(exc: TeacherAvatarError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _spine_teacher_error(exc: SpineTeacherError) -> None:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


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
        item = import_spine_teacher_package(
            file.filename or "teacher.zip",
            await file.read(),
            display_name=display_name,
            creator=creator,
            spine_editor_license_confirmed=spine_editor_license_confirmed,
            character_rights_confirmed=character_rights_confirmed,
            adult_character_confirmed=adult_character_confirmed,
        )
        return {"request_id": request.state.request_id, "item": item}
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
        return {"filename": file.filename or "character.png", "card": inspect_koikatsu_sunshine_card(await file.read())}
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
        result = rewrite_koikatsu_sunshine_card(await file.read(), body_values=body, face_values=face)
        output_name = f"{Path(file.filename or 'koikatsu-character').stem}-edited.png"
        return Response(content=result, media_type="image/png", headers={"Content-Disposition": f'attachment; filename="{output_name}"'})
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
        content = await file.read()
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
        payload = [(file.filename or "avatar.vrm", await file.read()) for file in files]
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

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"): fail("NOT_FOUND", "API 路由不存在", 404)
        return FileResponse(STATIC / "index.html")
