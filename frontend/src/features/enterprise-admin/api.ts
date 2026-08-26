import type {
  AuditEntryView,
  EnterpriseAdminClient,
  EnterpriseAdminSnapshot,
  MemberView,
  NavigationEntry,
  ProviderStatusView,
  QuotaPolicyView,
  WorkspaceOption,
} from './types'

interface BootstrapResponse {
  actor: EnterpriseAdminSnapshot['actor']
  tenant: {
    organization_id: string
    workspace_id: string | null
    mode: string
  }
  navigation: NavigationEntry[]
  authorization_still_required: true
}

interface MembersResponse {
  members: Array<{
    member_id: string
    subject_id: string
    email: string | null
    status: string
    roles: string[]
  }>
}

const configuredBase = (import.meta.env.VITE_ENTERPRISE_ADMIN_API_BASE as string | undefined)?.trim()
const DEFAULT_BASE = configuredBase || 'http://127.0.0.1:8003/api/enterprise-admin'

async function readJson<T>(response: Response): Promise<T> {
  const payload: unknown = await response.json()
  if (!response.ok) {
    const errorValue = (
      typeof payload === 'object'
      && payload !== null
      && 'error' in payload
      && typeof payload.error === 'object'
      && payload.error !== null
      && 'code' in payload.error
    ) ? payload.error.code : undefined
    const code = typeof errorValue === 'string' ? errorValue : undefined
    throw new Error(code || `enterprise admin request failed: ${response.status}`)
  }
  return payload as T
}

async function get<T>(base: string, path: string): Promise<T> {
  return readJson<T>(await fetch(`${base}${path}`, { headers: { Accept: 'application/json' } }))
}

async function post<T>(base: string, path: string, body: unknown): Promise<T> {
  return readJson<T>(await fetch(`${base}${path}`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }))
}

export function createEnterpriseAdminClient(base = DEFAULT_BASE): EnterpriseAdminClient {
  const normalizedBase = base.replace(/\/$/, '')
  return {
    async loadSnapshot(): Promise<EnterpriseAdminSnapshot> {
      const bootstrap = await get<BootstrapResponse>(normalizedBase, '/bootstrap')
      const [members, workspaces, providers, quotas, audit] = await Promise.all([
        get<MembersResponse>(normalizedBase, '/members'),
        get<{ workspaces: WorkspaceOption[] }>(normalizedBase, '/workspaces'),
        get<{ providers: ProviderStatusView[] }>(normalizedBase, '/providers'),
        get<{ policies: QuotaPolicyView[] }>(normalizedBase, `/quotas/${encodeURIComponent(bootstrap.actor.subject_id)}`),
        post<{ entries: AuditEntryView[] }>(normalizedBase, '/audit/query', {
          organization_id: bootstrap.tenant.organization_id,
          workspace_id: bootstrap.tenant.workspace_id,
        }),
      ])
      const normalizedMembers: MemberView[] = members.members.map((member) => ({
        ...member,
        organization_roles: member.roles,
        workspace_roles: {},
      }))
      return {
        mode: 'live',
        actor: bootstrap.actor,
        organizations: [{
          organization_id: bootstrap.tenant.organization_id,
          name: bootstrap.tenant.organization_id,
        }],
        workspaces: workspaces.workspaces.map((workspace) => ({
          ...workspace,
          organization_id: bootstrap.tenant.organization_id,
        })),
        members: normalizedMembers,
        providers: providers.providers,
        quotas: quotas.policies,
        audit: audit.entries,
        navigation: bootstrap.navigation,
        authorization_still_required: true,
      }
    },
  }
}
