# Adaptive Narrative Compiler V1

Implemented the material-to-scene pipeline inside the existing MentorFlow project.

- Workspace material is analyzed into a persisted `TaskSemanticProfile`.
- Coupling is selected as `strong`, `weak`, or `parallel` and a versioned Fusion Adapter is selected.
- A fixed Story Pack remains immutable; the Campaign Adaptation Blueprint binds the selected adapter to its existing anchors and learning gates.
- Learning Runs publish `LearningArtifact` records only at evaluator-backed terminal phases.
- Campaign-scoped `StoryObject` records expose verified facts, uncertainties, world effects, revision state, and later references.
- Scene Contracts and safe generated scenes are validated against fixed Story Pack constraints.
- Review artifacts revise earlier objects; provisional artifacts do not change adaptive world state.
- Prompt-injection-like material is sanitized and retained as data only.
- Frontend campaign and learning views expose the adaptive brief, artifacts, Story Objects, and scenario choices.

The implementation uses deterministic rules as the safe fallback when no provider is available. Provider use is not required for the compiler acceptance path.
