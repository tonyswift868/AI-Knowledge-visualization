from pathlib import Path

from PIL import Image


root = Path(__file__).resolve().parents[1]
source = root / "frontend" / "public" / "teacher2d" / "tsubaki-live2d" / "icon.png"
target = root / "release" / "native" / "MentorFlow.ico"
target.parent.mkdir(parents=True, exist_ok=True)
with Image.open(source) as image:
    image.convert("RGBA").save(
        target,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
print(target)
