"""Helper for collapsible (checkable) QGroupBox sections."""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QWidget


def bind_collapsible(box: QGroupBox) -> None:
    """Hide/show a checkable QGroupBox's contents based on its checked state.

    Qt's default checkable QGroupBox only disables (grays out) its children
    when unchecked; it keeps reserving their layout space. This hides them
    instead, collapsing the box down to just its title row.
    """
    children = [w for w in box.findChildren(QWidget) if w.parent() is box]

    def _apply(checked: bool) -> None:
        for w in children:
            w.setVisible(checked)

    box.toggled.connect(_apply)
    _apply(box.isChecked())
