package contract147;

import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import win.clannad.mentorflow.ServerApplication;

/** Test-only launcher for the real Java application on local fixtures. */
public final class Contract147Main {
    private Contract147Main() {}
    public static void main(String[] args) {
        new SpringApplicationBuilder(ServerApplication.class, Contract147Fixtures.class)
            .web(WebApplicationType.SERVLET).run(args);
    }
}
