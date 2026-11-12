package win.clannad.mentorflow.account;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentityDirectory;
import win.clannad.mentorflow.identity.ErrorResponses;
import org.springframework.security.oauth2.jwt.Jwt;

/** Literal HTTP boundary matching Python's /api/enterprise/auth/native routes. */
@RestController
@RequestMapping(NativeSessionService.PREFIX)
public final class NativeAuthController {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final ObjectProvider<NativeSessionService> sessions;
    private final ObjectProvider<IdentityDirectory> directory;

    public NativeAuthController(ObjectProvider<NativeSessionService> sessions,ObjectProvider<IdentityDirectory> directory) { this.sessions=sessions;this.directory=directory; }

    @PostMapping("/login")
    public ResponseEntity<byte[]> login(HttpServletRequest request) {
        return operate(request,"login");
    }
    @PostMapping("/refresh")
    public ResponseEntity<byte[]> refresh(HttpServletRequest request) { return operate(request,"refresh"); }
    @PostMapping("/logout")
    public ResponseEntity<byte[]> logout(HttpServletRequest request) { return operate(request,"logout"); }
    @GetMapping("/session")
    public ResponseEntity<byte[]> session(HttpServletRequest request) {
        if(!request.getMethod().equals("GET"))return methodNotAllowed("GET");
        try {
            transport(request,true);
            String authorization=request.getHeader(HttpHeaders.AUTHORIZATION);
            if(authorization==null||!authorization.startsWith("Bearer ")||authorization.indexOf(' ',7)>=0)
                throw new IdentityFailure("ENTERPRISE_SESSION_REAUTH_REQUIRED","登录已失效，请重新登录。",401);
            NativeSessionService service=requireService();
            Jwt principal=service.session(authorization);
            IdentityDirectory openCheck=directory.getIfAvailable();
            if(openCheck!=null)openCheck.ensureOpen(principal.getClaimAsString("iss"),principal.getSubject());
            return success(Map.of("state","active"),false);
        } catch(IdentityFailure failure) {
            if(failure.status>=500)ErrorResponses.log(request,failure.code,failure,false);
            return error(failure.code,failure.getMessage(),failure.status);
        }
        catch(Exception failure) {
            ErrorResponses.log(request,"NATIVE_AUTH_UNAVAILABLE",failure,false);
            return error("NATIVE_AUTH_UNAVAILABLE","账号服务暂不可用。",503);
        }
    }

    @RequestMapping(value={"/login","/refresh","/logout"},method={RequestMethod.GET,RequestMethod.HEAD,
        RequestMethod.PUT,RequestMethod.PATCH,RequestMethod.DELETE,RequestMethod.OPTIONS})
    public ResponseEntity<byte[]> unsupportedPostMethod(){return methodNotAllowed("POST");}
    @RequestMapping(value="/session",method={RequestMethod.POST,RequestMethod.PUT,RequestMethod.PATCH,
        RequestMethod.DELETE,RequestMethod.OPTIONS})
    public ResponseEntity<byte[]> unsupportedGetMethod(){return methodNotAllowed("GET");}

    private static ResponseEntity<byte[]> methodNotAllowed(String allowed) {
        byte[] body="{\"detail\":\"Method Not Allowed\"}".getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.status(405).header("Allow",allowed).contentType(MediaType.APPLICATION_JSON)
            .contentLength(body.length).body(body);
    }

