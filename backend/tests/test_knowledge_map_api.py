def _workspace(client):
    response = client.post(
        "/api/workspaces",
        json={"title": "Knowledge map E2E", "execution_mode": "demo"},
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_knowledge_map_is_grounded_versioned_and_idempotent(client):
    workspace_id = _workspace(client)
    source = client.post(
        f"/api/workspaces/{workspace_id}/sources/text",
        json={
            "title": "Algorithms",
            "text": "# Arrays\n\nContiguous storage supports indexed access.\n\n# Binary Search\n\nBinary search requires sorted input.",
            "trust_level": "authoritative",
        },
    )
    assert source.status_code == 200

    first = client.post(f"/api/workspaces/{workspace_id}/knowledge-map/regenerate")
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["changed"] is True
    knowledge_map = first_payload["knowledge_map"]
    assert knowledge_map["revision"] == 1
    assert knowledge_map["generation_mode"] == "deterministic_fallback"
    assert knowledge_map["provider_used"] is False
    assert knowledge_map["nodes"]
    assert all(node["source_refs"] for node in knowledge_map["nodes"] if node["status"] == "active")

    repeated = client.post(f"/api/workspaces/{workspace_id}/knowledge-map/regenerate")
    assert repeated.status_code == 200
    assert repeated.json()["changed"] is False
    assert repeated.json()["knowledge_map"]["fingerprint"] == knowledge_map["fingerprint"]

    quality = client.get(f"/api/workspaces/{workspace_id}/knowledge-map/quality")
    assert quality.status_code == 200
    assert quality.json()["quality"]["sourced_node_ratio"] == 1.0
    assert client.get(f"/api/workspaces/{workspace_id}/knowledge-map/revisions/1").status_code == 200


def test_knowledge_map_requires_real_source_chunks(client):
    workspace_id = _workspace(client)
    response = client.post(f"/api/workspaces/{workspace_id}/knowledge-map/regenerate")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KNOWLEDGE_MAP_NO_SOURCES"
