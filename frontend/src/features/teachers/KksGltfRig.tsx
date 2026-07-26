import { useEffect, useRef } from "react"
import * as THREE from "three"
import { OutlineEffect } from "three/examples/jsm/effects/OutlineEffect.js"
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js"
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js"
import type { TeacherAvatarMorph } from "./api"
import { KKS_NATURAL_ARM_POSE, type NativeHeadRuntimeState } from "./KoikatsuPmxRig"
import { unityEulerDegreesToQuaternion } from "./kksAbmx"
import {
  canonicalKksBoneName,
  evaluateKksBodyPatchSet,
  evaluateKksFacePatchSet,
  kksElbowCorrectionOffsets,
  kksExpressionWeight,
  kksOutlineProfile,
  loadBundledKksHeadShapeDriver,
  loadKksBodyShapeDriver,
  nextKksCameraZoom,
  relativeKksPatch,
  resolveBundledKksHeadProfile,
} from "./kksGltfRuntime"
import type { KksBodyShapeDriverData, KksLocalTransformPatch } from "./kksBodyShapeDriver"
import { resolveKksFaceBaseScale, type KksHeadShapeDriverData } from "./kksHeadShapeDriver"

type Props = {
  modelUrl: string
  motion: string
  morph: TeacherAvatarMorph
  cameraPreset?: "body" | "upper" | "face"
  cameraZoom?: number
  cameraYaw?: number
  cameraResetToken?: number
  nativeBody?: Record<string, number>
  nativeBodyTypeBone?: number
  nativeFace?: Record<string, number>
  nativeHeadId?: number | null
  className?: string
  onError?: (message: string) => void
  onNativeHeadStateChange?: (state: NativeHeadRuntimeState) => void
  onCameraZoomChange?: (zoom: number) => void
}

type BoneBaseline = {
  position: THREE.Vector3
  rotation: THREE.Quaternion
  scale: THREE.Vector3
}

