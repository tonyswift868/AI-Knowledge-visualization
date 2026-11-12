package identity219;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.ReadListener;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import javax.sql.DataSource;
import com.zaxxer.hikari.HikariDataSource;
import org.springframework.core.env.Environment;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import win.clannad.mentorflow.account.WorkspaceController146;
import win.clannad.mentorflow.identity.IdentityDirectory;
import win.clannad.mentorflow.identity.IdentityFailure;

/** Behavior probes for the 219 identity closure check and bounded body changes. */
public final class IdentityWorkspace219Main {
    public static void main(String[] args) throws Exception {
        String mode=args.length==0?"context,body":args[0];
        if(mode.contains("context"))contextChecksClosureOnceAndStillRejectsClosedAccounts();
        if(mode.contains("body"))workspaceBodyStopsAtLimitAndReturns413();
        if(mode.contains("pool")&&args.length>1)verifyPoolLifecycle(args[1]);
        System.out.println("IDENTITY_WORKSPACE_219_OK");
    }

    private static void contextChecksClosureOnceAndStillRejectsClosedAccounts() throws Exception {
        AtomicInteger checks = new AtomicInteger();
        IdentityDirectory open = directory((method, arguments) -> {
            if (method.equals("ensureOpen")) { checks.incrementAndGet(); return null; }
            if (method.equals("resolve")) return new IdentityDirectory.Membership("m1", List.of("OWNER"));
            if (method.equals("reachable")) return true;
            return null;
        });
        runContextFilter(open);
        require(checks.get() == 1, "open request closure checks=" + checks.get());

        AtomicInteger deniedChecks = new AtomicInteger();
        IdentityDirectory closed = directory((method, arguments) -> {
            if (method.equals("ensureOpen")) {
                deniedChecks.incrementAndGet();
                throw new IdentityFailure(403, "ACCOUNT_CLOSURE_IN_PROGRESS", "closed");
            }
            return null;
        });
        int status = runContextFilter(closed);
        require(deniedChecks.get() == 1 && status == 403,
            "closed request checks=" + deniedChecks.get() + " status=" + status);
    }

    private static IdentityDirectory directory(DirectoryCall call) {
        return (IdentityDirectory) Proxy.newProxyInstance(IdentityDirectory.class.getClassLoader(),
            new Class<?>[]{IdentityDirectory.class}, (proxy, method, arguments) -> call.call(method.getName(), arguments));
    }

    private static int runContextFilter(IdentityDirectory directory) throws Exception {
        var attributes = new java.util.HashMap<String,Object>();
        var responseState = new int[]{200};
        var responseBytes = new ByteArrayOutputStream();
        HttpServletRequest request = (HttpServletRequest) Proxy.newProxyInstance(
            HttpServletRequest.class.getClassLoader(), new Class<?>[]{HttpServletRequest.class},
            (proxy, method, arguments) -> switch (method.getName()) {
                case "getRequestURI" -> "/api/enterprise/catalog/model-prices";
                case "getHeader" -> "X-MentorFlow-Organization-ID".equals(arguments[0]) ? "org-219" : null;
                case "getAttribute" -> attributes.get(arguments[0]);
                case "setAttribute" -> { attributes.put((String) arguments[0], arguments[1]); yield null; }
                case "getCookies" -> null;
                case "getScheme" -> "https";
                case "getMethod" -> "GET";
                case "getContextPath", "getServletPath", "getPathInfo" -> "";
                default -> defaultValue(method.getReturnType());
            });
        HttpServletResponse response = (HttpServletResponse) Proxy.newProxyInstance(
            HttpServletResponse.class.getClassLoader(), new Class<?>[]{HttpServletResponse.class},
            (proxy, method, arguments) -> {
                if (method.getName().equals("setStatus")) responseState[0] = (Integer) arguments[0];
                if (method.getName().equals("getOutputStream")) return new jakarta.servlet.ServletOutputStream() {
                    @Override public boolean isReady() { return true; }
                    @Override public void setWriteListener(jakarta.servlet.WriteListener listener) {}
                    @Override public void write(int value) { responseBytes.write(value); }
                };
                return defaultValue(method.getReturnType());
            });
        Instant now = Instant.now();
        Jwt jwt = Jwt.withTokenValue("token219").header("alg", "RS256").claim("iss", "https://issuer.example")
            .subject("subject219").claim("jti", "jti219").issuedAt(now.minusSeconds(1)).expiresAt(now.plusSeconds(300))
            .claim("azp", "client219").build();
        SecurityContextHolder.getContext().setAuthentication(new JwtAuthenticationToken(jwt, List.of(), "subject219"));
        try {
            Class<?> type = Class.forName("win.clannad.mentorflow.identity.IdentitySecurity$ContextFilter");
            var constructor = type.getDeclaredConstructor(IdentityDirectory.class); constructor.setAccessible(true);
            Object filter = constructor.newInstance(directory);
            var method = type.getDeclaredMethod("doFilterInternal", HttpServletRequest.class,
                HttpServletResponse.class, FilterChain.class); method.setAccessible(true);
            method.invoke(filter, request, response, (FilterChain) (req, res) -> {});
            return responseState[0];
        } finally { SecurityContextHolder.clearContext(); }
    }

