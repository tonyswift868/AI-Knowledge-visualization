package win.clannad.mentorflow.identity;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.filter.OncePerRequestFilter;

@Configuration
public class IdentitySecurity {
    @Bean
    org.springframework.boot.web.server.WebServerFactoryCustomizer<org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory> pythonErrorHeader() {
        return factory -> factory.addContextValves(new org.apache.catalina.valves.ValveBase(true) {
            @Override public void invoke(org.apache.catalina.connector.Request request, org.apache.catalina.connector.Response response) throws IOException, ServletException {
                getNext().invoke(request,response);
                if (Boolean.TRUE.equals(request.getAttribute("python_principal_failure")) && !response.isCommitted()) {
                    // Servlet setContentType normalizes away the space after ';'.
                    // Preserve Python's literal transport value for this legacy 500.
                    response.getCoyoteResponse().setContentTypeNoCharset("text/plain; charset=utf-8");
                }
            }
        });
    }
    @Bean SecurityFilterChain api(HttpSecurity http, IdentitySettings settings, CompatibleJwtDecoder decoder, IdentityDirectory directory) throws Exception {
        http.csrf(c->c.disable()).sessionManagement(c->c.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
        http.headers(c->c.disable()); // Do not replace Python's existing cache/response headers.
        http.authorizeHttpRequests(c->c
            .requestMatchers(NativeAccessAuthenticator.LOGIN_PATH,NativeAccessAuthenticator.REFRESH_PATH,
                NativeAccessAuthenticator.LOGOUT_PATH,NativeAccessAuthenticator.SESSION_PATH).permitAll()
            .requestMatchers(AccountRoutes146.PUBLIC_PATHS.toArray(String[]::new)).permitAll()
            .requestMatchers("/api/**").authenticated().anyRequest().permitAll());
        var entryPoint = (org.springframework.security.web.AuthenticationEntryPoint)(request,response,error)-> {
            Throwable current=error; IdentityFailure matched=null;
            while (current!=null) { if (current instanceof IdentityFailure f) {matched=f;break;} current=current.getCause(); }
            if (matched==null) matched=new IdentityFailure(401,"ENTERPRISE_AUTH_REQUIRED","Enterprise authentication is required");
            ErrorResponses.write(request,response,matched);
        };
        http.exceptionHandling(c->c.authenticationEntryPoint(entryPoint));
        http.oauth2ResourceServer(c->c.authenticationEntryPoint(entryPoint)
            .bearerTokenResolver(request-> {
                if (!request.getRequestURI().startsWith("/api")) return null;
                if (NativeAccessAuthenticator.isSessionEndpoint(request.getRequestURI())||AccountRoutes146.isPublic(request.getRequestURI())) return null;
                String auth=request.getHeader("Authorization"); auth=auth==null?"":auth.strip();
                String cookie="";
                if (request.getCookies()!=null) for (Cookie item:request.getCookies()) if (item.getName().equals(settings.cookieName)) cookie=item.getValue().strip();
                if(AccountRoutes146.isWorkspace(request.getRequestURI()) || AccountRoutes146.isAccountProfile(request.getRequestURI())) {
                    if(!cookie.isEmpty())throw new IdentityFailure(401,"AUTH_BOUNDARY_CONFLICT","Account bootstrap requires bearer authentication");
                    if(!auth.startsWith("Bearer ")||auth.chars().filter(v->v==' ').count()!=1)
                        throw new IdentityFailure(401,"ENTERPRISE_AUTH_REQUIRED","A verified bearer account is required");
                }
                if (!auth.isEmpty() && !cookie.isEmpty()) throw new IdentityFailure(401,"AUTH_BOUNDARY_CONFLICT","Cookie and bearer identity modes cannot be mixed");
                if (auth.isEmpty()) {
                    if (!cookie.isEmpty()) throw new IdentityFailure(401,"ENTERPRISE_SESSION_INVALID","Enterprise session is invalid");
                    return null;
                }
                if (!auth.startsWith("Bearer ") || auth.chars().filter(v->v==' ').count()!=1)
                    throw new IdentityFailure(401,"OIDC_TOKEN_INVALID","Bearer authorization is malformed");
                String token=auth.substring(7);
                if(token.startsWith("mfn1_a_")&&!"https".equalsIgnoreCase(request.getScheme()))
                    throw new IdentityFailure("ENTERPRISE_SESSION_REAUTH_REQUIRED","登录已失效，请重新登录。");
                return token;
            }).jwt(cj->cj.decoder(decoder).jwtAuthenticationConverter(jwt ->
                new JwtAuthenticationToken(jwt,List.of(),jwt.getSubject()))));
        http.addFilterBefore(new RequestBoundaryFilter(settings),BearerTokenAuthenticationFilter.class);
        http.addFilterAfter(new ContextFilter(directory),BearerTokenAuthenticationFilter.class);
        return http.build();
    }
    public static String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception ex) {throw new IllegalStateException(ex);}
    }
    private static final class ContextFilter extends OncePerRequestFilter {
        private final IdentityDirectory directory;
        ContextFilter(IdentityDirectory directory) { this.directory=directory; }
        @Override protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws IOException,ServletException {
            if (!request.getRequestURI().startsWith("/api")) {chain.doFilter(request,response);return;}
            if (NativeAccessAuthenticator.isSessionEndpoint(request.getRequestURI())||AccountRoutes146.isPublic(request.getRequestURI())) {chain.doFilter(request,response);return;}
            if (AccountRoutes146.isAccountProfile(request.getRequestURI())) response.setHeader("Cache-Control", "private, no-cache");
            var auth=SecurityContextHolder.getContext().getAuthentication();
            if (!(auth instanceof JwtAuthenticationToken authenticated)) {chain.doFilter(request,response);return;}
            try {
                Jwt jwt=authenticated.getToken(); String issuer=jwt.getClaimAsString("iss"), subject=jwt.getSubject();
                directory.ensureOpen(issuer,subject);
                if(AccountRoutes146.isWorkspace(request.getRequestURI()) || AccountRoutes146.isAccountProfile(request.getRequestURI())) {
                    request.setAttribute("account_principal",jwt);
                    chain.doFilter(request,response);return;
                }
                String org=request.getHeader("X-MentorFlow-Organization-ID"); org=org==null?"":org.strip();
                String workspace=request.getHeader("X-MentorFlow-Workspace-ID"); workspace=workspace==null||workspace.isBlank()?null:workspace.strip();
                if (org.isEmpty()) throw new IdentityFailure(403,"ENTERPRISE_ORGANIZATION_REQUIRED","Bearer identity requires a selected organization");
                if (!jwt.getExpiresAt().isAfter(Instant.now())) throw new IdentityFailure(401,"ENTERPRISE_SESSION_INVALID","Enterprise session is revoked or expired");
                if (jwt.getNotBefore()!=null && jwt.getNotBefore().isAfter(Instant.now())) throw new IdentityFailure(401,"OIDC_TOKEN_NOT_ACTIVE","OIDC token is not active");
                String client=jwt.getClaimAsString("azp"); if (client==null) client=jwt.getClaimAsString("client_id");
                boolean service=client!=null && ("service-account-"+client).equals(jwt.getClaimAsString("preferred_username"));
                List<String> roles=List.of(), permissions=List.of(); String membership=null, actorSubject=subject;
                if (service) {
                    Map<String,Object> grant=directory.service(issuer,subject,client);
                    if (grant==null || !"ACTIVE".equals(grant.get("status")) || !issuer.equals(grant.get("issuer")) || !subject.equals(grant.get("subject")) || !client.equals(grant.get("client_id")) || !org.equals(grant.get("organization_id")) || !java.util.Objects.equals(workspace,grant.get("workspace_id")))
                        throw new IdentityFailure(403,"SERVICE_IDENTITY_NOT_ACTIVE","Service identity is not active in the selected tenant scope");
                    actorSubject=(String)grant.get("service_id");
                    @SuppressWarnings("unchecked") List<String> granted=(List<String>)grant.get("permissions"); permissions=granted;
                } else {
                    IdentityDirectory.Membership grant=directory.resolve(issuer,subject,org,workspace);
                    if (grant==null) throw new IdentityFailure(403,"MEMBERSHIP_NOT_ACTIVE","Membership is not active in the selected tenant scope");
                    membership=grant.id(); roles=grant.roles();
                }
                var actor=new LinkedHashMap<String,Object>();
                actor.put("schema_version","enterprise-context.v1"); actor.put("subject_id",actorSubject); actor.put("actor_kind",service?"SERVICE":"HUMAN");
                actor.put("issuer",issuer); actor.put("token_id",jwt.getId()); actor.put("session_id","bearer-"+sha256(issuer+"\u001f"+jwt.getId()));
                actor.put("request_id",request.getAttribute("request_id"));
                Object authTime=jwt.getClaims().get("auth_time");
                Instant instant=authTime instanceof Instant i ? i : authTime instanceof Number n ? Instant.ofEpochSecond(n.longValue()) : jwt.getIssuedAt();
                actor.put("authentication_time",instant.toString());
                String strength=jwt.getClaimAsString("acr"); actor.put("authentication_strength",strength==null?"oidc":strength);
                actor.put("organization_id",org);actor.put("workspace_id",workspace);actor.put("membership_id",membership);actor.put("roles",roles);actor.put("service_permissions",permissions);
                var tenant=new LinkedHashMap<String,Object>(); tenant.put("schema_version","enterprise-context.v1");tenant.put("organization_id",org);tenant.put("workspace_id",workspace);tenant.put("mode","ENTERPRISE_SERVER");
                request.setAttribute("actor_context",actor);request.setAttribute("tenant_context",tenant);
                chain.doFilter(request,response);
            } catch (IdentityFailure error) {ErrorResponses.write(request,response,error);}
        }
    }
}