    private ResponseEntity<byte[]> operate(HttpServletRequest request,String operation) {
        Map<String,Object> body=null;
        try {
            transport(request,false);
            NativeSessionService service=requireService();
            body=readBody(request);
            String field="login".equals(operation)?"username":"refresh_token";
            if(!body.keySet().equals("login".equals(operation)?java.util.Set.of("username","password"):java.util.Set.of(field))) throw new IllegalArgumentException();
            for(Object value:body.values())if(!(value instanceof String text)||text.isEmpty())throw new IllegalArgumentException();
            if("login".equals(operation)) {
                String rawUsername=(String)body.get("username"),password=(String)body.get("password");
                String username=win.clannad.mentorflow.identity.IdentitySettings.stripIdentityWhitespace(rawUsername);
                if(username.isEmpty()||rawUsername.codePointCount(0,rawUsername.length())>320
                        ||password.codePointCount(0,password.length())>1024)throw new IllegalArgumentException();
                return success(service.login(username,password,request.getRemoteAddr()==null?"unknown":request.getRemoteAddr()),true);
            }
            String token=(String)body.get("refresh_token");
            if("refresh".equals(operation))return success(service.refresh(token),true);
            service.logout(token);
            return success(Map.of("logged_out",true),true);
        } catch(IllegalArgumentException failure) { return error("NATIVE_REQUEST_INVALID","登录请求无法处理。",400); }
        catch(com.fasterxml.jackson.core.JsonProcessingException failure) { return error("NATIVE_REQUEST_INVALID","登录请求无法处理。",400); }
        catch(IdentityFailure failure) {
            if(failure.status>=500)ErrorResponses.log(request,failure.code,failure,false);
            return error(failure.code,failure.getMessage(),failure.status);
        }
        catch(Exception failure) {
            ErrorResponses.log(request,"NATIVE_AUTH_UNAVAILABLE",failure,false);
            return error("NATIVE_AUTH_UNAVAILABLE","账号服务暂不可用。",503);
        }
        finally { if(body!=null)body.clear(); }
    }

    private NativeSessionService requireService() {
        NativeSessionService value=sessions.getIfAvailable();
        if(value==null)throw new IdentityFailure("NATIVE_AUTH_NOT_READY","账号服务尚未准备好，请稍后重试。",503);
        return value;
    }
    private static void transport(HttpServletRequest request,boolean session) {
        if(!"https".equalsIgnoreCase(request.getScheme()) || nonEmptyHeader(request,"Origin")
                || request.getCookies()!=null&&request.getCookies().length>0
                || !session&&nonEmptyHeader(request,HttpHeaders.AUTHORIZATION))
            throw new IdentityFailure("NATIVE_TRANSPORT_REQUIRED","需要安全的原生账号连接。",403);
    }
    private static boolean nonEmptyHeader(HttpServletRequest request,String name) {
        String value=request.getHeader(name);return value!=null&&!value.isEmpty();
    }
    private static Map<String,Object> readBody(HttpServletRequest request)throws IOException {
        String type=request.getContentType();
        if(type==null||!type.split(";",2)[0].equals("application/json"))throw new IllegalArgumentException();
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();byte[] buffer=new byte[1024];int count;
        while((count=request.getInputStream().read(buffer))!=-1){if(bytes.size()+count>4096)throw new IllegalArgumentException();bytes.write(buffer,0,count);}
        Object parsed=JSON.readValue(bytes.toByteArray(),Object.class);
        if(!(parsed instanceof Map<?,?> map))throw new IllegalArgumentException();
        Map<String,Object> result=new LinkedHashMap<>();
        for(Map.Entry<?,?> entry:map.entrySet()){if(!(entry.getKey() instanceof String key))throw new IllegalArgumentException();result.put(key,entry.getValue());}
        return result;
    }
    private static ResponseEntity<byte[]> success(Map<String,Object> value,boolean pragma) {
        HttpHeaders headers=new HttpHeaders();headers.setContentType(MediaType.APPLICATION_JSON);headers.setCacheControl("no-store");
        if(pragma)headers.set("Pragma","no-cache");
        try {byte[] body=JSON.writeValueAsBytes(value);headers.setContentLength(body.length);return ResponseEntity.status(200).headers(headers).body(body);}
        catch(Exception failure){throw new IllegalStateException(failure);}
    }
    private static ResponseEntity<byte[]> error(String code,String message,int status) {
        Map<String,Object> body=new LinkedHashMap<>();body.put("code",code);body.put("message",message);
        HttpHeaders headers=new HttpHeaders();headers.setContentType(MediaType.APPLICATION_JSON);headers.setCacheControl("no-store");
        try {byte[] bytes=JSON.writeValueAsBytes(body);headers.setContentLength(bytes.length);return ResponseEntity.status(status).headers(headers).body(bytes);}
        catch(Exception failure){throw new IllegalStateException(failure);}
    }
}
