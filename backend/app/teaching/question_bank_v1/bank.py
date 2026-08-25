from __future__ import annotations

from collections import Counter
from typing import Iterable

from ...knowledge.inventory.models import stable_hash
from .fingerprint import QuestionDuplicateResolver
from .models import (
    QUESTION_BANK_VERSION,
    QuestionBankReceipt,
    QuestionCount,
    QuestionSourceTier,
    QuestionValidationStatus,
    TrustedQuestion,
    TrustedQuestionBank,
)


class TrustedQuestionBankBuilder:
    def __init__(
        self,
        *,
        duplicate_resolver: QuestionDuplicateResolver | None = None,
    ) -> None:
        self.duplicate_resolver = duplicate_resolver or QuestionDuplicateResolver()

    def build(self, candidates: Iterable[TrustedQuestion]) -> TrustedQuestionBank:
        resolution = self.duplicate_resolver.resolve(candidates)
        tier_counter = Counter(item.source_tier for item in resolution.questions)
        status_counter = Counter(item.validation_status for item in resolution.questions)
        bank_id = stable_hash(
            "tqb",
            {
                "compiler_version": QUESTION_BANK_VERSION,
                "conflicts": [item.to_dict() for item in resolution.conflicts],
                "questions": [item.to_dict() for item in resolution.questions],
            },
        )
        receipt_id = stable_hash(
            "tqbr",
            {
                "bank_id": bank_id,
                "collapsed_count": resolution.collapsed_count,
                "conflict_count": len(resolution.conflicts),
                "input_count": resolution.input_count,
                "retained_count": len(resolution.questions),
            },
        )
        receipt = QuestionBankReceipt(
            receipt_id=receipt_id,
            bank_id=bank_id,
            input_count=resolution.input_count,
            retained_count=len(resolution.questions),
            collapsed_count=resolution.collapsed_count,
            conflict_count=len(resolution.conflicts),
            tier_counts=tuple(
                QuestionCount(tier.value, tier_counter[tier])
                for tier in QuestionSourceTier
            ),
            status_counts=tuple(
                QuestionCount(status.value, status_counter[status])
                for status in QuestionValidationStatus
            ),
        )
        return TrustedQuestionBank(
            bank_id=bank_id,
            questions=resolution.questions,
            conflicts=resolution.conflicts,
            receipt=receipt,
        )
