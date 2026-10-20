package win.clannad.mentorflow.rag;

import java.util.Objects;
import org.springframework.stereotype.Component;

/** Test-only shadow for the production BGE adapter; returns a fixed 512-vector. */
@Component
public final class RagEmbedding {
    private static final float[] FIXED = fixedVector();

    static {
        System.out.println("RAG178_FIXED_EMBEDDING_LOADED");
    }

    public float[] embed(String text) {
        Objects.requireNonNull(text, "text");
        return FIXED.clone();
    }

    private static float[] fixedVector() {
        float[] vector = new float[512];
        vector[0] = 1.0f;
        return vector;
    }
}
