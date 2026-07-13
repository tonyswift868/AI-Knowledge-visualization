# Known limitations

- The current deterministic balance sample has a deliberate stage-50 boss choke point (21.88% win rate in the 1,152-battle report). It is a tuning signal for the chapter boss template, not a client-side difficulty gate.
- Multi-process SQLite load remains a follow-up; application-level idempotency, unique constraints and concurrent in-process publication are covered by the acceptance tests.
- Phaser replay is intentionally a compact event theater in V1. It renders the server event stream and portraits/assets while richer cut-in choreography can be added without changing the battle contract.

Excluded by product policy: PVP, guilds, rankings, arena, season reset, chat/friends, offline currency accrual, paid/VIP mechanics, manual skill selection, hard counters, and learning rewards from idle/browser time.
