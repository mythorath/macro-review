"""Machine-readable + human progress reporting for pipeline stages."""

from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterable, Iterator, TypeVar

from tqdm import tqdm

T = TypeVar("T")

_reporter_var: ContextVar["ProgressReporter | None"] = ContextVar(
    "macroreview_reporter",
    default=None,
)


class ProgressReporter(ABC):
    @abstractmethod
    def run_start(self, stages: list[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def stage_start(
        self,
        stage: str,
        *,
        total: int | None = None,
        metric: str | None = None,
        message: str | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def item(
        self,
        stage: str,
        *,
        current: int,
        total: int | None = None,
        status: str = "ok",
        path: str | None = None,
        image_id: str | None = None,
        metric: str | None = None,
        message: str | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def stage_done(
        self,
        stage: str,
        *,
        ok: int = 0,
        failed: int = 0,
        message: str | None = None,
        metric: str | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def warning(self, stage: str, message: str, *, metric: str | None = None) -> None:
        raise NotImplementedError

    def log(self, stage: str, message: str) -> None:
        """Informational message (not a warning)."""
        self.warning(stage, message)

    @abstractmethod
    def error(self, stage: str, message: str, *, metric: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def run_done(self, *, ok: bool = True, message: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def track(
        self,
        iterable: Iterable[T],
        *,
        stage: str,
        total: int | None = None,
        unit: str = "img",
        desc: str | None = None,
        metric: str | None = None,
    ) -> Iterator[T]:
        raise NotImplementedError


class HumanReporter(ProgressReporter):
    def run_start(self, stages: list[str]) -> None:
        print(f"Pipeline stages: {', '.join(stages)}")

    def stage_start(
        self,
        stage: str,
        *,
        total: int | None = None,
        metric: str | None = None,
        message: str | None = None,
    ) -> None:
        label = f"{stage}:{metric}" if metric else stage
        if message:
            print(f"== {label} == {message}")
        elif total is not None:
            print(f"== {label} == ({total})")
        else:
            print(f"== {label} ==")

    def item(
        self,
        stage: str,
        *,
        current: int,
        total: int | None = None,
        status: str = "ok",
        path: str | None = None,
        image_id: str | None = None,
        metric: str | None = None,
        message: str | None = None,
    ) -> None:
        # tqdm handles per-item display in track(); keep quiet here.
        return

    def stage_done(
        self,
        stage: str,
        *,
        ok: int = 0,
        failed: int = 0,
        message: str | None = None,
        metric: str | None = None,
    ) -> None:
        label = f"{stage}:{metric}" if metric else stage
        if message:
            print(message)
        else:
            print(f"{label}: {ok} ok" + (f", {failed} failed" if failed else ""))

    def warning(self, stage: str, message: str, *, metric: str | None = None) -> None:
        print(f"WARNING: {message}")

    def log(self, stage: str, message: str) -> None:
        print(message)

    def error(self, stage: str, message: str, *, metric: str | None = None) -> None:
        print(f"ERROR: {message}", file=sys.stderr)

    def run_done(self, *, ok: bool = True, message: str | None = None) -> None:
        print(message or ("Done." if ok else "Finished with errors."))

    def track(
        self,
        iterable: Iterable[T],
        *,
        stage: str,
        total: int | None = None,
        unit: str = "img",
        desc: str | None = None,
        metric: str | None = None,
    ) -> Iterator[T]:
        label = desc or (f"{stage}:{metric}" if metric else stage)
        yield from tqdm(iterable, total=total, desc=label, unit=unit)


class JsonlReporter(ProgressReporter):
    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdout

    def _emit(self, payload: dict[str, Any]) -> None:
        self._stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._stream.flush()

    def run_start(self, stages: list[str]) -> None:
        self._emit({"type": "run_start", "stages": list(stages)})

    def stage_start(
        self,
        stage: str,
        *,
        total: int | None = None,
        metric: str | None = None,
        message: str | None = None,
    ) -> None:
        event: dict[str, Any] = {"type": "stage_start", "stage": stage}
        if total is not None:
            event["total"] = total
        if metric is not None:
            event["metric"] = metric
        if message is not None:
            event["message"] = message
        self._emit(event)

    def item(
        self,
        stage: str,
        *,
        current: int,
        total: int | None = None,
        status: str = "ok",
        path: str | None = None,
        image_id: str | None = None,
        metric: str | None = None,
        message: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": "item",
            "stage": stage,
            "current": current,
            "status": status,
        }
        if total is not None:
            event["total"] = total
        if path is not None:
            event["path"] = path
        if image_id is not None:
            event["id"] = image_id
        if metric is not None:
            event["metric"] = metric
        if message is not None:
            event["message"] = message
        self._emit(event)

    def stage_done(
        self,
        stage: str,
        *,
        ok: int = 0,
        failed: int = 0,
        message: str | None = None,
        metric: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "type": "stage_done",
            "stage": stage,
            "ok": ok,
            "failed": failed,
        }
        if message is not None:
            event["message"] = message
        if metric is not None:
            event["metric"] = metric
        self._emit(event)

    def warning(self, stage: str, message: str, *, metric: str | None = None) -> None:
        event: dict[str, Any] = {
            "type": "warning",
            "stage": stage,
            "message": message,
        }
        if metric is not None:
            event["metric"] = metric
        self._emit(event)

    def log(self, stage: str, message: str) -> None:
        self._emit({"type": "log", "stage": stage, "message": message})

    def error(self, stage: str, message: str, *, metric: str | None = None) -> None:
        event: dict[str, Any] = {
            "type": "error",
            "stage": stage,
            "message": message,
        }
        if metric is not None:
            event["metric"] = metric
        self._emit(event)

    def run_done(self, *, ok: bool = True, message: str | None = None) -> None:
        event: dict[str, Any] = {"type": "run_done", "ok": ok}
        if message is not None:
            event["message"] = message
        self._emit(event)

    def track(
        self,
        iterable: Iterable[T],
        *,
        stage: str,
        total: int | None = None,
        unit: str = "img",
        desc: str | None = None,
        metric: str | None = None,
    ) -> Iterator[T]:
        if total is None:
            sequence: list[T] = list(iterable)
            total = len(sequence)
        else:
            sequence = iterable  # type: ignore[assignment]
        current = 0
        for obj in sequence:
            current += 1
            yield obj
            path = None
            image_id = None
            if isinstance(obj, dict):
                path = obj.get("path")
                image_id = obj.get("id")
            else:
                try:
                    path = obj["path"]  # type: ignore[index]
                    image_id = obj["id"]  # type: ignore[index]
                except Exception:
                    pass
            self.item(
                stage,
                current=current,
                total=total,
                status="ok",
                path=str(path) if path else None,
                image_id=str(image_id) if image_id else None,
                metric=metric,
            )


def make_reporter(mode: str) -> ProgressReporter:
    normalized = (mode or "human").strip().lower()
    if normalized == "jsonl":
        return JsonlReporter()
    if normalized == "human":
        return HumanReporter()
    raise ValueError(f"Unknown progress mode: {mode!r} (expected 'human' or 'jsonl')")


@contextmanager
def use_reporter(reporter: ProgressReporter):
    token = _reporter_var.set(reporter)
    try:
        yield reporter
    finally:
        _reporter_var.reset(token)


def get_reporter() -> ProgressReporter:
    current = _reporter_var.get()
    if current is None:
        return HumanReporter()
    return current


def track(
    iterable: Iterable[T],
    *,
    stage: str,
    total: int | None = None,
    unit: str = "img",
    desc: str | None = None,
    metric: str | None = None,
) -> Iterator[T]:
    return get_reporter().track(
        iterable,
        stage=stage,
        total=total,
        unit=unit,
        desc=desc,
        metric=metric,
    )
