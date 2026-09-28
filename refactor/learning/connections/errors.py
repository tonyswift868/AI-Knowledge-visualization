class ModelError(RuntimeError):
    """Safe public error: never retains request bodies, URLs or credentials.

    Provider errors expose usage and generated_text_length (Unicode characters,
    excluding reasoning/tools). A reported output limit is never a successful
    partial answer; callers should split the request, not blindly retry it.
    """
    def __init__(self, code: str, message: str, status: int | None = None):
        self.code, self.message, self.status = code, message, status
        self.usage, self.response_received = None, False
        self.generated_text_length, self.finish_reason, self.model = 0, '', None
        super().__init__(message)


class ModelCancelled(ModelError):
    def __init__(self):
        super().__init__("MODEL_CANCELLED", "模型请求已取消。")


# Provider adapters use these without changing legacy transport error mapping.
MODEL_INSUFFICIENT_BALANCE = "MODEL_INSUFFICIENT_BALANCE"
MODEL_SEARCH_UNSUPPORTED = "MODEL_SEARCH_UNSUPPORTED"
MODEL_OUTPUT_TRUNCATED = "MODEL_OUTPUT_TRUNCATED"
INSUFFICIENT_BALANCE_MESSAGE = "模型账户余额不足"
SEARCH_UNSUPPORTED_MESSAGE = "该模型厂商不支持自带联网搜索。"
