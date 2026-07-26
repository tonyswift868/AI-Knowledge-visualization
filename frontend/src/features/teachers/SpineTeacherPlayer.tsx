import { useEffect, useRef, useState } from "react"
import type { SpinePlayer } from "@esotericsoftware/spine-player"

export type TeacherAnimationState = "idle" | "listening" | "thinking" | "explaining" | "encouraging"

export type SpineTeacherManifest = {
  format: "mentorflow.spine-teacher"
  runtime_version: "4.1"
  license: {
    spine_editor_license_confirmed: true
    character_rights_confirmed: true
  }
  skeleton: {
    json_url?: string
    binary_url?: string
    atlas_url: string
  }
  animations: Record<TeacherAnimationState, string>
  skin?: string
}

type Props = {
  manifestUrl?: string
  state: TeacherAnimationState
  fallbackSrc: string
  fallbackAlt: string
  onRuntimeChange?: (runtime: "illustration" | "spine" | "error") => void
}

export function validateSpineTeacherManifest(value: unknown): SpineTeacherManifest {
  const manifest = value as Partial<SpineTeacherManifest> | null
  if (!manifest || manifest.format !== "mentorflow.spine-teacher" || manifest.runtime_version !== "4.1") {
    throw new Error("教师资源清单必须是 mentorflow.spine-teacher / Spine 4.1")
  }
  if (
    manifest.license?.spine_editor_license_confirmed !== true
    || manifest.license?.character_rights_confirmed !== true
  ) {
    throw new Error("Spine Editor 授权和角色资源使用权必须同时确认")
  }
  if (!manifest.skeleton?.atlas_url || (!manifest.skeleton.json_url && !manifest.skeleton.binary_url)) {
    throw new Error("教师资源清单缺少骨骼或 atlas 地址")
  }
  for (const state of ["idle", "listening", "thinking", "explaining", "encouraging"] as const) {
    if (!manifest.animations?.[state]) throw new Error(`教师资源清单缺少 ${state} 动画映射`)
  }
  return manifest as SpineTeacherManifest
}

export function SpineTeacherPlayer({
  manifestUrl,
  state,
  fallbackSrc,
  fallbackAlt,
  onRuntimeChange,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<SpinePlayer | null>(null)
  const manifestRef = useRef<SpineTeacherManifest | null>(null)
  const stateRef = useRef(state)
  const [runtime, setRuntime] = useState<"illustration" | "loading" | "spine" | "error">(
    manifestUrl ? "loading" : "illustration",
  )

  useEffect(() => {
    stateRef.current = state
    const player = playerRef.current
    const manifest = manifestRef.current
    if (!player || !manifest) return
    try {
      player.setAnimation(manifest.animations[state], true)
    } catch {
      setRuntime("error")
      onRuntimeChange?.("error")
    }
  }, [onRuntimeChange, state])

  useEffect(() => {
    if (!manifestUrl || !hostRef.current) {
      setRuntime("illustration")
      onRuntimeChange?.("illustration")
      return
    }

    let cancelled = false
    let visible = true
    let intersectionObserver: IntersectionObserver | undefined
    const host = hostRef.current
    const resolvedManifestUrl = manifestUrl

    async function mountPlayer() {
      try {
        setRuntime("loading")
        const [manifestResponse, runtimeModule] = await Promise.all([
          fetch(resolvedManifestUrl),
          import("@esotericsoftware/spine-player"),
          import("@esotericsoftware/spine-player/dist/spine-player.css"),
        ])
        if (!manifestResponse.ok) throw new Error(`教师资源清单加载失败：HTTP ${manifestResponse.status}`)
        const manifest = validateSpineTeacherManifest(await manifestResponse.json())
        if (cancelled) return
        manifestRef.current = manifest

        const player = new runtimeModule.SpinePlayer(host, {
          ...(manifest.skeleton.json_url
            ? { jsonUrl: manifest.skeleton.json_url }
            : { binaryUrl: manifest.skeleton.binary_url }),
          atlasUrl: manifest.skeleton.atlas_url,
          animation: manifest.animations[stateRef.current],
          skin: manifest.skin,
          alpha: true,
          premultipliedAlpha: true,
          backgroundColor: "#00000000",
          showControls: false,
          showLoading: false,
          preserveDrawingBuffer: false,
          defaultMix: 0.25,
          success: loadedPlayer => {
            if (cancelled) {
              loadedPlayer.dispose()
              return
            }
            playerRef.current = loadedPlayer
            setRuntime("spine")
            onRuntimeChange?.("spine")
            if (document.hidden || !visible) loadedPlayer.pause()
          },
          error: (_failedPlayer, message) => {
            if (cancelled) return
            console.error("Spine teacher runtime failed:", message)
            setRuntime("error")
            onRuntimeChange?.("error")
          },
        })
        playerRef.current = player

        const syncPlayback = () => {
          if (document.hidden || !visible) playerRef.current?.pause()
          else playerRef.current?.play()
        }
        document.addEventListener("visibilitychange", syncPlayback)
        intersectionObserver = new IntersectionObserver(entries => {
          visible = entries[0]?.isIntersecting ?? true
          syncPlayback()
        }, { threshold: 0.01 })
        intersectionObserver.observe(host)

        return () => document.removeEventListener("visibilitychange", syncPlayback)
      } catch (error) {
        if (cancelled) return
        console.error("Spine teacher setup failed:", error)
        setRuntime("error")
        onRuntimeChange?.("error")
      }
    }

    let removeVisibilityListener: (() => void) | undefined
    void mountPlayer().then(cleanup => { removeVisibilityListener = cleanup })

    return () => {
      cancelled = true
      removeVisibilityListener?.()
      intersectionObserver?.disconnect()
      playerRef.current?.dispose()
      playerRef.current = null
      manifestRef.current = null
      host.replaceChildren()
    }
  }, [manifestUrl, onRuntimeChange])

  return (
    <div
      className={`spine-teacher-renderer runtime-${runtime}`}
      data-testid="spine-teacher-renderer"
      data-runtime={runtime}
      data-animation-state={state}
    >
      <img key={fallbackSrc} className="spine-teacher-fallback" src={fallbackSrc} alt={fallbackAlt} />
      <div className="spine-teacher-host" ref={hostRef} aria-hidden={runtime !== "spine"} />
      {runtime === "loading" && <span className="spine-teacher-loading">正在加载 2D 骨骼教师…</span>}
    </div>
  )
}
