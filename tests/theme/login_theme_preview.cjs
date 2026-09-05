/* Static browser fixture only. Does NOT render FreeMarker or authenticate with Keycloak.
 * Uses installed Playwright and an extracted official keycloak-themes-26.7.3.jar.
 * Run with MF_PLAYWRIGHT_MODULE pointing to an existing @playwright/test installation.
 * Overlay the matching themes-vendor JAR's theme directory into it.
 * MF_KEYCLOAK_THEME_ASSETS must point to that extracted theme directory.
 */
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { chromium } = require(process.env.MF_PLAYWRIGHT_MODULE || '@playwright/test');
const root = path.resolve(__dirname, '../..');
const upstream = 'https://repo.maven.apache.org/maven2/org/keycloak/keycloak-themes/26.7.3/keycloak-themes-26.7.3.jar!theme/';

async function main() {
  const output = path.resolve(process.argv[2]);
  await fs.mkdir(output, { recursive: true });
  const assets = [
    'keycloak/common/resources/vendor/patternfly-v4/patternfly.min.css',
    'keycloak/common/resources/vendor/patternfly-v3/css/patternfly.min.css',
    'keycloak/common/resources/vendor/patternfly-v3/css/patternfly-additions.min.css',
    'keycloak/common/resources/lib/pficon/pficon.css',
    'keycloak/login/resources/css/login.css',
    'base/login/resources/js/passwordVisibility.js',
  ];
  const assetRoot = path.resolve(process.env.MF_KEYCLOAK_THEME_ASSETS);
  const sources = await Promise.all(assets.map(asset => fs.readFile(path.join(assetRoot, asset), 'utf8')));
  const styles = sources.slice(0, -1).map((source, i) => source.replace(/url\(['"]?([^)'"\s]+)['"]?\)/g, (match, url) => url.startsWith('data:') ? match : `url("https://static-fixture.invalid/${path.posix.normalize(path.posix.join(path.posix.dirname(assets[i]), url))}")`));
  const css = await fs.readFile(path.join(root, 'deploy/enterprise/themes/mentorflow-desktop-v1/login/resources/css/mentorflow.css'), 'utf8');
  const fixture = error => `<!doctype html><html class="login-pf" lang="zh-CN"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>STATIC FIXTURE — NOT KEYCLOAK AUTHENTICATION</title>
    <style>${styles.join('\n')}\n${css}</style></head>
    <body class="mentorflow-login"><div class="login-pf-page">
    <div id="kc-header"><div id="kc-header-wrapper">MentorFlow Enterprise</div></div>
    <div class="card-pf"><header class="login-pf-header"><h1 id="kc-page-title">MentorFlow</h1></header>
    <div id="kc-content"><div id="kc-content-wrapper"><div id="kc-form"><div id="kc-form-wrapper">
    <form id="kc-form-login" action="https://fixture.invalid/login-actions/authenticate" method="post">
    <div class="form-group"><label class="pf-c-form__label" for="username">账号</label>
    <input tabindex="2" id="username" name="username" class="pf-c-form-control" type="text" autocomplete="username" autofocus aria-invalid="${!!error}">
    ${error ? `<span id="input-error" class="pf-c-form__helper-text pf-m-error required kc-feedback-text" aria-live="polite">${error}</span>` : ''}</div>
    <div class="form-group"><label class="pf-c-form__label" for="password">密码</label>
    <div class="pf-c-input-group"><input tabindex="3" id="password" name="password" class="pf-c-form-control" type="password" autocomplete="current-password" aria-invalid="${!!error}">
    <button class="pf-c-button pf-m-control" type="button" aria-label="显示密码" aria-controls="password" data-password-toggle tabindex="4" data-icon-show="fa fa-eye" data-icon-hide="fa fa-eye-slash" data-label-show="显示密码" data-label-hide="隐藏密码"><i class="fa fa-eye" aria-hidden="true"></i></button></div></div>
    <div class="form-group login-pf-settings"><div id="kc-form-options"></div><div><span><a tabindex="6" href="https://fixture.invalid/reset">忘记密码？</a></span></div></div>
    <div id="kc-form-buttons" class="form-group"><input type="hidden" name="credentialId"><input tabindex="7" class="pf-c-button pf-m-primary pf-m-block btn-lg" name="login" id="kc-login" type="submit" value="登录"></div>
    </form></div></div></div></div></div></div></body></html>`;
  const browser = await chromium.launch({ headless: true, executablePath: process.env.MF_CHROMIUM_EXECUTABLE || undefined });
  const results = [];
  try {
    for (const scale of [1, 1.25, 1.5]) {
      // Fixed 480x600 physical content rectangle; CSS viewport shrinks with DPI.
      // This is a layout simulation, not Windows/Qt native DPI acceptance.
      for (const [state, error] of Object.entries({normal: '', invalid: '账号或密码不正确，请重试。', longError: '账号或密码不正确，请重试。'.repeat(8)})) {
        const context = await browser.newContext({ viewport: {width: Math.floor(480 / scale), height: Math.floor(600 / scale)}, deviceScaleFactor: scale });
        const page = await context.newPage();
        // Fulfill only local official assets; never contact a provider or remote form.
        await page.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.origin !== 'https://static-fixture.invalid') return route.abort();
          const target = path.resolve(assetRoot, '.' + decodeURIComponent(url.pathname));
          if (!target.startsWith(assetRoot + path.sep)) return route.abort();
          try { await route.fulfill({body: await fs.readFile(target)}); }
          catch { await route.abort(); }
        });
        await page.setContent(fixture(error));
        await page.addScriptTag({ content: sources.at(-1) });
        await page.evaluate(() => document.fonts.ready);
        assert.equal(await page.locator('#kc-header').isVisible(), false);
        assert.equal(await page.locator('input[type=submit]').count(), 1);
        assert.equal(await page.locator('[name=rememberMe]').count(), 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        const nestedScrollers = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(e => /auto|scroll/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 1).length);
        assert.equal(nestedScrollers, 0);
        for (const selector of ['#username', '#password', '[data-password-toggle]', '#kc-login', ...(error ? ['#input-error'] : [])]) {
          await page.locator(selector).scrollIntoViewIfNeeded();
          const bounds = await page.locator(selector).boundingBox();
          assert(bounds.x >= 0 && bounds.x + bounds.width <= Math.floor(480 / scale) + 1, selector);
          assert.equal(await page.locator(selector).isVisible(), true);
        }
        if (!error) assert.equal(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight), true);
        await page.locator('#username').focus();
        for (const expected of ['password', 'toggle', 'forgot', 'kc-login']) {
          await page.keyboard.press('Tab');
          assert.equal(await page.evaluate(() => document.activeElement.id || (document.activeElement.hasAttribute('data-password-toggle') ? 'toggle' : 'forgot')), expected);
        }
        await page.locator('#password').fill('synthetic-fixture-only');
        await page.locator('[data-password-toggle]').click();
        assert.equal(await page.locator('#password').getAttribute('type'), 'text');
        assert.equal(await page.locator('[data-password-toggle]').getAttribute('aria-label'), '隐藏密码');
        await page.locator('[data-password-toggle]').click();
        assert.equal(await page.locator('#password').getAttribute('type'), 'password');
        await page.evaluate(() => {
          window.fixtureSubmits = 0;
          document.querySelector('form').addEventListener('submit', event => { event.preventDefault(); window.fixtureSubmits++; });
        });
        await page.locator('#password').press('Enter');
        assert.equal(await page.evaluate(() => window.fixtureSubmits), 1);
        await page.locator('#username').fill('');
        await page.locator('#password').fill('');
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: path.join(output, `static-${scale}-${state}.png`), fullPage: true });
        results.push({scale, state, layout: 'PASS', keyboard: 'PASS', upstreamVisibility: 'PASS', authentication: 'NOT_RUN'});
        await context.close();
      }
    }
  } finally { await browser.close(); }
  const report = {kind: 'STATIC_FIXTURE_NOT_KEYCLOAK_RENDER', nativeWindowsQtDpi: 'NOT_RUN', realKeycloakAuthentication: 'NOT_RUN', sources: assets.map((asset, i) => ({url: (i < 3 ? upstream.replaceAll('keycloak-themes', 'keycloak-themes-vendor') : upstream) + asset, sha256: crypto.createHash('sha256').update(sources[i]).digest('hex')})), results};
  await fs.writeFile(path.join(output, 'static-browser-results.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({static_cases: results.length, status: 'PASS', real_keycloak: 'NOT_RUN'}));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
