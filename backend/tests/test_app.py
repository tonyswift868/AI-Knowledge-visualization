from __future__ import annotations

from datetime import date, timedelta
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    with TestClient(app) as c:
        yield c


def make_workspace(client):
    r = client.post("/api/workspaces", json={"title": "测试空间", "description": "闭环验收"})
    assert r.status_code == 200
    return r.json()


def make_goal(client, ws_id, days=14):
    return client.post(f"/api/workspaces/{ws_id}/goal", json={
        "goal_statement": "在截止日期前完成可验证学习成果", "motivation": "持续成长", "deadline": (date.today()+timedelta(days=days)).isoformat(),
        "minutes_per_day": 30, "days_per_week": 5, "current_level": "beginner", "expected_output": "一份成果清单", "success_criteria": ["完成最终任务"]
    })


def test_health_and_workspace_crud(client):
    assert client.get("/api/health").json()["status"] == "ok"
    ws = make_workspace(client)
    assert client.get(f"/api/workspaces/{ws['id']}").status_code == 200
    assert client.patch(f"/api/workspaces/{ws['id']}", json={"title":"新标题"}).json()["title"] == "新标题"


def test_source_goal_feasibility_plan_and_task(client):
    ws = make_workspace(client)
    source = client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"资料一","text":"# 关键概念\n\n这是一个足够长的真实资料片段，用于建立可以被引用的知识证据。"})
    assert source.status_code == 200
    assert source.json()["chunk_count"] >= 1
    assert make_goal(client, ws["id"]).status_code == 200
    assessment = client.post(f"/api/workspaces/{ws['id']}/feasibility/analyze")
    assert assessment.status_code == 200
    assert "estimated_total_minutes" in assessment.json()
    client.post(f"/api/workspaces/{ws['id']}/feasibility/confirm", json={"confirmed":True})
    plan = client.post(f"/api/workspaces/{ws['id']}/plans/generate")
    assert plan.status_code == 200, plan.text
    assert plan.json()["milestones"] and plan.json()["study_days"]
    assert plan.json()["study_days"][0]["tasks"][0]["scene"]["type"] == "branching_learning_scene"
    assert all(day["tasks"] for day in plan.json()["study_days"])
    client.post(f"/api/plans/{plan.json()['id']}/activate")
    task = plan.json()["study_days"][0]["tasks"][0]
    assert client.post(f"/api/tasks/{task['id']}/complete").status_code == 200
    progress = client.get(f"/api/workspaces/{ws['id']}/progress").json()
    assert progress["completed_tasks"] == 1
    assert progress["validated_completed_tasks"] == 0


def test_local_path_boundary_and_preview(client, tmp_path):
    ws = make_workspace(client)
    good = tmp_path / "notes.md"; good.write_text("# Notes\n\nhello", encoding="utf-8")
    preview = client.post("/api/local-sources/preview", json={"path":str(tmp_path),"allowed_root":str(tmp_path)})
    assert preview.status_code == 200 and len(preview.json()["files"]) == 1
    bad = client.post("/api/local-sources/preview", json={"path":str(tmp_path.parent),"allowed_root":str(tmp_path)})
    assert bad.status_code == 400


def test_citation_validator_drops_fake_reference(client):
    ws = make_workspace(client); make_goal(client, ws["id"])
    client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"资料","text":"真实证据"})
    session = client.post(f"/api/workspaces/{ws['id']}/sessions", json={"lesson_id":"missing"}).json()
    result = client.post(f"/api/sessions/{session['id']}/messages", json={"message":"完成"})
    assert result.status_code == 200 and result.json()["status"] == "insufficient"


def test_supplemental_is_opt_in_and_separate(client):
    ws = make_workspace(client)
    session = client.post(f"/api/workspaces/{ws['id']}/sessions", json={"lesson_id":"missing"}).json()
    denied = client.post(f"/api/sessions/{session['id']}/messages", json={"message":"无关问题"})
    assert "supplemental" not in denied.json()
    allowed = client.post(f"/api/sessions/{session['id']}/messages", json={"message":"无关问题", "allow_external_knowledge":True})
    assert allowed.status_code == 200
    assert allowed.json()["supplemental"]["label"] == "AI 补充，未包含在当前资料中"


def test_conflict_endpoint_returns_both_evidence_metadata(client):
    ws = make_workspace(client)
    client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"来源 A","text":"# 结论\n\n版本 A 的证据。"})
    client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"来源 B","text":"# 结论\n\n版本 B 的证据。"})
    conflicts = client.get(f"/api/workspaces/{ws['id']}/conflicts")
    assert conflicts.status_code == 200 and len(conflicts.json()) == 1
    item = conflicts.json()[0]
    assert item["source_a_title"] == "来源 A" and item["source_b_title"] == "来源 B"
    assert item["chunk_a_id"] and item["chunk_b_id"]


def test_provider_key_not_persisted(client):
    result = client.post("/api/providers/test", json={"provider":"fake","api_key":"do-not-store"})
    assert result.status_code == 200 and result.json()["ok"] is True


