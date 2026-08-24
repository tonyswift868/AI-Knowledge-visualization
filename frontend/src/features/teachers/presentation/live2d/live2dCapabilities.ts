/**
 * Runtime capability information for a Cubism model.
 *
 * A model's parameter list is not enough to tell us which parameters an
 * action may write: physics destinations are also parameters, but they are
 * owned by the Cubism physics solver.  The scanner keeps that distinction
 * explicit so action code can fail closed instead of accidentally fighting
 * physics.
 */

export type Live2DParameterCapability = {
  id: string
  minimum: number
  maximum: number
  defaultValue: number
  physicsOutput: boolean
}

export type Live2DMotionCapability = {
  group: string
  index: number
  file?: string
  loop?: boolean
  priority?: number
  durationMs?: number
  /** Optional model3 transition values, expressed in milliseconds. */
  fadeInMs?: number
  fadeOutMs?: number
}

export type Live2DMotionMetadata = {
  durationMs?: number
  loop?: boolean
}

export type Live2DExpressionCapability = {
  name: string
  file?: string
}

export type Live2DModelCapabilities = {
  parameters: readonly Live2DParameterCapability[]
  /** True only when the model's physics metadata was read successfully. */
  physicsKnown: boolean
  /** Parameter IDs written by physics destinations; never action targets. */
  physicsOutputParameterIds: readonly string[]
  /** Parameter IDs safe for internal action curves. */
  safeParameterIds: readonly string[]
  motionGroups: Readonly<Record<string, readonly Live2DMotionCapability[]>>
  expressions: readonly Live2DExpressionCapability[]
}

type AnyRecord = Record<string, unknown>

const asRecord = (value: unknown): AnyRecord | undefined =>
  value && typeof value === "object" ? value as AnyRecord : undefined

const asString = (value: unknown): string | undefined =>
  typeof value === "string" && value.length > 0 ? value : undefined

const asNumber = (value: unknown, fallback: number): number =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback

const optionalNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined

const arrayLike = (value: unknown): ArrayLike<unknown> | undefined => {
  if (Array.isArray(value)) return value
  if (value && typeof value === "object" && typeof (value as { length?: unknown }).length === "number") {
    return value as ArrayLike<unknown>
  }
  return undefined
}

function readPhysicsOutputIds(physics: unknown): { ids: Set<string>; known: boolean } {
  const result = new Set<string>()
  const root = asRecord(physics)
  const settings = root?.PhysicsSettings
  if (!Array.isArray(settings)) return { ids: result, known: false }
  for (const setting of settings) {
    const outputs = asRecord(setting)?.Output
    if (!Array.isArray(outputs)) return { ids: result, known: false }
    for (const output of outputs) {
      const destination = asRecord(asRecord(output)?.Destination)
      const id = asString(destination?.Id)
      if (!id) return { ids: result, known: false }
      result.add(id)
    }
  }
  return { ids: result, known: true }
}

function readParameters(parameters: unknown, cdi: unknown): Live2DParameterCapability[] {
  const result: Live2DParameterCapability[] = []
  const source = asRecord(parameters)
  const ids = Array.isArray(source?.ids) ? source.ids : undefined
  if (ids) {
    const minimum = arrayLike(source?.minimumValues)
    const maximum = arrayLike(source?.maximumValues)
    const defaults = arrayLike(source?.defaultValues)
    ids.forEach((value, index) => {
      const id = asString(value)
      if (!id) return
      result.push({
        id,
        minimum: asNumber(minimum?.[index], -1),
        maximum: asNumber(maximum?.[index], 1),
        defaultValue: asNumber(defaults?.[index], 0),
        physicsOutput: false,
      })
    })
    return result
  }

  // The cdi3 DisplayInfo is useful in tests and during a pre-load scan.  It
  // does not contain ranges, therefore the conservative [-1, 1] defaults.
  const cdiParameters = asRecord(cdi)?.Parameters
  if (!Array.isArray(cdiParameters)) return result
  for (const parameter of cdiParameters) {
    const id = asString(asRecord(parameter)?.Id)
    if (!id) continue
    result.push({ id, minimum: -1, maximum: 1, defaultValue: 0, physicsOutput: false })
  }
  return result
}

const MAX_MOTION_DURATION_SECONDS = 60 * 60

function boundedMotionDurationMs(value: unknown): number | undefined {
  const duration = optionalNumber(value)
  if (duration === undefined || duration <= 0 || duration > MAX_MOTION_DURATION_SECONDS) return undefined
  return duration * 1000
}

/**
 * Parse a motion3 file without interpreting curves or executing the motion.
 * Invalid/unavailable metadata intentionally returns undefined fields so the
 * caller cannot accidentally invent a duration or loop policy.
 */
