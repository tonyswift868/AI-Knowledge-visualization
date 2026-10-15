package contract142;

import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import win.clannad.mentorflow.ServerApplication;

/** Test-only launcher. Loaded from Maven test-classes and never packaged in the service jar. */
public final class Contract142Main {
    private Contract142Main() {}
    public static void main(String[] args) {
        new SpringApplicationBuilder(ServerApplication.class, Contract142Fixtures.class)
            .web(WebApplicationType.SERVLET).run(args);
    }
}
