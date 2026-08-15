"""Run the release-gated mixed-material acceptance against a local MentorFlow API.

This tool accepts only a Credential Locker reference.  It never accepts or
prints a raw Provider key and it does not inspect the keyring itself.
"""

from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
from docx import Document
from PIL import Image, ImageDraw, ImageFont


TERMINAL_JOB_STATES = {"ready", "ready_with_review", "failed", "cancelled"}


def _response(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text[:1000]}
    if response.is_error:
        raise RuntimeError(
            f"HTTP {response.status_code} {response.request.method} "
            f"{response.request.url}: {json.dumps(payload, ensure_ascii=False)}"
        )
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected an object from {response.request.url}")
    return payload


def _fixtures(root: Path, audio_source: Path) -> list[Path]:
    text = root / "acceptance-random-domain.txt"
    text.write_text(
        "VIOLET-731 is a randomly named boundary concept. Its observable "
        "acceptance criterion is to distinguish input, transformation, and output.",
        encoding="utf-8",
    )
    structured = root / "acceptance-pretty.json"
    structured.write_text(
        json.dumps(
            {
                "unfamiliar_domain": {
                    "sentinel": "QUARTZ-391",
                    "rule": "A stage may consume only the output of its declared predecessor.",
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    document_path = root / "acceptance-document.docx"
    document = Document()
    document.add_heading("Copper Finch Protocol", level=1)
    document.add_paragraph(
        "COPPER-FINCH-284 names a fictional workflow used only to verify "
        "cross-domain extraction and document evidence."
    )
    document.save(document_path)
    image_path = root / "acceptance-ocr.png"
    image = Image.new("RGB", (1600, 300), "white")
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 76)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text(
        (40, 90),
        "ACCEPTANCE OCR SENTINEL 482",
        font=font,
        fill="black",
    )
    image.save(image_path)
    audio_path = root / f"acceptance-audio{audio_source.suffix.lower()}"
    audio_path.write_bytes(audio_source.read_bytes())
    return [text, structured, document_path, image_path, audio_path]


def _upload(client: httpx.Client, workspace_id: str, path: Path) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as stream:
        return _response(
            client.post(
                f"/api/active-teaching/workspaces/{workspace_id}/materials",
                files={"file": (path.name, stream, mime)},
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real Provider acceptance for text + pretty JSON + DOCX + OCR + audio."
    )
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--credential-ref", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--audio-file", required=True, type=Path)
    parser.add_argument(
        "--audio-sentinel",
        required=True,
        help="A clearly spoken phrase that must appear in cited audio evidence.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    if not args.credential_ref.startswith("mfcred_"):
        raise ValueError("--credential-ref must be an opaque mfcred_... reference")
    audio_sentinel = " ".join(str(args.audio_sentinel).split()).strip()
    if not audio_sentinel:
        raise ValueError("--audio-sentinel must identify clearly spoken audio content")
    audio_source = args.audio_file.expanduser().resolve(strict=True)
    if audio_source.suffix.lower() not in {".wav", ".mp3", ".m4a", ".ogg", ".webm"}:
        raise ValueError("--audio-file must use a registered audio suffix")
    workspace_id = f"real-acceptance-{time.strftime('%Y%m%d-%H%M%S')}"
    temp_root = Path(os.getenv("MENTORFLOW_ACCEPTANCE_TEMP_ROOT", r"E:\Codex\Temp"))
    temp_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "schema": "mentorflow.real-provider-acceptance.v1",
        "workspace_id": workspace_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": {
            "type": "openai-compatible",
            "base_url_host": httpx.URL(args.base_url).host,
            "model": args.model,
            "credential_ref_supplied": True,
        },
    }
    with tempfile.TemporaryDirectory(prefix="mentorflow-km-v1-", dir=temp_root) as temp:
        fixture_paths = _fixtures(Path(temp), audio_source)
        with httpx.Client(
            base_url=args.server.rstrip("/"),
            timeout=httpx.Timeout(120.0),
        ) as client:
            session = _response(
                client.post(
                    "/api/provider-sessions",
                    json={
                        "provider": "openai-compatible",
                        "base_url": args.base_url,
                        "model": args.model,
                        "credential_ref": args.credential_ref,
                    },
                )
            )
            session_id = str(session["provider_session_id"])
            verified = _response(client.post(f"/api/provider-sessions/{session_id}/test"))
            if not verified.get("verified"):
                raise RuntimeError("Provider session did not become verified")

            materials = [_upload(client, workspace_id, path) for path in fixture_paths]
            material_ids = [str(item["id"]) for item in materials]
            expected_sentinels = {
                material_ids[0]: "VIOLET-731",
                material_ids[1]: "QUARTZ-391",
                material_ids[2]: "COPPER-FINCH-284",
                material_ids[3]: "ACCEPTANCE OCR SENTINEL 482",
            }
            audio_material_id = material_ids[4]
            _response(
                client.put(
                    f"/api/active-teaching/workspaces/{workspace_id}/receipt",
                    json={
                        "learning_goal": "Master every supplied random-domain material with evidence",
                        "current_level": "beginner",
                        "current_foundation": "beginner",
                        "target_level": "complete_mastery",
                        "start_date": date.today().isoformat(),
                        "deadline": (date.today() + timedelta(days=120)).isoformat(),
                        "study_weekdays": list(range(7)),
                        "daily_task_minutes": 720,
                        "preferred_learning_style": "neutral",
                        "discover_external_resources": False,
                        "material_ids": material_ids,
                    },
                )
            )
            job = _response(
                client.post(
                    f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs",
                    json={
                        "provider_session_id": session_id,
                        "material_ids": material_ids,
                    },
                )
            )
            deadline_at = time.monotonic() + max(1, args.timeout_seconds)
            while str(job.get("status") or "") not in TERMINAL_JOB_STATES:
                if time.monotonic() >= deadline_at:
                    raise TimeoutError(f"knowledge-map job {job.get('id')} did not finish")
                time.sleep(2)
                job = _response(
                    client.get(f"/api/active-teaching/knowledge-map/jobs/{job['id']}")
                )
            if job.get("status") != "ready":
                raise RuntimeError(
                    "Real acceptance requires ready with every material included; "
                    + json.dumps(job, ensure_ascii=False)
                )
            dispositions = list(job.get("dispositions") or [])
            if len(dispositions) != len(material_ids) or {
                str(item.get("disposition")) for item in dispositions
            } != {"included"}:
                raise RuntimeError("Not every material received an included disposition")

            graph = _response(
                client.get(
                    f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map",
                    params={"revision": "active"},
                )
            )
            nodes = list(graph.get("nodes") or [])
            edges = list(graph.get("edges") or [])
            cited_material_ids = {
                str(evidence.get("material_id") or "")
                for node in nodes
                if node.get("layer") == "material"
                for evidence in node.get("evidence") or []
            }
            if cited_material_ids != set(material_ids):
                raise RuntimeError("Material evidence coverage is incomplete")
            if any(
                not node.get("evidence")
                for node in nodes
                if node.get("layer") == "material"
            ) or any(
                not edge.get("evidence")
                for edge in edges
                if edge.get("layer") == "material"
            ):
                raise RuntimeError("A material-layer node or edge lacks evidence")
            evidence_by_material: dict[str, list[dict[str, object]]] = {}
            for node in nodes:
                if node.get("layer") != "material":
                    continue
                for evidence in node.get("evidence") or []:
                    evidence_by_material.setdefault(
                        str(evidence.get("material_id") or ""),
                        [],
                    ).append(evidence)
            for material_id, sentinel in expected_sentinels.items():
                quoted = "\n".join(
                    str(item.get("quote") or "")
                    for item in evidence_by_material.get(material_id, [])
                )
                if sentinel.casefold() not in quoted.casefold():
                    raise RuntimeError(
                        f"Sentinel {sentinel} is absent from cited evidence"
                    )
            audio_evidence = evidence_by_material.get(audio_material_id, [])
            audio_quotes = "\n".join(
                str(item.get("quote") or "") for item in audio_evidence
            )
            if audio_sentinel.casefold() not in audio_quotes.casefold():
                raise RuntimeError(
                    "The supplied audio sentinel is absent from cited audio evidence"
                )
            if not any(
                item.get("audio_start_ms") is not None
                and item.get("audio_end_ms") is not None
                and int(item["audio_end_ms"]) > int(item["audio_start_ms"])
                for item in audio_evidence
            ):
                raise RuntimeError(
                    "Audio material lacks a cited transcription time range"
                )

            material_themes = {
                str(node["id"])
                for node in nodes
                if node.get("layer") == "material"
                and node.get("node_type") == "theme"
            }
            material_concepts = {
                str(node["id"])
                for node in nodes
                if node.get("layer") == "material"
                and node.get("node_type") == "concept"
            }
            contains_edges = [
                edge
                for edge in edges
                if edge.get("layer") == "material"
                and edge.get("relation") == "contains"
            ]
            if not material_themes or not material_concepts:
                raise RuntimeError("Map lacks the required material theme/concept layers")
            if {
                str(edge.get("target_id") or "") for edge in contains_edges
            } != material_concepts or any(
                str(edge.get("source_id") or "") not in material_themes
                for edge in contains_edges
            ):
                raise RuntimeError(
                    "Evidence-backed contains relations do not cover every material concept"
                )

            confirmed_contains = 0
            for edge in contains_edges:
                candidates = _response(
                    client.get(
                        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/candidates"
                    )
                ).get("candidates") or []
                candidate = next(
                    (
                        item
                        for item in candidates
                        if item.get("candidate_type") == "edge"
                        and item.get("edge_id") == edge.get("id")
                        and item.get("status") == "pending"
                    ),
                    None,
                )
                if candidate is None:
                    raise RuntimeError(
                        f"No review candidate found for contains edge {edge.get('id')}"
                    )
                _response(
                    client.post(
                        f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map/"
                        f"candidates/{candidate['id']}/decision",
                        json={
                            "decision": "confirmed",
                            "reason": "real-provider mixed-material acceptance",
                        },
                    )
                )
                confirmed_contains += 1

            graph = _response(
                client.get(
                    f"/api/active-teaching/workspaces/{workspace_id}/knowledge-map",
                    params={"revision": "active"},
                )
            )
            confirmed_contains_edges = [
                edge
                for edge in graph.get("edges") or []
                if edge.get("layer") == "material"
                and edge.get("relation") == "contains"
                and edge.get("status") in {"confirmed", "accepted"}
            ]
            if len(confirmed_contains_edges) != len(contains_edges):
                raise RuntimeError("Not every material contains relation was confirmed")

            preview = _response(
                client.post(
                    f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/preview",
                    json={"revision": graph["revision"]},
                )
            )
            if preview.get("activation_blockers"):
                raise RuntimeError("Plan preview has unresolved activation blockers")
            extension = dict(preview.get("extension_draft") or {})
            activated = _response(
                client.post(
                    f"/api/active-teaching/workspaces/{workspace_id}/mastery-plans/{preview['preview_id']}/activate",
                    json={"confirm_extension": True}
                    if extension.get("requires_confirmation")
                    else {},
                )
            )
            if activated.get("status") != "active":
                raise RuntimeError("Plan activation did not return active status")
            activated_plan_id = (
                activated.get("plan_id")
                or activated.get("id")
                or preview.get("preview_id")
            )
            report.update(
                {
                    "status": "passed",
                    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "material_count": len(material_ids),
                    "material_ids": material_ids,
                    "dispositions": dispositions,
                    "map_revision": graph.get("revision"),
                    "node_count": len(nodes),
                    "edge_count": len(edges),
                    "evidence_material_count": len(cited_material_ids),
                    "material_nodes_with_evidence": sum(
                        1
                        for node in nodes
                        if node.get("layer") == "material" and node.get("evidence")
                    ),
                    "material_edges_with_evidence": sum(
                        1
                        for edge in edges
                        if edge.get("layer") == "material" and edge.get("evidence")
                    ),
                    "audio_time_range_verified": True,
                    "confirmed_contains_relation_count": confirmed_contains,
                    "plan_preview_id": preview.get("preview_id"),
                    "activated_plan_id": activated_plan_id,
                    "plan_activation_status": activated.get("status"),
                }
            )

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        report_path = args.report.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
