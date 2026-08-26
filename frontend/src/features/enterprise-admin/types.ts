export type NavigationState = 'VISIBLE' | 'DISABLED' | 'HIDDEN'

export type NavigationCategory =
  | 'Organization Admin'
  | 'Workspace Admin'
  | 'Content'
  | 'Instructor'
  | 'Learner'
  | 'Audit'
  | 'Cost'
  | 'Provider Settings'

export interface NavigationEntry {
  category: NavigationCategory
  state: NavigationState
  resource: string
  relationship_check_required: boolean
}

export interface NavigationContextView {
  organization_id: string
  workspace_id: string | null
  role: string
  navigation: NavigationEntry[]
  authorization_still_required: true
}

export interface EnterpriseActorView {
  subject_id: string
  role: string
  source: 'demo-adapter' | 'context-resolver'
}

export interface OrganizationOption {
  organization_id: string
  name: string
}

export interface WorkspaceOption {
  workspace_id: string
  organization_id: string
  name: string
  status: string
}

export interface MemberView {
  member_id: string
  subject_id: string
  email: string | null
  status: string
  organization_roles: string[]
  workspace_roles: Record<string, string[]>
}

export interface ProviderStatusView {
  config_id: string
  provider: string
  configured: boolean
  model: string[]
  status: string
  rotation_time: string | null
}

export interface QuotaPolicyView {
  policy_id: string
  scope: 'ORGANIZATION' | 'WORKSPACE' | 'USER'
  currency: string
  workspace_id: string | null
  user_id: string | null
  approved_models: string[]
  feature_quotas: Record<string, number>
}

export interface AuditEntryView {
  sequence: number
  actor_id: string
  action: string
  resource_type: string
  resource_id: string
  outcome: string
  reason_code: string
  occurred_at: string
}

export interface EnterpriseAdminSnapshot {
  mode: 'demo' | 'live'
  actor: EnterpriseActorView
  organizations: OrganizationOption[]
  workspaces: WorkspaceOption[]
  members: MemberView[]
  providers: ProviderStatusView[]
  quotas: QuotaPolicyView[]
  audit: AuditEntryView[]
  navigation: NavigationEntry[]
  navigation_contexts?: NavigationContextView[]
  authorization_still_required: true
}

export interface EnterpriseAdminClient {
  loadSnapshot(): Promise<EnterpriseAdminSnapshot>
}
