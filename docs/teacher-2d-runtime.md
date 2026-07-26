# Teacher 2D runtime

MentorFlow currently uses the approved V9 teaching/swimsuit mother images as
the default AI teacher. The default renderer is a low-cost DOM/CSS `image2d`
adapter. It preserves the finished artwork, avoids the old Koikatsu/PMX GPU
cost, and keeps the teaching brain independent from the visual runtime.

DragonBonesJS and its official Shizuku sample remain in the repository as a
licensed fallback and adapter reference. They are no longer the default
teacher or the product's character art.

## Runtime boundary

Business pages only use:

- `Teacher2DStage`
- `TeacherPresentationController`
- the `Teacher2DRenderer` interface

The controller selects a renderer from the manifest:

- `ImageTeacherAdapter`: current default; preloads the two V9 PNGs and renders
  them without WebGL.
- `DragonBonesTeacherAdapter`: optional MIT-licensed skeleton sample.
- `PlaceholderTeacherAdapter`: safe boundary for unsupported/future runtimes.

Changing the mother image or adopting a later Spine/DragonBones rig does not
change Learning Run, provider calls, teaching state, or the AI response flow.

## Current V9 manifest

- Manifest:
  `/teacher2d/original-ai-teacher-blonde-fang/manifest.json`
- Teaching form:
  `teacher-blonde-fang-v9-teaching-heart-key.png`
- Swimsuit form:
  `teacher-blonde-fang-v9-swimsuit-heart-key.png`
- Renderer: `image2d`
- WebGL: disabled
- Forms: `teaching`, `swimsuit`

Both forms retain the requested shared character identity, heart/key jewellery,
leg-band design, and the revised teaching-form zipper detail.

## Interaction and motion

The lightweight renderer provides:

- continuous slow breathing and small centre-of-gravity movement;
- pointer-following lean using bounded CSS variables;
- head, torso, and lower-body click zones with different reactions;
- semantic motions for idle, greeting, thinking, talking, explaining,
  questioning, praising, encouraging, confused, and ending;
- pause, mirror, scale, reset, and manual action controls;
- a visible teaching/swimsuit form control;
- a 1.05-second form transition using an outgoing silhouette, light sweep,
  energy ring, and incoming-form reveal;
- `prefers-reduced-motion` handling;
- page and renderer lifecycle cleanup without a second 3D runtime.

The motion is intentionally whole-image motion. It is not represented as
independent arm, mouth, hair, breast, or cloth bones. Those local deformations
require an approved layered PSD and a real mesh/bone rig.

## Teaching-state mapping

| Teaching state | Lightweight action |
| --- | --- |
| idle | breathe |
| greeting / ending | wave |
| thinking | think |
| talking | talk |
| explaining | explain |
| questioning | question |
| praising | cheer |
| encouraging | heart |
| confused | confused |

Learning Run derives these states from actual product state: request in
progress, model response, active question, correct result, retry result, and
errors. The image layer never decides teaching content.

## Replacing the current mother images

If a revised pair keeps approximately the same transparent full-body framing:

1. Place the two approved PNGs under a new
   `frontend/public/teacher2d/<teacher-id>/` directory.
2. Copy and update the V9 manifest with the new `textures` and `imageStates`
   URLs.
3. Point `DEFAULT_TEACHER_MANIFEST_URL` at the new manifest.
4. Run the image-adapter tests, production build, and real-browser visual gate.

No layer separation is required for a mother-image replacement in this
lightweight mode. Separation becomes necessary only when upgrading to
independent limb, face, hair, cloth, or physics animation.

## Verification

Focused checks:

```powershell
cd frontend
npm.cmd exec -- vitest run src/test/image_teacher_adapter.test.ts src/test/teacher_2d_presentation.test.ts src/test/teacher_companion_stage.test.tsx src/test/teacher_mother_image_review.test.tsx --pool=forks --maxWorkers=1 --no-file-parallelism
npm.cmd run build
```

Real-browser acceptance must confirm:

- both forms load without broken requests;
- the teacher is fully visible in the library and Learning Run;
- pointer movement changes `--look-x` and `--look-y`;
- click zones enter a visible reaction state;
- the form control activates the transition before the incoming image settles;
- browser console contains no errors.
