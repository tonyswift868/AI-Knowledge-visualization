// Presentation state only: no network, credential store, token or Vault access.
export const FORGOT_RECEIPT = '如果该账号存在，我们会向绑定邮箱发送密码重置邮件。';
export const FUTURE_PASSWORD_PRESENTATION = Object.freeze({minimum: 8, maximum: 128});
const SCREENS = new Set(['LOGIN', 'ACCOUNT_SELECT', 'REGISTER', 'REGISTER_SUBMITTING',
    'VERIFY_PENDING', 'FORGOT_REQUEST', 'FORGOT_SUBMITTING', 'FORGOT_SENT',
    'RESET_PASSWORD', 'RESET_WAITING', 'CODE_VERIFYING', 'CODE_INVALID']);

export function maskEmail(value) {
    if (typeof value !== 'string' || /\s/.test(value)) return '已绑定邮箱';
    const parts = value.split('@');
    if (parts.length !== 2 || !parts[0] || !parts[1]) return '已绑定邮箱';
    const local = Array.from(parts[0]);
    const visible = local.length >= 4 ? local[0] + '****' + local.slice(-2).join('')
        : local.length >= 2 ? local[0] + '****' : '****';
    return visible + '@' + parts[1];
}

export class AccountLifecycleState {
    constructor({capabilities = {}, accounts = [], initialScreen = 'LOGIN', now = () => performance.now()} = {}) {
        this.capabilities = Object.freeze({register: capabilities?.register === true,
            forgot_password: capabilities?.forgot_password === true});
        this.accounts = accounts.map(({key, displayName, safeId}) => {
            if (![key, displayName, safeId].every(v => typeof v === 'string' && v.trim())) {
                throw new TypeError('Saved-account presentation requires nonsecret labels');
            }
            return Object.freeze({key, displayName, safeId});
        });
        if (new Set(this.accounts.map(a => a.key)).size !== this.accounts.length) {
            throw new TypeError('Saved-account presentation keys must be unique');
        }
        this.screen = SCREENS.has(initialScreen) ? initialScreen : 'LOGIN';
        this.selectedAccount = null;
        this.resetError = '';
        this.registrationError = '';
        this.maskedEmail = '';
        this.resendState = 'READY';
        this.resendDeadline = 0;
        this.resetState = 'invalid';
        this.rememberPassword = false;
        this.autoLogin = false;
        this.requestSequence = 0;
        this.pendingRequest = null;
        this.now = now;
        this.listeners = new Set();
    }
    subscribe(listener) { this.listeners.add(listener); return () => this.listeners.delete(listener); }
    changed() { this.listeners.forEach(fn => fn()); }
    get requestId() { return this.pendingRequest?.id ?? null; }
    beginRequest(kind) { this.pendingRequest = {kind, id: ++this.requestSequence}; }
    acceptsResult(kind, id) { return this.pendingRequest?.kind === kind && this.pendingRequest.id === id; }
    navigate(screen) {
        if (!['LOGIN', 'ACCOUNT_SELECT', 'REGISTER', 'FORGOT_REQUEST'].includes(screen)) return false;
        if (screen === 'REGISTER' && !this.capabilities.register) return false;
        if (screen === 'FORGOT_REQUEST' && !this.capabilities.forgot_password) return false;
        this.screen = screen;
        this.registrationError = '';
        this.maskedEmail = '';
        this.resetState = 'invalid';
        this.resendState = 'READY';
        this.resendDeadline = 0;
        this.pendingRequest = null;
        this.changed();
        return true;
    }
    selectAccount(key) {
        const account = this.accounts.find(a => a.key === key);
        if (!account) return false;
        this.selectedAccount = account;
        this.rememberPassword = false; this.autoLogin = false;
        this.navigate('LOGIN'); // Selection is not authentication or a Vault switch.
        return true;
    }
    addAccount() {
        this.selectedAccount = null; this.rememberPassword = false; this.autoLogin = false;
        this.navigate('LOGIN');
    }
    requestRegistration() {
        if (!this.capabilities.register || this.screen !== 'REGISTER') return false;
        this.beginRequest('register');
        this.screen = 'REGISTER_SUBMITTING'; this.registrationError = ''; this.changed(); return true;
    }
    registrationResult(outcome, {requestId, email = '', retryAfter = 60} = {}) {
        if (this.screen !== 'REGISTER_SUBMITTING' || !this.acceptsResult('register', requestId)) return false;
        this.pendingRequest = null;
        if (outcome === 'verification_pending') {
            this.screen = 'VERIFY_PENDING'; this.maskedEmail = maskEmail(email);
            this.setCooldown(retryAfter);
        } else {
            this.screen = 'REGISTER';
            this.registrationError = outcome === 'username_taken' ? 'USERNAME_TAKEN' : 'REQUEST_FAILED';
        }
        this.changed(); return true;
    }
    usernameEdited() {
        if (this.registrationError === 'USERNAME_TAKEN') { this.registrationError = ''; this.changed(); }
    }
    get resendSeconds() { return Math.max(0, Math.ceil((this.resendDeadline - this.now()) / 1000)); }
    setCooldown(seconds) {
        const bounded = Number.isFinite(seconds) ? Math.max(0, Math.min(3600, Math.ceil(seconds))) : 60;
        this.resendDeadline = this.now() + bounded * 1000;
        this.resendState = 'READY';
    }
    requestResend() {
        if (!this.capabilities.register || this.screen !== 'VERIFY_PENDING' || this.resendSeconds || this.resendState === 'SENDING') return false;
        this.beginRequest('resend_verification');
        this.resendState = 'SENDING'; this.changed(); return true;
    }
    resendResult({requestId, accepted = false, retryAfter = 60} = {}) {
        if (this.screen !== 'VERIFY_PENDING' || this.resendState !== 'SENDING' || !this.acceptsResult('resend_verification', requestId)) return false;
        this.pendingRequest = null;
        if (accepted === true) this.setCooldown(retryAfter);
        else this.resendState = 'FAILED';
        this.changed(); return true;
    }
    requestForgot() {
        if (!this.capabilities.forgot_password || this.screen !== 'FORGOT_REQUEST') return false;
        this.beginRequest('forgot_password');
        this.screen = 'FORGOT_SUBMITTING'; this.changed(); return true;
    }
    forgotResult(requestId) {
        // No account-existence parameter, branch, message or observable state.
        if (this.screen !== 'FORGOT_SUBMITTING' || !this.acceptsResult('forgot_password', requestId)) return false;
        this.pendingRequest = null;
        this.screen = 'FORGOT_SENT'; this.changed(); return true;
    }
    presentReset(resetState) {
        this.pendingRequest = null;
        this.resetState = ['valid', 'invalid', 'expired', 'used'].includes(resetState) ? resetState : 'invalid';
        this.screen = 'RESET_PASSWORD'; this.changed();
    }
    requestReset() {
        if (!this.capabilities.forgot_password || this.screen !== 'RESET_PASSWORD' || this.resetState !== 'valid') return false;
        this.resetError = '';
        this.beginRequest('reset_password');
        this.screen = 'RESET_WAITING'; this.changed(); return true;
    }
    requestCodeVerify() {
        if (this.screen !== 'FORGOT_SENT' || this.pendingRequest) return false;
        this.beginRequest('verify_code'); this.resetError = '';
        this.screen = 'CODE_VERIFYING'; this.changed(); return true;
    }
    requestCodeResend() {
        if (!['FORGOT_SENT', 'CODE_INVALID'].includes(this.screen) || this.pendingRequest || this.resendSeconds) return false;
        this.beginRequest('resend_code'); this.resetError = ''; this.changed(); return true;
    }
    codeResult(value) {
        if (['expired', 'restart', 'completed'].includes(value.kind)) {
            this.pendingRequest = null;
        } else {
            const kind = {request: ['forgot_password', 'resend_code'], verify: ['verify_code'], complete: ['reset_password']}[value.kind];
            if (!kind?.some(k => this.acceptsResult(k, value.requestId))) return false;
            this.pendingRequest = null;
        }
        this.resetError = value.message || '';
        this.setCooldown(value.resendAfter || 0);
        if (value.kind === 'completed') { this.resetState='used'; this.screen='LOGIN'; }
        else if (value.stage === 'password') { this.resetState='valid'; this.screen='RESET_PASSWORD'; }
        else if (value.stage === 'code') { this.resetState='invalid'; this.screen='FORGOT_SENT'; }
        else if (value.stage === 'email') { this.resetState='invalid'; this.screen='FORGOT_REQUEST'; }
        else if (['RESET_PASSWORD','RESET_WAITING'].includes(this.screen)) { this.resetState='expired'; this.screen='RESET_PASSWORD'; }
        else { this.resetState='invalid'; this.screen='CODE_INVALID'; }
        this.changed(); return true;
    }
    setRememberPassword(checked) {
        this.rememberPassword = checked === true;
        if (!this.rememberPassword) this.autoLogin = false;
        this.changed();
    }
    setAutoLogin(checked) { this.autoLogin = this.rememberPassword && checked === true; this.changed(); }
}
