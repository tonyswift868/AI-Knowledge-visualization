import { expect, test } from '@playwright/test'

test('offline review starts, protects answers, and recovers after page close', async ({ page }) => {
  await page.goto('/review-demo.html')
  await expect(page.getByText('DEMO · 完全离线')).toBeVisible()
  await page.getByRole('button', { name: '创建离线复习活动' }).click()
  await page.getByRole('button', { name: '开始第 1 / 3 轮' }).click()

  await expect(page.getByText('第 1 / 3 轮')).toBeVisible()
  await expect(page.getByLabel('用自己的话回答')).toBeVisible()
  await expect(page.getByText('提交后参考答案')).toHaveCount(0)
  await expect(page.getByText('Provider calls').locator('strong')).toHaveText('0')

  await page.getByRole('button', { name: '给我一个提示' }).click()
  await expect(page.getByText('本地提示')).toBeVisible()
  await page.getByLabel('用自己的话回答').fill('故意错误')
  await page.getByRole('button', { name: '提交回答' }).click()
  await expect(page.getByRole('heading', { name: '依赖提示' })).toBeVisible()
  await expect(page.getByText('提交后参考答案')).toBeVisible()

  await page.reload()
  await expect(page.getByText('已从关闭页面前恢复')).toBeVisible()
  await expect(page.getByRole('heading', { name: '依赖提示' })).toBeVisible()
  await page.getByRole('button', { name: '下一题' }).click()
  await expect(page.getByLabel('用自己的话回答')).toBeVisible()
  await expect(page.getByText('提交后参考答案')).toHaveCount(0)
  await expect(page.getByText('Provider calls').locator('strong')).toHaveText('0')
})
