import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LocalAssetLifecycleView } from '../features/local-assets/LocalAssetLifecycleView'
import {
  assertServerDtoBoundary,
  type LocalAssetClient,
  type LocalAssetRecord,
  type ServerAssetManifestDto,
} from '../features/local-assets/localAssetApi'

const sha = 'a'.repeat(64)

const records: LocalAssetRecord[] = [
  {
    local_asset_id: 'available', filename: 'available.pdf', mime_type: 'application/pdf',
    size_bytes: 101, sha256: sha, source_kind: 'MANAGED', availability: 'AVAILABLE_LOCAL',
    portable: true, actions: [], labels: ['MANAGED'],
  },
  {
    local_asset_id: 'missing', filename: 'missing.pdf', mime_type: 'application/pdf',
    size_bytes: 102, sha256: sha, source_kind: 'MANAGED', availability: 'MISSING_ON_DEVICE',
    portable: true, actions: ['relink'], labels: ['MANAGED'],
  },
  {
    local_asset_id: 'linked', filename: 'linked.md', mime_type: 'text/markdown',
    size_bytes: 103, sha256: sha, source_kind: 'LINKED', availability: 'LINKED_FILE_MISSING',
    portable: false, actions: ['relink'], labels: ['LINKED', 'NOT_PORTABLE'],
  },
  {
    local_asset_id: 'remote', filename: 'remote.docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    size_bytes: 104, sha256: sha, source_kind: 'MANAGED', availability: 'REDOWNLOAD_AVAILABLE',
    portable: true, verified_source_url: 'https://downloads.example.test/remote.docx',
    actions: ['redownload', 'relink'], labels: ['MANAGED'],
  },
  {
    local_asset_id: 'package', filename: 'package.pptx', mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    size_bytes: 105, sha256: sha, source_kind: 'MANAGED', availability: 'RESTORABLE_FROM_PACKAGE',
    portable: true, actions: ['restore', 'relink'], labels: ['MANAGED'],
  },
]

function client(): LocalAssetClient {
  return {
    list: vi.fn().mockResolvedValue(records),
    importManaged: vi.fn().mockResolvedValue(records[0]),
    importLinked: vi.fn().mockResolvedValue(records[2]),
    relink: vi.fn().mockResolvedValue(records[0]),
    redownload: vi.fn().mockResolvedValue(records[0]),
    restore: vi.fn().mockResolvedValue(records[0]),
    serverDto: vi.fn().mockResolvedValue({
      local_asset_id: 'available', filename: 'available.pdf', mime_type: 'application/pdf',
      size_bytes: 101, sha256: sha, import_mode: 'MANAGED', portability: 'PORTABLE',
      availability: 'AVAILABLE',
    }),
  }
}

afterEach(cleanup)

describe('LocalAssetLifecycleView', () => {
  it('renders all five states, linked portability, and readable recovery actions', async () => {
    const api = client()
    render(<LocalAssetLifecycleView client={api} />)

    for (const state of [
      'AVAILABLE_LOCAL', 'MISSING_ON_DEVICE', 'LINKED_FILE_MISSING',
      'REDOWNLOAD_AVAILABLE', 'RESTORABLE_FROM_PACKAGE',
    ]) {
      expect(await screen.findByText(state)).toBeInTheDocument()
    }
    expect(within(screen.getByTestId('local-asset-linked')).getByText('LINKED · NOT_PORTABLE')).toBeInTheDocument()
    expect(within(screen.getByTestId('local-asset-linked')).getByRole('button', { name: '重新关联' })).toBeInTheDocument()
    expect(within(screen.getByTestId('local-asset-remote')).getByRole('button', { name: '重新下载' })).toBeInTheDocument()
    expect(within(screen.getByTestId('local-asset-package')).getByRole('button', { name: '从恢复包还原' })).toBeInTheDocument()
    expect(screen.queryByText(/FileNotFoundError|\{"detail"|Not Found/)).not.toBeInTheDocument()
  })

  it('connects managed, linked, relink, redownload, and package restore actions', async () => {
    const api = client()
    render(<LocalAssetLifecycleView client={api} />)
    await screen.findByText('AVAILABLE_LOCAL')

    const managedFile = new File(['managed'], 'managed.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByTestId('managed-import-input'), { target: { files: [managedFile] } })
    await waitFor(() => expect(api.importManaged).toHaveBeenCalledWith(managedFile))

    const linkedFile = new File(['linked'], 'linked.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByTestId('linked-import-input'), { target: { files: [linkedFile] } })
    await waitFor(() => expect(api.importLinked).toHaveBeenCalledWith(linkedFile))

    fireEvent.click(within(screen.getByTestId('local-asset-remote')).getByRole('button', { name: '重新下载' }))
    await waitFor(() => expect(api.redownload).toHaveBeenCalledWith('remote'))

    fireEvent.click(within(screen.getByTestId('local-asset-linked')).getByRole('button', { name: '重新关联' }))
    const replacement = new File(['linked'], 'replacement.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByTestId('asset-recovery-input'), { target: { files: [replacement] } })
    await waitFor(() => expect(api.relink).toHaveBeenCalledWith('linked', replacement))

    fireEvent.click(within(screen.getByTestId('local-asset-package')).getByRole('button', { name: '从恢复包还原' }))
    const pack = new File(['pack'], 'account.mentorflowpack')
    fireEvent.change(screen.getByTestId('asset-recovery-input'), { target: { files: [pack] } })
    await waitFor(() => expect(api.restore).toHaveBeenCalledWith('package', pack))
  })

  it('enforces the frozen server DTO allowlist and rejects paths or raw bytes', () => {
    const safe: ServerAssetManifestDto = {
      local_asset_id: 'linked', filename: 'linked.md', mime_type: 'text/markdown',
      size_bytes: 103, sha256: sha, import_mode: 'LINKED', portability: 'NONPORTABLE',
      availability: 'MISSING', device_id: 'device_1234',
    }
    expect(() => assertServerDtoBoundary(safe)).not.toThrow()
    expect(() => assertServerDtoBoundary({ ...safe, local_path: 'C:\\Users\\A\\linked.md' } as unknown as ServerAssetManifestDto)).toThrow('SERVER_DTO_FIELD_REJECTED')
    expect(() => assertServerDtoBoundary({ ...safe, filename: 'D:\\private.pdf' })).toThrow('SERVER_DTO_ABSOLUTE_PATH_REJECTED')
    expect(() => assertServerDtoBoundary({ ...safe, filename: new Blob(['raw']) } as unknown as ServerAssetManifestDto)).toThrow('SERVER_DTO_RAW_BYTES_REJECTED')
  })
})
