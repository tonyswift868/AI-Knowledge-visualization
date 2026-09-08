"""User-facing installation location contract; never creates parent folders."""
from pathlib import Path
import os


def existing_parent(target, *, require_product_name=True):
    raw=str(target)
    root=Path(raw)
    if not root.is_absolute() or raw.startswith('\\\\') or '..' in root.parts:
        raise ValueError('请选择这台电脑上已有的本地文件夹。')
    if not Path(root.anchor).is_dir():
        raise ValueError('找不到所选磁盘，请选择这台电脑上已有的位置。')
    if not root.parent.is_dir():
        raise ValueError('所选安装位置不存在。请选择一个已经存在的文件夹。')
    if require_product_name and root.name.casefold()!='mentorflow':
        raise ValueError('请选择现有父文件夹，并将程序安装到其中的 MentorFlow 文件夹。')
    return root


def size_text(value):
    unit=1024**3 if value>=1024**3 else 1024**2
    return f'{value/unit:.1f} '+('GB' if unit==1024**3 else 'MB')


def low_space_message(required,free):
    return f'磁盘空间不足。MentorFlow 需要约 {size_text(required)}，当前可用约 {size_text(free)}。请选择其他安装位置。'
