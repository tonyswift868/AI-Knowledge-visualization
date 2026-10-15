package contract145;

import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import win.clannad.mentorflow.ServerApplication;

/** Isolated local test launcher; production components still handle sync HTTP requests. */
public final class Contract145Main {
    private Contract145Main() {}
    public static void main(String[] args) {
        new SpringApplicationBuilder(ServerApplication.class, Contract145Fixtures.class)
            .web(WebApplicationType.SERVLET).run(args);
    }
}
