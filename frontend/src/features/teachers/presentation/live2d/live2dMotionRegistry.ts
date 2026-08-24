import type { Teacher2DManifest, TeacherPresentationState } from "../teacherPresentationTypes"
import {
  type Live2DModelCapabilities,
  type Live2DParameterCapability,
  registeredExpressionNames,
  registeredMotionNames,
} from "./live2dCapabilities"

export type Live2DActionSource = "registered-motion" | "registered-expression" | "internal-parameters"

export type Live2DActionImplementation = "registered-motion" | "registered-expression" | "internal-parameters"

export type Live2DActionCurvePoint = { at: number; value: number }
export type Live2DActionCurve = { parameterId: string; points: readonly Live2DActionCurvePoint[] }

export type Live2DSemanticAction = {
  id: string
  label: string
  semanticState?: TeacherPresentationState
  source: Live2DActionSource
  /** Stable inspector/provenance fields. */
  semanticRole: string
  sourceLicense: "model" | "imported-oss" | "retargeted"
  implementation: Live2DActionImplementation
  requiredParameters: readonly string[]
  requiredMotion?: { group: string; index: number; loop?: boolean; priority?: number; durationMs?: number; fadeInMs?: number; fadeOutMs?: number }
  priority: number
  interruptible: boolean
  cooldownMs: number
  durationMs?: number
  fallback?: string
  returnToIdle: boolean
  curves?: readonly Live2DActionCurve[]
  motion?: { group: string; index: number; loop?: boolean; priority?: number; durationMs?: number; fadeInMs?: number; fadeOutMs?: number }
  expression?: string
  /** Safe native parameter channels used by an internal action. */
  parameterIds: readonly string[]
  /** Runtime-facing availability.  Developer tooling must not infer this
   * from the mere presence of a registry row. */
  supported?: boolean
  supportReason?: string
  /** Curated, model-backed action intended for the native learner-facing
   * preview list.  The web inspector still retains the complete registry. */
  previewRecommended?: boolean
}

export type Live2DActionResolution = {
  requested: string
  action: Live2DSemanticAction
  fallback: boolean
  /** True when resolution intentionally returns an inert no-op action. */
  noop?: boolean
  reason?: string
}

export type Live2DMotionRegistry = {
  actions: readonly Live2DSemanticAction[]
  byId: ReadonlyMap<string, Live2DSemanticAction>
  resolve(requested: string): Live2DActionResolution
}

const semanticStates: readonly TeacherPresentationState[] = [
  "idle", "greeting", "thinking", "talking", "explaining", "questioning",
  "praising", "encouraging", "confused", "shy", "playful_annoyed", "ending", "error",
]

type ContractAction = {
  id: string
  role: string
  parameters: readonly string[]
  state?: TeacherPresentationState
  durationMs?: number
  returnToIdle?: boolean
}

/** The action vocabulary is intentionally model-agnostic.  An entry is only
 * exposed when this model has every required, non-physics channel. */
