"""Explicit live fetch probe; never collected/run by pytest. No model or login.

Run from the repo with --output pointing to disposable benchmark evidence.
Only the supplied T04 fixture URLs are read; no page links are crawled.
"""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from refactor.learning.course.research_pipeline import ResearchPipeline
from refactor.learning.course.source_filter import RawHit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fixture = ROOT/'tests/refactor/source_filter/fixtures/with_tool.json'
    data = json.loads(fixture.read_text(encoding='utf-8'))
    hits = [RawHit(item.get('url') or '', item.get('title') or '')
        for block in data['response']['content'] if block.get('type') == 'web_search_tool_result'
        for item in block['content']]
    started = datetime.now(timezone.utc).isoformat()
    with ResearchPipeline() as pipeline:
        result = pipeline.run(hits)
    summary = dict(started_at=started, input_count=len(hits), attempted=result.attempted,
        passed=len(result.verified), elapsed_seconds=round(result.elapsed_seconds, 3),
        rejected_counts=dict(Counter(item.code for item in result.rejected)),
        cancelled=result.cancelled, validation='existence/extraction/policy/title only; relevance is not scored',
        verified=[dict(url=item.url, final_url=item.final_url, title=item.title, site=item.site,
            tier=item.tier, category=item.category, reasons=item.reasons,
            retrieved_at=item.retrieved_at, content_sha256=item.content_sha256,
            media_type=item.document.media_type, chars=item.document.char_count) for item in result.verified],
        rejected=[asdict(item) for item in result.rejected])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