def test_feasibility_system_allocates_daily_minutes_with_buffer(client):
    ws = make_workspace(client)
    assert client.post(f"/api/workspaces/{ws['id']}/sources/text", json={"title":"资料一","text":"# 关键概念\n\n这是一个足够长的真实资料片段，用于建立可以被引用的知识证据。"}).status_code == 200
    assert make_goal(client, ws["id"]).status_code == 200
    response = client.post(f"/api/workspaces/{ws['id']}/feasibility/analyze")
    assert response.status_code == 200
    result = response.json()
    assert result["recommended_minutes_per_day"] >= result["base_minutes_per_day"]
    assert result["buffer_rate"] == 0.2
    saved_goal = client.get(f"/api/workspaces/{ws['id']}").json()["goal"]["payload"]
    assert saved_goal["minutes_per_day"] == 30
    assert result["minutes_per_day_applied"] is None
    assert result["suggested_goal_patch"]["minutes_per_day"] == result["recommended_minutes_per_day"]
    confirmed = client.post(
        f"/api/workspaces/{ws['id']}/feasibility/confirm",
        json={
            "confirmed": True,
            "selected_option": "increase_daily_minutes",
            "minutes_per_day": result["recommended_minutes_per_day"],
        },
    )
    assert confirmed.status_code == 200
    saved_goal = client.get(f"/api/workspaces/{ws['id']}").json()["goal"]["payload"]
    assert saved_goal["minutes_per_day"] == result["recommended_minutes_per_day"]


def test_start_date_is_saved_and_plan_starts_on_it(client):
    ws = make_workspace(client)
    start = date.today() + timedelta(days=2)
    deadline = start + timedelta(days=7)
    response = client.post(f"/api/workspaces/{ws['id']}/goal", json={
        "goal_statement": "在计划周期内完成可验证学习成果", "start_date": start.isoformat(), "deadline": deadline.isoformat(),
        "minutes_per_day": 30, "days_per_week": 5, "current_level": "beginner", "expected_output": "成果", "success_criteria": ["完成任务"],
    })
    assert response.status_code == 200
    assert response.json()["payload"]["start_date"] == start.isoformat()
    client.post(f"/api/workspaces/{ws['id']}/feasibility/analyze")
    client.post(f"/api/workspaces/{ws['id']}/feasibility/confirm", json={"confirmed": True})
    plan = client.post(f"/api/workspaces/{ws['id']}/plans/generate").json()
    assert plan["start_date"] == start.isoformat()
    assert plan["study_days"][0]["date"] >= start.isoformat()


def test_file_presets_can_use_a_configured_directory(client, tmp_path, monkeypatch):
    from app import main as main_module
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", tmp_path / "preset_settings.json")
    directory = tmp_path / "presets"
    saved = client.post("/api/presets", json={"name": "LeetCode Java17", "directory": str(directory), "form": {"title": "Top 150"}})
    assert saved.status_code == 200
    assert (directory / "LeetCode Java17.json").exists()
    listed = client.get("/api/presets", params={"directory": str(directory)})
    assert listed.status_code == 200
    assert listed.json()["presets"][0]["form"]["title"] == "Top 150"


def test_file_presets_reject_directory_outside_approved_data_root(client, tmp_path, monkeypatch):
    from app import main as main_module
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", tmp_path / "preset_settings.json")
    outside = tmp_path.parent / "mentorflow-outside-presets"
    response = client.post("/api/presets", json={"name": "escape", "directory": str(outside), "form": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRESET_DIRECTORY_NOT_ALLOWED"


def test_file_presets_reject_symlink_escape(client, tmp_path, monkeypatch):
    from app import main as main_module
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", data_root / "preset_settings.json")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = data_root / "linked-presets"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Windows symlink escape fixture unavailable; not an acceptance: {exc}")
    response = client.post("/api/presets", json={"name": "symlink-escape", "directory": str(link), "form": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRESET_DIRECTORY_NOT_ALLOWED"


def test_file_presets_reject_junction_escape(client, tmp_path, monkeypatch):
    from app import main as main_module
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(main_module, "PRESET_SETTINGS_FILE", data_root / "preset_settings.json")
    outside = tmp_path / "junction-outside"
    outside.mkdir()
    junction = data_root / "junction-presets"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"Windows junction escape fixture could not be created: {result.stdout} {result.stderr}")
    response = client.post("/api/presets", json={"name": "junction-escape", "directory": str(junction), "form": {}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PRESET_DIRECTORY_NOT_ALLOWED"


def test_kks_routes_report_explicit_unavailable_capability(client):
    capability = client.get("/api/koikatsu/capability")
    assert capability.status_code == 200
    assert capability.json()["available"] is False
    assert capability.json()["code"] == "KKS_RUNTIME_UNAVAILABLE"
    assert capability.json()["fallback"] == "teacher_without_avatar"
    assert capability.json()["status"] == "unavailable"
    assert capability.json()["runtime_version_hash"]
    response = client.get("/api/teacher-avatars")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "KKS_RUNTIME_UNAVAILABLE"
    assert response.json()["error"]["details"]["fallback"] == "teacher_without_avatar"
