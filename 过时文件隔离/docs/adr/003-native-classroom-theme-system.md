# ADR-003: Native classroom theme system

## Status

Accepted for the native classroom UI.

## Context

The classroom needs several coherent visual skins without turning the learning
state, teacher persona, or user data into theme-specific variants. It also needs
runtime switching from Settings and a generic first-install default.

## Open-source references

- **qt-material** (BSD-2-Clause) demonstrates runtime PySide6 stylesheet
  switching and theme palettes with primary, surface, and text roles.
- **daisyUI themes** (MIT) demonstrates named semantic theme tokens, one explicit
  default theme, runtime user selection, and component-wide consistency.
- **Hugging Face Chat UI** (Apache-2.0) is used as a conversation-layout
  reference: the transcript remains the primary surface and secondary controls
  do not replace the reading column.

No source code or visual assets from these projects are copied into MentorFlow,
and no new runtime dependency is added. MentorFlow keeps its existing PySide6
widgets and maps a small product-owned semantic palette into QSS.

## Decision

- `sakura` is the product default and preserves the existing pink identity.
- `cream`, `sky`, and `night` are optional local skins.
- A skin controls background atmosphere, surfaces, borders, role bubbles,
  selection indicators, scrollbars, and teacher-stage chrome together.
- The skin selector lives in Settings, outside the classroom.
- Switching skins never mutates lesson/session/draft/mastery state.
- The detached teacher stage remains large; it never consumes transcript width.
- The installed software contains only generic built-in defaults. A local skin
  choice is not included in the new-device migration snapshot.

## Consequences

The theme registry is intentionally small and testable. Adding a skin requires a
complete semantic palette rather than isolated control recoloring. The animated
background and message fade stay presentation-only and never invoke a Provider
or write learning evidence.
