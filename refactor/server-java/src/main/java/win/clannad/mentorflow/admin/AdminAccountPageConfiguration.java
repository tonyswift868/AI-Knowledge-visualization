package win.clannad.mentorflow.admin;

import org.apache.catalina.connector.Connector;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AdminAccountPageConfiguration {
    @Bean
    @ConditionalOnProperty(name = "MENTORFLOW_ADMIN_ACCOUNT_PAGE_ENABLED", havingValue = "true")
    WebServerFactoryCustomizer<TomcatServletWebServerFactory> adminAccountPageConnector(
            org.springframework.core.env.Environment env) {
        return factory -> {
            int port = env.getProperty("MENTORFLOW_ADMIN_ACCOUNT_PAGE_PORT", Integer.class, 8081);
            Connector connector = new Connector(TomcatServletWebServerFactory.DEFAULT_PROTOCOL);
            connector.setPort(port);
            connector.setProperty("address", "0.0.0.0");
            factory.addAdditionalTomcatConnectors(connector);
        };
    }
}
