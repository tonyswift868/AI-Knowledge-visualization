"""Stable errors shared by the offline voice components."""


class AsrError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class DownloadCancelled(AsrError):
    def __init__(self):
        super().__init__("ASR_DOWNLOAD_CANCELLED", "语音模型下载已取消。")
