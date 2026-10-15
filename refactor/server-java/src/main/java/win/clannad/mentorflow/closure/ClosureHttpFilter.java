package win.clannad.mentorflow.closure;

import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySettings;
import win.clannad.mentorflow.identity.NativeAccessAuthenticator;

/** Closure recovery credentials deliberately bypass ordinary tenant/bearer auth. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE+10)
public final class ClosureHttpFilter extends OncePerRequestFilter {
    public static final String PREFIX="/api/enterprise/account/closure";
    private static final JsonMapper JSON=JsonMapper.builder()
        .enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION)
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS).build();
    private final ObjectProvider<ClosureService> services;
    private final ObjectProvider<ClosureStore> stores;
    private final ObjectProvider<NativeAccessAuthenticator> sessions;
    private final IdentitySettings settings;
    public ClosureHttpFilter(ObjectProvider<ClosureService> services,ObjectProvider<ClosureStore> stores,
            ObjectProvider<NativeAccessAuthenticator> sessions,IdentitySettings settings) {
        this.services=services;this.stores=stores;this.sessions=sessions;this.settings=settings;
    }
    @Override protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().matches(java.util.regex.Pattern.quote(PREFIX)+"/[^/]{1,128}(?:/commit)?");
    }
    @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain)
            throws IOException,ServletException {
        String requestId=request.getHeader("X-Request-ID");
        requestId=requestId==null?"":IdentitySettings.stripIdentityWhitespace(requestId);
        if(!requestId.matches("[A-Za-z0-9._:-]{1,128}")) {
            byte[] bytes=new byte[16];new SecureRandom().nextBytes(bytes);requestId=HexFormat.of().formatHex(bytes);
        }
        request.setAttribute("request_id",requestId);
        response.setHeader("X-Request-ID",requestId);
        response.setHeader("X-MentorFlow-Runtime-Profile","ACCOUNT_CORE_MVP");
        String tail=request.getRequestURI().substring(PREFIX.length()+1);
        boolean commit=tail.endsWith("/commit");
        String operation=commit?tail.substring(0,tail.length()-7):tail;
        String action="POST".equals(request.getMethod())&&"prepare".equals(tail)?"prepare":commit?"commit":"status";
        String allowed=commit||"prepare".equals(tail)?"POST":"GET";
        // GET /prepare is the operation-status route, as in FastAPI.
        if("GET".equals(request.getMethod())&&!commit){action="status";allowed="GET";}
        if(!allowed.equals(request.getMethod())) {
            response.setHeader("Allow",commit||"prepare".equals(tail)?"POST":"GET");
            write(response,405,Map.of("detail","Method Not Allowed"),false);return;
        }
        Map<String,Object> body=null;
        try {
            transport(request);
            ClosureService service=services.getIfAvailable();
            ClosureStore store=stores.getIfAvailable();
            if(service==null||store==null)throw new ClosureFailure("CLOSURE_UNAVAILABLE");
            ClosureService.Result result;
            if("prepare".equals(action)) {
                if(has(request,"X-MentorFlow-Closure-Token"))throw new ClosureFailure("CLOSURE_AUTH_BOUNDARY_CONFLICT",403);
                String auth=request.getHeader("Authorization");
                if(auth==null||!auth.startsWith("Bearer mfn1_a_"))throw new ClosureFailure("CLOSURE_NATIVE_AUTH_REQUIRED",401);
                auth=IdentitySettings.stripIdentityWhitespace(auth);
                if(auth.indexOf(' ',7)>=0)throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","A verified bearer account is required");
                NativeAccessAuthenticator nativeAccess=sessions.getIfAvailable();
                if(nativeAccess==null)throw new IdentityFailure("ENTERPRISE_SESSION_REAUTH_REQUIRED","登录已失效，请重新登录。");
                Jwt principal=nativeAccess.authenticateAccess(auth.substring(7));
                store.ensureOpen(principal.getClaimAsString("iss"),principal.getSubject());
                String kind=principal.getClaimAsString("actor_kind");
                if(kind==null)kind="HUMAN";
                if(!"HUMAN".equals(kind))throw new IdentityFailure(403,"ACCOUNT_BOOTSTRAP_HUMAN_REQUIRED","A human account is required");
                body=readBody(request,"current_password",String.class);
                String password=(String)body.get("current_password");
                if(password.isEmpty()||password.codePointCount(0,password.length())>1024)throw new ClosureFailure("CLOSURE_INPUT_INVALID",400);
                result=service.prepare(principal.getClaimAsString("iss"),principal.getSubject(),kind,password,
                    request.getRemoteAddr()==null?"unknown":request.getRemoteAddr());
            } else {
                if(has(request,"Authorization"))throw new ClosureFailure("CLOSURE_AUTH_BOUNDARY_CONFLICT",403);
                String token=request.getHeader("X-MentorFlow-Closure-Token");
                if("status".equals(action))result=service.status(operation,token);
                else {
                    body=readBody(request,"confirmed",Boolean.class);
                    if(!Boolean.TRUE.equals(body.get("confirmed")))throw new ClosureFailure("CLOSURE_CONFIRMATION_REQUIRED",400);
                    result=service.commit(operation,token);
                }
            }
            write(response,result.status(),result.payload(),true);
        } catch(ClosureFailure failure){error(response,failure.code,failure.status,action,operation);}
        catch(IdentityFailure failure){error(response,failure.code,failure.status,action,operation);}
        catch(Exception failure){error(response,"CLOSURE_UNAVAILABLE",503,action,operation);}
        finally{if(body!=null)body.clear();}
    }
    private static boolean has(HttpServletRequest request,String header){return request.getHeader(header)!=null;}
    private static void transport(HttpServletRequest request) {
        if(!"https".equals(request.getScheme())||has(request,"Origin")||has(request,"Cookie")
                ||has(request,"X-MentorFlow-Organization-ID")||has(request,"X-MentorFlow-Workspace-ID"))
            throw new ClosureFailure("CLOSURE_TRANSPORT_REQUIRED",403);
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> readBody(HttpServletRequest request,String key,Class<?> type) {
        byte[] bytes=null;
        try {
            String contentType=request.getContentType();
            if(contentType==null||!contentType.split(";",2)[0].equals("application/json"))throw new IllegalArgumentException();
            bytes=request.getInputStream().readNBytes(4097);
            if(bytes.length>4096)throw new IllegalArgumentException();
            Object value=JSON.readValue(bytes,Object.class);
            if(!(value instanceof Map<?,?> map)||!map.keySet().equals(Set.of(key))||map.get(key)==null||map.get(key).getClass()!=type)
                throw new IllegalArgumentException();
            return (Map<String,Object>)map;
        }catch(Exception failure){throw new ClosureFailure("CLOSURE_INPUT_INVALID",400);}
        finally{if(bytes!=null)java.util.Arrays.fill(bytes,(byte)0);}
    }
    private static void error(HttpServletResponse response,String code,int status,String action,String operation)throws IOException {
        var payload=new LinkedHashMap<String,Object>();payload.put("code",code);payload.put("message","注销请求未完成，不能视为注销成功。");
        if(!"prepare".equals(action)) {
            payload.put("state",null);payload.put("operation_id",operation!=null&&operation.matches("[0-9a-f]{32}")?operation:null);
            payload.put("account_id",null);
        }
        write(response,status,payload,true);
    }
    private static void write(HttpServletResponse response,int status,Map<String,Object> payload,boolean noStore)throws IOException {
        byte[] bytes=JSON.writeValueAsBytes(payload);response.setStatus(status);response.setContentType("application/json");
        if(noStore)response.setHeader("Cache-Control","no-store");
        response.setContentLength(bytes.length);response.getOutputStream().write(bytes);
    }
}