function bakedMaterial(source: THREE.Material): THREE.Material {
  const input = source as THREE.MeshStandardMaterial
  const opacity = Number.isFinite(input.opacity) ? input.opacity : 1
  const isEyeOverlay = /eye-(?:left|right)/i.test(source.name)
  const isFaceOverlay = /face-(?:brow|nose-line|eye-line)/i.test(source.name)
  const isEyeWhite = /face-eye-white/i.test(source.name)
  const isCutout = isEyeOverlay || isFaceOverlay || isEyeWhite || /(?:atlas|hair)-alpha/i.test(source.name)
  const isAtlas = /atlas-(?:opaque|alpha)/i.test(source.name)
  // The combined Body alpha primitive contains sclera, brows and eyelines.
  // Its dark atlas is not one semantic shade layer: applying it to the whole
  // primitive turns the white of the eyes into a black mask. KKS also keeps
  // iris overlays self-lit, so only opaque skin and hair/clothes consume the
  // light/dark toon pair.
  const useDarkMap = !isEyeOverlay && !isFaceOverlay && !isEyeWhite && !/^Body .*atlas-alpha$/i.test(source.name)
  const darkMap = useDarkMap ? input.emissiveMap || null : null
  const material = new THREE.MeshToonMaterial({
    name: source.name,
    map: input.map || null,
    normalMap: input.normalMap || null,
    normalScale: input.normalScale?.clone() || new THREE.Vector2(1, 1),
    color: input.color?.clone() || new THREE.Color(0xffffff),
    opacity,
    // KKS hair, face lines and iris overlays are authored as cutouts. BLEND
    // makes their shared skinned mesh order-dependent and creates the white
    // "mask" artefact, so the Web profile uses deterministic alpha testing.
    transparent: false,
    // Some older KKBP GLBs labelled DITHERED atlas materials as OPAQUE.
    // Their transparent packing cells then render as black holes. Applying a
    // small alpha test to every atlas is backward-compatible for true opaque
    // maps and repairs those already-published GLBs without card-specific
    // texture edits.
    alphaTest: isEyeOverlay || isFaceOverlay ? .01 : isCutout || isAtlas ? .045 : 0,
    side: input.side,
    depthTest: input.depthTest,
    // KKS draws irises and facial ink over the underlying sclera/skin. Let
    // those overlays pass depth testing without claiming the depth buffer;
    // the sclera itself remains a normal depth-writing cutout.
    depthWrite: !(isEyeOverlay || isFaceOverlay),
    polygonOffset: isFaceOverlay,
    polygonOffsetFactor: isFaceOverlay ? -1 : 0,
    polygonOffsetUnits: isFaceOverlay ? -1 : 0,
    vertexColors: input.vertexColors,
    toneMapped: false,
  })
  if (material.map) material.map.colorSpace = THREE.SRGBColorSpace
  if (darkMap) darkMap.colorSpace = THREE.SRGBColorSpace
  if (material.normalMap) material.normalMap.colorSpace = THREE.NoColorSpace
  material.userData.kksDarkMap = darkMap
  material.userData.outlineParameters = kksOutlineProfile(source.name)
  material.onBeforeCompile = shader => {
    shader.uniforms.kksDarkMap = { value: darkMap || material.map }
    shader.uniforms.kksHasDarkMap = { value: darkMap ? 1 : 0 }
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <map_pars_fragment>",
        `#include <map_pars_fragment>
uniform sampler2D kksDarkMap;
uniform float kksHasDarkMap;`,
      )
      .replace(
        "#include <normal_fragment_maps>",
        `#include <normal_fragment_maps>
#ifdef USE_MAP
  vec3 kksLightColor = diffuseColor.rgb;
  vec3 kksDarkColor = texture2D( kksDarkMap, vMapUv ).rgb;
  float kksShade = smoothstep(
    0.08,
    0.42,
    dot( normalize( normal ), normalize( vec3( -0.35, 0.62, 0.70 ) ) )
  );
  diffuseColor.rgb = mix(
    kksLightColor,
    mix( kksDarkColor, kksLightColor, kksShade ),
    kksHasDarkMap
  );
#endif`,
      )
      .replace(
        "#include <lights_fragment_end>",
        `#include <lights_fragment_end>
reflectedLight.directDiffuse = diffuseColor.rgb;
reflectedLight.indirectDiffuse = vec3( 0.0 );`,
      )
  }
  material.customProgramCacheKey = () => `mentorflow-kks-toon-v1-${darkMap ? "dark" : "light"}`
  return material
}

