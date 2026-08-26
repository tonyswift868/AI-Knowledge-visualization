"""Deterministic synthetic tenants for migration, isolation and scale gates."""

from __future__ import annotations

from dataclasses import dataclass
import json

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class SyntheticOrganization:
    organization_id: str
    workspace_ids: tuple[str, str]
    subject_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyntheticResource:
    resource_id: str
    resource_kind: str
    table: str
    organization_id: str
    workspace_id: str
    owner_subject_id: str
    scope_type: str


@dataclass(frozen=True, slots=True)
class SyntheticTenantSeed:
    organizations: tuple[SyntheticOrganization, ...]
    resources: tuple[SyntheticResource, ...]

    @property
    def organization_count(self) -> int:
        return len(self.organizations)

    @property
    def workspace_count(self) -> int:
        return sum(len(item.workspace_ids) for item in self.organizations)

    @property
    def user_count(self) -> int:
        return sum(len(item.subject_ids) for item in self.organizations)

    @property
    def resource_count(self) -> int:
        return len(self.resources)


def build_synthetic_tenant_seed() -> SyntheticTenantSeed:
    user_counts = (7, 7, 6)
    cursor = 0
    organizations: list[SyntheticOrganization] = []
    resources: list[SyntheticResource] = []
    table_kinds = (
        ("documents", "DOCUMENT"),
        ("evidence", "EVIDENCE"),
        ("knowledge_maps", "KNOWLEDGE_MAP"),
        ("learning_plans", "PLAN"),
        ("teacher_sessions", "TEACHER_SESSION"),
        ("review_campaigns", "REVIEW_CAMPAIGN"),
        ("student_events", "STUDENT_EVENT"),
        ("cost_events", "COST_EVENT"),
    )
    scope_types = ("PERSONAL", "ORGANIZATION", "WORKSPACE")
    for organization_index, user_count in enumerate(user_counts, start=1):
        subject_ids = tuple(
            f"synthetic-user-{index:02d}"
            for index in range(cursor + 1, cursor + user_count + 1)
        )
        cursor += user_count
        organizations.append(
            SyntheticOrganization(
                organization_id=f"synthetic-org-{organization_index}",
                workspace_ids=(
                    f"synthetic-org-{organization_index}-workspace-1",
                    f"synthetic-org-{organization_index}-workspace-2",
                ),
                subject_ids=subject_ids,
            )
        )
        organization = organizations[-1]
        for workspace_index, workspace_id in enumerate(
            organization.workspace_ids,
            start=1,
        ):
            for resource_index, (table, kind) in enumerate(table_kinds, start=1):
                resources.append(
                    SyntheticResource(
                        resource_id=(
                            f"synthetic:{kind.lower()}:{organization_index}:"
                            f"{workspace_index}"
                        ),
                        resource_kind=kind,
                        table=table,
                        organization_id=organization.organization_id,
                        workspace_id=workspace_id,
                        owner_subject_id=organization.subject_ids[0],
                        scope_type=scope_types[(resource_index - 1) % 3],
                    )
                )
    seed = SyntheticTenantSeed(
        organizations=tuple(organizations),
        resources=tuple(resources),
    )
    if (
        seed.organization_count != 3
        or seed.workspace_count != 6
        or seed.user_count != 20
        or seed.resource_count != 48
    ):
        raise AssertionError("synthetic tenant seed cardinality changed")
    return seed


