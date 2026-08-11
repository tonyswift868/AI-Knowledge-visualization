"""Independent Windows desktop pet prototype.

The pet animation is deliberately independent from text input:

* the render/idle loop keeps ticking while a game is in the foreground;
* the low-level keyboard hook is observational in every foreground window;
* the hook never swallows, changes, or injects a key, it only emits a short
  visual reaction for a real key-down event.

This is a replacement companion for the closed-source Sogou pet component.
It does not patch, inject into, or modify Sogou Input.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import queue
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageTk


KEY_COLOR = "#00ff00"
KEY_PULSE_SECONDS = 0.34
KEYBOARD_BG = "#182234"
KEYBOARD_KEY = "#2d3d56"
KEYBOARD_KEY_OUTLINE = "#6a7d9a"
KEYBOARD_ACTIVE = "#ffd66b"
KEYBOARD_ACTIVE_OUTLINE = "#fff0b5"
DEFAULT_GAME_PROCESSES = {
    # Conservative defaults. Add a process explicitly with --game-process
    # when a game is not detected by its full-screen window.
    "cs2.exe",
    "eldenring.exe",
    "genshinimpact.exe",
    "starrail.exe",
    "leagueclient.exe",
    "valorant-win64-shipping.exe",
}


def normalize_vk_code(vk_code: int) -> int:
    """Collapse left/right modifier VKs so one keycap can represent them."""

    value = int(vk_code)
    if value in (0xA0, 0xA1):  # VK_LSHIFT / VK_RSHIFT
        return 0x10
    if value in (0xA2, 0xA3):  # VK_LCONTROL / VK_RCONTROL
        return 0x11
    if value in (0xA4, 0xA5):  # VK_LMENU / VK_RMENU
        return 0x12
    return value


def vk_label(vk_code: int) -> str:
    """Return a compact, human-readable label for a Windows virtual key."""

    value = normalize_vk_code(vk_code)
    if 0x30 <= value <= 0x39 or 0x41 <= value <= 0x5A:
        return chr(value)
    labels = {
        0x08: "⌫",
        0x09: "Tab",
        0x0D: "Enter",
        0x10: "Shift",
        0x11: "Ctrl",
        0x12: "Alt",
        0x1B: "Esc",
        0x20: "Space",
        0x25: "←",
        0x26: "↑",
        0x27: "→",
        0x28: "↓",
        0x2E: "Del",
        0xBA: ";",
        0xBB: "+",
        0xBC: ",",
        0xBD: "-",
        0xBE: ".",
        0xBF: "/",
        0xC0: "`",
        0xDB: "[",
        0xDC: "\\",
        0xDD: "]",
        0xDE: "'",
    }
    return labels.get(value, f"0x{value:02X}")


def key_motion_bias(vk_code: int) -> float:
    """Map a key's keyboard column to a small, natural body lean."""

    value = normalize_vk_code(vk_code)
    if value in (0x25,):
        return -1.0
    if value in (0x27,):
        return 1.0
    if value in {ord(letter) for letter in "12345QWERTASDFGZXCVB"}:
        return -1.0
    if value in {ord(letter) for letter in "67890YUIOPHJKLNM"}:
        return 1.0
    return 0.0


@dataclass(frozen=True)
class ForegroundInfo:
    hwnd: int = 0
    pid: int = 0
    process_name: str = ""
    title: str = ""
    is_fullscreen: bool = False
    is_game: bool = False


@dataclass(frozen=True)
class ArmSource:
    """A source-art arm cutout with explicit shoulder and fingertip anchors."""

    image: Image.Image
    shoulder: tuple[float, float]
    fingertip: tuple[float, float]


