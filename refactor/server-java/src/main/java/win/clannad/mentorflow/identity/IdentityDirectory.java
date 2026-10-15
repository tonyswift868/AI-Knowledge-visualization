package win.clannad.mentorflow.identity;

import java.util.List;
import java.util.Map;

/** Authorization remains database/configuration-owned, never taken from JWT roles. */
public interface IdentityDirectory {
    record Membership(String id, List<String> roles) {}
    void ensureOpen(String issuer, String subject);
    Membership resolve(String issuer, String subject, String organization, String workspace);
    Map<String,Object> service(String issuer, String subject, String clientId);
    boolean reachable();
}
