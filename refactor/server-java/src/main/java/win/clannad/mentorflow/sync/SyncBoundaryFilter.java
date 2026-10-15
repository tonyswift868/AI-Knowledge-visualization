package win.clannad.mentorflow.sync;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

/** Preserve the sync-specific identity status without changing identity code. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE+20)
public final class SyncBoundaryFilter extends OncePerRequestFilter {
    @Override protected boolean shouldNotFilter(HttpServletRequest request) {return !request.getRequestURI().startsWith("/api/enterprise/sync/");}
    @SuppressWarnings("unchecked") @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
        var cached=new DeferredResponse(response);
        try {chain.doFilter(request,cached);}
        catch(ServletException|RuntimeException failure) {
            Throwable cause=failure;boolean databaseFailure=false;
            while(cause!=null){if(cause instanceof java.sql.SQLException){databaseFailure=true;break;}cause=cause.getCause();}
            if(!databaseFailure)throw failure;
            // Membership lookup can fail before the controller. Python maps
            // that database outage to QUERY_FAILED, never an empty snapshot.
            cached.resetBuffer();
            SyncController.writeJson(cached,503,SyncValidation.ordered("code","SYNC_QUERY_FAILED","sync_status","QUERY_FAILED"),true);
        }
        byte[] bytes=cached.getContentAsByteArray();
        if(cached.getStatus()>=400&&cached.getContentType()!=null&&cached.getContentType().startsWith("application/json")) {
            Object value=SyncJson.read(new String(bytes,StandardCharsets.UTF_8));
            if(value instanceof Map<?,?> raw&&raw.containsKey("error")) {
                Map<String,Object> body=(Map<String,Object>)raw;
                body.put("sync_status",cached.getStatus()==401||cached.getStatus()==403?"NOT_AUTHORIZED":"QUERY_FAILED");
                bytes=SyncJson.dump(body,false).getBytes(StandardCharsets.UTF_8);
                cached.resetBuffer();cached.setContentLength(bytes.length);cached.getOutputStream().write(bytes);
            }
        }
        if(Boolean.TRUE.equals(request.getAttribute("python_principal_failure")))cached.copyWithoutFlush();
        else cached.copyBodyToResponse();
    }
    private static final class DeferredResponse extends ContentCachingResponseWrapper {
        DeferredResponse(HttpServletResponse response){super(response);}
        // Let the existing identity Tomcat valve restore Python's literal
        // text/plain header before the short unhandled-error body commits.
        void copyWithoutFlush() throws IOException {super.copyBodyToResponse(false);}
    }
}
