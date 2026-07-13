import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('battle replay consumes persisted events and exposes playback controls', async ({ page }) => {
  await openExpedition(page)
  const challenge = await apiJson<{ battle_id: string; result: { winner: string; units: unknown[] }; event_count: number }>(page, '/api/expedition/stages/1/challenge', { method: 'POST', data: {} })
  const events = await apiJson<{ events: Array<{ event_type: string; numeric_values?: Record<string, number | string> }> }>(page, `/api/expedition/battles/${challenge.battle_id}/events`)
  expect(challenge.result.units.length).toBeGreaterThanOrEqual(5)
  expect(events.events.length).toBe(challenge.event_count)
  expect(events.events.some(event => event.event_type === 'DAMAGE')).toBeTruthy()
  await page.getByRole('button', { name: '无限闯关', exact: true }).click()
  await expect(page.getByRole('button', { name: '开始挑战', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '开始挑战', exact: true }).click()
  await expect(page.getByRole('region', { name: '战斗回放' })).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('button', { name: '4x', exact: true })).toBeVisible()
  await expect(page.getByText('资源预加载')).toBeVisible()
})
