import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { EnterpriseAdminControlPlane } from '../features/enterprise-admin/EnterpriseAdminControlPlane'
import type { EnterpriseAdminClient, EnterpriseAdminSnapshot } from '../features/enterprise-admin/types'


afterEach(() => cleanup())


describe('EnterpriseAdminControlPlane', () => {
  it('renders the independent demo actor, tenant selectors, and all governance panels', () => {
    const { container } = render(<EnterpriseAdminControlPlane />)
    expect(screen.getByText('Demo actor adapter')).toBeInTheDocument()
    expect(screen.getByLabelText('Organization')).toBeInTheDocument()
    expect(screen.getByLabelText('Workspace')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Members & roles' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Provider status' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Quota enforcement' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Audit activity' })).toBeInTheDocument()
    expect(screen.getAllByText(/VISIBLE/)).toHaveLength(8)
    const rendered = container.textContent?.toLowerCase() ?? ''
    expect(rendered).not.toContain('api_key')
    expect(rendered).not.toContain('mfcred_')
    expect(rendered).not.toContain('secret_reference')
  })

  it('shows workspace-specific role bindings for the same member', () => {
    render(<EnterpriseAdminControlPlane />)
    expect(screen.getAllByText('INSTRUCTOR', { selector: 'td' })).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('Workspace'), {
      target: { value: 'workspace-science' },
    })
    expect(screen.getByText('CONTENT_MANAGER', { selector: 'td' })).toBeInTheDocument()
  })

  it('loads a live snapshot through the context-resolver API client seam', async () => {
    const liveSnapshot: EnterpriseAdminSnapshot = {
      mode: 'live',
      actor: { subject_id: 'server-owner', role: 'ORGANIZATION_OWNER', source: 'context-resolver' },
      organizations: [{ organization_id: 'org-server', name: 'Server Organization' }],
      workspaces: [{ workspace_id: 'ws-server', organization_id: 'org-server', name: 'Server Workspace', status: 'ACTIVE' }],
      members: [],
      providers: [],
      quotas: [],
      audit: [],
      navigation: [{
        category: 'Organization Admin',
        state: 'VISIBLE',
        resource: 'organization',
        relationship_check_required: true,
      }],
      authorization_still_required: true,
    }
    const client: EnterpriseAdminClient = {
      loadSnapshot: () => Promise.resolve(liveSnapshot),
    }
    render(<EnterpriseAdminControlPlane client={client} />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading enterprise governance')
    await waitFor(() => expect(screen.getByText('Authenticated context resolver')).toBeInTheDocument())
    expect(screen.getByText('server-owner')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Server Workspace' })).toBeInTheDocument()
  })
})
