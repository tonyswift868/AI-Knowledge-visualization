"""Offline contract checks for summarize420; uses synthetic data only."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def write_inputs(work: Path, *, incomplete=False, missing_label=False, duplicate_label=False,
                 duplicate_combo=False, short_topic_gate=False, order_differs=False):
    from run420 import TOPICS

    runs = []
    labels = []
    for topic_index, topic in enumerate(TOPICS):
        for arm in ("A", "B", "C"):
            ids = [f"{topic_index}-{arm}-{index}" for index in range(10)]
            special_target = topic_index == 0 and arm == "B"
            for index, candidate_id in enumerate(ids):
                if special_target and index < 5 and (short_topic_gate or order_differs):
                    label = "不能用"
                elif special_target and index >= 5 and short_topic_gate:
                    label = ("能用", "不能用", "勉强", "能用", "不能用")[index - 5]
                elif special_target and index >= 5 and order_differs:
                    label = ("能用", "能用", "能用", "不能用", "不能用")[index - 5]
                else:
                    label = "能用" if index < 3 else "不能用"
                labels.append({"id": candidate_id, "label": label})
            if special_target:
                judgments = [
                    {"id": ids[5], "score": 3},
                    {"id": ids[6], "score": 2},
                    {"id": ids[7], "score": 0},
                ]
            else:
                judgments = [{"id": ids[i], "score": 3} for i in range(3)]
            runs.append({
                "topic": topic,
                "arm": arm,
                "order": ids[:5],
                "selected": ids[5:10] if special_target else ids[:5],
                "judgments": judgments,
                "search_calls": 0,
                "read_calls": 0,
                "model_calls": 0,
                "cny_upper": 0,
                "elapsed_seconds": 0,
                "rerank_seconds": 0,
            })
    if incomplete:
        runs.pop()
    if duplicate_combo:
        runs.append(dict(runs[0]))
    (work / "runs.json").write_text(json.dumps(runs, ensure_ascii=False), encoding="utf-8")
    if missing_label:
        labels = [row for row in labels if row["id"] != "0-B-5"]
    if duplicate_label:
        labels.append(dict(labels[0]))
    label_path = work / "labels.jsonl"
    label_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in labels), encoding="utf-8")
    pool = [{"id": row["id"]} for row in labels]
    if missing_label:
        pool.append({"id": "0-B-5"})
    (work / "blind-pool.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in pool), encoding="utf-8")
    (work / "costs.json").write_text("[]", encoding="utf-8")
    return label_path


def rejected(call, expected):
    try:
        call()
    except expected:
        return
    raise AssertionError(f"expected {expected.__name__}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run420 import TOPICS
    from summarize420 import summarize

    with TemporaryDirectory(dir=args.work, prefix="summary-check-") as temporary:
        root = Path(temporary)

        incomplete = root / "incomplete"
        incomplete.mkdir()
        labels_path = write_inputs(incomplete, incomplete=True)
        rejected(lambda: summarize(incomplete, labels_path), ValueError)

        repeated_run = root / "repeated-run"
        repeated_run.mkdir()
        labels_path = write_inputs(repeated_run, duplicate_combo=True)
        rejected(lambda: summarize(repeated_run, labels_path), ValueError)

        missing = root / "missing"
        missing.mkdir()
        labels_path = write_inputs(missing, missing_label=True)
        rejected(lambda: summarize(missing, labels_path), KeyError)

        duplicate = root / "duplicate"
        duplicate.mkdir()
        labels_path = write_inputs(duplicate, duplicate_label=True)
        rejected(lambda: summarize(duplicate, labels_path), ValueError)

        valid = root / "valid"
        valid.mkdir()
        labels_path = write_inputs(valid, short_topic_gate=True)
        result = summarize(valid, labels_path)
        assert len(result["rows"]) == len(TOPICS) * 3
        target = next(row for row in result["rows"] if row["topic"] == TOPICS[0] and row["arm"] == "B")
        assert target["usable"] == 2 and not target["enough"]
        assert target["precision_at_5"] == 0.0
        assert target["kappa"] == 0.0
        assert not result["gate"]["B"]

        ordered = root / "ordered"
        ordered.mkdir()
        labels_path = write_inputs(ordered, order_differs=True)
        ordered_result = summarize(ordered, labels_path)
        target = next(row for row in ordered_result["rows"]
                      if row["topic"] == TOPICS[0] and row["arm"] == "B")
        assert target["usable"] == 3 and target["enough"]
        assert target["selected_count"] == 5 and target["selected_precision"] == 0.6
        assert target["precision_at_5"] == 0.0
        assert ordered_result["gate"]["B"]

    print("check_summary420 offline checks passed")


if __name__ == "__main__":
    main()
