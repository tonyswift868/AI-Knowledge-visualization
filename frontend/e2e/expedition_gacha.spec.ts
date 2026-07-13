import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('gacha banners are server-described and history is readable', async ({ page }) => {
  await openExpedition(page)
  const banners = await apiJson<{ items: Array<{ banner_id: string; cost_single: number; cost_ten: number; pity_limit: number }> }>(page, '/api/expedition/gacha/banners')
  const history = await apiJson<{ items: Array<Record<string, unknown>> }>(page, '/api/expedition/gacha/history')
  expect(banners.items.length).toBeGreaterThanOrEqual(2)
  for (const banner of banners.items) {
    expect(banner.cost_ten).toBeGreaterThan(banner.cost_single)
    expect(banner.pity_limit).toBeGreaterThan(0)
  }
  expect(Array.isArray(history.items)).toBeTruthy()
  await page.getByRole('button', { name: '招募卡池', exact: true }).click()
  await expect(page.getByText('HERO SUMMON')).toBeVisible()
})