export function KksGltfRig({
  modelUrl,
  motion,
  morph,
  cameraPreset = "body",
  cameraZoom = 1,
  cameraYaw = 0,
  cameraResetToken = 0,
  nativeBody,
  nativeBodyTypeBone,
  nativeFace,
  nativeHeadId,
  className,
  onError,
  onNativeHeadStateChange,
  onCameraZoomChange,
}: Props) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const bodyRef = useRef(nativeBody)
  const bodyTypeRef = useRef(nativeBodyTypeBone)
  const faceRef = useRef(nativeFace)
  const morphRef = useRef(morph)
  const cameraRef = useRef({ preset: cameraPreset, zoom: cameraZoom, yaw: cameraYaw })
  const orbitRef = useRef({ yaw: 0, pitch: 0 })
  const requestRenderRef = useRef<() => void>(() => undefined)
  const onCameraZoomChangeRef = useRef(onCameraZoomChange)
  const applyShapeRef = useRef<() => void>(() => undefined)
  const applyFaceRef = useRef<() => void>(() => undefined)
  const applyExpressionRef = useRef<() => void>(() => undefined)

  useEffect(() => { bodyRef.current = nativeBody; applyShapeRef.current() }, [nativeBody])
  useEffect(() => { bodyTypeRef.current = nativeBodyTypeBone; applyShapeRef.current() }, [nativeBodyTypeBone])
  useEffect(() => { faceRef.current = nativeFace; applyFaceRef.current() }, [nativeFace])
  useEffect(() => { morphRef.current = morph; applyExpressionRef.current() }, [morph])
  useEffect(() => { onCameraZoomChangeRef.current = onCameraZoomChange }, [onCameraZoomChange])
  useEffect(() => {
    cameraRef.current = { preset: cameraPreset, zoom: cameraZoom, yaw: cameraYaw }
    requestRenderRef.current()
  }, [cameraPreset, cameraZoom, cameraYaw])
  useEffect(() => {
    orbitRef.current = { yaw: 0, pitch: 0 }
    requestRenderRef.current()
  }, [cameraResetToken])
  useEffect(() => {
    const mount = mountRef.current
    if (!mount || !modelUrl) return
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(28, 1, .02, 100)
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" })
    } catch (error) {
      mount.dataset.modelState = "unavailable"
      onError?.(error instanceof Error ? error.message : "KKS GLB WebGL 初始化失败")
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    renderer.shadowMap.enabled = false
    const outlineEffect = new OutlineEffect(renderer, {
      defaultThickness: 0.00125,
      defaultColor: [0.07, 0.045, 0.08],
      defaultAlpha: 1,
      defaultKeepAlive: false,
    })
    renderer.domElement.style.touchAction = "none"
    mount.replaceChildren(renderer.domElement)
    mount.dataset.modelState = "loading"
    mount.dataset.runtimeMode = "kkbp-web"
    mount.dataset.renderScheduler = "event-driven"
    const headProfile = resolveBundledKksHeadProfile(nativeHeadId)
    const hasFaceValues = Object.keys(faceRef.current || {}).length > 0
    mount.dataset.faceShapeProfile = headProfile === null ? "unsupported" : String(headProfile)
    mount.dataset.faceShapeState = !hasFaceValues ? "no-card-values" : headProfile === null ? "partial" : "loading"
    if (headProfile === null && hasFaceValues) onNativeHeadStateChange?.({
      status: "degraded",
      reason: "KKBP Web GLB 已保留 52 项脸部参数接口，但当前导出尚未携带可验证的头型专属骨骼曲线。",
      fallbackApplied: false,
    })

    if (hasFaceValues && headProfile !== null) {
      onNativeHeadStateChange?.({ status: "loading", profile: String(headProfile) })
    }

    let root: THREE.Group | null = null
    let bodyDriver: KksBodyShapeDriverData | null = null
    let headDriver: KksHeadShapeDriverData | null = null
    let disposed = false
    let pendingFrame = 0
    let renderCount = 0
    let bounds = { center: new THREE.Vector3(0, 1, 0), height: 1.7, width: .7, depth: .5 }
    const bones = new Map<string, THREE.Object3D>()
    const baselines = new Map<THREE.Object3D, BoneBaseline>()
    const rootScaleBaseline = new THREE.Vector3(1, 1, 1)
    const baselineBody = Object.fromEntries(Object.entries(nativeBody || {}).map(([key, value]) => [key, Number(value)]))
    const baselineFace = Object.fromEntries(Object.entries(nativeFace || {}).map(([key, value]) => [key, Number(value)]))
    const baselineTypeBone = nativeBodyTypeBone
    const naturalPoseFallbacks: Record<string, string> = {
      cf_j_arm00_l: "cf_s_arm01_l",
      cf_j_forearm01_l: "cf_s_forearm01_l",
      cf_j_arm00_r: "cf_s_arm01_r",
      cf_j_forearm01_r: "cf_s_forearm01_r",
    }
    const blenderPoseAdapters: Record<string, { bone: string; rotation: [number, number, number] }> = {
      // GLTFLoader sanitizes Blender bone spaces to underscores.
      cf_j_arm00_l: { bone: "left_arm", rotation: [0, 0, -78] },
      cf_j_forearm01_l: { bone: "left_elbow", rotation: [0, 0, -6] },
      cf_j_arm00_r: { bone: "right_arm", rotation: [0, 0, 78] },
      cf_j_forearm01_r: { bone: "right_elbow", rotation: [0, 0, 6] },
    }

    const applyElbowCorrections = () => {
      let matches = 0
      ;([
        ["left", "left_elbow", -6],
        ["right", "right_elbow", 6],
      ] as const).forEach(([side, controllerName, bendDegrees]) => {
        const controller = bones.get(controllerName)
        if (!controller) return
        const controllerLength = controller.position.length()
        kksElbowCorrectionOffsets(
          side,
          THREE.MathUtils.degToRad(bendDegrees),
          controllerLength,
        ).forEach(({ bone: name, position }) => {
          const bone = bones.get(canonicalKksBoneName(name))
          const baseline = bone && baselines.get(bone)
          if (!bone || !baseline) return
          bone.position.copy(baseline.position).add(new THREE.Vector3(...position))
          matches += 1
        })
      })
      mount.dataset.jointCorrectionMatches = String(matches)
      mount.dataset.jointCorrectionRequired = "4"
      mount.dataset.jointCorrectionState = matches === 4 ? "ready" : "partial"
    }

    const applyNaturalPose = () => {
      let matches = 0
      KKS_NATURAL_ARM_POSE.forEach(({ bone: name, rotation }) => {
        const canonical = canonicalKksBoneName(name)
        const blenderAdapter = blenderPoseAdapters[canonical]
        const bone = bones.get(canonical)
          || (blenderAdapter && bones.get(blenderAdapter.bone))
          || bones.get(naturalPoseFallbacks[canonical])
        const baseline = bone && baselines.get(bone)
        if (!bone || !baseline) return
        // Simplification mode C removes KKBP's non-deforming cf_j arm
        // controllers but retains the corresponding cf_s deform bones. Rebase
        // before posing so repeated slider updates never accumulate rotation.
        bone.quaternion.copy(baseline.rotation)
        const delta = blenderAdapter && bones.get(blenderAdapter.bone) === bone
          ? new THREE.Quaternion().setFromEuler(new THREE.Euler(
              THREE.MathUtils.degToRad(blenderAdapter.rotation[0]),
              THREE.MathUtils.degToRad(blenderAdapter.rotation[1]),
              THREE.MathUtils.degToRad(blenderAdapter.rotation[2]),
              "XYZ",
            ))
          : unityEulerDegreesToQuaternion(rotation)
        bone.quaternion.multiply(delta)
        matches += 1
      })
      applyElbowCorrections()
      root?.updateMatrixWorld(true)
      const leftElbow = bones.get("left_elbow")?.getWorldPosition(new THREE.Vector3())
      const rightElbow = bones.get("right_elbow")?.getWorldPosition(new THREE.Vector3())
      if (leftElbow && rightElbow) {
        mount.dataset.armPoseSample = [...leftElbow.toArray(), ...rightElbow.toArray()]
          .map(value => value.toFixed(3))
          .join(",")
      }
      mount.dataset.naturalPoseMatches = String(matches)
      mount.dataset.naturalPose = matches === KKS_NATURAL_ARM_POSE.length ? "ready" : "partial"
    }

    const renderNow = () => {
      pendingFrame = 0
      if (disposed || document.hidden) return
      const control = cameraRef.current
      const preset = control.preset
      camera.fov = preset === "face" ? 22 : preset === "upper" ? 26 : 30
      const targetY = preset === "face"
        ? bounds.center.y + bounds.height * .32
        : preset === "upper"
          ? bounds.center.y + bounds.height * .17
          : bounds.center.y
      // Frame the requested portion from the actual perspective FOV. A radius
      // multiplier clips a 1.9 m character at 30 degrees even when its bounds
      // are correct; vertical and horizontal constraints both matter.
      // Include the authored hair silhouette. The previous .28/.62 crops
      // framed only to the skull/body bounds and cut roughly 5% off the head.
      const framingHeight = bounds.height * (preset === "face" ? .42 : preset === "upper" ? .74 : 1.08)
      const framingWidth = preset === "face"
        ? bounds.height * .34
        : Math.max(bounds.width, bounds.depth) * 1.12
      const verticalFov = THREE.MathUtils.degToRad(camera.fov)
      const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * Math.max(.1, camera.aspect))
      const verticalDistance = framingHeight / (2 * Math.tan(verticalFov / 2))
      const horizontalDistance = framingWidth / (2 * Math.tan(horizontalFov / 2))
      const distance = Math.max(.45, verticalDistance, horizontalDistance) / Math.max(.5, control.zoom)
      mount.dataset.cameraZoom = control.zoom.toFixed(3)
      const yaw = orbitRef.current.yaw
      const pitch = orbitRef.current.pitch
      camera.position.set(
        Math.sin(yaw) * distance,
        targetY + Math.sin(pitch) * distance,
        Math.cos(yaw) * distance,
      )
      camera.lookAt(0, targetY, 0)
      camera.updateProjectionMatrix()
      if (root) root.rotation.y = control.yaw
      outlineEffect.render(scene, camera)
      mount.dataset.renderCount = String(++renderCount)
    }
    const requestRender = () => {
      if (disposed || document.hidden || pendingFrame) return
      pendingFrame = requestAnimationFrame(renderNow)
    }
    requestRenderRef.current = requestRender

    const applyExpressions = () => {
      if (!root) return
      const smile = THREE.MathUtils.clamp(Number(morphRef.current?.smile || 0), 0, 1)
      const eyeOpen = THREE.MathUtils.clamp(Number(morphRef.current?.blink ?? 1), 0, 1)
      let smileMatches = 0
      let blinkMatches = 0
      root.traverse(node => {
        if (!(node instanceof THREE.Mesh) || !node.morphTargetDictionary || !node.morphTargetInfluences) return
        Object.entries(node.morphTargetDictionary).forEach(([name, index]) => {
          const weight = kksExpressionWeight(name, smile, eyeOpen)
          if (weight === null) return
          node.morphTargetInfluences![index] = weight
          if (weight > 0 && /笑顔/.test(name)) smileMatches += 1
          if (weight > 0 && /闭眼/.test(name)) blinkMatches += 1
        })
      })
      mount.dataset.expressionSample = `${smile.toFixed(2)},${eyeOpen.toFixed(2)}`
      mount.dataset.smileMorphMatches = String(smileMatches)
      mount.dataset.blinkMorphMatches = String(blinkMatches)
      requestRender()
    }
    applyExpressionRef.current = applyExpressions

    const applyFace = () => {
      if (!root || !bodyDriver || !headDriver || !faceRef.current) return
      const current = evaluateKksFacePatchSet(headDriver, faceRef.current)
      const authored = evaluateKksFacePatchSet(headDriver, baselineFace)
      const correctionY = Number(bodyDriver.corrections?.[2]?.scale?.[1] ?? 0)
      const headCorrectValue = bodyTypeRef.current === 1 && Number.isFinite(correctionY) && Math.abs(1 + correctionY) > 1e-8
        ? 1 / (1 + correctionY)
        : 1
      const resolveFaceBase = (patches: Record<string, KksLocalTransformPatch>) => {
        const faceBase = bones.get(canonicalKksBoneName("cf_J_FaceBase"))
        const patch = patches.cf_J_FaceBase
        const sourceScaleX = Number(patch?.scale?.[0])
        if (!faceBase?.parent || !patch || !Number.isFinite(sourceScaleX)) return patches
        faceBase.parent.updateWorldMatrix(true, false)
        const parentScale = faceBase.parent.getWorldScale(new THREE.Vector3())
        return {
          ...patches,
          cf_J_FaceBase: {
            ...patch,
            scale: resolveKksFaceBaseScale(
              sourceScaleX,
              [parentScale.x, parentScale.y, parentScale.z],
              headCorrectValue,
            ),
          },
        }
      }
      const resolvedCurrent = resolveFaceBase(current)
      const resolvedAuthored = resolveFaceBase(authored)
      const required = Object.keys(resolvedCurrent).length
      let matches = 0
      Object.entries(resolvedCurrent).forEach(([name, patch]) => {
        const bone = bones.get(canonicalKksBoneName(name))
        const baseline = bone && baselines.get(bone)
        const authoredPatch = resolvedAuthored[name]
        if (!bone || !baseline || !authoredPatch) return
        const relative = relativeKksPatch(patch, authoredPatch)
        bone.position.copy(baseline.position)
        bone.quaternion.copy(baseline.rotation)
        bone.scale.copy(baseline.scale)
        relative.position.forEach((value, axis) => {
          if (value !== null) bone.position.setComponent(axis, bone.position.getComponent(axis) + value)
        })
        bone.quaternion.multiply(unityEulerDegreesToQuaternion(relative.rotation.map(value => value ?? 0)))
        relative.scale.forEach((value, axis) => {
          if (value !== null) bone.scale.setComponent(axis, bone.scale.getComponent(axis) * value)
        })
        matches += 1
      })
      const state = matches === required ? "ready" : "partial"
      mount.dataset.faceShapeState = state
      mount.dataset.faceShapeMatches = String(matches)
      mount.dataset.faceShapeRequired = String(required)
      mount.dataset.faceBoneMatches = String(matches)
      mount.dataset.nativeFaceKeys = String(Object.keys(faceRef.current).length)
      mount.dataset.headCorrectValue = headCorrectValue.toFixed(6)
      if (state === "ready") {
        onNativeHeadStateChange?.({ status: "ready", profile: String(headProfile) })
      } else {
        onNativeHeadStateChange?.({
          status: "degraded",
          profile: String(headProfile),
          reason: `KKBP Web GLB matched only ${matches}/${required} face bones.`,
          fallbackApplied: false,
        })
      }
      requestRender()
    }
    applyFaceRef.current = applyFace

    const applyShape = () => {
      if (!bodyDriver || !root) return
      const current = evaluateKksBodyPatchSet(bodyDriver, bodyRef.current, bodyTypeRef.current)
      const authored = evaluateKksBodyPatchSet(bodyDriver, baselineBody, baselineTypeBone)
      const requiredMatches = Object.keys(current).length
      let matches = 0
      Object.entries(current).forEach(([name, patch]) => {
        const canonicalName = canonicalKksBoneName(name)
        const baselinePatch = authored[name]
        if (canonicalName === "cf_n_height" && root && baselinePatch) {
          const relative = relativeKksPatch(patch, baselinePatch)
          root.scale.copy(rootScaleBaseline)
          relative.scale.forEach((value, axis) => {
            if (value !== null) root!.scale.setComponent(axis, root!.scale.getComponent(axis) * value)
          })
          matches += 1
          return
        }
        const bone = bones.get(canonicalName)
        const baseline = bone && baselines.get(bone)
        if (!bone || !baseline || !baselinePatch) return
        const relative = relativeKksPatch(patch, baselinePatch)
        bone.position.copy(baseline.position)
        bone.quaternion.copy(baseline.rotation)
        bone.scale.copy(baseline.scale)
        relative.position.forEach((value, axis) => {
          if (value !== null) bone.position.setComponent(axis, bone.position.getComponent(axis) + value)
        })
        const rotation = relative.rotation.map(value => value ?? 0)
        bone.quaternion.multiply(unityEulerDegreesToQuaternion(rotation))
        relative.scale.forEach((value, axis) => {
          if (value !== null) bone.scale.setComponent(axis, bone.scale.getComponent(axis) * value)
        })
        matches += 1
      })
      mount.dataset.bodyShapeState = matches === requiredMatches ? "ready" : "partial"
      mount.dataset.boneMatches = String(matches)
      mount.dataset.bodyShapeRequiredMatches = String(requiredMatches)
      mount.dataset.nativeBodyKeys = String(Object.keys(bodyRef.current || {}).length)
      applyFace()
      applyNaturalPose()
      requestRender()
    }
    applyShapeRef.current = applyShape

    const loader = new GLTFLoader()
    loader.setMeshoptDecoder(MeshoptDecoder)
    loader.load(modelUrl, gltf => {
      if (disposed) return
      root = gltf.scene
      rootScaleBaseline.copy(root.scale)
      const replaced = new Map<THREE.Material, THREE.Material>()
      let darkMapCount = 0
      let normalMapCount = 0
      let outlineMaterialCount = 0
      root.traverse(node => {
        if (node instanceof THREE.Bone) {
          const key = canonicalKksBoneName(node.name)
          if (key && !bones.has(key)) bones.set(key, node)
          baselines.set(node, {
            position: node.position.clone(),
            rotation: node.quaternion.clone(),
            scale: node.scale.clone(),
          })
        }
        if (!(node instanceof THREE.Mesh)) return
        node.castShadow = false
        node.receiveShadow = false
        const replace = (material: THREE.Material) => {
          const cached = replaced.get(material)
          if (cached) return cached
          const created = bakedMaterial(material)
          replaced.set(material, created)
          if (created.userData.kksDarkMap) darkMapCount += 1
          if (created.userData.outlineParameters?.visible) outlineMaterialCount += 1
          if ((created as THREE.MeshToonMaterial).normalMap) normalMapCount += 1
          material.dispose()
          return created
        }
        node.material = Array.isArray(node.material) ? node.material.map(replace) : replace(node.material)
      })
      scene.add(root)
      const box = new THREE.Box3().setFromObject(root)
      const center = box.getCenter(new THREE.Vector3())
      root.position.x -= center.x
      root.position.y -= box.min.y
      root.position.z -= center.z
      const size = box.getSize(new THREE.Vector3())
      bounds = {
        center: new THREE.Vector3(0, size.y / 2, 0),
        height: Math.max(.1, size.y),
        width: Math.max(.1, size.x),
        depth: Math.max(.1, size.z),
      }
      applyNaturalPose()
      mount.dataset.modelState = "ready"
      mount.dataset.materialMode = "kks-light-dark-toon"
      mount.dataset.outlineMode = "event-driven-inverted-hull"
      mount.dataset.outlineMaterialCount = String(outlineMaterialCount)
      mount.dataset.darkMapCount = String(darkMapCount)
      mount.dataset.normalMapCount = String(normalMapCount)
      mount.dataset.motion = motion
      applyExpressions()
      applyShape()
      requestRender()
    }, undefined, error => {
      if (disposed) return
      mount.dataset.modelState = "error"
      onError?.(error instanceof Error ? error.message : "KKS GLB 模型加载失败")
    })
    void loadKksBodyShapeDriver().then(driver => {
      if (disposed) return
      bodyDriver = driver
      applyShape()
    }).catch(error => {
      if (disposed) return
      mount.dataset.bodyShapeState = "error"
      onError?.(error instanceof Error ? error.message : "KKS 身体驱动加载失败")
    })

    const headAbortController = new AbortController()
    if (headProfile !== null && hasFaceValues) {
      void loadBundledKksHeadShapeDriver(headProfile, headAbortController.signal).then(driver => {
        if (disposed || headAbortController.signal.aborted) return
        headDriver = driver
        mount.dataset.headShapeDriverVersion = String(driver.version)
        applyFace()
      }).catch(error => {
        if (disposed || headAbortController.signal.aborted) return
        mount.dataset.faceShapeState = "partial"
        onNativeHeadStateChange?.({
          status: "degraded",
          profile: String(headProfile),
          reason: error instanceof Error ? error.message : "KKS head shape driver unavailable",
          fallbackApplied: false,
        })
      })
    }

    const resize = () => {
      const width = Math.max(1, mount.clientWidth)
      const height = Math.max(1, mount.clientHeight)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      requestRender()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(mount)
    resize()
    const visibility = () => { if (!document.hidden) requestRender() }
    document.addEventListener("visibilitychange", visibility)
    let pointerId = -1
    let pointerX = 0
    let pointerY = 0
    const pointerDown = (event: PointerEvent) => {
      pointerId = event.pointerId
      pointerX = event.clientX
      pointerY = event.clientY
      renderer.domElement.setPointerCapture(pointerId)
    }
    const pointerMove = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return
      orbitRef.current.yaw -= (event.clientX - pointerX) * .012
      orbitRef.current.pitch = THREE.MathUtils.clamp(orbitRef.current.pitch + (event.clientY - pointerY) * .008, -.55, .55)
      pointerX = event.clientX
      pointerY = event.clientY
      requestRender()
    }
    const pointerEnd = (event: PointerEvent) => {
      if (event.pointerId !== pointerId) return
      if (renderer.domElement.hasPointerCapture(pointerId)) renderer.domElement.releasePointerCapture(pointerId)
      pointerId = -1
    }
    const wheel = (event: WheelEvent) => {
      event.preventDefault()
      event.stopPropagation()
      const next = nextKksCameraZoom(cameraRef.current.zoom, event.deltaY)
      cameraRef.current.zoom = next
      mount.dataset.cameraZoom = next.toFixed(3)
      onCameraZoomChangeRef.current?.(next)
      requestRender()
    }
    renderer.domElement.addEventListener("pointerdown", pointerDown)
    renderer.domElement.addEventListener("pointermove", pointerMove)
    renderer.domElement.addEventListener("pointerup", pointerEnd)
    renderer.domElement.addEventListener("pointercancel", pointerEnd)
    renderer.domElement.addEventListener("wheel", wheel, { passive: false })
    mount.addEventListener("wheel", wheel, { passive: false, capture: true })

    return () => {
      disposed = true
      headAbortController.abort()
      if (pendingFrame) cancelAnimationFrame(pendingFrame)
      observer.disconnect()
      document.removeEventListener("visibilitychange", visibility)
      renderer.domElement.removeEventListener("pointerdown", pointerDown)
      renderer.domElement.removeEventListener("pointermove", pointerMove)
      renderer.domElement.removeEventListener("pointerup", pointerEnd)
      renderer.domElement.removeEventListener("pointercancel", pointerEnd)
      renderer.domElement.removeEventListener("wheel", wheel)
      mount.removeEventListener("wheel", wheel, { capture: true })
      const geometries = new Set<THREE.BufferGeometry>()
      const materials = new Set<THREE.Material>()
      const textures = new Set<THREE.Texture>()
      root?.traverse(node => {
        if (!(node instanceof THREE.Mesh)) return
        geometries.add(node.geometry)
        const values = Array.isArray(node.material) ? node.material : [node.material]
        values.forEach(material => {
          materials.add(material)
          const toon = material as THREE.MeshToonMaterial
          const map = toon.map
          if (map) textures.add(map)
          if (toon.normalMap) textures.add(toon.normalMap)
          const darkMap = material.userData.kksDarkMap as THREE.Texture | undefined
          if (darkMap) textures.add(darkMap)
        })
      })
      geometries.forEach(value => value.dispose())
      materials.forEach(value => value.dispose())
      textures.forEach(value => value.dispose())
      renderer.dispose()
      renderer.forceContextLoss()
      if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement)
      requestRenderRef.current = () => undefined
      applyShapeRef.current = () => undefined
      applyFaceRef.current = () => undefined
      applyExpressionRef.current = () => undefined
    }
  }, [modelUrl, nativeHeadId])

  return <div ref={mountRef} className={className} aria-label="KKS Web GLB 角色预览" />
}
