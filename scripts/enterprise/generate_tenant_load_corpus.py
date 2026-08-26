from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "tests" / "load" / "fixtures" / "tenant-corpus.v1.json"
RESOURCE_TYPES = ("document", "knowledge_map", "plan", "progress", "review", "cost", "audit")


def render() -> str:
    organizations: list[dict[str, object]] = []
    for org_number in range(1, 4):
        org_key = f"org-{org_number:02d}"
        workspaces = [f"{org_key}-workspace-{index:02d}" for index in range(1, 3)]
        users = [
            {
                "user_key": f"{org_key}-user-{user_number:02d}",
                "organization_id": org_key,
                "workspace_ids": workspaces,
            }
            for user_number in range(1, 8)
        ]
        resources = {
            resource_type: [f"{org_key}-{resource_type}-{index:02d}" for index in range(1, 4)]
            for resource_type in RESOURCE_TYPES
        }
        organizations.append(
            {
                "organization_id": org_key,
                "workspace_ids": workspaces,
                "users": users,
                "resources": resources,
            }
        )
    payload = {
        "schema": "mentorflow.enterprise-tenant-load-corpus.v1",
        "synthetic": True,
        "contains_credentials": False,
        "organizations": organizations,
        "requirements": {
            "minimum_organizations": 3,
            "minimum_users": 20,
            "foreign_resource_types": list(RESOURCE_TYPES),
            "unauthorized_success_target": 0,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic three-tenant load corpus.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render()
    output = args.output.resolve()
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("tenant load corpus is missing or stale")
        print("verified tenant load corpus: 3 organizations, 21 users, 7 foreign resource types")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
