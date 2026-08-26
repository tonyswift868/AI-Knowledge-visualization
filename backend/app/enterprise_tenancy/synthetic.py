"""Deterministic synthetic tenants for migration, isolation and scale gates."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class SyntheticOrganization:
    organization_id: str
    workspace_ids: tuple[str, str]
    subject_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SyntheticTenantSeed:
    organizations: tuple[SyntheticOrganization, ...]

    @property
    def organization_count(self) -> int:
        return len(self.organizations)

    @property
    def workspace_count(self) -> int:
        return sum(len(item.workspace_ids) for item in self.organizations)

    @property
    def user_count(self) -> int:
        return sum(len(item.subject_ids) for item in self.organizations)


def build_synthetic_tenant_seed() -> SyntheticTenantSeed:
    user_counts = (7, 7, 6)
    cursor = 0
    organizations: list[SyntheticOrganization] = []
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
    seed = SyntheticTenantSeed(organizations=tuple(organizations))
    if (
        seed.organization_count != 3
        or seed.workspace_count != 6
        or seed.user_count != 20
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
                        INSERT INTO workspace_role_bindings(
                          organization_id, workspace_id, subject_id,
                          role, status
                        ) VALUES (
                          :organization_id, :workspace_id, :subject_id,
                          :role, 'active'
                        )
                        ON CONFLICT (
                          organization_id, workspace_id, subject_id
                        ) DO UPDATE SET
                          role = EXCLUDED.role,
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
    return resolved


__all__ = [
    "SyntheticOrganization",
    "SyntheticTenantSeed",
    "build_synthetic_tenant_seed",
    "seed_synthetic_tenants",
]
