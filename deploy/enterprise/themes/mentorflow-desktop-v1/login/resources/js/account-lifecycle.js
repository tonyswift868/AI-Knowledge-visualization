import {bindPasswordPair} from './password-pair.js';
import {AccountLifecycleState, FORGOT_RECEIPT, FUTURE_PASSWORD_PRESENTATION} from './account-lifecycle-state.js';

const mounted = new WeakMap();
export function mountAccountLifecycle(root, options = {}) {
    if (mounted.has(root)) return mounted.get(root);
    const model = new AccountLifecycleState(options);
    const listeners = new AbortController();
    const on = (element, event, fn) => element.addEventListener(event, fn, {signal: listeners.signal});
    const find = selector => root.querySelector(selector);
    const register = find('#mf-register-form');
    const reset = find('#mf-reset-form');
    const forgot = find('#mf-forgot-form');
    const username = find('#mf-register-username');
    const email = find('#mf-register-email');
    const first = register.querySelector('[data-mf-new-password]');
    const identity = find('#mf-forgot-identity');
    const sections = [...root.querySelectorAll('[data-screen]')];
    let previousScreen = model.screen;
    const emit = (intent, accountKey) => {
        // Nonsecret intent/correlation only; never form fields or credentials.
        const detail = {intent};
        if (accountKey !== undefined) detail.accountKey = accountKey;
        if (model.pendingRequest?.kind === intent) detail.requestId = model.requestId;
        root.dispatchEvent(new CustomEvent('mentorflow-account-intent', {detail}));
    };
    const validateRegister = () => {
        const usernameValid = !!username.value.trim() && model.registrationError !== 'USERNAME_TAKEN';
        const emailValid = email.validity.valid;
        const length = Array.from(first.value).length;
        const policyValid = length >= FUTURE_PASSWORD_PRESENTATION.minimum && length <= FUTURE_PASSWORD_PRESENTATION.maximum;
        find('#mf-username-error').textContent = model.registrationError === 'USERNAME_TAKEN' ? '这个账号已被使用，请换一个。' : '';
        find('#mf-email-error').textContent = email.value && !emailValid ? '请输入有效的邮箱地址。' : '';
        find('#mf-register-policy-error').textContent = length && !policyValid ? '密码长度需要为 8–128 个字符。' : '';
        username.setAttribute('aria-invalid', String(model.registrationError === 'USERNAME_TAKEN'));
        email.setAttribute('aria-invalid', String(!!email.value && !emailValid));
        first.setAttribute('aria-invalid', String(!!length && !policyValid));
        return usernameValid && emailValid && policyValid;
    };
    first.setAttribute('aria-describedby', first.getAttribute('aria-describedby') + ' mf-register-password-policy mf-register-policy-error');
    const registerPair = bindPasswordPair(register, {
        canSubmit: () => model.capabilities.register && model.screen === 'REGISTER' && validateRegister(),
        onEdited: validateRegister,
    });
    const resetPair = bindPasswordPair(reset, {
        canSubmit: () => model.capabilities.forgot_password && model.screen === 'RESET_PASSWORD' && model.resetState === 'valid',
    });
    const choices = find('#mf-saved-accounts');
    for (const account of model.accounts) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'mf-account-choice';
        const name = document.createElement('span'); name.textContent = account.displayName;
        const safeId = document.createElement('span'); safeId.className = 'mf-account-id'; safeId.textContent = account.safeId;
        button.append(name, safeId);
        on(button, 'click', () => { if (model.selectAccount(account.key)) emit('account_selected', account.key); });
        choices.append(button);
    }
    const render = () => {
        const oldSection = sections.find(section => section.dataset.screen.split(' ').includes(previousScreen));
        const nextSection = sections.find(section => section.dataset.screen.split(' ').includes(model.screen));
        if (oldSection !== nextSection) {
            // Discard typed credentials when leaving a form, never on mismatch.
            oldSection?.querySelectorAll('form').forEach(form => form.reset());
            registerPair.reset(); resetPair.reset();
        } else if (previousScreen !== model.screen) {
            registerPair.reset(); resetPair.reset();
        }
        for (const section of sections) section.hidden = section !== nextSection;
        if (previousScreen !== model.screen) nextSection?.querySelector('h2')?.focus();
        previousScreen = model.screen;
        for (const [intent, key, tooltip] of [
            ['REGISTER', 'register', '当前账号服务暂未开放注册'],
            ['FORGOT_REQUEST', 'forgot_password', '当前账号服务暂未开放密码找回'],
        ]) {
            const button = find(`[data-navigate="${intent}"]`);
            button.disabled = !model.capabilities[key]; button.title = model.capabilities[key] ? '' : tooltip;
        }
        find('#mf-selected-account').textContent = model.selectedAccount ? `以 ${model.selectedAccount.displayName} 登录` : '使用你的 MentorFlow 账号继续。';
        find('#mf-no-saved-accounts').hidden = model.accounts.length > 0;
        find('#mf-remember-password').checked = model.rememberPassword;
        find('#mf-auto-login').checked = model.autoLogin;
        find('#mf-auto-login').disabled = !model.rememberPassword;
        find('#mf-masked-email').textContent = model.maskedEmail;
        const resend = find('#mf-resend');
        resend.disabled = !model.capabilities.register || model.resendState === 'SENDING' || model.resendSeconds > 0;
        resend.textContent = model.resendState === 'SENDING' ? '正在重新发送…'
            : model.resendSeconds ? `重新发送验证邮件（${model.resendSeconds} 秒）` : '重新发送验证邮件';
        find('#mf-resend-status').textContent = model.resendState === 'FAILED' ? '暂时无法重新发送，请稍后再试。'
            : model.resendSeconds ? '请稍候再尝试重新发送。' : '';
        find('#mf-register-status').textContent = model.screen === 'REGISTER_SUBMITTING' ? '正在提交注册申请…'
            : model.registrationError === 'REQUEST_FAILED' ? '暂时无法提交，请稍后重试。' : '';
        register.querySelectorAll('input, [data-mf-password-reveal]').forEach(input => input.disabled = model.screen === 'REGISTER_SUBMITTING');
        find('#mf-forgot-submit').disabled = !model.capabilities.forgot_password || model.screen !== 'FORGOT_REQUEST' || !identity.value.trim();
        identity.disabled = model.screen === 'FORGOT_SUBMITTING';
        find('#mf-forgot-status').textContent = model.screen === 'FORGOT_SUBMITTING' ? '正在提交请求…' : '';
        find('#mf-forgot-receipt').textContent = FORGOT_RECEIPT;
        find('#mf-reset-status').textContent = model.screen === 'RESET_WAITING' ? '正在等待账号服务确认重置结果。'
            : ({valid: '请设置新密码。', invalid: '链接无效，请返回登录重新开始。',
                expired: '链接已过期，请返回登录重新开始。', used: '链接已使用，请返回登录重新开始。'})[model.resetState];
        reset.querySelectorAll('input, [data-mf-password-reveal]').forEach(input => input.disabled = model.resetState !== 'valid' || model.screen === 'RESET_WAITING');
        validateRegister(); registerPair.refresh(); resetPair.refresh();
    };
    root.querySelectorAll('[data-navigate]').forEach(button => on(button, 'click', () => model.navigate(button.dataset.navigate)));
    on(find('[data-intent="login"]'), 'click', () => emit('login'));
    on(find('#mf-add-account'), 'click', () => { model.addAccount(); emit('add_account'); });
    on(find('#mf-remember-password'), 'change', event => model.setRememberPassword(event.target.checked));
    on(find('#mf-auto-login'), 'change', event => model.setAutoLogin(event.target.checked));
    on(username, 'input', () => { model.usernameEdited(); validateRegister(); registerPair.refresh(); });
    for (const event of ['input', 'change']) on(email, event, () => { validateRegister(); registerPair.refresh(); });
    on(identity, 'input', render);
    on(identity, 'change', render);
    on(register, 'submit', event => {
        const blocked = event.defaultPrevented;
        event.preventDefault();
        if (!blocked && validateRegister() && model.requestRegistration()) emit('register');
    });
    on(forgot, 'submit', event => {
        event.preventDefault();
        if (identity.value.trim() && model.requestForgot()) emit('forgot_password');
    });
    on(reset, 'submit', event => {
        const blocked = event.defaultPrevented;
        event.preventDefault();
        if (!blocked && model.requestReset()) { reset.reset(); resetPair.reset(); emit('reset_password'); }
    });
    on(find('#mf-resend'), 'click', () => { if (model.requestResend()) emit('resend_verification'); });
    const unsubscribe = model.subscribe(render);
    const timer = setInterval(() => { if (model.screen === 'VERIFY_PENDING') render(); }, 1000);
    const instance = {model, render, dispose() {
        clearInterval(timer); unsubscribe(); listeners.abort(); registerPair.destroy(); resetPair.destroy();
        root.querySelectorAll('form').forEach(form => form.reset()); choices.replaceChildren(); mounted.delete(root);
    }};
    mounted.set(root, instance);
    render();
    return instance;
}

const root = document.getElementById('mf-account-lifecycle');
if (root) mountAccountLifecycle(root); // Capabilities default FALSE. No production route.
