// Keep the lifecycle/performance contract inside Vitest's configured test root.
// The implementation-local suite is imported so it remains colocated with the
// loader while still running in every full frontend test pass.
import "../features/teachers/kksSwitchLifecycle.test"
