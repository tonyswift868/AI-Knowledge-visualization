"""Runtime contracts extracted from refactor/learning/tools/service.py."""
from __future__ import annotations

import json

from refactor.learning.chat.persistence import AtomicJsonFreeChatStore
from refactor.learning.chat.persistence import MAX_SESSION_FILE_BYTES

from refactor.learning.chat.errors import ToolError

class ScopedChatStore(AtomicJsonFreeChatStore):
    """Keep extracted serialization while enforcing the injected account boundary."""
    def _path(self, session_id):
        path = super()._path(session_id)
        if path.is_symlink():
            raise ToolError("会话记录不能链接到其他账号。")
        return path

    def _atomic_write(self, session):
        if len(json.dumps(self._encode(session), ensure_ascii=False).encode("utf-8")) > MAX_SESSION_FILE_BYTES:
            raise ToolError("对话已达到保存容量，请新建对话；现有记录已保留。")
        super()._atomic_write(session)
