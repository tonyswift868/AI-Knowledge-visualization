import { useEffect, useMemo, useState } from 'react'
import { ENTERPRISE_ADMIN_DEMO_FIXTURE } from './demoFixture'
import type { EnterpriseAdminClient, EnterpriseAdminSnapshot } from './types'
import './enterprise-admin.css'

interface EnterpriseAdminControlPlaneProps {
  client?: EnterpriseAdminClient
  initialSnapshot?: EnterpriseAdminSnapshot
}

function roleLabel(values: string[]): string {
  return values.length ? values.join(' · ') : 'No workspace role'
}

export function EnterpriseAdminControlPlane({
  client,
  initialSnapshot = ENTERPRISE_ADMIN_DEMO_FIXTURE,
}: EnterpriseAdminControlPlaneProps) {
  const [snapshot, setSnapshot] = useState(initialSnapshot)
  const [loading, setLoading] = useState(Boolean(client))
  const [error, setError] = useState<string | null>(null)
  const [organizationId, setOrganizationId] = useState(initialSnapshot.organizations[0]?.organization_id ?? '')
  const [workspaceId, setWorkspaceId] = useState(
    initialSnapshot.workspaces.find((workspace) => workspace.organization_id === organizationId)?.workspace_id ?? '',
  )

  useEffect(() => {
    if (!client) return
    let active = true
    setLoading(true)
    client.loadSnapshot().then((next) => {
      if (!active) return
      setSnapshot(next)
      const nextOrganization = next.organizations[0]?.organization_id ?? ''
      setOrganizationId(nextOrganization)
      setWorkspaceId(next.workspaces.find((workspace) => workspace.organization_id === nextOrganization)?.workspace_id ?? '')
      setError(null)
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : 'Unable to load control plane')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [client])

  const workspaces = useMemo(
    () => snapshot.workspaces.filter((workspace) => workspace.organization_id === organizationId),
    [organizationId, snapshot.workspaces],
  )
  const scopedQuotas = snapshot.quotas.filter(
    (policy) => policy.workspace_id === null || policy.workspace_id === workspaceId,
  )

  function selectOrganization(nextOrganizationId: string) {
    setOrganizationId(nextOrganizationId)
    setWorkspaceId(
      snapshot.workspaces.find((workspace) => workspace.organization_id === nextOrganizationId)?.workspace_id ?? '',
    )
  }

  return (
    <main className="enterprise-admin-shell">
      <header className="enterprise-admin-header">
        <div>
          <p className="enterprise-admin-eyebrow">Enterprise governance</p>
          <h1>Admin Control Plane</h1>
          <p className="enterprise-admin-subtitle">
            Authorization is re-evaluated by the backend for every read and mutation.
          </p>
        </div>
        <div className="enterprise-admin-actor" aria-label="Authenticated actor adapter">
          <span>{snapshot.mode === 'demo' ? 'Demo actor adapter' : 'Authenticated context resolver'}</span>
          <strong>{snapshot.actor.subject_id}</strong>
          <small>{snapshot.actor.role}</small>
        </div>
      </header>

      {loading && <p role="status">Loading enterprise governance…</p>}
      {error && <p role="alert">{error}</p>}

      <section className="enterprise-admin-selectors" aria-label="Tenant selectors">
        <label>
          Organization
          <select value={organizationId} onChange={(event) => selectOrganization(event.target.value)}>
            {snapshot.organizations.map((organization) => (
              <option key={organization.organization_id} value={organization.organization_id}>{organization.name}</option>
            ))}
          </select>
        </label>
        <label>
          Workspace
          <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
            {workspaces.map((workspace) => (
              <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.name}</option>
            ))}
          </select>
        </label>
      </section>

      <nav className="enterprise-admin-permissions" aria-label="Permission-aware navigation">
        {snapshot.navigation.map((entry) => (
          <span key={entry.category} data-state={entry.state}>{entry.category} · {entry.state}</span>
        ))}
      </nav>

      <section className="enterprise-admin-grid">
        <article className="enterprise-admin-card enterprise-admin-card-wide">
          <div className="enterprise-admin-card-heading">
            <div><p>Access</p><h2>Members &amp; roles</h2></div>
            <span>Workspace roles are independent</span>
          </div>
          <div className="enterprise-admin-table-wrap">
            <table>
              <thead><tr><th>Member</th><th>Status</th><th>Organization role</th><th>Selected workspace role</th></tr></thead>
              <tbody>
                {snapshot.members.map((member) => (
                  <tr key={member.member_id}>
                    <td><strong>{member.subject_id}</strong><small>{member.email ?? 'Redacted'}</small></td>
                    <td>{member.status}</td>
                    <td>{roleLabel(member.organization_roles)}</td>
                    <td>{roleLabel(member.workspace_roles[workspaceId] ?? [])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>

        <article className="enterprise-admin-card">
          <div className="enterprise-admin-card-heading"><div><p>Runtime</p><h2>Provider status</h2></div><span>Metadata only</span></div>
          {snapshot.providers.map((provider) => (
            <div className="enterprise-admin-metric" key={provider.config_id}>
              <strong>{provider.provider}</strong>
              <span>{provider.status} · {provider.model.join(', ')}</span>
              <small>{provider.configured ? 'Configured' : 'Not configured'} · rotation {provider.rotation_time ?? 'not recorded'}</small>
            </div>
          ))}
        </article>

        <article className="enterprise-admin-card">
          <div className="enterprise-admin-card-heading"><div><p>Cost</p><h2>Quota enforcement</h2></div><span>Strictest wins</span></div>
          {scopedQuotas.map((policy) => (
            <div className="enterprise-admin-metric" key={policy.policy_id}>
              <strong>{policy.scope}</strong>
              <span>{policy.policy_id}</span>
              <small>{Object.entries(policy.feature_quotas).map(([feature, limit]) => `${feature}: ${limit}`).join(' · ') || 'Model and budget policy'}</small>
            </div>
          ))}
        </article>

        <article className="enterprise-admin-card enterprise-admin-card-wide">
          <div className="enterprise-admin-card-heading"><div><p>Evidence</p><h2>Audit activity</h2></div><span>Read-only · redacted</span></div>
          <ol className="enterprise-admin-audit">
            {snapshot.audit.map((entry) => (
              <li key={entry.sequence}>
                <span data-outcome={entry.outcome}>{entry.outcome}</span>
                <div><strong>{entry.action}</strong><small>{entry.actor_id} · {entry.resource_type}/{entry.resource_id}</small></div>
                <code>{entry.reason_code}</code>
              </li>
            ))}
          </ol>
        </article>
      </section>
    </main>
  )
}
