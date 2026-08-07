"""Results page — native gallery over scored SQLite rows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.pipeline_exe import managed_python
from gui.results.delegate import ThumbnailDelegate
from gui.results.detail_pane import DetailPane
from gui.results.model import ResultsModel
from gui.widgets.collapsible import bind_collapsible
from gui.widgets.progress_panel import ProgressPanel
from gui.workers.cli_worker import CliWorker
from settings import load_settings


class ResultsPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = CliWorker(self)
        self._running = False

        self.model = ResultsModel(self)
        self.delegate = ThumbnailDelegate(self)

        self.title = QLabel("Results")
        self.title.setObjectName("PageTitle")
        self.subtitle = QLabel("Scored images from the pipeline database.")
        self.subtitle.setObjectName("PageSubtitle")
        self.subtitle.setWordWrap(True)
        self.empty = QLabel("No scored images yet — run the pipeline from Library.")
        self.empty.setObjectName("PageSubtitle")
        self.empty.setWordWrap(True)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("SecondaryButton")
        self.export_btn = QPushButton("Export crops")
        self.open_report_btn = QPushButton("Open report.html")
        self.open_report_btn.setObjectName("SecondaryButton")

        self.min_share = QSlider(Qt.Horizontal)
        self.min_share.setRange(0, 100)
        self.min_share.setValue(0)
        self.min_share_label = QLabel("0.0")

        self.rec_portfolio = QCheckBox("portfolio")
        self.rec_share = QCheckBox("share")
        self.rec_maybe = QCheckBox("maybe")
        self.rec_skip = QCheckBox("skip")
        self.best_only = QCheckBox("Best-of-burst only")
        self.crop_only = QCheckBox("Crop-worthy only")
        self.library = QComboBox()
        self.library.addItem("All libraries", "")
        self.subject = QLineEdit()
        self.subject.setPlaceholderText("Subject or filename…")
        self.sort = QComboBox()
        self.sort.addItem("Share score", "share")
        self.sort.addItem("Tech score", "tech")
        self.sort.addItem("Filename", "filename")

        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.0, 10.0)
        self.threshold.setSingleStep(0.5)
        self.threshold.setValue(7.0)
        self.threshold.setSpecialValueText("default")
        self.use_threshold = QCheckBox("Override threshold")
        self.crop_limit = QSpinBox()
        self.crop_limit.setRange(1, 500)
        self.crop_limit.setValue(50)
        self.use_crop_limit = QCheckBox("Limit exports")

        self.view = QListView()
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(6)
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setSelectionMode(QListView.SingleSelection)
        self.view.setMouseTracking(True)

        self.detail = DetailPane()
        self.progress = ProgressPanel()
        self.progress.setVisible(False)

        self.refresh_btn.clicked.connect(self.refresh)
        self.export_btn.clicked.connect(self._export_crops)
        self.open_report_btn.clicked.connect(self._open_report)
        self.min_share.valueChanged.connect(self._on_filters)
        self.rec_portfolio.toggled.connect(self._on_filters)
        self.rec_share.toggled.connect(self._on_filters)
        self.rec_maybe.toggled.connect(self._on_filters)
        self.rec_skip.toggled.connect(self._on_filters)
        self.best_only.toggled.connect(self._on_filters)
        self.crop_only.toggled.connect(self._on_filters)
        self.library.currentIndexChanged.connect(self._on_filters)
        self.subject.textChanged.connect(self._on_filters)
        self.sort.currentIndexChanged.connect(self._on_filters)
        self.view.selectionModel().selectionChanged.connect(self._on_selection)
        self.progress.cancel_requested.connect(self._worker.cancel)
        self._worker.line_received.connect(self.progress.handle_event)
        self._worker.text_received.connect(self.progress.append_text)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.export_btn)
        toolbar.addWidget(self.open_report_btn)
        toolbar.addStretch(1)
        toolbar.addWidget(self.status)

        # Two compact rows instead of four — reclaims vertical space for
        # the thumbnail grid, especially on large monitors.
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Min share"))
        row1.addWidget(self.min_share, 1)
        row1.addWidget(self.min_share_label)
        row1.addSpacing(12)
        for box in (
            self.rec_portfolio,
            self.rec_share,
            self.rec_maybe,
            self.rec_skip,
        ):
            row1.addWidget(box)

        row2 = QHBoxLayout()
        row2.addWidget(self.best_only)
        row2.addWidget(self.crop_only)
        row2.addSpacing(12)
        row2.addWidget(QLabel("Library"))
        row2.addWidget(self.library, 1)
        row2.addWidget(QLabel("Sort"))
        row2.addWidget(self.sort)
        row2.addWidget(self.subject, 2)

        filters = QGroupBox("Filters")
        fl = QVBoxLayout(filters)
        fl.setSpacing(6)
        fl.addLayout(row1)
        fl.addLayout(row2)

        advanced = QGroupBox("Crop export advanced")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        adv = QFormLayout(advanced)
        thr_row = QHBoxLayout()
        thr_row.addWidget(self.use_threshold)
        thr_row.addWidget(self.threshold)
        lim_row = QHBoxLayout()
        lim_row.addWidget(self.use_crop_limit)
        lim_row.addWidget(self.crop_limit)
        adv.addRow(thr_row)
        adv.addRow(lim_row)
        bind_collapsible(advanced)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.addWidget(self.empty)
        left_l.addWidget(self.view, 1)
        splitter.addWidget(left)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)
        root.addWidget(self.title)
        root.addWidget(self.subtitle)
        root.addLayout(toolbar)
        root.addWidget(filters)
        root.addWidget(advanced)
        root.addWidget(splitter, 1)
        root.addWidget(self.progress)

    def refresh(self) -> None:
        self.delegate.clear_cache()
        total = self.model.reload()
        self._reload_libraries()
        self._on_filters()
        shown = self.model.rowCount()
        self.empty.setVisible(shown == 0)
        self.view.setVisible(True)
        if self.model.last_error:
            self.status.setText(f"Load error: {self.model.last_error}")
        else:
            self.status.setText(f"{shown} shown · {total} scored")
        self._refresh_report_button()
        self._update_export_state()
        if shown == 0:
            self.detail.clear()

    def _reload_libraries(self) -> None:
        current = self.library.currentData()
        self.library.blockSignals(True)
        self.library.clear()
        self.library.addItem("All libraries", "")
        for lib in self.model.libraries():
            self.library.addItem(lib, lib)
        idx = self.library.findData(current)
        self.library.setCurrentIndex(max(0, idx))
        self.library.blockSignals(False)

    def _selected_recommendations(self) -> set[str]:
        out: set[str] = set()
        if self.rec_portfolio.isChecked():
            out.add("portfolio")
        if self.rec_share.isChecked():
            out.add("share")
        if self.rec_maybe.isChecked():
            out.add("maybe")
        if self.rec_skip.isChecked():
            out.add("skip")
        return out

    def _on_filters(self) -> None:
        min_share = self.min_share.value() / 10.0
        self.min_share_label.setText(f"{min_share:.1f}")
        self.model.set_filters(
            min_share=min_share,
            recommendations=self._selected_recommendations(),
            best_of_burst_only=self.best_only.isChecked(),
            crop_worthy_only=self.crop_only.isChecked(),
            library=str(self.library.currentData() or ""),
            subject_query=self.subject.text(),
            sort_key=str(self.sort.currentData() or "share"),
        )
        self.empty.setVisible(self.model.rowCount() == 0)
        if not self.model.last_error:
            self.status.setText(
                f"{self.model.rowCount()} shown · filters applied"
            )

    def _on_selection(self, *_args) -> None:
        indexes = self.view.selectionModel().selectedIndexes()
        if not indexes:
            self.detail.clear()
            return
        row = self.model.row_at(indexes[0])
        self.detail.set_row(row)

    def _report_path(self) -> Path:
        settings = load_settings()
        return Path(settings.data_dir) / "report.html"

    def _refresh_report_button(self) -> None:
        report = self._report_path()
        exists = report.is_file()
        self.open_report_btn.setEnabled(exists)
        self.open_report_btn.setToolTip(
            str(report) if exists else "No report.html found under data_dir yet."
        )

    def _open_report(self) -> None:
        report = self._report_path()
        if report.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))

    def _update_export_state(self) -> None:
        ready = managed_python() is not None and not self._running
        self.export_btn.setEnabled(ready)
        if managed_python() is None:
            self.export_btn.setToolTip("Run setup first (pipeline Python missing).")
        else:
            self.export_btn.setToolTip("Export suggested crops via managed pipeline")

    def _export_crops(self) -> None:
        if self._worker.busy or self._running:
            return
        if managed_python() is None:
            self.status.setText("Run setup first (pipeline Python missing).")
            return
        threshold = self.threshold.value() if self.use_threshold.isChecked() else None
        limit = (
            int(self.crop_limit.value()) if self.use_crop_limit.isChecked() else None
        )
        self._running = True
        self.progress.setVisible(True)
        self.progress.reset(stages=["crop-export"])
        self.status.setText("Exporting crops…")
        self._update_export_state()
        self.progress.set_running(True)
        self._worker.run_crop_export(threshold=threshold, limit=limit)

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self.progress.append_text(f"[error] {message}")
        if not self._worker.busy:
            self._running = False
            self._update_export_state()
            self.progress.set_running(False)

    def _on_finished(self, code: int) -> None:
        self._running = False
        self.progress.set_running(False)
        self._update_export_state()
        if code == 0:
            settings = load_settings()
            crop_dir = Path(settings.data_dir) / "suggested_crops"
            self.status.setText(f"Crops exported to {crop_dir}")
        else:
            self.status.setText(f"Crop export exited with code {code}")
