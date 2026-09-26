from .errors import ModelError, ModelCancelled


def __getattr__(name):
    if name == "ModelClient":
        from .client import ModelClient
        return ModelClient
    raise AttributeError(name)


def create_page(context, parent=None):
    from .page import ConnectionsPage
    return ConnectionsPage(context, parent)


__all__ = ["ModelClient", "ModelError", "ModelCancelled", "create_page"]