export function parseLive2DMotionMetadata(input: unknown): Live2DMotionMetadata | undefined {
  const root = asRecord(input)
  // A motion3 document must carry its metadata under `Meta`; accepting
  // arbitrary root-level Duration/Loop fields would turn malformed files
  // into an invented capability.
  const meta = asRecord(root?.Meta)
  if (!meta) return undefined
  const durationMs = boundedMotionDurationMs(meta.Duration)
  const loop = typeof meta.Loop === "boolean" ? meta.Loop : undefined
  if (durationMs === undefined && loop === undefined) return undefined
  return { durationMs, loop }
}

function motionMetadataFor(metadata: unknown, file: string): Live2DMotionMetadata | undefined {
  if (metadata instanceof Map) return parseLive2DMotionMetadata(metadata.get(file))
  const record = asRecord(metadata)
  return parseLive2DMotionMetadata(record?.[file])
}

function readMotions(settings: unknown, metadata?: unknown): Readonly<Record<string, readonly Live2DMotionCapability[]>> {
  const motions = asRecord(asRecord(settings)?.FileReferences)?.Motions
  const result: Record<string, Live2DMotionCapability[]> = {}
  if (!motions || typeof motions !== "object") return result
  for (const [group, entries] of Object.entries(motions as AnyRecord)) {
    if (!Array.isArray(entries)) continue
    result[group] = entries.flatMap((entry, index) => {
      const definition = asRecord(entry)
      const file = asString(definition?.File)
      if (!file) return []
      const motionMetadata = motionMetadataFor(metadata, file)
      return [{
        group,
        index,
        file,
        // A valid motion3 Meta value is authoritative.  The model3 entry is
        // retained only as a bounded fallback when that metadata is absent.
        loop: motionMetadata?.loop ?? (typeof definition?.Loop === "boolean" ? definition.Loop : undefined),
        priority: optionalNumber(definition?.Priority),
        durationMs: motionMetadata?.durationMs
          ?? optionalNumber(definition?.DurationMs)
          ?? (optionalNumber(definition?.Duration) !== undefined ? optionalNumber(definition?.Duration)! * 1000 : undefined),
        fadeInMs: boundedMotionDurationMs(definition?.FadeInTime),
        fadeOutMs: boundedMotionDurationMs(definition?.FadeOutTime),
      }]
    })
  }
  return result
}

function readExpressions(settings: unknown): Live2DExpressionCapability[] {
  const expressions = asRecord(asRecord(settings)?.FileReferences)?.Expressions
  if (!Array.isArray(expressions)) return []
  return expressions.flatMap(entry => {
    const definition = asRecord(entry)
    const name = asString(definition?.Name)
    const file = asString(definition?.File)
    return name && file ? [{ name, file }] : []
  })
}

/**
 * Scan model3/cdi/physics data and, when available, the native core model
 * parameter arrays.  No network access occurs here; callers may pass data
 * from their own fetch/cache layer.
 */
export function scanLive2DModelCapabilities(input: {
  settings?: unknown
  parameters?: unknown
  physics?: unknown
  cdi?: unknown
  /** Optional map of motion File -> parsed/raw motion3 JSON metadata. */
  motionMetadata?: unknown
}): Live2DModelCapabilities {
  const physics = readPhysicsOutputIds(input.physics)
  const allParameters = readParameters(input.parameters, input.cdi).map(parameter => ({
    ...parameter,
    physicsOutput: physics.ids.has(parameter.id),
  }))
  const parameters = allParameters.filter(parameter => !parameter.physicsOutput)
  // Without a readable physics file we cannot know which parameters are
  // solver-owned.  Keep the parameter inventory for diagnostics, but expose
  // no internal-action write targets (fail closed).
  const safeParameterIds = physics.known ? parameters.map(parameter => parameter.id) : []
  return {
    parameters,
    physicsKnown: physics.known,
    physicsOutputParameterIds: [...physics.ids].sort(),
    safeParameterIds,
    motionGroups: readMotions(input.settings, input.motionMetadata),
    expressions: readExpressions(input.settings),
  }
}

export function registeredMotionNames(capabilities: Live2DModelCapabilities): string[] {
  return Object.values(capabilities.motionGroups).flatMap(group => group.map(motion => `motion:${motion.group}:${motion.index}`))
}

export function registeredExpressionNames(capabilities: Live2DModelCapabilities): string[] {
  return capabilities.expressions.map(expression => `expression:${expression.name}`)
}

export function isPhysicsOutputParameter(capabilities: Live2DModelCapabilities | undefined, id: string): boolean {
  return Boolean(capabilities?.physicsOutputParameterIds.includes(id))
}

// Naming aliases keep the capability boundary easy to discover for callers
// that use either the model-oriented or capability-oriented terminology.
export const scanLive2DCapabilities = scanLive2DModelCapabilities
export const buildLive2DModelCapabilities = scanLive2DModelCapabilities
