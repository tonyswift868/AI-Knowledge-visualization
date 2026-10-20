package win.clannad.mentorflow.account;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Service;
import win.clannad.mentorflow.identity.IdentityDirectory;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySettings;

/** Lifecycle business rules ported from lifecycle_api.py and password_sessions.py. */
@Service
public final class LifecycleService146 {
    public static final String PREFIX="/api/enterprise/auth/lifecycle";
    private static final String RESET_UNAVAILABLE="重置服务暂不可用，请稍后重试。";
    private final ObjectProvider<NativeIdentityBridge> bridges;
    private final ObjectProvider<NativeSessionService> sessions;
    private final ObjectProvider<IdentityDirectory> directory;
    private final ObjectProvider<IdentitySessionRevocation146> oidcRevocation;
    private final AtomicInteger resetJobs=new AtomicInteger();

    public LifecycleService146(ObjectProvider<NativeIdentityBridge> bridges,
            ObjectProvider<NativeSessionService> sessions,ObjectProvider<IdentityDirectory> directory,
            ObjectProvider<IdentitySessionRevocation146> oidcRevocation) {
        this.bridges=bridges;this.sessions=sessions;this.directory=directory;
        this.oidcRevocation=oidcRevocation;
    }

    public record Result(int status,Map<String,Object> body,boolean pragma) {}
    private static Result ok(Map<String,Object> body){return new Result(200,body,false);}
    private static Result accepted(Map<String,Object> body){return new Result(202,body,false);}
    private static IdentityFailure failure(int status,String code,String message){return new IdentityFailure(status,code,message);}
    private static LinkedHashMap<String,Object> obj(Object... pairs){var m=new LinkedHashMap<String,Object>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;}

