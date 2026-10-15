package win.clannad.mentorflow.closure;

/** Existing-identity-provider seam. Implementations must confirm deletion before returning. */
@FunctionalInterface
public interface ClosureProvider {
    default boolean active() { return true; }
    void ensureDeleted(String subject);
}
