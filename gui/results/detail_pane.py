"""Detail pane for a selected gallery row."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QRectF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.results.style import CROP_COLOR, CROP_MASK_ALPHA

CROP_PREVIEW_MAX_W = 260


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _crop_box_rect(box: tuple[float, float, float, float], width: int, height: int) -> QRect:
    x0, y0, x1, y1 = box
    left = max(0, min(width - 1, round(x0 * width)))
    top = max(0, min(height - 1, round(y0 * height)))
    right = max(left + 1, min(width, round(x1 * width)))
    bottom = max(top + 1, min(height, round(y1 * height)))
    return QRect(left, top, right - left, bottom - top)


def _with_crop_overlay(pixmap: QPixmap, box: tuple[float, float, float, float]) -> QPixmap:
    """Draw a dimmed mask + amber outline over the suggested crop, matching report.py's HTML overlay."""
    crop_rect = _crop_box_rect(box, pixmap.width(), pixmap.height())
    full_rect = pixmap.rect()
    if crop_rect == full_rect:
        return pixmap
    out = QPixmap(pixmap)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRect(QRectF(full_rect))
    path.addRect(QRectF(crop_rect))
    path.setFillRule(Qt.OddEvenFill)
    painter.fillPath(path, QColor(0, 0, 0, CROP_MASK_ALPHA))
    pen = QPen(QColor(CROP_COLOR))
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(crop_rect.adjusted(1, 1, -1, -1))
    painter.end()
    return out


def _cropped(pixmap: QPixmap, box: tuple[float, float, float, float]) -> QPixmap:
    return pixmap.copy(_crop_box_rect(box, pixmap.width(), pixmap.height()))


