# Known limitations

- The safe fallback analyzer is deterministic and rule-based. A provider can improve semantic richness, but provider output is not trusted to mutate Story Pack canon.
- Dynamic scenes currently use a bounded template rather than unconstrained generated prose; this is intentional for continuity and prompt-injection safety.
- Browser reminders depend on Notification permission and only run while the page is open; the existing daily refresh setting defaults to 00:00.
- The current acceptance suite validates representative material families and the fixed reference pack. A larger production corpus would need additional domain-specific adapters and evaluator rubrics.
- No online character/story search is performed; the fixed Story Pack and existing original art assets remain the source of canon and presentation.
