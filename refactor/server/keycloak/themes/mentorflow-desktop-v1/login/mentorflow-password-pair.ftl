<#-- Shared MentorFlow password UI. Native Keycloak owns credential validation. -->
<#macro fields prefix="password" firstName="password-new" firstLabel="passwordNew" confirmLabel="passwordConfirm" serverErrors=true autofocus=true>
    <#assign firstId=prefix + "-new" confirmId=prefix + "-confirm">
    <#assign firstError="input-error-" + prefix confirmError="input-error-" + prefix + "-confirm">
    <#assign mismatchId="mf-" + prefix + "-mismatch">
    <div class="${properties.kcFormGroupClass!}">
        <label for="${firstId}" class="${properties.kcLabelClass!}">${msg(firstLabel)}</label>
        <div class="${properties.kcInputGroup!}" dir="ltr">
            <input type="password" id="${firstId}" name="${firstName}" data-mf-new-password class="${properties.kcInputClass!}"
                   <#if autofocus>autofocus</#if> autocomplete="new-password" required
                   aria-describedby="${firstError}"
                   aria-invalid="${(serverErrors && messagesPerField.existsError('password'))?c}" />
            <button class="${properties.kcFormPasswordVisibilityButtonClass!}" type="button"
                    data-mf-password-reveal aria-controls="${firstId}" aria-pressed="false"
                    data-label-show="${msg('showPassword')}" data-label-hide="${msg('hidePassword')}">
                ${msg("showPassword")}
            </button>
        </div>
        <span id="${firstError}" data-mf-password-error class="${properties.kcInputErrorMessageClass!}" aria-live="polite">
            <#if serverErrors && messagesPerField.existsError('password')>
                ${kcSanitize(messagesPerField.get('password'))?no_esc}
            </#if>
        </span>
    </div>
    <div class="${properties.kcFormGroupClass!}">
        <label for="${confirmId}" class="${properties.kcLabelClass!}">${msg(confirmLabel)}</label>
        <input type="password" id="${confirmId}" name="password-confirm" data-mf-confirm-password class="${properties.kcInputClass!}"
               autocomplete="new-password" required aria-describedby="${confirmError} ${mismatchId}"
               aria-invalid="${(serverErrors && messagesPerField.existsError('password-confirm'))?c}" />
        <span id="${confirmError}" data-mf-confirm-error class="${properties.kcInputErrorMessageClass!}" aria-live="polite">
            <#if serverErrors && messagesPerField.existsError('password-confirm')>
                ${kcSanitize(messagesPerField.get('password-confirm'))?no_esc}
            </#if>
        </span>
        <span id="${mismatchId}" data-mf-password-mismatch class="${properties.kcInputErrorMessageClass!}" aria-live="polite" hidden>
            ${msg("mentorflowPasswordMismatch")}
        </span>
    </div>
</#macro>
