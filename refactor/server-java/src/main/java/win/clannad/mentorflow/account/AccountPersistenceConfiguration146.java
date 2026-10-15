package win.clannad.mentorflow.account;

import javax.sql.DataSource;
import java.time.Clock;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;

/** Workspace discovery also supports JWT-only deployments; Alembic owns all DDL. */
@Configuration
public class AccountPersistenceConfiguration146 {
    @Bean @ConditionalOnMissingBean(Clock.class)
    Clock accountClock() {return Clock.systemUTC();}
    @Bean
    @ConditionalOnMissingBean(DataSource.class)
    @ConditionalOnProperty(name="MENTORFLOW_DATABASE_URL")
    DataSource accountDataSource(Environment env) {
        return NativeConfiguration144.nativeDataSource(env);
    }
}
