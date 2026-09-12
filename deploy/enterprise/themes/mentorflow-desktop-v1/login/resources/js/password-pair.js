// Equality/visibility only. The native Keycloak POST validates the action state
// and the canonical realm password policy; this module never parses either.
const form = document.getElementById('kc-passwd-update-form');
if (form) {
    const first = form.elements.namedItem('password-new');
    const confirm = form.elements.namedItem('password-confirm');
    const submit = document.getElementById('kc-reset-submit');
    const reveal = form.querySelector('[data-mf-password-reveal]');
    const mismatch = document.getElementById('mf-password-mismatch');
    const serverConfirmError = document.getElementById('input-error-password-confirm');
    let submitting = false;

    const refresh = () => {
        const complete = first.value.length > 0 && confirm.value.length > 0;
        const differs = complete && first.value !== confirm.value;
        mismatch.hidden = !differs;
        confirm.setAttribute('aria-invalid', String(differs || !!serverConfirmError.textContent.trim()));
        submit.disabled = submitting || !complete || differs;
        return complete && !differs;
    };
    const edited = () => {
        // A prior server mismatch refers to the previous submission. Policy
        // errors remain separate and are never treated as a matching result.
        serverConfirmError.textContent = '';
        refresh();
    };
    for (const input of [first, confirm]) {
        input.addEventListener('input', edited);
        input.addEventListener('change', edited); // normal autofill/paste behavior
    }
    reveal.addEventListener('click', () => {
        const visible = first.type === 'password';
        first.type = visible ? 'text' : 'password';
        reveal.textContent = visible ? reveal.dataset.labelHide : reveal.dataset.labelShow;
        reveal.setAttribute('aria-pressed', String(visible));
    });
    form.addEventListener('submit', event => {
        if (event.submitter?.name === 'cancel-aia') return;
        if (submitting || !refresh()) {
            event.preventDefault();
            return;
        }
        submitting = true;
        submit.disabled = true;
    });
    window.addEventListener('pageshow', () => {
        submitting = false;
        // Back/forward navigation must not restore a revealed first field.
        first.type = 'password';
        reveal.textContent = reveal.dataset.labelShow;
        reveal.setAttribute('aria-pressed', 'false');
        refresh();
    });
    refresh();
}
