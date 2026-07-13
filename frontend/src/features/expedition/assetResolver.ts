import type { AssetKind, ExpeditionAsset, Hero } from "./api"

export function fallbackAssetUrl(heroId: string, kind: AssetKind) {
  const filename = kind === "skill_cutin" ? "skill_cut_in" : kind
  return `/assets/expedition/heroes/${heroId}/${filename}.svg`
}

const fallback = (heroId: string, kind: AssetKind): ExpeditionAsset => ({
  asset_id: `hero:${heroId}:${kind}`,
  url: fallbackAssetUrl(heroId, kind),
  width: 0,
  height: 0,
  focal_point: { x: 0.5, y: 0.4 },
  scale: 1,
  offset: { x: 0, y: 0 },
  fallback_used: true,
  version: "fallback",
})

/** One resolver for every React surface and the Phaser adapter. */
export function resolveExpeditionAsset(hero: Pick<Hero, "hero_id" | "assets"> | undefined, kind: AssetKind): ExpeditionAsset {
  if (!hero) return fallback("jinxiao", kind)
  return hero.assets?.[kind] || fallback(hero.hero_id, kind)
}

export function withAssetFallback(heroId: string, kind: AssetKind, asset: ExpeditionAsset | undefined): ExpeditionAsset {
  return asset || fallback(heroId, kind)
}
