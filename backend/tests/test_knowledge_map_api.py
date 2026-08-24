from app.main import app


_RETIRED_MAIN_KNOWLEDGE_MAP_PATHS = {
    "/api/workspaces/{workspace_id}/knowledge-map",
    "/api/workspaces/{workspace_id}/knowledge-map/regenerate",
    "/api/workspaces/{workspace_id}/knowledge-map/quality",
    "/api/workspaces/{workspace_id}/knowledge-map/revisions/{revision}",
}


def test_provider_free_main_knowledge_map_contract_is_retired():
    """The old deterministic service routes are intentionally not registered.

    Checking the OpenAPI registry avoids coupling this retirement assertion to
    whether a future router returns 404 or FastAPI's method-level 405.
    """
    paths = set(app.openapi()["paths"])
    assert _RETIRED_MAIN_KNOWLEDGE_MAP_PATHS.isdisjoint(paths)


def test_active_teaching_legacy_generator_reports_canonical_successor(client):
    retired = client.post(
        "/api/active-teaching/workspaces/retired/knowledge-graph/generate"
    )
    assert retired.status_code == 410
    payload = retired.json()
    assert payload["error"]["code"] == "LEGACY_KNOWLEDGE_GRAPH_ENDPOINT"
    assert payload["error"]["details"]["canonical_path"].endswith(
        "/knowledge-map/jobs"
    )


def test_canonical_knowledge_map_job_successor_is_registered_and_provider_gated(client):
    paths = app.openapi()["paths"]
    jobs_path = "/api/active-teaching/workspaces/{workspace_id}/knowledge-map/jobs"
    assert "post" in paths[jobs_path]

    response = client.post(
        "/api/active-teaching/workspaces/retired/knowledge-map/jobs", json={}
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "PROVIDER_VERIFICATION_REQUIRED"
