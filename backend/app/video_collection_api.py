"""Explicit, resumable API for bounded video-course collections.

Metadata/subtitle retrieval happens only after the learner confirms it.  Model
summaries have a second, independent gate and require a verified real Provider.
No endpoint downloads video media or stores Provider credentials/session ids.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, ConfigDict, Field

from .active_teaching import _ingest_text_material
from .core_errors import AppError
from .provider_runtime import provider_for_session, require_provider_session
from .provider_service import ProviderError
from .video_collection import (
    DEFAULT_MANIFEST_PAGE_SIZE,
    DEFAULT_SUMMARY_BATCH_SIZE,
    HARD_MAX_PARALLEL,
    MAX_EPISODES,
    MAX_MANIFEST_PAGE_SIZE,
    JobStatus,
    VideoCollectionError,
    VideoCollectionService,
    get_video_collection_service,
)
from .video_transcript import (
    VideoTranscriptError,
    classify_video_url,
    enumerate_video_collection_page,
    extract_video_episode_subtitle,
    source_family_id,
    source_lineage,
)


router = APIRouter(prefix="/api/active-teaching/workspaces/{workspace_id}/video-collections", tags=["video-collections"])


class CollectionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_url: str = Field(min_length=1, max_length=2400)
    user_confirmed_retrieval: bool = False
    max_episodes: int = Field(default=MAX_EPISODES, ge=1, le=MAX_EPISODES)
    page_size: int = Field(default=DEFAULT_MANIFEST_PAGE_SIZE, ge=1, le=MAX_MANIFEST_PAGE_SIZE)
    requested_languages: list[str] = Field(default_factory=lambda: ["zh.*", "en.*"], max_length=8)
    allow_automatic: bool = True
    idempotency_key: str = Field(default="", max_length=160)


class TranscriptStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_confirmed_retrieval: bool = False
    requested_languages: list[str] = Field(default_factory=lambda: ["zh.*", "en.*"], max_length=8)
    allow_automatic: bool = True
    max_parallel: int = Field(default=1, ge=1, le=HARD_MAX_PARALLEL)
    idempotency_key: str = Field(default="", max_length=160)


class SummaryStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_confirmed_summary: bool = False
    provider_session_id: str = Field(default="", max_length=160)
    batch_size: int = Field(default=DEFAULT_SUMMARY_BATCH_SIZE, ge=10, le=20)
    idempotency_key: str = Field(default="", max_length=160)


class ResumeJobIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider_session_id: str = Field(default="", max_length=160)


class ActivateMaterialIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_confirmed_activation: bool = False
    role: str = Field(default="anchor", pattern=r"^(anchor|support)$")


def _service() -> VideoCollectionService:
    return get_video_collection_service()


def _service_error(exc: VideoCollectionError) -> AppError:
    return AppError(exc.code, exc.message, exc.status)


def _require_collection(service: VideoCollectionService, workspace_id: str, collection_id: str) -> dict[str, Any]:
    collection = service.get_collection(collection_id, workspace_id=workspace_id)
    if collection is None:
        raise AppError("VIDEO_COLLECTION_NOT_FOUND", "长课程不存在", 404)
    return collection


def _failure_payload(exc: BaseException, default_code: str) -> dict[str, Any]:
    code = str(getattr(exc, "code", "") or default_code)[:80]
    return {"code": code, "retryable": True, "type": type(exc).__name__[:80]}


async def _fail_job(service: VideoCollectionService, job_id: str, exc: BaseException, default_code: str) -> None:
    job = service.get_job(job_id)
    if not job or job["status"] in {"cancelled", "completed", "completed_with_failures"}:
        return
    try:
        service.update_job_projection(
            job_id,
            status="failed",
            failure=_failure_payload(exc, default_code),
        )
    except VideoCollectionError:
        pass


async def _run_enumeration_job(job_id: str, collection_id: str) -> None:
    service = _service()
    job = service.get_job(job_id, collection_id=collection_id)
    if not job or job["status"] in {"paused", "cancelled", "completed", "completed_with_failures"}:
        return
    request = dict(job.get("request") or {})
    source_url = str(request.get("source_url") or "")
    page_size = int(request.get("page_size") or DEFAULT_MANIFEST_PAGE_SIZE)
    max_episodes = int(request.get("max_episodes") or MAX_EPISODES)
    try:
        if job["status"] == "queued":
            service.update_job_projection(job_id, status="running")
        entries: list[dict[str, Any]] = []
        offset = 0
        reported_count: int | None = None
        title = ""
        while offset < max_episodes:
            current = service.get_job(job_id)
            if not current or current["status"] in {"paused", "cancel_requested", "cancelled"}:
                return
            page = await enumerate_video_collection_page(
                source_url,
                offset=offset,
                page_size=min(page_size, max_episodes - offset),
            )
            page_items = list(page.get("items") or [])
            entries.extend(page_items)
            reported_count = page.get("reported_count") if page.get("reported_count") is not None else reported_count
            title = str(page.get("title") or title)
            offset += len(page_items)
            service.update_job_projection(
                job_id,
                progress={
                    "phase": "enumerating",
                    "enumerated": len(entries),
                    "limit": max_episodes,
                    "reported_count": reported_count,
                },
                checkpoint={"next_offset": offset, "restart_from_zero": True},
            )
            if not page.get("has_more") or not page_items:
                break
        if not entries:
            raise VideoTranscriptError("VIDEO_COLLECTION_EMPTY", "课程目录中没有可用剧集", status=422)
        if len(entries) >= max_episodes and reported_count and reported_count > max_episodes:
            partial_warning = "requested_episode_limit_reached"
        else:
            partial_warning = ""
        if title:
            service.update_collection_title(collection_id, title)
        manifest = service.upsert_manifest(
            collection_id,
            entries,
            reported_count=reported_count,
        )
        service.update_job_projection(
            job_id,
            status="completed" if not partial_warning else "completed_with_failures",
            progress={
                "phase": "manifest_ready",
                "enumerated": manifest["enumerated_count"],
                "selected": manifest["selected_count"],
                "reported_count": manifest.get("reported_count"),
                "warning": partial_warning or None,
            },
            result={
                "collection_id": collection_id,
                "manifest_revision": manifest["manifest_revision"],
                "enumerated_count": manifest["enumerated_count"],
                "title": title,
                "partial": bool(partial_warning),
            },
            failure={"code": partial_warning, "retryable": False} if partial_warning else {},
        )
    except BaseException as exc:
        await _fail_job(service, job_id, exc, "VIDEO_COLLECTION_ENUMERATION_FAILED")


async def _process_transcript_episode(
    service: VideoCollectionService,
    job_id: str,
    episode: Mapping[str, Any],
    requested_languages: list[str],
    allow_automatic: bool,
) -> None:
    episode_id = str(episode.get("id") or "")
    try:
        transcript = await extract_video_episode_subtitle(
            str(episode.get("canonical_url") or ""),
            requested_languages=requested_languages,
            allow_automatic=allow_automatic,
        )
        segments = list(transcript.get("segments") or [])
        quality = dict(transcript.get("quality") or {})
        track = dict(transcript.get("track") or {}) if transcript.get("track") else {}
        if not segments:
            service.checkpoint_episode(
                job_id,
                episode_id,
                {"quality": quality, "track": track},
                status="needs_review",
                failure={"code": str(quality.get("reason") or "SUBTITLE_UNAVAILABLE"), "retryable": True},
            )
            return
        stored = service.insert_segments(episode_id, segments, job_id=job_id)
        target = "ready" if quality.get("state") == "ready" else "needs_review"
        service.checkpoint_episode(
            job_id,
            episode_id,
            {
                "quality": quality,
                "track": track,
                "segment_count": stored["segment_count"],
                "subtitle_bytes": stored["subtitle_bytes"],
            },
            status=target,
            failure={} if target == "ready" else {"code": str(quality.get("reason") or "SUBTITLE_NEEDS_REVIEW"), "retryable": True},
        )
    except BaseException as exc:
        service.checkpoint_episode(
            job_id,
            episode_id,
            {},
            status="failed",
            failure=_failure_payload(exc, "VIDEO_EPISODE_TRANSCRIPT_FAILED"),
        )


async def _run_transcript_job(job_id: str, collection_id: str) -> None:
    service = _service()
    job = service.get_job(job_id, collection_id=collection_id)
    if not job or job["status"] in {"paused", "cancelled", "completed", "completed_with_failures"}:
        return
    request = dict(job.get("request") or {})
    languages = [str(item) for item in list(request.get("requested_languages") or ["zh.*", "en.*"])][:8]
    allow_automatic = bool(request.get("allow_automatic", True))
    max_parallel = max(1, min(HARD_MAX_PARALLEL, int(request.get("max_parallel") or 1)))
    try:
        if job["status"] == "queued":
            service.update_job_projection(job_id, status="running")
        while True:
            current = service.get_job(job_id)
            if not current or current["status"] in {"paused", "cancel_requested", "cancelled"}:
                return
            claimed: list[dict[str, Any]] = []
            for index in range(max_parallel):
                item = service.claim_next_episode(job_id, worker_id=f"{job_id}:{index}")
                if item:
                    claimed.append(item)
            if not claimed:
                break
            await asyncio.gather(
                *[
                    _process_transcript_episode(service, job_id, item, languages, allow_automatic)
                    for item in claimed
                ]
            )
        service.finalize_job(job_id)
    except BaseException as exc:
        await _fail_job(service, job_id, exc, "VIDEO_COLLECTION_TRANSCRIPT_FAILED")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


async def _provider_summary(provider: Any, *, scope: str, inputs: list[dict[str, Any]], coverage: Mapping[str, Any]) -> dict[str, Any]:
    prompt_payload = json.dumps(
        {"scope": scope, "inputs": inputs, "coverage": dict(coverage)},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(prompt_payload) > 12_000:
        raise ValueError("bounded summary prompt exceeded 12000 characters")
    response = await provider.json(
        [
            {
                "role": "system",
                "content": (
                    "你负责生成长课程分层摘要。只能使用输入中的已验证内容；不得补写未处理剧集，"
                    "不得声称全课程完成。返回 JSON：summary、key_points（数组）、warnings（数组）。"
                ),
            },
            {
                "role": "user",
                "content": prompt_payload,
            },
        ]
    )
    if not isinstance(response, Mapping):
        raise ValueError("provider summary response is not an object")
    summary = str(response.get("summary") or response.get("text") or "").strip()[:20000]
    if not summary:
        raise ValueError("provider summary is empty")
    return {
        "summary": summary,
        "key_points": [str(item)[:400] for item in list(response.get("key_points") or [])[:24]],
        "warnings": [str(item)[:240] for item in list(response.get("warnings") or [])[:16]],
    }


def _summary_prompt_item(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(item.get("summary_json") or {})
    return {
        "summary_id": str(item.get("id") or item.get("summary_id") or "")[:96],
        "summary": str(item.get("summary") or item.get("summary_text") or "")[:900],
        "key_points": [str(value)[:100] for value in list(item.get("key_points") or payload.get("key_points") or [])[:2]],
    }


async def _reduce_provider_summaries(
    provider: Any,
    *,
    scope: str,
    items: list[Mapping[str, Any]],
    coverage: Mapping[str, Any],
    fanout: int = 8,
) -> dict[str, Any]:
    """Reduce every child summary through a bounded tree; never truncate tails."""

    if not items:
        raise ValueError("no summary inputs")
    current = [_summary_prompt_item(item) for item in items]
    while len(current) > fanout:
        reduced: list[dict[str, Any]] = []
        for offset in range(0, len(current), fanout):
            result = await _provider_summary(
                provider,
                scope=f"{scope}_reduction",
                inputs=current[offset:offset + fanout],
                coverage=coverage,
            )
            reduced.append(result)
        current = [_summary_prompt_item(item) for item in reduced]
    return await _provider_summary(provider, scope=scope, inputs=current, coverage=coverage)


def _sample_segment_evidence(segments: list[Mapping[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    if len(segments) <= limit:
        selected = segments
    else:
        indexes = sorted({round(index * (len(segments) - 1) / (limit - 1)) for index in range(limit)})
        selected = [segments[index] for index in indexes]
    return [
        {"segment_id": item["id"], "start_ms": item["start_ms"], "end_ms": item["end_ms"], "quote_hash": item["quote_hash"]}
        for item in selected
    ]


async def _summarize_episode(
    service: VideoCollectionService,
    provider: Any,
    provider_fingerprint: str,
    collection_id: str,
    episode: Mapping[str, Any],
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    segments = service.episode_segments_for_summary(collection_id, str(episode["id"]))
    if not segments:
        raise ValueError("episode has no verified subtitle segments")
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_size = 0
    for segment in segments:
        compact = {
            "segment_id": segment["id"],
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
            "text": segment["text"],
        }
        size = len(json.dumps(compact, ensure_ascii=False))
        if current and current_size + size > 5000:
            chunks.append({"segments": current})
            current, current_size = [], 0
        current.append(compact)
        current_size += size
    if current:
        chunks.append({"segments": current})
    chunk_summaries = [await _provider_summary(provider, scope="episode_chunk", inputs=[chunk], coverage=coverage) for chunk in chunks]
    result = chunk_summaries[0] if len(chunk_summaries) == 1 else await _reduce_provider_summaries(
        provider,
        scope="episode",
        items=chunk_summaries,
        coverage=coverage,
    )
    evidence = _sample_segment_evidence(segments)
    input_fp = _fingerprint([{"id": item["id"], "quote_hash": item["quote_hash"]} for item in segments])
    return service.store_summary(
        collection_id,
        scope_type="episode",
        scope_id=str(episode["id"]),
        episode_id=str(episode["id"]),
        input_fingerprint=input_fp,
        provider_fingerprint=provider_fingerprint,
        summary_text=result["summary"],
        summary_json={"key_points": result["key_points"], "warnings": result["warnings"]},
        evidence=evidence,
    )


async def _run_summary_job(
    job_id: str,
    collection_id: str,
    provider_session_id: str,
    provider_fingerprint: str,
    batch_size: int,
) -> None:
    service = _service()
    try:
        session = require_provider_session(provider_session_id, verified=True)
        if session.provider == "fake":
            raise ProviderError("PROVIDER_VERIFICATION_REQUIRED", "长课程摘要需要已验证的真实 Provider", 428)
        provider = provider_for_session(session)
        job = service.get_job(job_id, collection_id=collection_id)
        if not job or job["status"] in {"paused", "cancelled", "completed", "completed_with_failures"}:
            return
        if job["status"] == "queued":
            service.update_job_projection(job_id, status="running")
        coverage = service.coverage(collection_id, job_id=job_id)
        episodes = service.selected_episodes(collection_id, status="ready")
        episode_summaries: list[dict[str, Any]] = []
        failures: list[str] = []
        for index, episode in enumerate(episodes):
            current = service.get_job(job_id)
            if not current or current["status"] in {"paused", "cancel_requested", "cancelled"}:
                return
            try:
                episode_summaries.append(
                    await _summarize_episode(service, provider, provider_fingerprint, collection_id, episode, coverage)
                )
            except BaseException:
                failures.append(str(episode["id"]))
            service.update_job_projection(
                job_id,
                progress={
                    "phase": "summarizing_episode",
                    "completed": index + 1,
                    "total_ready": len(episodes),
                    "failed_episode_ids": failures[:100],
                },
            )
        batch_summaries: list[dict[str, Any]] = []
        for batch_index in range(0, len(episode_summaries), batch_size):
            group = episode_summaries[batch_index:batch_index + batch_size]
            result = await _reduce_provider_summaries(provider, scope="batch", items=group, coverage=coverage)
            batch_id = f"level-1-batch-{batch_index // batch_size + 1}"
            batch_summaries.append(
                service.store_summary(
                    collection_id,
                    scope_type="batch",
                    scope_id=batch_id,
                    batch_key=batch_id,
                    input_fingerprint=_fingerprint([item["input_fingerprint"] for item in group]),
                    provider_fingerprint=provider_fingerprint,
                    summary_text=result["summary"],
                    summary_json={"key_points": result["key_points"], "warnings": result["warnings"]},
                    evidence=[{"summary_id": item["id"]} for item in group],
                )
            )
        if not batch_summaries:
            raise ValueError("no episode summaries were generated")
        summary_level = 1
        while len(batch_summaries) > batch_size:
            next_level: list[dict[str, Any]] = []
            for group_index, offset in enumerate(range(0, len(batch_summaries), batch_size), start=1):
                group = batch_summaries[offset:offset + batch_size]
                result = await _reduce_provider_summaries(provider, scope="batch", items=group, coverage=coverage)
                batch_id = f"level-{summary_level + 1}-batch-{group_index}"
                next_level.append(
                    service.store_summary(
                        collection_id,
                        scope_type="batch",
                        scope_id=batch_id,
                        batch_key=batch_id,
                        input_fingerprint=_fingerprint([item["input_fingerprint"] for item in group]),
                        provider_fingerprint=provider_fingerprint,
                        summary_text=result["summary"],
                        summary_json={"key_points": result["key_points"], "warnings": result["warnings"], "hierarchy_level": summary_level + 1},
                        evidence=[{"summary_id": item["id"]} for item in group],
                    )
                )
            batch_summaries = next_level
            summary_level += 1
        final = await _reduce_provider_summaries(provider, scope="collection", items=batch_summaries, coverage=coverage)
        collection_summary = service.store_summary(
            collection_id,
            scope_type="collection",
            scope_id=collection_id,
            input_fingerprint=_fingerprint([item["input_fingerprint"] for item in batch_summaries]),
            provider_fingerprint=provider_fingerprint,
            summary_text=final["summary"],
            summary_json={"key_points": final["key_points"], "warnings": final["warnings"], "coverage": coverage},
            evidence=[{"summary_id": item["id"]} for item in batch_summaries],
        )
        partial = bool(failures or coverage.get("complete") is not True)
        service.update_job_projection(
            job_id,
            status="completed_with_failures" if partial else "completed",
            progress={
                "phase": "summary_ready",
                "episode_summaries": len(episode_summaries),
                "final_level_summaries": len(batch_summaries),
                "summary_hierarchy_levels": summary_level,
                "failed_episode_ids": failures[:100],
                "coverage": coverage,
            },
            result={"collection_summary_id": collection_summary["id"], "partial": partial},
            failure={"code": "PARTIAL_SUMMARY_COVERAGE", "retryable": True} if partial else {},
        )
    except BaseException as exc:
        await _fail_job(service, job_id, exc, "VIDEO_COLLECTION_SUMMARY_FAILED")


def _schedule_job(background: BackgroundTasks, job: Mapping[str, Any], *, provider_session_id: str = "") -> None:
    request = dict(job.get("request") or {})
    job_type = str(job.get("job_type") or "")
    if job_type == "enumerate":
        background.add_task(_run_enumeration_job, str(job["id"]), str(job["collection_id"]))
    elif job_type == "transcribe":
        background.add_task(_run_transcript_job, str(job["id"]), str(job["collection_id"]))
    elif job_type == "summarize":
        if not provider_session_id:
            raise AppError("PROVIDER_SESSION_REQUIRED", "继续摘要需要重新验证 Provider", 428)
        session = require_provider_session(provider_session_id, verified=True)
        if session.provider == "fake":
            raise AppError("PROVIDER_VERIFICATION_REQUIRED", "长课程摘要需要已验证的真实 Provider", 428)
        provider_fp = _fingerprint(
            {
                "provider": session.provider,
                "host": urlparse(session.base_url).hostname or "",
                "model": session.model_reported or session.model,
            }
        )
        background.add_task(
            _run_summary_job,
            str(job["id"]),
            str(job["collection_id"]),
            provider_session_id,
            provider_fp,
            int(request.get("summary_batch_size") or DEFAULT_SUMMARY_BATCH_SIZE),
        )


@router.post("", status_code=202)
async def create_video_collection(workspace_id: str, body: CollectionCreateIn, background: BackgroundTasks) -> dict[str, Any]:
    if not body.user_confirmed_retrieval:
        raise AppError("VIDEO_RETRIEVAL_CONFIRMATION_REQUIRED", "请先确认读取该课程目录", 409)
    capability = classify_video_url(body.source_url)
    if not capability.get("recognized"):
        raise AppError("VIDEO_URL_UNSUPPORTED", "该链接不是可识别的视频或课程地址", 415)
    service = _service()
    try:
        collection = service.create_collection(
            workspace_id=workspace_id,
            provider=str(capability.get("provider") or capability.get("hostname") or "video"),
            canonical_url=body.source_url,
            title="视频长课程",
            idempotency_key=body.idempotency_key or None,
            metadata={
                "retrieval_authorized": True,
                "max_episodes": body.max_episodes,
                "requested_languages": body.requested_languages,
                "allow_automatic": body.allow_automatic,
            },
        )
        job = service.create_job(
            collection["id"],
            job_type="enumerate",
            idempotency_key=(body.idempotency_key + ":enumerate") if body.idempotency_key else None,
            request={
                "source_url": collection["canonical_url"],
                "retrieval_authorized": True,
                "max_episodes": body.max_episodes,
                "page_size": body.page_size,
                "requested_languages": body.requested_languages,
                "allow_automatic": body.allow_automatic,
            },
        )
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc
    if job["status"] in {"queued", "failed"}:
        if job["status"] == "failed":
            job = service.retry_job(job["id"])
        _schedule_job(background, job)
    return {"schema": "mentorflow.video-collection.v1", "collection": collection, "job": job}


@router.get("/{collection_id}")
async def get_video_collection(workspace_id: str, collection_id: str) -> dict[str, Any]:
    service = _service()
    collection = _require_collection(service, workspace_id, collection_id)
    try:
        return {
            "schema": "mentorflow.video-collection.v1",
            "collection": collection,
            "coverage": service.coverage(collection_id),
            "jobs": service.job_page(collection_id, limit=10)["items"],
        }
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc


@router.get("/{collection_id}/manifest")
async def get_video_collection_manifest(
    workspace_id: str,
    collection_id: str,
    cursor: str | None = Query(default=None, max_length=1000),
    limit: int = Query(default=DEFAULT_MANIFEST_PAGE_SIZE, ge=1, le=MAX_MANIFEST_PAGE_SIZE),
) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    try:
        return service.manifest_page(collection_id, page_size=limit, cursor=cursor)
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc


@router.get("/{collection_id}/episodes/{episode_id}")
async def get_video_collection_episode(
    workspace_id: str,
    collection_id: str,
    episode_id: str,
    segment_offset: int = Query(default=0, ge=0),
    segment_limit: int = Query(default=8, ge=1, le=100),
) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    try:
        return service.episode_detail(collection_id, episode_id, segment_offset=segment_offset, segment_limit=segment_limit)
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc


@router.post("/{collection_id}/jobs/transcribe", status_code=202)
async def start_video_collection_transcript(
    workspace_id: str,
    collection_id: str,
    body: TranscriptStartIn,
    background: BackgroundTasks,
) -> dict[str, Any]:
    if not body.user_confirmed_retrieval:
        raise AppError("VIDEO_RETRIEVAL_CONFIRMATION_REQUIRED", "请先确认读取逐集字幕", 409)
    service = _service()
    collection = _require_collection(service, workspace_id, collection_id)
    if int(collection.get("enumerated_count") or 0) <= 0:
        raise AppError("VIDEO_COLLECTION_MANIFEST_REQUIRED", "请先完成课程目录读取", 409)
    try:
        job = service.create_job(
            collection_id,
            job_type="transcribe",
            idempotency_key=body.idempotency_key or None,
            max_parallel=body.max_parallel,
            request={
                "retrieval_authorized": True,
                "manifest_revision": int(collection.get("manifest_revision") or 0),
                "requested_languages": body.requested_languages,
                "allow_automatic": body.allow_automatic,
                "max_parallel": body.max_parallel,
            },
        )
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc
    if job["status"] == "queued":
        _schedule_job(background, job)
    return {"collection_id": collection_id, "job": job}


@router.post("/{collection_id}/jobs/summarize", status_code=202)
async def start_video_collection_summary(
    workspace_id: str,
    collection_id: str,
    body: SummaryStartIn,
    background: BackgroundTasks,
) -> dict[str, Any]:
    if not body.user_confirmed_summary:
        raise AppError("VIDEO_SUMMARY_CONFIRMATION_REQUIRED", "请先确认使用 Provider 生成分层摘要", 409)
    session = require_provider_session(body.provider_session_id, verified=True)
    if session.provider == "fake":
        raise AppError("PROVIDER_VERIFICATION_REQUIRED", "长课程摘要需要已验证的真实 Provider", 428)
    service = _service()
    collection = _require_collection(service, workspace_id, collection_id)
    provider_fp = _fingerprint(
        {
            "provider": session.provider,
            "host": urlparse(session.base_url).hostname or "",
            "model": session.model_reported or session.model,
        }
    )
    try:
        job = service.create_job(
            collection_id,
            job_type="summarize",
            idempotency_key=body.idempotency_key or f"summary:r{int(collection.get('manifest_revision') or 0)}:{provider_fp}",
            summary_batch_size=body.batch_size,
            request={
                "summary_authorized": True,
                "summary_batch_size": body.batch_size,
                "manifest_revision": int(collection.get("manifest_revision") or 0),
            },
        )
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc
    if job["status"] == "queued":
        _schedule_job(background, job, provider_session_id=body.provider_session_id)
    return {"collection_id": collection_id, "job": job}


@router.get("/{collection_id}/jobs/{job_id}")
async def get_video_collection_job(workspace_id: str, collection_id: str, job_id: str) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    job = service.get_job(job_id, collection_id=collection_id)
    if job is None:
        raise AppError("VIDEO_COLLECTION_JOB_NOT_FOUND", "长课程任务不存在", 404)
    return job


@router.post("/{collection_id}/jobs/{job_id}/pause")
async def pause_video_collection_job(workspace_id: str, collection_id: str, job_id: str) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    try:
        job = service.get_job(job_id, collection_id=collection_id)
        if job is None:
            raise AppError("VIDEO_COLLECTION_JOB_NOT_FOUND", "长课程任务不存在", 404)
        return service.pause_job(job_id)
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc


@router.post("/{collection_id}/jobs/{job_id}/cancel")
async def cancel_video_collection_job(workspace_id: str, collection_id: str, job_id: str) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    try:
        job = service.get_job(job_id, collection_id=collection_id)
        if job is None:
            raise AppError("VIDEO_COLLECTION_JOB_NOT_FOUND", "长课程任务不存在", 404)
        return service.cancel_job(job_id)
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc


@router.post("/{collection_id}/jobs/{job_id}/resume", status_code=202)
async def resume_video_collection_job(
    workspace_id: str,
    collection_id: str,
    job_id: str,
    body: ResumeJobIn,
    background: BackgroundTasks,
) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    try:
        existing = service.get_job(job_id, collection_id=collection_id)
        if existing is None:
            raise AppError("VIDEO_COLLECTION_JOB_NOT_FOUND", "长课程任务不存在", 404)
        job = service.resume_job(job_id)
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc
    _schedule_job(background, job, provider_session_id=body.provider_session_id)
    return job


@router.post("/{collection_id}/jobs/{job_id}/retry", status_code=202)
async def retry_video_collection_job(
    workspace_id: str,
    collection_id: str,
    job_id: str,
    body: ResumeJobIn,
    background: BackgroundTasks,
) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    try:
        existing = service.get_job(job_id, collection_id=collection_id)
        if existing is None:
            raise AppError("VIDEO_COLLECTION_JOB_NOT_FOUND", "长课程任务不存在", 404)
        job = service.retry_job(job_id)
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc
    _schedule_job(background, job, provider_session_id=body.provider_session_id)
    return job


@router.get("/{collection_id}/summaries")
async def get_video_collection_summaries(
    workspace_id: str,
    collection_id: str,
    scope_type: str | None = Query(default=None, pattern=r"^(episode|batch|collection)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    service = _service()
    _require_collection(service, workspace_id, collection_id)
    try:
        return service.summary_page(collection_id, scope_type=scope_type, offset=offset, limit=limit)
    except VideoCollectionError as exc:
        raise _service_error(exc) from exc


@router.post("/{collection_id}/activate-material")
async def activate_video_collection_material(
    workspace_id: str,
    collection_id: str,
    body: ActivateMaterialIn,
) -> dict[str, Any]:
    if not body.user_confirmed_activation:
        raise AppError("VIDEO_COLLECTION_ACTIVATION_CONFIRMATION_REQUIRED", "请先确认把课程摘要加入学习资料", 409)
    service = _service()
    collection = _require_collection(service, workspace_id, collection_id)
    summaries = service.summary_page(collection_id, scope_type="collection", limit=1)
    if not summaries["items"]:
        raise AppError("VIDEO_COLLECTION_SUMMARY_REQUIRED", "请先生成课程级摘要", 409)
    summary = summaries["items"][-1]
    coverage = service.coverage(collection_id)
    key_points = [str(item) for item in list((summary.get("summary_json") or {}).get("key_points") or [])]
    text = str(summary.get("summary_text") or "").strip()
    if key_points:
        text += "\n\n关键点：\n" + "\n".join(f"- {item}" for item in key_points)
    family = source_family_id(str(collection.get("canonical_url") or ""))
    material = await _ingest_text_material(
        workspace_id,
        text=text,
        filename=f"{str(collection.get('title') or '视频长课程')[:120]}-分层摘要.txt",
        source_url=str(collection.get("canonical_url") or ""),
        source_kind="video_collection_summary",
        provenance={
            "source_family_id": family,
            "collection_id": collection_id,
            "summary_id": summary["id"],
            "material_role": body.role,
            "coverage": coverage,
            "lineage": source_lineage(
                family,
                role="collection_anchor" if body.role == "anchor" else "collection_support",
                relation="hierarchical_summary",
                independent=False,
                parent_url=str(collection.get("canonical_url") or ""),
            ),
            "independent_evidence": False,
            "independence": "provider_summary_with_timestamped_evidence",
        },
    )
    return {"collection_id": collection_id, "role": body.role, "material": material, "coverage": coverage}


def recover_interrupted_video_collection_jobs() -> list[dict[str, Any]]:
    """Mark interrupted work resumable; never auto-reissue network requests."""

    return _service().recover_interrupted()
