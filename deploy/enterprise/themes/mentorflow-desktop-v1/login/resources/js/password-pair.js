// Equality/visibility only. Native Keycloak still owns password policy and
// action validation. The future lifecycle shell can add presentation guards.
export function bindPasswordPair(form, {canSubmit = () => true, onEdited = () => {}} = {}) {
    const first = form.querySelector('[data-mf-new-password]');
    const confirm = form.querySelector('[data-mf-confirm-password]');
    const submit = form.querySelector('[data-mf-pair-submit]');
    const reveal = form.querySelector('[data-mf-password-reveal]');
    const mismatch = form.querySelector('[data-mf-password-mismatch]');
    const serverConfirmError = form.querySelector('[data-mf-confirm-error]');
    const listeners = new AbortController();
    const options = {signal: listeners.signal};
    let submitting = false;
    const refresh = () => {
        const complete = first.value.length > 0 && confirm.value.length > 0;
        const differs = complete && first.value !== confirm.value;
        mismatch.hidden = !differs;
        confirm.setAttribute('aria-invalid', String(differs || !!serverConfirmError.textContent.trim()));
        const allowed = complete && !differs && canSubmit();
        submit.disabled = submitting || !allowed;
        return allowed;
    };
    const edited = () => {
        serverConfirmError.textContent = '';
        onEdited();
        refresh();
    };
    for (const input of [first, confirm]) {
        input.addEventListener('input', edited, options);
        input.addEventListener('change', edited, options);
    }
    reveal.addEventListener('click', () => {
        const visible = first.type === 'password';
        first.type = visible ? 'text' : 'password';
        reveal.textContent = visible ? reveal.dataset.labelHide : reveal.dataset.labelShow;
        reveal.setAttribute('aria-pressed', String(visible));
    }, options);
    form.addEventListener('submit', event => {
        if (event.submitter?.name === 'cancel-aia') return;
        if (submitting || !refresh()) {
            event.preventDefault();
            return;
        }
        submitting = true;
        submit.disabled = true;
    }, options);
    const reset = () => {
        submitting = false;
        first.type = 'password';
        reveal.textContent = reveal.dataset.labelShow;
        reveal.setAttribute('aria-pressed', 'false');
        refresh();
    };
    window.addEventListener('pageshow', reset, options);
    reset();
    return {refresh, reset, destroy: () => listeners.abort()};
}

const nativeResetForm = document.getElementById('kc-passwd-update-form');
if (nativeResetForm) bindPasswordPair(nativeResetForm);