def seed_synthetic_tenants(
    connection: Connection,
    seed: SyntheticTenantSeed | None = None,
) -> SyntheticTenantSeed:
    """Seed with a migration/admin connection, never an application role."""

    resolved = seed or build_synthetic_tenant_seed()
    for organization_index, organization in enumerate(
        resolved.organizations,
        start=1,
    ):
        connection.execute(
            text(
                """
                INSERT INTO organizations(id, slug, name, status)
                VALUES (:id, :slug, :name, 'active')
                ON CONFLICT (id) DO UPDATE SET
                  slug = EXCLUDED.slug,
                  name = EXCLUDED.name,
                  status = EXCLUDED.status,
                  updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "id": organization.organization_id,
                "slug": f"synthetic-{organization_index}",
                "name": f"Synthetic Organization {organization_index}",
            },
        )
        for workspace_index, workspace_id in enumerate(
            organization.workspace_ids,
            start=1,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO workspaces(
                      id, organization_id, slug, name, status
                    ) VALUES (
                      :id, :organization_id, :slug, :name, 'active'
                    )
                    ON CONFLICT (id) DO UPDATE SET
                      organization_id = EXCLUDED.organization_id,
                      slug = EXCLUDED.slug,
                      name = EXCLUDED.name,
                      status = EXCLUDED.status,
                      updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "id": workspace_id,
                    "organization_id": organization.organization_id,
                    "slug": f"workspace-{workspace_index}",
                    "name": (
                        f"Synthetic Organization {organization_index} "
                        f"Workspace {workspace_index}"
                    ),
                },
            )
        for subject_index, subject_id in enumerate(
            organization.subject_ids,
            start=1,
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO organization_memberships(
                      organization_id, subject_id, role, status
                    ) VALUES (
                      :organization_id, :subject_id, :role, 'active'
                    )
                    ON CONFLICT (organization_id, subject_id) DO UPDATE SET
                      role = EXCLUDED.role,
                      status = EXCLUDED.status,
                      updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "organization_id": organization.organization_id,
                    "subject_id": subject_id,
                    "role": "OWNER" if subject_index == 1 else "MEMBER",
                },
            )
            for workspace_id in organization.workspace_ids:
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace_memberships(
                          organization_id, workspace_id, subject_id, status
                        ) VALUES (
                          :organization_id, :workspace_id, :subject_id, 'active'
                        )
                        ON CONFLICT (
                          organization_id, workspace_id, subject_id
                        ) DO UPDATE SET
                          status = EXCLUDED.status,
                          updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "organization_id": organization.organization_id,
                        "workspace_id": workspace_id,
                        "subject_id": subject_id,
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO workspace_role_bindings(
                          organization_id, workspace_id, subject_id,
                          role, status
                        ) VALUES (
                          :organization_id, :workspace_id, :subject_id,
                          :role, 'active'
                        )
                        ON CONFLICT (
                          organization_id, workspace_id, subject_id, role
                        ) DO UPDATE SET
                          status = EXCLUDED.status,
                          updated_at = CURRENT_TIMESTAMP
                        """
                    ),
                    {
                        "organization_id": organization.organization_id,
                        "workspace_id": workspace_id,
                        "subject_id": subject_id,
                        "role": "ADMIN" if subject_index == 1 else "LEARNER",
                    },
                )
    json_value = (
        "CAST(:payload_json AS JSONB)"
        if connection.dialect.name == "postgresql"
        else ":payload_json"
    )
    for resource in resolved.resources:
        parameters = {
            "scope_id": f"scope:{resource.resource_id}",
            "resource_id": resource.resource_id,
            "resource_kind": resource.resource_kind,
            "scope_type": resource.scope_type,
            "owner_subject_id": resource.owner_subject_id,
            "organization_id": resource.organization_id,
            "workspace_id": resource.workspace_id,
            "classification": "INTERNAL",
            "title": f"Synthetic {resource.resource_kind}",
            "status": "ready",
            "payload_json": json.dumps(
                {"synthetic": True},
                separators=(",", ":"),
            ),
            "search_text": f"synthetic {resource.resource_kind.lower()} tenant corpus",
            "job_id": f"job:{resource.resource_id}",
            "cache_key": f"cache:{resource.resource_id}",
        }
        connection.execute(
            text(
                """
                INSERT INTO resource_scopes(
                  id, resource_kind, resource_id, scope_type,
                  owner_subject_id, organization_id, workspace_id,
                  classification
                ) VALUES (
                  :scope_id, :resource_kind, :resource_id, :scope_type,
                  :owner_subject_id, :organization_id, :workspace_id,
                  :classification
                )
                ON CONFLICT (resource_kind, resource_id) DO UPDATE SET
                  scope_type = EXCLUDED.scope_type,
                  owner_subject_id = EXCLUDED.owner_subject_id,
                  organization_id = EXCLUDED.organization_id,
                  workspace_id = EXCLUDED.workspace_id,
                  classification = EXCLUDED.classification,
                  updated_at = CURRENT_TIMESTAMP
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {resource.table}(
                  resource_id, resource_scope_id, resource_kind,
                  organization_id, workspace_id, scope_type,
                  owner_subject_id, classification, title, status,
                  payload_json, search_text, job_id, cache_key
                ) VALUES (
                  :resource_id, :scope_id, :resource_kind,
                  :organization_id, :workspace_id, :scope_type,
                  :owner_subject_id, :classification, :title, :status,
                  {json_value}, :search_text, :job_id, :cache_key
                )
                ON CONFLICT (resource_id) DO UPDATE SET
                  resource_scope_id = EXCLUDED.resource_scope_id,
                  organization_id = EXCLUDED.organization_id,
                  workspace_id = EXCLUDED.workspace_id,
                  scope_type = EXCLUDED.scope_type,
                  owner_subject_id = EXCLUDED.owner_subject_id,
                  classification = EXCLUDED.classification,
                  title = EXCLUDED.title,
                  status = EXCLUDED.status,
                  payload_json = EXCLUDED.payload_json,
                  search_text = EXCLUDED.search_text,
                  job_id = EXCLUDED.job_id,
                  cache_key = EXCLUDED.cache_key,
                  updated_at = CURRENT_TIMESTAMP
                """
            ),
            parameters,
        )
    return resolved


__all__ = [
    "SyntheticOrganization",
    "SyntheticResource",
    "SyntheticTenantSeed",
    "build_synthetic_tenant_seed",
    "seed_synthetic_tenants",
]