    private NativeIdentityBridge bridge(){NativeIdentityBridge b=bridges.getIfAvailable();if(b==null||!b.lifecycleActive())throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","当前账号服务暂未开放此功能。");return b;}
    public boolean lifecycleActive(){try{bridge();return true;}catch(Exception unavailable){return false;}}

    public Result capabilities(){boolean enabled=lifecycleActive();return ok(obj("register",enabled,"forgot_password",enabled));}

    public Result register(Map<String,String> value,String peer){
        NativeIdentityBridge b=bridge();
        String username=IdentitySettings.stripIdentityWhitespace(value.get("username")),email=IdentitySettings.stripIdentityWhitespace(value.get("email")),password=value.get("password");
        if(username.isEmpty()||cp(username)>255||hasControl(username)||cp(email)>254||!email(email))return registrationError("INPUT_INVALID");
        if(cp(password)<8||cp(password)>128)return registrationError("INPUT_INVALID");
        var payload=obj("operation","registration-start","username",username,"password",password,"email",email);
        try{return registrationSent(registrationAuthority(b,payload),201);}finally{payload.clear();value.clear();}
    }

    public Result registrationCode(Map<String,String> value,String operation,String peer){
        NativeIdentityBridge b=bridge();
        String credential=value.get("credential"),code=value.get("code");
        if(!token43(credential)||operation.equals("verify")&&!digits(code,6))return registrationError("INPUT_INVALID");
        AccountRateLimit146.check("registration:"+sha(credential),"watch");
        var payload=obj("operation","registration-"+operation,"credential",credential);if(operation.equals("verify"))payload.put("code",code);
        try {
            JsonNode result=registrationAuthority(b,payload);
            if(operation.equals("resend"))return registrationSent(result,200);
            if("PENDING_COMMIT".equals(text(result,"status"))) {
                result=registrationAuthority(b,obj("operation","registration-status","credential",credential));
                if(!"VERIFIED".equals(text(result,"status")))return registrationError("UNAVAILABLE");
                return ok(obj("state","VERIFIED","message","注册成功，请登录"));
            }
            return registrationError(text(result,"status"));
        } finally{payload.clear();value.clear();}
    }

    public Result registrationCheck(Map<String,String> value,String peer){
        NativeIdentityBridge b=bridge();
        String field=value.get("field"),candidate=value.get("value");
        if(field.equals("username")) {if(candidate.isEmpty()||cp(candidate)>255||!candidate.equals(IdentitySettings.stripIdentityWhitespace(candidate))||candidate.codePoints().anyMatch(c->c<32))return inputCheckError();}
        else if(field.equals("email")){if(cp(candidate)>254||!email(candidate))return inputCheckError();}
        else return inputCheckError();
        if(field.equals("email")) {
            // Email availability is confirmed through the mailbox flow, never by this public check.
            return ok(obj("field","email","available",true));
        }
        try {
            String subject=findAccount(b,"username",candidate);
            return ok(obj("field","username","available",subject==null));
        } catch(Exception e){return new Result(503,obj("code","REGISTRATION_CHECK_UNAVAILABLE"),false);}
        finally{value.clear();}
    }

    public Result forgot(String peer){bridge();return new Result(410,obj("code","RESET_CODE_REQUIRED","message","请在 MentorFlow 软件的找回密码页面使用邮箱验证码；旧版入口已停用。"),false);}

    public Result resetRequest(Map<String,String> value,String peer){
        NativeIdentityBridge b=bridge();String email=value.get("email").strip();value.clear();
        if(cp(email)>254||!email(email))return new Result(400,obj("code","RESET_EMAIL_INVALID","message","请输入有效的绑定邮箱。"),false);
        int jobs=resetJobs.incrementAndGet();if(jobs>32){resetJobs.decrementAndGet();throw failure(429,"RESET_RATE_LIMITED","请稍后重试。");}
        String ticket=token();Thread.startVirtualThread(()->{try{
            JsonNode result=authority(b,obj("operation","email-code-request","transaction_id",ticket,"email",email));
            // Failure and absent accounts intentionally have the same anonymous response.
            if(!"ACCEPTED".equals(text(result,"status"))||!"SUBMITTED".equals(text(result,"delivery")))return;
        }catch(Exception ignored){}finally{resetJobs.decrementAndGet();}});
        return accepted(obj("state","CODE_SENT","transaction_id",ticket,"expires_in",600,"resend_after",60,"message","如果该邮箱绑定了可找回账号，我们会发送验证码，请查看邮箱。"));
    }

    public Result resetVerify(Map<String,String> value,String peer){NativeIdentityBridge b=bridge();String id=value.get("transaction_id"),code=value.get("code");value.clear();
        if(!token43(id)||!digits(code,6))return resetError("RESET_CODE_INVALID_OR_EXPIRED","验证码错误、已失效或尝试次数过多，请重新获取。");
        JsonNode result=authority(b,obj("operation","email-code-verify","transaction_id",id,"code",code));
        if(!"VERIFIED".equals(text(result,"status")))return resetError("RESET_CODE_INVALID_OR_EXPIRED","验证码错误、已失效或尝试次数过多，请重新获取。");
        if(!result.isObject()||result.size()!=3||!result.has("status")||!result.has("reset_proof")||!result.has("expires_in"))throw failure(503,"RESET_SERVICE_UNAVAILABLE",RESET_UNAVAILABLE);
        String proof=text(result,"reset_proof");if(!token43(proof)||!"300".equals(text(result,"expires_in")))throw failure(503,"RESET_SERVICE_UNAVAILABLE",RESET_UNAVAILABLE);
        return ok(obj("state","VERIFIED","reset_proof",proof,"expires_in",300));
    }

    public Result resetComplete(Map<String,String> value,String peer){NativeIdentityBridge b=bridge();NativeSessionService ns=requireSessions();
        String proof=value.get("reset_proof"),password=value.get("new_password"),confirm=value.get("confirm_password");value.clear();
        if(!token43(proof))return resetError("RESET_PROOF_INVALID_OR_EXPIRED","重置验证已失效，请重新获取验证码。");
        if(!password.equals(confirm))return resetError("PASSWORD_CONFIRMATION_MISMATCH","两次输入的密码不一致。");
        if(cp(password)<8||cp(password)>128)return resetError("PASSWORD_POLICY_REJECTED","密码须为 8–128 位，并符合账号密码规则。");
        JsonNode result=authority(b,obj("operation","email-code-complete","reset_proof",proof,"new_password",password));
        String status=text(result,"status");
        if("PENDING_COMMIT".equals(status)){result=authority(b,obj("operation","email-code-status","reset_proof",proof));status=text(result,"status");if(!"COMPLETED".equals(status))throw failure(503,"RESET_SERVICE_UNAVAILABLE","重置尚未确认，请重新获取验证码。");}
        if("SAME_CURRENT_PASSWORD".equals(status))return resetError("NEW_PASSWORD_SAME_AS_CURRENT","新密码不能与当前密码相同");
        if("PASSWORD_POLICY_REJECTED".equals(status))return resetError("PASSWORD_POLICY_REJECTED","密码不符合账号密码规则，请修改后重试。");
        if("INVALID".equals(status))return resetError("RESET_PROOF_INVALID_OR_EXPIRED","重置验证已失效，请重新获取验证码。");
        String subject=text(result,"subject");if(!"COMPLETED".equals(status)||!result.isObject()||result.size()!=2||!validSubject(subject))throw failure(503,"RESET_SERVICE_UNAVAILABLE","重置服务暂不可用，请稍后重试。");
        ns.revokeSubject(subject);
        String accountId=accountId(ns.issuer(),subject);
        return ok(obj("state","COMPLETED","account_id",accountId,"message","密码已重置，请使用新密码登录 MentorFlow。"));
    }

    public Actor authorizeProtected(Jwt jwt,HttpServletRequestData request){return protectedActor(jwt,request);}
    public Result emailProfile(Jwt jwt,HttpServletRequestData request){return emailProfile(protectedActor(jwt,request));}
    public Result emailProfile(Actor context){JsonNode result=emailAuthority(context,"profile",new LinkedHashMap<>());return ok(emailProfilePayload(result));}

    public Result emailRequest(Jwt jwt,HttpServletRequestData request,Map<String,String> value,String peer){return emailRequest(protectedActor(jwt,request),value);}
    public Result emailRequest(Actor context,Map<String,String> value){String email=IdentitySettings.stripIdentityWhitespace(value.get("email"));value.clear();if(cp(email)>254||!email(email))return resetError("EMAIL_INPUT_INVALID","请输入有效邮箱。");String transaction=token();JsonNode result=emailAuthority(context,"request",obj("transaction_id",transaction,"email",email));if(!result.isObject()||result.size()!=1||!"SENT".equals(text(result,"status")))throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","验证码暂未发送，请稍后重试。");return accepted(obj("state","CODE_SENT","transaction_id",transaction,"expires_in",600,"resend_after",60));}

    public Result emailVerify(Jwt jwt,HttpServletRequestData request,Map<String,String> value,String peer){return emailVerify(protectedActor(jwt,request),value);}
    public Result emailVerify(Actor context,Map<String,String> value){String transaction=value.get("transaction_id"),code=value.get("code");value.clear();if(!token43(transaction)||!digits(code,6))return resetError("EMAIL_CODE_INVALID_OR_EXPIRED","验证码错误或已失效，请重新获取。");JsonNode result=emailAuthority(context,"verify",obj("transaction_id",transaction,"code",code));if(!result.isObject()||result.size()!=1||!"PENDING_COMMIT".equals(text(result,"status")))throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","邮箱验证尚未确认，请稍后重试。");result=emailAuthority(context,"status",obj("transaction_id",transaction));Map<String,Object> profile=emailProfilePayload(result);if(!Boolean.TRUE.equals(profile.get("email_verified")))throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","邮箱验证尚未确认。");var body=obj("state","EMAIL_VERIFIED");body.putAll(profile);return ok(body);}

    public Result changePassword(Jwt jwt,HttpServletRequestData request,Map<String,String> value,String peer){return changePassword(protectedActor(jwt,request),value,peer);}
    public Result changePassword(Actor context,Map<String,String> value,String peer){
        String current=value.get("current_password"),next=value.get("new_password");
        try {
            if(current.isEmpty()||cp(current)>1024)throw new IllegalArgumentException();
            if(cp(next)<8||cp(next)>128)throw new IllegalArgumentException();
            requireSessions().throttle(context.subject,peer);
            String username=usernameForSubject(bridge(),context.subject);
            NativeSessionService.NativeIdentity verified;
            try{verified=bridge().authenticate(username,current);}catch(IdentityFailure failure){if("NATIVE_CREDENTIALS_INVALID".equals(failure.code))return new Result(400,obj("code","CURRENT_PASSWORD_INVALID","message","当前密码不正确。"),false);throw failure;}
            if(!verified.issuer().equals(context.issuer)||!verified.subject().equals(context.subject))throw failure(401,"NATIVE_CREDENTIALS_INVALID","账号或密码不正确");
            if(current.equals(next))return resetError("NEW_PASSWORD_SAME_AS_CURRENT","新密码不能与当前密码相同");
            return changeAndRevoke(context,next);
        } catch(IllegalArgumentException e){return new Result(400,obj("code","PASSWORD_INPUT_INVALID","message","请检查密码长度。"),false);}
        catch(IdentityFailure e){if("PASSWORD_INPUT_INVALID".equals(e.code))return new Result(400,obj("code",e.code,"message","请检查密码长度。"),false);throw e;}
        catch(Exception e){throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","密码暂未修改，请稍后重试。");}
        finally{value.clear();current=null;next=null;}
    }

    private Result changeAndRevoke(Actor context,String password){
        NativeSessionService ns=requireSessions();boolean changed=false,known=false,nativeDone=false,oidcDone=false,providerDone=false;
        try{var updated=bridge().adminCall("PUT","/users/"+context.subject+"/reset-password",obj("type","password","value",password,"temporary",false),Map.of());if(updated.status()==204){changed=true;known=true;}}
        catch(Exception ignored){}
        finally{password=null;try{ns.revokeSubject(context.subject);nativeDone=true;}catch(Exception ignored){};
            IdentitySessionRevocation146 oidc=oidcRevocation.getIfAvailable();if(oidc==null)oidcDone=true;else try{oidcDone=oidc.revokeBySubject(context.issuer,context.subject);}catch(Exception ignored){} }
        providerDone=providerLogout(context.subject);
        String state;int status;
        if(!known){state="PASSWORD_CHANGE_OUTCOME_UNKNOWN";status=503;}
        else if(!(nativeDone&&oidcDone&&providerDone)){state="PASSWORD_CHANGED_REVOCATION_PENDING";status=503;}
        else {state="PASSWORD_CHANGED";status=200;}
        var out=obj("state",state,"reauthentication_required",true,"revocation_scope","CURRENT_PROCESS_AND_KEYCLOAK_ONLINE_SESSIONS","oidc_bearer_policy","PROVIDER_VALIDATION_OR_EXPIRY","oidc_offline_sessions","NOT_CONFIRMED","native_sessions_revoked",nativeDone,"backend_oidc_sessions_revoked",oidcDone,"keycloak_logout_confirmed",providerDone,"password_changed",known?changed:null);if(status!=200)out.put("code",state);
        return new Result(status,out,false);
    }

    public Result revokeSessions(Jwt jwt,HttpServletRequestData request,String peer){return revokeSessions(protectedActor(jwt,request));}
    public Result revokeSessions(Actor context){boolean nativeDone=false,oidcDone=false;try{requireSessions().revokeSubject(context.subject);nativeDone=true;}catch(Exception ignored){}IdentitySessionRevocation146 hook=oidcRevocation.getIfAvailable();if(hook==null)oidcDone=true;else try{oidcDone=hook.revokeBySubject(context.issuer,context.subject);}catch(Exception ignored){}boolean provider=providerLogout(context.subject);boolean complete=nativeDone&&oidcDone&&provider;var out=obj("state",complete?"SESSION_REVOCATION_CONFIRMED":"SESSION_REVOCATION_PENDING","reauthentication_required",true,"revocation_scope","CURRENT_PROCESS_AND_KEYCLOAK_ONLINE_SESSIONS","oidc_bearer_policy","PROVIDER_VALIDATION_OR_EXPIRY","oidc_offline_sessions","NOT_CONFIRMED","native_sessions_revoked",nativeDone,"backend_oidc_sessions_revoked",oidcDone,"keycloak_logout_confirmed",provider);if(!complete)out.put("code","SESSION_REVOCATION_PENDING");return new Result(complete?200:503,out,false);}

    private boolean providerLogout(String subject){try{var r=bridge().adminCall("POST","/users/"+subject+"/logout",null,Map.of());return r.status()==204;}catch(Exception e){return false;}}
    private String usernameForSubject(NativeIdentityBridge b,String subject){
        try {
            if(!subject.matches("[A-Za-z0-9_-]{1,255}"))throw new IllegalArgumentException();
            var r=b.adminCall("GET","/users/"+subject,null,Map.of());JsonNode n=r.body();
            if(r.status()!=200||n==null||!n.isObject()||!subject.equals(text(n,"id"))
                    ||text(n,"username").isEmpty()||cp(text(n,"username"))>255
                    ||!n.path("enabled").isBoolean()||!n.path("enabled").booleanValue())throw new IllegalArgumentException();
            return text(n,"username");
        } catch(Exception failure) {
            // Python converts a LifecycleError from this lookup before it ever
            // reaches the password mutation/revocation operation.
            throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","密码暂未修改，请稍后重试。");
        }
    }

    public record Actor(String issuer,String subject,String membership,String organization,String workspace){}
    public record HttpServletRequestData(String scheme,String origin,String authorization,boolean hasCookies,String organization,String workspace,Map<String,Object> actorContext){}
    private Actor protectedActor(Jwt jwt,HttpServletRequestData request){
        if(!"https".equalsIgnoreCase(request.scheme)||request.origin!=null&&!request.origin.isEmpty()||request.hasCookies||request.authorization==null||!request.authorization.startsWith("Bearer mfn1_a_")||request.authorization.indexOf(' ',7)>=0)
            throw failure(403,"LIFECYCLE_TRANSPORT_REQUIRED","需要安全的原生账号连接。");
        NativeSessionService ns=requireSessions();Jwt fresh=ns.authenticateAccess(request.authorization.substring(7));
        String issuer=fresh.getClaimAsString("iss"),subject=fresh.getSubject();if(jwt==null||!Objects.equals(issuer,jwt.getClaimAsString("iss"))||!Objects.equals(subject,jwt.getSubject()))throw failure(409,"ENTERPRISE_IDENTITY_CONTEXT_CHANGED","Enterprise identity context changed during authorization");
        if(request.organization==null||request.organization.isBlank())throw failure(403,"ENTERPRISE_ORGANIZATION_REQUIRED","Bearer identity requires a selected organization");
        String workspace=request.workspace==null||request.workspace.isBlank()?null:request.workspace;
        IdentityDirectory dir=directory.getIfAvailable();if(dir==null)throw failure(503,"ENTERPRISE_IDENTITY_NOT_READY","Enterprise identity runtime is not ready");
        dir.ensureOpen(issuer,subject);IdentityDirectory.Membership membership=dir.resolve(issuer,subject,request.organization,workspace);
        if(membership==null)throw failure(403,"MEMBERSHIP_NOT_ACTIVE","Membership is not active in the selected tenant scope");
        Map<String,Object> ctx=request.actorContext;if(ctx==null||!Objects.equals(ctx.get("issuer"),issuer)||!Objects.equals(ctx.get("subject_id"),subject)||!Objects.equals(ctx.get("membership_id"),membership.id())||!"HUMAN".equals(ctx.get("actor_kind")))throw failure(409,"ENTERPRISE_IDENTITY_CONTEXT_CHANGED","Enterprise identity context changed during authorization");
        return new Actor(issuer,subject,membership.id(),request.organization,workspace);
    }

    private JsonNode emailAuthority(Actor actor,String operation,Map<String,Object> fields){var payload=new LinkedHashMap<String,Object>();payload.put("operation","email-binding-"+operation);payload.put("subject",actor.subject);payload.putAll(fields);JsonNode n;try{n=bridge().authority(payload,2048,"ACCOUNT_ACTION_UNAVAILABLE","账号邮箱服务暂不可用，请稍后重试。");}catch(Exception e){throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","账号邮箱服务暂不可用，请稍后重试。");}finally{payload.clear();}String status=text(n,"status");if(operation.equals("status")&&!"PROFILE".equals(status))throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","邮箱验证尚未确认，请刷新账号资料或重新获取验证码。");Map<String,String[]> errors=Map.of("INVALID",new String[]{"EMAIL_CODE_INVALID_OR_EXPIRED","验证码错误、已失效或尝试次数过多，请重新获取。","400"},"EMAIL_ALREADY_BOUND",new String[]{"EMAIL_ALREADY_BOUND","账号已有绑定邮箱，本页仅支持验证该邮箱。","409"},"EMAIL_ALREADY_VERIFIED",new String[]{"EMAIL_ALREADY_VERIFIED","当前邮箱已验证。","409"},"RATE_LIMITED",new String[]{"EMAIL_CODE_RATE_LIMITED","发送过于频繁，请稍后重试。","429"});if(errors.containsKey(status)){String[] e=errors.get(status);throw failure(Integer.parseInt(e[2]),e[0],e[1]);}return n;}

    private Map<String,Object> emailProfilePayload(JsonNode result){if(!result.isObject()||result.size()!=3||!"PROFILE".equals(text(result,"status"))||!result.path("email").isTextual()||cp(text(result,"email"))>254||!Set.of("true","false").contains(text(result,"email_verified"))||text(result,"email").isEmpty()&&"true".equals(text(result,"email_verified")))throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","账号资料暂不可用。");String email=text(result,"email");boolean verified="true".equals(text(result,"email_verified"));return obj("email",email.isEmpty()?null:email,"email_verified",verified,"can_bind",email.isEmpty(),"can_verify",!email.isEmpty()&&!verified);}

    private JsonNode authority(NativeIdentityBridge b,Map<String,Object> payload){try{return b.authority(payload,2048,"RESET_SERVICE_UNAVAILABLE",RESET_UNAVAILABLE);}catch(IdentityFailure f){throw f;}catch(Exception e){throw failure(503,"RESET_SERVICE_UNAVAILABLE",RESET_UNAVAILABLE);}finally{payload.clear();}}
    private JsonNode registrationAuthority(NativeIdentityBridge b,Map<String,Object> payload){try{return b.authority(payload,2048,"REGISTRATION_UNAVAILABLE","注册结果暂未确认，请稍后重试；若账号已创建，10 分钟后可重新开始。");}catch(Exception e){throw failure(503,"REGISTRATION_UNAVAILABLE","注册结果暂未确认，请稍后重试；若账号已创建，10 分钟后可重新开始。");}finally{payload.clear();}}
    private JsonNode admin(NativeIdentityBridge b,String method,String path,Map<String,Object> payload,Map<String,String> params){var r=b.adminCall(method,path,payload,params);if(r.status()<200||r.status()>=300)throw failure(503,"ACCOUNT_ACTION_UNAVAILABLE","账号服务暂不可用。");return r.body();}
    private String findAccount(NativeIdentityBridge b,String field,String value){Map<String,String> params=field.equals("username")?Map.of("username",value,"exact","true","max","2"):Map.of("email",value,"exact","true","max","2");JsonNode rows=admin(b,"GET","/users",null,params);if(!rows.isArray()||rows.size()>1)throw new IllegalStateException();if(rows.isEmpty())return null;JsonNode row=rows.get(0);String candidate=text(row,field),subject=text(row,"id");if(candidate.isEmpty()||!candidate.equalsIgnoreCase(value)||!subject.matches("[A-Za-z0-9_-]{1,255}"))throw new IllegalStateException();return subject;}

    private static Result registrationSent(JsonNode result,int status){if(!"CODE_SENT".equals(text(result,"status"))||!token43(text(result,"credential"))||!"600".equals(text(result,"expires_in"))||!"60".equals(text(result,"resend_after")))return registrationError(text(result,"status"));return new Result(status,obj("state","CODE_SENT","credential",text(result,"credential"),"expires_in",600,"resend_after",60),false);}
    private static Result registrationError(String code){Map<String,Object[]> errors=Map.ofEntries(Map.entry("USERNAME_ALREADY_IN_USE",new Object[]{409,"账号名已被使用；如刚注册但未验证，请在 10 分钟后用相同账号名和邮箱重新开始。"}),Map.entry("INPUT_INVALID",new Object[]{400,"请检查账号名、邮箱和密码。"}),Map.entry("PASSWORD_POLICY_REJECTED",new Object[]{400,"密码不符合规则，请使用 8–128 个字符并重试。"}),Map.entry("WRONG_CODE",new Object[]{400,"验证码错误，请重新输入。"}),Map.entry("EXPIRED",new Object[]{400,"注册凭据已过期，请用相同账号名和邮箱重新注册并获取验证码。"}),Map.entry("EXHAUSTED",new Object[]{400,"验证码错误已达 5 次，请重新发送验证码。"}),Map.entry("INVALID",new Object[]{400,"注册凭据无效或已使用，请返回登录或重新注册。"}),Map.entry("COOLDOWN",new Object[]{429,"请在 60 秒后重新发送验证码。"}),Map.entry("SEND_LIMIT",new Object[]{429,"发送次数已达每小时上限，请稍后重试。"}),Map.entry("UNAVAILABLE",new Object[]{503,"注册结果暂未确认，请稍后重试；若账号已创建，10 分钟后可重新开始。"}));if(!errors.containsKey(code))code="UNAVAILABLE";Object[] e=errors.get(code);String output=Set.of("USERNAME_ALREADY_IN_USE","PASSWORD_POLICY_REJECTED").contains(code)?code:"REGISTRATION_"+code;return new Result((int)e[0],obj("code",output,"message",e[1]),false);}
    private static Result inputCheckError(){return new Result(400,obj("code","REGISTRATION_INPUT_INVALID","message","请检查账号名或邮箱。"),false);}
    private static Result resetError(String code,String message){return new Result(400,obj("code",code,"message",message),false);}
    private NativeSessionService requireSessions(){NativeSessionService value=sessions.getIfAvailable();if(value==null)throw failure(503,"NATIVE_AUTH_NOT_READY","账号服务尚未准备好，请稍后重试。");return value;}
    private static int cp(String value){return value.codePointCount(0,value.length());}
    private static boolean hasControl(String value){return value.codePoints().anyMatch(c->c<32||c>=127&&c<=159);}
    private static boolean email(String value){return value.matches("(?U)[^\\s@]+@[^\\s@]+\\.[^\\s@]+");}
    private static boolean token43(String value){return value!=null&&value.matches("[A-Za-z0-9_-]{43}");}
    private static boolean digits(String value,int count){return value!=null&&value.matches("[0-9]{"+count+"}");}
    private static boolean validSubject(String value){return value!=null&&!value.isEmpty()&&cp(value)<=128;}
    private static String text(JsonNode node,String key){JsonNode value=node==null?null:node.get(key);return value!=null&&value.isTextual()?value.asText():"";}
    private static String token(){byte[] bytes=new byte[32];new java.security.SecureRandom().nextBytes(bytes);return java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);}
    private static String sha(String value){try{return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}catch(Exception e){throw new IllegalStateException(e);}}
    private static String accountId(String issuer,String subject){String i=sha(issuer),s=sha(subject);return "mfa1_"+sha("mentorflow.account.v1:"+i+":"+s);}
}
