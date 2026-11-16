"""Shared local tenant seeding used by the Java-only 149 cutover tests."""
import os
from pathlib import Path

import psycopg

from contract_support144 import OLD_ISSUER
import contract_support147 as c147

ARTIFACTS = Path(os.environ.get("WIRE148_TMP", r"D:\MentorFlow\.local\test-tmp\wire148")) / "cutover"


def _seed_subject(admin_url: str, subject: str) -> None:
    """Seed tenant membership required by the real Java sync routes."""
    with psycopg.connect(admin_url.replace("postgresql+psycopg:", "postgresql:"), autocommit=True) as conn:
        conn.execute("INSERT INTO organizations(id,slug,name,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (c147.ORG, c147.ORG, "Cutover 149"))
        conn.execute("INSERT INTO workspaces(id,organization_id,slug,name,status) VALUES (%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (c147.WORKSPACE, c147.ORG, c147.WORKSPACE, "Cutover workspace"))
        conn.execute("INSERT INTO organization_memberships(organization_id,subject_id,role,status) VALUES (%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                     (c147.ORG, subject))
        conn.execute("INSERT INTO workspace_memberships(organization_id,workspace_id,subject_id,status) VALUES (%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                     (c147.ORG, c147.WORKSPACE, subject))
        conn.execute("INSERT INTO workspace_role_bindings(organization_id,workspace_id,subject_id,role,status) VALUES (%s,%s,%s,'LEARNER','active') ON CONFLICT DO NOTHING",
                     (c147.ORG, c147.WORKSPACE, subject))
        conn.execute("INSERT INTO rehydration_manifests(issuer,subject_id,organization_id,workspace_id,scope_version,initialized,generation) VALUES (%s,%s,%s,%s,'v1',true,1) ON CONFLICT DO NOTHING",
                     (OLD_ISSUER, subject, c147.ORG, c147.WORKSPACE))
