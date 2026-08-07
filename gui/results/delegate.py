"""Thumbnail grid delegate for Results gallery."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from gui.results.model import ResultsModel
from gui.results.style import CROP_COLOR, CROP_MASK_ALPHA, rec_color

THUMB = 148
PAD = 6
TEXT_H = 46
ACCENT_H = 3
CELL = QSize(THUMB + PAD * 2, THUMB + TEXT_H + PAD * 2 + ACCENT_H)


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._cache_limit = 200
        self._placeholder = QPixmap(THUMB, THUMB)
        self._placeholder.fill(QColor("#d9d0c2"))

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return CELL

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        bg = QColor("#3d6b6e") if selected else (QColor("#f2ecdf") if hovered else QColor("#faf7f1"))
        border = QColor("#2c5255") if selected else (QColor("#b7a892") if hovered else QColor("#cfc4b4"))
        painter.setPen(border)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 6, 6)

        rec = str(index.data(ResultsModel.RecommendationRole) or "")
        accent = QColor(rec_color(rec))
        accent_rect = QRect(rect.left() + 6, rect.top() + 4, rect.width() - 12, ACCENT_H)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(accent_rect, 1, 1)

        thumb_rect = QRect(
            rect.left() + PAD,
            rect.top() + PAD + ACCENT_H + 2,
            THUMB,
            THUMB,
        )
        pix = self._pixmap_for(index)
        scaled = pix.scaled(
            THUMB,
            THUMB,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        x = thumb_rect.left() + (THUMB - scaled.width()) // 2
        y = thumb_rect.top() + (THUMB - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)

        crop_box = index.data(ResultsModel.CropBoxRole)
        if crop_box:
            self._draw_crop_overlay(painter, QRect(x, y, scaled.width(), scaled.height()), crop_box)

        share = index.data(ResultsModel.ShareScoreRole)
        share_s = f"{float(share):.1f}" if share is not None else "—"
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(31, 42, 46, 200))
        badge_rect = QRect(thumb_rect.right() - 40, thumb_rect.top() + 5, 34, 18)
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(QColor("#f7f3ea"))
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(badge_rect, Qt.AlignCenter, share_s)

        text_top = thumb_rect.bottom() + 3
        text_color = QColor("#f7f3ea") if selected else QColor("#1c1a17")
        painter.setPen(text_color)
        name_font = QFont(option.font)
        name_font.setPointSize(8)
        painter.setFont(name_font)
        name = str(index.data(ResultsModel.FilenameRole) or "")
        name_rect = QRect(rect.left() + PAD, text_top, THUMB, 14)
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, name)

        rec_font = QFont(option.font)
        rec_font.setPointSize(8)
        rec_font.setBold(True)
        painter.setFont(rec_font)
        painter.setPen(QColor("#f0ece2") if selected else accent)
        rec_rect = QRect(rect.left() + PAD, text_top + 14, THUMB, 14)
        painter.drawText(
            rec_rect,
            Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine,
            (rec or "—").upper(),
        )

        glance_font = QFont(option.font)
        glance_font.setPointSize(8)
        painter.setFont(glance_font)
        muted_color = QColor("#d7e0e2") if selected else QColor("#6b6459")
        painter.setPen(muted_color)
        tech = index.data(ResultsModel.TechScoreRole)
        tech_s = f"T {float(tech):.1f}" if tech is not None else "T —"
        glance_rect = QRect(rect.left() + PAD, text_top + 28, THUMB, 14)
        painter.drawText(glance_rect, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine, tech_s)

        tags = []
        group_size = index.data(ResultsModel.GroupSizeRole) or 1
        if group_size > 1:
            tags.append(f"×{group_size}")
        if index.data(ResultsModel.CropWorthyRole):
            tags.append("crop")
        if tags:
            tag_color = QColor("#f0ece2") if selected else QColor(CROP_COLOR)
            painter.setPen(tag_color)
            painter.drawText(
                glance_rect,
                Qt.AlignRight | Qt.AlignVCenter | Qt.TextSingleLine,
                "  ".join(tags),
            )
        painter.restore()

    def _draw_crop_overlay(self, painter: QPainter, pix_rect: QRect, box) -> None:
        x0, y0, x1, y1 = box
        crop_rect = QRect(
            pix_rect.left() + round(x0 * pix_rect.width()),
            pix_rect.top() + round(y0 * pix_rect.height()),
            max(1, round((x1 - x0) * pix_rect.width())),
            max(1, round((y1 - y0) * pix_rect.height())),
        )
        if crop_rect == pix_rect:
            return
        path = QPainterPath()
        path.addRect(QRectF(pix_rect))
        path.addRect(QRectF(crop_rect))
        path.setFillRule(Qt.OddEvenFill)
        painter.save()
        painter.setClipRect(pix_rect)
        painter.fillPath(path, QColor(0, 0, 0, CROP_MASK_ALPHA))
        pen = QPen(QColor(CROP_COLOR))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(crop_rect.adjusted(1, 1, -1, -1))
        painter.restore()

    def _pixmap_for(self, index) -> QPixmap:
        image_id = str(index.data(ResultsModel.IdRole) or "")
        preview = str(index.data(ResultsModel.PreviewPathRole) or "")
        cache_key = image_id or preview
        if cache_key and cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]
        pix = self._placeholder
        if preview:
            path = Path(preview)
            if path.is_file():
                loaded = QPixmap(str(path))
                if not loaded.isNull():
                    pix = loaded
        if cache_key:
            self._cache[cache_key] = pix
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)
        return pix

    def clear_cache(self) -> None:
        self._cache.clear()
