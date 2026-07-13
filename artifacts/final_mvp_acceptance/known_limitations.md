# Known limitations

- The planning chat returns a structured Plan/Replan action and confirmation target; it does not silently activate a replan. The existing plan page remains the authoritative confirmation surface.
- Code tasks use textual evidence and explicitly report `not_executed`/external evidence; no arbitrary code sandbox is introduced.
- Browser voice uses `speechSynthesis` with text fallback; no voice cloning or external audio generation is required for this MVP.
- The supplied reference Story Pack remains immutable. Project character illustrations are a presentation fallback/mapping and do not alter the Story Pack checksum or narrative facts.
