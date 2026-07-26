import { useEffect, useRef } from "react"
import * as THREE from "three"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm"
import type { KoikatsuAbmxModifier, KoikatsuAppearance, TeacherAvatarMorph } from "./api"
import { KoikatsuPmxRig, type NativeHeadRuntimeState, type RuntimeManifestPayload } from "./KoikatsuPmxRig"
import { KksGltfRig } from "./KksGltfRig"
import { isKksGltfRuntime } from "./kksGltfRuntime"

type Props = {
  modelUrl: string
  format?: "vrm" | "glb" | "pmx"
  runtimeMode?: "native-template" | "pmx-export" | "kkbp-web"
  motion: string
  morph: TeacherAvatarMorph
  cameraPreset?: "body" | "upper" | "face"
  cameraZoom?: number
  cameraYaw?: number
  cameraResetToken?: number
  extrasEnabled?: boolean
  extrasManifestUrl?: string
  extrasManifestPayload?: RuntimeManifestPayload
  nativeExport?: boolean
  nativeProfileKey?: string
  nativeBody?: Record<string, number>
  nativeBodyTypeBone?: number
  nativeAbmxModifiers?: KoikatsuAbmxModifier[]
  nativeFace?: Record<string, number>
  nativeHeadId?: number | null
  nativeAppearance?: KoikatsuAppearance
  neutralOnly?: boolean
  className?: string
  onError?: (message: string) => void
  onNativeHeadStateChange?: (state: NativeHeadRuntimeState) => void
  onCameraZoomChange?: (zoom: number) => void
}

const motionPose: Record<string, Record<string, [number, number, number]>> = {
  idle: {
    leftUpperArm: [0, 0, 1.25],
    rightUpperArm: [0, 0, -1.25],
    leftLowerArm: [0, 0, 0.08],
    rightLowerArm: [0, 0, -0.08],
  },
  explain: { rightUpperArm: [-0.2, 0.1, -0.85], rightLowerArm: [-0.25, 0.15, -0.45], head: [0.02, -0.1, 0.03] },
  question: { leftUpperArm: [-0.1, -0.05, 0.72], leftLowerArm: [-0.35, 0.1, 0.42], head: [0.05, 0.05, -0.12] },
  encourage: { leftUpperArm: [0.05, 0.05, 0.65], rightUpperArm: [0.05, -0.05, -0.65], head: [-0.04, 0, 0] },
  quiz: { rightUpperArm: [-0.25, 0.2, -0.62], rightLowerArm: [-0.55, 0, -0.34], head: [0.02, -0.08, 0.05] },
  celebrate: { leftUpperArm: [0.05, 0, 1.35], rightUpperArm: [0.05, 0, -1.35], leftLowerArm: [0, 0, 0.2], rightLowerArm: [0, 0, -0.2] },
  redirect: { leftUpperArm: [-0.2, 0, 0.48], rightUpperArm: [-0.2, 0, -0.48], head: [0.05, 0, 0] },
}

export function TeacherAvatarRig(props: Props) {
  const format = props.format ?? "vrm"
  if (format === "pmx") {
    return <KoikatsuPmxRig modelUrl={props.modelUrl} runtimeMode={props.runtimeMode === "kkbp-web" ? "pmx-export" : props.runtimeMode} morph={props.morph} nativeBody={props.nativeBody} nativeBodyTypeBone={props.nativeBodyTypeBone} nativeAbmxModifiers={props.nativeAbmxModifiers} nativeFace={props.nativeFace} nativeHeadId={props.nativeHeadId} nativeAppearance={props.nativeAppearance} cameraPreset={props.cameraPreset} cameraZoom={props.cameraZoom} cameraYaw={props.cameraYaw} cameraResetToken={props.cameraResetToken} extrasEnabled={props.extrasEnabled} extrasManifestUrl={props.extrasManifestUrl} extrasManifestPayload={props.extrasManifestPayload} nativeExport={props.nativeExport} nativeProfileKey={props.nativeProfileKey} neutralOnly={props.neutralOnly} className={props.className} onError={props.onError} onNativeHeadStateChange={props.onNativeHeadStateChange} onCameraZoomChange={props.onCameraZoomChange} />
  }
  if (isKksGltfRuntime(format, props.nativeExport, props.runtimeMode)) {
    return <KksGltfRig modelUrl={props.modelUrl} motion={props.motion} morph={props.morph} cameraPreset={props.cameraPreset} cameraZoom={props.cameraZoom} cameraYaw={props.cameraYaw} cameraResetToken={props.cameraResetToken} nativeBody={props.nativeBody} nativeBodyTypeBone={props.nativeBodyTypeBone} nativeFace={props.nativeFace} nativeHeadId={props.nativeHeadId} className={props.className} onError={props.onError} onNativeHeadStateChange={props.onNativeHeadStateChange} onCameraZoomChange={props.onCameraZoomChange} />
  }
  return <VrmTeacherAvatarRig {...props} />
}

