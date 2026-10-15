package win.clannad.mentorflow.account;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import win.clannad.mentorflow.identity.IdentityFailure;

/** HTTP adapter for the public and authenticated account lifecycle endpoints. */
@RestController
@RequestMapping(LifecycleService146.PREFIX)
public final class LifecycleController146 {
    private static final ObjectMapper JSON=JsonMapper.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    private final ObjectProvider<LifecycleService146> services;

    public LifecycleController146(ObjectProvider<LifecycleService146> services) {this.services=services;}

    @GetMapping("/capabilities") public ResponseEntity<byte[]> capabilities(HttpServletRequest request){
        try{transport(request,true);LifecycleService146 service=services.getIfAvailable();boolean enabled=service!=null&&service.lifecycleActive();var body=new LinkedHashMap<String,Object>();body.put("register",enabled);body.put("forgot_password",enabled);return write(200,body,false);}
        catch(Exception ignored){var body=new LinkedHashMap<String,Object>();body.put("register",false);body.put("forgot_password",false);return write(200,body,false);}
    }
    @PostMapping("/register") public ResponseEntity<byte[]> register(HttpServletRequest r){return call(r,Set.of("username","email","password"),(s,b,a)->s.register(b,peer(r)));}
    @PostMapping("/register/verify") public ResponseEntity<byte[]> registrationVerify(HttpServletRequest r){return code(r,"verify");}
    @PostMapping("/register/resend") public ResponseEntity<byte[]> registrationResend(HttpServletRequest r){return code(r,"resend");}
    @PostMapping("/register/check") public ResponseEntity<byte[]> registrationCheck(HttpServletRequest r){return call(r,Set.of("field","value"),(s,b,a)->s.registrationCheck(b,peer(r)));}
    @PostMapping("/forgot") public ResponseEntity<byte[]> forgot(HttpServletRequest r){return call(r,null,(s,b,a)->s.forgot(peer(r)));}
    @PostMapping("/reset-code/request") public ResponseEntity<byte[]> resetRequest(HttpServletRequest r){return call(r,Set.of("email"),(s,b,a)->s.resetRequest(b,peer(r)));}
    @PostMapping("/reset-code/verify") public ResponseEntity<byte[]> resetVerify(HttpServletRequest r){return call(r,Set.of("transaction_id","code"),(s,b,a)->s.resetVerify(b,peer(r)));}
    @PostMapping("/reset-code/complete") public ResponseEntity<byte[]> resetComplete(HttpServletRequest r){return call(r,Set.of("reset_proof","new_password","confirm_password"),(s,b,a)->s.resetComplete(b,peer(r)));}
    @GetMapping("/email") public ResponseEntity<byte[]> emailProfile(HttpServletRequest r){return call(r,null,(s,b,a)->s.emailProfile(a));}
    @PostMapping("/email-code/request") public ResponseEntity<byte[]> emailRequest(HttpServletRequest r){return call(r,Set.of("email"),(s,b,a)->s.emailRequest(a,b));}
    @PostMapping("/email-code/verify") public ResponseEntity<byte[]> emailVerify(HttpServletRequest r){return call(r,Set.of("transaction_id","code"),(s,b,a)->s.emailVerify(a,b));}
    @PostMapping("/change-password") public ResponseEntity<byte[]> changePassword(HttpServletRequest r){return call(r,Set.of("current_password","new_password"),(s,b,a)->s.changePassword(a,b,peer(r)));}
    @PostMapping("/revoke-sessions") public ResponseEntity<byte[]> revokeSessions(HttpServletRequest r){return call(r,Set.of(),(s,b,a)->s.revokeSessions(a));}