    private static void workspaceBodyStopsAtLimitAndReturns413() throws Exception {
        byte[] body = new byte[1_048_589];
        AtomicInteger consumed = new AtomicInteger();
        ServletInputStream stream = new ServletInputStream() {
            private final InputStream source = new java.io.ByteArrayInputStream(body);
            @Override public int read() throws IOException { int value=source.read(); if(value>=0)consumed.incrementAndGet(); return value; }
            @Override public int read(byte[] buffer,int offset,int length) throws IOException {
                int count=source.read(buffer,offset,length); if(count>0)consumed.addAndGet(count); return count;
            }
            @Override public boolean isFinished() { return consumed.get() == body.length; }
            @Override public boolean isReady() { return true; }
            @Override public void setReadListener(ReadListener listener) {}
        };
        HttpServletRequest request = (HttpServletRequest) Proxy.newProxyInstance(
            HttpServletRequest.class.getClassLoader(), new Class<?>[]{HttpServletRequest.class},
            (proxy, method, arguments) -> method.getName().equals("getInputStream") ? stream : defaultValue(method.getReturnType()));
        Object result = new WorkspaceController146(null, null, null).initialize(request);
        require(result instanceof org.springframework.http.ResponseEntity<?> response && response.getStatusCode().value() == 413,
            "oversized body did not return 413: " + result);
        require(consumed.get() == 4097, "oversized body bytes read=" + consumed.get());
    }

    private static void verifyPoolLifecycle(String databaseUrl) throws Exception {
        var directory=newDirectory(databaseUrl);
        var verified=win.clannad.mentorflow.identity.JdbcIdentityDirectory.class.getDeclaredField("verified");
        verified.setAccessible(true);((AtomicBoolean)verified.get(directory)).set(true);
        var dataSource=win.clannad.mentorflow.identity.JdbcIdentityDirectory.class.getDeclaredField("dataSource");
        dataSource.setAccessible(true);
        require(dataSource.get(directory) instanceof HikariDataSource,"directory is not backed by Hikari pool");
        HikariDataSource pool=(HikariDataSource)dataSource.get(directory);
        require(pool.getMaximumPoolSize()==10,"unexpected pool max="+pool.getMaximumPoolSize());
        require(pool.getConnectionTimeout()==10_000,"unexpected pool timeout="+pool.getConnectionTimeout());
        var connect=win.clannad.mentorflow.identity.JdbcIdentityDirectory.class.getDeclaredMethod("connect");
        connect.setAccessible(true);
        int pid=backendPid((Connection)connect.invoke(directory));
        Connection first=(Connection)connect.invoke(directory);
        try(Statement statement=first.createStatement()) {
            try(ResultSet result=statement.executeQuery("SELECT pg_backend_pid()")) { result.next(); require(result.getInt(1)==pid,"pool did not reuse an idle backend"); }
            first.setAutoCommit(false);
            statement.execute("SET LOCAL application_name='identity-pool-reset-219'");
        }
        first.close();
        Connection reset=(Connection)connect.invoke(directory);
        try(reset;Statement statement=reset.createStatement()) {
            require(reset.getAutoCommit(),"borrowed connection retained autoCommit=false");
            try(ResultSet result=statement.executeQuery("SELECT pg_backend_pid(),current_setting('application_name')")) {
                result.next();require(result.getInt(1)==pid,"pool did not return the same backend after transaction");
                require(!"identity-pool-reset-219".equals(result.getString(2)),"SET LOCAL leaked across pool return");
            }
        }
        verifyConcurrentBorrows((DataSource)dataSource.get(directory));
        var close=win.clannad.mentorflow.identity.JdbcIdentityDirectory.class.getMethod("close");
        close.invoke(directory);
        boolean rejected=false;
        try { connect.invoke(directory); }
        catch(java.lang.reflect.InvocationTargetException expected) { rejected=expected.getCause() instanceof java.sql.SQLException; }
        require(rejected,"closed directory pool accepted a new connection");

        var unavailable=newDirectory("jdbc:postgresql://127.0.0.1:1/postgres?user=postgres");
        require(!unavailable.reachable(),"temporarily unreachable database was reported healthy");
        unavailable.close();
    }

