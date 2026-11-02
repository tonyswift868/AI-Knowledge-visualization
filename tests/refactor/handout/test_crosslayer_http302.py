from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from scripts.handout.crosslayer_http import extract, _java_routes, _python_routes, _norm


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_http302_resolves_literals_fstrings_and_class_prefixes(tmp_path):
    write(tmp_path, "refactor/learning/api.py", '''
API = "/api/v1"
class Client:
    def fetch(self, item_id):
        self.transport.request_json("GET", f"{API}/items/{item_id}")
    def remove(self):
        self.transport.request_json("DELETE", API + "/items/known")
''')
    java = '''
package demo;
public final class Service { public static final String PREFIX="/api/v1"; }
@RequestMapping(Service.PREFIX)
public class ItemController {
  @GetMapping("/items/{id}")
  public Object fetch() { return null; }
  @DeleteMapping("/items/known") public Object remove() { return null; }
}
'''
    write(tmp_path, "refactor/server-java/src/demo/ItemController.java", java)
    result = extract(tmp_path)
    by_name = {row["name"]: row for row in result["connections"]}
    get = by_name["GET /api/v1/items/{}"]
    assert get["source"] == dict(file="refactor/learning/api.py", line=5, function="fetch")
    assert get["target"]["function"] == "fetch"
    assert get["target"]["line"] == 7
    assert by_name["DELETE /api/v1/items/known"]["target"]["function"] == "remove"
    assert not result["unresolved"]


def test_http302_follows_one_static_request_wrapper_to_path_callsite(tmp_path):
    write(tmp_path, "refactor/learning/rag.py", '''
API = "/api/enterprise/rag/v1"
class Rag:
    def send(self, query):
        self.transport.request_json("POST", API + "/search")
    def upload(self, row):
        self._send_upload(f"{API}/documents/{row['id']}/chunks")
    def _send_upload(self, path):
        self.transport.request_json("POST", path)
''')
    write(tmp_path, "refactor/server-java/src/demo/RagController.java", '''
@RequestMapping("/api/enterprise/rag/v1")
public class RagController {
  @PostMapping("/search") public Object search() { return null; }
  @PostMapping("/documents/{doc_id}/chunks")
  public Object upload() { return null; }
}
''')
    result = extract(tmp_path)
    search = next(row for row in result["connections"] if row["name"] == "POST /api/enterprise/rag/v1/search")
    upload = next(row for row in result["connections"] if row["name"] == "POST /api/enterprise/rag/v1/documents/{}/chunks")
    assert search["source"]["function"] == "send"
    assert upload["source"]["function"] == "upload"
    assert upload["source"]["line"] == 7
    assert upload["via"] == [dict(file="refactor/learning/rag.py", line=9, function="_send_upload")]


def test_http302_parses_fastapi_prefix_and_keeps_unmatched_path_unresolved(tmp_path):
    write(tmp_path, "refactor/server/api.py", '''
router = APIRouter(prefix="/api/internal")
@router.delete("/items/{item_id}")
def delete_item():
    pass
''')
    write(tmp_path, "refactor/learning/client.py", '''
class Client:
    def one(self, value):
        self.http.request_json("DELETE", f"/api/internal/items/{value}")
    def two(self):
        self.http.request_json("GET", "/api/internal/missing")
''')
    result = extract(tmp_path)
    assert len(result["connections"]) == 1
    assert result["connections"][0]["target"]["function"] == "delete_item"
    assert len(result["unresolved"]) == 1
    assert result["unresolved"][0]["name"] == "GET /api/internal/missing"


def test_http302_ignores_non_http_method_mapping_and_external_urls(tmp_path):
    write(tmp_path, "refactor/learning/client.py", '''
class Client:
    def fetch(self):
        self.http.get("https://outside.example/resource")
        self.cache.get("/api/v1/items")
''')
    write(tmp_path, "refactor/server-java/src/demo/Controller.java", '''
@RequestMapping("/api/v1")
public class ItemController {
  @GetMapping("/items") public Object get() { return null; }
}
''')
    result = extract(tmp_path)
    assert result["connections"] == []
    assert result["unresolved"] == []
    assert result["dynamic"] == []


def test_http302_normalizes_only_path_parameter_names():
    assert _norm("api/v1/items/{first_id}/chunks/{second}") == "/api/v1/items/{}/chunks/{}"
    assert _norm("/api//v1/search/") == "/api/v1/search"
