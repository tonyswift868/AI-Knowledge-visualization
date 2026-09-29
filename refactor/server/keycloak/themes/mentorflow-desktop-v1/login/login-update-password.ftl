<#-- Based on Keycloak 26.7.3 base/login/login-update-password.ftl (Apache-2.0).
     MentorFlow: independent confirmation, first-field reveal, live equality feedback.
     Keycloak owns the authenticated action, password policy and credential update. -->
<#import "template.ftl" as layout>
<#import "password-commons.ftl" as passwordCommons>
<#import "mentorflow-password-pair.ftl" as passwordPair>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('password','password-confirm'); section>
    <#if section = "header">
        ${msg("updatePasswordTitle")}
    <#elseif section = "form">
        <form id="kc-passwd-update-form" class="${properties.kcFormClass!}" action="${url.loginAction}" method="post">
            <@passwordPair.fields/>
            <div class="${properties.kcFormGroupClass!}">
                <@passwordCommons.logoutOtherSessions/>
                <div id="kc-form-buttons" class="${properties.kcFormButtonsClass!}">
                    <input name="login" id="kc-reset-submit" data-mf-pair-submit disabled
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