class AnimationModel:
    """Small deterministic animation model used by both the UI and tests."""

    def __init__(self) -> None:
        self.capture_enabled = True
        self.visual_enabled = True
        self._typing_until = 0.0
        self._burst = 0.0
        self._last_key = 0
        self._key_pulses: dict[int, tuple[float, float]] = {}

    def set_capture_enabled(self, enabled: bool) -> None:
        self.capture_enabled = bool(enabled)
        # Do not erase visual state when capture is disabled. This is the
        # important separation that keeps the pet animated in a game.
        if not enabled:
            self._typing_until = 0.0
            self._burst = 0.0

    def note_key(self, vk_code: int, now: float | None = None, *, force_visual: bool = False) -> None:
        if not self.capture_enabled and not force_visual:
            return
        stamp = time.monotonic() if now is None else now
        key = normalize_vk_code(vk_code)
        self._last_key = key
        self._typing_until = max(self._typing_until, stamp + 0.14)
        self._burst = min(1.0, self._burst + 0.72)
        self._key_pulses[key] = (stamp, stamp + KEY_PULSE_SECONDS)

    def key_visuals(self, now: float | None = None) -> dict[int, float]:
        """Return active keycaps and a 0..1 press/release strength."""

        return {key: strength for key, (strength, _progress) in self.key_states(now).items()}

    def key_states(self, now: float | None = None) -> dict[int, tuple[float, float]]:
        """Return active keys as ``key -> (strength, progress)``.

        ``progress`` is intentionally exposed separately from the visual
        strength.  The keyboard glow can spring back quickly while the hand
        still needs a little travel time to reach the key and return to rest.
        """

        stamp = time.monotonic() if now is None else now
        active: dict[int, tuple[float, float]] = {}
        for key, (started, until) in list(self._key_pulses.items()):
            if stamp >= until:
                del self._key_pulses[key]
                continue
            progress = max(0.0, min(1.0, (stamp - started) / KEY_PULSE_SECONDS))
            # A short firm press followed by a softer spring-back.
            strength = 1.0 if progress <= 0.28 else 1.0 - ((progress - 0.28) / 0.72)
            active[key] = (max(0.0, min(1.0, strength)), progress)
        return active

    def frame(self, now: float | None = None) -> dict[str, float | str | bool]:
        stamp = time.monotonic() if now is None else now
        idle_phase = stamp * 2.15
        active = self.capture_enabled and stamp < self._typing_until
        if active:
            # Exponential decay makes repeated keys blend into one smooth
            # motion rather than restarting a jerky animation each time.
            self._burst *= 0.86
        else:
            self._burst *= 0.78
        breath = math.sin(idle_phase) * 1.8
        # Keep the torso response small and slow. Key-specific hand travel is
        # the primary feedback; a fast full-body oscillation looked like the
        # pet was typing by itself even when the keyboard was quiet.
        typing = math.sin(stamp * 12.0) * self._burst * 1.25
        sway = math.sin(stamp * 1.15) * 1.4
        visuals = self.key_visuals(stamp)
        bias = 0.0
        if visuals:
            total = sum(visuals.values()) or 1.0
            bias = sum(key_motion_bias(key) * strength for key, strength in visuals.items()) / total
        return {
            "visual_enabled": self.visual_enabled,
            "capture_enabled": self.capture_enabled,
            "mode": "typing" if active else "idle",
            # These are deliberately rigid whole-body motions. They avoid the
            # old neck-only stretch while making each key press visible in the
            # character's posture as well as on the keyboard overlay.
            "dx": sway + bias * self._burst * 1.6,
            "dy": breath + typing,
            "scale": 1.0 + (0.006 * self._burst),
            "tilt": math.sin(stamp * 8.0) * self._burst * 0.35 + bias * self._burst * 1.0,
            "key_bias": bias,
            "last_key": self._last_key,
        }


def _windows_api() -> tuple[object, object, object]:
    if os.name != "nt":
        raise RuntimeError("desktop_pet.py requires Windows")
    return ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.psapi


