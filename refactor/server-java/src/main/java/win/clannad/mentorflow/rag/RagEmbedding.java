package win.clannad.mentorflow.rag;

import dev.langchain4j.model.embedding.onnx.bgesmallzhv15q.BgeSmallZhV15QuantizedEmbeddingModel;
import org.springframework.stereotype.Component;

import java.util.Objects;

/**
 * In-process Chinese embeddings for RAG. The ONNX model is loaded on the first
 * request so ordinary server startup and health checks do not pay its memory cost.
 *
 * Query-specific instructions are intentionally supplied by the caller: stored
 * document chunks and search queries have different recommended prefixes.
 */
@Component
public final class RagEmbedding {
    private volatile BgeSmallZhV15QuantizedEmbeddingModel model;

    /** Embed one text and return the model's 512-dimensional vector. */
    public float[] embed(String text) {
        Objects.requireNonNull(text, "text");
        return model().embed(text).content().vector();
    }

    private BgeSmallZhV15QuantizedEmbeddingModel model() {
        BgeSmallZhV15QuantizedEmbeddingModel current = model;
        if (current == null) {
            synchronized (this) {
                current = model;
                if (current == null) {
                    current = new BgeSmallZhV15QuantizedEmbeddingModel();
                    model = current;
                }
            }
        }
        return current;
    }
}
