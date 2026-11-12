package win.clannad.mentorflow.sync;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.servlet.HandlerMapping;
import win.clannad.mentorflow.identity.IdentityFailure;
import static win.clannad.mentorflow.sync.SyncValidation.ordered;

/** HTTP adapter over the identity package's verified Actor/Tenant seam. */
@RestController
@RequestMapping("/api/enterprise/sync/v1")
public final class SyncController {
    private static final Logger LOG = LoggerFactory.getLogger(SyncController.class);
    private final SyncRepository repository;
    public SyncController(SyncRepository repository) {this.repository=repository;}

    @PutMapping("/entities/{kind}/{entity_id}")
    public void write(HttpServletRequest request,HttpServletResponse response,@PathVariable("kind") String kind,
                      @PathVariable("entity_id") String entityId) throws IOException {
        mutate(request,response,kind,entityId,false);
    }
    @PostMapping("/entities/{kind}/{entity_id}/delete")
    public void delete(HttpServletRequest request,HttpServletResponse response,@PathVariable("kind") String kind,
                       @PathVariable("entity_id") String entityId) throws IOException {
        mutate(request,response,kind,entityId,true,false);
    }
    @PostMapping("/entities/{kind}/{entity_id}/tombstone")
    public void tombstone(HttpServletRequest request,HttpServletResponse response,@PathVariable("kind") String kind,
                       @PathVariable("entity_id") String entityId) throws IOException {
        mutate(request,response,kind,entityId,true,true);
    }
    private void mutate(HttpServletRequest request,HttpServletResponse response,String kind,String entityId,boolean deleting) throws IOException {
        mutate(request,response,kind,entityId,deleting,false);
    }
    @SuppressWarnings("unchecked") private void mutate(HttpServletRequest request,HttpServletResponse response,String kind,String entityId,boolean deleting,boolean createMissingTombstone) throws IOException {
        handle(request,response,()-> {
            var errors=new ArrayList<Map<String,Object>>();SyncValidation.literal(errors,kind,"path");
            var data=SyncValidation.operation(body(request),deleting,createMissingTombstone,errors);
            if(!errors.isEmpty())throw new SyncValidation.Invalid(errors);
            Map<String,Object> projected=SyncPayload.project(kind,entityId,(Map<String,Object>)data.get("payload"),deleting);
            return repository.write(actor(request),tenant(request),kind,entityId,(String)data.get("request_id"),
                ((Number)data.get("expected_revision")).intValue(),projected,deleting,createMissingTombstone);
        });
    }
    @PostMapping("/entities/bulk")
    public void bulk(HttpServletRequest request,HttpServletResponse response) throws IOException {
        handle(request,response,()-> {
            var errors=new ArrayList<Map<String,Object>>();var rows=SyncValidation.bulk(body(request),errors);
            if(!errors.isEmpty())throw new SyncValidation.Invalid(errors);
            return repository.writeMany(actor(request),tenant(request),rows,SyncValidation::row);
        });
    }
    @GetMapping("/snapshot")
    public void snapshot(HttpServletRequest request,HttpServletResponse response) throws IOException {
        handle(request,response,()-> {
            var errors=new ArrayList<Map<String,Object>>();String scope=parameter(request,"required_scope"),version=parameter(request,"scope_version");
            maxLength(errors,"required_scope",scope,512);maxLength(errors,"scope_version",version,64);
            if(!errors.isEmpty())throw new SyncValidation.Invalid(errors);
            return repository.snapshot(actor(request),tenant(request),scope==null?null:Arrays.asList(scope.split(",",-1)),version);
        });
    }
    @GetMapping("/changes")
    public void changes(HttpServletRequest request,HttpServletResponse response) throws IOException {
        handle(request,response,()-> {
            var errors=new ArrayList<Map<String,Object>>();
            Long since=SyncValidation.queryInteger(errors,"since",parameter(request,"since"),0,0,Long.MAX_VALUE);
            String kind=parameter(request,"kind");if(kind==null)kind="learning_run";SyncValidation.literal(errors,kind,"query");
            Long limit=SyncValidation.queryInteger(errors,"limit",parameter(request,"limit"),100,1,500);
            if(!errors.isEmpty())throw new SyncValidation.Invalid(errors);
            return repository.changes(actor(request),tenant(request),since,kind,limit.intValue());
        });
    }
    private static String parameter(HttpServletRequest request,String name) {String[] values=request.getParameterValues(name);return values==null?null:values[values.length-1];}
    private static void maxLength(List<Map<String,Object>> errors,String name,String value,int maximum) {
        if(value!=null&&value.codePointCount(0,value.length())>maximum)errors.add(SyncValidation.issue("string_too_long",List.of("query",name),"String should have at most "+maximum+" characters",value,"max_length",maximum));
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> actor(HttpServletRequest request) {
        Object actor=request.getAttribute("actor_context"),tenant=request.getAttribute("tenant_context");
        if(!(actor instanceof Map)||!(tenant instanceof Map))throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED","Authenticated workspace required",401);
        return (Map<String,Object>)actor;
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> tenant(HttpServletRequest request) {return (Map<String,Object>)request.getAttribute("tenant_context");}
    private static Object body(HttpServletRequest request) throws IOException {
        byte[] bytes=request.getInputStream().readAllBytes();
        if(bytes.length==0)throw new SyncValidation.Invalid(List.of(SyncValidation.issue("missing",List.of("body"),"Field required",null)));
        String text;
        try {text=StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString();}
        catch(java.nio.charset.CharacterCodingException error){throw new BodyParsing();}
        String type=request.getContentType();
        if(type!=null&&!type.split(";",2)[0].strip().equals("application/json")&&!type.split(";",2)[0].strip().endsWith("+json"))return text;
        if(text.startsWith("\ufeff"))text=text.substring(1);
        Object value;
        try {value=SyncJson.read(text);}
        catch(IllegalArgumentException failure) {
            Throwable cause=failure.getCause();int position=0;String message="Expecting value";
            if(cause instanceof com.fasterxml.jackson.core.JsonProcessingException json) {
                position=(int)Math.max(0,json.getLocation().getCharOffset());
                String detail=json.getOriginalMessage();
                if(detail.contains("Trailing token"))message="Extra data";
                else if(detail.contains("colon"))message="Expecting ':' delimiter";
                else if(detail.contains("Unrecognized character escape")){message="Invalid \\escape";position=Math.max(0,position-1);}
                else if(detail.contains("field name")||text.strip().equals("{")) {message="Expecting property name enclosed in double quotes";if(text.strip().equals("{"))position=text.length();}
                else if(detail.contains("close marker")||detail.contains("Unexpected end-of-input")){
                    String stripped=text.stripTrailing();
                    message=stripped.endsWith(":")||stripped.endsWith(",")||stripped.endsWith("[")?"Expecting value":"Expecting ',' delimiter";
                    position=text.length();
                }
                else if(detail.contains("comma"))message="Expecting ',' delimiter";
            }
            position=text.codePointCount(0,Math.min(position,text.length()));
            throw new SyncValidation.Invalid(List.of(SyncValidation.issue("json_invalid",List.of("body",position),"JSON decode error",ordered(),"error",message)));
        }
        if(value==null)throw new SyncValidation.Invalid(List.of(SyncValidation.issue("missing",List.of("body"),"Field required",null)));
        return value;
    }
    private static class BodyParsing extends RuntimeException {}
    @FunctionalInterface private interface Work {Object run() throws IOException;}
    private void handle(HttpServletRequest request,HttpServletResponse response,Work work) throws IOException {
        try {
            Map<String,Object> authenticatedActor = actor(request);
            repository.recordDailyActivity(authenticatedActor);
            writeJson(response,200,work.run(),true);
        }
        catch(SyncValidation.Invalid failure){writeJson(response,422,ordered("detail",failure.errors),false);}
        catch(SyncFailure failure){
            if(failure.status>=500) {
                String route = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE) instanceof String pattern
                        ? pattern : request.getServletPath();
                LOG.warn("service_error code={} request_id={} route={} exception={} sqlState={}", failure.code,
                        request.getAttribute("request_id"), route, exceptionClass(failure), sqlState(failure));
            }
            if(failure.code.equals("SYNC_NOT_READY"))throw new IdentityFailure("SYNC_NOT_READY","Structured sync unavailable",503,failure);
            writeJson(response,failure.status,ordered("code",failure.code,"sync_status","QUERY_FAILED"),true);
        }
        catch(IdentityFailure failure){throw failure;}
        catch(BodyParsing failure){writeJson(response,400,ordered("detail","There was an error parsing the body"),false);}
        catch(RuntimeException failure) {
            // The reference's unhandled exceptions are plain text, not invented
            // structured state. In particular they must never imply empty data.
            String route = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE) instanceof String pattern
                    ? pattern : request.getServletPath();
            LOG.error("service_error code=SYNC_INTERNAL_ERROR request_id={} route={} exception={} sqlState={}",
                    request.getAttribute("request_id"), route, exceptionClass(failure), sqlState(failure), failure);
            response.reset();response.setStatus(500);
            request.setAttribute("python_principal_failure",Boolean.TRUE);
            response.setContentType("text/plain");
            byte[] bytes="Internal Server Error".getBytes(StandardCharsets.UTF_8);
            response.setContentLength(bytes.length);response.getOutputStream().write(bytes);
        }
    }
    private static String sqlState(Throwable failure) {
        Throwable current=failure;
        while(current!=null) {
            if(current instanceof java.sql.SQLException sql && sql.getSQLState()!=null)return sql.getSQLState();
            current=current.getCause();
        }
        return "";
    }
    private static String exceptionClass(Throwable failure) {
        Throwable current=failure, deepest=failure;
        while(current!=null) { deepest=current; current=current.getCause(); }
        return deepest == null ? "" : deepest.getClass().getName();
    }
    static void writeJson(HttpServletResponse response,int status,Object value,boolean noStore) throws IOException {
        byte[] bytes=SyncJson.dump(value,false).getBytes(StandardCharsets.UTF_8);
        response.setStatus(status);response.setContentType("application/json");
        if(noStore)response.setHeader("Cache-Control","no-store");
        response.setContentLength(bytes.length);response.getOutputStream().write(bytes);
    }
}
