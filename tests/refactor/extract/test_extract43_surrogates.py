"""BUG-43: PDF text with surrogate halves must not break extraction output or saving."""
import json

from refactor.learning.course.extract import clean_text
from refactor.learning.preferences.storage import AccountJSON


def test_clean_text_joins_pairs_folds_math_letters_and_replaces_lone_halves():
    italic_x = "\U0001d465"                       # MATHEMATICAL ITALIC SMALL X
    pair = italic_x.encode("utf-16-le").decode("utf-16-le", "surrogatepass")
    split = "".join(chr(int.from_bytes(italic_x.encode("utf-16-be")[i:i + 2], "big")) for i in (0, 2))
    assert clean_text("解方程 " + split + " + 1 = 3") == "解方程 x + 1 = 3"
    assert clean_text(pair) == "x"
    lone = "\ud835" + "等式"
    cleaned = clean_text(lone)
    assert cleaned == "�等式"
    cleaned.encode("utf-8")                       # encodable now
    assert clean_text("，（中文标点）！") == "，（中文标点）！"


def test_account_json_saves_a_draft_with_a_lone_surrogate(tmp_path):
    store = AccountJSON("mfa1_" + "0" * 64, tmp_path, "draft.json")
    store.write({"excerpt": "原文\ud835片段", "items": ["\udc65"]})
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert saved == {"excerpt": "原文�片段", "items": ["�"]}