class DetailPane(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row: dict[str, Any] | None = None

        self.preview = QLabel("Select an image")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setStyleSheet(
            "QLabel { background: #1f2a2e; color: #d7e0e2; border-radius: 6px; }"
        )

        self.filename = QLabel("—")
        self.filename.setObjectName("PageTitle")
        self.filename.setWordWrap(True)
        self.meta = QLabel("")
        self.meta.setObjectName("PageSubtitle")
        self.meta.setWordWrap(True)
        self.comment = QLabel("")
        self.comment.setWordWrap(True)

        self.open_original_btn = QPushButton("Open original")
        self.open_preview_btn = QPushButton("Open preview")
        self.open_preview_btn.setObjectName("SecondaryButton")
        self.reveal_btn = QPushButton("Reveal in Explorer")
        self.reveal_btn.setObjectName("SecondaryButton")

        self.open_original_btn.clicked.connect(self._open_original)
        self.open_preview_btn.clicked.connect(self._open_preview)
        self.reveal_btn.clicked.connect(self._reveal)

        self.crop_group = QWidget()
        crop_l = QVBoxLayout(self.crop_group)
        crop_l.setContentsMargins(0, 4, 0, 0)
        crop_l.setSpacing(4)
        self.crop_caption = QLabel("Suggested crop")
        self.crop_caption.setObjectName("PageSubtitle")
        self.crop_preview = QLabel()
        self.crop_preview.setAlignment(Qt.AlignCenter)
        self.crop_preview.setStyleSheet(
            "QLabel { background: #1f2a2e; border-radius: 6px; }"
        )
        self.open_crop_btn = QPushButton("Open exported crop")
        self.open_crop_btn.setObjectName("SecondaryButton")
        self.open_crop_btn.clicked.connect(self._open_crop)
        crop_l.addWidget(self.crop_caption)
        crop_l.addWidget(self.crop_preview)
        crop_l.addWidget(self.open_crop_btn)
        self.crop_group.setVisible(False)

        form = QFormLayout()
        self.share_l = QLabel("—")
        self.tech_l = QLabel("—")
        self.aes_l = QLabel("—")
        self.vlm_l = QLabel("—")
        self.comp_l = QLabel("—")
        self.rec_l = QLabel("—")
        self.subject_l = QLabel("—")
        self.crop_l = QLabel("—")
        self.burst_l = QLabel("—")
        form.addRow("Share", self.share_l)
        form.addRow("Tech", self.tech_l)
        form.addRow("Aesthetic", self.aes_l)
        form.addRow("VLM", self.vlm_l)
        form.addRow("Comp", self.comp_l)
        form.addRow("Recommendation", self.rec_l)
        form.addRow("Subject", self.subject_l)
        form.addRow("Crop worthy", self.crop_l)
        form.addRow("Burst", self.burst_l)

        btns = QHBoxLayout()
        btns.addWidget(self.open_original_btn)
        btns.addWidget(self.open_preview_btn)
        btns.addWidget(self.reveal_btn)
        btns.addStretch(1)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.preview)
        layout.addWidget(self.filename)
        layout.addWidget(self.meta)
        layout.addLayout(form)
        layout.addWidget(self.comment)
        layout.addWidget(self.crop_group)
        layout.addLayout(btns)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)
        self.clear()

    def clear(self) -> None:
        self._row = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText("Select an image")
        self.filename.setText("—")
        self.meta.setText("")
        self.comment.setText("")
        for lab in (
            self.share_l,
            self.tech_l,
            self.aes_l,
            self.vlm_l,
            self.comp_l,
            self.rec_l,
            self.subject_l,
            self.crop_l,
            self.burst_l,
        ):
            lab.setText("—")
        self.open_original_btn.setEnabled(False)
        self.open_preview_btn.setEnabled(False)
        self.reveal_btn.setEnabled(False)
        self.crop_group.setVisible(False)
        self.crop_preview.setPixmap(QPixmap())
        self.open_crop_btn.setEnabled(False)

    def set_row(self, row: dict[str, Any] | None) -> None:
        if not row:
            self.clear()
            return
        self._row = row
        name = str(row.get("filename") or Path(str(row.get("path") or "")).name)
        self.filename.setText(name)
        lib = row.get("source_library") or ""
        self.meta.setText(str(lib))
        self.share_l.setText(_fmt(row.get("share_score")))
        self.tech_l.setText(_fmt(row.get("tech_score_c")))
        self.aes_l.setText(_fmt(row.get("aes_score_c")))
        self.vlm_l.setText(_fmt(row.get("vlm_score_c")))
        self.comp_l.setText(_fmt(row.get("comp_score_c")))
        self.rec_l.setText(str(row.get("share_recommendation") or "—"))
        self.subject_l.setText(str(row.get("subject") or "—"))
        self.crop_l.setText("Yes" if row.get("crop_worthy") else "No")
        group_size = row.get("group_size") or 0
        is_best = row.get("is_best")
        if group_size and int(group_size) > 1:
            self.burst_l.setText(
                f"{'Best' if is_best else 'Member'} of {int(group_size)}"
            )
        else:
            self.burst_l.setText("Single")
        comment = str(row.get("comment") or "").strip()
        self.comment.setText(comment)

        preview_path = row.get("preview_path") or ""
        pix = QPixmap()
        if preview_path and Path(preview_path).is_file():
            pix = QPixmap(str(preview_path))
        crop_box = row.get("_crop_box_norm")
        if pix.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("No preview")
        else:
            self.preview.setText("")
            display = _with_crop_overlay(pix, crop_box) if crop_box else pix
            self.preview.setPixmap(
                display.scaled(
                    self.preview.width() or 320,
                    280,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

        self._update_crop_panel(row, pix, crop_box)

        path = Path(str(row.get("path") or ""))
        self.open_original_btn.setEnabled(path.is_file())
        self.open_preview_btn.setEnabled(bool(preview_path) and Path(preview_path).is_file())
        self.reveal_btn.setEnabled(path.exists() or path.parent.is_dir())

    def _update_crop_panel(
        self,
        row: dict[str, Any],
        preview_pix: QPixmap,
        crop_box: tuple[float, float, float, float] | None,
    ) -> None:
        export_path = str(row.get("crop_export_path") or "")
        has_export = bool(export_path) and Path(export_path).is_file()
        self.open_crop_btn.setEnabled(has_export)

        crop_pix = QPixmap()
        if has_export:
            crop_pix = QPixmap(export_path)
            self.crop_caption.setText("Suggested crop (exported, full-res)")
        elif crop_box and not preview_pix.isNull():
            crop_pix = _cropped(preview_pix, crop_box)
            self.crop_caption.setText("Suggested crop (preview resolution)")

        if crop_pix.isNull():
            self.crop_group.setVisible(False)
            return
        self.crop_group.setVisible(True)
        self.crop_preview.setPixmap(
            crop_pix.scaled(
                CROP_PREVIEW_MAX_W,
                CROP_PREVIEW_MAX_W,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def _open_original(self) -> None:
        if not self._row:
            return
        path = Path(str(self._row.get("path") or ""))
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_preview(self) -> None:
        if not self._row:
            return
        path = Path(str(self._row.get("preview_path") or ""))
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_crop(self) -> None:
        if not self._row:
            return
        path = Path(str(self._row.get("crop_export_path") or ""))
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _reveal(self) -> None:
        if not self._row:
            return
        path = Path(str(self._row.get("path") or ""))
        if sys.platform == "win32" and path.exists():
            subprocess.run(
                ["explorer", "/select,", str(path.resolve())],
                check=False,
            )
            return
        target = path if path.is_dir() else path.parent
        if target.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
