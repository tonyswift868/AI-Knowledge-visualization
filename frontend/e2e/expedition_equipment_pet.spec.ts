import { test, expect } from '@playwright/test'
import { apiJson, openExpedition } from './expedition.helpers'

test('equipment and pet catalogs remain separate from hero assets', async ({ page }) => {
  await openExpedition(page)
  const equipment = await apiJson<{ definitions: Array<{ equipment_id: string }>; instances: Array<{ instance_id: string }> }>(page, '/api/expedition/equipment')
  const pets = await apiJson<{ definitions: Array<{ pet_id: string }>; owned: Array<{ pet_id: string }> }>(page, '/api/expedition/pets')
  expect(equipment.definitions.length).toBeGreaterThan(0)
  expect(equipment.instances.every(item => item.instance_id)).toBeTruthy()
  expect(pets.definitions.length).toBeGreaterThan(0)
  expect(pets.owned.every(item => item.pet_id)).toBeTruthy()
  await page.getByRole('button', { name: '装备与宠物', exact: true }).click()
  await expect(page.getByText('装备工坊')).toBeVisible()
  await expect(page.getByText('宠物图鉴')).toBeVisible()
})