const contractActions: readonly ContractAction[] = [
  { id: "IDLE_SOFT", role: "idle", parameters: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBreath"], state: "idle", returnToIdle: false },
  { id: "ALERT", role: "alert", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen"] },
  { id: "BLINK", role: "blink", parameters: ["ParamEyeLOpen", "ParamEyeROpen"], durationMs: 500 },
  { id: "BREATH", role: "breath", parameters: ["ParamBreath"], durationMs: 2200, returnToIdle: true },
  { id: "LOOK_USER", role: "look_user", parameters: ["ParamEyeBallX", "ParamEyeBallY"] },
  { id: "LOOK_LEFT", role: "look_left", parameters: ["ParamEyeBallX", "ParamAngleX"], durationMs: 1250 },
  { id: "LOOK_RIGHT", role: "look_right", parameters: ["ParamEyeBallX", "ParamAngleX"], durationMs: 1250 },
  { id: "LOOK_UP", role: "look_up", parameters: ["ParamEyeBallY"] },
  { id: "LOOK_DOWN", role: "look_down", parameters: ["ParamEyeBallY"] },
  { id: "HEAD_TILT_LEFT", role: "head_tilt_left", parameters: ["ParamAngleZ"] },
  { id: "HEAD_TILT_RIGHT", role: "head_tilt_right", parameters: ["ParamAngleZ"] },
  { id: "NOD", role: "nod", parameters: ["ParamAngleY"], durationMs: 900 },
  { id: "SHAKE_HEAD", role: "shake_head", parameters: ["ParamAngleX"], durationMs: 900 },
  { id: "GREETING", role: "greeting", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm"], state: "greeting", durationMs: 1500 },
  { id: "FAREWELL", role: "farewell", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamMouthForm"], state: "ending", durationMs: 1500 },
  { id: "LISTEN", role: "listen", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen"], state: "thinking" },
  { id: "TALK_SOFT", role: "talk_soft", parameters: ["ParamAngleX", "ParamAngleY", "ParamMouthOpenY", "ParamMouthForm"], state: "talking", durationMs: 2200 },
  { id: "TALK_ACTIVE", role: "talk_active", parameters: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamMouthOpenY", "ParamMouthForm"], state: "explaining", durationMs: 2300 },
  { id: "THINK", role: "think", parameters: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm"], state: "thinking", durationMs: 1900 },
  { id: "QUESTION", role: "question", parameters: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamMouthX"], state: "questioning", durationMs: 1800 },
  { id: "EXPLAIN", role: "explain", parameters: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamMouthOpenY", "ParamMouthForm"], state: "explaining" },
  { id: "EXPLAIN_EMPHASIS", role: "explain_emphasis", parameters: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamMouthOpenY", "ParamMouthForm"], state: "explaining" },
  { id: "CONFIRM", role: "confirm", parameters: ["ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamMouthForm"], state: "praising" },
  { id: "CORRECT", role: "correct", parameters: ["ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamMouthForm"], state: "praising" },
  { id: "ENCOURAGE", role: "encourage", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm"], state: "encouraging" },
  { id: "PRAISE", role: "praise", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamMouthForm", "ParamCheekPuff"], state: "praising" },
  { id: "HAPPY", role: "happy", parameters: ["ParamAngleX", "ParamEyeLOpen", "ParamEyeROpen", "ParamMouthForm", "ParamCheekPuff"], state: "praising" },
  { id: "SURPRISED", role: "surprised", parameters: ["ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthOpenY"], state: "questioning" },
  { id: "SHY", role: "shy", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamMouthForm", "ParamCheekPuff"], state: "shy" },
  { id: "SERIOUS", role: "serious", parameters: ["ParamAngleX", "ParamAngleY", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm"], state: "error" },
  { id: "WINK", role: "wink", parameters: ["ParamEyeLOpen", "ParamEyeROpen"], durationMs: 550 },
]

// These are intentionally input/facial channels only.  Body/hand/eye
// expression channels are commonly physics destinations in user models and
// are therefore omitted unless the scanner proves they are safe.
const internalRecipe: Readonly<Record<TeacherPresentationState, readonly string[]>> = {
  loading: [],
  idle: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBreath"],
  greeting: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm"],
  thinking: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamMouthX"],
  talking: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamMouthOpenY", "ParamMouthForm", "ParamMouthX"],
  explaining: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthOpenY", "ParamMouthForm"],
  questioning: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamMouthX", "ParamMouthPucker"],
  praising: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthOpenY", "ParamMouthForm", "ParamCheekPuff"],
  encouraging: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthOpenY", "ParamMouthForm", "ParamCheekPuff"],
  confused: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamMouthX", "ParamMouthPucker"],
  shy: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamCheekPuff"],
  playful_annoyed: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamMouthX", "ParamMouthPucker", "ParamCheekPuff"],
  ending: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthOpenY", "ParamMouthForm"],
  error: ["ParamAngleX", "ParamAngleY", "ParamAngleZ", "ParamEyeLOpen", "ParamEyeROpen", "ParamBrowLY", "ParamBrowRY", "ParamMouthForm", "ParamMouthPucker"],
  hidden: [],
}

const defaultParameterRoles: Readonly<Record<string, string>> = {
  ParamAngleX: "angleX", ParamAngleY: "angleY", ParamAngleZ: "angleZ",
  ParamBodyAngleX: "bodyX", ParamBodyAngleY: "bodyY", ParamBodyAngleZ: "bodyZ",
  ParamEyeLOpen: "eyeLeftOpen", ParamEyeROpen: "eyeRightOpen",
  ParamEyeExpression1: "eyeExpression1", ParamEyeExpression2: "eyeExpression2",
  ParamMouthOpenY: "mouthOpen", ParamMouthForm: "mouthForm", ParamMouthX: "mouthX",
  MouthPucker: "mouthPucker", MouthShrug: "mouthShrug", JawOpen: "jawOpen",
  ParamBrowLY: "browLeftY", ParamBrowRY: "browRightY", ParamBrowLForm: "browLeftForm", ParamBrowRForm: "browRightForm",
  ParamEyeBrowX: "eyeBrowX", ParamCheekPuff: "cheekPuff", ParamBreath: "breath",
}

function parameterIdForCanonical(manifest: Teacher2DManifest, id: string): string {
  const configured = manifest.live2d?.parameters
  const role = defaultParameterRoles[id]
  return role && configured?.[role as keyof NonNullable<NonNullable<Teacher2DManifest["live2d"]>["parameters"]>] || id
}

function motionForAlias(alias: string | undefined, capabilities: Live2DModelCapabilities): Live2DSemanticAction["motion"] | undefined {
  if (!alias || !alias.startsWith("motion:")) return undefined
  const [, group, rawIndex] = alias.split(":")
  const index = Number(rawIndex)
  if (!group || !Number.isInteger(index)) return undefined
  const definition = capabilities.motionGroups[group]?.find(motion => motion.index === index)
  return definition
    ? {
      group,
      index,
      loop: definition.loop,
      priority: definition.priority,
      durationMs: definition.durationMs,
      fadeInMs: definition.fadeInMs,
      fadeOutMs: definition.fadeOutMs,
    }
    : undefined
}

function expressionForName(name: string | undefined, capabilities: Live2DModelCapabilities): string | undefined {
  if (!name) return undefined
  return capabilities.expressions.some(expression => expression.name === name) ? name : undefined
}

function makeInternalAction(
  manifest: Teacher2DManifest,
  state: TeacherPresentationState,
  safeIds: ReadonlySet<string>,
): Live2DSemanticAction | undefined {
  const required = internalRecipe[state].map(id => parameterIdForCanonical(manifest, id))
  // A recipe is a contract, not a best-effort suggestion. Partial coverage
  // would silently omit channels, so expose it only when every channel is
  // safe and present.
  if (!required.length || required.some(id => !safeIds.has(id))) return undefined
  const parameterIds = required
  return {
    id: state,
    label: state,
    semanticState: state,
    source: "internal-parameters",
    semanticRole: state,
    sourceLicense: "retargeted",
    implementation: "internal-parameters",
    requiredParameters: parameterIds,
    priority: state === "idle" ? 1 : 2,
    interruptible: true,
    cooldownMs: 0,
    durationMs: state === "idle" ? undefined : state === "talking" || state === "explaining" ? 2100 : 1800,
    fallback: "idle",
    returnToIdle: state !== "idle",
    curves: [],
    parameterIds,
  }
}

/**
 * Build the action registry after a native model has loaded.  Registered
 * motions/expressions win; only then do we expose a bounded internal-parameter
 * fallback.  Unknown action IDs are never silently turned into transforms.
 */
export function createLive2DMotionRegistry(
  manifest: Teacher2DManifest,
  capabilities: Live2DModelCapabilities,
): Live2DMotionRegistry {
  const safeIds = new Set(capabilities.safeParameterIds)
  const registeredMotions = new Set(registeredMotionNames(capabilities))
  const registeredExpressions = new Set(registeredExpressionNames(capabilities))
  const actions: Live2DSemanticAction[] = []
  const defaultNormalized = (id: string): number => {
    const parameter = capabilities.parameters.find(candidate => candidate.id === id)
    if (!parameter || parameter.maximum <= parameter.minimum) return .5
    return Math.max(0, Math.min(1, (parameter.defaultValue - parameter.minimum) / (parameter.maximum - parameter.minimum)))
  }
  const curvesFor = (action: Live2DSemanticAction): readonly Live2DActionCurve[] => {
    if (action.source === "registered-motion" && action.motion) return []
    const clamp = (value: number) => Math.max(0, Math.min(1, value))
    const points = (values: readonly number[]) => values.map((value, index) => ({ at: values.length === 1 ? 0 : index / (values.length - 1), value: clamp(value) }))
    return action.requiredParameters.map(parameterId => {
      const base = defaultNormalized(parameterId)
      let values: readonly number[] = [base, base, base]
      if (action.id === "BLINK" && /Eye[LR]Open/.test(parameterId)) values = [base, 0, base]
      else if (action.id === "WINK" && parameterId === "ParamEyeLOpen") values = [base, base, 0, 0, base]
      else if (action.id === "WINK" && parameterId === "ParamEyeROpen") values = [base, base, base, base, base]
      else if (action.id === "LOOK_LEFT" && parameterId === "ParamEyeBallX") values = [base, .08, .08, base]
      else if (action.id === "LOOK_LEFT" && parameterId === "ParamAngleX") values = [base, clamp(base - .2), clamp(base - .2), base]
      else if (action.id === "LOOK_RIGHT" && parameterId === "ParamEyeBallX") values = [base, .92, .92, base]
      else if (action.id === "LOOK_RIGHT" && parameterId === "ParamAngleX") values = [base, clamp(base + .2), clamp(base + .2), base]
      else if (action.id === "LOOK_UP" && parameterId === "ParamEyeBallY") values = [base, .85, base]
      else if (action.id === "LOOK_DOWN" && parameterId === "ParamEyeBallY") values = [base, .15, base]
      else if (action.id === "HEAD_TILT_LEFT" && parameterId === "ParamAngleZ") values = [base, clamp(base - .32), clamp(base - .32), base]
      else if (action.id === "HEAD_TILT_RIGHT" && parameterId === "ParamAngleZ") values = [base, clamp(base + .32), clamp(base + .32), base]
      else if (action.id === "NOD" && parameterId === "ParamAngleY") values = [base, clamp(base + .34), clamp(base - .18), clamp(base + .3), base]
      else if (action.id === "SHAKE_HEAD" && parameterId === "ParamAngleX") values = [base, clamp(base - .34), clamp(base + .34), clamp(base - .28), clamp(base + .28), base]
      else if (action.id === "BREATH" && parameterId === "ParamBreath") values = [.08, .92, .08]
      else if (action.id === "TALK_SOFT" && parameterId === "ParamMouthOpenY") values = [base, .42, .16, .5, .2, .38, base]
      else if (action.id === "TALK_SOFT" && parameterId === "ParamMouthForm") values = [base, clamp(base + .18), clamp(base + .12), clamp(base + .2), base]
      else if (action.id === "TALK_SOFT" && parameterId === "ParamAngleX") values = [base, clamp(base - .08), clamp(base + .08), base]
      else if (action.id === "TALK_SOFT" && parameterId === "ParamAngleY") values = [base, clamp(base + .08), base, clamp(base + .06), base]
      else if (action.id === "TALK_ACTIVE" && parameterId === "ParamMouthOpenY") values = [base, .68, .18, .8, .22, .7, base]
      else if (action.id === "TALK_ACTIVE" && parameterId === "ParamMouthForm") values = [base, clamp(base + .32), clamp(base + .18), clamp(base + .34), base]
      else if (action.id === "THINK" && parameterId === "ParamAngleX") values = [base, clamp(base + .16), clamp(base + .16), base]
      else if (action.id === "THINK" && parameterId === "ParamAngleY") values = [base, clamp(base + .12), clamp(base + .12), base]
      else if (action.id === "THINK" && /ParamBrow[LR]Y/.test(parameterId)) values = [base, clamp(base - .24), clamp(base - .24), base]
      else if (action.id === "QUESTION" && parameterId === "ParamAngleY") values = [base, clamp(base - .18), clamp(base - .18), base]
      else if (action.id === "QUESTION" && /ParamBrow[LR]Y/.test(parameterId)) values = [base, clamp(base + .3), clamp(base + .3), base]
      else if (action.id === "QUESTION" && parameterId === "ParamMouthX") values = [base, clamp(base + .26), clamp(base + .26), base]
      else if (action.id === "SURPRISED" && /Eye[LR]Open/.test(parameterId)) values = [base, 1, 1, base]
      else if (action.id === "SURPRISED" && /ParamBrow[LR]Y/.test(parameterId)) values = [base, 1, 1, base]
      else if (action.id === "SURPRISED" && parameterId === "ParamMouthOpenY") values = [base, .75, .75, base]
      else if (action.id === "ALERT" && /Eye[LR]Open/.test(parameterId)) values = [base, 1, 1, base]
      else if (["TALK_ACTIVE", "EXPLAIN", "EXPLAIN_EMPHASIS", "explaining", "talking"].includes(action.id) && parameterId === "ParamAngleX") values = [base, clamp(base - .22), clamp(base + .25), clamp(base - .14), base]
      else if (["TALK_ACTIVE", "EXPLAIN", "EXPLAIN_EMPHASIS", "explaining", "talking"].includes(action.id) && parameterId === "ParamAngleY") values = [base, clamp(base + .22), clamp(base - .12), clamp(base + .16), base]
      else if (["THINK", "thinking"].includes(action.id) && parameterId === "ParamAngleZ") values = [base, clamp(base + .28), clamp(base + .28), base]
      else if (["QUESTION", "questioning"].includes(action.id) && parameterId === "ParamAngleZ") values = [base, clamp(base - .25), clamp(base - .25), base]
      else if (["ALERT", "SURPRISED"].includes(action.id) && parameterId === "ParamAngleY") values = [base, clamp(base - .3), clamp(base - .18), base]
      else {
        // Angle inputs are the safe drivers for this model's native physics;
        // its body, hair and sleeve/hand physics visibly follow these curves.
        // Never write those physics destination parameters directly.
        const delta = /MouthOpenY/.test(parameterId) ? .34 : /Angle/.test(parameterId) ? .23 : /Brow/.test(parameterId) ? .18 : .13
        values = [base, clamp(base + delta), base]
      }
      return { parameterId, points: points(values) }
    })
  }

  for (const state of semanticStates) {
    const alias = manifest.animationAliases?.[state]
    const aliasExpression = alias?.startsWith("expression:") ? alias.slice("expression:".length) : alias
    const expressionName = expressionForName(manifest.live2d?.expressions?.[state], capabilities)
      || expressionForName(aliasExpression, capabilities)
    const motion = motionForAlias(alias, capabilities)
    const internal = makeInternalAction(manifest, state, safeIds)
    if (motion && registeredMotions.has(`motion:${motion.group}:${motion.index}`)) {
      actions.push({
        id: state,
        label: state,
        semanticState: state,
        source: "registered-motion",
        semanticRole: state,
        sourceLicense: "model",
        implementation: "registered-motion",
        requiredParameters: internal?.parameterIds || [],
        requiredMotion: motion,
        priority: state === "idle" ? (motion.priority ?? 1) : (motion.priority ?? 3),
        interruptible: true,
        cooldownMs: 0,
        durationMs: motion.durationMs,
        fallback: internal ? "idle" : undefined,
        returnToIdle: state !== "idle",
        motion,
        expression: expressionName,
        parameterIds: internal?.parameterIds || [],
      })
      continue
    }
    if (expressionName && registeredExpressions.has(`expression:${expressionName}`)) {
      actions.push({
        id: state,
        label: state,
        semanticState: state,
        source: "registered-expression",
        semanticRole: state,
        sourceLicense: "model",
        implementation: "registered-expression",
        requiredParameters: internal?.parameterIds || [],
        priority: state === "idle" ? 1 : 2,
        interruptible: true,
        cooldownMs: 0,
        fallback: internal ? "idle" : undefined,
        returnToIdle: state !== "idle",
        expression: expressionName,
        parameterIds: internal?.parameterIds || [],
      })
      continue
    }
    if (internal) actions.push(internal)
  }

  // Stable action-vocabulary rows are useful to the inspector and to tests,
  // but are only advertised when every requested parameter is truly safe.
  for (const definition of contractActions) {
    if (actions.some(action => action.id === definition.id)) continue
    const required = definition.parameters.map(id => parameterIdForCanonical(manifest, id)).filter(id => safeIds.has(id))
    if (required.length !== definition.parameters.length) continue
    const semantic = definition.state ? actions.find(action => action.id === definition.state) : undefined
    const implementation = semantic?.implementation || "internal-parameters"
    const source = semantic?.source || "internal-parameters"
    const expression = semantic?.expression
    const motion = semantic?.motion
    actions.push({
      id: definition.id,
      label: definition.id,
      semanticState: definition.state,
      source,
      semanticRole: definition.role,
      sourceLicense: semantic?.sourceLicense || "retargeted",
      implementation,
      requiredParameters: required,
      requiredMotion: semantic?.requiredMotion,
      priority: semantic?.priority || 2,
      interruptible: semantic?.interruptible ?? true,
      cooldownMs: semantic?.cooldownMs || 0,
      durationMs: definition.durationMs ?? semantic?.durationMs ?? 1800,
      fallback: semantic?.fallback || "idle",
      returnToIdle: definition.returnToIdle ?? semantic?.returnToIdle ?? true,
      motion,
      expression,
      parameterIds: required,
    })
  }

  // Expose every registered asset to the inspector, while keeping semantic
  // IDs stable and avoiding duplicate rows for aliases.
  for (const [group, motions] of Object.entries(capabilities.motionGroups)) {
    for (const motion of motions) {
      const id = `motion:${group}:${motion.index}`
      if (actions.some(action => action.id === id)) continue
      actions.push({
        id,
        label: id,
        source: "registered-motion",
        semanticRole: id,
        sourceLicense: "model",
        implementation: "registered-motion",
        requiredParameters: [],
        requiredMotion: motion,
        priority: motion.priority ?? 2,
        interruptible: true,
        cooldownMs: 0,
        returnToIdle: false,
        durationMs: motion.durationMs,
        motion,
        parameterIds: [],
      })
    }
  }
  for (const expression of capabilities.expressions) {
    const id = `expression:${expression.name}`
    if (actions.some(action => action.id === id)) continue
    actions.push({
      id,
      label: id,
      source: "registered-expression",
      semanticRole: id,
      sourceLicense: "model",
      implementation: "registered-expression",
      requiredParameters: [],
      priority: 2,
      interruptible: true,
      cooldownMs: 0,
      returnToIdle: false,
      expression: expression.name,
      parameterIds: [],
    })
  }

  // The supplied model's Cry motion animates tear physics only, while the
  // Cry expression changes the face.  Neither is a complete learner-facing
  // action by itself, so expose one honest composite and keep the raw assets
  // available only in the developer registry.
  const cryMotion = actions.find(action => action.id === "motion:Cry:0")
  const cryExpression = capabilities.expressions.some(expression => expression.name === "cry")
    ? "cry"
    : undefined
  if (cryMotion?.motion && cryExpression && !actions.some(action => action.id === "CRY")) {
    actions.push({
      ...cryMotion,
      id: "CRY",
      label: "CRY",
      semanticRole: "cry",
      expression: cryExpression,
      returnToIdle: true,
    })
  }

  const noHighlightsExpression = capabilities.expressions.some(expression => expression.name === "no-highlights")
    ? "no-highlights"
    : undefined
  const seriousAction = actions.find(action => action.id === "SERIOUS")
  if (seriousAction && noHighlightsExpression && !actions.some(action => action.id === "NO_HIGHLIGHTS")) {
    actions.push({
      ...seriousAction,
      id: "NO_HIGHLIGHTS",
      label: "NO_HIGHLIGHTS",
      semanticRole: "serious_no_highlights",
      expression: noHighlightsExpression,
      returnToIdle: true,
    })
  }

  const previewActionIds = new Set([
    "ALERT", "BLINK", "WINK", "BREATH",
    "LOOK_LEFT", "LOOK_RIGHT", "LOOK_UP", "LOOK_DOWN",
    "HEAD_TILT_LEFT", "HEAD_TILT_RIGHT", "NOD", "SHAKE_HEAD",
    "TALK_SOFT", "TALK_ACTIVE", "THINK", "QUESTION", "SURPRISED", "CRY", "NO_HIGHLIGHTS",
  ])
  const finalizedActions = actions.map(action => {
    const curves = curvesFor(action)
    const hasObservableCurve = curves.some(curve => {
      const values = curve.points.map(point => point.value)
      return values.length > 1 && Math.max(...values) - Math.min(...values) > .001
    })
    const supported = action.source === "registered-motion"
      || action.source === "registered-expression"
      || hasObservableCurve
    return {
      ...action,
      curves,
      supported,
      supportReason: supported ? undefined : "没有可观察的原生动作或安全参数曲线",
      previewRecommended: previewActionIds.has(action.id)
        || (action.id.startsWith("motion:") && action.id !== "motion:Cry:0")
        || (action.id.startsWith("expression:") && !["expression:cry", "expression:no-highlights"].includes(action.id)),
    }
  })
  const byId = new Map(finalizedActions.map(action => [action.id, action] as const))
  const idle = byId.get("idle") || actions.find(action => action.semanticState === "idle")
  return {
    actions: finalizedActions,
    byId,
    resolve(requested: string): Live2DActionResolution {
      const action = byId.get(requested)
      if (action) return { requested, action, fallback: false }
      if (!idle) {
        return {
          requested,
          action: {
            id: "idle",
            label: "idle",
            source: "internal-parameters",
            semanticRole: "idle",
            sourceLicense: "retargeted",
            implementation: "internal-parameters",
            requiredParameters: [],
            priority: 1,
            interruptible: true,
            cooldownMs: 0,
            returnToIdle: false,
            curves: [],
            parameterIds: [],
          },
          fallback: true,
          noop: true,
          reason: `未知动作：${requested}；当前模型没有可用的动作通道`,
        }
      }
      return { requested, action: idle, fallback: true, reason: `未知动作：${requested}` }
    },
  }
}

export function internalActionParameterIds(action: Live2DSemanticAction, capabilities: Live2DModelCapabilities): string[] {
  const safe = new Set(capabilities.safeParameterIds)
  return action.parameterIds.filter(id => safe.has(id))
}

export function semanticActionStates(): readonly TeacherPresentationState[] {
  return semanticStates
}

export const createLive2DActionRegistry = createLive2DMotionRegistry
export const resolveLive2DSemanticAction = (registry: Live2DMotionRegistry, requested: string): Live2DActionResolution => registry.resolve(requested)
