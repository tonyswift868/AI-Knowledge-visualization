"""Runtime compatibility budget, independent of machine LongPathsEnabled.

240 UTF-16 code units leaves a margin below legacy Win32 MAX_PATH. The .u
attempt name is 12 random hex digits; full signed identity stays in metadata.
"""
from pathlib import PureWindowsPath

RUNTIME_PATH_LIMIT = 240
ATTEMPT_CHARS = 12
MESSAGE = '安装位置路径过长，请选择更靠近磁盘根目录的位置。'


def units(value):
    return len(str(value).encode('utf-16-le')) // 2


def budget(install_root, paths):
    root = PureWindowsPath(str(install_root).removeprefix('\\\\?\\'))
    if not root.is_absolute() or len(root.drive) != 2 or '..' in root.parts:
        raise ValueError('请选择本地程序文件夹。')
    longest = max((units(p) for p in paths), default=0)
    suffix = units('\\.u\\' + 'a'*ATTEMPT_CHARS + '\\App\\')
    maximum = RUNTIME_PATH_LIMIT - suffix - longest
    result = dict(longest_relative_path_length=longest,
        max_supported_install_root_length=maximum,
        current_app_absolute_max_path=units(root)+len('\\App\\')+longest,
        staging_app_absolute_max_path=units(root)+suffix+longest)
    if units(root) > maximum:
        raise ValueError(MESSAGE)
    return result


def runtime_root(app, paths):
    path = str(app).removeprefix('\\\\?\\').rstrip('\\/')
    longest = max((units(p) for p in paths), default=0)
    if units(path)+1+longest > RUNTIME_PATH_LIMIT:
        raise ValueError(MESSAGE)
    return units(path)+1+longest
