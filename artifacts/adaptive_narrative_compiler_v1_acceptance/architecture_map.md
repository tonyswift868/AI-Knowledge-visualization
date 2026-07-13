# Architecture map

```text
Workspace sources + goal
        |
        v
Task Semantic Analyzer -> TaskSemanticProfileORM
        |
        v
Coupling selector -> Fusion Adapter library (10 adapters, version 1.0.0)
        |
        v
Campaign Adaptation BlueprintORM
        |
        v
SceneContractORM -> Continuity snapshot/validator -> safe generated scene
        ^                                      |
        |                                      v
Learning Run evaluator -> LearningArtifactORM -> StoryObjectORM
                                                   |
                                                   v
                                  later dialogue / dynamic scene references
```

Isolation boundaries:

- profiles and artifacts are workspace-scoped;
- blueprints, contracts, snapshots, scenes, and Story Objects are campaign-scoped;
- the migration removes the incorrect one-artifact/one-Story-Object uniqueness so one artifact can be represented independently in multiple campaigns.
