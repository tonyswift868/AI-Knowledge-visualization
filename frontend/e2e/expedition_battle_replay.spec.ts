import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('battle replay consumes persisted events and exposes playback controls', async ({ page }) => {
  await openExpedition(page)
  const stagesTab = page.getByRole('button', { name: '无限闯关', exact: true })
  await stagesTab.click()
  const startButton = page.getByRole('button', { name: '开始挑战', exact: true })
  // The expedition shell renders before its initial parallel load completes.
  // Wait for that load before issuing either the API fixture challenge or the
  // UI challenge; otherwise the button can still be disabled as “战斗写入中”.
  await expect(startButton).toBeVisible()
  await expect(startButton).toBeEnabled({ timeout: 30_000 })
  const challenge = await apiJson<{ battle_id: string; result: { winner: string; units: unknown[] }; event_count: number }>(page, '/api/expedition/stages/1/challenge', { method: 'POST', data: {} })
  const events = await apiJson<{ events: Array<{ event_type: string; numeric_values?: Record<string, number | string> }> }>(page, `/api/expedition/battles/${challenge.battle_id}/events`)
  expect(challenge.result.units.length).toBeGreaterThanOrEqual(5)
  expect(events.events.length).toBe(challenge.event_count)
  expect(events.events.some(event => event.event_type === 'DAMAGE')).toBeTruthy()
  // The initial API challenge is persisted separately from the UI flow. The
  // button must still be enabled before starting the visible replay challenge.
  await expect(startButton).toBeEnabled({ timeout: 30_000 })
  await startButton.click()
  await expect(page.getByRole('region', { name: '战斗回放' })).toBeVisible({ timeout: 15000 })
  // Wait for the write to settle before asserting playback controls. This
  // closes the race where the button still reads “战斗写入中”.
  await expect(startButton).toBeEnabled({ timeout: 30_000 })
  await expect(page.locator('.expedition-replay-loading')).toBeVisible()
  await expect(page.getByText(/预加载\s+\d+%/)).toBeVisible()
  await expect(page.getByRole('button', { name: '4x', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重播', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '跳过', exact: true })).toBeVisible()
})
