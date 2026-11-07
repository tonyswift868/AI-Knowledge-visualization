package win.clannad.mentorflow.persistence;

import java.lang.reflect.Field;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;
import org.springframework.core.env.StandardEnvironment;
import win.clannad.mentorflow.account.AccountPersistenceConfiguration146;
import win.clannad.mentorflow.rag.RagRepository;
import win.clannad.mentorflow.sync.SyncRepository;

/** Offline DI wiring gate: production repositories share one hidden business pool. */
public final class BusinessDatabaseWiring323Main {
    private BusinessDatabaseWiring323Main() {}

    public static void main(String[] args) throws Exception {
        configuredRepositoriesSharePool();
        missingUrlKeepsRepositoriesUnready();
        System.out.println("BUSINESS_DATABASE_WIRING_323_OK");
    }

    private static void configuredRepositoriesSharePool() throws Exception {
        try (AnnotationConfigApplicationContext context = context(Map.of(
                "MENTORFLOW_DATABASE_URL", "postgresql://fixture:fixture@localhost:5432/mentorflow"))) {
            BusinessDatabase business = context.getBean(BusinessDatabase.class);
            SyncRepository sync = context.getBean(SyncRepository.class);
            RagRepository rag = context.getBean(RagRepository.class);
            DataSource pool = business.dataSource();

            check(pool != null, "configured URL must create the lazy business pool");
            check(context.containsBean("accountDataSource"), "account DataSource condition bean must remain present");
            Map<String, DataSource> sources = context.getBeansOfType(DataSource.class);
            check(sources.size() == 1, "business pool must not be exposed as a DataSource bean: " + sources.keySet());
            check(sources.get("accountDataSource") != pool, "account DataSource must remain distinct from business pool");
            check(repositoryDataSource(sync) == pool, "SyncRepository must use the shared business pool");
            check(repositoryDataSource(rag) == pool, "RagRepository must use the shared business pool");
        }
    }

    private static void missingUrlKeepsRepositoriesUnready() throws Exception {
        try (AnnotationConfigApplicationContext context = context(Map.of())) {
            BusinessDatabase business = context.getBean(BusinessDatabase.class);
            check(business.dataSource() == null, "missing URL must not create a business pool");
            check(!ready(context.getBean(SyncRepository.class)), "SyncRepository must remain unready without URL");
            check(!ready(context.getBean(RagRepository.class)), "RagRepository must remain unready without URL");
            check(context.getBeansOfType(DataSource.class).isEmpty(), "missing URL must not create account DataSource");
        }
    }

    private static AnnotationConfigApplicationContext context(Map<String, Object> properties) {
        AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext();
        var sources = context.getEnvironment().getPropertySources();
        sources.remove(StandardEnvironment.SYSTEM_ENVIRONMENT_PROPERTY_SOURCE_NAME);
        sources.remove(StandardEnvironment.SYSTEM_PROPERTIES_PROPERTY_SOURCE_NAME);
        sources.addFirst(new MapPropertySource("fixture", properties));
        context.register(BusinessDatabase.class, SyncRepository.class, RagRepository.class,
                AccountPersistenceConfiguration146.class);
        try {
            context.refresh();
        } catch (RuntimeException | Error failure) {
            context.close();
            throw failure;
        }
        return context;
    }

    private static DataSource repositoryDataSource(Object repository) throws Exception {
        return (DataSource) field(repository.getClass(), "dataSource").get(repository);
    }

    private static boolean ready(Object repository) throws Exception {
        return field(repository.getClass(), "ready").getBoolean(repository);
    }

    private static Field field(Class<?> type, String name) throws NoSuchFieldException {
        Field field = type.getDeclaredField(name);
        field.setAccessible(true);
        return field;
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
