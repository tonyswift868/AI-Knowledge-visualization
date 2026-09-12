<#-- Future Desktop presentation shell; deliberately not a native Keycloak page
     override or an enabled route. No credentials leave these UI-only forms. -->
<#import "template.ftl" as layout>
<#import "mentorflow-password-pair.ftl" as passwordPair>
<@layout.registrationLayout displayMessage=false; section>
    <#if section = "header">MentorFlow
    <#elseif section = "form">
        <link rel="stylesheet" href="${url.resourcesPath}/css/account-lifecycle.css">
        <div id="mf-account-lifecycle">
            <section data-screen="LOGIN">
                <h2 tabindex="-1">登录 MentorFlow</h2>
                <p id="mf-selected-account"></p>
                <button type="button" data-intent="login" class="mf-primary">继续登录</button>
                <div class="mf-preferences">
                    <label><input id="mf-remember-password" type="checkbox"> 记住密码</label>
                    <label><input id="mf-auto-login" type="checkbox" disabled> 自动登录</label>
                </div>
                <button type="button" data-navigate="ACCOUNT_SELECT">选择已保存账号</button>
                <div class="mf-actions">
                    <button type="button" data-navigate="REGISTER" disabled title="当前账号服务暂未开放注册">注册账号</button>
                    <button type="button" data-navigate="FORGOT_REQUEST" disabled title="当前账号服务暂未开放密码找回">忘记密码</button>
                </div>
            </section>
            <section data-screen="ACCOUNT_SELECT" hidden>
                <h2 tabindex="-1">欢迎回来</h2>
                <div id="mf-saved-accounts" role="group" aria-label="已保存账号"></div>
                <p id="mf-no-saved-accounts">还没有已保存账号。</p>
                <button type="button" id="mf-add-account">+ 添加其他账号</button>
                <button type="button" data-navigate="LOGIN">返回登录</button>
            </section>
            <section data-screen="REGISTER REGISTER_SUBMITTING" hidden>
                <h2 tabindex="-1">创建你的账号</h2>
                <form id="mf-register-form" method="dialog" novalidate>
                    <div class="form-group">
                        <label for="mf-register-username">账号</label>
                        <input class="pf-c-form-control" id="mf-register-username" name="username" autocomplete="username" required aria-describedby="mf-username-hint mf-username-error">
                        <p id="mf-username-hint" class="mf-hint">账号需要唯一。</p>
                        <p id="mf-username-error" class="mf-error" aria-live="polite"></p>
                    </div>
                    <div class="form-group">
                        <label for="mf-register-email">邮箱</label>
                        <input class="pf-c-form-control" id="mf-register-email" name="email" type="email" autocomplete="email" required aria-describedby="mf-email-error">
                        <p id="mf-email-error" class="mf-error" aria-live="polite"></p>
                    </div>
                    <p id="mf-register-password-policy" class="mf-hint">密码长度为 8–128 个字符，无需特定字符组合。</p>
                    <@passwordPair.fields prefix="register-password" firstName="password" firstLabel="password" confirmLabel="mentorflowRegisterConfirm" serverErrors=false autofocus=false/>
                    <p id="mf-register-policy-error" class="mf-error" aria-live="polite"></p>
                    <p id="mf-register-status" role="status"></p>
                    <button type="submit" data-mf-pair-submit class="mf-primary" disabled>注册账号</button>
                </form>
                <button type="button" data-navigate="LOGIN">返回登录</button>
            </section>
            <section data-screen="VERIFY_PENDING" hidden>
                <h2 tabindex="-1">验证你的邮箱</h2>
                <p>验证邮件已发送到：</p>
                <p id="mf-masked-email" class="mf-account-label"></p>
                <p class="mf-hint">请打开邮件完成验证，然后返回登录。</p>
                <p id="mf-resend-status" role="status" aria-live="polite"></p>
                <button type="button" id="mf-resend" disabled>重新发送验证邮件</button>
                <button type="button" data-navigate="LOGIN">返回登录</button>
            </section>
            <section data-screen="FORGOT_REQUEST FORGOT_SUBMITTING" hidden>
                <h2 tabindex="-1">找回密码</h2>
                <form id="mf-forgot-form" method="dialog" novalidate>
                    <label for="mf-forgot-identity">账号或邮箱</label>
                    <input class="pf-c-form-control" id="mf-forgot-identity" name="username" autocomplete="username" required>
                    <p id="mf-forgot-status" role="status"></p>
                    <button type="submit" id="mf-forgot-submit" class="mf-primary" disabled>发送重置邮件</button>
                </form>
                <button type="button" data-navigate="LOGIN">返回登录</button>
            </section>
            <section data-screen="FORGOT_SENT" hidden>
                <h2 tabindex="-1">请检查邮箱</h2>
                <p id="mf-forgot-receipt" role="status"></p>
                <button type="button" data-navigate="LOGIN">返回登录</button>
            </section>
            <section data-screen="RESET_PASSWORD RESET_WAITING" hidden>
                <h2 tabindex="-1">设置新密码</h2>
                <p id="mf-reset-status" role="status"></p>
                <form id="mf-reset-form" method="dialog" novalidate>
                    <@passwordPair.fields prefix="reset-password" serverErrors=false autofocus=false/>
                    <button type="submit" data-mf-pair-submit class="mf-primary" disabled>设置新密码</button>
                </form>
                <button type="button" data-navigate="LOGIN">返回登录</button>
            </section>
        </div>
        <script type="module" src="${url.resourcesPath}/js/account-lifecycle.js"></script>
    </#if>
</@layout.registrationLayout>
