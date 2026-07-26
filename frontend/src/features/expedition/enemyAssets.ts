import type { ExpeditionAsset } from "./api"

const enemyPortraits = new Set(["row_crossbow", "poison_needle", "field_medic", "counter_guard", "bomb_golem"])

export function enemyBattlePortraitUrl(enemyId: string) {
  return enemyPortraits.has(enemyId) ? `/assets/expedition/enemies/${enemyId}/battle_portrait-v2.png` : ""
}

export function enemyBattleAsset(enemyId: string): ExpeditionAsset | null {
  const url = enemyBattlePortraitUrl(enemyId)
  if (!url) return null
  return {
    asset_id: `enemy:${enemyId}:battle_portrait`,
    url,
    width: 1024,
    height: 1536,
    focal_point: { x: 0.5, y: 0.45 },
    scale: 1,
    offset: { x: 0, y: 0 },
    fallback_used: false,
    version: "enemy-art-v2",
  }
}
