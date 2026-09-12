/* Offline, headless UI/state tests. No provider, SMTP, Vault, screenshots of
 * passwords, request bodies, browser traces or secret-bearing error dumps.
 * Args: FreeMarker fixture directory, optional directory for EMPTY UI images.
 */
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.MF_PLAYWRIGHT_MODULE || 'playwright');
const login = path.resolve(__dirname, '../../deploy/enterprise/themes/mentorflow-desktop-v1/login');
let stage = 'startup';

async function main() {
    const fixtures = path.resolve(process.argv[2]);
    const browser = await chromium.launch({headless: true, executablePath: process.env.MF_CHROMIUM_EXECUTABLE || undefined});
    const checks = [];
    try {
        const context = await browser.newContext({viewport: {width: 480, height: 720}});
        const page = await context.newPage();
        let unexpectedRequests = 0;
        const errors = [];
        page.on('pageerror', () => errors.push('pageerror'));
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== 'https://fixture.invalid' || route.request().method() !== 'GET') {
                unexpectedRequests++; return route.abort();
            }
            if (url.pathname === '/lifecycle.html') return route.fulfill({contentType: 'text/html', path: path.join(fixtures, 'lifecycle.html')});
            const target = path.resolve(login, '.' + decodeURIComponent(url.pathname));
            if (!url.pathname.startsWith('/resources/') || !target.startsWith(login + path.sep)) {
                unexpectedRequests++; return route.abort();
            }
            return route.fulfill({path: target});
        });
        const start = async (options = {}) => {
            await page.goto('https://fixture.invalid/lifecycle.html');
            await page.evaluate(async options => {
                const {mountAccountLifecycle} = await import('/resources/js/account-lifecycle.js');
                const root = document.getElementById('mf-account-lifecycle');
                mountAccountLifecycle(root).dispose();
                window.testNow = 0;
                window.ui = mountAccountLifecycle(root, {...options, now: () => window.testNow});
                window.intents = [];
                root.addEventListener('mentorflow-account-intent', event => window.intents.push(event.detail));
            }, options);
        };
        const enabled = {register: true, forgot_password: true}; // Fixture seam only.
        const register = async capabilities => start({initialScreen: 'REGISTER', capabilities: capabilities || enabled});
        const first = page.locator('#register-password-new');
        const confirm = page.locator('#register-password-confirm');
        const submit = page.locator('#mf-register-form [type=submit]');
        const fillRegister = async (password = 'abcdefgh') => {
            await page.locator('#mf-register-username').fill('tonyswift555');
            await page.locator('#mf-register-email').fill('21234534@qq.com');
            await first.fill(password); await confirm.fill(password);
        };
        const check = async (name, work) => { stage = name; await work(); checks.push(name); };

        await check('capabilities-closed-by-default-and-strict-booleans', async () => {
            for (const capabilities of [{}, null, [], 'true', {register: 'true', forgot_password: 1}]) {
                await start({capabilities});
                for (const screen of ['REGISTER', 'FORGOT_REQUEST']) {
                    const button = page.locator(`[data-navigate="${screen}"]`);
                    assert(await button.isVisible()); assert(await button.isDisabled());
                    assert((await button.getAttribute('title')).includes('暂未开放'));
                    assert.equal(await page.evaluate(s => window.ui.model.navigate(s), screen), false);
                }
                assert.equal(await page.evaluate(() => window.intents.length), 0);
            }
        });
        await check('closed-register-page-cannot-submit-even-valid-fields', async () => {
            await register({register: false}); await fillRegister();
            assert(await submit.isDisabled());
            assert.equal(await page.evaluate(() => window.ui.model.requestRegistration()), false);
        });
        await check('four-register-fields-and-free-username-presentation', async () => {
            await register();
            assert.equal(await page.locator('#mf-register-form input').count(), 4);
            assert.equal((await page.locator('#mf-username-hint').innerText()).trim(), '账号需要唯一。');
            assert.equal(await page.locator('#mf-register-username').getAttribute('pattern'), null);
            assert.equal(await page.locator('#mf-register-email').getAttribute('type'), 'email');
            assert.equal(await first.getAttribute('type'), 'password');
            assert.equal(await confirm.getAttribute('type'), 'password');
            assert.equal((await page.locator('label[for=register-password-confirm]').innerText()).trim(), '确认密码');
            await fillRegister();
            for (const username of ['9用户', '12345678', 'Name With Spaces', 'u@example.org']) {
                await page.locator('#mf-register-username').fill(username); assert(await submit.isEnabled());
            }
        });
        await check('email-format-live-feedback-and-recovery', async () => {
            await register(); await fillRegister();
            await page.locator('#mf-register-email').fill('not-an-email');
            assert(await submit.isDisabled());
            assert.equal(await page.locator('#mf-email-error').innerText(), '请输入有效的邮箱地址。');
            await page.locator('#mf-register-email').fill('test@example.org');
            assert(await submit.isEnabled()); assert.equal(await page.locator('#mf-email-error').innerText(), '');
        });
        await check('password-8-to-128-without-composition-requirement', async () => {
            await register();
            for (const [password, allowed] of [['a'.repeat(7), false], ['a'.repeat(8), true],
                ['1'.repeat(8), true], ['!'.repeat(8), true], ['密码'.repeat(4), true],
                ['🙂'.repeat(8), true], ['a'.repeat(128), true], ['a'.repeat(129), false]]) {
                await fillRegister(password);
                assert.equal(await submit.isEnabled(), allowed);
                assert.equal((await page.locator('#mf-register-policy-error').innerText()).length > 0, !allowed);
            }
        });
        await check('shared-pair-live-mismatch-and-first-only-reveal', async () => {
            await register(); await fillRegister(); await confirm.fill('mismatched');
            assert(await submit.isDisabled());
            assert.equal((await page.locator('#mf-register-password-mismatch').innerText()).trim(), '两次输入的密码不一致，请重新确认。');
            await page.locator('#mf-register-form [data-mf-password-reveal]').click();
            assert.equal(await first.getAttribute('type'), 'text');
            assert.equal(await confirm.getAttribute('type'), 'password');
            assert.equal(await page.locator('[aria-controls=register-password-confirm]').count(), 0);
            assert(await first.evaluate(e => e.value === 'abcdefgh'));
            await confirm.fill('abcdefgh');
            assert(!(await page.locator('#mf-register-password-mismatch').isVisible())); assert(await submit.isEnabled());
        });
        await check('duplicate-username-is-server-presentation-not-local-lookup', async () => {
            await register(); await fillRegister(); await submit.click();
            assert.equal(await page.evaluate(() => window.ui.model.screen), 'REGISTER_SUBMITTING');
            await page.evaluate(() => window.ui.model.registrationResult('username_taken', {requestId: window.ui.model.requestId}));
            assert.equal(await page.locator('#mf-username-error').innerText(), '这个账号已被使用，请换一个。');
            assert(await submit.isDisabled()); assert(await first.evaluate(e => e.value === 'abcdefgh'));
            await page.locator('#mf-register-username').fill('another account');
            assert.equal(await page.locator('#mf-username-error').innerText(), ''); assert(await submit.isEnabled());
        });
        await check('registration-waits-for-presentation-result-no-main-window', async () => {
            await register(); await fillRegister(); await submit.click();
            assert.equal(await page.evaluate(() => window.ui.model.screen), 'REGISTER_SUBMITTING');
            assert(!(await page.locator('[data-screen=VERIFY_PENDING]').isVisible()));
            assert.deepEqual(await page.evaluate(() => window.intents), [{intent: 'register', requestId: 1}]);
            await page.evaluate(() => document.querySelector('#mf-register-form').dispatchEvent(new SubmitEvent('submit', {cancelable: true})));
            assert.equal(await page.evaluate(() => window.intents.length), 1);
        });
        await check('verify-pending-masks-email-and-discards-form-values', async () => {
            await page.evaluate(() => window.ui.model.registrationResult('verification_pending', {requestId: window.ui.model.requestId, email: '21234534@qq.com', retryAfter: 60}));
            assert.equal(await page.evaluate(() => window.ui.model.screen), 'VERIFY_PENDING');
            assert.equal(await page.locator('#mf-masked-email').innerText(), '2****34@qq.com');
            assert((await page.locator('[data-screen=VERIFY_PENDING]').innerText()).includes('验证邮件已发送到：'));
            assert(await page.evaluate(() => [...document.querySelectorAll('form input')].every(input => !input.value)));
            assert(await page.locator('#mf-resend').isDisabled());
            assert((await page.locator('#mf-resend').innerText()).includes('60 秒'));
        });
        await check('resend-cooldown-sending-failure-retry-and-no-email-call', async () => {
            await page.evaluate(() => { window.testNow = 60001; window.ui.render(); });
            assert(await page.locator('#mf-resend').isEnabled());
            await page.locator('#mf-resend').click();
            assert(await page.locator('#mf-resend').isDisabled());
            assert.equal(await page.evaluate(() => window.ui.model.resendState), 'SENDING');
            await page.evaluate(() => window.ui.model.resendResult({requestId: window.ui.model.requestId, accepted: false}));
            assert.equal(await page.locator('#mf-resend-status').innerText(), '暂时无法重新发送，请稍后再试。');
            await page.locator('#mf-resend').click();
            await page.evaluate(() => window.ui.model.resendResult({requestId: window.ui.model.requestId, accepted: true, retryAfter: 30}));
            assert((await page.locator('#mf-resend').innerText()).includes('30 秒'));
            assert.equal(unexpectedRequests, 0);
        });
        await check('verify-back-to-login-and-stale-result-ignored', async () => {
            await page.locator('[data-screen=VERIFY_PENDING] [data-navigate=LOGIN]').click();
            assert.equal(await page.evaluate(() => window.ui.model.screen), 'LOGIN');
            assert.equal(await page.evaluate(() => window.ui.model.resendResult({accepted: true})), false);
            assert.equal(await page.evaluate(() => window.ui.model.maskedEmail), '');
        });
        await check('forgot-identical-existing-and-missing-account-receipt', async () => {
            const copies = [];
            for (const exists of [true, false]) {
                await start({initialScreen: 'FORGOT_REQUEST', capabilities: enabled});
                await page.locator('#mf-forgot-identity').fill(exists ? 'known-user' : 'unknown-user');
                await page.locator('#mf-forgot-submit').click();
                assert.equal(await page.evaluate(() => window.ui.model.screen), 'FORGOT_SUBMITTING');
                await page.evaluate(exists => window.ui.model.forgotResult(window.ui.model.requestId, {exists}), exists);
                copies.push(await page.locator('#mf-forgot-receipt').innerText());
                assert.deepEqual(await page.evaluate(() => window.intents), [{intent: 'forgot_password', requestId: 1}]);
                assert.equal(await page.locator('#mf-forgot-identity').inputValue(), '');
            }
            assert.equal(copies[0], '如果该账号存在，我们会向绑定邮箱发送密码重置邮件。');
            assert.equal(copies[0], copies[1]);
        });
        await check('reset-invalid-expired-used-states-remain-blocked', async () => {
            await start({capabilities: enabled});
            for (const state of ['invalid', 'expired', 'used', 'unknown']) {
                await page.evaluate(state => window.ui.model.presentReset(state), state);
                assert(await page.locator('#mf-reset-form [type=submit]').isDisabled());
                assert(await page.locator('#reset-password-new').isDisabled());
                assert.equal(await page.evaluate(() => window.ui.model.requestReset()), false);
                assert((await page.locator('#mf-reset-status').innerText()).includes('返回登录'));
            }
        });
        await check('reset-pair-reused-and-success-waits-for-owner-01', async () => {
            await page.evaluate(() => window.ui.model.presentReset('valid'));
            await page.locator('#reset-password-new').fill('new synthetic value');
            await page.locator('#reset-password-confirm').fill('different value');
            assert(await page.locator('#mf-reset-form [type=submit]').isDisabled());
            await page.locator('#mf-reset-form [data-mf-password-reveal]').click();
            assert.equal(await page.locator('#reset-password-confirm').getAttribute('type'), 'password');
            await page.locator('#reset-password-confirm').fill('new synthetic value');
            await page.locator('#mf-reset-form [type=submit]').click();
            assert.equal(await page.evaluate(() => window.ui.model.screen), 'RESET_WAITING');
            assert.equal(await page.locator('#mf-reset-status').innerText(), '正在等待账号服务确认重置结果。');
            assert(!(await page.locator('body').innerText()).includes('密码已重置'));
            assert.equal(await page.evaluate(() => typeof window.ui.model.resetResult), 'undefined');
            assert(await page.evaluate(() => [...document.querySelectorAll('#mf-reset-form input')].every(input => !input.value)));
        });
        await check('multi-account-safe-labels-only-no-selection-login-or-vault-switch', async () => {
            await start({initialScreen: 'ACCOUNT_SELECT', accounts: [
                {key: 'account-a', displayName: 'tonyswift555', safeId: '账号 · 7c24c330', password: 'must-never-be-retained'},
                {key: 'account-b', displayName: '其他已保存账号', safeId: '账号 · 61287bdd'},
            ]});
            assert.equal(await page.locator('.mf-account-choice').count(), 2);
            assert(!(await page.locator('body').innerText()).includes('must-never-be-retained'));
            assert.equal(await page.evaluate(() => Object.hasOwn(window.ui.model.accounts[0], 'password')), false);
            await page.locator('.mf-account-choice').first().click();
            assert.equal(await page.evaluate(() => window.ui.model.screen), 'LOGIN');
            assert((await page.locator('#mf-selected-account').innerText()).includes('tonyswift555'));
            assert.deepEqual(await page.evaluate(() => window.intents), [{intent: 'account_selected', accountKey: 'account-a'}]);
            await page.locator('[data-navigate=ACCOUNT_SELECT]').click();
            await page.locator('#mf-add-account').click();
            assert.equal(await page.evaluate(() => window.ui.model.selectedAccount), null);
        });
        await check('future-remember-and-auto-default-off-memory-only', async () => {
            await start();
            assert(!(await page.locator('#mf-remember-password').isChecked()));
            assert(!(await page.locator('#mf-auto-login').isChecked())); assert(await page.locator('#mf-auto-login').isDisabled());
            await page.locator('#mf-remember-password').check(); await page.locator('#mf-auto-login').check();
            assert.equal(await page.evaluate(() => window.ui.model.autoLogin), true);
            await page.locator('#mf-remember-password').uncheck();
            assert(!(await page.locator('#mf-auto-login').isChecked()));
            assert.deepEqual(await page.evaluate(() => window.intents), []);
            await start(); assert.equal(await page.evaluate(() => window.ui.model.rememberPassword), false);
        });
        await check('stale-registration-and-resend-replies-cannot-replace-new-request', async () => {
            await register();
            const results = await page.evaluate(() => {
                const model = window.ui.model;
                model.requestRegistration(); const oldRequest = model.requestId;
                model.navigate('LOGIN'); model.navigate('REGISTER');
                model.requestRegistration(); const currentRequest = model.requestId;
                const stale = model.registrationResult('verification_pending', {requestId: oldRequest, email: 'old@host.test'});
                const stateAfterStale = model.screen;
                const current = model.registrationResult('verification_pending', {requestId: currentRequest, email: '21234534@qq.com', retryAfter: 0});
                model.requestResend(); const oldResend = model.requestId;
                model.resendResult({requestId: oldResend, accepted: false});
                model.requestResend();
                const staleResend = model.resendResult({requestId: oldResend, accepted: true});
                return {stale, stateAfterStale, current, staleResend, resendState: model.resendState};
            });
            assert.deepEqual(results, {stale: false, stateAfterStale: 'REGISTER_SUBMITTING', current: true,
                staleResend: false, resendState: 'SENDING'});
            assert.equal(await page.locator('#mf-masked-email').innerText(), '2****34@qq.com');
        });
        await check('account-choice-and-add-clear-future-auto-login-consent', async () => {
            await start({accounts: [{key: 'a', displayName: 'A', safeId: '账号 · 12345678'}]});
            const result = await page.evaluate(() => {
                const model = window.ui.model;
                model.setRememberPassword(true); model.setAutoLogin(true); model.selectAccount('a');
                const selected = [model.rememberPassword, model.autoLogin];
                model.setRememberPassword(true); model.setAutoLogin(true); model.addAccount();
                return [...selected, model.rememberPassword, model.autoLogin];
            });
            assert.deepEqual(result, [false, false, false, false]);
        });
        await check('remount-password-component-restores-masked-default', async () => {
            await register(); await fillRegister();
            await page.locator('#mf-register-form [data-mf-password-reveal]').click();
            await page.evaluate(async () => {
                const {mountAccountLifecycle} = await import('/resources/js/account-lifecycle.js');
                window.ui.dispose();
                window.ui = mountAccountLifecycle(document.getElementById('mf-account-lifecycle'), {initialScreen: 'REGISTER'});
            });
            assert.equal(await first.getAttribute('type'), 'password');
            assert.equal(await confirm.getAttribute('type'), 'password');
            assert(await first.evaluate(e => e.value.length === 0));
        });
        await check('masking-edge-cases-and-safe-text-rendering', async () => {
            const masks = await page.evaluate(async () => {
                const {maskEmail} = await import('/resources/js/account-lifecycle-state.js');
                return ['a@host.test', 'ab@host.test', 'abc@host.test', 'bad', 'a b@host.test'].map(maskEmail);
            });
            assert.deepEqual(masks, ['****@host.test', 'a****@host.test', 'a****@host.test', '已绑定邮箱', '已绑定邮箱']);
            await start({initialScreen: 'ACCOUNT_SELECT', accounts: [{key: 'a', displayName: '<img src=x onerror=alert(1)>', safeId: '账号 · a1'}]});
            assert.equal(await page.locator('.mf-account-choice img').count(), 0);
        });
        await check('small-viewport-multi-account-and-register-layout', async () => {
            await page.setViewportSize({width: 320, height: 600});
            await start({initialScreen: 'ACCOUNT_SELECT', accounts: Array.from({length: 8}, (_, i) => ({key: 'account-' + i,
                displayName: i ? '较长的账号显示名称'.repeat(4) : 'tonyswift555', safeId: '账号 · 12345678'}))});
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await page.locator('#mf-add-account').scrollIntoViewIfNeeded();
            assert(await page.locator('#mf-add-account').isVisible());
            await register(); assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
            await submit.scrollIntoViewIfNeeded(); assert(await submit.isVisible());
        });
        await check('no-network-side-effects-storage-cookies-or-browser-errors', async () => {
            assert.equal(unexpectedRequests, 0); assert.deepEqual(errors, []);
            assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
            assert.deepEqual(await context.cookies(), []);
        });
        if (process.argv[3]) {
            const images = path.resolve(process.argv[3]); await fs.mkdir(images, {recursive: true});
            await page.setViewportSize({width: 480, height: 720});
            for (const screen of ['REGISTER', 'ACCOUNT_SELECT', 'LOGIN']) {
                await start({initialScreen: screen, accounts: [{key: 'preview', displayName: 'tonyswift555', safeId: '账号 · 7c24c330'}]});
                await page.screenshot({path: path.join(images, screen + '.png'), fullPage: true}); // Always empty password fields.
            }
        }
        console.log(JSON.stringify({scope: 'INACTIVE_UI_SHELL_ONLY', count: checks.length, checks}));
    } finally { await browser.close(); }
}
main().catch(() => { console.error('Lifecycle UI check failed: ' + stage); process.exitCode = 1; });
