# Three-hour minimum rig plan

This plan targets a convincing AI-teacher idle presentation, not a commercial-game-equivalent final rig.

## Minimum layer set

1. back hair
2. torso and skirt
3. head and neck
4. front/side hair groups
5. left upper arm, forearm, and hand
6. right upper arm, forearm, and hand
7. left thigh, lower leg, and foot
8. right thigh, lower leg, and foot
9. left/right eye whites, irises, upper lids, and brows
10. mouth neutral/open/smile
11. chest and blouse deformation mesh
12. skirt hem deformation mesh

Hidden pixels behind hair, shoulders, elbows, hips, and knees must be reconstructed before rigging.

## Minimum bones and controls

- root, hips, torso, chest, neck, head
- upper/lower arm and hand chains
- thigh, lower-leg, and foot chains
- three to five hair chains
- eye X/Y, blink, mouth open, smile
- breathing, torso sway, hair physics approximation

## First animation set

- `idle`: breathing, subtle weight shift, hair sway
- `greeting`: raised-hand motion and smile
- `thinking`: head tilt, eye movement
- `talking`: mouth loop plus restrained upper-body motion
- `praising`: smile and small forward lean
- `click_react`: blink, head turn, hair follow-through

## Time box after mother-image approval

- 0:00–0:45: layer masks and hidden-area reconstruction
- 0:45–1:25: texture atlas and DragonBones skeleton
- 1:25–2:10: bones, weights, face controls, breathing and hair motion
- 2:10–2:40: semantic animation mapping and website manifest
- 2:40–3:00: desktop/mobile browser validation and fixes

The time box can produce a usable prototype. Commercial Spine-level cloth, body deformation, expression quality, and numerous hand-authored actions require several additional days.
