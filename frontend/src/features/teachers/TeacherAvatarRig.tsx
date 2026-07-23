import { useEffect, useRef } from "react"
import * as THREE from "three"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm"
import type { TeacherAvatarMorph } from "./api"

type Props = {
  modelUrl: string
  motion: string
  morph: TeacherAvatarMorph
  className?: string
  onError?: (message: string) => void
}

const motionPose: Record<string, Record<string, [number, number, number]>> = {
  idle: {},
  explain: { rightUpperArm: [-0.2, 0.1, -0.85], rightLowerArm: [-0.25, 0.15, -0.45], head: [0.02, -0.1, 0.03] },
  question: { leftUpperArm: [-0.1, -0.05, 0.72], leftLowerArm: [-0.35, 0.1, 0.42], head: [0.05, 0.05, -0.12] },
  encourage: { leftUpperArm: [0.05, 0.05, 0.65], rightUpperArm: [0.05, -0.05, -0.65], head: [-0.04, 0, 0] },
  quiz: { rightUpperArm: [-0.25, 0.2, -0.62], rightLowerArm: [-0.55, 0, -0.34], head: [0.02, -0.08, 0.05] },
  celebrate: { leftUpperArm: [0.05, 0, 1.35], rightUpperArm: [0.05, 0, -1.35], leftLowerArm: [0, 0, 0.2], rightLowerArm: [0, 0, -0.2] },
  redirect: { leftUpperArm: [-0.2, 0, 0.48], rightUpperArm: [-0.2, 0, -0.48], head: [0.05, 0, 0] },
}

export function TeacherAvatarRig({ modelUrl, motion, morph, className, onError }: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const mount = mountRef.current
    if (!mount || !modelUrl) return
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 30)
    camera.position.set(0, 1.38, 3.2)
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.shadowMap.enabled = false
    mount.replaceChildren(renderer.domElement)
    scene.add(new THREE.HemisphereLight(0xeef5ff, 0x1b1730, 2.1))
    const key = new THREE.DirectionalLight(0xfff0dc, 3.2)
    key.position.set(1.6, 2.8, 2.4)
    scene.add(key)
    const rim = new THREE.DirectionalLight(0x87c8ff, 2.2)
    rim.position.set(-2.4, 1.5, -1.5)
    scene.add(rim)

    let vrm: VRM | null = null
    let frame = 0
    let disposed = false
    const clock = new THREE.Clock()
    const rest = new Map<string, THREE.Quaternion>()
    const loader = new GLTFLoader()
    loader.register(parser => new VRMLoaderPlugin(parser))
    loader.load(modelUrl, gltf => {
      if (disposed) return
      vrm = gltf.userData.vrm as VRM
      VRMUtils.removeUnnecessaryVertices(vrm.scene)
      VRMUtils.combineSkeletons(vrm.scene)
      vrm.scene.rotation.y = Math.PI
      vrm.scene.scale.set(1, morph.height, 1)
      scene.add(vrm.scene)
      const box = new THREE.Box3().setFromObject(vrm.scene)
      const center = box.getCenter(new THREE.Vector3())
      vrm.scene.position.x -= center.x
      vrm.scene.position.y -= box.min.y
      for (const boneName of ["head", "upperChest", "leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm"] as const) {
        const bone = vrm.humanoid.getNormalizedBoneNode(boneName)
        if (bone) rest.set(boneName, bone.quaternion.clone())
      }
      const head = vrm.humanoid.getRawBoneNode("head")
      if (head) head.scale.setScalar(morph.head_scale)
      const chest = vrm.humanoid.getRawBoneNode("upperChest") || vrm.humanoid.getRawBoneNode("chest")
      if (chest) chest.scale.set(morph.shoulder_width, 1, morph.upper_chest)
      vrm.expressionManager?.setValue("happy", morph.smile)
    }, undefined, error => onError?.(error instanceof Error ? error.message : "VRM 模型加载失败"))

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

    const animate = () => {
      frame = requestAnimationFrame(animate)
      const delta = Math.min(0.05, clock.getDelta())
      const elapsed = clock.elapsedTime
      if (vrm) {
        vrm.update(delta)
        vrm.scene.position.y = Math.sin(elapsed * 1.7) * 0.008
        vrm.scene.rotation.y = Math.PI + Math.sin(elapsed * 0.45) * 0.025
        const targetPose = motionPose[motion] || motionPose.idle
        for (const [boneName, initial] of rest.entries()) {
          const bone = vrm.humanoid.getNormalizedBoneNode(boneName as any)
          if (!bone) continue
          const rotation = targetPose[boneName] || [0, 0, 0]
          const pulse = motion === "explain" && boneName === "rightLowerArm" ? Math.sin(elapsed * 2.2) * 0.08 : 0
          const deltaRotation = new THREE.Quaternion().setFromEuler(new THREE.Euler(rotation[0], rotation[1], rotation[2] + pulse, "XYZ"))
          const target = initial.clone().multiply(deltaRotation)
          bone.quaternion.slerp(target, 1 - Math.pow(0.001, delta))
        }
        const blink = Math.max(0, Math.sin(elapsed * 0.72 * Math.PI * 2) - 0.965) / 0.035
        vrm.expressionManager?.setValue("blink", Math.min(1, blink) * morph.blink)
      }
      renderer.render(scene, camera)
    }
    animate()

    return () => {
      disposed = true
      cancelAnimationFrame(frame)
      observer.disconnect()
      if (vrm) VRMUtils.deepDispose(vrm.scene)
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [modelUrl, motion, morph, onError])

  return <div className={className} ref={mountRef} aria-label="VRM 教师模型预览" />
}