class ForegroundClassifier:
    """Reads the foreground window without capturing or injecting input."""

    def __init__(self, game_processes: Iterable[str] = ()) -> None:
        self.game_processes = {
            name.lower().strip()
            for name in game_processes
            if name and name.strip()
        } | DEFAULT_GAME_PROCESSES
        self.user32, self.kernel32, _ = _windows_api()

    def is_game_process(self, process_name: str) -> bool:
        return process_name.lower().strip() in self.game_processes

    def inspect(self) -> ForegroundInfo:
        hwnd = int(self.user32.GetForegroundWindow())
        if not hwnd:
            return ForegroundInfo()
        pid = ctypes.c_ulong(0)
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = self._window_title(hwnd)
        process_name = self._process_name(int(pid.value))
        fullscreen = self._is_fullscreen(hwnd)
        is_game = self.is_game_process(process_name) or fullscreen
        return ForegroundInfo(
            hwnd=hwnd,
            pid=int(pid.value),
            process_name=process_name,
            title=title,
            is_fullscreen=fullscreen,
            is_game=is_game,
        )

    def _window_title(self, hwnd: int) -> str:
        length = int(self.user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _process_name(self, pid: int) -> str:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        self.kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.kernel32.OpenProcess.restype = ctypes.c_void_p
        self.kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        self.kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = ctypes.c_int
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return Path(buffer.value).name.lower()
        finally:
            self.kernel32.CloseHandle(handle)

    def _is_fullscreen(self, hwnd: int) -> bool:
        # Full-screen detection is a safe fallback for games without a known
        # process name. It can also pause capture in a true full-screen video;
        # that is preferable to observing keys in an unknown full-screen app.
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        screen_width = self.user32.GetSystemMetrics(0)
        screen_height = self.user32.GetSystemMetrics(1)
        return width >= screen_width and height >= screen_height and rect.left <= 0 and rect.top <= 0


class KeyboardMonitor:
    """Non-invasive key-down notifier; it never blocks or changes a key."""

    WH_KEYBOARD_LL = 13
    HC_ACTION = 0
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104

    def __init__(self, on_key: Callable[[int], None]) -> None:
        self.on_key = on_key
        self.enabled = False
        self._hook = None
        self._callback = None
        self._user32 = None
        self._kernel32 = None

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if enabled:
            self._install()
        else:
            self._uninstall()

    def _install(self) -> None:
        user32, kernel32, _ = _windows_api()
        self._user32, self._kernel32 = user32, kernel32
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_ulong]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = ctypes.c_int

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.c_ulong),
                ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        def callback(code: int, wparam: int, lparam: int) -> int:
            if code == self.HC_ACTION and wparam in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN):
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                # The callback is intentionally observational. The key is
                # always forwarded to Windows, including in game mode.
                self.on_key(int(data.vkCode))
            return int(user32.CallNextHookEx(self._hook, code, wparam, lparam))

        self._callback = HOOKPROC(callback)
        module = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(self.WH_KEYBOARD_LL, self._callback, module, 0)
        if not self._hook:
            self.enabled = False
            raise OSError("SetWindowsHookExW(WH_KEYBOARD_LL) failed")

    def _uninstall(self) -> None:
        if self._hook and self._user32:
            self._user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._callback = None

    def close(self) -> None:
        self.enabled = False
        self._uninstall()


