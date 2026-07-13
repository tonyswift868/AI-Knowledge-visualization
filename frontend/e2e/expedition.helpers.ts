import { expect, type Page } from '@playwright/test'

export async function apiJson<T>(page: Page, path: string, init?: Parameters<Page['request']['fetch']>[1]): Promise<T> {
  const response = await page.request.fetch(path, init)
  expect(response.ok(), `${init?.method || 'GET'} ${path}: ${await response.text()}`).toBeTruthy()
  return response.json() as Promise<T>
}
export async function openExpedition(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('button', { name: '学习远征', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '学习远征', exact: true }).click()
  await expect(page.getByTestId('expedition-page')).toBeVisible()
}

export async function getHeroes(page: Page) {
  return apiJson<{ items: Array<{ hero_id: string; owned: boolean; assets: Record<string, { url: string; fallback_used: boolean; version: string }> }> }>(page, '/api/expedition/heroes')
}
