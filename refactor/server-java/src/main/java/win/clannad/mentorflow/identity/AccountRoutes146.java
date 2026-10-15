package win.clannad.mentorflow.identity;

import java.util.Set;

/** Exact public paths from Python's lifecycle/reset boundary; no prefix exemption. */
public final class AccountRoutes146 {
    private AccountRoutes146() {}
    public static final Set<String> PUBLIC_PATHS=Set.of(
        "/api/enterprise/auth/lifecycle/capabilities",
        "/api/enterprise/auth/lifecycle/register",
        "/api/enterprise/auth/lifecycle/register/check",
        "/api/enterprise/auth/lifecycle/register/verify",
        "/api/enterprise/auth/lifecycle/register/resend",
        "/api/enterprise/auth/lifecycle/forgot",
        "/api/enterprise/auth/lifecycle/reset-code/request",
        "/api/enterprise/auth/lifecycle/reset-code/verify",
        "/api/enterprise/auth/lifecycle/reset-code/complete",
        "/api/enterprise/auth/reset/watch",
        "/api/enterprise/auth/reset/status");
    public static boolean isPublic(String path) {return PUBLIC_PATHS.contains(path);}
    public static boolean isWorkspace(String path) {
        return "/api/enterprise/account/workspaces".equals(path)
            ||"/api/enterprise/account/workspaces/initialize".equals(path);
    }
}
