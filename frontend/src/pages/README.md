# Page composition

The current product shell keeps page composition in `App.tsx`; route and feature contracts live in `app/routes.ts` and `features/*/api.ts` so pages can be split without changing API semantics.
