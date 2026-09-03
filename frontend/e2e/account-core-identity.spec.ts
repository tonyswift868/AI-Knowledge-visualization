import { createHash } from 'node:crypto'
import { expect, test, type BrowserContext } from '@playwright/test'

const issuer = 'https://identity.account-core.test'
const authorizationEndpoint = `${issuer}/protocol/openid-connect/auth`
const callback = 'http://127.0.0.1:43891/oauth/callback'
const clientId = 'mentorflow-desktop-qa'

type LoginFixture = {
  authorizationUrl: URL
  codeVerifier: string
  state: string
}

function codeChallenge(verifier: string): string {
  return createHash('sha256').update(verifier, 'ascii').digest('base64url')
}

async function installSyntheticIdentityProvider(
  context: BrowserContext,
  subject: string,
  fixture: LoginFixture,
): Promise<URL[]> {
  const observed: URL[] = []
  await context.route(`${issuer}/**`, async (route) => {
    const requested = new URL(route.request().url())
    observed.push(requested)
    const callbackUrl = new URL(callback)
    callbackUrl.searchParams.set('code', `synthetic-code-${subject}`)
    callbackUrl.searchParams.set('state', fixture.state)
    await route.fulfill({
      status: 200,
      headers: {
        'cache-control': 'no-store',
        'content-type': 'text/html; charset=utf-8',
        'set-cookie': `mf_qa_subject=${subject}; Path=/; HttpOnly; Secure; SameSite=Lax`,
      },
      body: `<!doctype html><meta charset="utf-8"><script>location.replace(${JSON.stringify(callbackUrl.toString())})</script>`,
    })
  })
  await context.route(`${callback}**`, async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        'cache-control': 'no-store',
        'content-security-policy': "default-src 'none'",
        'content-type': 'text/html; charset=utf-8',
      },
      body: '<!doctype html><meta charset="utf-8"><p id="receipt">Returned to MentorFlow</p>',
    })
  })
  return observed
}

function loginFixture(suffix: string): LoginFixture {
  const codeVerifier = `qa-verifier-${suffix}-${'x'.repeat(48)}`
  const state = `qa-state-${suffix}`
  const authorizationUrl = new URL(authorizationEndpoint)
  authorizationUrl.searchParams.set('response_type', 'code')
  authorizationUrl.searchParams.set('client_id', clientId)
  authorizationUrl.searchParams.set('redirect_uri', callback)
  authorizationUrl.searchParams.set('scope', 'openid profile email')
  authorizationUrl.searchParams.set('state', state)
  authorizationUrl.searchParams.set('nonce', `qa-nonce-${suffix}`)
  authorizationUrl.searchParams.set('code_challenge', codeChallenge(codeVerifier))
  authorizationUrl.searchParams.set('code_challenge_method', 'S256')
  return { authorizationUrl, codeVerifier, state }
}

async function completeExternalBrowserLogin(
  context: BrowserContext,
  fixture: LoginFixture,
): Promise<void> {
  const page = await context.newPage()
  await page.setContent('<button id="login" type="button">Login</button>')
  await page.locator('#login').evaluate((button, target) => {
    button.addEventListener('click', () => window.location.assign(String(target)))
  }, fixture.authorizationUrl.toString())
  await page.locator('#login').click()
  await expect(page).toHaveURL(new RegExp(`^${callback.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\?`))
  await expect(page.locator('#receipt')).toHaveText('Returned to MentorFlow')
  const returned = new URL(page.url())
  expect(returned.searchParams.get('state')).toBe(fixture.state)
  expect(returned.searchParams.get('code')).toMatch(/^synthetic-code-/)
  expect(returned.searchParams.has('access_token')).toBe(false)
  expect(returned.searchParams.has('refresh_token')).toBe(false)
}

test('external browser identity is isolated by BrowserContext and uses PKCE', async ({ browser }) => {
  const accountA = await browser.newContext()
  const accountB = await browser.newContext()
  const anonymous = await browser.newContext()
  const fixtureA = loginFixture('a')
  const fixtureB = loginFixture('b')
  const observedA = await installSyntheticIdentityProvider(accountA, 'qa-user-a', fixtureA)
  const observedB = await installSyntheticIdentityProvider(accountB, 'qa-user-b', fixtureB)

  try {
    await completeExternalBrowserLogin(accountA, fixtureA)
    await completeExternalBrowserLogin(accountB, fixtureB)

    for (const [fixture, observed] of [[fixtureA, observedA], [fixtureB, observedB]] as const) {
      expect(observed).toHaveLength(1)
      const request = observed[0]
      expect(request.origin + request.pathname).toBe(authorizationEndpoint)
      expect(request.searchParams.get('response_type')).toBe('code')
      expect(request.searchParams.get('client_id')).toBe(clientId)
      expect(request.searchParams.get('redirect_uri')).toBe(callback)
      expect(request.searchParams.get('scope')?.split(' ')).toEqual(['openid', 'profile', 'email'])
      expect(request.searchParams.get('state')).toBe(fixture.state)
      expect(request.searchParams.get('nonce')).toMatch(/^qa-nonce-/)
      expect(request.searchParams.get('code_challenge')).toBe(codeChallenge(fixture.codeVerifier))
      expect(request.searchParams.get('code_challenge_method')).toBe('S256')
      for (const forbidden of ['password', 'client_secret', 'code_verifier']) {
        expect(request.searchParams.has(forbidden)).toBe(false)
      }
    }

    const cookiesA = await accountA.cookies(issuer)
    const cookiesB = await accountB.cookies(issuer)
    const cookiesAnonymous = await anonymous.cookies(issuer)
    expect(cookiesA.find((cookie) => cookie.name === 'mf_qa_subject')?.value).toBe('qa-user-a')
    expect(cookiesB.find((cookie) => cookie.name === 'mf_qa_subject')?.value).toBe('qa-user-b')
    expect(cookiesA.some((cookie) => cookie.value === 'qa-user-b')).toBe(false)
    expect(cookiesB.some((cookie) => cookie.value === 'qa-user-a')).toBe(false)
    expect(cookiesAnonymous).toEqual([])

    console.log('ACCOUNT PASS_LOCAL | ROUND2_LIVE_REQUIRED')
  } finally {
    await Promise.all([accountA.close(), accountB.close(), anonymous.close()])
  }
})
