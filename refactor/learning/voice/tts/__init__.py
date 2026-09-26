"""Resident local teacher speech. Importing this package starts no process.

Construct TtsService(data_root=...), call warm_up() after application startup,
then speak(text)/stop() on the Qt thread. close() also runs on application exit.
"""

__all__ = ["TtsConfig", "TtsRuntime", "TtsService"]


def __getattr__(name):
    if name == "TtsConfig":
        from .config import TtsConfig
        return TtsConfig
    if name == "TtsRuntime":
        from .runtime import TtsRuntime
        return TtsRuntime
    if name == "TtsService":
        from .service import TtsService
        return TtsService
    raise AttributeError(name)
