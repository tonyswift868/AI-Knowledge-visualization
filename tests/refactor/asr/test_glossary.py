import pytest

from refactor.learning.voice.asr.glossary import correct


@pytest.mark.parametrize("text,terms,expected", [
    ("今天学习扭断第一定律。", ["牛顿"], "今天学习牛顿第一定律。"),
    ("今天学习扭断第一定律。", [], "今天学习扭断第一定律。"),
    ("扭断第一定律。", ["质量", "物理"], "扭断第一定律。"),
    ("这里说牛顿，另一处说扭断。", ["牛顿"], "这里说牛顿，另一处说扭断。"),
    ("牛顿第一定律", ["牛顿", "扭断"], "牛顿第一定律"),
    ("力与例。", ["力"], "力与例。"),
    ("扭盾。", ["牛顿"], "牛顿。"),  # exact toneless homophones
    ("扭论。", ["牛顿"], "扭论。"),  # lun/dun have different initials
    ("留端。", ["牛顿"], "留端。"),  # two syllables differ
    ("牛当。", ["牛顿"], "牛当。"),  # dang/dun: same initial, > 1 edit
    ("物昂。", ["物安"], "物昂。"),  # zero initial is not fuzzy evidence
    ("度速。", ["读书"], "度速。"),  # s/sh are not the same initial
    ("比较 deep seek 和 openai。", ["DeepSeek", "Open AI"], "比较 DeepSeek 和 Open AI。"),
    ("N E W T O N", ["Newton"], "Newton"),
    ("使用 gpt 4 和 c ++", ["GPT4", "C++"], "使用 GPT4 和 C++"),
    ("AI 与 ai", ["AI"], "AI 与 ai"),  # canonical occurrence protects the term
    ("chair AIR _ai ai2", ["AI"], "chair AIR _ai ai2"),
    ("deep\nseek, deep-seek", ["DeepSeek"], "deep\nseek, deep-seek"),
    ("载声", ["再生", "在生"], "载声"),  # indistinguishable course terms
    ("载声", ["再生", "在生", "再深"], "载声"),  # don't fall back to a weaker guess
    ("open ai", ["OpenAI", "Open AI"], "open ai"),
    ("仲量", ["重量"], "重量"),  # phrase-aware reading zhong, not chong
])
def test_conservative_thresholds(text, terms, expected):
    output, edits = correct(text, terms)
    assert output == expected
    if output == text:
        assert edits == []
    for start, end, original, _ in edits:
        assert text[start:end] == original


def test_offsets_are_original_codepoints_and_can_apply_and_undo():
    text = "🎙 扭断与 deep seek，再提扭断。"
    output, edits = correct(text, ["牛顿", "DeepSeek"])
    assert output == "🎙 牛顿与 DeepSeek，再提牛顿。"
    assert edits == [(2, 4, "扭断", "牛顿"), (6, 15, "deep seek", "DeepSeek"), (18, 20, "扭断", "牛顿")]
    applied = text
    for start, end, original, replacement in reversed(edits):
        applied = applied[:start] + replacement + applied[end:]
    assert applied == output
    adjusted, delta = [], 0
    for start, end, original, replacement in edits:
        adjusted.append((start + delta, start + delta + len(replacement), original))
        delta += len(replacement) - (end - start)
    for start, end, original in reversed(adjusted):
        applied = applied[:start] + original + applied[end:]
    assert applied == text


def test_no_cascade_order_independence_and_longest_match():
    text = "扭断第一定律"
    terms = ["牛顿", "牛顿第一定律", " 牛顿 ", "", "牛顿"]
    expected = ("牛顿第一定律", [(0, 6, text, "牛顿第一定律")])
    assert correct(text, terms) == expected
    assert correct(text, reversed(terms)) == expected
    assert correct("扭盾", ["牛顿", "扭断"]) == ("牛顿", [(0, 2, "扭盾", "牛顿")])


def test_terms_must_be_a_collection_of_strings():
    with pytest.raises(TypeError):
        correct("扭断", "牛顿")
    with pytest.raises(TypeError):
        correct("扭断", [123])
