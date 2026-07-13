import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SourceImportPanel } from '../App'

const preview = {
  preview_id: 'ap_test', archive_name: 'study_bundle.zip', archive_size_bytes: 120, expanded_size_bytes: 240,
  file_count: 3, supported_count: 2, ignored_count: 0, rejected_count: 1,
  tree: [
    { id: 'af_doc', relative_path: 'notes/java.md', filename: 'java.md', extension: '.md', size_bytes: 20, kind: 'document', language: null, status: 'supported', reason: null, selected_by_default: true },
    { id: 'af_code', relative_path: 'src/main.py', filename: 'main.py', extension: '.py', size_bytes: 30, kind: 'code', language: 'python', status: 'supported', reason: null, selected_by_default: true },
    { id: 'af_bad', relative_path: 'bin/run.exe', filename: 'run.exe', extension: '.exe', size_bytes: 10, kind: 'binary', language: null, status: 'rejected', reason: '默认拒绝可执行或二进制文件', selected_by_default: false },
  ],
}

function mockFetch(importResponse: any = { imported: [], duplicates: [], failed: [], ignored: [] }) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes('/archive/preview')) return Promise.resolve(new Response(JSON.stringify(preview), { status: 200 }))
    if (url.includes('/archive/import')) return Promise.resolve(new Response(JSON.stringify(importResponse), { status: 200 }))
    return Promise.resolve(new Response('[]', { status: 200 }))
  })
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('V6 secure ZIP import UI', () => {
  it('accept includes zip and all first-party text formats', () => {
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />)
    expect(screen.getByTestId('file-input')).toHaveAttribute('accept', expect.stringContaining('.zip'))
    expect(screen.getByTestId('file-input')).toHaveAttribute('accept', expect.stringContaining('.markdown'))
  })

  it('requests archive preview instead of direct upload', async () => {
    const fetchMock = mockFetch(); vi.stubGlobal('fetch', fetchMock)
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />)
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } })
    fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'ZIP preview' })).toBeInTheDocument())
    expect(fetchMock.mock.calls.some(call => String(call[0]).includes('/archive/preview'))).toBe(true)
  })

  it('renders the preview tree and archive summary', async () => {
    vi.stubGlobal('fetch', mockFetch())
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />)
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } })
    fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByText('notes/java.md')).toBeInTheDocument())
    expect(screen.getByText(/压缩 120 bytes/)).toBeInTheDocument()
    expect(screen.getByText('支持 2')).toBeInTheDocument()
  })

  it('checks supported files by default and disables rejected files', async () => {
    vi.stubGlobal('fetch', mockFetch())
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />)
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } })
    fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByText('bin/run.exe')).toBeInTheDocument())
    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
    expect(checkboxes[0].checked).toBe(true); expect(checkboxes[1].checked).toBe(true); expect(checkboxes[2].disabled).toBe(true)
  })

  it('supports select-all, document-only and code-only filters', async () => {
    vi.stubGlobal('fetch', mockFetch())
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />)
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } }); fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByText('src/main.py')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '仅文档' })); expect(screen.getByText('notes/java.md')).toBeInTheDocument(); expect(screen.queryByText('src/main.py')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '仅代码' })); expect(screen.getByText('src/main.py')).toBeInTheDocument(); expect(screen.queryByText('notes/java.md')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全选支持文件' })); expect((screen.getAllByRole('checkbox')[0] as HTMLInputElement).checked).toBe(true)
  })

  it('searches internal paths', async () => {
    vi.stubGlobal('fetch', mockFetch())
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } }); fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByText('src/main.py')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('搜索 ZIP 文件'), { target: { value: 'java' } }); expect(screen.getByText('notes/java.md')).toBeInTheDocument(); expect(screen.queryByText('src/main.py')).not.toBeInTheDocument()
  })

  it('sends only selected ids in import payload', async () => {
    const fetchMock = mockFetch({ imported: [{ relative_path: 'notes/java.md' }], duplicates: [], failed: [], ignored: [] }); vi.stubGlobal('fetch', fetchMock)
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } }); fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument()); fireEvent.click(screen.getAllByRole('checkbox')[1]); fireEvent.click(screen.getByRole('button', { name: /确认导入/ }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/成功 1/))
    const call = fetchMock.mock.calls.find(call => String(call[0]).includes('/archive/import'))
    expect(JSON.parse(call?.[1]?.body).selected_file_ids).toEqual(['af_doc'])
  })

  it('can cancel a preview without importing', async () => {
    const fetchMock = mockFetch(); vi.stubGlobal('fetch', fetchMock)
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } }); fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument()); fireEvent.click(screen.getByRole('button', { name: /取消/ })); expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(fetchMock.mock.calls.some(call => String(call[0]).includes('/archive/import'))).toBe(false)
  })

  it('shows real stage and result counts', async () => {
    vi.stubGlobal('fetch', mockFetch({ imported: [{ relative_path: 'notes/java.md' }], duplicates: [{ relative_path: 'src/main.py' }], failed: [{ relative_path: 'bad.md' }], ignored: [{ relative_path: 'bin/run.exe' }] }))
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } }); fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument()); fireEvent.click(screen.getByRole('button', { name: /确认导入/ })); await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('成功 1，重复 1，失败 1，忽略 1'))
  })

  it('prevents confirm with an empty selection', async () => {
    vi.stubGlobal('fetch', mockFetch())
    render(<SourceImportPanel ws={{ id: 'w1' } as any} text="source" />); fireEvent.change(screen.getByTestId('file-input'), { target: { files: [new File(['zip'], 'study_bundle.zip')] } }); fireEvent.click(screen.getByRole('button', { name: '上传并解析' }))
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument()); screen.getAllByRole('checkbox').slice(0, 2).forEach(box => fireEvent.click(box)); expect(screen.getByRole('button', { name: /确认导入/ })).toBeDisabled()
  })
})
