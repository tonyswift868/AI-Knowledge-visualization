import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('lineup contract exposes a five-member selection on the accessible 3x3 grid', async ({ page }) => {
  await openExpedition(page)
  const payload = await apiJson<{
    items: Array<{ lineup_id: string; active: boolean; formation_type: string; members: Array<{ slot_id: string; hero_id: string }> }>
    formation: { type: string; cells: string[] }
  }>(page, '/api/expedition/lineups')
  expect(payload.items.length).toBeGreaterThan(0)
  const active = payload.items.find(item => item.active) || payload.items[0]
  expect(payload.formation.type).toBe('grid_3x3')
  expect(payload.formation.cells).toEqual(Array.from({ length: 9 }, (_, index) => `cell_${index}`))
  expect(active.formation_type).toBe('grid_3x3')
  expect(active.members).toHaveLength(5)
  const memberCells = active.members.map(member => member.slot_id)
  expect(new Set(memberCells).size).toBe(5)
  expect(memberCells.every(cell => payload.formation.cells.includes(cell))).toBeTruthy()
  await page.getByRole('button', { name: '九宫格阵容', exact: true }).click()
  await expect(page.locator('.expedition-formation-slot')).toHaveCount(9)
  await expect(page.getByRole('button', { name: /第 [1-3] 行 · 第 [1-3] 列/ })).toHaveCount(9)
})
