"""Offline metrics for the 420 search evaluation set."""
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence


LABELS = ("能用", "勉强", "不能用")


def precision_at_5(labels: Sequence[str], relevant_label: str = "能用") -> float:
    """Return relevant items among the first five, always using 5 as denominator."""
    return sum(label == relevant_label for label in labels[:5]) / 5


def cohen_kappa(rater_a: Sequence[str], rater_b: Sequence[str]) -> float:
    """Three-class nominal (unweighted) Cohen's kappa.

    For the degenerate case where expected agreement is 1, return 1 when the
    observed labels agree perfectly, and 0 otherwise. The latter is defensive:
    with identical constant marginals it cannot occur, but keeps the function
    defined for malformed/extended callers.
    """
    if len(rater_a) != len(rater_b):
        raise ValueError("raters must label the same number of items")
    if not rater_a:
        raise ValueError("kappa requires at least one labeled item")
    if any(label not in LABELS for label in (*rater_a, *rater_b)):
        raise ValueError(f"labels must be from {LABELS!r}")

    observed = sum(a == b for a, b in zip(rater_a, rater_b)) / len(rater_a)
    counts_a, counts_b = Counter(rater_a), Counter(rater_b)
    expected = sum(counts_a[label] * counts_b[label] for label in LABELS) / len(rater_a) ** 2
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def topic_gate(topic_labels: Mapping[str, Iterable[str]], minimum_usable: int = 3) -> dict:
    """Count usable labels per topic and report whether every topic reaches the gate."""
    if minimum_usable < 0:
        raise ValueError("minimum_usable must be non-negative")
    topics = {
        topic: {
            "usable": sum(label == "能用" for label in labels),
            "passes": False,
        }
        for topic, labels in topic_labels.items()
    }
    for result in topics.values():
        result["passes"] = result["usable"] >= minimum_usable
    return {"topics": topics, "passes": bool(topics) and all(row["passes"] for row in topics.values())}


if __name__ == "__main__":
    assert precision_at_5(["能用", "不能用"]) == 0.2
    assert precision_at_5(["能用"] * 5) == 1.0
    assert cohen_kappa(LABELS, LABELS) == 1.0
    assert cohen_kappa(["能用"] * 3, ["能用"] * 3) == 1.0
    assert cohen_kappa(["能用", "能用", "不能用"], ["不能用", "能用", "能用"]) < 0
    assert topic_gate({"A": ["能用"] * 3, "B": ["能用", "勉强"]}) == {
        "topics": {"A": {"usable": 3, "passes": True}, "B": {"usable": 1, "passes": False}},
        "passes": False,
    }
    print("metrics420 self-check passed")
