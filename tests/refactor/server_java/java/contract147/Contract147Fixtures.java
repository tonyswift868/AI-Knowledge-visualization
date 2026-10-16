package contract147;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import win.clannad.mentorflow.account.IdentitySessionRevocation146;

/** Local-only fixture marker; closure now uses the production account hooks. */
@Configuration(proxyBeanMethods=false)
public class Contract147Fixtures {
    @Bean IdentitySessionRevocation146 contract147OidcSessionRevocation() {
        return (issuer,subject)->{
            String root=System.getenv("JAVA144_TMP");
            if(root!=null&&!root.isBlank())try {
                Files.writeString(Path.of(root).resolve("closure-oidc-revocations.log"),
                    issuer+"\t"+subject+"\n",StandardOpenOption.CREATE,StandardOpenOption.APPEND);
            }catch(Exception error){throw new IllegalStateException("local OIDC revocation trace write failed",error);}
            return !"user-closure-hooks-fail".equals(subject);
        };
    }
}
