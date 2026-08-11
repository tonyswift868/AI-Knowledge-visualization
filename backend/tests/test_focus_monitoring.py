from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app import active_teaching, focus_monitoring


def _seed_teaching_session() -> str:
    now = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc).isoformat()
    plan_id = "plan_focus_test"
    session_id = "session_focus_test"
    with active_teaching._connect() as connection:
        connection.execute(
            """INSERT INTO active_plans
               (id,workspace_id,receipt,created_at,feasible,
                total_estimated_task_minutes,unscheduled_task_minutes,warning)
               VALUES (?,?,?, ?,1,60,0,'')""",
            (plan_id, "workspace_focus_test", "{}", now),
        )
        connection.execute(
            """INSERT INTO active_teaching_sessions
               (id,workspace_id,plan_id,state,started_at,updated_at,actual_elapsed_minutes)
               VALUES (?,?,?,?,?,?,0)""",
            (
                session_id,
                "workspace_focus_test",
                plan_id,
                json.dumps(
                    {
                        "active_task_id": "task_focus_test",
                        "teacher_memory": {"last_interaction": "teaching"},
                    }
                ),
                now,
                now,
            ),
        )
        connection.commit()
    return session_id


def test_focus_monitoring_stores_only_key_transitions_and_summary(client, monkeypatch):
    session_id = _seed_teaching_session()
    clock = {"now": datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(focus_monitoring, "_now_dt", lambda: clock["now"])

    started = client.post(
        f"/api/active-teaching/sessions/{session_id}/focus-monitoring/start",
        json={"detector_kind": "mediapipe_face_detector", "camera_enabled": True},
    )
    assert started.status_code == 200
    focus_id = started.json()["id"]
    assert started.json()["privacy"] == {
        "processing": "browser_local",
        "frames_stored": False,
        "video_uploaded": False,
        "audio_captured": False,
        "event_cap": 120,
    }

    clock["now"] += timedelta(seconds=2)
    unchanged = client.post(
        f"/api/active-teaching/focus-monitoring/{focus_id}/events",
        json={
            "client_event_id": "event-restored-initial",
            "event_type": "attention_restored",
            "reason": "attention_restored",
        },
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["reason"] == "state_unchanged"

    clock["now"] += timedelta(seconds=8)
    lost = client.post(
        f"/api/active-teaching/focus-monitoring/{focus_id}/events",
        json={
            "client_event_id": "event-attention-lost-001",
            "event_type": "attention_lost",
            "reason": "face_absent",
        },
    )
    assert lost.status_code == 200
    assert lost.json()["accepted"] is True

    clock["now"] += timedelta(seconds=2)
    repeated = client.post(
        f"/api/active-teaching/focus-monitoring/{focus_id}/events",
        json={
            "client_event_id": "event-attention-lost-002",
            "event_type": "attention_lost",
            "reason": "window_blurred",
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["reason"] == "state_unchanged"

    duplicate = client.post(
        f"/api/active-teaching/focus-monitoring/{focus_id}/events",
        json={
            "client_event_id": "event-attention-lost-001",
            "event_type": "attention_lost",
            "reason": "face_absent",
        },
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["reason"] == "duplicate"

    clock["now"] += timedelta(seconds=8)
    restored = client.post(
        f"/api/active-teaching/focus-monitoring/{focus_id}/events",
        json={
            "client_event_id": "event-attention-restored-01",
            "event_type": "attention_restored",
            "reason": "attention_restored",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["accepted"] is True

    clock["now"] += timedelta(seconds=10)
    finished = client.post(
        f"/api/active-teaching/focus-monitoring/{focus_id}/finish",
        json={"reason": "user_finished"},
    )
    assert finished.status_code == 200
    body = finished.json()
    assert body["status"] == "completed"
    assert body["total_seconds"] == 30
    assert body["distracted_seconds"] == 10
    assert body["focused_seconds"] == 20
    assert body["focus_rate"] == 66.7
    assert body["distraction_count"] == 1
    assert body["event_count"] == 2
    assert [event["event_type"] for event in body["events"]] == [
        "attention_lost",
        "attention_restored",
    ]

    history = client.get(
        f"/api/active-teaching/sessions/{session_id}/focus-monitoring"
    )
    assert history.status_code == 200
    assert history.json()["items"][0]["id"] == focus_id

    with active_teaching._connect() as connection:
        state = json.loads(
            connection.execute(
                "SELECT state FROM active_teaching_sessions WHERE id=?",
                (session_id,),
            ).fetchone()["state"]
        )
        assert state["last_focus_summary"]["focus_rate"] == 66.7
        assert state["teacher_memory"]["last_focus_summary"]["id"] == focus_id
        focus_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(active_focus_events)"
            ).fetchall()
        }
        assert focus_columns == {
            "id",
            "focus_session_id",
            "client_event_id",
            "event_type",
            "reason",
            "recorded_at",
        }


def test_focus_monitoring_requires_camera_and_is_idempotent(client):
    session_id = _seed_teaching_session()
    denied = client.post(
        f"/api/active-teaching/sessions/{session_id}/focus-monitoring/start",
        json={"camera_enabled": False},
    )
    assert denied.status_code == 409

    first = client.post(
        f"/api/active-teaching/sessions/{session_id}/focus-monitoring/start",
        json={"camera_enabled": True},
    )
    second = client.post(
        f"/api/active-teaching/sessions/{session_id}/focus-monitoring/start",
        json={"camera_enabled": True},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
