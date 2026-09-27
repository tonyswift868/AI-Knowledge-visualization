"""Teacher free chat for the new window (UI-02).

No system prompt of our own: the only instruction sent is the persona the user
wrote (settings.chat_persona), read fresh on every turn. Nothing here enters the
teaching pipeline. Sessions use CourseStore; old tool JSON is imported once.
"""
from __future__ import annotations

from ..connections.errors import ModelCancelled
from ..tools.free_chat.contracts import utc_now
from ..tools.free_chat.persistence import FreeChatStoreError
from ..tools.free_chat.prompt import FreeChatHistoryWindow
from ..tools.free_chat.session import FreeChatSessionConflictError, FreeChatSessionNotFoundError
from ..course.store import StoreError
from ..tools.storage import ToolError
from .bridge import BridgeError, CallContext

MAX_MESSAGE = 20000
# Only a cost/context boundary: the oldest turns drop out, nothing is added.
HISTORY = FreeChatHistoryWindow(max_estimated_tokens=60000, chars_per_token=2)


def _summary(session):
    return {"id": session.session_id, "title": session.title, "updated_at": session.updated_at.isoformat(),
            "count": len(session.messages)}


def _full(session):
    return dict(_summary(session), messages=[
        {"role": m.role, "content": m.content, "at": m.timestamp.isoformat(), "seq": m.sequence}
        for m in session.messages])


def _session_id(params):
    value = params.get("session_id")
    if not isinstance(value, str) or not value or len(value) > 128:
        raise BridgeError("PARAM_INVALID", "对话编号无效。")
    return value


def register(bridge, services):
    def store():
        return services().chat

    def guarded(fn):
        def run(params, ctx: CallContext):
            try:
                if ctx.cancel.is_set(): raise ModelCancelled()
                return fn(params, ctx)
            except ModelCancelled:
                raise BridgeError("CANCELLED", "已停止，这一轮没有保存。") from None
            except FreeChatSessionNotFoundError:
                raise BridgeError("CHAT_NOT_FOUND", "这段对话已不存在，请刷新列表。") from None
            except FreeChatSessionConflictError:
                raise BridgeError("CHAT_CONFLICT", "这段对话刚在别处更新过，请重新打开后再发。") from None
            except (FreeChatStoreError, ToolError, StoreError) as exc:
                raise BridgeError("CHAT_STORAGE", str(exc) or "对话记录读写失败。") from None
            except ValueError:
                raise BridgeError("PARAM_INVALID", "输入内容无效，请检查后重试。") from None
        return run

    def list_sessions(params, ctx):
        return {"sessions": [_summary(s) for s in store().list_sessions()]}

    def get(params, ctx):
        return {"session": _full(store().get(_session_id(params)))}

    def create(params, ctx):
        return {"session": _full(store().create(session_id=None, created_at=utc_now()))}

    def delete(params, ctx):
        return {"deleted": store().delete(_session_id(params))}

    def send(params, ctx: CallContext):
        current = services()
        chat = current.chat
        session = chat.get(_session_id(params))
        text = params.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE:
            raise BridgeError("PARAM_INVALID", f"消息不能为空，也不能超过 {MAX_MESSAGE} 字。")
        text = text.strip()
        persona = current.settings.load()["chat_persona"].strip()
        picked = HISTORY.select(session.messages, required_texts=(persona, text))
        messages = [{"role": m.role, "content": m.content} for m in picked.messages]
        messages.append({"role": "user", "content": text})
        try:
            result = current.gateway.complete("free_chat", messages, system=persona or None,
                                                 on_text=ctx.stream, cancel=ctx.cancel)
        except ModelCancelled:
            raise BridgeError("CANCELLED", "已停止，这一轮没有保存。") from None
        if ctx.cancel.is_set():
            raise BridgeError("CANCELLED", "已停止，这一轮没有保存。")
        updated = chat.commit_turn(session.session_id, expected_version=session.version, user_content=text,
                                   assistant_content=result.text, committed_at=utc_now(), cancel=ctx.cancel)
        return {"session": _full(updated)}

    bridge.register("chat.list", guarded(list_sessions))
    bridge.register("chat.get", guarded(get))
    bridge.register("chat.create", guarded(create))
    bridge.register("chat.delete", guarded(delete))
    bridge.register("chat.send", guarded(send))
