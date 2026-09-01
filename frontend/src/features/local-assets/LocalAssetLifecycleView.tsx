import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createLocalAssetClient,
  type LocalAssetAction,
  type LocalAssetClient,
  type LocalAssetRecord,
} from './localAssetApi'

const stateLabels: Record<LocalAssetRecord['availability'], string> = {
  AVAILABLE_LOCAL: '本机可用',
  MISSING_ON_DEVICE: '本机缺失，请重新关联',
  LINKED_FILE_MISSING: '链接文件缺失，请重新关联',
  REDOWNLOAD_AVAILABLE: '可从已验证 HTTPS 来源重新下载',
  RESTORABLE_FROM_PACKAGE: '可从当前账号恢复包还原',
}

const actionLabels: Record<LocalAssetAction, string> = {
  relink: '重新关联',
  redownload: '重新下载',
  restore: '从恢复包还原',
}

const safeError = (error: unknown) =>
  error instanceof Error && error.message === 'WINDOWS_DESKTOP_REQUIRED'
    ? '本地资料操作仅在 MentorFlow Windows Desktop 中可用。'
    : '操作未完成。请检查文件是否仍在当前设备，或选择其它恢复方式。'

export type LocalAssetLifecycleViewProps = {
  client?: LocalAssetClient
}

export function LocalAssetLifecycleView({ client: suppliedClient }: LocalAssetLifecycleViewProps) {
  const defaultClient = useMemo(() => createLocalAssetClient(), [])
  const client = suppliedClient || defaultClient
  const [assets, setAssets] = useState<LocalAssetRecord[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [recovery, setRecovery] = useState<{ id: string; action: 'relink' | 'restore' } | null>(null)
  const recoveryInput = useRef<HTMLInputElement>(null)

  const refresh = async () => {
    try {
      setAssets(await client.list())
    } catch (error) {
      setMessage(safeError(error))
    }
  }

  useEffect(() => { void refresh() }, [client])

  const run = async (operation: () => Promise<LocalAssetRecord>, success: string) => {
    setBusy(true)
    try {
      await operation()
      setMessage(success)
      await refresh()
    } catch (error) {
      setMessage(safeError(error))
    } finally {
      setBusy(false)
    }
  }

  const selectedFile = (files: FileList | null) => files?.[0] || null

  const recover = (asset: LocalAssetRecord, action: LocalAssetAction) => {
    if (action === 'redownload') {
      void run(() => client.redownload(asset.local_asset_id), '已按原 SHA-256 恢复资料。')
      return
    }
    setRecovery({ id: asset.local_asset_id, action })
    recoveryInput.current?.click()
  }

  return (
    <section aria-labelledby="local-assets-title" data-testid="local-asset-lifecycle">
      <div className="section-head">
        <div>
          <p className="eyebrow">CURRENT ACCOUNT VAULT</p>
          <h1 id="local-assets-title">本地资料</h1>
          <p>默认使用 Managed Import，把文件复制到当前账号 Vault。Linked Import 只保留当前设备链接。</p>
        </div>
      </div>

      <div className="card" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <label className="primary" style={{ cursor: busy ? 'wait' : 'pointer' }}>
          Managed Import（推荐）
          <input
            data-testid="managed-import-input"
            type="file"
            hidden
            disabled={busy}
            onChange={event => {
              const file = selectedFile(event.currentTarget.files)
              if (file) void run(() => client.importManaged(file), '已复制到当前账号 Vault；原文件可安全移动。')
              event.currentTarget.value = ''
            }}
          />
        </label>
        <label className="ghost" style={{ cursor: busy ? 'wait' : 'pointer' }}>
          Linked Import（高级 · NOT_PORTABLE）
          <input
            data-testid="linked-import-input"
            type="file"
            hidden
            disabled={busy}
            onChange={event => {
              const file = selectedFile(event.currentTarget.files)
              if (file) void run(() => client.importLinked(file), '已保存当前设备链接；文件不会复制或备份。')
              event.currentTarget.value = ''
            }}
          />
        </label>
        <span>shared-models 仅用于模型二进制，不是资料 Vault。</span>
      </div>

      <input
        ref={recoveryInput}
        data-testid="asset-recovery-input"
        type="file"
        hidden
        accept={recovery?.action === 'restore' ? '.mentorflowpack' : undefined}
        onChange={event => {
          const file = selectedFile(event.currentTarget.files)
          const target = recovery
          if (file && target) {
            if (target.action === 'restore') {
              void run(() => client.restore(target.id, file), '已从恢复包还原，资料 revision 保持不变。')
            } else {
              void run(() => client.relink(target.id, file), '已重新关联同一 revision。')
            }
          }
          event.currentTarget.value = ''
          setRecovery(null)
        }}
      />

      {message && <div role="status" className="safety" style={{ marginTop: 12 }}>{message}</div>}
      <div style={{ display: 'grid', gap: 12, marginTop: 16 }}>
        {assets.length === 0 && <div className="empty-state">当前账号还没有本地资料。</div>}
        {assets.map(asset => (
          <article className="card" key={asset.local_asset_id} data-testid={`local-asset-${asset.local_asset_id}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
              <div>
                <h3>{asset.filename}</h3>
                <p>{asset.mime_type} · {asset.size_bytes.toLocaleString()} B</p>
                <code>{asset.sha256}</code>
              </div>
              <div style={{ textAlign: 'right' }}>
                <strong>{asset.availability}</strong>
                <p>{stateLabels[asset.availability]}</p>
                <p>{asset.source_kind === 'LINKED' ? 'LINKED · NOT_PORTABLE' : 'MANAGED · 当前账号 Vault'}</p>
              </div>
            </div>
            {asset.actions.length > 0 && (
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                {asset.actions.map(action => (
                  <button
                    key={action}
                    type="button"
                    disabled={busy}
                    onClick={() => recover(asset, action)}
                  >
                    {actionLabels[action]}
                  </button>
                ))}
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}
