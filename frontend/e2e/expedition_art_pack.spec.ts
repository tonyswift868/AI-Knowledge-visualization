import path from 'node:path'
import { test, expect } from '@playwright/test'
import { apiJson, getHeroes, openExpedition } from './expedition.helpers'
import { makeDevArtPack } from './expedition.art-fixture'

test('art review page keeps fallback explicit and exposes all 24 hero bindings', async ({ page }) => {
  await openExpedition(page)
  const heroes = await getHeroes(page)
  expect(heroes.items).toHaveLength(24)
  const active = await apiJson<{ active: unknown }>(page, '/api/expedition/art-packs/active')
  if (!active.active) expect(heroes.items.every(hero => Object.values(hero.assets).every(asset => asset.fallback_used === true && asset.version === 'fallback'))).toBeTruthy()
  await page.getByRole('button', { name: '美术审核', exact: true }).click()
  await expect(page.getByTestId('art-review-diagnostics')).toBeVisible()
  await expect(page.getByText('下载审核报告 JSON')).toBeVisible()
  await expect(page.getByText(/当前使用 SVG fallback|当前 Active/)).toBeVisible()
})

test('DEV ART PACK FIXTURE covers preview rejection, install, activate, resolver, and rollback', async ({ page }) => {
  test.setTimeout(120000)
  await openExpedition(page)
  const heroes = await getHeroes(page)
  const heroIds = heroes.items.map(hero => hero.hero_id)
  const preview = async (buffer: Buffer) => {
    const response = await page.request.post('/api/expedition/art-packs/preview', { multipart: { file: { name: 'DEV_ART_PACK_FIXTURE.zip', mimeType: 'application/zip', buffer } } })
    return { response, body: await response.json() }
  }
  const missing = await preview(makeDevArtPack(heroIds, '1.0.0-missing', { missing: `${heroIds[0]}:portrait` }))
  expect(missing.response.status()).toBe(400)
  const badHash = await preview(makeDevArtPack(heroIds, '1.0.0-bad-hash', { badHash: `heroes/${heroIds[0]}/portrait.png` }))
  expect(badHash.response.ok()).toBeTruthy()
  expect(badHash.body.valid).toBe(false)
  const traversal = await preview(makeDevArtPack(heroIds, '1.0.0-traversal', { traversal: true }))
  expect(traversal.response.status()).toBe(400)

  const firstVersion = `1.0.${Date.now()}`
  const firstPreview = await preview(makeDevArtPack(heroIds, firstVersion))
  expect(firstPreview.response.ok()).toBeTruthy()
  expect(firstPreview.body.valid).toBe(true)
  const installed = await apiJson<{ item: { pack_id: string; version: string } }>(page, '/api/expedition/art-packs/install', { method: 'POST', data: { preview_id: firstPreview.body.preview_id, confirm: true } })
  for (const heroId of heroIds) for (const kind of ['portrait', 'avatar', 'skill_cutin', 'skill_icon']) await apiJson(page, `/api/expedition/art-packs/${installed.item.pack_id}/${installed.item.version}/reviews/${heroId}/${kind}`, { method: 'POST', data: { review_status: 'APPROVED' } })
  await apiJson(page, `/api/expedition/art-packs/${installed.item.pack_id}/${installed.item.version}/activate`, { method: 'POST', data: {} })
  const activeFirst = await apiJson<{ active: { version: string } }>(page, '/api/expedition/art-packs/active')
  expect(activeFirst.active.version).toBe(firstVersion)
  const resolved = await apiJson<{ fallback_used: boolean; assets: { avatar: { url: string; version: string } } }>(page, `/api/expedition/heroes/${heroIds[0]}/assets`)
  expect(resolved.fallback_used).toBe(false)
  expect(resolved.assets.avatar.version).toBe(firstVersion)
  expect((await page.request.get(resolved.assets.avatar.url)).ok()).toBeTruthy()

  const secondVersion = `2.0.${Date.now()}`
  const secondPreview = await preview(makeDevArtPack(heroIds, secondVersion))
  const installedSecond = await apiJson<{ item: { pack_id: string; version: string } }>(page, '/api/expedition/art-packs/install', { method: 'POST', data: { preview_id: secondPreview.body.preview_id, confirm: true } })
  for (const heroId of heroIds) for (const kind of ['portrait', 'avatar', 'skill_cutin', 'skill_icon']) await apiJson(page, `/api/expedition/art-packs/${installedSecond.item.pack_id}/${installedSecond.item.version}/reviews/${heroId}/${kind}`, { method: 'POST', data: { review_status: 'APPROVED' } })
  await apiJson(page, `/api/expedition/art-packs/${installedSecond.item.pack_id}/${installedSecond.item.version}/activate`, { method: 'POST', data: {} })
  await apiJson(page, `/api/expedition/art-packs/${installedSecond.item.pack_id}/${installedSecond.item.version}/rollback`, { method: 'POST', data: {} })
  const afterRollback = await apiJson<{ active: { version: string } }>(page, '/api/expedition/art-packs/active')
  expect(afterRollback.active.version).toBe(firstVersion)
  await page.goto('/', { waitUntil: 'domcontentloaded', timeout: 15000 })
  await page.getByRole('button', { name: '学习远征', exact: true }).click()
  await page.getByRole('button', { name: '美术审核', exact: true }).click()
  await expect(page.getByTestId('art-review-diagnostics')).toBeVisible()
  await expect(page.locator('table.art-review-table tbody tr')).toHaveCount(24)
  await page.screenshot({ path: path.resolve(process.cwd(), '../artifacts/expedition_art_pipeline_v1_acceptance/art_review_page_screenshot.png'), fullPage: true })
})