    private static win.clannad.mentorflow.identity.JdbcIdentityDirectory newDirectory(String databaseUrl) throws Exception {
        var environment = (Environment) Proxy.newProxyInstance(Environment.class.getClassLoader(),
            new Class<?>[]{Environment.class}, (proxy, method, arguments) -> {
                if (method.getName().equals("getProperty") && arguments != null && arguments.length >= 1) {
                    String key=(String)arguments[0];
                    if(key.equals("MENTORFLOW_DATABASE_URL")) return databaseUrl;
                    if(key.equals("MENTORFLOW_SERVICE_IDENTITY_GRANTS_JSON")) return "";
                    if(arguments.length >= 2) return arguments[1];
                }
                return defaultValue(method.getReturnType());
            });
        var settings=(win.clannad.mentorflow.identity.IdentitySettings)uninitialized(win.clannad.mentorflow.identity.IdentitySettings.class);
        var issuer=win.clannad.mentorflow.identity.IdentitySettings.class.getField("canonicalIssuer");issuer.setAccessible(true);
        issuer.set(settings,"https://issuer.example");
        return new win.clannad.mentorflow.identity.JdbcIdentityDirectory(environment,settings);
    }

    private static Object uninitialized(Class<?> type) throws Exception {
        var unsafeField=sun.misc.Unsafe.class.getDeclaredField("theUnsafe");unsafeField.setAccessible(true);
        return ((sun.misc.Unsafe)unsafeField.get(null)).allocateInstance(type);
    }

    private static int backendPid(Connection connection) throws Exception {
        try(connection;Statement statement=connection.createStatement();ResultSet result=statement.executeQuery("SELECT pg_backend_pid()")) {
            result.next();return result.getInt(1);
        }
    }

    private static void verifyConcurrentBorrows(DataSource source) throws Exception {
        int count=4;
        var ready=new CountDownLatch(count);var release=new CountDownLatch(1);
        var pool=Executors.newFixedThreadPool(count);var pids=java.util.Collections.synchronizedSet(new java.util.HashSet<Integer>());
        try {
            var futures=new java.util.ArrayList<java.util.concurrent.Future<?>>();
            for(int i=0;i<count;i++) futures.add(pool.submit(()-> {
                try(Connection connection=source.getConnection();Statement statement=connection.createStatement();
                    ResultSet result=statement.executeQuery("SELECT pg_backend_pid()")) {
                    result.next();pids.add(result.getInt(1));ready.countDown();
                    if(!release.await(10,TimeUnit.SECONDS))throw new AssertionError("concurrent pool release timed out");
                } catch(Exception failure) {throw new RuntimeException(failure);}
            }));
            require(ready.await(10,TimeUnit.SECONDS),"concurrent pool borrow timed out");
            require(pids.size()==count,"concurrent pool reused an active backend: "+pids);
            release.countDown();for(var future:futures)future.get(10,TimeUnit.SECONDS);
        } finally {release.countDown();pool.shutdownNow();}
    }

    private static Object defaultValue(Class<?> type) {
        if (!type.isPrimitive()) return null;
        if (type == boolean.class) return false;
        if (type == char.class) return '\0';
        return 0;
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    @FunctionalInterface private interface DirectoryCall { Object call(String method,Object[] arguments) throws Throwable; }
}
