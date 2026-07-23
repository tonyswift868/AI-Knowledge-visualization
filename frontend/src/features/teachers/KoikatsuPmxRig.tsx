import { useEffect, useRef } from "react"
import * as THREE from "three"
import { MMDLoader } from "three-stdlib"
import { createOnDemandRenderScheduler } from "./kksSwitchLifecycle"

type Props = {
  modelUrl: string
  cameraPreset?: "body" | "upper" | "face"
  cameraZoom?: number
  cameraResetToken?: number
  className?: string
  onError?: (message: string) => void
}

export type RuntimeClothingNode = { source_property?: string; stateNode?: string; clothesStates?: number[] }

/** Pure state predicate retained for the contract tests while native KKS runtime is unavailable. */
export function isRuntimeClothingNodeVisible(item: RuntimeClothingNode): boolean {
  const property = item.source_property?.split(".").pop() || ""
  const node = item.stateNode || ""
  const states = item.clothesStates || []
  if (property === "ClothesBra" || property === "ClothesShorts") return property === "ClothesBra" ? true : node === "n_bot_c" || node === ""
  if (property === "ClothesTop") return node === (states[0] ? "n_top_b" : "n_top_a")
  if (property === "ClothesBot") return node === (states[1] ? "n_bot_b" : "n_bot_a")
  return true
}

/** Renders a KKBP export without reusing the generic VRM skeleton. */
export function KoikatsuPmxRig({ modelUrl, cameraPreset = "body", cameraZoom = 1, cameraResetToken = 0, className, onError }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const invalidateRef = useRef<() => void>(() => {})
  const controls = useRef({ zoom: cameraZoom, reset: cameraResetToken, yaw: 0, pitch: 0 })
  useEffect(() => { controls.current.zoom = cameraZoom; invalidateRef.current() }, [cameraZoom])
  useEffect(() => { controls.current = { ...controls.current, reset: cameraResetToken, yaw: 0, pitch: 0 }; invalidateRef.current() }, [cameraResetToken])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(cameraPreset === "face" ? 24 : 30, 1, .05, 100)
    const baseDistance = cameraPreset === "face" ? 1.25 : cameraPreset === "upper" ? 2.1 : 3.4
    const targetY = cameraPreset === "face" ? 1.55 : cameraPreset === "upper" ? 1.25 : 1.0
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.domElement.style.touchAction = "none"
    mount.replaceChildren(renderer.domElement)
    mount.dataset.modelState = "loading"
    mount.dataset.renderCount = "0"
    scene.add(new THREE.HemisphereLight(0xf6f7ff, 0x191425, 2.4))
    const key = new THREE.DirectionalLight(0xffead3, 3.1); key.position.set(2, 3, 3); scene.add(key)
    const rim = new THREE.DirectionalLight(0x80b7ff, 1.8); rim.position.set(-3, 2, -2); scene.add(rim)

    let mesh: THREE.SkinnedMesh | null = null
    let disposed = false
    const renderScene = () => {
      const control = controls.current
      const distance = baseDistance / Math.max(.5, control.zoom)
      camera.position.set(Math.sin(control.yaw) * distance, targetY + Math.sin(control.pitch) * distance * .6, Math.cos(control.yaw) * distance)
      camera.lookAt(0, targetY, 0)
      renderer.render(scene, camera)
      mount.dataset.renderCount = String(Number(mount.dataset.renderCount || "0") + 1)
    }
    const scheduler = createOnDemandRenderScheduler(renderScene)
    invalidateRef.current = scheduler.invalidate
    const loader = new MMDLoader()
    loader.load(modelUrl, loaded => {
      if (disposed) {
        loaded.geometry.dispose()
        const staleMaterials = Array.isArray(loaded.material) ? loaded.material : [loaded.material]
        staleMaterials.forEach(material => material.dispose())
        return
      }
      mesh = loaded
      const box = new THREE.Box3().setFromObject(mesh)
      const center = box.getCenter(new THREE.Vector3())
      mesh.position.set(-center.x, -box.min.y, -center.z)
      scene.add(mesh)
      mount.dataset.modelState = "ready"
      scheduler.invalidate()
    }, undefined, error => { mount.dataset.modelState = "error"; onError?.(error.message || "恋活 PMX 模型加载失败") })

    const resize = () => { const width = Math.max(1, mount.clientWidth), height = Math.max(1, mount.clientHeight); renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix(); scheduler.invalidate() }
    const observer = new ResizeObserver(resize); observer.observe(mount); resize()
    let pointerId = -1, x = 0, y = 0
    const down = (event: PointerEvent) => { pointerId = event.pointerId; x = event.clientX; y = event.clientY; renderer.domElement.setPointerCapture(pointerId) }
    const move = (event: PointerEvent) => { if (event.pointerId !== pointerId) return; controls.current.yaw -= (event.clientX - x) * .012; controls.current.pitch = THREE.MathUtils.clamp(controls.current.pitch + (event.clientY - y) * .008, -.55, .55); x = event.clientX; y = event.clientY; scheduler.invalidate() }
    const up = (event: PointerEvent) => { if (event.pointerId !== pointerId) return; if (renderer.domElement.hasPointerCapture(pointerId)) renderer.domElement.releasePointerCapture(pointerId); pointerId = -1 }
    renderer.domElement.addEventListener("pointerdown", down); renderer.domElement.addEventListener("pointermove", move); renderer.domElement.addEventListener("pointerup", up); renderer.domElement.addEventListener("pointercancel", up)
    const visibility = () => scheduler.setVisible(!document.hidden)
    document.addEventListener("visibilitychange", visibility)
    visibility()
    scheduler.invalidate()
    return () => { disposed = true; invalidateRef.current = () => {}; scheduler.dispose(); document.removeEventListener("visibilitychange", visibility); observer.disconnect(); renderer.domElement.removeEventListener("pointerdown", down); renderer.domElement.removeEventListener("pointermove", move); renderer.domElement.removeEventListener("pointerup", up); renderer.domElement.removeEventListener("pointercancel", up); if (mesh) { mesh.geometry.dispose(); const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]; materials.forEach(material => material.dispose()) }; renderer.dispose(); renderer.domElement.remove() }
  }, [modelUrl, cameraPreset, onError])
  return <div className={className} ref={mountRef} aria-label="恋活 PMX 教师模型预览" data-model-state="loading" />
}
