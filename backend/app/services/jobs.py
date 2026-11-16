"""Background job queue for processing tasks."""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor

from ..config import settings
from ..db import session_scope
from ..models import Task, TaskEvent, utcnow
from ..pipeline.pipeline import recover_orphans, run_task


class JobManager:
    def __init__(self, max_workers: int = 1) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, max_workers), thread_name_prefix="pipeline"
        )
        self._lock = threading.Lock()
        self._futures: dict[str, Future] = {}
        self._pending: set[str] = set()

    def start(self) -> None:
        recover_orphans()
        with session_scope() as db:
            queued = db.query(Task).filter(Task.status == "queued").all()
            ids = [task.id for task in queued]
        for task_id in ids:
            self.submit(task_id, already_queued=True)

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            future = self._futures.get(task_id)
            return bool(future and not future.done())

    def submit(self, task_id: str, *, already_queued: bool = False) -> bool:
        with self._lock:
            future = self._futures.get(task_id)
            if future and not future.done():
                # Resume was requested while the paused job was still unwinding.
                self._pending.add(task_id)
                return True
            if not already_queued:
                with session_scope() as db:
                    task = db.get(Task, task_id)
                    if task is None:
                        return False
                    task.status = "queued"
                    task.stage = "queued"
                    task.progress = 0
                    task.message = "等待处理"
                    task.error = None
                    task.cancel_requested = False
                    db.add(TaskEvent(task_id=task.id, stage="queued", message="任务已加入队列"))
            self._futures[task_id] = self._pool.submit(self._run, task_id)
            return True

    def _run(self, task_id: str) -> None:
        try:
            run_task(task_id)
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            with session_scope() as db:
                task = db.get(Task, task_id)
                if task and task.status not in {"completed", "canceled"}:
                    task.status = "failed"
                    task.error = f"未捕获的异常：{exc}"[:4000]
                    task.message = "处理失败"
                    task.finished_at = utcnow()
                    db.add(
                        TaskEvent(
                            task_id=task_id,
                            stage=task.stage,
                            level="error",
                            message=f"未捕获的异常：{exc}",
                        )
                    )
        finally:
            with self._lock:
                self._futures.pop(task_id, None)
                pending = task_id in self._pending
                self._pending.discard(task_id)
            if pending:
                with session_scope() as db:
                    task = db.get(Task, task_id)
                    should_run = bool(
                        task
                        and task.status == "queued"
                        and not task.cancel_requested
                    )
                if should_run:
                    self.submit(task_id, already_queued=False)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


job_manager = JobManager(max_workers=settings.max_concurrent_jobs)
