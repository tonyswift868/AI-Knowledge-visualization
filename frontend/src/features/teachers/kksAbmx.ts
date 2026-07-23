import * as THREE from "three"

export type KksAbmxVec3 = [number, number, number]

/** Clean-room representation of one active-coordinate ABMX bone modifier. */
export type KksAbmxModifier = {
  bone_name: string
  bone_location?: number
  scale: KksAbmxVec3
  length: number
  position: KksAbmxVec3
  rotation: KksAbmxVec3
}

export type KksAbmxBaseline = {
  position: THREE.Vector3
  rotation: THREE.Quaternion
  scale: THREE.Vector3
}

const finite = (value: unknown, fallback: number) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

/**
 * Evaluate ABMX once from an unmodified local-transform baseline.
 * Multiple effects use ABMX's public composition contract: scale/length
 * multiply while position/rotation add.
 */
export function evaluateKksAbmxTransform(
  baseline: KksAbmxBaseline,
  modifiers: readonly KksAbmxModifier[],
): KksAbmxBaseline {
  const scale = new THREE.Vector3(1, 1, 1)
  const position = new THREE.Vector3()
  const rotation = new THREE.Vector3()
  let length = 1
  modifiers.forEach(modifier => {
    scale.multiply(new THREE.Vector3(
      finite(modifier.scale?.[0], 1),
      finite(modifier.scale?.[1], 1),
      finite(modifier.scale?.[2], 1),
    ))
    length *= finite(modifier.length, 1)
    position.add(new THREE.Vector3(
      finite(modifier.position?.[0], 0),
      finite(modifier.position?.[1], 0),
      finite(modifier.position?.[2], 0),
    ))
    rotation.add(new THREE.Vector3(
      finite(modifier.rotation?.[0], 0),
      finite(modifier.rotation?.[1], 0),
      finite(modifier.rotation?.[2], 0),
    ))
  })
  const delta = new THREE.Quaternion().setFromEuler(new THREE.Euler(
    THREE.MathUtils.degToRad(rotation.x),
    THREE.MathUtils.degToRad(rotation.y),
    THREE.MathUtils.degToRad(rotation.z),
    "ZXY",
  ))
  return {
    scale: baseline.scale.clone().multiply(scale),
    rotation: baseline.rotation.clone().multiply(delta),
    position: baseline.position.clone().multiplyScalar(length).add(position),
  }
}