/** Keep the VRM hooks in a stable component when the outer route changes. */
function VrmTeacherAvatarRig({ modelUrl, motion, morph, cameraPreset = "body", cameraZoom = 1, cameraYaw = 0, cameraResetToken = 0, className, onError }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const motionRef = useRef(motion)
  const morphRef = useRef(morph)
  const cameraRef = useRef({ zoom: cameraZoom, yaw: cameraYaw })
  const orbitRef = useRef({ yaw: 0, pitch: 0 })

  useEffect(() => { motionRef.current = motion }, [motion])
  useEffect(() => { morphRef.current = morph }, [morph])
  useEffect(() => { cameraRef.current = { zoom: cameraZoom, yaw: cameraYaw } }, [cameraZoom, cameraYaw])
  useEffect(() => { orbitRef.current = { yaw: 0, pitch: 0 } }, [cameraResetToken])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount || !modelUrl) return
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(cameraPreset === "face" ? 23 : 28, 1, 0.1, 30)
    if (cameraPreset === "face") camera.position.set(0, 1.58, 1.38)
    else if (cameraPreset === "upper") camera.position.set(0, 1.42, 2.2)
    else camera.position.set(0, 1.38, 3.2)
    const baseCameraY = camera.position.y
    const baseCameraZ = camera.position.z
    const cameraTargetY = cameraPreset === "face" ? 1.58 : cameraPreset === "upper" ? 1.32 : 1.05
    camera.position.z /= Math.max(.5, cameraZoom)
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.shadowMap.enabled = false
    mount.replaceChildren(renderer.domElement)
    renderer.domElement.style.touchAction = "none"
    mount.dataset.modelState = "loading"
    scene.add(new THREE.HemisphereLight(0xeef5ff, 0x1b1730, 2.1))
    const key = new THREE.DirectionalLight(0xfff0dc, 3.2)
    key.position.set(1.6, 2.8, 2.4)
    scene.add(key)
    const rim = new THREE.DirectionalLight(0x87c8ff, 2.2)
    rim.position.set(-2.4, 1.5, -1.5)
    scene.add(rim)

    let vrm: VRM | null = null
    let baseModelY = 0
    let frame = 0
    let disposed = false
    const resetView = () => { orbitRef.current = { yaw: 0, pitch: 0 } }
    const setViewZoom = (event: Event) => { const zoom = Number((event as CustomEvent<number>).detail); if (Number.isFinite(zoom)) cameraRef.current.zoom = zoom }
    window.addEventListener("teacher-avatar-reset-view", resetView)
    window.addEventListener("teacher-avatar-set-zoom", setViewZoom)
    const clock = new THREE.Clock()
    const rest = new Map<string, THREE.Quaternion>()
    const restScale = new Map<string, THREE.Vector3>()
    const loader = new GLTFLoader()
    loader.register(parser => new VRMLoaderPlugin(parser))
    loader.load(modelUrl, gltf => {
      if (disposed) { VRMUtils.deepDispose(gltf.scene); return }
      const loadedVrm = gltf.userData.vrm as VRM | undefined
      if (!loadedVrm) {
        onError?.("该模型没有 VRM 人形骨架，无法使用通用动作")
        VRMUtils.deepDispose(gltf.scene)
        return
      }
      vrm = loadedVrm
      mount.dataset.modelState = "ready"
      VRMUtils.removeUnnecessaryVertices(vrm.scene)
      VRMUtils.combineSkeletons(vrm.scene)
      vrm.scene.rotation.y = Math.PI + cameraYaw
      const initialMorph = morphRef.current
      vrm.scene.scale.set(1, initialMorph.height, 1)
      scene.add(vrm.scene)
      const box = new THREE.Box3().setFromObject(vrm.scene)
      const center = box.getCenter(new THREE.Vector3())
      vrm.scene.position.x -= center.x
      vrm.scene.position.y -= box.min.y
      baseModelY = vrm.scene.position.y
      for (const boneName of ["head", "upperChest", "chest", "spine", "hips", "leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm", "leftUpperLeg", "leftLowerLeg", "rightUpperLeg", "rightLowerLeg"] as const) {
        const bone = vrm.humanoid.getNormalizedBoneNode(boneName)
        if (bone) rest.set(boneName, bone.quaternion.clone())
        const rawBone = vrm.humanoid.getRawBoneNode(boneName)
        if (rawBone) restScale.set(boneName, rawBone.scale.clone())
      }
      applyBodyMorph(initialMorph, 1)
      vrm.expressionManager?.setValue("happy", initialMorph.smile)
    }, undefined, error => {
      if (disposed) return
      mount.dataset.modelState = "error"
      onError?.(error instanceof Error ? error.message : "VRM 模型加载失败")
    })

    const applyBodyMorph = (activeMorph: TeacherAvatarMorph, blend: number) => {
      const factors = new Map<string, THREE.Vector3>()
      const factorFor = (boneName: string) => {
        const existing = factors.get(boneName)
        if (existing) return existing
        const created = new THREE.Vector3(1, 1, 1)
        factors.set(boneName, created)
        return created
      }
      const multiplyChain = (boneNames: string[], x: number, y: number, z: number) => {
        const available = boneNames.filter(boneName => restScale.has(boneName))
        if (!available.length) return
        const count = available.length
        for (const boneName of available) {
          const factor = factorFor(boneName)
          factor.x *= Math.pow(x, 1 / count)
          factor.y *= Math.pow(y, 1 / count)
          factor.z *= Math.pow(z, 1 / count)
        }
      }
      const scaleBone = (boneName: string, x: number, y: number, z: number) => {
        const bone = vrm?.humanoid.getRawBoneNode(boneName as any)
        const base = restScale.get(boneName)
        if (!bone || !base) return
        bone.scale.x = THREE.MathUtils.lerp(bone.scale.x, base.x * x, blend)
        bone.scale.y = THREE.MathUtils.lerp(bone.scale.y, base.y * y, blend)
        bone.scale.z = THREE.MathUtils.lerp(bone.scale.z, base.z * z, blend)
      }
      multiplyChain(["head"], activeMorph.head_scale, activeMorph.head_scale, activeMorph.head_scale)
      // Depth is hard to read from the front camera, so give it a restrained
      // width component as well; this keeps a 10× setting visibly useful
      // without multiplying the full torso by 10 on every axis.
      const chestFrontScale = Math.sqrt(Math.max(0.1, activeMorph.upper_chest))
      multiplyChain(["spine", "chest", "upperChest"], activeMorph.shoulder_width * chestFrontScale, 1, activeMorph.upper_chest)
      multiplyChain(["spine"], activeMorph.waist_width, 1, activeMorph.waist_width)
      multiplyChain(["hips"], activeMorph.hip_width, 1, activeMorph.hip_width)
      multiplyChain(["leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm"], 1, activeMorph.arm_length, 1)
      multiplyChain(["leftUpperLeg", "leftLowerLeg", "rightUpperLeg", "rightLowerLeg"], 1, activeMorph.leg_length, 1)
      for (const [boneName, factor] of factors) scaleBone(boneName, factor.x, factor.y, factor.z)
    }

    const resize = () => {
      const width = Math.max(1, mount.clientWidth)
      const height = Math.max(1, mount.clientHeight)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(mount)
    resize()

    let pointerId = -1
    let pointerX = 0
    let pointerY = 0
    const onPointerDown = (event: PointerEvent) => {
      pointerId = event.pointerId
      pointerX = event.clientX
      pointerY = event.clientY
      renderer.domElement.setPointerCapture(pointerId)
    }
    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return
      orbitRef.current.yaw -= (event.clientX - pointerX) * 0.012
      orbitRef.current.pitch = THREE.MathUtils.clamp(orbitRef.current.pitch + (event.clientY - pointerY) * 0.008, -0.55, 0.55)
      pointerX = event.clientX
      pointerY = event.clientY
    }
    const onPointerEnd = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return
      if (renderer.domElement.hasPointerCapture(pointerId)) renderer.domElement.releasePointerCapture(pointerId)
      pointerId = -1
    }
    renderer.domElement.addEventListener("pointerdown", onPointerDown)
    renderer.domElement.addEventListener("pointermove", onPointerMove)
    renderer.domElement.addEventListener("pointerup", onPointerEnd)
    renderer.domElement.addEventListener("pointercancel", onPointerEnd)

    const animate = () => {
      frame = requestAnimationFrame(animate)
      const delta = Math.min(0.05, clock.getDelta())
      const elapsed = clock.elapsedTime
      if (vrm) {
        vrm.update(delta)
        const activeMotion = motionRef.current
        const activeMorph = morphRef.current
        const blend = 1 - Math.pow(0.001, delta)
        const distance = baseCameraZ / Math.max(.5, cameraRef.current.zoom)
        const orbit = orbitRef.current
        camera.position.x = Math.sin(orbit.yaw) * distance
        camera.position.y = baseCameraY + Math.sin(orbit.pitch) * distance * .65
        camera.position.z = Math.cos(orbit.yaw) * distance
        camera.lookAt(0, cameraTargetY, 0)
        vrm.scene.scale.y = THREE.MathUtils.lerp(vrm.scene.scale.y, activeMorph.height, blend)
        applyBodyMorph(activeMorph, blend)
        // Keep the model standing on its original floor level while it idles.
        // Without the base offset, the next animation frame overwrites the
        // normalization above and the character can appear sunken or cropped.
        vrm.scene.position.y = baseModelY + Math.sin(elapsed * 1.7) * 0.008
        vrm.scene.rotation.y = Math.PI + cameraRef.current.yaw + Math.sin(elapsed * 0.45) * 0.025
        const targetPose = { ...motionPose.idle, ...(motionPose[activeMotion] || {}) }
        for (const [boneName, initial] of rest.entries()) {
          const bone = vrm.humanoid.getNormalizedBoneNode(boneName as any)
          if (!bone) continue
          const rotation = targetPose[boneName] || [0, 0, 0]
          const pulse = activeMotion === "explain" && boneName === "rightLowerArm" ? Math.sin(elapsed * 2.2) * 0.08 : 0
          const deltaRotation = new THREE.Quaternion().setFromEuler(new THREE.Euler(rotation[0], rotation[1], rotation[2] + pulse, "XYZ"))
          const target = initial.clone().multiply(deltaRotation)
          bone.quaternion.slerp(target, blend)
        }
        const blink = Math.max(0, Math.sin(elapsed * 0.72 * Math.PI * 2) - 0.965) / 0.035
        vrm.expressionManager?.setValue("happy", activeMorph.smile)
        vrm.expressionManager?.setValue("blink", Math.min(1, blink) * activeMorph.blink)
      }
      renderer.render(scene, camera)
    }
    animate()

    return () => {
      disposed = true
      window.removeEventListener("teacher-avatar-reset-view", resetView)
      window.removeEventListener("teacher-avatar-set-zoom", setViewZoom)
      cancelAnimationFrame(frame)
      observer.disconnect()
      renderer.domElement.removeEventListener("pointerdown", onPointerDown)
      renderer.domElement.removeEventListener("pointermove", onPointerMove)
      renderer.domElement.removeEventListener("pointerup", onPointerEnd)
      renderer.domElement.removeEventListener("pointercancel", onPointerEnd)
      if (vrm) VRMUtils.deepDispose(vrm.scene)
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [modelUrl, cameraPreset, onError])

  return <div className={className} ref={mountRef} aria-label="VRM 教师模型预览" data-model-state="loading" />
}
