import * as React from "react"
import { call, BridgeFailure } from "../bridge"
import { useApp } from "../store"
import { Button } from "../ui"
import { AvatarCropDialog } from "./AvatarCropDialog"
import "./profile.css"

type AccountProfile = { nickname: string; signature: string; version: number; avatar_url: string | null; available: boolean; offline: boolean }
type Crop = { x: number; y: number; size: number }
type Preview = { url: string; width: number; height: number }

export function AccountProfilePanel() {
  const account = useApp((s) => s.account)
  const [profile, setProfile] = React.useState<AccountProfile | null>(null)
  const [nickname, setNickname] = React.useState("")
  const [signature, setSignature] = React.useState("")
  const [preview, setPreview] = React.useState<Preview | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState("")
  const [savedStatus, setSavedStatus] = React.useState("")
  const identity = account?.account_id ?? account?.name ?? ""
  const request = React.useRef(0)
  const scope = React.useRef({ identity, revision: 0 })
  if (scope.current.identity !== identity) scope.current = { identity, revision: scope.current.revision + 1 }
  const isCurrentScope = (revision: number, expectedIdentity: string) =>
    scope.current.revision === revision && scope.current.identity === expectedIdentity &&
    ((useApp.getState().account?.account_id ?? useApp.getState().account?.name ?? "") === expectedIdentity)
  const setAccountProfile = (value: AccountProfile) => {
    const current = useApp.getState().account
    if (current) useApp.getState().set({ account: { ...current, nickname: value.nickname ?? "", signature: value.signature ?? "", avatar_url: value.avatar_url ?? null } })
  }
  const load = React.useCallback(async () => {
    const sequence = ++request.current
    const { revision } = scope.current
    const current = () => sequence === request.current && isCurrentScope(revision, identity)
    setLoading(true); setError("")
    try {
      const result = await call<AccountProfile>("account.profile_get")
      if (!current()) return
      setProfile(result); setNickname(result.nickname ?? ""); setSignature(result.signature ?? "")
      setAccountProfile(result)
    } catch (e) {
      if (!current()) return
      const failure = e as BridgeFailure
      if (["NOT_FOUND", "METHOD_NOT_FOUND", "UNKNOWN_METHOD", "UNSUPPORTED"].includes(failure.code)) {
        setProfile({ nickname: "", signature: "", version: 0, avatar_url: null, available: false, offline: false })
      } else setError(failure.message || "个人资料暂时无法读取。")
    } finally { if (current()) setLoading(false) }
  }, [identity])
  React.useEffect(() => {
    setProfile(null); setNickname(""); setSignature(""); setPreview(null); setBusy(false); setSavedStatus("")
    void load()
    return () => { request.current++ }
  }, [identity, load])
  const available = profile?.available ?? false
  const offline = profile?.offline ?? account?.offline ?? false
  const nicknameCount = Array.from(nickname).length
  const signatureCount = Array.from(signature).length
  const invalid = nicknameCount > 30 || signatureCount > 60
  const changed = !!profile && (nickname !== profile.nickname || signature !== profile.signature)
  const profilePatch = (result: AccountProfile, revision: number, expectedIdentity: string) => {
    if (!isCurrentScope(revision, expectedIdentity)) return
    setProfile(result); setNickname(result.nickname ?? ""); setSignature(result.signature ?? ""); setPreview(null)
    setAccountProfile(result); setSavedStatus("已保存")
  }
  const profileAvatarPatch = (result: AccountProfile, revision: number, expectedIdentity: string, message: string) => {
    if (!isCurrentScope(revision, expectedIdentity)) return
    setProfile(result); setPreview(null)
    setAccountProfile(result); setSavedStatus(message)
  }
  async function saveText() {
    if (!profile || !available || offline || invalid || !changed || busy) return
    const { revision } = scope.current
    const expectedIdentity = identity
    setBusy(true); setError(""); setSavedStatus("")
    try { profilePatch(await call<AccountProfile>("account.profile_save", { nickname, signature, version: profile.version }), revision, expectedIdentity) }
    catch (e) { if (isCurrentScope(revision, expectedIdentity)) setError((e as Error).message || "保存没有完成。") }
    finally { if (isCurrentScope(revision, expectedIdentity)) setBusy(false) }
  }
  async function chooseAvatar() {
    if (!available || offline || busy) return
    const { revision } = scope.current
    const expectedIdentity = identity
    setBusy(true); setError("")
    try {
      const result = await call<{ preview_url: string; width: number; height: number; cancelled?: boolean }>("account.profile_choose_avatar")
      if (!isCurrentScope(revision, expectedIdentity)) return
      if (result.cancelled) return
      if (!result.preview_url || result.width < 1 || result.height < 1) throw new Error("头像预览无法读取。")
      setPreview({ url: result.preview_url, width: result.width, height: result.height })
    } catch (e) { if (isCurrentScope(revision, expectedIdentity)) setError((e as Error).message || "头像没有读取成功。") }
    finally { if (isCurrentScope(revision, expectedIdentity)) setBusy(false) }
  }
  async function saveAvatar(crop: Crop) {
    if (!preview || !profile || !available || offline || busy) return
    const { revision } = scope.current
    const expectedIdentity = identity
    setBusy(true); setError(""); setSavedStatus("")
    try {
      const result = await call<AccountProfile>("account.profile_save_avatar", { crop, version: profile.version })
      profileAvatarPatch(result, revision, expectedIdentity, "头像已保存")
    } catch (e) { if (isCurrentScope(revision, expectedIdentity)) setError((e as Error).message || "头像没有保存成功。") }
    finally { if (isCurrentScope(revision, expectedIdentity)) setBusy(false) }
  }
  async function deleteAvatar() {
    if (!profile?.avatar_url || !available || offline || busy) return
    const { revision } = scope.current
    const expectedIdentity = identity
    setBusy(true); setError(""); setSavedStatus("")
    try { profileAvatarPatch(await call<AccountProfile>("account.profile_delete_avatar", { version: profile.version }), revision, expectedIdentity, "头像已删除") }
    catch (e) { if (isCurrentScope(revision, expectedIdentity)) setError((e as Error).message || "头像没有删除成功。") }
    finally { if (isCurrentScope(revision, expectedIdentity)) setBusy(false) }
  }
  const displayName = nickname || account?.name || "账号"
  const initial = (Array.from(account?.name || "M")[0] || "M").toLocaleUpperCase()

  return <section className="stack mf-profile" aria-label="个人资料" data-testid="profile226">
    <header className="mf-profile-heading">
      <div><span className="mf-profile-eyebrow">A LITTLE SPACE OF YOUR OWN</span><h2>个人资料</h2>
        <p className="sub">给自己留一张小小的个人海报。头像与昵称只对你显示。</p></div>
      <span className="mf-profile-private" aria-label="仅自己可见">◈ 仅自己可见</span>
    </header>
    {loading ? <p className="muted" role="status">正在读取个人资料…</p> : profile && !available ? <p className="muted" role="status">当前服务器暂不支持个人资料，账号仍可正常使用。</p> : !profile ? <div><p className="muted" role="status">个人资料暂时无法读取，请重试。</p><Button small onClick={() => void load()}>重新读取</Button></div> : <>
      <div className="mf-profile-poster">
        <div className="mf-profile-portrait">
          <div className="mf-profile-art">
            <div className="mf-profile-orbit" aria-hidden="true" />
            <div className="mf-profile-avatar" data-testid="profile226-avatar">
              {profile.avatar_url ? <img src={profile.avatar_url} alt="账号头像" /> : <span aria-label={`默认头像 ${initial}`}>{initial}</span>}
            </div>
          </div>
          <span className="mf-profile-badge">✦ 正在自己的节奏里</span>
          <div className="mf-profile-avatar-actions">
            <Button small disabled={offline || busy} onClick={() => void chooseAvatar()}>{profile.avatar_url ? "重新选择原图" : "选择头像"}</Button>
            {profile.avatar_url && <Button small disabled={offline || busy} onClick={() => void deleteAvatar()}>删除头像</Button>}
          </div>
          <span className="mf-profile-photo-note">JPEG / PNG · 原图最大 5 MB</span>
        </div>
        <div className="mf-profile-editor">
          <div className="mf-profile-identity"><span className="mf-profile-greeting">你好，{account?.name || "学习者"}</span>
            <h3>{displayName}</h3><p>{signature || "在自己的节奏里，慢慢学会新的东西。"}</p>
            <small>登录用户名：{account?.name ?? "—"} · {account?.role_label || "学习者"}</small></div>
          <div className="mf-profile-fields">
            <label className="stack"><b>昵称</b><input className="input" aria-label="昵称" value={nickname}
              onChange={e => { setNickname(e.target.value); setSavedStatus("") }} disabled={offline || busy} placeholder={account?.name || "留空时显示登录用户名"} />
              <span className="small muted">{nicknameCount}/30 字 · 可以留空，也可以与别人重名。</span></label>
            <label className="stack"><b>个性签名</b><textarea className="input" aria-label="个性签名" value={signature} rows={3}
              onChange={e => { setSignature(e.target.value); setSavedStatus("") }} disabled={offline || busy} placeholder="写一句介绍自己的话（选填）" />
              <span className="small muted">{signatureCount}/60 字 · 只对自己显示</span></label>
          </div>
          <div className="mf-profile-save-row"><Button kind="primary" busy={busy} disabled={offline || !changed || invalid || loading} onClick={() => void saveText()}>保存个人资料</Button>
            <span className={savedStatus ? "mf-profile-saved is-saved" : "mf-profile-saved"} role="status">{savedStatus ? `✓ ${savedStatus}` : changed ? "尚有未保存修改" : "资料已同步"}</span>
            <Button small disabled={loading || busy} onClick={() => void load()}>重新读取</Button></div>
          {offline && <p className="small muted" role="status">当前离线，只能查看本机保存的资料；联网后才能修改。</p>}
          {invalid && <p role="alert" className="error-text">昵称最多 30 字，签名最多 60 字。</p>}
        </div>
        <div className="mf-profile-footer" aria-hidden="true">YOUR SPACE · 01</div>
      </div>
    </>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {preview && <AvatarCropDialog preview={preview} busy={busy} error={error || undefined}
      onClose={() => { if (!busy) { setPreview(null); setError("") } }} onSave={(crop) => void saveAvatar(crop)} />}
  </section>
}
