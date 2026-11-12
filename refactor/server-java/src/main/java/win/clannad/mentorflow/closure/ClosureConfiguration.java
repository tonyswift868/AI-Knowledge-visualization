package win.clannad.mentorflow.closure;

import java.time.Clock;
import javax.sql.DataSource;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import win.clannad.mentorflow.account.NativeIdentityBridge;
import win.clannad.mentorflow.account.NativeSessionService;
import win.clannad.mentorflow.identity.IdentityFailure;
import win.clannad.mentorflow.identity.IdentitySettings;

/** Wire closure without changing the account-owned native/cookie implementations. */
@Configuration
@ConditionalOnProperty(name="MENTORFLOW_DATABASE_URL")
public class ClosureConfiguration {
    @Bean @ConditionalOnMissingBean(ClosureStore.class)
    ClosureStore closureStore(IdentitySettings settings,DataSource source) {
        return new PostgresClosureStore(source,settings.canonicalIssuer);
    }
    @Bean @ConditionalOnMissingBean(ClosureAdminClient.class)
    ClosureAdminClient closureAdminClient(Environment env,ObjectProvider<Clock> clocks) {
        return new ClosureAdminClient(env,clocks.getIfAvailable(Clock::systemUTC));
    }
    @Bean @ConditionalOnMissingBean(ClosureProvider.class)
    ClosureProvider closureProvider(ClosureAdminClient admin) {return admin;}
    @Bean @ConditionalOnMissingBean(ClosureAuthority.class)
    ClosureAuthority closureAuthority(ClosureAdminClient admin,ObjectProvider<NativeSessionService> sessions,
            ObjectProvider<NativeIdentityBridge> bridges,ObjectProvider<ClosureSessionHooks> hooks) {
        return new ClosureAuthority() {
            @Override public boolean active(){return admin.active()&&bridges.getIfAvailable()!=null&&hooks.getIfAvailable()!=null;}
            @Override public void throttle(String subject,String peer) {
                ClosureSessionHooks value=hooks.getIfAvailable();
                if(value==null)throw new ClosureFailure("CLOSURE_UNAVAILABLE");
                value.throttle(subject,peer);
            }
            @Override public void verifyCurrentPassword(String issuer,String subject,String password) {
                String username=admin.username(subject);
                try {
                    NativeIdentityBridge bridge=bridges.getIfAvailable();
                    if(bridge==null)throw new ClosureFailure("CLOSURE_IDENTITY_UNAVAILABLE");
                    var checked=bridge.authenticate(username,password);
                    if(!issuer.equals(checked.issuer())||!subject.equals(checked.subject()))throw new ClosureFailure("CLOSURE_IDENTITY_INVALID",403);
                }catch(IdentityFailure error) {
                    if("NATIVE_CREDENTIALS_INVALID".equals(error.code))throw new ClosureFailure("CURRENT_PASSWORD_INVALID",400);
                    throw new ClosureFailure("CLOSURE_IDENTITY_UNAVAILABLE");
                }
            }
            @Override public Revocation revokeLocal(String subject) {
                boolean nativeDone=false,oidcDone=false;
                try{NativeSessionService service=sessions.getIfAvailable();if(service!=null){service.revokeSubject(subject);nativeDone=true;}}catch(Exception ignored){}
                try{ClosureSessionHooks value=hooks.getIfAvailable();if(value!=null){value.revokeOidcSubject(subject);oidcDone=true;}}catch(Exception ignored){}
                return new Revocation(nativeDone,oidcDone);
            }
        };
    }
    @Bean @ConditionalOnMissingBean(ClosureService.class)
    ClosureService closureService(ClosureStore store,ClosureProvider provider,ClosureAuthority authority,ObjectProvider<Clock> clocks) {
        return new ClosureService(store,provider,authority,clocks.getIfAvailable(Clock::systemUTC));
    }
}
