"""QAbstractListModel for scored gallery rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal

from report import load_scored_rows


def _parse_crop_box(raw: Any) -> tuple[float, float, float, float] | None:
    if not raw:
        return None
    try:
        box = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(box, (list, tuple)) and len(box) == 4:
            x0, y0, x1, y1 = (float(v) for v in box)
            return (x0, y0, x1, y1)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


class ResultsModel(QAbstractListModel):
    IdRole = Qt.UserRole + 1
    PathRole = Qt.UserRole + 2
    PreviewPathRole = Qt.UserRole + 3
    ShareScoreRole = Qt.UserRole + 4
    RecommendationRole = Qt.UserRole + 5
    FilenameRole = Qt.UserRole + 6
    LibraryRole = Qt.UserRole + 7
    IsBestRole = Qt.UserRole + 8
    CropWorthyRole = Qt.UserRole + 9
    SubjectRole = Qt.UserRole + 10
    RowDictRole = Qt.UserRole + 11
    TechScoreRole = Qt.UserRole + 12
    CropBoxRole = Qt.UserRole + 13
    CropExportPathRole = Qt.UserRole + 14
    GroupSizeRole = Qt.UserRole + 15

    filters_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all: list[dict[str, Any]] = []
        self._rows: list[dict[str, Any]] = []
        self._min_share = 0.0
        self._recommendations: set[str] = set()
        self._best_of_burst_only = False
        self._crop_worthy_only = False
        self._library = ""
        self._subject_query = ""
        self._sort_key = "share"  # share | tech | filename
        self._error: str | None = None

    @property
    def last_error(self) -> str | None:
        return self._error

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: N802
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role in (Qt.DisplayRole, self.FilenameRole):
            return row.get("filename") or ""
        if role == self.IdRole:
            return row.get("id")
        if role == self.PathRole:
            return row.get("path") or ""
        if role == self.PreviewPathRole:
            return row.get("preview_path") or ""
        if role == self.ShareScoreRole:
            return row.get("share_score")
        if role == self.TechScoreRole:
            return row.get("tech_score_c")
        if role == self.RecommendationRole:
            return row.get("share_recommendation") or ""
        if role == self.LibraryRole:
            return row.get("source_library") or ""
        if role == self.IsBestRole:
            return bool(row.get("is_best"))
        if role == self.CropWorthyRole:
            return bool(row.get("crop_worthy"))
        if role == self.SubjectRole:
            return row.get("subject") or ""
        if role == self.CropBoxRole:
            return row.get("_crop_box_norm")
        if role == self.CropExportPathRole:
            path = row.get("crop_export_path") or ""
            return path if path and Path(path).is_file() else ""
        if role == self.GroupSizeRole:
            return int(row.get("group_size") or 1)
        if role == self.RowDictRole:
            return row
        if role == Qt.ToolTipRole:
            rec = row.get("share_recommendation") or "-"
            share = row.get("share_score")
            share_s = f"{float(share):.1f}" if share is not None else "-"
            return f"{row.get('filename')}\nshare {share_s} · {rec}"
        return None

    def roleNames(self):  # noqa: N802
        return {
            self.IdRole: b"id",
            self.PathRole: b"path",
            self.PreviewPathRole: b"preview_path",
            self.ShareScoreRole: b"share_score",
            self.RecommendationRole: b"recommendation",
            self.FilenameRole: b"filename",
            self.LibraryRole: b"library",
            self.IsBestRole: b"is_best",
            self.CropWorthyRole: b"crop_worthy",
            self.SubjectRole: b"subject",
            self.RowDictRole: b"row",
            self.TechScoreRole: b"tech_score",
            self.CropBoxRole: b"crop_box",
            self.CropExportPathRole: b"crop_export_path",
            self.GroupSizeRole: b"group_size",
        }

    def row_at(self, index: QModelIndex) -> dict[str, Any] | None:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        return self._rows[index.row()]

    def libraries(self) -> list[str]:
        libs = sorted(
            {
                str(r.get("source_library") or "").strip()
                for r in self._all
                if str(r.get("source_library") or "").strip()
            }
        )
        return libs

    def reload(self) -> int:
        self._error = None
        try:
            self._all = list(load_scored_rows())
            for row in self._all:
                row["_crop_box_norm"] = _parse_crop_box(row.get("crop_box"))
        except Exception as exc:  # noqa: BLE001 — surface to UI
            self._error = str(exc)
            self._all = []
        self._apply()
        return len(self._all)

    def set_filters(
        self,
        *,
        min_share: float | None = None,
        recommendations: set[str] | None = None,
        best_of_burst_only: bool | None = None,
        crop_worthy_only: bool | None = None,
        library: str | None = None,
        subject_query: str | None = None,
        sort_key: str | None = None,
    ) -> None:
        if min_share is not None:
            self._min_share = float(min_share)
        if recommendations is not None:
            self._recommendations = set(recommendations)
        if best_of_burst_only is not None:
            self._best_of_burst_only = bool(best_of_burst_only)
        if crop_worthy_only is not None:
            self._crop_worthy_only = bool(crop_worthy_only)
        if library is not None:
            self._library = library
        if subject_query is not None:
            self._subject_query = subject_query
        if sort_key is not None:
            self._sort_key = sort_key
        self._apply()
        self.filters_changed.emit()

    def _apply(self) -> None:
        rows = []
        q = self._subject_query.strip().lower()
        for row in self._all:
            share = row.get("share_score")
            share_f = float(share) if share is not None else None
            if share_f is not None and share_f < self._min_share:
                continue
            if share_f is None and self._min_share > 0:
                continue
            rec = str(row.get("share_recommendation") or "").strip().lower()
            if self._recommendations and rec not in self._recommendations:
                continue
            if self._best_of_burst_only:
                group_size = int(row.get("group_size") or 0)
                if group_size > 1 and not row.get("is_best"):
                    continue
            if self._crop_worthy_only and not row.get("crop_worthy"):
                continue
            if self._library and str(row.get("source_library") or "") != self._library:
                continue
            if q:
                subject = str(row.get("subject") or "").lower()
                filename = str(row.get("filename") or "").lower()
                if q not in subject and q not in filename:
                    continue
            rows.append(row)

        def sort_key(r: dict[str, Any]):
            if self._sort_key == "filename":
                return (str(r.get("filename") or "").lower(),)
            if self._sort_key == "tech":
                tech = r.get("tech_score_c")
                return (
                    0 if tech is not None else 1,
                    -(float(tech) if tech is not None else 0.0),
                    str(r.get("filename") or "").lower(),
                )
            share = r.get("share_score")
            return (
                0 if share is not None else 1,
                -(float(share) if share is not None else 0.0),
                str(r.get("filename") or "").lower(),
            )

        rows.sort(key=sort_key)
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
