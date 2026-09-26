class ModelError(RuntimeError):
    """Safe public error: never retains request bodies, URLs or credentials."""
    def __init__(self, code: str, message: str, status: int | None = None):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class ModelCancelled(ModelError):
    def __init__(self):
        super().__init__("MODEL_CANCELLED", "模型请求已取消。")
