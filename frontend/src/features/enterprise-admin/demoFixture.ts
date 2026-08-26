import type { EnterpriseAdminSnapshot } from './types'

const navigationCategories: EnterpriseAdminSnapshot['navigation'][number]['category'][] = [
  'Organization Admin',
  'Workspace Admin',
  'Content',
  'Instructor',
  'Learner',
  'Audit',
  'Cost',
  'Provider Settings',
]

function navigationFor(role: string): EnterpriseAdminSnapshot['navigation'] {
  return navigationCategories.map((category) => {
    const contentManagerVisible = ['Content', 'Instructor'].includes(category)
    return {
      category,
      state: role === 'ORGANIZATION_OWNER' || contentManagerVisible ? 'VISIBLE' : 'HIDDEN',
      resource: category.toLowerCase().replaceAll(' ', '_'),
      relationship_check_required: true,
    }
  })
}

export const ENTERPRISE_ADMIN_DEMO_FIXTURE: EnterpriseAdminSnapshot = {
  mode: 'demo',
  actor: {
    subject_id: 'demo-enterprise-owner',
    role: 'ORGANIZATION_OWNER',
    source: 'demo-adapter',
  },
  organizations: [
    { organization_id: 'demo-org', name: 'MentorFlow Academy' },
    { organization_id: 'demo-org-labs', name: 'MentorFlow Labs' },
  ],
  workspaces: [
    { workspace_id: 'workspace-math', organization_id: 'demo-org', name: 'Mathematics', status: 'ACTIVE' },
    { workspace_id: 'workspace-science', organization_id: 'demo-org', name: 'Science', status: 'ACTIVE' },
    { workspace_id: 'workspace-research', organization_id: 'demo-org-labs', name: 'Research', status: 'ACTIVE' },
  ],
  members: [
    {
      member_id: 'member-alex',
      subject_id: 'alex-instructor',
      email: 'alex@example.test',
      status: 'ACTIVE',
      organization_roles: ['INSTRUCTOR'],
      workspace_roles: {
        'workspace-math': ['INSTRUCTOR'],
        'workspace-science': ['CONTENT_MANAGER'],
      },
    },
    {
      member_id: 'member-rin',
      subject_id: 'rin-auditor',
      email: 'rin@example.test',
      status: 'ACTIVE',
      organization_roles: ['AUDITOR'],
      workspace_roles: {},
    },
  ],
  providers: [
    {
      config_id: 'provider-demo-org',
      provider: 'openai-compatible',
      configured: true,
      model: ['approved-model'],
      status: 'ACTIVE',
      rotation_time: '2026-08-26T08:00:00+00:00',
    },
  ],
  quotas: [
    {
      policy_id: 'quota-demo-org',
      scope: 'ORGANIZATION',
      currency: 'USD',
      workspace_id: null,
      user_id: null,
      approved_models: ['approved-model'],
      feature_quotas: { 'teacher-session': 500 },
    },
    {
      policy_id: 'quota-demo-math',
      scope: 'WORKSPACE',
      currency: 'USD',
      workspace_id: 'workspace-math',
      user_id: null,
      approved_models: ['approved-model'],
      feature_quotas: { 'teacher-session': 120 },
    },
  ],
  audit: [
    {
      sequence: 42,
      actor_id: 'demo-enterprise-owner',
      action: 'membership.invite',
      resource_type: 'membership',
      resource_id: 'member-alex',
      outcome: 'ALLOW',
      reason_code: 'ROLE_AND_RELATIONSHIP_ALLOW',
      occurred_at: '2026-08-26T08:20:00+00:00',
    },
    {
      sequence: 43,
      actor_id: 'outside-actor',
      action: 'workspace.read',
      resource_type: 'workspace',
      resource_id: 'workspace-math',
      outcome: 'DENY',
      reason_code: 'CROSS_TENANT_DENIED',
      occurred_at: '2026-08-26T08:21:00+00:00',
    },
  ],
  navigation: navigationFor('ORGANIZATION_OWNER'),
  navigation_contexts: [
    {
      organization_id: 'demo-org',
      workspace_id: 'workspace-math',
      role: 'ORGANIZATION_OWNER',
      navigation: navigationFor('ORGANIZATION_OWNER'),
      authorization_still_required: true,
    },
    {
      organization_id: 'demo-org',
      workspace_id: 'workspace-science',
      role: 'CONTENT_MANAGER',
      navigation: navigationFor('CONTENT_MANAGER'),
      authorization_still_required: true,
    },
    {
      organization_id: 'demo-org-labs',
      workspace_id: 'workspace-research',
      role: 'AUDITOR',
      navigation: navigationFor('AUDITOR'),
      authorization_still_required: true,
    },
  ],
  authorization_still_required: true,
}
