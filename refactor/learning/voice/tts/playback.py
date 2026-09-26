"""Small PCM16 loudness window indexed by consumed device frames, not wall time."""
from array import array
from collections import deque
import math
import sys


class PlaybackMeter:
    """Retain PCM references until consumed; examine only the last 50 ms.

    No work runs on the synthesis/first-audio path except deque append. RMS is
    computed at 20 Hz, with a noise gate and attack/release smoothing. A stopped
    device clock represents silence even if future PCM is already synthesized.
    """
    def __init__(self, sample_rate: int):
        self.window = max(1, round(sample_rate * .05))
        self.chunks = deque()
        self.previous_frame = 0
        self.level = 0.0

    def append(self, start_frame: int, pcm: bytes):
        self.chunks.append((start_frame, pcm))

    def sample(self, played_frame: int) -> float:
        if played_frame <= self.previous_frame:
            self.level = 0.0
            return self.level
        self.previous_frame = played_frame
        start = max(0, played_frame - self.window)
        while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) // 2 <= start:
            self.chunks.popleft()
        energy, count = 0, 0
        for offset, pcm in self.chunks:
            if offset >= played_frame:
                break
            low, high = max(start, offset), min(played_frame, offset + len(pcm) // 2)
            if high <= low:
                continue
            values = array("h", pcm[(low - offset) * 2:(high - offset) * 2])
            if sys.byteorder != "little":
                values.byteswap()
            energy += sum(value * value for value in values)
            count += len(values)
        rms = math.sqrt(energy / count) / 32768 if count else 0.0
        if rms <= .003:
            self.level = 0.0
        else:
            target = min(1.0, rms * 4)
            alpha = .65 if target > self.level else .30
            self.level += alpha * (target - self.level)
        return self.level
