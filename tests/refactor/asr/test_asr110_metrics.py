"""Offline checks for the opt-in evaluation's metrics and corpus preservation."""
from tests.refactor.asr.asr110_evaluate import distance, norm, scores, zip_hotwords


def test_asr110_metric_counts_and_normalization():
    assert norm('GraphRAG、向量 RAG。') == 'graphrag向量rag'
    assert distance('向量那个', '下料来个') == 3
    assert scores('Chunk 切太大', 'chunk切太大。', ['Chunk']) == {
        'errors': 0, 'characters': 8, 'terms_total': 1, 'terms_correct': 1}


def test_asr110_missing_terms_are_not_scored_by_phonetic_similarity():
    result = scores('用 Agent 当场决定', '用那个当场决定', ['Agent'])
    assert result['terms_correct'] == 0
    assert result['terms_total'] == 1


def test_asr110_hotword_encoder_drops_only_unencodable_entries():
    assert zip_hotwords(['向量', 'GraphRAG', 'Top-p', 'E1027', '未知'], {'向','量'}) == ['向量','GRAPHRAG']
