import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('infinite stage definitions remain deterministic at checkpoints', async ({ page }) => {
  await openExpedition(page)
  const stages = await Promise.all([1, 10, 50, 100, 300].map(number => apiJson<{ stage_number: number; seed: number; difficulty: { hp: number; atk: number }; boss?: unknown }>(page, `/api/expedition/stages/${number}`)))
  expect(stages.map(stage => stage.stage_number)).toEqual([1, 10, 50, 100, 300])
  expect(new Set(stages.map(stage => stage.seed)).size).toBe(5)
  expect(stages[4].difficulty.hp).toBeGreaterThan(stages[0].difficulty.hp)
  expect(stages[1].boss).toBeTruthy()
})
