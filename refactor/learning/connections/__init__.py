from .errors import ModelError, ModelCancelled


def __getattr__(name):
    if name == "ModelClient":
        from .client import ModelClient
        return ModelClient
    raise AttributeError(name)


__all__ = ["ModelClient", "ModelError", "ModelCancelled"]
