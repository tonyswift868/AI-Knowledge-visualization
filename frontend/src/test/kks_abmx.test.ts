import { describe, expect, it } from "vitest"
import * as THREE from "three"
import { evaluateKksAbmxTransform, type KksAbmxModifier } from "../features/teachers/kksAbmx"

const modifier = (overrides: Partial<KksAbmxModifier> = {}): KksAbmxModifier => ({
  bone_name: "cf_J_FaceBase",
  scale: [2, 3, 4],
  length: 1.5,
  position: [.1, .2, .3],
  rotation: [0, 0, 10],
  ...overrides,
})

describe("KKS ABMX transform contract", () => {
  it("applies scale, length, offset and Euler delta from the unmodified baseline", () => {
    const result = evaluateKksAbmxTransform({
      position: new THREE.Vector3(1, 2, 3),
      rotation: new THREE.Quaternion(),
      scale: new THREE.Vector3(.5, 1, 2),
    }, [modifier()])
    expect(result.position.toArray()).toEqual([1.6, 3.2, 4.8])
    expect(result.scale.toArray()).toEqual([1, 3, 8])
    expect(THREE.MathUtils.radToDeg(new THREE.Euler().setFromQuaternion(result.rotation, "ZXY").z)).toBeCloseTo(10)
  })

  it("combines multiple effects without applying either modifier twice", () => {
    const baseline = {
      position: new THREE.Vector3(1, 0, 0),
      rotation: new THREE.Quaternion(),
      scale: new THREE.Vector3(1, 1, 1),
    }
    const result = evaluateKksAbmxTransform(baseline, [
      modifier({ scale: [2, 1, 1], length: 2, position: [1, 0, 0], rotation: [0, 0, 5] }),
      modifier({ scale: [.5, 3, 1], length: .5, position: [2, 0, 0], rotation: [0, 0, 7] }),
    ])
    expect(result.position.toArray()).toEqual([4, 0, 0])
    expect(result.scale.toArray()).toEqual([1, 3, 1])
    expect(THREE.MathUtils.radToDeg(new THREE.Euler().setFromQuaternion(result.rotation, "ZXY").z)).toBeCloseTo(12)
    expect(baseline.position.toArray()).toEqual([1, 0, 0])
  })
})
