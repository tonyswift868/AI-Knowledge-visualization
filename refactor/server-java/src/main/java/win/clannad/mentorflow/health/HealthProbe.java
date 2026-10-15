package win.clannad.mentorflow.health;

import java.util.List;

/** Health state provider; test applications can replace it without bypassing HTTP. */
public interface HealthProbe {
    boolean started();
    List<Dependency> dependencies();

    record Dependency(String name, boolean ready, String code, long durationMs,
                      boolean required, String requirementState) {}
}
