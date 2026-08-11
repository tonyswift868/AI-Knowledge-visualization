import unittest

from PIL import Image

from desktop_pet import AnimationModel, DesktopPet, ForegroundClassifier, normalize_vk_code


class DesktopPetModelTests(unittest.TestCase):
    def test_idle_state_has_no_synthetic_key_activity(self) -> None:
        model = AnimationModel()
        self.assertEqual(model.key_visuals(0.0), {})
        self.assertEqual(model.key_states(0.0), {})
        self.assertEqual(model.frame(0.05)["mode"], "idle")

    def test_explicit_capture_pause_does_not_disable_idle_visuals(self) -> None:
        model = AnimationModel()
        model.note_key(65, now=10.0)
        self.assertEqual(model.frame(10.05)["mode"], "typing")

        model.set_capture_enabled(False)
        model.note_key(66, now=10.06)
        frame = model.frame(10.08)
        self.assertFalse(frame["capture_enabled"])
        self.assertTrue(frame["visual_enabled"])
        self.assertEqual(frame["mode"], "idle")

    def test_repeated_keys_are_smoothed_into_one_burst(self) -> None:
        model = AnimationModel()
        model.note_key(65, now=0.0)
        model.note_key(66, now=0.04)
        first = model.frame(0.05)
        second = model.frame(0.08)
        self.assertGreater(first["dy"], -10)
        self.assertLess(abs(float(second["dy"])), 10)

    def test_key_visuals_keep_the_pressed_key_until_spring_back(self) -> None:
        model = AnimationModel()
        model.note_key(0xA0, now=0.0)  # left Shift normalizes to one keycap
        active = model.key_visuals(0.06)
        self.assertIn(0x10, active)
        self.assertGreater(active[0x10], 0.1)
        self.assertEqual(normalize_vk_code(0xA1), 0x10)
        self.assertEqual(model.key_visuals(0.5), {})

    def test_key_state_exposes_progress_for_hand_travel(self) -> None:
        model = AnimationModel()
        model.note_key(ord("Q"), now=2.0)
        early = model.key_states(2.02)[ord("Q")]
        late = model.key_states(2.20)[ord("Q")]
        self.assertLess(early[1], late[1])
        self.assertGreater(early[0], 0.0)

    def test_key_bias_changes_with_keyboard_side(self) -> None:
        model = AnimationModel()
        model.note_key(ord("A"), now=1.0)
        left = model.frame(1.04)
        model.note_key(ord("L"), now=1.10)
        right = model.frame(1.14)
        self.assertLess(float(left["key_bias"]), 0)
        self.assertGreater(float(right["key_bias"]), 0)

    def test_process_classifier_is_explicit_and_case_insensitive(self) -> None:
        classifier = ForegroundClassifier(["ExampleGame.exe"])
        self.assertTrue(classifier.is_game_process("examplegame.EXE"))
        self.assertFalse(classifier.is_game_process("notepad.exe"))

    def test_arm_sources_keep_sleeve_connected_to_fingertip(self) -> None:
        source = Image.new("RGBA", (1254, 1254), (255, 255, 255, 255))
        arms = DesktopPet._build_arm_sources(source)
        self.assertEqual(set(arms), {"left", "right"})
        self.assertLess(arms["left"].fingertip[0], arms["left"].shoulder[0])
        self.assertGreater(arms["right"].fingertip[0], arms["right"].shoulder[0])
        self.assertGreater(arms["left"].fingertip[1], arms["left"].shoulder[1])
        self.assertGreater(arms["right"].fingertip[1], arms["right"].shoulder[1])


if __name__ == "__main__":
    unittest.main()