    @RequestMapping(value={"/register","/register/verify","/register/resend","/register/check","/forgot","/reset-code/request","/reset-code/verify","/reset-code/complete","/email-code/request","/email-code/verify","/change-password","/revoke-sessions"},method={RequestMethod.GET,RequestMethod.HEAD,RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public ResponseEntity<byte[]> unsupported(){return methodNotAllowed("POST");}
    @RequestMapping(value="/capabilities",method={RequestMethod.POST,RequestMethod.HEAD,RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public ResponseEntity<byte[]> unsupportedCapabilities(){return methodNotAllowed("GET");}
    @RequestMapping(value="/email",method={RequestMethod.POST,RequestMethod.HEAD,RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public ResponseEntity<byte[]> unsupportedEmail(){return methodNotAllowed("GET");}

    private ResponseEntity<byte[]> code(HttpServletRequest request,String operation){
        return call(request,operation.equals("verify")?Set.of("credential","code"):Set.of("credential"),(s,b,a)->s.registrationCode(b,operation,peer(request)));
    }

    @FunctionalInterface private interface Operation { LifecycleService146.Result apply(LifecycleService146 service,Map<String,String> body,LifecycleService146.Actor actor); }
    private ResponseEntity<byte[]> call(HttpServletRequest request,Set<String> fields,Operation operation){
        Map<String,String> values=null;
        try{
            boolean protectedPath=request.getRequestURI().endsWith("/email")||request.getRequestURI().endsWith("/email-code/request")||request.getRequestURI().endsWith("/email-code/verify")||request.getRequestURI().endsWith("/change-password")||request.getRequestURI().endsWith("/revoke-sessions");
            transport(request,!protectedPath);
            LifecycleService146 service=services.getIfAvailable();
            if(service==null)throw new IdentityFailure(503,"ACCOUNT_ACTION_UNAVAILABLE","当前账号服务暂未开放此功能。");
            if(!service.lifecycleActive())throw new IdentityFailure(503,"ACCOUNT_ACTION_UNAVAILABLE","当前账号服务暂未开放此功能。");
            LifecycleService146.Actor actor=protectedPath?service.authorizeProtected(jwt(),requestData(request)):null;
            String path=request.getRequestURI();String peer=peer(request);
            if(path.endsWith("/register")||path.endsWith("/register/verify")||path.endsWith("/register/resend")||path.endsWith("/forgot")||path.contains("/reset-code/"))AccountRateLimit146.check(peer,"watch");
            else if(path.endsWith("/register/check"))AccountRateLimit146.check(peer,"registration-check");
            else if((path.endsWith("/email-code/request")||path.endsWith("/email-code/verify"))&&actor!=null)AccountRateLimit146.check(actor.subject(),"watch");
            if(fields!=null){values=body(request);if(!values.keySet().equals(fields))throw invalid();}
            final Map<String,String> parsed=values;
            LifecycleService146.Result result=operation.apply(service,parsed,actor);
            return write(result.status(),result.body(),result.pragma());
        }catch(IdentityFailure failure){
            if("REGISTRATION_UNAVAILABLE".equals(failure.code))return write(failure.status,ordered("code",failure.code,"message",failure.getMessage()),false);
            if("LIFECYCLE_REQUEST_INVALID".equals(failure.code))return invalidForPath(request.getRequestURI());
            throw failure;
        }catch(IllegalArgumentException failure){return write(400,ordered("code","LIFECYCLE_REQUEST_INVALID","message","请求无法处理。"),false);}
        finally{if(values!=null)values.clear();}
    }

    private static void transport(HttpServletRequest r,boolean emptyBody){
        if(!"https".equalsIgnoreCase(r.getScheme())||nonempty(r.getHeader("Origin"))||r.getCookies()!=null&&r.getCookies().length>0
                ||emptyBody&&nonempty(r.getHeader(HttpHeaders.AUTHORIZATION)))throw new IdentityFailure(403,"LIFECYCLE_TRANSPORT_REQUIRED","需要安全的原生账号连接。");
    }
    private static Map<String,String> body(HttpServletRequest request){
        String type=request.getContentType();if(type==null||!type.split(";",2)[0].equals("application/json"))throw invalid();byte[] raw;
        try{raw=request.getInputStream().readNBytes(4097);}catch(IOException e){throw invalid();}
        try{
            if(raw.length>4096)throw invalid();String text;
            try{text=StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(raw)).toString();}catch(CharacterCodingException e){throw invalid();}
            JsonNode node;try{node=JSON.readTree(text);}catch(Exception e){throw invalid();}
            if(node==null||!node.isObject())throw invalid();var result=new LinkedHashMap<String,String>();node.fields().forEachRemaining(entry->{if(!entry.getValue().isTextual())throw invalid();result.put(entry.getKey(),entry.getValue().asText());});return result;
        }finally{java.util.Arrays.fill(raw,(byte)0);}
    }
    private static LifecycleService146.HttpServletRequestData requestData(HttpServletRequest r){
        Object value=r.getAttribute("actor_context");@SuppressWarnings("unchecked") Map<String,Object> actor=value instanceof Map<?,?>?(Map<String,Object>)value:null;
        return new LifecycleService146.HttpServletRequestData(r.getScheme(),r.getHeader("Origin"),r.getHeader(HttpHeaders.AUTHORIZATION),r.getCookies()!=null&&r.getCookies().length>0,r.getHeader("X-MentorFlow-Organization-ID"),r.getHeader("X-MentorFlow-Workspace-ID"),actor);
    }
    private static org.springframework.security.oauth2.jwt.Jwt jwt(){
        Authentication auth=org.springframework.security.core.context.SecurityContextHolder.getContext().getAuthentication();
        if(auth instanceof JwtAuthenticationToken token)return token.getToken();return null;
    }
    private static String peer(HttpServletRequest r){return r.getRemoteAddr()==null?"unknown":r.getRemoteAddr();}
    private static boolean nonempty(String value){return value!=null&&!value.isEmpty();}
    private static IdentityFailure invalid(){return new IdentityFailure(400,"LIFECYCLE_REQUEST_INVALID","请求无法处理。");}
    private static ResponseEntity<byte[]> invalidForPath(String path){
        if(path.endsWith("/register")||path.endsWith("/register/verify")||path.endsWith("/register/resend"))return write(400,ordered("code","REGISTRATION_INPUT_INVALID","message","请检查账号名、邮箱和密码。"),false);
        if(path.endsWith("/register/check"))return write(400,ordered("code","REGISTRATION_INPUT_INVALID","message","请检查账号名或邮箱。"),false);
        if(path.endsWith("/reset-code/request"))return write(400,ordered("code","RESET_EMAIL_INVALID","message","请输入有效的绑定邮箱。"),false);
        if(path.endsWith("/reset-code/verify"))return write(400,ordered("code","RESET_CODE_INVALID_OR_EXPIRED","message","验证码错误、已失效或尝试次数过多，请重新获取。"),false);
        if(path.endsWith("/reset-code/complete"))return write(400,ordered("code","RESET_PROOF_INVALID_OR_EXPIRED","message","重置验证已失效，请重新获取验证码。"),false);
        if(path.endsWith("/email-code/request"))return write(400,ordered("code","EMAIL_INPUT_INVALID","message","请输入有效邮箱。"),false);
        if(path.endsWith("/email-code/verify"))return write(400,ordered("code","EMAIL_CODE_INVALID_OR_EXPIRED","message","验证码错误或已失效，请重新获取。"),false);
        if(path.endsWith("/change-password"))return write(400,ordered("code","PASSWORD_INPUT_INVALID","message","请检查密码长度。"),false);
        if(path.endsWith("/revoke-sessions"))return write(400,ordered("code","SESSION_REVOCATION_INPUT_INVALID"),false);
        return write(400,ordered("code","LIFECYCLE_REQUEST_INVALID","message","请求无法处理。"),false);
    }
    private static LinkedHashMap<String,Object> ordered(Object... pairs){var result=new LinkedHashMap<String,Object>();for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);return result;}
    private static ResponseEntity<byte[]> methodNotAllowed(String allowed){byte[] bytes="{\"detail\":\"Method Not Allowed\"}".getBytes(StandardCharsets.UTF_8);return ResponseEntity.status(405).header(HttpHeaders.ALLOW,allowed).contentType(MediaType.APPLICATION_JSON).contentLength(bytes.length).body(bytes);}
    private static ResponseEntity<byte[]> write(int status,Map<String,Object> body,boolean pragma){try{byte[] bytes=JSON.writeValueAsBytes(body);var result=ResponseEntity.status(status).contentType(MediaType.APPLICATION_JSON).header(HttpHeaders.CACHE_CONTROL,"no-store");if(pragma)result.header(HttpHeaders.PRAGMA,"no-cache");return result.contentLength(bytes.length).body(bytes);}catch(Exception e){return ResponseEntity.status(503).build();}}
}
