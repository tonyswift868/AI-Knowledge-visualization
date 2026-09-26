"""Page-owned cancellable work, with no Qt objects touched from worker threads."""
from queue import Empty, Queue
from threading import Event, Thread

from PySide6.QtCore import QObject, QTimer, Signal

from .service import Cancelled
from .storage import ToolError


class PageJobs(QObject):
    busy_changed = Signal(bool)
    failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.disposed = False
        self.busy = False
        self.cancel_event = Event()
        self.queue = Queue()
        self.callback = None
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        # parent destruction also cancels work if host forgets explicit dispose().
        event_holder = self
        parent.destroyed.connect(lambda: event_holder.dispose())

    def run(self, operation, callback):
        if self.disposed or self.busy:
            return False
        self.busy = True
        self.cancel_event = Event()
        self.callback = callback
        self.busy_changed.emit(True)
        queue, event = self.queue, self.cancel_event

        def work():
            try:
                queue.put((True, operation(event)))
            except Cancelled as exc:
                queue.put((False, str(exc)))
            except ToolError as exc:
                queue.put((False, str(exc)))
            except Exception:
                queue.put((False, "操作失败，请检查记录目录或共享服务后重试；输入仍保留。"))

        Thread(target=work, name="mentorflow-tools", daemon=True).start()
        return True

    def _poll(self):
        if self.disposed:
            return
        try:
            success, value = self.queue.get_nowait()
        except Empty:
            return
        callback = self.callback
        self.callback = None
        self.busy = False
        self.busy_changed.emit(False)
        if success:
            callback(value)
        else:
            self.failed.emit(value)

    def cancel(self):
        self.cancel_event.set()

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.cancel_event.set()
        self.callback = None
        try:
            self.timer.stop()
        except RuntimeError:
            pass
