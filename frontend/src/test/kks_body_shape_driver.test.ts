import { describe, expect, it } from 'vitest'
import nativeDriverJson from '../../../legacy/koikatsu-3d/resources/web/kks-body-shape-driver.json'
import {
  evaluateKksBodyAlwaysShape,
  evaluateKksBodyShape,
  KKS_BODY_ALWAYS_TARGET_BONES,
  KKS_BODY_TARGET_BONES,
  mergeKksLocalTransformPatch,
  type KksBodyShapeDriverData,
} from '../features/teachers/kksBodyShapeDriver'

const nativeDriver = nativeDriverJson as unknown as KksBodyShapeDriverData
const sliders = (value = 0.5) => Array(44).fill(value) as number[]

describe('native KKS female body shape driver', () => {
  it('evaluates every one of the 86 native destination bones without card-specific rules', () => {
    const result = evaluateKksBodyShape(nativeDriver, sliders())
    expect(Object.keys(result)).toEqual([...KKS_BODY_TARGET_BONES])
    expect(Object.keys(result)).toHaveLength(86)

    for (const patch of Object.values(result)) {
      expect([...patch.position, ...patch.rotation, ...patch.scale].some((component) => component !== null)).toBe(true)
      for (const component of [...patch.position, ...patch.rotation, ...patch.scale]) {
        if (component !== null) expect(Number.isFinite(component)).toBe(true)
      }
    }
  })

  it('matches the two supplemental shoulder roots from ShapeBodyInfoFemale.UpdateAlways', () => {
    expect(KKS_BODY_ALWAYS_TARGET_BONES).toEqual(['cf_d_shoulder_L', 'cf_d_shoulder_R'])
    expect(evaluateKksBodyAlwaysShape(nativeDriver, { typeBone: 0 })).toEqual({
      cf_d_shoulder_L: {
        position: [-0.01563369, null, null], rotation: [null, null, null], scale: [null, null, null],
      },
      cf_d_shoulder_R: {
        position: [0.01560147, null, null], rotation: [null, null, null], scale: [null, null, null],
      },
    })
    const corrected = evaluateKksBodyAlwaysShape(nativeDriver, { typeBone: 1 })
    expect(corrected.cf_d_shoulder_L.position[0]).toBe(nativeDriver.corrections[30].position[0])
    expect(corrected.cf_d_shoulder_R.position[0]).toBe(nativeDriver.corrections[31].position[0])
  })

  it('remains finite at both endpoints of all 44 card sliders', () => {
    for (const endpoint of [0, 1]) {
      const result = evaluateKksBodyShape(nativeDriver, sliders(endpoint))
      for (const patch of Object.values(result)) {
        for (const component of [...patch.position, ...patch.rotation, ...patch.scale]) {
          if (component !== null) expect(Number.isFinite(component)).toBe(true)
        }
      }
    }
  })

  it('matches SliderUnlocker extrapolation instead of clamping persisted values', () => {
    const below = sliders(); below[0] = -0.5
    const above = sliders(); above[0] = 1.5
    expect(evaluateKksBodyShape(nativeDriver, below).cf_n_height.scale[0]).toBeCloseTo(0.76)
    expect(evaluateKksBodyShape(nativeDriver, above).cf_n_height.scale[0]).toBeCloseTo(1.08)
  })

  it('uses the exported native animation curve rather than a hand-authored gain', () => {
    const low = sliders(); low[0] = 0
    const high = sliders(); high[0] = 1
    expect(evaluateKksBodyShape(nativeDriver, low).cf_n_height.scale).toEqual([
      0.8399999737739563, 0.8399999737739563, 0.8399999737739563,
    ])
    expect(evaluateKksBodyShape(nativeDriver, high).cf_n_height.scale).toEqual([1, 1, 1])
  })

  it('keeps the authored left/right breast root translations on opposite sides', () => {
    const result = evaluateKksBodyShape(nativeDriver, sliders())
    const left = result.cf_d_bust01_L.position[0]
    const right = result.cf_d_bust01_R.position[0]
    expect(left).not.toBeNull()
    expect(right).not.toBeNull()
    expect(left as number).toBeLessThan(0)
    expect(right as number).toBeGreaterThan(0)
    expect(Math.abs(left as number)).toBeCloseTo(Math.abs(right as number), 5)
  })

  it('matches Mathf.LerpAngle across the 360-degree boundary', () => {
    const keyframe = (rotation: number) => ({
      key: 0, position: [0, 0, 0] as const, rotation: [rotation, 0, 0] as const, scale: [1, 1, 1] as const,
    })
    const data: KksBodyShapeDriverData = {
      version: 1,
      sliderCount: 44,
      categories: [{ slider: 0, source: 'cf_a_berry', position: [false, false, false], rotation: [true, false, false], scale: [false, false, false] }],
      animation: { cf_a_berry: [keyframe(350), { ...keyframe(10), key: 1 }] },
      corrections: [],
    }
    const values = sliders(); values[0] = 0.5
    expect(evaluateKksBodyShape(data, values).cf_s_spine01.rotation[0]).toBeCloseTo(360)
  })

  it('preserves untouched source and destination axes as the game does', () => {
    const data: KksBodyShapeDriverData = {
      version: 1,
      sliderCount: 44,
      categories: [{ slider: 0, source: 'cf_a_height', position: [false, false, false], rotation: [false, false, false], scale: [true, false, false] }],
      animation: { cf_a_height: [{ key: 0, position: [9, 9, 9], rotation: [9, 9, 9], scale: [2, 3, 4] }] },
      corrections: [],
    }
    expect(evaluateKksBodyShape(data, sliders()).cf_n_height).toEqual({
      position: [null, null, null],
      rotation: [null, null, null],
      scale: [2, 1, 1],
    })
  })

  it('rejects truncated card parameter arrays instead of silently inventing values', () => {
    expect(() => evaluateKksBodyShape(nativeDriver, Array(43).fill(0.5))).toThrow(/Expected 44/)
  })

  it('merges partial native SetLocal calls without destroying bind-pose axes', () => {
    expect(mergeKksLocalTransformPatch(
      { position: [1, 2, 3], rotation: [4, 5, 6], scale: [7, 8, 9] },
      { position: [null, 20, null], rotation: [40, null, null], scale: [null, null, 90] },
    )).toEqual({ position: [1, 20, 3], rotation: [40, 5, 6], scale: [7, 8, 90] })
  })

  it('keeps the authored bind transform when a malformed extrapolation is non-finite', () => {
    expect(mergeKksLocalTransformPatch(
      { position: [1, 2, 3], rotation: [4, 5, 6], scale: [7, 8, 9] },
      { position: [Number.NaN, null, Number.POSITIVE_INFINITY], rotation: [null, Number.NEGATIVE_INFINITY, null], scale: [Number.NaN, 80, null] },
    )).toEqual({ position: [1, 2, 3], rotation: [4, 5, 6], scale: [7, 80, 9] })
  })
})
