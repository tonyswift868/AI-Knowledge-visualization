package contract147;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import win.clannad.mentorflow.closure.ClosureSessionHooks;

/** Supplies only the pre-existing OIDC-session revocation seam for the local fixture. */
@Configuration(proxyBeanMethods=false)
public class Contract147Fixtures {
    @Bean @Primary ClosureSessionHooks closureSessionHooks147() {
        return new ClosureSessionHooks() {
            @Override public void throttle(String subject,String peer) { /* bounded HTTP suite uses low request volume */ }
            @Override public void revokeOidcSubject(String subject) { /* no browser OIDC sessions are seeded */ }
        };
    }
}
