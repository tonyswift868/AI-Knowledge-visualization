import pytest
from refactor.learning.voice.tts.text_prep import prepare_text, split_sentences


def test_markdown_code_links_math_and_acronyms():
    value = prepare_text("# **今天**\n先看 [文档](https://example.com/a)。\n```python\nprint('secret code')\n```\n用 API 读取 20% 的数据，公式 $x^2=4$。")
    assert "今天" in value and "文档" in value and "接口" in value
    assert "这里有一段代码" in value and "这里有一个公式" in value
    assert "百分之二十" in value
    assert all(marker not in value for marker in ("```", "secret code", "https://", "**", "$", "^"))


@pytest.mark.parametrize("source,expected", [
    ("3.5kg", "三点五千克"), ("250ms", "二百五十毫秒"), ("16GB", "十六吉字节"),
    ("2026年9月27日", "二零二六年九月二十七日"), ("10001", "一万零一"),
    ("-3.14", "负三点一四"), ("1e-3", "一乘以十的负三次方"), ("3-5分钟", "三到五分钟"),
    ("3~5分钟", "三到五分钟"),
    ("答案是-3", "答案是负三"),
    ("GPU 和 CPU", "图形处理器 和 中央处理器"), ("v1.2.3", "版本一点二点三"),
    ("36℃", "三十六摄氏度"), ("00120", "零零一二零"),
])
def test_chinese_readings(source, expected):
    assert prepare_text(source) == expected


def test_split_chinese_sentences_then_long_clauses_never_exceeds_sixty():
    source = "我们先看一个例子。接着想一想，为什么这样呢？" + "这是比较长的解释，" * 20 + "无标点" * 40
    result = split_sentences(source)
    assert result[0] == "我们先看一个例子。"
    assert all(0 < len(s) <= 60 for s in result)
    assert "".join(result) == prepare_text(source)


def test_empty_unfinished_code_and_very_long_input():
    assert not split_sentences(" \n ** ### ")
    assert "代码" in prepare_text("下面是代码：```python\nwhile True: pass")
    with pytest.raises(ValueError):
        split_sentences("长" * 100001)
    with pytest.raises(ValueError):
        split_sentences("hello", 100)


def test_acronym_expansion_is_split_after_normalization():
    assert all(len(s) <= 60 for s in split_sentences("CPU，GPU，HTTP，HTTPS，" * 15))
