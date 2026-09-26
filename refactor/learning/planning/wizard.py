"""QtWebChannel adapter for the account-local planning bundle.

The HTML side owns presentation and form state.  This module exposes only
account-scoped operations through a small JSON bridge; it never starts the old
HTTP application and never passes credentials or model configuration to JS.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QWidget
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView

from ..plan_models import PlanError
from ..observability import complete_with_metadata
from .service import PlanService, normalize_form


class _LocalOnly(QWebEngineUrlRequestInterceptor):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root).resolve()

    def interceptRequest(self, info):
        url = info.requestUrl()
        allowed = url.scheme() == "qrc" and url.path() == "/qtwebchannel/qwebchannel.js"
        if url.isLocalFile():
            try:
                allowed = Path(url.toLocalFile()).resolve().is_relative_to(self.root)
            except (OSError, ValueError):
                allowed = False
        if not allowed:
            info.block(True)


class _PlanningPage(QWebEnginePage):
    def __init__(self, profile, parent, entry):
        super().__init__(profile, parent)
        self.entry = Path(entry).resolve()

    def acceptNavigationRequest(self, url, kind, is_main_frame):
        if not url.isLocalFile():
            return False
        try:
            return Path(url.toLocalFile()).resolve() == self.entry
        except (OSError, ValueError):
            return False


class PlanningBridge(QObject):
    """Asynchronous request/result channel with per-request stale fencing."""

    result = Signal(str)
    plan_saved = Signal(object)

    def __init__(self, page, service: PlanService, bundle_root=None):
        super().__init__(page)
        self.page = page
        self.service = service
        self._disposed = False
        self._generation = 0
        self._future = None
        self._future_request = None
        self._draft = None
        self._staged_form = None
        self._cancel = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-planning-bridge")
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)

    @property
    def busy(self):
        return self._future is not None

    def _emit(self, request_id, action, *, ok, data=None, code="", message=""):
        payload = {"id": request_id, "action": action, "ok": bool(ok)}
        if ok:
            payload["data"] = data if data is not None else {}
            payload["error"] = {"code": "", "message": ""}
        else:
            payload["data"] = {}
            payload["error"] = {"code": code or "PLANNING_ERROR", "message": message or "计划操作失败。"}
        try:
            self.result.emit(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        except RuntimeError:
            pass

    def _error(self, request_id, action, code, message):
        self._emit(request_id, action, ok=False, code=code, message=message)

    @staticmethod
    def _request(value):
        try:
            request = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError, json.JSONDecodeError):
            raise PlanError("REQUEST_INVALID", "计划请求不是有效 JSON。") from None
        if not isinstance(request, dict) or not isinstance(request.get("id"), str) or not request["id"]:
            raise PlanError("REQUEST_INVALID", "计划请求缺少有效 id。")
        action = request.get("action")
        if not isinstance(action, str) or action not in {"get_state", "stage_draft", "save_draft", "save_preset", "import_materials",
                                                          "import_text", "assist", "search_sources", "fetch_sources", "analyze", "analyze_intake", "review_draft",
                                                          "lock_stage", "unlock_stage", "adjust_stage", "replan_draft", "confirm_draft",
                                                          "generate", "save", "cancel"}:
            raise PlanError("REQUEST_INVALID", "计划请求 action 无效。")
        payload = request.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise PlanError("REQUEST_INVALID", "计划请求 payload 不是对象。")
        return request["id"], action, payload

    @Slot(str)
    def request(self, raw):
        try:
            request_id, action, payload = self._request(raw)
        except PlanError as exc:
            self._error("", "request", exc.code, str(exc))
            return
        if self._disposed:
            self._error(request_id, action, "CANCELLED", "计划页面已关闭。")
            return
        if action == "cancel":
            self.cancel(request_id)
            return
        if action == "stage_draft":
            try:
                from .service import normalize_form
                form = normalize_form(payload.get("form"))
            except PlanError as exc:
                self._error(request_id, action, exc.code, str(exc))
                return
            self._staged_form = form
            self._draft = None
            self._emit(request_id, action, ok=True, data={"form": form, "staged": True})
            return
        if self.busy:
            self._error(request_id, action, "BUSY", "计划操作正在进行，请等待或取消当前操作。")
            return
        if action == "import_materials":
            self._choose_materials(request_id)
            return
        if action in {"generate", "save_draft", "review_draft", "replan_draft", "confirm_draft"}:
            # A changed form or a new generation is a new snapshot; an earlier
            # preview can never remain saveable if this attempt later fails.
            self._draft = None
        handlers = {
            "get_state": lambda cancel: {**self.service.ui_state(), "research_sources": self.service.research_sources()},
            "save_draft": lambda cancel: self.service.save_draft(payload.get("form")),
            "save_preset": lambda cancel: self.service.save_preset(payload.get("name", ""), payload.get("form")),
            "import_text": lambda cancel: self.service.import_text(payload.get("text", ""), payload.get("title", "粘贴资料")),
            "assist": lambda cancel: self._assist(payload, cancel),
            "search_sources": lambda cancel: self._search_sources(payload, cancel),
            "fetch_sources": lambda cancel: self.service.research_fetch_sources(payload.get("candidates", []), cancel=cancel),
            "analyze": lambda cancel: self.service.analyze(payload.get("form")),
            "analyze_intake": lambda cancel: self.service.analyze_intake(payload.get("form")),
            "review_draft": lambda cancel: self.service.review_draft(payload.get("form")),
            "lock_stage": lambda cancel: self.service.lock_stage(payload.get("stage_id")),
            "unlock_stage": lambda cancel: self.service.unlock_stage(payload.get("stage_id")),
            "adjust_stage": lambda cancel: self.service.adjust_stage(payload.get("stage_id"), payload.get("summary")),
            "replan_draft": lambda cancel: self.service.replan_draft(payload.get("form"), payload.get("reason", "")),
            "confirm_draft": lambda cancel: self.service.confirm_draft(
                plan_id=payload.get("plan_id"), expected_revision=payload.get("expected_revision")),
            "generate": lambda cancel: self._generate(payload, cancel),
            "save": lambda cancel: self._save(cancel),
        }
        self._start(request_id, action, handlers[action])

    def _assist(self, payload, cancel):
        """Have the configured model reflect the learner's request.

        Searching remains a separate, explicit action so the UI can show and
        let the learner choose the returned sources.  The model receives only
        the conversation text and returns a short, actionable reply plus a
        suggested search query; it never receives credentials or hidden page
        state.
        """
        message = payload.get("message", "")
        if not isinstance(message, str) or not message.strip() or len(message) > 8_000:
            raise PlanError("PLANNING_MESSAGE_INVALID", "需求消息不能为空且不能超过 8000 个字符。")
        history = payload.get("history", [])
        if not isinstance(history, list) or len(history) > 20:
            raise PlanError("PLANNING_MESSAGE_INVALID", "需求对话记录无效。")
        provider = self.service.context.get("provider_session_provider")
        provider_session_id = provider() if callable(provider) else self.service.context.get("provider_session_id")
        if provider_session_id:
            safe_history = []
            for item in history:
                if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                    raise PlanError("PLANNING_MESSAGE_INVALID", "需求对话记录无效。")
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    safe_history.append({"role": item["role"], "content": text.strip()[:4_000]})
            result = self.service.server_planning_assistant(
                message=message.strip(), conversation=safe_history[-8:], cancel=cancel,
            )
            reply = result["reply"]
            updates = self._assist_updates({"goal_statement": result["goal_summary"]}) if result["goal_summary"] else {}
            if result["missing_questions"] and not any(question in reply for question in result["missing_questions"]):
                reply += "\n\n还需要澄清：" + "；".join(result["missing_questions"][:3])
            queries = result.get("queries", [])
            query = queries[0]["query"] if queries else ""
            return {"reply": reply, "query": query, "queries": queries, "updates": updates}
        messages = [dict(role="system", content=(
            "你是 MentorFlow 的学习规划助手。像真人规划伙伴一样自由对话，不要固定开场白、问卷或编号清单。"
            "先根据完整对话澄清学习目标和希望得到的成果；目标明确后建议检索短语，让学习者先选择资料。"
            "当前水平、开始/截止日期、每周学习日、每天可用分钟和单次时长可在选资料后继续确认。"
            "尚不明确时只追问一个最关键的问题。"
            "信息足够时用自然语言复述理解，并邀请学习者核对。只把学习者明确说出的内容放入提案，"
            "不能将系统默认值当作用户偏好，也不能推断具体日期、时长或水平。"
            "回复末尾输出机器元数据：PLAN_FIELDS: 后跟 JSON 对象；仅当学习目标已明确时，"
            "可输出 SEARCH_QUERIES: 后跟最多 5 个对象组成的 JSON 数组，每项包含 query 和 reason。"
            "兼容时也可输出 SEARCH_QUERY: 后跟一条检索短语。检索短语应覆盖不同资料类型或角度，不能编造来源。"
            "PLAN_FIELDS 只允许 goal_statement、expected_output、current_level、motivation、success_criteria、"
            "start_date、deadline、weekdays、minutes_per_day、preferred_session_minutes、constraints。"
            "日期使用 YYYY-MM-DD，weekdays 使用 0 到 6 的整数数组，current_level 只能为 beginner、intermediate 或 advanced；"
            "每轮都从完整对话中提取所有明确字段，不要只返回本轮新增字段；没有明确值就省略字段。"
            "普通回复保持自然、简短，机器元数据不写进回复正文。"
            "不要声称已经搜索或读取网页，不要输出凭据、链接或虚构资料。"))]
        for item in history:
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                messages.append({"role": item["role"], "content": text.strip()[:8_000]})
        messages.append({"role": "user", "content": message.strip()})
        model = self.service.context.get("model_client")
        if model is None:
            raise PlanError("MODEL_UNAVAILABLE", "模型尚未连接；可以先直接填写需求或选择本机资料。")
        try:
            output = complete_with_metadata(model, messages, cancel=cancel,
                                            metadata={"role": "PLAN_ASSISTANT"})
        except Exception as exc:
            self.service._check_cancel(cancel)
            raise PlanError("MODEL_REQUEST_FAILED", "模型暂时无法回应，请检查模型连接后重试。") from exc
        self.service._check_cancel(cancel)
        reply = output.strip() if isinstance(output, str) else str(output)
        if not reply:
            raise PlanError("MODEL_RESPONSE_INVALID", "模型没有返回可用的需求回应。")
        query = ""
        queries = []
        updates = {}
        lines = reply.splitlines()
        kept = []
        for line in lines:
            if line.strip().upper().startswith("SEARCH_QUERY:"):
                query = line.split(":", 1)[1].strip()[:500]
                if query:
                    queries.append({"query": query, "reason": "模型根据已确认的学习目标建议的检索词"})
            elif line.strip().upper().startswith("SEARCH_QUERIES:"):
                try:
                    proposed_queries = json.loads(line.split(":", 1)[1].strip())
                except (TypeError, ValueError):
                    proposed_queries = []
                if isinstance(proposed_queries, list):
                    for item in proposed_queries[:5]:
                        if not isinstance(item, dict):
                            continue
                        candidate = item.get("query")
                        reason = item.get("reason", "")
                        if not isinstance(candidate, str) or not candidate.strip():
                            continue
                        candidate = candidate.strip()[:500]
                        reason = reason.strip()[:300] if isinstance(reason, str) else ""
                        if candidate.casefold() not in {entry["query"].casefold() for entry in queries}:
                            queries.append({"query": candidate, "reason": reason})
            elif line.strip().upper().startswith("PLAN_FIELDS:"):
                try:
                    proposed = json.loads(line.split(":", 1)[1].strip())
                except (TypeError, ValueError):
                    proposed = {}
                if isinstance(proposed, dict):
                    updates = self._assist_updates(proposed)
            else:
                kept.append(line)
        query = queries[0]["query"] if queries else query
        intake_form = dict(self._staged_form or {})
        intake_form.update(updates)
        intake = self.service.analyze_intake(intake_form)
        reply_text = "\n".join(kept).strip()[:6_000] or "我已理解这段需求。"
        if intake["next_question"] and intake["next_question"] not in reply_text:
            reply_text += "\n\n我还需要确认一件事：" + intake["next_question"]
        return {"reply": reply_text, "query": query, "queries": queries[:5], "updates": updates,
                "missing_questions": intake["questions"], "intake": intake}

    @staticmethod
    def _assist_updates(proposed):
        """Keep model-proposed conversation slots typed, bounded, and reviewable."""
        from .service import normalize_proposed_fields
        return normalize_proposed_fields(proposed)

    def _choose_materials(self, request_id):
        paths, _ = QFileDialog.getOpenFileNames(self.page, "导入学习资料", "", "文本资料 (*.txt *.md *.csv *.json)")
        if not paths:
            self._emit(request_id, "import_materials", ok=True, data=self.service.ui_state())
            return
        self._start(request_id, "import_materials", lambda cancel: self.service.import_files(paths))

    def _generate(self, payload, cancel):
        form = normalize_form(payload.get("form"))
        mode = payload.get("mode", "model")
        selection = payload.get("selection", "keep_current")
        if payload.get("generation_confirmed") is True:
            # The checkbox is only user intent.  Persist the server-side
            # confirmation event before invoking the expensive workers; the
            # service performs the revision/CAS check and is idempotent when
            # DraftReview already confirmed this snapshot.
            self.service.confirm_draft(
                plan_id=payload.get("plan_id"),
                expected_revision=payload.get("expected_revision"),
            )
        plan = self.service.generate_from_form(
            form, mode=mode, selection=selection,
            confirmed=payload.get("generation_confirmed") is True,
            cancel=cancel,
        )
        return {"plan": self.service.plan_payload(plan), "_draft": plan}

    def _search_sources(self, payload, cancel):
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            form = payload.get("form") or self._staged_form or {}
            query = " ".join(str(form.get(key) or "").strip() for key in ("title", "description", "goal_statement")).strip()
        return {"query": query, "candidates": self.service.research_search(query, cancel=cancel,
                                                                             limit=payload.get("limit", 8))}

    def _save(self, cancel):
        if self._draft is None:
            raise PlanError("DRAFT_UNKNOWN", "当前没有可保存的计划草稿，请先生成计划。")
        self.service.require_current()
        plan = self.service.save_plan(self._draft)
        return {"plan": self.service.plan_payload(plan), "_saved": plan}

    def _start(self, request_id, action, operation):
        self._cancel = Event()
        generation = self._generation
        self._future_request = (generation, request_id, action)
        self._future = self._executor.submit(operation, self._cancel)
        self._timer.start()

    def _poll(self):
        if self._future is None or not self._future.done():
            return
        future, request = self._future, self._future_request
        self._future, self._future_request = None, None
        self._timer.stop()
        generation, request_id, action = request
        if self._disposed or generation != self._generation:
            return
        try:
            data = future.result()
            self.service.require_current()
            if action == "generate":
                self._draft = data.pop("_draft")
            elif action == "save":
                self._draft = None
                self.plan_saved.emit(data.pop("_saved"))
            elif action == "save_draft":
                saved_form = data.get("form")
                if self._staged_form == saved_form:
                    self._staged_form = None
                self._draft = None
            self._emit(request_id, action, ok=True, data=data)
        except Exception as exc:
            if isinstance(exc, PlanError):
                self._error(request_id, action, exc.code, str(exc))
            else:
                self._error(request_id, action, "PLANNING_ERROR", "计划操作失败；未报告成功。")

    @Slot(str)
    def cancel(self, request_id=""):
        if self._disposed:
            self._error(request_id, "cancel", "CANCELLED", "计划页面已关闭。")
            return
        if self._future is None:
            self._draft = None
            self._emit(request_id, "cancel", ok=True, data={"cancelled": False})
            return
        # A completed future may be waiting for the timer tick.  Deliver its
        # real result before answering cancel; a completed write is never
        # truthfully reported as cancelled.
        if self._future.done():
            self._poll()
            if self._future is None:
                self._draft = None
                self._emit(request_id, "cancel", ok=True, data={"cancelled": False})
                return
        old_request = self._future_request
        old_action = old_request[2] if old_request is not None else ""
        if old_action not in {"assist", "generate", "analyze", "search_sources", "fetch_sources"}:
            self._emit(request_id, "cancel", ok=True,
                      data={"cancelled": False, "in_progress": True, "action": old_action})
            return
        self._generation += 1
        self._cancel.set()
        self._future.cancel()
        self._future, self._future_request = None, None
        self._timer.stop()
        self._draft = None
        if old_request is not None:
            _, old_id, old_action = old_request
            self._error(old_id, old_action, "CANCELLED", "计划操作已取消；未保存草稿。")
        self._emit(request_id, "cancel", ok=True, data={"cancelled": True})

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        # A queued state-writing operation may have captured an older form.
        # Let it finish before publishing the latest staged form, otherwise a
        # worker that acquires PlanService's lock after this flush could put
        # the old snapshot back on disk.
        pending = self._future
        pending_request = self._future_request
        if pending is not None and pending_request is not None and pending_request[2] in {"save_draft", "save_preset"}:
            # A queued write behind a cancelled/slow operation must not make
            # disposal wait on that unrelated work.  Only an already-running
            # local write is allowed to finish before the latest flush.
            if not pending.cancel():
                try:
                    pending.result()
                except Exception:
                    pass
            self._future, self._future_request = None, None
        staged = self._staged_form
        self._staged_form = None
        if staged is not None:
            try:
                # Flush once synchronously before the owning page lets the
                # shared service close.  Session fencing inside save_draft
                # prevents a changed account from receiving this state.
                self.service.save_draft(staged)
            except Exception:
                # Disposal cannot claim persistence; the next page will show
                # the last durable draft and the caller receives no success.
                pass
        self._generation += 1
        self._cancel.set()
        self._timer.stop()
        if self._future is not None:
            self._future.cancel()
        self._future, self._future_request = None, None
        self._executor.shutdown(wait=False, cancel_futures=True)


class PlanningWebView(QWidget):
    """Offline web view used by PlanningPage; kept separate for bridge tests."""

    def __init__(self, page, service, bundle_root, parent=None):
        from PySide6.QtWidgets import QVBoxLayout
        super().__init__(parent)
        self._disposed = False
        self.view = QWebEngineView(self)
        # Chromium creates its native child window asynchronously.  The shell
        # constructs this page while its top-level window is still hidden, so
        # warm the child early and keep the page itself hidden in the stack.
        # Delaying child creation until the first click can make Windows
        # briefly hide/show or resize the visible shell.
        self._initial_surface_revealed = False
        self.view.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        # Keep even Qt's fallback paths inside the existing D-drive runtime.
        profile_root = Path(service.context["data_root"]) / ".web-cache" / "planning"
        self.profile.setPersistentStoragePath(str(profile_root / "storage"))
        self.profile.setCachePath(str(profile_root / "cache"))
        root = Path(bundle_root).resolve()
        entry = root / "index.html"
        self.interceptor = _LocalOnly(root, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.page = _PlanningPage(self.profile, self.view, entry)
        self.view.setPage(self.page)
        self.page.loadFinished.connect(self._reveal_initial_surface)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.channel = QWebChannel(self.page)
        self.bridge = PlanningBridge(self, service, root)
        self.channel.registerObject("planning", self.bridge)
        self.page.setWebChannel(self.channel)
        if entry.is_file():
            self.view.setUrl(QUrl.fromLocalFile(str(entry)))
            # Prepare the Chromium child HWND while the owning LearningWindow
            # is still hidden during account binding.  Waiting for
            # ``loadFinished`` here meant the first visible navigation could
            # still be the moment at which Qt created/showed the native child;
            # on Windows that can make the top-level shell appear to reopen.
            # The page itself remains hidden in the stacked widget until the
            # user navigates to it, so this only creates an invisible warm
            # surface.  The load callback remains as a compatibility fence
            # for callers that construct the view outside the shell.
            self._reveal_initial_surface()
        else:
            QTimer.singleShot(0, self._reveal_initial_surface)

    def _reveal_initial_surface(self, *_):
        if self._disposed or self._initial_surface_revealed:
            return
        self._initial_surface_revealed = True
        self.view.show()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        # A late loadFinished callback must not make a closing page visible
        # again while Qt is tearing down the native Chromium child surface.
        self.view.hide()
        self.bridge.dispose()
        self.view.stop()
