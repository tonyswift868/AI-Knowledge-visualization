# Teacher 02 — Blonde Fang Mother Image

## Provenance

- Generated on: 2026-07-26
- Generation path: Codex built-in ImageGen
- Character status: original adult character, explicitly designed as age 25+
- Reference role: visual-quality reference only for mature proportions, controlled skin sheen, dimensional chest/leg lighting, and premium mobile-game rendering
- Excluded from reuse: reference identity, face, exact hair, flower accessory, swimsuit, pose, silhouette, pixels, model data, and animation data
- No GameKee or Brown Dust asset is included in this directory.

## Files

- `teacher-blonde-fang-v1-keyed.png`: original one-shot generation on a flat magenta key
- `teacher-blonde-fang-v1.png`: locally keyed RGBA mother image for review and later layer separation
- `teacher-blonde-fang-v2-transform-sheet-keyed.png`: one-shot synchronized two-state edit on a flat magenta key
- `teacher-blonde-fang-v2-transform-sheet.png`: transparent two-state source sheet
- `teacher-blonde-fang-v2-teaching-review.png`: connected-component extraction of the bunny teaching state
- `teacher-blonde-fang-v2-swimsuit-review.png`: connected-component extraction of the swimsuit chat state

The two V2 review images are visual approval assets. They are not yet the final
rig layers. The production rig will use the swimsuit body as the base and make
the coat, skirt, socks, and shoes independently switchable overlays.

## Final generation prompt

Create one completely original adult anime woman, clearly age 25+, as a second AI teacher. Give her warm golden-blonde short layered hair with a small side braid, vivid blue eyes, and one clearly visible upper canine fang. Use mature non-chibi proportions around 6.5–7 heads tall, a voluptuous hourglass figure, very full bust, defined waist, rounded hips, substantial thighs, long shapely legs, and coherent adult anatomy.

Dress her in an original white halter-style blouse with opaque fabric, navy asymmetric wrap skirt, pale-blue sash, gold pendant, and dark-blue open-toe ankle-strap sandals. Show every toe. Use a relaxed upright three-quarter pose: one hand holds a slim closed teaching tablet and the other makes a friendly explaining gesture. Keep arms and legs separated for later 2D rigging.

Render as polished Japanese mobile-game key art with clean line art, soft cel shading blended with painterly gradients, warm key light, cool rim light, dimensional peach skin, and controlled glossy highlights on shoulders, collarbones, chest, arms and legs. The sheen must look luminous rather than wet or plastic. Use a perfectly flat `#ff00ff` chroma-key background with no floor, shadow, text, logo, watermark, or scenery. Keep the complete character inside frame from hair to toes.

Do not copy the reference character's identity, face, exact hairstyle, accessories, costume, pose, or silhouette. Avoid teenage appearance, chibi head, bikini, flower hairpiece, cropped feet, closed shoes, fused limbs, extra digits, nudity, or explicit content.

## Alpha validation

- Dimensions: 864 × 1821
- Mode: RGBA
- Alpha bounding box: `(159, 32, 796, 1792)`
- All four corner alpha values: `0`
- Full hair, hands, sandals, and toes remain inside the alpha bounds.

## V2 synchronized transformation edit

The V2 edit preserved the original adult blonde fang identity while changing
the skin to a lighter porcelain-ivory treatment and producing two coordinated
states in one generation:

1. Normal teaching: original ivory bunny-ear hooded coat, cream/blue/navy
   layered skirt, white over-knee socks, and navy Mary Jane shoes.
2. Private chat: original white-and-powder-blue swimsuit, blue pendant, ankle
   chain, and bare visible toes.

Both states use the same adult identity, mature proportions, camera, lighting,
standing baseline, separated legs, and raised teaching hand. The two supplied
game screenshots were used only for the abstract ideas of cute rabbit
outerwear, a cute skirt silhouette, fair glossy skin, and a white/blue swimsuit
contrast. No reference costume, flower, bicycle, food prop, pose, scene, pixels,
model data, or animation data was reused.

### V2 alpha validation

- Transform sheet: 864 × 1821 RGBA; alpha box `(5, 147, 861, 1678)`
- Teaching review: 531 × 1579 RGBA; alpha box `(24, 24, 507, 1555)`
- Swimsuit review: 459 × 1553 RGBA; alpha box `(24, 24, 435, 1529)`
- All four corners of all three files have alpha `0`.
- Teaching ears, raised hand, Mary Jane shoes, swimsuit hands, and all bare
  toes remain inside their respective alpha bounds.

