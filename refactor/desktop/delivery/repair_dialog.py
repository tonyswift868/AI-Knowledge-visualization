"""Runtime contracts extracted from scripts/windows_installed_launch.py."""
import ctypes

def show_repair(message):
    # Internal exceptions are diagnostics, not product error copy. This fallback
    # is reserved for when even the independent repair runtime is unsafe.
    ctypes.windll.user32.MessageBoxW(None, "MentorFlow 暂时无法安全启动。请使用可信的安装包修复程序；原有学习资料不会被删除。", "MentorFlow — 需要修复安装", 0x10)
