/* Headless tests of FreeMarker-produced fixtures and the actual theme JS.
 * No provider requests, password logging, screenshots, traces or storage files.
 * Args: rendered fixture directory. Uses existing Playwright/Chromium via env.
 */
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.MF_PLAYWRIGHT_MODULE || 'playwright');
const login = path.resolve(__dirname, '../../deploy/enterprise/themes/mentorflow-desktop-v1/login');

async function main() {
    const fixtures = path.resolve(process.argv[2]);
    const browser = await chromium.launch({headless: true, executablePath: process.env.MF_CHROMIUM_EXECUTABLE || undefined});
    const checks = [];
    try {
        const context = await browser.newContext({viewport: {width: 480, height: 600}});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', () => errors.push('pageerror'));
        let posts = 0;
        await page.route('**/*', async route => {
            const u = new URL(route.request().url());
            if (u.origin !== 'https://fixture.invalid') throw new Error('Unexpected remote request');
            if (u.pathname === '/native-required-action') {
                posts++;
                return route.fulfill({status: 204, body: ''}); // do not read/capture form data
            }
            if (u.pathname === '/normal-login') return route.fulfill({contentType: 'text/html', body: '<h1>正常登录</h1>'});
            if (u.pathname.startsWith('/resources/')) {
                const target = path.resolve(login, '.' + decodeURIComponent(u.pathname));
                assert(target.startsWith(login + path.sep));
                return route.fulfill({path: target});
            }
            const name = u.pathname.slice(1);
            assert(/^[a-z-]+\.html$/.test(name));
            return route.fulfill({contentType: 'text/html', body: await fs.readFile(path.join(fixtures, name))});
        });
        const open = async name => { await page.goto(`https://fixture.invalid/${name}.html`); };
        const first = page.locator('#password-new');
        const confirm = page.locator('#password-confirm');
        const submit = page.locator('#kc-reset-submit');
        const mismatch = page.locator('#mf-password-mismatch');
        const reveal = page.locator('[data-mf-password-reveal]');
        const check = async (name, fn) => { await fn(); checks.push(name); };

        await open('normal');
        await check('both-empty', async () => {
            assert(await submit.isDisabled()); assert(!(await mismatch.isVisible()));
            assert.equal(await first.getAttribute('type'), 'password');
            assert.equal(await confirm.getAttribute('type'), 'password');
        });
        await check('first-only', async () => {
            await first.fill('synthetic long passphrase');
            assert(await submit.isDisabled()); assert(!(await mismatch.isVisible()));
        });
        await check('second-only', async () => {
            await first.fill(''); await confirm.fill('synthetic long passphrase');
            assert(await submit.isDisabled()); assert(!(await mismatch.isVisible()));
        });
        await check('matching-inputs-enable-native-policy-submission', async () => {
            await first.fill('synthetic long passphrase');
            assert(await submit.isEnabled()); assert(!(await mismatch.isVisible()));
        });
        await check('mismatch-live-and-no-copy-or-clear', async () => {
            await confirm.fill('different synthetic value');
            assert(await submit.isDisabled());
            assert.equal((await mismatch.innerText()).trim(), '两次输入的密码不一致，请重新确认。');
            assert(await first.evaluate(e => e.value === 'synthetic long passphrase'));
        });
        await check('show-hide-first-confirm-always-masked-no-control', async () => {
            assert.equal(await page.locator('[aria-controls="password-confirm"], [data-password-toggle]').count(), 0);
            await reveal.click(); assert.equal(await first.getAttribute('type'), 'text');
            assert.equal((await reveal.innerText()).trim(), '隐藏密码');
            assert.equal(await confirm.getAttribute('type'), 'password');
            assert(await submit.isDisabled());
            await reveal.click(); assert.equal(await first.getAttribute('type'), 'password');
            assert.equal((await reveal.innerText()).trim(), '显示密码');
        });
        await check('retype-match-clears-immediately-even-when-first-revealed', async () => {
            await reveal.click(); await confirm.fill('synthetic long passphrase');
            assert(!(await mismatch.isVisible())); assert(await submit.isEnabled());
            assert.equal(await confirm.getAttribute('type'), 'password');
            assert.equal(await confirm.getAttribute('aria-invalid'), 'false');
        });
        await check('ordinary-paste-and-autofill-contract', async () => {
            for (const input of [first, confirm]) assert.equal(await input.getAttribute('autocomplete'), 'new-password');
            assert(await confirm.evaluate(e => e.dispatchEvent(new ClipboardEvent('paste', {cancelable: true}))));
            await confirm.evaluate(e => { e.value = ''; e.dispatchEvent(new Event('change', {bubbles: true})); });
            assert(await submit.isDisabled());
            await confirm.fill('synthetic long passphrase');
        });
        await check('submit-native-post-once', async () => {
            const count = posts;
            await submit.click(); await page.waitForTimeout(50);
            assert.equal(posts, count + 1); assert(await submit.isDisabled());
            assert.equal(await page.evaluate(() => document.querySelector('form').dispatchEvent(new SubmitEvent('submit', {cancelable: true}))), false);
            assert.equal(posts, count + 1);
        });
        await check('pageshow-masks-and-reenables', async () => {
            await page.evaluate(() => window.dispatchEvent(new Event('pageshow')));
            assert.equal(await first.getAttribute('type'), 'password'); assert(await submit.isEnabled());
        });
        await check('server-mismatch-clears-on-retype', async () => {
            await open('server-mismatch');
            assert((await page.locator('#input-error-password-confirm').innerText()).includes('两次输入'));
            await first.fill('synthetic long passphrase'); await confirm.fill('synthetic long passphrase');
            assert.equal((await page.locator('#input-error-password-confirm').innerText()).trim(), '');
            assert(!(await mismatch.isVisible())); assert(await submit.isEnabled());
        });
        for (const state of ['policy-min', 'policy-max', 'policy-denied', 'policy-username', 'policy-email']) {
            await check(state + '-separate-from-mismatch', async () => {
                await open(state); assert(await page.locator('#server-message').isVisible());
                assert(!(await mismatch.isVisible())); assert(await submit.isDisabled());
                assert(!(await page.locator('#server-message').innerText()).includes('两次输入'));
            });
        }
        for (const state of ['invalid', 'expired', 'used']) {
            await check(state + '-controlled-error-no-password-form', async () => {
                await open(state); assert(await page.locator('#kc-error-message').isVisible());
                assert.equal(await page.locator('input, form, [type=submit]').count(), 0);
                assert((await page.locator('#kc-error-message').innerText()).includes('请返回 MentorFlow'));
            });
        }
        await check('provider-specific-success-wording-and-normal-login-link', async () => {
            await open('success-specific');
            assert((await page.locator('#kc-info-message').innerText()).includes('密码已重置，请使用新密码登录 MentorFlow。'));
            assert.equal(await page.locator('form').count(), 0);
            assert(page.url().endsWith('/success-specific.html')); // no auto navigation/session creation
            await page.locator('#kc-info-message a').click();
            assert(page.url().endsWith('/normal-login'));
        });
        await check('generic-native-success-is-not-mislabeled-as-password-reset', async () => {
            await open('success-generic');
            assert(!(await page.locator('#kc-info-message').innerText()).includes('密码已重置'));
        });
        await check('cancel-required-action-with-empty-fields', async () => {
            await open('cancel'); const count = posts;
            await page.locator('[name=cancel-aia]').click(); await page.waitForTimeout(50);
            assert.equal(posts, count + 1);
        });
        await check('no-storage-no-browser-errors-no-horizontal-clipping', async () => {
            await open('normal');
            assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
            assert.deepEqual(await context.cookies(), []); assert.deepEqual(errors, []);
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        });
        console.log(JSON.stringify({scope: 'OFFLINE_RENDERED_UI_ONLY', count: checks.length, checks}));
    } finally { await browser.close(); }
}
// Do not print assertion values, DOM, request bodies or browser dumps on failure.
main().catch(() => { console.error('Reset UI fixture check failed; inspect the named checks locally.'); process.exitCode = 1; });