### V2 edit prompt summary

Edit the original adult blonde fang teacher into a synchronized two-panel
character sheet. Keep the same clearly 25+ identity, short golden-blonde bob
with side braid, blue eyes, visible single fang, mature 6.5–7-head body, very
full bust, waist, hips, thighs, face, pose, scale, camera, and premium
mobile-game rendering. Make the skin lighter porcelain-ivory while retaining
controlled chest and leg highlights.

Panel A uses an original premium ivory bunny-ear cropped hooded coat with
powder-blue lining, blush-pink piping, rose-gold hardware and constellation
embroidery; an original cream, powder-blue and navy layered adult skater skirt;
white over-knee socks; and navy low-heel Mary Jane shoes. Panel B removes those
overlays and shows an original opaque white-and-blue halter swimsuit, pendant,
ankle chain, and bare toes. Both panels must use the same rig-friendly standing
pose with separated legs and fully visible hands and feet on a flat magenta
background. Do not copy either reference character or include schoolgirl
styling, teenage features, props, text, scenery, nudity, or explicit content.

## V4 cute-outfit and material edit

Generated with the Codex built-in ImageGen editor on 2026-07-26, using the V3
transparent review sheet as the edit target.

Files:

- `teacher-blonde-fang-v4-cute-gloss-keyed.png`: generated flat green-key source;
- `teacher-blonde-fang-v4-cute-gloss.png`: locally keyed RGBA review image.

Changes:

- the left teaching form now has a closed rabbit jacket and a coordinated
  ivory, blush-pink, baby-blue star-and-ribbon skirt set;
- the left face has a brighter friendly expression and a clearly readable
  single upper fang;
- the right form uses stronger controlled skin and fabric highlights;
- the two forms no longer overlap, making later extraction materially safer.

Validation:

- dimensions: `866 x 1817`;
- mode: `RGBA`;
- alpha extrema: `(0, 255)`;
- all four corner alpha values: `0`;
- SHA-256:
  `9DBC95BD8F2E364B503CA4C9C972C5A9C56D13928F9620169D003CD607319ACE`.

Known review limitation:

- the raised right hand is still too close to the canvas boundary for a final
  rig source. V4 is an art-direction checkpoint; after approval, each form
  should be regenerated separately with generous padding and no cropped
  extremities.

## V5 single teaching-form mother candidate

Generated with the Codex built-in ImageGen editor on 2026-07-26 after rejecting
the dual-form hairstyle attempts that produced collisions.

Files:

- `teacher-blonde-fang-v5-teaching-upturned-side-ponytail-keyed.png`:
  flat green-key source;
- `teacher-blonde-fang-v5-teaching-upturned-side-ponytail.png`:
  locally keyed RGBA review image.

Design state:

- single teaching-form character only;
- thin rabbit-plush hoodie with the hood down and long ears hanging behind;
- simple coordinated skater skirt with restrained trim;
- high side ponytail with an elevated, outward-curving, curled silhouette;
- small visible fang, complete hands, legs, shoes, and safe canvas padding.

Validation:

- dimensions: `878 x 1792`;
- mode: `RGBA`;
- alpha extrema: `(0, 255)`;
- alpha bounding box: `(197, 110, 732, 1698)`;
- all four corner alpha values: `0`;
- SHA-256:
  `EC2BD98C46BD4D74DF35CE6B633A7EF7C475DF3260300FB9CEC7F6B2109FB686`.

The supplied small hairstyle screenshot was used only for the abstract
side-ponytail trajectory: a high lateral tie, initial upward lift, an outward
curve, and curled tapered ends. No reference face, clothing, pose, pixels, or
character identity is included.

## V6 balanced bear teaching-form candidate

Generated with the Codex built-in ImageGen editor on 2026-07-26 after the
hairstyle and head-proportion review.

Files:

- `teacher-blonde-fang-v6-teaching-bear-balanced-keyed.png`: flat green-key
  source;
- `teacher-blonde-fang-v6-teaching-bear-balanced.png`: locally keyed RGBA
  review image.

Corrections:

- replaced the rabbit theme with a restrained milk-cream teddy-bear fleece
  hoodie, lowered hood, two small round hood ears, and a simple bear badge;
