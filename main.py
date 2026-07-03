"""
main.py — Altium Assembly Steps: 2D PCB BOM viewer.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAbstractTextDocumentLayout, QAction, QBrush, QColor, QIcon, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pcb_model import BomEntry, PcbModel, parse_prjpcb_dnp
from pcb_viewer import PcbViewer
from population_state import PopulationState

ICON_PATH = Path(__file__).parent / "icon.svg"


class PcbLoadWorker(QThread):
    """Loads a PcbModel on a background thread to keep the UI responsive."""

    progress = Signal(int, str)     # (percent, message)
    finished_ok = Signal(object)    # PcbModel
    finished_err = Signal(str, str) # (title, message)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        try:
            model = PcbModel.load(self._path, progress=self.progress.emit)
            self.finished_ok.emit(model)
        except FileNotFoundError as exc:
            self.finished_err.emit("File Not Found", str(exc))
        except ValueError as exc:
            self.finished_err.emit("No PCB Found", str(exc))
        except Exception as exc:
            self.finished_err.emit("Load Error", f"Failed to parse file:\n{exc}")

_COLOR_PLACED_ALL  = QColor(60, 180, 60)   # whole row complete
_COLOR_PLACED_CELL = QColor(100, 210, 100) # single side complete



def _apply_row_colors(
    table: "QTableWidget",
    row: int,
    top_refs: list[str],
    bot_refs: list[str],
    visible: list[str],
    placed: frozenset[str],
    dnp: frozenset[str] = frozenset(),
) -> None:
    effective = placed | dnp
    all_done  = bool(visible)   and all(d in effective for d in visible)
    top_done  = bool(top_refs)  and all(d in effective for d in top_refs)
    bot_done  = bool(bot_refs)  and all(d in effective for d in bot_refs)
    row_brush  = QBrush(_COLOR_PLACED_ALL)
    cell_brush = QBrush(_COLOR_PLACED_CELL)
    none_brush = QBrush()
    if all_done:
        for col in range(7):
            item = table.item(row, col)
            if item:
                item.setBackground(row_brush)
    else:
        for col in range(5):
            item = table.item(row, col)
            if item:
                item.setBackground(none_brush)
        item5 = table.item(row, 5)
        item6 = table.item(row, 6)
        if item5:
            item5.setBackground(cell_brush if top_done else none_brush)
        if item6:
            item6.setBackground(cell_brush if bot_done else none_brush)


def _refs_html(refs: list[str], placed: frozenset[str], dnp: frozenset[str] = frozenset()) -> str:
    parts = []
    for d in refs:
        if d in placed:
            parts.append(f'<span style="color:#1a9c1a;font-weight:bold">{d}</span>')
        elif d in dnp:
            parts.append(f'<span style="color:#cc2200">{d}</span>')
        else:
            parts.append(d)
    return ", ".join(parts)


class _HtmlDelegate(QStyledItemDelegate):
    """Renders HTML markup stored in the DisplayRole of table cells."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Reused across paint/sizeHint calls — constructing a QTextDocument per
        # cell is a hotspot when resizing rows on large BOMs.
        self._doc = QTextDocument()

    def paint(self, painter, option, index) -> None:
        opt = type(option)(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        # Draw background / selection highlight without text
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        # Draw HTML text on top
        doc = self._doc
        doc.setDefaultFont(opt.font)
        doc.setHtml(index.data(Qt.ItemDataRole.DisplayRole) or "")
        doc.setTextWidth(opt.rect.width())
        painter.save()
        painter.translate(opt.rect.left(), opt.rect.top())
        painter.setClipRect(QRectF(0, 0, opt.rect.width(), opt.rect.height()))
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        opt = type(option)(option)
        self.initStyleOption(opt, index)
        doc = self._doc
        doc.setDefaultFont(opt.font)
        doc.setHtml(index.data(Qt.ItemDataRole.DisplayRole) or "")
        doc.setTextWidth(max(opt.rect.width(), 1))
        return QSize(int(doc.idealWidth()), max(int(doc.size().height()), 20))


_SIDE_BTN_STYLE = """
QPushButton {
    padding: 4px 10px;
    border: 1px solid #666;
    border-radius: 3px;
}
QPushButton:checked {
    background-color: #0078d4;
    color: white;
    border-color: #005a9e;
    font-weight: bold;
}
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Altium Assembly Tool")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1400, 900)

        self._model: PcbModel | None = None
        self._current_index: int = -1
        self._view_side: str = "TOP"
        # Each entry: (BomEntry, visible_designators)
        self._active_bom: list[tuple[BomEntry, list[str]]] = []
        self._worker: PcbLoadWorker | None = None
        self._progress_dlg: QProgressDialog | None = None
        self._placement = PopulationState()
        self._dnp: frozenset[str] = frozenset()

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._btn_open = QPushButton("Open File")
        self._btn_load_prjpcb = QPushButton("Load .PrjPcb")
        self._lbl_filename = QLabel("No file loaded")
        self._lbl_filename.setMinimumWidth(220)

        self._btn_prev = QPushButton("◄ Prev")
        self._btn_next = QPushButton("Next ►")
        self._lbl_step = QLabel("")
        self._lbl_step.setMinimumWidth(100)

        self._btn_fit = QPushButton("Fit View")

        self._btn_clear = QPushButton("Clear Selection")

        self._btn_save_state = QPushButton("Save State")
        self._btn_open_state = QPushButton("Open State")

        self._btn_top_view = QPushButton("Top Side")
        self._btn_top_view.setCheckable(True)
        self._btn_top_view.setChecked(True)
        self._btn_top_view.setStyleSheet(_SIDE_BTN_STYLE)

        self._btn_bottom_view = QPushButton("Bottom Side")
        self._btn_bottom_view.setCheckable(True)
        self._btn_bottom_view.setStyleSheet(_SIDE_BTN_STYLE)

        self._view_side_group = QButtonGroup(self)
        self._view_side_group.addButton(self._btn_top_view)
        self._view_side_group.addButton(self._btn_bottom_view)
        self._view_side_group.setExclusive(True)

        def _section(label: str) -> None:
            toolbar.addSeparator()
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "QLabel { color: #999; font-size: 9px; font-weight: bold;"
                " padding: 0 4px 0 6px; }"
            )
            toolbar.addWidget(lbl)

        for w in (self._btn_open, self._btn_load_prjpcb, self._lbl_filename):
            toolbar.addWidget(w)

        _section("Steps")
        for w in (self._btn_prev, self._btn_next, self._lbl_step):
            toolbar.addWidget(w)

        _section("View")
        for w in (self._btn_fit, self._btn_clear):
            toolbar.addWidget(w)

        _section("Config")
        for w in (self._btn_save_state, self._btn_open_state):
            toolbar.addWidget(w)

        _section("Board Side")
        for w in (self._btn_top_view, self._btn_bottom_view):
            toolbar.addWidget(w)

        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(False)
        self._btn_fit.setEnabled(False)
        self._btn_clear.setEnabled(False)
        self._btn_save_state.setEnabled(False)
        self._btn_open_state.setEnabled(False)
        self._btn_top_view.setEnabled(False)
        self._btn_bottom_view.setEnabled(False)

        # Central splitter — viewer on top, BOM panel on bottom
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.setCentralWidget(splitter)

        # Top: PCB viewer
        self._viewer = PcbViewer()

        # Bottom: BOM panel
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(4, 4, 4, 4)

        bom_header = QHBoxLayout()
        bom_header.setContentsMargins(0, 0, 0, 0)
        bom_header.addWidget(QLabel("Bill of Materials"))
        self._btn_hide_fitted = QPushButton("Hide Fitted")
        self._btn_hide_fitted.setCheckable(True)
        self._btn_hide_fitted.setToolTip(
            "Hide BOM rows where every part is already placed (or marked DNP)"
        )
        bom_header.addWidget(self._btn_hide_fitted)
        bom_header.addStretch(1)
        bottom_layout.addLayout(bom_header)

        self._bom_table = QTableWidget(0, 7)
        self._bom_table.setHorizontalHeaderLabels(
            ["#", "QTY", "Placed", "To Place", "Name", "Top Refs", "Bottom Refs"]
        )
        hdr = self._bom_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(True)
        self._bom_table.verticalHeader().setVisible(False)
        self._bom_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._bom_table.setWordWrap(True)
        self._bom_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._bom_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._bom_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._bom_table.setAlternatingRowColors(True)
        self._bom_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        _html_delegate = _HtmlDelegate(self._bom_table)
        self._bom_table.setItemDelegateForColumn(5, _html_delegate)
        self._bom_table.setItemDelegateForColumn(6, _html_delegate)
        bottom_layout.addWidget(self._bom_table)

        splitter.addWidget(self._viewer)
        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([650, 250])

        self.statusBar().showMessage("Ready. Open a .PcbDoc or .PrjPcb file.")

    def _connect_signals(self) -> None:
        self._btn_open.clicked.connect(self._on_open_file)
        self._btn_load_prjpcb.clicked.connect(self._on_load_prjpcb)
        self._btn_prev.clicked.connect(self._on_prev)
        self._btn_next.clicked.connect(self._on_next)
        self._btn_fit.clicked.connect(self._viewer.fit_to_view)
        self._btn_clear.clicked.connect(self._on_clear_selection)
        self._btn_save_state.clicked.connect(self._on_save_state)
        self._btn_open_state.clicked.connect(self._on_open_state)
        self._viewer.double_clicked_item.connect(self._on_viewer_double_click)
        self._btn_top_view.clicked.connect(self._on_view_top)
        self._btn_bottom_view.clicked.connect(self._on_view_bottom)
        self._btn_hide_fitted.toggled.connect(self._apply_completed_filter)
        self._bom_table.currentCellChanged.connect(
            lambda row, *_: self._on_bom_row_changed(row)
        )
        self._bom_table.customContextMenuRequested.connect(self._on_bom_context_menu)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Altium PCB File",
            "",
            "Altium PCB Files (*.PcbDoc *.pcbdoc);;All Files (*)",
        )
        if path:
            self._load_file(Path(path))

    def _on_load_prjpcb(self) -> None:
        default_dir = ""
        if self._model and self._model.filepath:
            default_dir = str(self._model.filepath.parent)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Altium Project File",
            default_dir,
            "Altium Project Files (*.PrjPcb *.prjpcb);;All Files (*)",
        )
        if not path:
            return
        try:
            self._dnp = parse_prjpcb_dnp(Path(path))
            self._update_viewer()
            self._update_bom_colors()
            self.statusBar().showMessage(
                f"Loaded {Path(path).name}  |  {len(self._dnp)} DNP component(s)"
            )
        except Exception as exc:
            self._show_error("PrjPcb Load Error", f"Failed to parse file:\n{exc}")

    def _load_file(self, path: Path) -> None:
        self._progress_dlg = QProgressDialog(f"Loading {path.name}…", None, 0, 100, self)
        self._progress_dlg.setWindowTitle("Loading PCB")
        self._progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress_dlg.setMinimumDuration(0)
        self._progress_dlg.setAutoClose(False)
        self._progress_dlg.setAutoReset(False)
        self._progress_dlg.setCancelButton(None)
        self._progress_dlg.setValue(0)

        self._btn_open.setEnabled(False)
        self.statusBar().showMessage(f"Loading {path.name}…")

        self._worker = PcbLoadWorker(path)
        self._worker.progress.connect(self._on_load_progress)
        self._worker.finished_ok.connect(self._on_load_ok)
        self._worker.finished_err.connect(self._on_load_err)
        # Release the worker only once the thread has actually finished —
        # dropping the last reference from the result handlers can destroy a
        # QThread that is still returning from run().
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_finished(self) -> None:
        worker = self._worker
        if worker is not None:
            self._worker = None
            worker.deleteLater()

    def _on_load_progress(self, pct: int, msg: str) -> None:
        dlg = self._progress_dlg
        if dlg is not None:
            dlg.setValue(pct)
            dlg.setLabelText(msg)

    def _on_load_ok(self, model: PcbModel) -> None:
        if self._progress_dlg is not None:
            self._progress_dlg.close()
            self._progress_dlg = None
        self._btn_open.setEnabled(True)

        self._model = model
        self._current_index = -1
        self._view_side = "TOP"
        self._lbl_filename.setText(model.board_name)
        self._rebuild_active_bom()
        self._viewer.set_flip(False)
        self._update_viewer()
        QTimer.singleShot(0, self._viewer.fit_to_view)
        self._placement.clear()
        self._btn_fit.setEnabled(True)
        self._btn_clear.setEnabled(True)
        self._btn_save_state.setEnabled(True)
        self._btn_open_state.setEnabled(True)
        self._btn_top_view.setEnabled(True)
        self._btn_bottom_view.setEnabled(True)
        self._btn_top_view.setChecked(True)
        self._update_navigation()
        self.statusBar().showMessage(
            f"Loaded {model.board_name}  |  {len(self._active_bom)} BOM groups"
        )

    def _on_load_err(self, title: str, message: str) -> None:
        if self._progress_dlg is not None:
            self._progress_dlg.close()
            self._progress_dlg = None
        self._btn_open.setEnabled(True)
        self.statusBar().showMessage("Load failed.")
        self._show_error(title, message)

    def _on_prev(self) -> None:
        if self._active_bom and self._current_index > 0:
            self._select_row(self._current_index - 1)

    def _on_next(self) -> None:
        if self._active_bom and self._current_index < len(self._active_bom) - 1:
            self._select_row(self._current_index + 1)

    def _on_view_top(self) -> None:
        self._view_side = "TOP"
        self._viewer.set_flip(False)
        self._refresh_svg()

    def _on_view_bottom(self) -> None:
        self._view_side = "BOTTOM"
        self._viewer.set_flip(True)
        self._refresh_svg()

    def _refresh_svg(self) -> None:
        if self._model is None:
            return
        self._update_viewer()

    def _on_bom_row_changed(self, row: int) -> None:
        if not self._active_bom or row < 0 or row == self._current_index:
            return
        self._current_index = row
        self._update_navigation()
        self._update_viewer()

    def _on_clear_selection(self) -> None:
        if self._model is None:
            return
        self._current_index = -1
        self._bom_table.blockSignals(True)
        self._bom_table.clearSelection()
        self._bom_table.blockSignals(False)
        self._update_navigation()
        self._update_viewer()

    def _on_viewer_double_click(self, item_x: float, item_y: float) -> None:
        if self._model is None:
            return
        hidden = self._model.hidden_designators_for_side(self._view_side)
        if 0 <= self._current_index < len(self._active_bom):
            # BOM row selected: only refs from that row, on the current side
            _, visible = self._active_bom[self._current_index]
            allowed: set[str] | None = set(visible) - hidden
            exclude = None
        else:
            allowed = None
            exclude = hidden
        vx, vy = self._model.viewbox_origin
        desig = self._model.component_at_svg(item_x + vx, item_y + vy, exclude=exclude, allowed=allowed)
        if desig is None:
            return
        self._placement.toggle(desig)
        self._update_viewer()
        self._update_bom_row_for(desig)

    def _on_save_state(self) -> None:
        default = ""
        if self._model and self._model.filepath:
            default = str(self._model.filepath.with_suffix(".popstate.json"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Population State", default,
            "Population State (*.json);;All Files (*)",
        )
        if path:
            self._placement.save(Path(path))
            self.statusBar().showMessage(f"Saved: {Path(path).name}")

    def _on_open_state(self) -> None:
        default_dir = ""
        if self._model and self._model.filepath:
            default_dir = str(self._model.filepath.parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Population State", default_dir,
            "Population State (*.json);;All Files (*)",
        )
        if path:
            self._placement.load(Path(path))
            self._update_viewer()
            self._update_bom_colors()
            self.statusBar().showMessage(f"Loaded: {Path(path).name}")

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _rebuild_active_bom(self) -> None:
        if self._model is None:
            self._active_bom = []
            self._populate_bom_table([])
            return

        result: list[tuple[BomEntry, list[str]]] = [
            (entry, entry.designators) for entry in self._model.bom
        ]
        self._active_bom = result
        self._current_index = -1
        self._bom_table.blockSignals(True)
        self._bom_table.clearSelection()
        self._bom_table.blockSignals(False)
        self._populate_bom_table(result)
        self._update_navigation()

    def _select_row(self, index: int) -> None:
        self._current_index = index
        self._bom_table.blockSignals(True)
        self._bom_table.selectRow(index)
        self._bom_table.scrollToItem(self._bom_table.item(index, 0))
        self._bom_table.blockSignals(False)
        self._update_navigation()
        self._update_viewer()

    def _placed_visible(self) -> frozenset[str]:
        """Placed designators to render green border for — only the current BOM row, current side."""
        if self._model is None or not (0 <= self._current_index < len(self._active_bom)):
            return frozenset()
        hidden = self._model.hidden_designators_for_side(self._view_side)
        _, visible = self._active_bom[self._current_index]
        return frozenset(d for d in visible if d in self._placement.placed and d not in hidden)

    def _dnp_visible(self) -> frozenset[str]:
        """DNP designators to render red X for — only the current BOM row, current side."""
        if self._model is None or not self._dnp or not (0 <= self._current_index < len(self._active_bom)):
            return frozenset()
        hidden = self._model.hidden_designators_for_side(self._view_side)
        _, visible = self._active_bom[self._current_index]
        return frozenset(d for d in visible if d in self._dnp and d not in hidden)

    def _update_viewer(self) -> None:
        if self._model is None:
            return
        if 0 <= self._current_index < len(self._active_bom):
            _, visible = self._active_bom[self._current_index]
            hide = self._model.hidden_designators_for_side(self._view_side)
            svg = self._model.svg_for_designators(set(visible), hide or None)
        else:
            svg = self._model.side_filtered_svg(self._view_side)
        placed = self._placed_visible()
        if placed:
            svg = self._model.add_placed_markers(svg, placed)
        dnp = self._dnp_visible()
        if dnp:
            svg = self._model.add_dnp_markers(svg, dnp)
        self._viewer.load_svg(svg)

    def _update_navigation(self) -> None:
        if self._model is None:
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
            self._lbl_step.setText("")
            return
        n = len(self._active_bom)
        idx = self._current_index
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < n - 1)
        self._lbl_step.setText(f"Step {idx + 1} of {n}" if idx >= 0 else f"0 of {n}")

    def _apply_default_column_widths(self) -> None:
        w = self._bom_table.viewport().width()
        if w <= 0:
            return
        self._bom_table.setColumnWidth(0, int(w * 0.03))  # #
        self._bom_table.setColumnWidth(1, int(w * 0.05))  # QTY
        self._bom_table.setColumnWidth(2, int(w * 0.05))  # Placed
        self._bom_table.setColumnWidth(3, int(w * 0.06))  # To Place
        self._bom_table.setColumnWidth(4, int(w * 0.23))  # Name
        self._bom_table.setColumnWidth(5, int(w * 0.29))  # Top Refs
        # column 6 (Bottom Refs) fills the remainder via setStretchLastSection(True)

    def _populate_bom_table(self, active: list[tuple[BomEntry, list[str]]]) -> None:
        placed = self._placement.placed
        dnp = self._dnp
        effective = placed | dnp
        self._bom_table.blockSignals(True)
        self._bom_table.clearContents()
        self._bom_table.setRowCount(len(active))
        for row, (entry, visible) in enumerate(active):
            placed_count = sum(1 for d in visible if d in effective)
            def _centered(text: str) -> QTableWidgetItem:
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                return item
            self._bom_table.setItem(row, 0, _centered(str(row + 1)))
            self._bom_table.setItem(row, 1, _centered(str(len(visible))))
            self._bom_table.setItem(row, 2, _centered(str(placed_count)))
            self._bom_table.setItem(row, 3, _centered(str(len(visible) - placed_count)))
            self._bom_table.setItem(row, 4, QTableWidgetItem(entry.comment))
            self._bom_table.setItem(row, 5, QTableWidgetItem(_refs_html(entry.top_refs, placed, dnp)))
            self._bom_table.setItem(row, 6, QTableWidgetItem(_refs_html(entry.bot_refs, placed, dnp)))
            _apply_row_colors(self._bom_table, row, entry.top_refs, entry.bot_refs, visible, placed, dnp)
        self._bom_table.blockSignals(False)
        self._apply_default_column_widths()
        self._bom_table.resizeRowsToContents()
        self._apply_completed_filter()

    def _refresh_bom_row(self, row: int) -> None:
        """Refresh counts, ref HTML, and colours of one BOM row in place."""
        entry, visible = self._active_bom[row]
        placed = self._placement.placed
        dnp = self._dnp
        effective = placed | dnp
        placed_count = sum(1 for d in visible if d in effective)
        placed_item = self._bom_table.item(row, 2)
        to_place_item = self._bom_table.item(row, 3)
        if placed_item:
            placed_item.setText(str(placed_count))
        if to_place_item:
            to_place_item.setText(str(len(visible) - placed_count))
        top_item = self._bom_table.item(row, 5)
        bot_item = self._bom_table.item(row, 6)
        if top_item:
            top_item.setText(_refs_html(entry.top_refs, placed, dnp))
        if bot_item:
            bot_item.setText(_refs_html(entry.bot_refs, placed, dnp))
        _apply_row_colors(self._bom_table, row, entry.top_refs, entry.bot_refs, visible, placed, dnp)

    def _update_bom_colors(self) -> None:
        """Refresh ref HTML and row/cell colours without rebuilding the whole table."""
        self._bom_table.blockSignals(True)
        for row in range(len(self._active_bom)):
            self._refresh_bom_row(row)
        self._bom_table.blockSignals(False)
        self._bom_table.resizeRowsToContents()
        self._bom_table.viewport().update()
        self._apply_completed_filter()

    def _update_bom_row_for(self, desig: str) -> None:
        """Refresh only the BOM row containing the given designator (toggle hot path)."""
        for row, (_entry, visible) in enumerate(self._active_bom):
            if desig in visible:
                self._bom_table.blockSignals(True)
                self._refresh_bom_row(row)
                self._bom_table.blockSignals(False)
                self._bom_table.resizeRowToContents(row)
                if self._btn_hide_fitted.isChecked():
                    self._bom_table.setRowHidden(row, self._row_is_complete(visible))
                return

    def _row_is_complete(self, visible: list[str]) -> bool:
        """True when every visible designator in the row is placed or DNP."""
        effective = self._placement.placed | self._dnp
        return bool(visible) and all(d in effective for d in visible)

    def _apply_completed_filter(self) -> None:
        """Hide fully-fitted BOM rows when the 'Hide Fitted' toggle is on."""
        hide = self._btn_hide_fitted.isChecked()
        for row, (_entry, visible) in enumerate(self._active_bom):
            self._bom_table.setRowHidden(row, hide and self._row_is_complete(visible))

    # ------------------------------------------------------------------
    # Keyboard navigation
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Down and not self._bom_table.hasFocus():
            self._on_next()
        elif event.key() == Qt.Key.Key_Up and not self._bom_table.hasFocus():
            self._on_prev()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _on_bom_context_menu(self, pos) -> None:
        row = self._bom_table.rowAt(pos.y())
        if row < 0 or row >= len(self._active_bom):
            return
        entry, visible = self._active_bom[row]
        top_refs = entry.top_refs
        bot_refs = entry.bot_refs
        menu = QMenu(self)
        a_all = QAction(f"Copy All Refs ({len(visible)})", self)
        a_all.triggered.connect(lambda: QApplication.clipboard().setText(", ".join(visible)))
        menu.addAction(a_all)
        if top_refs:
            a_top = QAction(f"Copy Top Refs ({len(top_refs)})", self)
            a_top.triggered.connect(lambda: QApplication.clipboard().setText(", ".join(top_refs)))
            menu.addAction(a_top)
        if bot_refs:
            a_bot = QAction(f"Copy Bottom Refs ({len(bot_refs)})", self)
            a_bot.triggered.connect(lambda: QApplication.clipboard().setText(", ".join(bot_refs)))
            menu.addAction(a_bot)
        menu.exec(self._bom_table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # Error display
    # ------------------------------------------------------------------

    def _show_error(self, title: str, message: str) -> None:
        self.statusBar().showMessage(f"Error: {title}")
        QMessageBox.critical(self, title, message)


def _run_browser_mode(pcb_path: Path | None, port: int) -> None:
    import threading
    import webbrowser
    from web_server import WebServer

    server = WebServer(port=port)
    url = f"http://127.0.0.1:{port}"

    if pcb_path is not None:
        print(f"Loading {pcb_path.name}…")
        try:
            server.load(pcb_path)
        except Exception as exc:
            print(f"Load error: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"Serving at {url}  (Ctrl-C to quit)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.run()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Altium Assembly Tool")
    parser.add_argument("file", nargs="?", help="PCB file to open (.PcbDoc)")
    parser.add_argument("--browser", action="store_true", help="Launch browser-based UI")
    parser.add_argument("--port", type=int, default=4321, help="Port for browser UI (default: 4321)")
    args = parser.parse_args()

    pcb_path = Path(args.file) if args.file else None

    if args.browser:
        _run_browser_mode(pcb_path, args.port)
        return

    app = QApplication(sys.argv[:1])  # don't pass argparse args to Qt
    app.setApplicationName("Altium Assembly Steps")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    if pcb_path is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, lambda: window._load_file(pcb_path))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
