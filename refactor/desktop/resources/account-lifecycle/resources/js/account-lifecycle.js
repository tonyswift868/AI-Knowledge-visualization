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
    const registrationCheckTimers = new Map();
    let previousScreen = model.screen;
    const emit = (intent, accountKey) => {
        // Nonsecret intent/correlation only; never form fields or credentials.
        const detail = {intent};
        if (accountKey !== undefined) detail.accountKey = accountKey;
        if (model.pendingRequest?.kind === intent) detail.requestId = model.requestId;
        root.dispatchEvent(new CustomEvent('mentorflow-account-intent', {detail}));
    };
    const validateRegister = () => {
        const usernameTaken = model.registrationAvailability.username.state === 'taken'
            && model.registrationAvailability.username.value === username.value.trim();
        // Availability remains advisory: an expired, incomplete self-registration
        // can be safely restarted only by the server with both fields matching.
        const usernameValid = !!username.value.trim();
        const emailValid = !!email.value.trim() && email.validity.valid;
        const length = Array.from(first.value).length;
        const policyValid = length >= FUTURE_PASSWORD_PRESENTATION.minimum && length <= FUTURE_PASSWORD_PRESENTATION.maximum;
        find('#mf-username-error').textContent = model.registrationError === 'USERNAME_TAKEN' || usernameTaken ? '账号名已被使用；若是尚未完成的注册，10 分钟后可用相同账号名和邮箱重新开始。' : '';
        find('#mf-email-error').textContent = email.value && !email.validity.valid ? '请输入有效的邮箱地址。' : '';
        find('#mf-register-policy-error').textContent = length && !policyValid ? '密码长度需要为 8–128 个字符。' : '';
        username.setAttribute('aria-invalid', String(model.registrationError === 'USERNAME_TAKEN' || usernameTaken));
        email.setAttribute('aria-invalid', String(!!email.value && !email.validity.valid));
        first.setAttribute('aria-invalid', String(!!length && !policyValid));
        return usernameValid && emailValid && policyValid;
    };
    const renderAvailability = (field, selector) => {
        const entry = model.registrationAvailability[field];
        const target = find(selector);
        const text = entry.state === 'checking' ? '正在检查可用性…'
            : entry.state === 'available' ? '账号名可用。'
            : entry.state === 'taken' ? ''
            : entry.state === 'failed' ? (entry.message || '暂时无法检查可用性，注册时会再次确认。') : '';
        target.textContent = text;
        target.dataset.state = entry.state;
        target.hidden = !text;
    };
    const scheduleRegistrationCheck = (field, input, immediate = false) => {
        const prior = registrationCheckTimers.get(field);
        if (prior) clearTimeout(prior);
        const value = input.value.trim();
        const entry = model.registrationAvailability[field];
        if (!value) { model.registrationFieldEdited(field, value); return; }
        if (entry.value === value && ['checking', 'available', 'taken'].includes(entry.state)) return;
        const timer = setTimeout(() => {
            registrationCheckTimers.delete(field);
            const requestId = model.beginRegistrationCheck(field, value);
            if (requestId === null) return;
            root.dispatchEvent(new CustomEvent('mentorflow-account-intent', {
                detail: {intent: 'check_registration', field, value, requestId},
            }));
        }, immediate ? 0 : 500);
        registrationCheckTimers.set(field, timer);
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
            if (previousScreen === 'REGISTER_SUBMITTING' && model.screen === 'REGISTER') {
                // Clear the submitting lock after a failed response, while retaining every field value.
                registerPair.reset();
            } else if (!(['REGISTER', 'REGISTER_SUBMITTING'].includes(previousScreen)
                && ['REGISTER', 'REGISTER_SUBMITTING'].includes(model.screen))) {
                registerPair.reset(); resetPair.reset();
            }
        }
        for (const section of sections) section.hidden = section !== nextSection;
        if (oldSection !== nextSection) {
            window.scrollTo(0, 0);
            nextSection?.querySelector('h2')?.focus({preventScroll: true});
        }
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
        find('#mf-verification-summary').textContent = '如果这个邮箱可以使用，我们已发送验证邮件，请查收。';
        find('#mf-verification-help').textContent = '收到验证码时，请在此输入邮件中的 6 位验证码，10 分钟内有效；若邮件提示邮箱已有账号，可返回登录或找回密码。';
        const registrationCode = find('#mf-registration-code');
        registrationCode.disabled = !model.registrationCodeValid || !!model.pendingRequest;
        find('#mf-registration-code-submit').disabled = registrationCode.disabled || !/^[0-9]{6}$/.test(registrationCode.value);
        find('#mf-registration-code-status').textContent = model.pendingRequest?.kind === 'verify_registration' ? '正在验证…' : model.registrationCodeError;
        find('#mf-registration-restart').disabled = !!model.pendingRequest;
        const resend = find('#mf-resend');
        resend.hidden = options.canResendVerification !== true;
        resend.disabled = !model.capabilities.register || !model.registrationCodeValid || !!model.pendingRequest || model.resendSeconds > 0;
        resend.textContent = model.resendState === 'SENDING' ? '正在重新发送…'
            : model.resendSeconds ? `重新发送验证码（${model.resendSeconds} 秒）` : '重新发送验证码';
        find('#mf-resend-status').textContent = resend.hidden ? '' : model.resendState === 'FAILED' ? '暂时无法重新发送，请稍后再试。'
            : model.resendSeconds ? '请稍候再尝试重新发送。' : '';
        find('#mf-register-status').textContent = model.screen === 'REGISTER_SUBMITTING' ? '正在提交注册申请…'
            : model.registrationError === 'USERNAME_TAKEN' ? ''
            : model.registrationMessage || (['EMAIL_SUBMISSION_UNAVAILABLE', 'ACCOUNT_SUBMISSION_UNAVAILABLE'].includes(model.registrationError)
                ? '暂时无法开始验证，请核对填写信息后重试。'
                : model.registrationError === 'REQUEST_FAILED' ? '暂时无法确认注册结果，请返回登录尝试或稍后重试。' : '');
        find('#mf-register-status').classList.toggle('mf-error', !!model.registrationError);
        register.querySelectorAll('input, [data-mf-password-reveal]').forEach(input => input.disabled = model.screen === 'REGISTER_SUBMITTING');
        find('#mf-forgot-submit').disabled = !model.capabilities.forgot_password || model.screen !== 'FORGOT_REQUEST' || !identity.value.trim() || !identity.validity.valid || model.resendSeconds > 0;
        identity.disabled = model.screen === 'FORGOT_SUBMITTING';
        find('#mf-forgot-status').textContent = model.screen === 'FORGOT_SUBMITTING' ? '正在发送验证码…' : model.resetError;
        find('#mf-forgot-submit').textContent = model.resendSeconds ? `发送验证码（${model.resendSeconds} 秒）` : '发送验证码';
        find('#mf-forgot-receipt').textContent = '如果绑定邮箱符合条件，我们会发送 6 位验证码。请在当前窗口输入，10 分钟内有效。';
        const code = find('#mf-reset-code');
        code.disabled = model.screen !== 'FORGOT_SENT' || !!model.pendingRequest;
        find('#mf-code-submit').disabled = code.disabled || !/^[0-9]{6}$/.test(code.value);
        find('#mf-code-status').textContent = model.screen === 'CODE_VERIFYING' ? '正在验证…' : model.resetError;
        find('#mf-code-resend').disabled = !!model.pendingRequest || model.resendSeconds > 0;
        find('#mf-code-resend').textContent = model.resendSeconds ? `重新发送验证码（${model.resendSeconds} 秒）` : '重新发送验证码';
        find('#mf-reset-restart').hidden = model.resetState === 'valid';
        find('#mf-reset-restart').disabled = !!model.pendingRequest;
        find('#mf-reset-status').textContent = model.resetError || (model.screen === 'RESET_WAITING' ? '正在等待账号服务确认重置结果。'
            : ({valid: '请设置新密码。', invalid: '验证无效，请返回登录重新开始。',
                expired: '验证已过期，请返回登录重新开始。', used: '验证已使用，请返回登录重新开始。'})[model.resetState]);
        if (model.resetState !== 'valid') reset.reset();
        reset.querySelectorAll('input, [data-mf-password-reveal]').forEach(input => input.disabled = model.resetState !== 'valid' || model.screen === 'RESET_WAITING');
        validateRegister(); registerPair.refresh(); resetPair.refresh();
        renderAvailability('username', '#mf-username-availability');
    };
    root.querySelectorAll('[data-navigate]').forEach(button => on(button, 'click', () => model.navigate(button.dataset.navigate)));
    on(find('[data-intent="login"]'), 'click', () => emit('login'));
    on(find('#mf-add-account'), 'click', () => { model.addAccount(); emit('add_account'); });
    on(find('#mf-remember-password'), 'change', event => model.setRememberPassword(event.target.checked));
    on(find('#mf-auto-login'), 'change', event => model.setAutoLogin(event.target.checked));
    on(username, 'input', () => { model.usernameEdited(username.value); scheduleRegistrationCheck('username', username); validateRegister(); registerPair.refresh(); });
    on(username, 'blur', () => scheduleRegistrationCheck('username', username, true));
    on(email, 'input', () => { model.emailEdited(); validateRegister(); registerPair.refresh(); });
    on(email, 'change', () => { model.emailEdited(); validateRegister(); registerPair.refresh(); });
    on(identity, 'input', render);
    on(identity, 'change', render);
    on(register, 'submit', event => {
        const blocked = event.defaultPrevented;
        event.preventDefault();
        if (!blocked && validateRegister() && model.requestRegistration()) emit('register');
    });
    on(forgot, 'submit', event => {
        event.preventDefault();
        if (identity.value.trim() && identity.validity.valid && model.requestForgot()) emit('forgot_password');
    });
    on(reset, 'submit', event => {
        const blocked = event.defaultPrevented;
        event.preventDefault();
        if (!blocked && model.requestReset()) { emit('reset_password'); reset.reset(); resetPair.reset(); }
    });
    on(find('#mf-reset-code'), 'input', render);
    on(find('#mf-code-form'), 'submit', event => {
        event.preventDefault();
        if (/^[0-9]{6}$/.test(find('#mf-reset-code').value) && model.requestCodeVerify()) emit('verify_code');
    });
    on(find('#mf-code-resend'), 'click', () => { if (model.requestCodeResend()) emit('resend_code'); });
    on(find('#mf-reset-restart'), 'click', () => emit('restart_reset'));
    on(find('#mf-resend'), 'click', () => { if (model.requestResend()) emit('resend_verification'); });
    on(find('#mf-registration-code'), 'input', render);
    on(find('#mf-registration-code-form'), 'submit', event => {
        event.preventDefault();
        if (/^[0-9]{6}$/.test(find('#mf-registration-code').value) && model.requestRegistrationVerify()) emit('verify_registration');
    });
    on(find('#mf-registration-restart'), 'click', () => emit('restart_registration'));
    const unsubscribe = model.subscribe(render);
    const timer = setInterval(() => { if (['VERIFY_PENDING','FORGOT_REQUEST','FORGOT_SENT','CODE_INVALID'].includes(model.screen)) render(); }, 1000);
    const instance = {model, render, dispose() {
        clearInterval(timer); registrationCheckTimers.forEach(clearTimeout); registrationCheckTimers.clear();
        unsubscribe(); listeners.abort(); registerPair.destroy(); resetPair.destroy();
        root.querySelectorAll('form').forEach(form => form.reset()); choices.replaceChildren(); mounted.delete(root);
    }};
    mounted.set(root, instance);
    render();
    return instance;
}

const root = document.getElementById('mf-account-lifecycle');
if (root && !window.qt) { root.hidden=false; mountAccountLifecycle(root); } // Native adapter supplies capabilities explicitly.
