import { describe, expect, it } from 'vitest'
import nativeDriverJson from '../../public/assets/kks/kks-head-shape-driver-0.json'
import {
  evaluateKksHeadShape,
  KKS_HEAD_TARGET_BONES,
  type KksHeadShapeDriverData,
} from '../features/teachers/kksHeadShapeDriver'

const nativeDriver = nativeDriverJson as unknown as KksHeadShapeDriverData
const sliders = (value = 0.5) => Array(52).fill(value) as number[]

describe('native KKS female head shape driver', () => {
  it('evaluates all 59 original face destination bones', () => {
    const result = evaluateKksHeadShape(nativeDriver, sliders())
    expect(Object.keys(result)).toEqual([...KKS_HEAD_TARGET_BONES])
    expect(Object.keys(result)).toHaveLength(59)
    for (const patch of Object.values(result)) {
      expect([...patch.position, ...patch.rotation, ...patch.scale].some(component => component !== null)).toBe(true)
      for (const component of [...patch.position, ...patch.rotation, ...patch.scale]) {
        if (component !== null) expect(Number.isFinite(component)).toBe(true)
      }
    }
  })

  it('uses the extracted head animation curve for face width', () => {
    const low = sliders(); low[0] = 0
    const high = sliders(); high[0] = 1
    expect(evaluateKksHeadShape(nativeDriver, low).cf_J_FaceBase.scale).toEqual([0.949999988079071, 1, 1])
    expect(evaluateKksHeadShape(nativeDriver, high).cf_J_FaceBase.scale).toEqual([1.0499999523162842, 1, 1])
  })

  it('remains finite at both endpoints and rejects incomplete card values', () => {
    for (const endpoint of [0, 1]) {
      const result = evaluateKksHeadShape(nativeDriver, sliders(endpoint))
      for (const patch of Object.values(result)) {
        for (const component of [...patch.position, ...patch.rotation, ...patch.scale]) {
          if (component !== null) expect(Number.isFinite(component)).toBe(true)
        }
      }
    }
    expect(() => evaluateKksHeadShape(nativeDriver, Array(51).fill(0.5))).toThrow(/Expected 52/)
  })
})
