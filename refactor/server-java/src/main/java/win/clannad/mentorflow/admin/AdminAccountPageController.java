package win.clannad.mentorflow.admin;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.json.JsonMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import org.springframework.core.env.Environment;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Read-only workbench view, served only by the dedicated local connector. */
@RestController
public final class AdminAccountPageController {
    private static final JsonMapper JSON = new JsonMapper();
    private static final Map<String, String> COUNTERS = counters();
    private final AdminOverviewService overview;
    private final int pagePort;

    public AdminAccountPageController(AdminOverviewService overview, Environment env) {
        this.overview = overview;
        this.pagePort = env.getProperty("MENTORFLOW_ADMIN_ACCOUNT_PAGE_PORT", Integer.class, 8081);
    }

    @GetMapping(value = "/admin/accounts", produces = MediaType.TEXT_HTML_VALUE)
    public ResponseEntity<byte[]> page(HttpServletRequest request) {
        if (request.getLocalPort() != pagePort) return notFound();
        JsonNode totals;
        try { totals = JSON.readTree(overview.collect()); }
        catch (Exception unavailable) { totals = JSON.createObjectNode(); }
        List<AdminOverviewService.AccountRow> rows = overview.accountRows();
        StringBuilder html = new StringBuilder(4096);
        html.append("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
                .append("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
                .append("<title>MentorFlow 账号</title><style>")
                .append("body{margin:0;background:#f5f7fa;color:#182230;font:15px/1.5 system-ui,sans-serif}")
                .append("main{max-width:1280px;margin:0 auto;padding:28px 20px}h1{font-size:25px;margin:0 0 22px}")
                .append("h2{font-size:16px;margin:0 0 12px}.totals{background:#fff;border:1px solid #dce2e9;border-radius:10px;padding:18px;margin-bottom:24px}")
                .append("dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px 18px;margin:0}")
                .append("dt{color:#5d6877;font-size:13px}dd{margin:2px 0 0;font-size:20px;font-variant-numeric:tabular-nums}")
                .append(".table-scroll{overflow-x:auto;background:#fff;border:1px solid #dce2e9;border-radius:10px}")
                .append("table{border-collapse:collapse;min-width:900px;width:100%;white-space:nowrap}th,td{padding:11px 14px;border-bottom:1px solid #e7ebf0;text-align:left}")
                .append("th{background:#f8fafc;color:#495566;font-size:13px;font-weight:600}tbody tr:last-child td{border-bottom:0}")
                .append("th:nth-child(n+4),td:nth-child(n+4){text-align:right;font-variant-numeric:tabular-nums}</style></head>")
                .append("<body><main><h1>MentorFlow 账号</h1><section class=\"totals\"><h2>账号总数</h2><dl>");
        for (var counter : COUNTERS.entrySet()) {
            html.append("<dt>").append(escape(counter.getValue())).append("</dt>")
                    .append("<dd data-counter=\"").append(escape(counter.getKey())).append("\">")
                    .append(escape(display(totals.get(counter.getKey())))).append("</dd>");
        }
        html.append("</dl></section><h2>账号</h2><div class=\"table-scroll\"><table id=\"accounts\"><thead><tr>")
                .append("<th>账号</th><th>注册时间（日本时间）</th><th>最近同步日（UTC）</th>")
                .append("<th>ready 文档数</th><th>ready 分段数</th>")
                .append("<th>已同步题数（只算同步到服务器的）</th></tr></thead><tbody>");
        for (var row : rows) {
            html.append("<tr data-testid=\"account-row\">");
            cell(html, row.account()); cell(html, row.registeredAt());
            cell(html, row.lastSyncDay() == null || "null".equals(row.lastSyncDay()) ? "取不到" : row.lastSyncDay());
            cell(html, display(row.readyDocumentCount())); cell(html, display(row.readyChunkCount()));
            cell(html, display(row.syncedQuestionCount())); html.append("</tr>");
        }
        html.append("</tbody></table></div></main></body></html>");
        byte[] body = html.toString().getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .contentType(new MediaType("text", "html", StandardCharsets.UTF_8))
                .contentLength(body.length).body(body);
    }

    private static ResponseEntity<byte[]> notFound() {
        byte[] body = "Not Found".getBytes(StandardCharsets.UTF_8);
        return ResponseEntity.status(404).header(HttpHeaders.CACHE_CONTROL, "no-store")
                .contentType(MediaType.TEXT_PLAIN).contentLength(body.length).body(body);
    }

    private static void cell(StringBuilder html, String value) {
        html.append("<td>").append(escape(value == null ? "取不到" : value)).append("</td>");
    }

    private static String display(Object value) {
        if (value == null) return "取不到";
        if (value instanceof JsonNode node) return node.isNull() ? "取不到" : node.asText();
        return String.valueOf(value);
    }

    private static String escape(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\"", "&quot;").replace("'", "&#39;");
    }

    private static Map<String, String> counters() {
        Map<String, String> values = new LinkedHashMap<>();
        values.put("registered_human_users", "注册账号总数"); values.put("registrations_today", "今日注册");
        values.put("registrations_7d", "近 7 天注册"); values.put("registrations_30d", "近 30 天注册");
        values.put("enabled_users", "启用账号"); values.put("disabled_users", "停用账号");
        values.put("email_verified_users", "邮箱已验证"); values.put("email_unverified_users", "邮箱未验证");
        values.put("required_action_users", "有待完成操作"); values.put("backend_account_count", "后台账号数");
        values.put("workspace_member_count", "工作区成员数"); values.put("identity_account_mismatch_count", "身份不匹配数");
        return Collections.unmodifiableMap(values);
    }
}
