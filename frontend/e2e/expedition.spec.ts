import { test, expect } from '@playwright/test'
import { openExpedition } from './expedition.helpers'

test('expedition shell exposes navigation, fallback labeling, and the review page', async ({ page }) => {
  await openExpedition(page)
  await expect(page.getByTestId('expedition-page')).toBeVisible()
  await expect(page.getByText('学习水晶', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '美术审核', exact: true }).click()
  await expect(page.getByTestId('art-review-diagnostics')).toBeVisible()
  await expect(page.getByText(/当前使用 SVG fallback|当前 Active/)).toBeVisible()
})
