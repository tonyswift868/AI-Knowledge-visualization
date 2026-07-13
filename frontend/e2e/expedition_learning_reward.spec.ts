import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('learning reward ledger and milestones expose auditable fields', async ({ page }) => {
  await openExpedition(page)
  const profile = await apiJson<{ training_exp: number; crystals: number; total_verified_minutes: number }>(page, '/api/expedition/profile')
  const ledger = await apiJson<{ items: Array<{ released_minutes: number; frozen_minutes: number }> }>(page, '/api/expedition/ledger')
  const milestones = await apiJson<{ items: Array<{ milestone_id: string; required_minutes: number; claimed: boolean }> }>(page, '/api/expedition/milestones')
  expect(profile).toEqual(expect.objectContaining({ training_exp: expect.any(Number), crystals: expect.any(Number), total_verified_minutes: expect.any(Number) }))
  expect(ledger.items.every(item => item.released_minutes >= 0 && item.frozen_minutes >= 0)).toBeTruthy()
  expect(milestones.items.map(item => item.milestone_id)).toEqual(expect.arrayContaining(['verified_60', 'verified_180']))
})
