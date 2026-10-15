package contract144;

import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import win.clannad.mentorflow.ServerApplication;

/** Only exposes test observations; native configuration and JDBC directory are production beans. */
public final class Contract144Main {
    public static void main(String[] args) {
        new SpringApplicationBuilder(ServerApplication.class,Contract144Fixtures.class)
            .web(WebApplicationType.SERVLET).run(args);
    }
}
