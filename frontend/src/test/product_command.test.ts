import { afterEach, expect, it, vi } from 'vitest'
import { productFetch } from '../api/productCommand'

afterEach(() => vi.unstubAllGlobals())

it('reuses the logical command after a lost response, then starts a new command after ACK', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
    calls.push(new Headers(init.headers).get('X-MentorFlow-Command-ID')!)
    if (calls.length === 1) throw new TypeError('synthetic network loss')
    return new Response('{}', {status: 200})
  }))
  const init = {method: 'POST', body: JSON.stringify({explicitly_confirmed_by_user: true})}
  await expect(productFetch('/api/plan-product/previews/test/publish', init)).rejects.toThrow()
  await productFetch('/api/plan-product/previews/test/publish', init)
  await productFetch('/api/plan-product/previews/test/publish', init)
  expect(calls[0]).toBe(calls[1])
  expect(calls[2]).not.toBe(calls[1])
})

it('retains a pending server response and distinguishes changed input', async () => {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url, init) => {
    calls.push(new Headers(init.headers).get('X-MentorFlow-Command-ID')!)
    return new Response('{}', {status: calls.length === 1 ? 503 : 200})
  }))
  await productFetch('/api/product-settings/theme', {method: 'PUT', body: '{"value":"dark"}'})
  await productFetch('/api/product-settings/theme', {method: 'PUT', body: '{"value":"dark"}'})
  await productFetch('/api/product-settings/theme', {method: 'PUT', body: '{"value":"light"}'})
  expect(calls[0]).toBe(calls[1])
  expect(calls[2]).not.toBe(calls[1])
})
