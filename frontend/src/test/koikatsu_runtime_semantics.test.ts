import { describe, expect, it } from "vitest"
import { isRuntimeClothingNodeVisible } from "../features/teachers/KoikatsuPmxRig"

const item = (property: string, stateNode = "", clothesStates = Array(9).fill(0)) => ({
  id: `${property}:${stateNode}`,
  source_kind: "clothes",
  source_property: `outfit0.ChaFileClothes.${property}`,
  stateNode,
  clothesStates,
})

describe("KKS runtime clothing state semantics", () => {
  it("keeps explicitly selected bra and shorts even when outer clothes exist", () => {
    expect(isRuntimeClothingNodeVisible(item("ClothesBra"))).toBe(true)
    expect(isRuntimeClothingNodeVisible(item("ClothesShorts"))).toBe(true)
  })

  it("selects canonical top state nodes from Status.clothesState", () => {
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_a"))).toBe(true)
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_b"))).toBe(false)
    const half = [1, 0, 0, 0, 0, 0, 0, 0, 0]
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_a", half))).toBe(false)
    expect(isRuntimeClothingNodeVisible(item("ClothesTop", "n_top_b", half))).toBe(true)
  })

  it("uses the bottom and underwear state slots required by ChaControl", () => {
    const states = [0, 1, 0, 2, 0, 0, 0, 0, 0]
    expect(isRuntimeClothingNodeVisible(item("ClothesBot", "n_bot_a", states))).toBe(false)
    expect(isRuntimeClothingNodeVisible(item("ClothesBot", "n_bot_b", states))).toBe(true)
    expect(isRuntimeClothingNodeVisible(item("ClothesShorts", "n_bot_b", states))).toBe(false)
    expect(isRuntimeClothingNodeVisible(item("ClothesShorts", "n_bot_c", states))).toBe(true)
  })
})