- simplified the coordinated skirt construction;
- increased the complete head/hair unit modestly and shortened the visible
  neck so the head-to-shoulder ratio reads more naturally;
- attached the side ponytail to the skull behind the ear, with gravity-driven
  fall and only a restrained lower-end curl;
- preserved the single-character rig-safe composition and complete limbs.

Validation:

- dimensions: `882 x 1783`;
- mode: `RGBA`;
- alpha extrema: `(0, 255)`;
- alpha bounding box: `(203, 85, 774, 1770)`;
- all four corner alpha values: `0`;
- SHA-256:
  `F6A3BCF78E23EB7F84CCC7075F340BBB1946C79D72B3861059BE665048295224`.

The supplied portrait reference was used only to judge the relationship
between skull size, neck length, shoulder width, and the side-ponytail tie.
No reference face, clothing, pose, pixels, or character identity is included.

## V8 gummy-bear dual-form candidates

Generated with the Codex built-in ImageGen editor on 2026-07-26. The teaching
form was refined from the approved honey-caramel direction, then the swimsuit
form was generated as a separate identity-preserving state. Both are stored as
independent full-body images to avoid overlap and simplify later PSD splitting.

Files:

- `teacher-blonde-fang-v8-teaching-gummy-bear-keyed.png`: flat green-key
  teaching-form source;
- `teacher-blonde-fang-v8-teaching-gummy-bear.png`: locally keyed RGBA
  teaching-form candidate;
- `teacher-blonde-fang-v8-swimsuit-gummy-bear-keyed.png`: flat green-key
  swimsuit-form source;
- `teacher-blonde-fang-v8-swimsuit-gummy-bear.png`: locally keyed RGBA
  swimsuit-form candidate.

Teaching-form changes:

- removed the two large lower-abdomen paw pockets and restored a clean waist;
- moved the gummy-bear paw-pad motifs to small symmetrical upper-chest
  appliques;
- increased the adult bust volume and expressed it through closed-cardigan
  seam shaping, fabric tension, folds, and controlled highlights;
- retained the amber gummy-bear zipper charm, cream skirt, restrained blue
  accents, natural head size, side ponytail, and single upper fang.

Swimsuit-form state:

- preserves the same adult face, hair, body proportions, gesture, scale, and
  camera;
- uses an opaque ivory halter swimsuit with powder-blue piping and the same
  amber gummy-bear charm;
- removes the teaching overlays, socks, and shoes and keeps both feet fully
  visible for later toe/foot rigging;
- retains the luminous premium-game skin and fabric highlight treatment.

Validation:

- both final images: `885 x 1778`, `RGBA`, alpha extrema `(0, 255)`;
- teaching alpha bounding box: `(203, 63, 744, 1745)`;
- swimsuit alpha bounding box: `(206, 69, 758, 1699)`;
- all four corner alpha values are `0` for both images;
- teaching SHA-256:
  `B428DE115152E06EDCB58F17E9B0332A750E5D2A4EE8C4E1F1A2F51D4C97076F`;
- swimsuit SHA-256:
  `B29F0649443469DF88FE81C28C95DB37C7685D1EE08E062271505B980483D626`.

Known pre-rig cleanup:

- the lowered hand still has slightly pointed generated fingertips despite the
  short-natural-nail correction pass. Correct the fingertip silhouettes during
  PSD separation before mesh or bone work; do not treat V8 as final rig art
  until that manual cleanup and a face-scale review pass.

## Current approved pair: V9

The active mother images retained in the application are:

- `teacher-blonde-fang-v9-teaching-heart-key.png`
- `teacher-blonde-fang-v9-swimsuit-heart-key.png`

V9 adds the shared leg-band design and heart/key jewellery requested for both
forms. The teaching form also carries the revised shaped bear zipper pull.
These two files replace the old V2 review images in `TeacherAvatarStudio`.

Validation:

- teaching: `884 x 1778`, RGBA, transparent corners, SHA-256
  `6E7D1AFC9813BFB35E850EFB572727DBB15FBA90E92380DE9C59523A21FF8655`;
- swimsuit: `885 x 1778`, RGBA, transparent corners, SHA-256
  `D86EA0AE63F75873AD16494E1306BEDA8E42E46F3D76C32CAE94D24467204E55`.

Historical V1-V8 candidates, key-colour sources, prompt packages, and the
See-Through PSD were moved without deletion to:

`E:\Downloads\MentorFlow_teacher2d_archive_20260726`
