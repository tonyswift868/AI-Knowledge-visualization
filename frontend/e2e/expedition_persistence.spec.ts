import { test, expect } from '@playwright/test'
import { apiJson, getHeroes, openExpedition } from './expedition.helpers'

test('profile, hero resolver, and fallback state survive a reload', async ({ page }) => {
  await openExpedition(page)
  const before = await apiJson<{ profile_id: string; revision: number }>(page, '/api/expedition/profile')
  const heroes = await getHeroes(page)
  expect(heroes.items).toHaveLength(24)
  expect(heroes.items.every(hero => hero.assets.avatar.url && hero.assets.skill_cutin.url)).toBeTruthy()
  await page.reload()
  const after = await apiJson<{ profile_id: string; revision: number }>(page, '/api/expedition/profile')
  expect(after.profile_id).toBe(before.profile_id)
  expect(after.revision).toBeGreaterThanOrEqual(before.revision)
})