class DesktopPet:
    def __init__(
        self,
        image_path: Path,
        scale: float,
        game_processes: Iterable[str],
        poll_ms: int,
        diagnostic: bool,
        demo_typing: bool = False,
    ) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", KEY_COLOR)
        self.root.configure(bg=KEY_COLOR)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        source_image = Image.open(image_path).convert("RGBA")
        image = source_image
        width = max(64, int(image.width * scale))
        height = max(64, int(image.height * scale))
        image = self._binary_alpha(image.resize((width, height), Image.Resampling.LANCZOS))
        # The keyboard is deliberately in front of the lower half of the
        # character, like the reference desktop pet.  This gives the hands a
        # believable surface to land on instead of leaving a detached keyboard
        # below the feet.
        self._canvas_width = max(width + 30, 330)
        # Keep the keyboard over the lower body like the reference pet.  This
        # hides the original lower hands while leaving the small moving hand
        # sprites visible on the key row.
        self._keyboard_top = max(156, int(height * 0.59))
        self._canvas_height = max(height + 8, self._keyboard_top + 108)
        self._sprite_frames = self._build_sprite_frames(image)
        self._arm_sources = self._build_arm_sources(source_image)
        self.canvas = tk.Canvas(
            self.root,
            width=self._canvas_width,
            height=self._canvas_height,
            bg=KEY_COLOR,
            highlightthickness=0,
        )
        self.canvas.pack()
        self.image_id = self.canvas.create_image(
            self._canvas_width // 2,
            height // 2,
            image=self._sprite_frames[0],
        )
        self._base_center = (self._canvas_width // 2, height // 2)
        self._keyboard_items: dict[int, tuple[int, int]] = {}
        self._keyboard_centers: dict[int, tuple[float, float]] = {}
        self._keyboard_status_id = 0
        self._build_keyboard()
        # Render the original sleeve + hand pixels as one connected layer.
        # This prevents a floating hand sprite from jumping between keys.
        self._arm_items: dict[str, int] = {}
        self._arm_photos: dict[str, ImageTk.PhotoImage] = {}
        self._arm_photo_cache: dict[tuple[str, int, int, int, int], ImageTk.PhotoImage] = {}
        self._arm_render_keys: dict[str, tuple[str, int, int, int, int]] = {}
        self._arm_shoulder_anchors = {
            "left": (
                self._canvas_width / 2 + (405 - source_image.width / 2) * scale,
                660 * scale,
            ),
            "right": (
                self._canvas_width / 2 + (712 - source_image.width / 2) * scale,
                660 * scale,
            ),
        }
        self._hand_positions: dict[str, tuple[float, float]] = {}
        self._last_hand_update = time.monotonic()
        self._last_arm_update = 0.0
        self._build_arms()
        self._poll_ms = max(50, poll_ms)
        self._diagnostic = diagnostic
        self._demo_typing = demo_typing
        self._demo_index = 0
        # Alternate left/right and upper/lower rows so the visual preview
        # makes the hand travel obvious without sending a real key anywhere.
        self._demo_sequence = [ord(letter) for letter in "ALSKDJFHQPWOUI"]
        self._last_focus_poll = 0.0
        self._foreground = ForegroundInfo()
        self._events: queue.Queue[int] = queue.Queue()
        self.model = AnimationModel()
        self.classifier = ForegroundClassifier(game_processes)
        self.keyboard = KeyboardMonitor(lambda vk: self._events.put(vk))
        self._keyboard_error = ""
        try:
            self.keyboard.set_enabled(True)
        except OSError as exc:
            # Some locked-down desktop sessions reject low-level hooks. The
            # pet must remain visible and animated instead of disappearing;
            # report the missing typing channel and keep the visual channel.
            self._keyboard_error = str(exc)
            if diagnostic:
                print(f"keyboard capture unavailable: {exc}", flush=True)
        self._apply_click_through()
        self._place_bottom_right(self._canvas_width, self._canvas_height)
        if diagnostic:
            print(
                f"pet_window=ready size={self._canvas_width}x{self._canvas_height} transparent={KEY_COLOR}",
                flush=True,
            )
            print(f"demo_typing={'on' if demo_typing else 'off'}", flush=True)
            if demo_typing:
                print("demo_typing=on (synthetic preview only)", flush=True)

    def _build_sprite_frames(self, image: Image.Image) -> dict[int, ImageTk.PhotoImage]:
        """Prepare rigid lean frames so typing moves the whole character."""

        return {
            angle: ImageTk.PhotoImage(
                self._binary_alpha(image.rotate(
                    angle,
                    resample=Image.Resampling.BICUBIC,
                    expand=False,
                    center=(image.width / 2, image.height / 2),
                ))
            )
            for angle in (-4, -2, 0, 2, 4)
        }

    @staticmethod
    def _binary_alpha(image: Image.Image, threshold: int = 150) -> Image.Image:
        """Remove semi-transparent edge pixels before Tk color-key compositing.

        Windows color-key transparency has no reliable notion of a partially
        transparent pixel: anti-aliased edges get blended with the key color
        and become a neon fringe.  A small binary matte is cleaner at pet size
        and keeps the hand/body layers visually consistent.
        """

        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A").point(lambda value: 255 if value >= threshold else 0)
        clean = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
        clean.paste(rgba, mask=alpha)
        return clean

    @staticmethod
    def _build_arm_sources(source_image: Image.Image) -> dict[str, ArmSource]:
        """Extract connected sleeve+hand cutouts from the canonical art.

        The source drawing already contains the correct cuff folds, skin tone,
        and hand shape. Polygon mattes keep the skirt/body out of each cutout;
        the explicit anchors let the whole arm transform from shoulder to the
        active key without ever detaching the hand from its sleeve.
        """

        polygons = {
            "left": [
                (395, 642),
                (470, 668),
                (479, 710),
                (443, 754),
                (401, 782),
                (370, 807),
                (323, 805),
                (300, 785),
                (347, 747),
                (360, 703),
            ],
            "right": [
                (704, 642),
                (766, 652),
                (817, 688),
                (839, 738),
                (862, 777),
                (915, 808),
                (932, 830),
                (899, 850),
                (850, 821),
                (808, 790),
                (765, 750),
                (731, 706),
            ],
        }
        shoulder_points = {"left": (405, 660), "right": (712, 660)}
        fingertip_points = {"left": (323, 795), "right": (905, 820)}
        result: dict[str, ArmSource] = {}
        for side, polygon in polygons.items():
            matte = Image.new("L", source_image.size, 0)
            ImageDraw.Draw(matte).polygon(polygon, fill=255)
            alpha = ImageChops.multiply(source_image.getchannel("A"), matte)
            cutout = source_image.copy()
            cutout.putalpha(alpha)
            bbox = alpha.getbbox()
            if bbox is None:
                continue
            cutout = cutout.crop(bbox)
            result[side] = ArmSource(
                image=cutout,
                shoulder=(
                    shoulder_points[side][0] - bbox[0],
                    shoulder_points[side][1] - bbox[1],
                ),
                fingertip=(
                    fingertip_points[side][0] - bbox[0],
                    fingertip_points[side][1] - bbox[1],
                ),
            )
        return result

    def _build_keyboard(self) -> None:
        """Draw a compact keyboard overlay whose keycaps can be animated."""

        rows = [
            [
                (0x1B, "Esc", 1.0),
                *[(0x30 + ((index + 1) % 10), str((index + 1) % 10), 1.0) for index in range(10)],
                (0xBD, "-", 1.0),
                (0xBB, "=", 1.0),
                (0x08, "⌫", 2.0),
            ],
            [
                (0x09, "Tab", 1.5),
                *[(ord(letter), letter, 1.0) for letter in "QWERTYUIOP"],
                (0xDB, "[", 1.0),
                (0xDD, "]", 1.0),
            ],
            [
                (0x14, "Caps", 1.7),
                *[(ord(letter), letter, 1.0) for letter in "ASDFGHJKL"],
                (0xBA, ";", 1.0),
                (0xDE, "'", 1.0),
                (0x0D, "Enter", 1.8),
            ],
            [
                (0x10, "Shift", 2.0),
                *[(ord(letter), letter, 1.0) for letter in "ZXCVBNM"],
                (0xBC, ",", 1.0),
                (0xBE, ".", 1.0),
                (0xBF, "/", 1.0),
                (0x10, "Shift", 2.0),
            ],
            [
                (0x11, "Ctrl", 1.8),
                (0x12, "Alt", 1.8),
                (0x20, "Space", 5.8),
                (0x25, "←", 1.0),
                (0x26, "↑", 1.0),
                (0x27, "→", 1.0),
                (0x28, "↓", 1.0),
            ],
        ]
        top = self._keyboard_top
        # The keyboard is a horizontal surface in front of the pet: its far
        # edge (number row) is the edge closest to her and its near edge
        # (spacebar row) widens toward the viewer. A shallow trapezoid made
        # the keys read like a vertical panel; the stronger perspective here
        # makes the facing direction unambiguous.
        far_margin = max(42, int(self._canvas_width * 0.14))
        near_margin = 6
        far_y = top - 2
        near_y = self._canvas_height - 4
        self.canvas.create_polygon(
            far_margin,
            far_y,
            self._canvas_width - far_margin,
            far_y,
            self._canvas_width - near_margin,
            near_y,
            near_margin,
            near_y,
            fill=KEYBOARD_BG,
            outline="#e8c66f",
            width=1,
        )
        self.canvas.create_line(
            near_margin + 2,
            near_y - 1,
            self._canvas_width - near_margin - 2,
            near_y - 1,
            fill="#f2cf78",
            width=2,
        )
        self._keyboard_status_id = self.canvas.create_text(
            self._canvas_width // 2,
            top + 6,
            text="键盘反馈 · 等待输入",
            fill="#ffe8a5",
            font=("Segoe UI", 7, "bold"),
        )
        key_width = 17
        gap = 2
        row_height = 17
        for row_index, row in enumerate(rows):
            depth = row_index / max(1, len(rows) - 1)
            total_units = sum(item[2] for item in row)
            row_scale = 0.78 + depth * 0.22
            row_gap = gap + depth * 0.6
            row_height_px = row_height * (0.72 + depth * 0.28)
            row_width = (total_units * key_width + (len(row) - 1) * row_gap) * row_scale
            x = (self._canvas_width - row_width) / 2
            y = top + 15 + row_index * 20
            for code, label, units in row:
                current_width = units * key_width * row_scale
                skew = 2.5 * depth
                rect = self.canvas.create_polygon(
                    x + skew,
                    y,
                    x + current_width + skew,
                    y,
                    x + current_width,
                    y + row_height_px - 3,
                    x,
                    y + row_height_px - 3,
                    fill=KEYBOARD_KEY,
                    outline=KEYBOARD_KEY_OUTLINE,
                    width=1,
                )
                text = self.canvas.create_text(
                    x + current_width / 2 + skew / 2,
                    y + (row_height_px - 3) / 2,
                    text=label,
                    fill="#edf3ff",
                    font=("Segoe UI", 6, "bold"),
                )
                self._keyboard_items.setdefault(normalize_vk_code(code), (rect, text))
                self._keyboard_centers.setdefault(
                    normalize_vk_code(code),
                    (x + current_width / 2 + skew / 2, y + (row_height_px - 3) / 2),
                )
                x += current_width + row_gap * row_scale

    def _build_arms(self) -> None:
        """Create source-art arm layers in the resting pose."""

        fallback = next(
            iter(self._keyboard_centers.values()),
            (self._canvas_width / 2, self._keyboard_top + 48),
        )
        rests = {
            "left": self._keyboard_centers.get(ord("F"), fallback),
            "right": self._keyboard_centers.get(ord("J"), fallback),
        }
        for side, shoulder in self._arm_shoulder_anchors.items():
            position = rests[side]
            self._hand_positions[side] = position
            cache_key, photo = self._arm_photo_for(side, shoulder, position)
            self._arm_photos[side] = photo
            self._arm_render_keys[side] = cache_key
            self._arm_items[side] = self.canvas.create_image(
                0,
                0,
                image=photo,
                anchor="nw",
            )

    def _arm_photo_for(
        self,
        side: str,
        shoulder: tuple[float, float],
        wrist: tuple[float, float],
    ) -> tuple[tuple[str, int, int, int, int], ImageTk.PhotoImage]:
        """Return a cached transformed arm, quantized to six display pixels."""

        cache_key = (
            side,
            round(shoulder[0] / 6.0),
            round(shoulder[1] / 6.0),
            round(wrist[0] / 6.0),
            round(wrist[1] / 6.0),
        )
        photo = self._arm_photo_cache.get(cache_key)
        if photo is None:
            photo = ImageTk.PhotoImage(self._render_arm(side, shoulder, wrist))
            self._arm_photo_cache[cache_key] = photo
            if len(self._arm_photo_cache) > 256:
                self._arm_photo_cache.pop(next(iter(self._arm_photo_cache)))
        return cache_key, photo

    def _render_arm(
        self,
        side: str,
        shoulder: tuple[float, float],
        wrist: tuple[float, float],
    ) -> Image.Image:
        """Transform the complete source arm so its fingertips meet ``wrist``."""

        source = self._arm_sources.get(side)
        if source is None:
            return Image.new("RGBA", (self._canvas_width, self._canvas_height), (0, 0, 0, 0))
        source_vector = (
            source.fingertip[0] - source.shoulder[0],
            source.fingertip[1] - source.shoulder[1],
        )
        target_vector = (wrist[0] - shoulder[0], wrist[1] - shoulder[1])
        source_length = math.hypot(*source_vector)
        target_length = math.hypot(*target_vector)
        if source_length < 1.0 or target_length < 1.0:
            return Image.new("RGBA", (self._canvas_width, self._canvas_height), (0, 0, 0, 0))
        # The shoulder-to-key distance can be longer than the source pose, so
        # do not stop at a 15% stretch: the fingertip must actually reach the
        # target key instead of hovering above it.
        factor = max(0.12, min(1.8, target_length / source_length))
        angle = math.atan2(target_vector[1], target_vector[0]) - math.atan2(
            source_vector[1], source_vector[0]
        )
        cosine = math.cos(angle)
        sine = math.sin(angle)
        inverse_scale = 1.0 / factor
        anchor_x, anchor_y = shoulder
        source_x, source_y = source.shoulder
        affine = (
            cosine * inverse_scale,
            sine * inverse_scale,
            source_x - (cosine * anchor_x + sine * anchor_y) * inverse_scale,
            -sine * inverse_scale,
            cosine * inverse_scale,
            source_y - (-sine * anchor_x + cosine * anchor_y) * inverse_scale,
        )
        transformed = source.image.transform(
            (self._canvas_width, self._canvas_height),
            Image.Transform.AFFINE,
            affine,
            resample=Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
        return self._binary_alpha(transformed, threshold=100)

    def _update_hands(self, now: float) -> None:
        # The keyboard and body continue at the main 60 FPS tick.  Arm layers
        # are expensive source-art transforms, so update them at a stable
        # 45 FPS; the interpolation keeps travel smooth while avoiding a PIL
        # transform on every Tk frame.
        if self._last_arm_update and now - self._last_arm_update < (1.0 / 45.0):
            return
        self._last_arm_update = now
        states = self.model.key_states(now)
        fallback = next(iter(self._keyboard_centers.values()), (self._canvas_width / 2, self._keyboard_top + 48))
        rests = {
            "left": self._keyboard_centers.get(ord("F"), fallback),
            "right": self._keyboard_centers.get(ord("J"), fallback),
        }
        active_key = max(states, key=lambda key: states[key][0]) if states else None
        active_side = None
        if active_key is not None:
            center = self._keyboard_centers.get(active_key, fallback)
            active_side = "left" if center[0] < self._canvas_width / 2 else "right"

        elapsed = max(0.0, min(0.1, now - self._last_hand_update))
        self._last_hand_update = now
        # Exponential interpolation avoids the visible one-frame jump that a
        # fixed linear blend produced when a key changed rows. The hands now
        # travel continuously toward the active key and ease back to F/J.
        blend = 1.0 - math.exp(-max(0.0, elapsed) * 9.0)
        blend = max(0.10, min(0.24, blend if elapsed else 0.16))
        frame = self.model.frame(now)
        for side, item in self._arm_items.items():
            target = self._keyboard_centers.get(active_key, rests[side]) if side == active_side else rests[side]
            current = self._hand_positions[side]
            position = (
                current[0] + (target[0] - current[0]) * blend,
                current[1] + (target[1] - current[1]) * blend,
            )
            self._hand_positions[side] = position
            # Land the fingertips inside the keycap instead of leaving a
            # visible gap above it; the active press then sinks a few more
            # pixels in sync with the key highlight.
            press = states.get(active_key, (0.0, 0.0))[0] * 4.0 if side == active_side else 0.0
            shoulder = self._arm_shoulder_anchors[side]
            shoulder = (
                shoulder[0] + float(frame["dx"]),
                shoulder[1] + float(frame["dy"]),
            )
            wrist = (
                position[0],
                position[1] + 2.5 + press,
            )
            cache_key, photo = self._arm_photo_for(side, shoulder, wrist)
            if cache_key != self._arm_render_keys.get(side):
                self._arm_photos[side] = photo
                self._arm_render_keys[side] = cache_key
                self.canvas.itemconfigure(item, image=photo)

    @staticmethod
    def _blend_color(base: str, active: str, strength: float) -> str:
        amount = max(0.0, min(1.0, float(strength)))
        base_rgb = tuple(int(base[index : index + 2], 16) for index in (1, 3, 5))
        active_rgb = tuple(int(active[index : index + 2], 16) for index in (1, 3, 5))
        rgb = tuple(round(left + (right - left) * amount) for left, right in zip(base_rgb, active_rgb))
        return "#%02x%02x%02x" % rgb

    def _update_keyboard(self, now: float) -> None:
        visuals = self.model.key_visuals(now)
        for code, (rect, text) in self._keyboard_items.items():
            strength = visuals.get(code, 0.0)
            self.canvas.itemconfigure(
                rect,
                fill=self._blend_color(KEYBOARD_KEY, KEYBOARD_ACTIVE, strength),
                outline=KEYBOARD_ACTIVE_OUTLINE if strength > 0.08 else KEYBOARD_KEY_OUTLINE,
                width=2 if strength > 0.45 else 1,
            )
            self.canvas.itemconfigure(text, fill="#1d2638" if strength > 0.45 else "#edf3ff")
        if visuals:
            active_code = max(visuals, key=visuals.get)
            status = f"小手按下 {vk_label(active_code)}"
        else:
            status = "小手待命 · 等待输入"
        self.canvas.itemconfigure(self._keyboard_status_id, text=status)

    def _apply_click_through(self) -> None:
        # Tk owns the layered/color-key setup created by ``-transparentcolor``.
        # Do not add WS_EX_LAYERED here: setting that bit a second time without
        # reapplying the color key can leave the whole Tk surface transparent
        # on Windows (the window exists, but the pet cannot be seen).
        # WS_EX_NOACTIVATE keeps the game focused. WS_EX_TRANSPARENT is a
        # best-effort click-through style; the pet never handles mouse input.
        hwnd = self.root.winfo_id()
        user32, _, _ = _windows_api()
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        user32.GetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        current = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, current | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    def _place_bottom_right(self, width: int, height: int) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, screen_w - width - 28)
        y = max(0, screen_h - height - 48)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _poll_focus(self, now: float) -> None:
        if now - self._last_focus_poll < self._poll_ms / 1000:
            return
        self._last_focus_poll = now
        try:
            info = self.classifier.inspect()
        except Exception as exc:  # keep animation alive if metadata is unavailable
            if self._diagnostic:
                print(f"foreground inspection failed: {exc}", flush=True)
            return
        if info.is_game != self._foreground.is_game or info.process_name != self._foreground.process_name:
            self._foreground = info
            # Keep observing key-down events in games so the pet can place a
            # hand on the corresponding key.  KeyboardMonitor always calls
            # CallNextHookEx and never injects or consumes input, so this does
            # not turn game keys into text or alter the game controls.
            self.model.set_capture_enabled(True)
            try:
                self.keyboard.set_enabled(True)
            except OSError as exc:
                self._keyboard_error = str(exc)
                if self._diagnostic:
                    print(f"keyboard capture unavailable: {exc}", flush=True)
            if self._diagnostic:
                state = "animation=on, key-observe=on, input=untouched" if info.is_game else "animation=on, key-observe=on"
                print(f"foreground={info.process_name or '<unknown>'} {state}", flush=True)

    def _tick(self) -> None:
        now = time.monotonic()
        self._poll_focus(now)
        while True:
            try:
                self.model.note_key(self._events.get_nowait(), now)
            except queue.Empty:
                break
        frame = self.model.frame(now)
        cx, cy = self._base_center
        angle = min(self._sprite_frames, key=lambda candidate: abs(candidate - float(frame["tilt"])))
        self.canvas.itemconfigure(self.image_id, image=self._sprite_frames[angle])
        self.canvas.coords(self.image_id, cx + float(frame["dx"]), cy + float(frame["dy"]))
        self._update_keyboard(now)
        self._update_hands(now)
        self.root.after(16, self._tick)

    def _demo_key(self) -> None:
        if not self._demo_typing:
            return
        # Demo input is local-only visual preview.  It deliberately bypasses
        # the real capture gate so we can validate the hand animation while a
        # game is foregrounded, without synthesising any system key.
        self.model.note_key(self._demo_sequence[self._demo_index], force_visual=True)
        self._demo_index = (self._demo_index + 1) % len(self._demo_sequence)
        self.root.after(170, self._demo_key)

    def run(self) -> None:
        self.root.after(0, self._tick)
        if self._demo_typing:
            self.root.after(350, self._demo_key)
        self.root.mainloop()

    def close(self) -> None:
        self.keyboard.close()
        self.root.destroy()


def _default_image() -> Path:
    return Path(__file__).resolve().parents[2] / "artifacts" / "desktop_pet" / "qpet-v1" / "qpet-v1-binary.png"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent animated Q-pet with game-safe typing capture")
    parser.add_argument("--image", type=Path, default=_default_image())
    parser.add_argument("--scale", type=float, default=0.24)
    parser.add_argument("--poll-ms", type=int, default=120)
    parser.add_argument("--game-process", action="append", default=[])
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument(
        "--demo-typing",
        action="store_true",
        help="preview key-specific body and keycap animation without injecting input",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        model = AnimationModel()
        model.note_key(65, now=1.0)
        assert model.frame(1.05)["mode"] == "typing"
        model.set_capture_enabled(False)
        model.note_key(66, now=1.06)
        assert model.frame(1.10)["capture_enabled"] is False
        print("desktop-pet self-test: ok")
        return 0
    if not args.image.exists():
        parser.error(f"image not found: {args.image}")
    pet = DesktopPet(
        args.image,
        args.scale,
        args.game_process,
        args.poll_ms,
        args.diagnostic,
        args.demo_typing,
    )
    try:
        pet.run()
    except KeyboardInterrupt:
        pet.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
