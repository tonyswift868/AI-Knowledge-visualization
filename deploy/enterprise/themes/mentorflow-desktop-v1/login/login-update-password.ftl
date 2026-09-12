<#-- Based on Keycloak 26.7.3 base/login/login-update-password.ftl (Apache-2.0).
     MentorFlow: independent confirmation, first-field reveal, live equality feedback.
     Keycloak owns the authenticated action, password policy and credential update. -->
<#import "template.ftl" as layout>
<#import "password-commons.ftl" as passwordCommons>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('password','password-confirm'); section>
    <#if section = "header">
        ${msg("updatePasswordTitle")}
    <#elseif section = "form">
        <form id="kc-passwd-update-form" class="${properties.kcFormClass!}" action="${url.loginAction}" method="post">
            <div class="${properties.kcFormGroupClass!}">
                <label for="password-new" class="${properties.kcLabelClass!}">${msg("passwordNew")}</label>
                <div class="${properties.kcInputGroup!}" dir="ltr">
                    <input type="password" id="password-new" name="password-new" class="${properties.kcInputClass!}"
                           autofocus autocomplete="new-password" required
                           aria-describedby="input-error-password"
                           aria-invalid="${messagesPerField.existsError('password')?c}" />
                    <button class="${properties.kcFormPasswordVisibilityButtonClass!}" type="button"
                            data-mf-password-reveal aria-controls="password-new" aria-pressed="false"
                            data-label-show="${msg('showPassword')}" data-label-hide="${msg('hidePassword')}">
                        ${msg("showPassword")}
                    </button>
                </div>
                <span id="input-error-password" class="${properties.kcInputErrorMessageClass!}" aria-live="polite">
                    <#if messagesPerField.existsError('password')>
                        ${kcSanitize(messagesPerField.get('password'))?no_esc}
                    </#if>
                </span>
            </div>
            <div class="${properties.kcFormGroupClass!}">
                <label for="password-confirm" class="${properties.kcLabelClass!}">${msg("passwordConfirm")}</label>
                <input type="password" id="password-confirm" name="password-confirm" class="${properties.kcInputClass!}"
                       autocomplete="new-password" required
                       aria-describedby="input-error-password-confirm mf-password-mismatch"
                       aria-invalid="${messagesPerField.existsError('password-confirm')?c}" />
                <span id="input-error-password-confirm" class="${properties.kcInputErrorMessageClass!}" aria-live="polite">
                    <#if messagesPerField.existsError('password-confirm')>
                        ${kcSanitize(messagesPerField.get('password-confirm'))?no_esc}
                    </#if>
                </span>
                <span id="mf-password-mismatch" class="${properties.kcInputErrorMessageClass!}" aria-live="polite" hidden>
                    ${msg("mentorflowPasswordMismatch")}
                </span>
            </div>
            <div class="${properties.kcFormGroupClass!}">
                <@passwordCommons.logoutOtherSessions/>
                <div id="kc-form-buttons" class="${properties.kcFormButtonsClass!}">
                    <input name="login" id="kc-reset-submit" disabled
                           class="${properties.kcButtonClass!} ${properties.kcButtonPrimaryClass!} ${properties.kcButtonBlockClass!} ${properties.kcButtonLargeClass!}"
                           type="submit" value="${msg('mentorflowResetSubmit')}" />
                    <#if isAppInitiatedAction??>
                        <button class="${properties.kcButtonClass!} ${properties.kcButtonDefaultClass!} ${properties.kcButtonLargeClass!}"
                                type="submit" name="cancel-aia" value="true" formnovalidate>${msg("doCancel")}</button>
                    </#if>
                </div>
            </div>
            <noscript><p role="alert">${msg("mentorflowPasswordJavascriptRequired")}</p></noscript>
        </form>
        <script type="module" src="${url.resourcesPath}/js/password-pair.js"></script>
    </#if>
</@layout.registrationLayout>
