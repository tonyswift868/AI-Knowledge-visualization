package win.clannad.mentorflow;

import java.util.*;
import jakarta.mail.Session;
import org.eclipse.angus.mail.smtp.SMTPTransport;
import org.keycloak.email.*;
import org.keycloak.models.KeycloakSession;
import org.keycloak.truststore.JSSETruststoreConfigurator;
import org.keycloak.common.enums.HostnameVerificationPolicy;

/** Observe the actual realm mail sender's network. Never creates or sends a message. */
final class MailTransportHealth {
    private static Map<String,String> result(boolean ready,String code) {
        return Map.of("status",ready?"READY":"UNAVAILABLE","code",code);
    }
    static Map<String,String> observe(KeycloakSession session) {
        var config=session.getContext().getRealm().getSmtpConfig();
        if(config==null || config.isEmpty())return result(false,"EMAIL_AUTHORITY_NOT_CONFIGURED");
        Properties props=new Properties();
        EmailAuthenticator authenticator;
        try {
            boolean tls="true".equals(config.get("starttls")), ssl="true".equals(config.get("ssl"));
            int port=Integer.parseInt(config.get("port"));
            if(tls==ssl || !"true".equals(config.get("auth")) || port<1 || port>65535
                    || Objects.toString(config.get("host"),"").isBlank()
                    || Objects.toString(config.get("user"),"").isBlank()
                    || Objects.toString(config.get("password"),"").isBlank()
                    || !"basic".equals(config.getOrDefault("authType","basic")))throw new IllegalArgumentException();
            // validate() checks addresses only. Reuse the active sender and its
            // existing BASIC authenticator (including Keycloak vault resolution).
            var sender=session.getProvider(EmailSenderProvider.class);
            var factory=session.getKeycloakSessionFactory().getProviderFactory(EmailSenderProvider.class);
            if(!(sender instanceof DefaultEmailSenderProvider) || !(factory instanceof DefaultEmailSenderProviderFactory))
                throw new IllegalArgumentException();
            sender.validate(config);
            authenticator=((DefaultEmailSenderProviderFactory)factory).getEmailAuthenticators().get(EmailAuthenticator.AuthenticatorType.BASIC);
            if(authenticator==null)throw new IllegalArgumentException();
            props.setProperty("mail.smtp.host",config.get("host"));
            // Avoid reverse DNS of the container/desktop hostname during EHLO.
            props.setProperty("mail.smtp.localhost","[127.0.0.1]");
            props.setProperty("mail.smtp.port",Integer.toString(port));
            props.setProperty("mail.smtp.auth","true");
            props.setProperty("mail.smtp.ssl.enable",Boolean.toString(ssl));
            props.setProperty("mail.smtp.starttls.enable",Boolean.toString(tls));
            props.setProperty("mail.smtp.starttls.required",Boolean.toString(tls));
            props.setProperty("mail.smtp.ssl.checkserveridentity","true");
            props.setProperty("mail.smtp.ssl.protocols","TLSv1.3 TLSv1.2");
            props.setProperty("mail.smtp.connectiontimeout","1000");
            props.setProperty("mail.smtp.timeout","1000");
            props.setProperty("mail.smtp.writetimeout","1000");
            props.setProperty("mail.smtp.quitwait","false");
            props.setProperty("mail.debug","false");
            props.setProperty("mail.debug.auth","false");
            var trust=new JSSETruststoreConfigurator(session);
            var sockets=trust.getSSLSocketFactory();
            if(sockets!=null) {
                if(trust.getProvider().getPolicy()==HostnameVerificationPolicy.ANY)throw new IllegalArgumentException();
                props.put("mail.smtp.ssl.socketFactory",sockets);
            }
        } catch(Exception invalid) { return result(false,"EMAIL_CONFIGURATION_UNAVAILABLE"); }
        var mail=Session.getInstance(props);mail.setDebug(false);
        try(var transport=(SMTPTransport)mail.getTransport("smtp")) {
            authenticator.connect(session,config,transport);
            // Some SMTP libraries allow connect() when AUTH is not advertised.
            // Require evidence of both encrypted transport and successful AUTH.
            if(!transport.isSSL() || transport.getLastReturnCode()!=235)
                return result(false,"EMAIL_TRANSPORT_UNAVAILABLE");
            transport.issueCommand("NOOP",250);
            return result(true,"EMAIL_TLS_AUTH_READY");
        } catch(Exception unavailable) {
            // Never expose/log provider exceptions, SMTP replies, config or secrets.
            return result(false,"EMAIL_TRANSPORT_UNAVAILABLE");
        }
    }
}
