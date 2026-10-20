package verify178;

import java.nio.file.Path;
import win.clannad.mentorflow.rag.RagEmbedding;

/** Verifies the isolated classpath selects only the deterministic test adapter. */
public final class EmbeddingShadowVerify178 {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("expected shadow classes directory");
        Path expected = Path.of(args[0]).toRealPath();
        Path actual = Path.of(RagEmbedding.class.getProtectionDomain().getCodeSource()
                .getLocation().toURI()).toRealPath();
        if (!actual.equals(expected)) throw new AssertionError("unexpected RagEmbedding origin: " + actual);
        float[] first = new RagEmbedding().embed("fixed-vector-check");
        if (first.length != 512 || first[0] != 1.0f) throw new AssertionError("wrong fixed vector");
        first[0] = 0.0f;
        if (new RagEmbedding().embed("copy-check")[0] != 1.0f)
            throw new AssertionError("fixed vector must not be mutable across calls");
        String implementation = RagEmbedding.class.getName();
        if (!implementation.equals("win.clannad.mentorflow.rag.RagEmbedding"))
            throw new AssertionError("unexpected embedding implementation");
        System.out.println("RAG178_EMBEDDING_SHADOW_OK origin=" + actual + " dimensions=512 fixed=true");
    }
}
