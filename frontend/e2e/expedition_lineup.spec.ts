import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('lineup contract keeps five stable slots and active state', async ({ page }) => {
  await openExpedition(page)
  const payload = await apiJson<{ items: Array<{ lineup_id: string; active: boolean; members: Array<{ slot_id: string; hero_id: string }> }> }>(page, '/api/expedition/lineups')
  expect(payload.items.length).toBeGreaterThan(0)
  const active = payload.items.find(item => item.active) || payload.items[0]
  expect(active.members).toHaveLength(5)
  expect(active.members.map(member => member.slot_id)).toEqual(['slot_1', 'slot_2', 'slot_3', 'slot_4', 'slot_5'])
  await page.getByRole('button', { name: '十字阵容', exact: true }).click()
  await expect(page.locator('.expedition-formation-slot')).toHaveCount(5)
})
