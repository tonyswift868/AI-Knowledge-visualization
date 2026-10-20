"""Teacher free chat for the new window (UI-02).

No system prompt of our own: the only instruction sent is the persona the user
wrote (settings.chat_persona), read fresh on every turn. Nothing here enters the
teaching pipeline. Sessions use CourseStore; old tool JSON is imported once.
"""
from __future__ import annotations

from ..connections.errors import ModelCancelled
from refactor.learning.chat.contracts import utc_now
from refactor.learning.chat.persistence import FreeChatStoreError
from refactor.learning.chat.prompt import FreeChatHistoryWindow
from refactor.learning.chat.session import FreeChatSessionConflictError
from refactor.learning.chat.session import FreeChatSessionNotFoundError
from ..course.store import StoreError
from refactor.learning.chat.errors import ToolError
from ..preferences.storage import AccountJSON, StorageError
from .bridge import BridgeError, CallContext

MAX_MESSAGE = 20000
# Only a cost/context boundary: the oldest turns drop out, nothing is added.
HISTORY = FreeChatHistoryWindow(max_estimated_tokens=60000, chars_per_token=2)


def _summary(session):
    return {"id": session.session_id, "title": session.title, "updated_at": session.updated_at.isoformat(),
            "count": len(session.messages), "archived": bool(getattr(session, "archived", False))}


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
        archived = params.get("archived", False)
        if not isinstance(archived, bool):
            raise BridgeError("PARAM_INVALID", "对话列表状态无效。")
        return {"sessions": [_summary(s) for s in store().list_sessions(archived=archived)]}

    def get(params, ctx):
        return {"session": _full(store().get(_session_id(params)))}

    def create(params, ctx):
        return {"session": _full(store().create(session_id=None, created_at=utc_now()))}

    def delete(params, ctx):
        return {"deleted": store().delete(_session_id(params))}

    def archive(params, ctx):
        session_id = _session_id(params)
        archived = params.get("archived")
        if not isinstance(archived, bool):
            raise BridgeError("PARAM_INVALID", "归档状态无效。")
        return {"session": _full(store().set_archived(session_id, archived))}

    def chat_ui_storage(current):
        return AccountJSON(current.account_id, current.data_root, "chat-ui.json")

    def ui_get(params, ctx):
        current = services()
        try:
            value = chat_ui_storage(current).read({"schema": "chat_ui.v1", "list_collapsed": False})
            if value.get("schema") != "chat_ui.v1" or not isinstance(value.get("list_collapsed"), bool):
                return {"list_collapsed": False, "available": True}
            return {"list_collapsed": value["list_collapsed"], "available": True}
        except StorageError:
            # This is an optional presentation preference; a damaged or
            # temporarily unavailable file must not prevent opening chat.
            return {"list_collapsed": False, "available": False}

    def ui_set(params, ctx):
        collapsed = params.get("list_collapsed")
        if not isinstance(collapsed, bool):
            raise BridgeError("PARAM_INVALID", "对话列表状态无效。")
        current = services()
        try:
            chat_ui_storage(current).write({"schema": "chat_ui.v1", "list_collapsed": collapsed})
            return {"list_collapsed": collapsed, "saved": True}
        except StorageError:
            # Apply the requested state in the current view even if persistence
            # is temporarily unavailable. The frontend can keep it in memory.
            return {"list_collapsed": collapsed, "saved": False}

    def send(params, ctx: CallContext):
        current = services()
        chat = current.chat
        session = chat.get(_session_id(params))
        text = params.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE:
            raise BridgeError("PARAM_INVALID", f"消息不能为空，也不能超过 {MAX_MESSAGE} 字。")
        text = text.strip()
        # Restore only after validating the user's message and before taking
        # the content snapshot for generation. If a remote archive arrives
        # while the model is responding, its metadata stays archived and does
        # not invalidate the generated turn.
        if session.archived:
            chat.set_archived(session.session_id, False)
            session = chat.get(session.session_id)
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
    bridge.register("chat.archive", guarded(archive))
    bridge.register("chat.ui_get", guarded(ui_get))
    bridge.register("chat.ui_set", guarded(ui_set))
    bridge.register("chat.send", guarded(send))
