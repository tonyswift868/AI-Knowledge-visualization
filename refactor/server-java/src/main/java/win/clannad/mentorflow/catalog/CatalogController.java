package win.clannad.mentorflow.catalog;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import win.clannad.mentorflow.identity.IdentityFailure;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Read-only catalog endpoints. The response body is the original file bytes. */
@RestController
@RequestMapping("/api/enterprise/catalog")
public final class CatalogController {
    private static final int MAX_CATALOG_BYTES = 1024 * 1024;
    private static final ObjectMapper STRICT_JSON = new ObjectMapper()
        .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

    private final Path catalogDirectory;

    public CatalogController(@Value("${MENTORFLOW_CATALOG_DIR:/run/mentorflow/catalog}") String directory) {
        this.catalogDirectory = Path.of(directory);
    }

    @GetMapping("/source-whitelist")
    public void sourceWhitelist(HttpServletRequest request, HttpServletResponse response) throws IOException {
        writeCatalog(request, response, "source-whitelist.json");
    }

    @GetMapping("/model-prices")
    public void modelPrices(HttpServletRequest request, HttpServletResponse response) throws IOException {
        writeCatalog(request, response, "model-prices.json");
    }

    private void writeCatalog(HttpServletRequest request, HttpServletResponse response, String filename)
        throws IOException {
        if (request.getAttribute("actor_context") == null || request.getAttribute("tenant_context") == null) {
            throw new IdentityFailure("ENTERPRISE_AUTH_REQUIRED", "Authenticated workspace required");
        }

        byte[] content;
        Path path = catalogDirectory.resolve(filename);
        try {
            // Read at most one byte over the inclusive limit, as the Python route does.
            try (var stream = Files.newInputStream(path)) {
                content = stream.readNBytes(MAX_CATALOG_BYTES + 1);
            }
        } catch (java.nio.file.NoSuchFileException missing) {
            throw new IdentityFailure("CATALOG_NOT_CONFIGURED", "目录文件尚未配置。", 404);
        } catch (IOException failure) {
            throw new IdentityFailure("CATALOG_INVALID", "目录文件暂不可用。", 503, failure);
        }

        if (content.length > MAX_CATALOG_BYTES || !isVersionedObject(content)) {
            throw new IdentityFailure("CATALOG_INVALID", "目录文件格式无效。", 503);
        }

        String etag = '"' + sha256(content) + '"';
        response.setHeader(HttpHeaders.ETAG, etag);
        response.setHeader(HttpHeaders.CACHE_CONTROL, "private, max-age=300");
        if (matches(request.getHeader(HttpHeaders.IF_NONE_MATCH), etag)) {
            response.setStatus(HttpServletResponse.SC_NOT_MODIFIED);
            return;
        }
        response.setStatus(HttpServletResponse.SC_OK);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setContentLength(content.length);
        response.getOutputStream().write(content);
    }

    private static boolean isVersionedObject(byte[] content) {
        try (JsonParser parser = STRICT_JSON.getFactory().createParser(content)) {
            JsonNode value = STRICT_JSON.readTree(parser);
            // FAIL_ON_TRAILING_TOKENS makes readTree reject a second root value.
            return value != null && value.isObject() && value.has("version");
        } catch (IOException | RuntimeException invalid) {
            return false;
        }
    }

    private static boolean matches(String header, String etag) {
        if (header == null) return false;
        for (String candidate : header.split(",", -1)) {
            String token = candidate.trim();
            if (token.equals("*") || token.startsWith("W/") && token.substring(2).equals(etag) || token.equals(etag)) {
                return true;
            }
        }
        return false;
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
