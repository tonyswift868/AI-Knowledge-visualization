import { describe, expect, it } from 'vitest'
import { fallbackAssetUrl, resolveExpeditionAsset, withAssetFallback } from '../features/expedition/assetResolver'

describe('ExpeditionAssetResolver', () => {
  it('maps skill cut-ins to the existing fallback filename', () => {
    expect(fallbackAssetUrl('jinxiao', 'skill_cutin')).toBe('/assets/expedition/heroes/jinxiao/skill_cut_in.svg')
  })

  it('returns official metadata without rewriting the asset URL', () => {
    const official = { asset_id: 'art:v1:jinxiao:avatar', url: '/api/expedition/art-assets/a/file', width: 512, height: 512, focal_point: { x: 0.4, y: 0.5 }, scale: 1.1, offset: { x: 2, y: -1 }, fallback_used: false, version: '1.0.0' }
    expect(resolveExpeditionAsset({ hero_id: 'jinxiao', assets: { avatar: official } } as any, 'avatar')).toEqual(official)
  })

  it('returns a safe per-asset fallback when a hero or asset is missing', () => {
    const resolved = resolveExpeditionAsset(undefined, 'skill_icon')
    expect(resolved.fallback_used).toBe(true)
    expect(resolved.url).toContain('/skill_icon.svg')
    expect(withAssetFallback('leishan', 'portrait', undefined).version).toBe('fallback')
  })
})
