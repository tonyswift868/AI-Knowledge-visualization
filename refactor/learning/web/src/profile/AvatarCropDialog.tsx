import * as React from "react"
import { createPortal } from "react-dom"
import Cropper, { type Area } from "react-easy-crop"
import { X } from "lucide-react"
import "./avatar-crop.css"

export type AvatarCrop = { x: number; y: number; size: number }
export type AvatarCropPreview = { url: string; width: number; height: number }

export type AvatarCropDialogProps = {
  preview: AvatarCropPreview
  busy: boolean
  error?: string
  onClose: () => void
  onSave: (crop: AvatarCrop) => void
}

/** Cropping coordinates are in the EXIF-corrected, <=1024px server preview's pixels. */
export function AvatarCropDialog({ preview, busy, error, onClose, onSave }: AvatarCropDialogProps) {
  const [zoom, setZoom] = React.useState(1)
  const [crop, setCrop] = React.useState({ x: 0, y: 0 })
  const [cropPixels, setCropPixels] = React.useState<Area | null>(null)
  const closeRef = React.useRef<HTMLButtonElement>(null)
  const dialogRef = React.useRef<HTMLElement>(null)
  const previewCanvasRef = React.useRef<HTMLCanvasElement>(null)
  const openerRef = React.useRef<HTMLElement | null>(null)
  const closeHandlerRef = React.useRef(onClose)
  const busyRef = React.useRef(busy)
  closeHandlerRef.current = onClose
  busyRef.current = busy

  React.useEffect(() => {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (closeRef.current?.disabled) dialogRef.current?.focus()
    else closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault()
        closeHandlerRef.current()
        return
      }
      if (event.key === "Tab") {
        const controls = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
        )
        if (!controls?.length) return
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {
          event.preventDefault(); last.focus()
        } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current?.contains(document.activeElement))) {
          event.preventDefault(); first.focus()
        }
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => {
      window.removeEventListener("keydown", onKeyDown)
      openerRef.current?.focus()
    }
  }, [])

  React.useEffect(() => {
    const canvas = previewCanvasRef.current
    const context = canvas?.getContext("2d")
    if (!canvas || !context || !cropPixels) return
    const image = new Image()
    image.onload = () => {
      const size = Math.min(cropPixels.width, cropPixels.height)
      context.clearRect(0, 0, canvas.width, canvas.height)
      context.save()
      context.beginPath()
      context.arc(canvas.width / 2, canvas.height / 2, canvas.width / 2, 0, Math.PI * 2)
      context.clip()
      context.drawImage(image, cropPixels.x, cropPixels.y, size, size, 0, 0, canvas.width, canvas.height)
      context.restore()
    }
    image.src = preview.url
    return () => { image.onload = null }
  }, [preview.url, cropPixels])

  const save = () => {
    if (busy || !cropPixels) return
    // react-easy-crop reports the selected square in source-image pixels, matching image_preview.
    const size = Math.round(Math.min(cropPixels.width, cropPixels.height))
    const x = Math.max(0, Math.min(preview.width - size, Math.round(cropPixels.x)))
    const y = Math.max(0, Math.min(preview.height - size, Math.round(cropPixels.y)))
    if (size > 0) onSave({ x, y, size })
  }

  return createPortal(
    <div className="avatar-crop-backdrop" data-testid="profile226-crop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose() }}>
      <section ref={dialogRef} tabIndex={-1} className="avatar-crop-dialog" role="dialog" aria-modal="true" aria-labelledby="avatar-crop-title" aria-describedby="avatar-crop-help">
        <div className="avatar-crop-visual">
          <p className="avatar-crop-caption">拖动照片调整位置</p>
          <div className="avatar-crop-stage">
            <Cropper image={preview.url} crop={crop} zoom={zoom} minZoom={1} maxZoom={3} zoomSpeed={0.12}
              aspect={1} cropShape="round" showGrid={false} restrictPosition
              style={{ containerStyle: { pointerEvents: busy ? "none" : "auto" } }}
              onCropChange={setCrop} onZoomChange={setZoom} onCropComplete={(_, pixels) => setCropPixels(pixels)} />
          </div>
        </div>
        <div className="avatar-crop-controls">
          <header className="avatar-crop-header">
            <div><p className="avatar-crop-kicker">圆形预览 · 头像效果</p><h2 id="avatar-crop-title">找到最像你的角度</h2></div>
            <button ref={closeRef} className="avatar-crop-icon" type="button" aria-label="关闭裁剪窗口" onClick={onClose} disabled={busy}><X size={19} /></button>
          </header>
          <p id="avatar-crop-help" className="avatar-crop-help">按住照片拖动位置，使用滑杆缩放。圆形预览模拟头像在账号卡片里的显示效果。</p>
          <div className="avatar-crop-tools">
            <label htmlFor="avatar-crop-zoom">缩放</label>
            <input id="avatar-crop-zoom" type="range" min="1" max="3" step="0.01" value={zoom}
              onChange={event => setZoom(Number(event.target.value))} disabled={busy} aria-label="缩放照片" />
            <output htmlFor="avatar-crop-zoom">{Math.round(zoom * 100)}%</output>
          </div>
          <div className="avatar-crop-preview-row">
            <canvas ref={previewCanvasRef} className="avatar-crop-circle-preview" width={112} height={112} aria-hidden="true" />
            <span><strong>圆形显示预览</strong><br /><small>保存取景，头像以圆形显示</small></span>
          </div>
          {error && <p className="avatar-crop-error" role="alert">{error}</p>}
          <footer className="avatar-crop-footer">
            <button className="avatar-crop-cancel" type="button" onClick={onClose} disabled={busy}>取消</button>
            <button className="avatar-crop-save" type="button" onClick={save} disabled={busy || !cropPixels}>
              {busy ? "正在保存…" : "保存头像"}
            </button>
          </footer>
        </div>
      </section>
    </div>, document.body,
  )
}
