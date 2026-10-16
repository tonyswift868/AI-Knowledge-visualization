package win.clannad.mentorflow.identity;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;

/** Minimal access diagnostics. It deliberately never logs a raw URI or request data. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 5)
public final class RequestAccessLog149Filter extends OncePerRequestFilter {
    private static final Logger LOG=LoggerFactory.getLogger(RequestAccessLog149Filter.class);
    private static final Set<String> STATIC_ROUTES=staticRoutes();
    private static final Pattern SYNC_ENTITY=Pattern.compile(
        "/api/enterprise/sync/v1/entities/[^/]{1,128}/[^/]{1,128}(?:/delete)?");
    private static final Pattern CLOSURE=Pattern.compile(
        "/api/enterprise/account/closure/(?:prepare|[0-9a-f]{32}(?:/commit)?)");

    private static Set<String> staticRoutes() {
        var routes=new HashSet<>(AccountRoutes146.PUBLIC_PATHS);
        routes.addAll(Set.of("/health/live", "/health/ready", "/health/startup",
        "/api/enterprise/account/workspaces",
        "/api/enterprise/account/workspaces/initialize",
        "/api/enterprise/auth/native/login", "/api/enterprise/auth/native/refresh",
        "/api/enterprise/auth/native/logout", "/api/enterprise/auth/native/session",
        "/api/enterprise/auth/me", "/api/enterprise/auth/admin/overview",
        "/api/enterprise/catalog/source-whitelist", "/api/enterprise/catalog/model-prices",
        "/api/enterprise/auth/lifecycle/capabilities", "/api/enterprise/auth/lifecycle/register",
        "/api/enterprise/auth/lifecycle/register/check", "/api/enterprise/auth/lifecycle/register/verify",
        "/api/enterprise/auth/lifecycle/register/resend", "/api/enterprise/auth/lifecycle/forgot",
        "/api/enterprise/auth/lifecycle/reset-code/request", "/api/enterprise/auth/lifecycle/reset-code/verify",
        "/api/enterprise/auth/lifecycle/reset-code/complete", "/api/enterprise/auth/lifecycle/email",
        "/api/enterprise/auth/lifecycle/email-code/request", "/api/enterprise/auth/lifecycle/email-code/verify",
        "/api/enterprise/auth/lifecycle/change-password", "/api/enterprise/auth/lifecycle/revoke-sessions",
        "/api/enterprise/auth/reset/watch", "/api/enterprise/auth/reset/status",
        "/api/enterprise/sync/v1/snapshot", "/api/enterprise/sync/v1/changes",
        "/api/enterprise/sync/v1/entities/bulk", "/api/enterprise/account/closure/prepare"));
        return Set.copyOf(routes);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain)
        throws ServletException,IOException {
        long started=System.nanoTime();
        HttpServletResponse wrapped=new jakarta.servlet.http.HttpServletResponseWrapper(response);
        int escapedStatus=0;
        try {
            chain.doFilter(request,wrapped);
        } catch(ServletException|IOException|RuntimeException failure) {
            escapedStatus=HttpServletResponse.SC_INTERNAL_SERVER_ERROR;
            throw failure;
        } finally {
            Object mapped=request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE);
            String route=mapped instanceof String pattern&&!pattern.equals("/**")
                ?pattern:safeRoute(request.getRequestURI());
            long elapsed=Math.max(0,(System.nanoTime()-started)/1_000_000);
            LOG.info("http method={} route={} status={} elapsed_ms={}",safeMethod(request.getMethod()),route,
                escapedStatus==0?wrapped.getStatus():escapedStatus,elapsed);
        }
    }

    private static String safeRoute(String uri) {
        if(STATIC_ROUTES.contains(uri))return uri;
        if(SYNC_ENTITY.matcher(uri).matches())return uri.endsWith("/delete")
            ?"/api/enterprise/sync/v1/entities/{kind}/{entity_id}/delete"
            :"/api/enterprise/sync/v1/entities/{kind}/{entity_id}";
        if(uri.endsWith("/prepare")&&uri.equals("/api/enterprise/account/closure/prepare"))return uri;
        if(CLOSURE.matcher(uri).matches())return uri.endsWith("/commit")
            ?"/api/enterprise/account/closure/{operation_id}/commit"
            :"/api/enterprise/account/closure/{operation_id}";
        return "UNMATCHED";
    }

    private static String safeMethod(String method) {
        return switch(method) {
            case "GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS" -> method;
            default -> "OTHER";
        };
    }
}
