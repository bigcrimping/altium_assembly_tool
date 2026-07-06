"""
main.py — Altium Assembly Steps: 2D PCB BOM viewer.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QAction,
    QBrush,
    QColor,
    QIcon,
    QKeySequence,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
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


def _entry_matches(entry: BomEntry, query: str) -> bool:
    """Case-insensitive substring match against name, description, or any designator."""
    if query in entry.comment.lower() or query in entry.description.lower():
        return True
    return any(query in d.lower() for d in entry.designators)


def _refs_html(refs: list[str], placed: frozenset[str], dnp: frozenset[str] = frozenset()) -> str:
    parts = []
    for d in refs:
        if d in placed:
            # Colour-blind-safe (Okabe-Ito), matches the board's placed/DNP markers.
            parts.append(f'<span style="color:#00c489;font-weight:bold">{d}</span>')
        elif d in dnp:
            parts.append(f'<span style="color:#ff7a33">{d}</span>')
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


class FlowLayout(QLayout):
    """Lays widgets left-to-right and wraps them onto new rows as width runs out.

    Replaces a single QToolBar for the main controls: on narrow windows the
    buttons flow onto additional rows — all staying visible and directly
    clickable — instead of collapsing into the hard-to-use overflow ('>>')
    popup that QToolBar shows when its items don't fit on one line.
    """

    def __init__(self, parent=None, margin: int = 4, hspacing: int = 6,
                 vspacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item) -> None:  # noqa: N802 (Qt override)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 (Qt override)
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 (Qt override)
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 (Qt override)
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt override)
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt override)
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            item_size = item.sizeHint()
            next_x = x + item_size.width() + self._hspace
            if next_x - self._hspace > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._vspace
                next_x = x + item_size.width() + self._hspace
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))
            x = next_x
            line_height = max(line_height, item_size.height())
        return y + line_height - rect.y() + margins.bottom()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Altium Assembly Tool")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1400, 900)

        self._model: PcbModel | None = None
        self._current_index: int = -1
        self._dnp_view: bool = False  # show every DNP part board-wide (not a single BOM row)
        self._view_side: str = "TOP"
        # Each entry: (BomEntry, visible_designators)
        self._active_bom: list[tuple[BomEntry, list[str]]] = []
        self._worker: PcbLoadWorker | None = None
        self._progress_dlg: QProgressDialog | None = None
        self._placement = PopulationState()
        self._dnp: frozenset[str] = frozenset()
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._settings = QSettings("altium_assembly_tool", "AltiumAssemblyTool")

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Wrapping toolbar: a flow layout so controls reflow onto extra rows on
        # narrow windows instead of hiding behind a QToolBar overflow popup.
        toolbar_widget = QWidget()
        flow = FlowLayout(toolbar_widget)
        tb_policy = toolbar_widget.sizePolicy()
        tb_policy.setHeightForWidth(True)
        tb_policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        toolbar_widget.setSizePolicy(tb_policy)

        self._btn_open = QToolButton()
        self._btn_open.setText("Open File")
        self._btn_open.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._recent_menu = QMenu(self._btn_open)
        self._recent_menu.setToolTipsVisible(True)
        self._btn_open.setMenu(self._recent_menu)
        self._rebuild_recent_menu()
        self._btn_load_prjpcb = QPushButton("Load .PrjPcb")
        self._lbl_filename = QLabel("No file loaded")
        self._lbl_filename.setMinimumWidth(220)

        self._btn_prev = QPushButton("◄ Prev")
        self._btn_next = QPushButton("Next ►")
        self._lbl_step = QLabel("")
        self._lbl_step.setMinimumWidth(100)

        self._btn_fit = QPushButton("Fit View")
        self._btn_fit_sel = QPushButton("Fit Selection")
        self._btn_fit_sel.setToolTip("Zoom to the components of the selected BOM row")

        self._btn_auto_zoom = QPushButton("Auto Zoom")
        self._btn_auto_zoom.setCheckable(True)
        self._btn_auto_zoom.setStyleSheet(_SIDE_BTN_STYLE)
        self._btn_auto_zoom.setToolTip("Automatically zoom to each BOM row as you step through")

        self._btn_labels = QPushButton("Labels")
        self._btn_labels.setCheckable(True)
        self._btn_labels.setChecked(True)
        self._btn_labels.setStyleSheet(_SIDE_BTN_STYLE)
        self._btn_labels.setToolTip("Show designator labels on the selected row's components")

        self._btn_clear = QPushButton("Clear Selection")

        self._btn_dnp_view = QPushButton("Show DNP")
        self._btn_dnp_view.setCheckable(True)
        self._btn_dnp_view.setStyleSheet(_SIDE_BTN_STYLE)
        self._btn_dnp_view.setToolTip(
            "Highlight every part marked Do Not Fit (DNP) across the board — "
            "a quick check of parts that should not be fitted"
        )

        self._btn_save_state = QPushButton("Save State")
        self._btn_open_state = QPushButton("Open State")

        self._btn_auto_save = QPushButton("Auto Save")
        self._btn_auto_save.setCheckable(True)
        self._btn_auto_save.setStyleSheet(_SIDE_BTN_STYLE)
        self._btn_auto_save.setToolTip(
            "Automatically save progress to <board>.popstate.json after every change"
        )
        self._btn_auto_save.setChecked(
            self._settings.value("autoSave", False, type=bool)
        )

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
            lbl = QLabel(label)
            lbl.setStyleSheet(
                "QLabel { color: #999; font-size: 9px; font-weight: bold;"
                " padding: 0 4px 0 6px; }"
            )
            flow.addWidget(lbl)

        for w in (self._btn_open, self._btn_load_prjpcb, self._lbl_filename):
            flow.addWidget(w)

        _section("Steps")
        for w in (self._btn_prev, self._btn_next, self._lbl_step):
            flow.addWidget(w)

        _section("View")
        for w in (self._btn_fit, self._btn_fit_sel, self._btn_auto_zoom, self._btn_labels,
                  self._btn_clear, self._btn_dnp_view):
            flow.addWidget(w)

        _section("Config")
        for w in (self._btn_save_state, self._btn_open_state, self._btn_auto_save):
            flow.addWidget(w)

        _section("Board Side")
        for w in (self._btn_top_view, self._btn_bottom_view):
            flow.addWidget(w)

        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(False)
        self._btn_fit.setEnabled(False)
        self._btn_fit_sel.setEnabled(False)
        self._btn_clear.setEnabled(False)
        self._btn_dnp_view.setEnabled(False)
        self._btn_save_state.setEnabled(False)
        self._btn_open_state.setEnabled(False)
        self._btn_top_view.setEnabled(False)
        self._btn_bottom_view.setEnabled(False)

        # Central layout — wrapping toolbar on top, splitter below
        splitter = QSplitter(Qt.Orientation.Vertical)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(toolbar_widget)
        central_layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

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
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter by designator or name…")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.setMaximumWidth(260)
        bom_header.addWidget(self._search_box)
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

        self._lbl_progress = QLabel("")
        self.statusBar().addPermanentWidget(self._lbl_progress)
        self.statusBar().showMessage("Ready. Open a .PcbDoc or .PrjPcb file.")

    def _connect_signals(self) -> None:
        self._btn_open.clicked.connect(self._on_open_file)
        self._btn_load_prjpcb.clicked.connect(self._on_load_prjpcb)
        self._btn_prev.clicked.connect(self._on_prev)
        self._btn_next.clicked.connect(self._on_next)
        self._btn_fit.clicked.connect(self._viewer.fit_to_view)
        self._btn_fit_sel.clicked.connect(self._zoom_to_selection)
        self._btn_auto_zoom.toggled.connect(self._on_auto_zoom_toggled)
        self._btn_labels.toggled.connect(lambda _checked: self._update_viewer())
        self._btn_clear.clicked.connect(self._on_clear_selection)
        self._btn_dnp_view.toggled.connect(self._on_dnp_view_toggled)
        self._btn_save_state.clicked.connect(self._on_save_state)
        self._btn_open_state.clicked.connect(self._on_open_state)
        self._btn_auto_save.toggled.connect(self._on_auto_save_toggled)
        self._viewer.double_clicked_item.connect(self._on_viewer_double_click)
        self._viewer.clicked_item.connect(self._on_viewer_click)
        self._btn_top_view.clicked.connect(self._on_view_top)
        self._btn_bottom_view.clicked.connect(self._on_view_bottom)
        self._btn_hide_fitted.toggled.connect(self._apply_row_filters)
        self._search_box.textChanged.connect(self._apply_row_filters)
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
        if not self._confirm_discard_unsaved():
            return
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
        self._dnp_view = False
        self._btn_dnp_view.blockSignals(True)
        self._btn_dnp_view.setChecked(False)
        self._btn_dnp_view.blockSignals(False)
        self._view_side = "TOP"
        self._lbl_filename.setText(model.board_name)
        self._undo.clear()
        self._redo.clear()

        # Auto-load companion files sitting next to the board
        auto_notes = self._load_companion_files(model)

        self._rebuild_active_bom()
        self._viewer.set_flip(False)
        self._update_viewer()
        QTimer.singleShot(0, self._viewer.fit_to_view)
        self._btn_fit.setEnabled(True)
        self._btn_fit_sel.setEnabled(True)
        self._btn_clear.setEnabled(True)
        self._btn_dnp_view.setEnabled(True)
        self._btn_save_state.setEnabled(True)
        self._btn_open_state.setEnabled(True)
        self._btn_top_view.setEnabled(True)
        self._btn_bottom_view.setEnabled(True)
        self._btn_top_view.setChecked(True)
        self._update_navigation()
        self._update_progress()
        self._add_recent_file(model.filepath)
        msg = f"Loaded {model.board_name}  |  {len(self._active_bom)} BOM groups"
        if auto_notes:
            msg += "  |  auto-loaded " + ", ".join(auto_notes)
        self.statusBar().showMessage(msg)

    def _load_companion_files(self, model: PcbModel) -> list[str]:
        """Load sibling .PrjPcb (DNP) and .popstate.json (progress) if present.

        Returns human-readable notes about what was loaded.
        """
        notes: list[str] = []
        self._dnp = frozenset()
        self._placement.clear()
        if model.filepath is None:
            return notes
        try:
            prjs = [p for p in model.filepath.parent.iterdir()
                    if p.suffix.lower() == ".prjpcb"]
        except OSError:
            prjs = []
        if len(prjs) == 1:  # only auto-load when unambiguous
            try:
                self._dnp = parse_prjpcb_dnp(prjs[0])
                notes.append(f"DNP from {prjs[0].name}")
            except Exception:
                pass
        state_path = model.filepath.with_suffix(".popstate.json")
        if state_path.exists():
            try:
                self._placement.load(state_path)
                notes.append(f"progress from {state_path.name}")
            except Exception:
                pass
        return notes

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
        # set_flip queues a full fit; queue the selection zoom behind it
        QTimer.singleShot(0, self._auto_zoom_if_enabled)

    def _on_view_bottom(self) -> None:
        self._view_side = "BOTTOM"
        self._viewer.set_flip(True)
        self._refresh_svg()
        QTimer.singleShot(0, self._auto_zoom_if_enabled)

    def _refresh_svg(self) -> None:
        if self._model is None:
            return
        self._update_viewer()

    def _on_bom_row_changed(self, row: int) -> None:
        if not self._active_bom or row < 0 or row == self._current_index:
            return
        self._exit_dnp_view()
        self._current_index = row
        self._update_navigation()
        self._update_viewer()
        self._auto_zoom_if_enabled()

    def _on_clear_selection(self) -> None:
        if self._model is None:
            return
        self._exit_dnp_view()
        self._current_index = -1
        self._bom_table.blockSignals(True)
        self._bom_table.clearSelection()
        self._bom_table.blockSignals(False)
        self._update_navigation()
        self._update_viewer()
        self._auto_zoom_if_enabled()

    def _exit_dnp_view(self) -> None:
        """Leave the whole-board DNP view (without re-triggering its toggle handler)."""
        if not self._dnp_view:
            return
        self._dnp_view = False
        self._btn_dnp_view.blockSignals(True)
        self._btn_dnp_view.setChecked(False)
        self._btn_dnp_view.blockSignals(False)

    def _on_dnp_view_toggled(self, checked: bool) -> None:
        if self._model is None:
            return
        self._dnp_view = checked
        if checked:
            # Whole-board DNP set drives the view — drop any single-row selection.
            self._current_index = -1
            self._bom_table.blockSignals(True)
            self._bom_table.clearSelection()
            self._bom_table.blockSignals(False)
            self._update_navigation()
            n = len(self._dnp)
            self.statusBar().showMessage(
                f"DNP view: {n} part(s) marked Do Not Fit"
                if n else
                "DNP view: no parts marked Do Not Fit — load a .PrjPcb for DNP data"
            )
        self._update_viewer()
        self._auto_zoom_if_enabled()

    def _on_viewer_double_click(self, item_x: float, item_y: float) -> None:
        if self._model is None:
            return
        hidden = self._model.hidden_designators_for_side(self._view_side)
        active = self._active_designators()
        if active is not None:
            # A row or the DNP view is active: only its refs, on the current side
            allowed: set[str] | None = set(active) - hidden
            exclude = None
        else:
            allowed = None
            exclude = hidden
        vx, vy = self._model.viewbox_origin
        desig = self._model.component_at_svg(item_x + vx, item_y + vy, exclude=exclude, allowed=allowed)
        if desig is None:
            return
        self._placement.toggle(desig)
        self._record_toggle(desig)
        self._update_viewer()
        self._update_bom_row_for(desig)
        self._auto_save_if_enabled()

    def _on_save_state(self) -> None:
        self._save_state_interactive()

    def _save_state_interactive(self) -> bool:
        """Save via file dialog. Returns True if saved."""
        default = ""
        if self._model and self._model.filepath:
            default = str(self._model.filepath.with_suffix(".popstate.json"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Population State", default,
            "Population State (*.json);;All Files (*)",
        )
        if not path:
            return False
        self._placement.save(Path(path))
        self.statusBar().showMessage(f"Saved: {Path(path).name}")
        return True

    def _on_open_state(self) -> None:
        if not self._confirm_discard_unsaved():
            return
        default_dir = ""
        if self._model and self._model.filepath:
            default_dir = str(self._model.filepath.parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Population State", default_dir,
            "Population State (*.json);;All Files (*)",
        )
        if path:
            self._placement.load(Path(path))
            self._undo.clear()
            self._redo.clear()
            self._update_viewer()
            self._update_bom_colors()
            self.statusBar().showMessage(f"Loaded: {Path(path).name}")

    def _confirm_discard_unsaved(self) -> bool:
        """Prompt when placement progress is unsaved. Returns True to proceed."""
        if not self._placement.is_modified or self._btn_auto_save.isChecked():
            return True
        resp = QMessageBox.question(
            self,
            "Unsaved Progress",
            "Placement progress has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if resp == QMessageBox.StandardButton.Cancel:
            return False
        if resp == QMessageBox.StandardButton.Save:
            return self._save_state_interactive()
        return True

    def _on_auto_save_toggled(self, checked: bool) -> None:
        self._settings.setValue("autoSave", checked)
        if checked:
            self._auto_save_if_enabled()

    def _auto_save_if_enabled(self) -> None:
        if (
            self._btn_auto_save.isChecked()
            and self._model is not None
            and self._model.filepath is not None
        ):
            try:
                self._placement.save(self._model.filepath.with_suffix(".popstate.json"))
            except OSError:
                self.statusBar().showMessage("Auto-save failed")

    # ------------------------------------------------------------------
    # Recent files
    # ------------------------------------------------------------------

    _MAX_RECENT = 8

    def _recent_files(self) -> list[str]:
        val = self._settings.value("recentFiles", [])
        if isinstance(val, str):  # QSettings can collapse a 1-item list to str
            val = [val]
        return [p for p in (val or []) if p]

    def _add_recent_file(self, path: Path | None) -> None:
        if path is None:
            return
        paths = self._recent_files()
        s = str(path)
        if s in paths:
            paths.remove(s)
        paths.insert(0, s)
        self._settings.setValue("recentFiles", paths[: self._MAX_RECENT])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        paths = self._recent_files()
        if not paths:
            act = QAction("(No recent files)", self)
            act.setEnabled(False)
            self._recent_menu.addAction(act)
            return
        for p in paths:
            act = QAction(Path(p).name, self)
            act.setToolTip(p)
            act.triggered.connect(lambda checked=False, pp=p: self._open_recent(pp))
            self._recent_menu.addAction(act)

    def _open_recent(self, path_str: str) -> None:
        p = Path(path_str)
        if not p.exists():
            self._show_error("File Not Found", f"File no longer exists:\n{p}")
            return
        self._load_file(p)

    # ------------------------------------------------------------------
    # Undo / redo of placement toggles
    # ------------------------------------------------------------------

    def _record_toggle(self, desig: str) -> None:
        self._undo.append(desig)
        del self._undo[:-200]
        self._redo.clear()

    def _undo_toggle(self) -> None:
        self._pop_toggle(self._undo, self._redo, "Undo")

    def _redo_toggle(self) -> None:
        self._pop_toggle(self._redo, self._undo, "Redo")

    def _pop_toggle(self, source: list[str], dest: list[str], verb: str) -> None:
        if not source or self._model is None:
            return
        desig = source.pop()
        dest.append(desig)
        now_placed = self._placement.toggle(desig)
        self._update_viewer()
        self._update_bom_row_for(desig)
        self._auto_save_if_enabled()
        self.statusBar().showMessage(
            f"{verb}: {desig} is now {'placed' if now_placed else 'unplaced'}"
        )

    # ------------------------------------------------------------------
    # Progress, zoom-to-selection, identify
    # ------------------------------------------------------------------

    def _update_progress(self) -> None:
        if self._model is None:
            self._lbl_progress.setText("")
            return
        effective = self._placement.placed | self._dnp
        total = 0
        done = 0
        for entry in self._model.bom:
            total += len(entry.designators)
            done += sum(1 for d in entry.designators if d in effective)
        pct = (100.0 * done / total) if total else 0.0
        self._lbl_progress.setText(f"Fitted {done}/{total} ({pct:.1f}%)")

    def _active_designators(self) -> list[str] | None:
        """Designators currently highlighted on the board: every DNP part when the
        DNP view is on, otherwise the selected BOM row's components (or None)."""
        if self._model is None:
            return None
        if self._dnp_view:
            if not self._dnp:
                return None
            return [d for _entry, visible in self._active_bom for d in visible if d in self._dnp]
        if 0 <= self._current_index < len(self._active_bom):
            return self._active_bom[self._current_index][1]
        return None

    def _selection_bounds(self) -> tuple[float, float, float, float] | None:
        """Union bbox (SVG coords) of the active components on the visible side."""
        active = self._active_designators()
        if self._model is None or active is None:
            return None
        hidden = self._model.hidden_designators_for_side(self._view_side)
        boxes = [
            self._model.component_bounds[d]
            for d in active
            if d not in hidden and d in self._model.component_bounds
        ]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def _zoom_to_selection(self) -> None:
        bounds = self._selection_bounds()
        if bounds is None or self._model is None:
            return
        vx, vy = self._model.viewbox_origin
        x0, y0, x1, y1 = bounds
        pad = max(x1 - x0, y1 - y0) * 0.15 + 1.0
        self._viewer.zoom_to_item_rect(QRectF(
            x0 - vx - pad, y0 - vy - pad,
            (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad,
        ))

    def _on_auto_zoom_toggled(self, checked: bool) -> None:
        if checked:
            self._zoom_to_selection()

    def _auto_zoom_if_enabled(self) -> None:
        if self._btn_auto_zoom.isChecked():
            if self._active_designators() is not None:
                self._zoom_to_selection()
            else:
                self._viewer.fit_to_view()

    def _row_index_of(self, desig: str) -> int | None:
        for row, (_entry, visible) in enumerate(self._active_bom):
            if desig in visible:
                return row
        return None

    def _on_viewer_click(self, item_x: float, item_y: float) -> None:
        """Single click on the board: identify the component in the status bar + BOM."""
        if self._model is None:
            return
        hidden = self._model.hidden_designators_for_side(self._view_side)
        vx, vy = self._model.viewbox_origin
        desig = self._model.component_at_svg(item_x + vx, item_y + vy, exclude=set(hidden))
        if desig is None:
            return
        row = self._row_index_of(desig)
        parts = [desig]
        if row is not None:
            entry = self._active_bom[row][0]
            parts.append(entry.comment)
            if entry.description:
                parts.append(entry.description)
        msg = " — ".join(parts)
        if row is not None:
            msg += f"  (row {row + 1})"
            self._flash_bom_row(row)
        self.statusBar().showMessage(msg, 6000)

    def _flash_bom_row(self, row: int) -> None:
        """Scroll to a BOM row and briefly highlight it without changing the selection."""
        item = self._bom_table.item(row, 0)
        if item is None:
            return
        self._bom_table.scrollToItem(item)
        brush = QBrush(QColor(255, 200, 0, 130))
        self._bom_table.blockSignals(True)
        for col in range(7):
            it = self._bom_table.item(row, col)
            if it:
                it.setBackground(brush)
        self._bom_table.blockSignals(False)
        QTimer.singleShot(800, lambda: self._unflash_bom_row(row))

    def _unflash_bom_row(self, row: int) -> None:
        if 0 <= row < len(self._active_bom):
            self._bom_table.blockSignals(True)
            self._refresh_bom_row(row)
            self._bom_table.blockSignals(False)

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
        self._exit_dnp_view()
        self._current_index = index
        self._bom_table.blockSignals(True)
        self._bom_table.selectRow(index)
        self._bom_table.scrollToItem(self._bom_table.item(index, 0))
        self._bom_table.blockSignals(False)
        self._update_navigation()
        self._update_viewer()
        self._auto_zoom_if_enabled()

    def _placed_visible(self) -> frozenset[str]:
        """Placed designators to render a placed border for — the active set, current side."""
        active = self._active_designators()
        if self._model is None or active is None:
            return frozenset()
        hidden = self._model.hidden_designators_for_side(self._view_side)
        return frozenset(d for d in active if d in self._placement.placed and d not in hidden)

    def _dnp_visible(self) -> frozenset[str]:
        """DNP designators to render an X marker for — the active set, current side."""
        active = self._active_designators()
        if self._model is None or not self._dnp or active is None:
            return frozenset()
        hidden = self._model.hidden_designators_for_side(self._view_side)
        return frozenset(d for d in active if d in self._dnp and d not in hidden)

    def _update_viewer(self) -> None:
        if self._model is None:
            return
        hide = self._model.hidden_designators_for_side(self._view_side)
        visible = self._active_designators()
        if visible is not None:
            svg = self._model.svg_for_designators(set(visible), hide or None)
        else:
            svg = self._model.side_filtered_svg(self._view_side)
        placed = self._placed_visible()
        if placed:
            svg = self._model.add_placed_markers(svg, placed)
        dnp = self._dnp_visible()
        if dnp:
            svg = self._model.add_dnp_markers(svg, dnp)
        if visible is not None and self._btn_labels.isChecked():
            svg = self._model.add_designator_labels(
                svg, set(visible) - hide, mirrored=(self._view_side == "BOTTOM")
            )
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
            name_item = QTableWidgetItem(entry.comment)
            if entry.description:
                name_item.setToolTip(entry.description)
            self._bom_table.setItem(row, 4, name_item)
            self._bom_table.setItem(row, 5, QTableWidgetItem(_refs_html(entry.top_refs, placed, dnp)))
            self._bom_table.setItem(row, 6, QTableWidgetItem(_refs_html(entry.bot_refs, placed, dnp)))
            _apply_row_colors(self._bom_table, row, entry.top_refs, entry.bot_refs, visible, placed, dnp)
        self._bom_table.blockSignals(False)
        self._apply_default_column_widths()
        self._bom_table.resizeRowsToContents()
        self._apply_row_filters()

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
        self._update_progress()
        self._apply_row_filters()

    def _update_bom_row_for(self, desig: str) -> None:
        """Refresh only the BOM row containing the given designator (toggle hot path)."""
        row = self._row_index_of(desig)
        if row is None:
            return
        self._bom_table.blockSignals(True)
        self._refresh_bom_row(row)
        self._bom_table.blockSignals(False)
        self._bom_table.resizeRowToContents(row)
        self._update_progress()
        if self._btn_hide_fitted.isChecked() or self._search_box.text().strip():
            self._apply_row_filters()

    def _row_is_complete(self, visible: list[str]) -> bool:
        """True when every visible designator in the row is placed or DNP."""
        effective = self._placement.placed | self._dnp
        return bool(visible) and all(d in effective for d in visible)

    def _apply_row_filters(self, *_args) -> None:
        """Hide BOM rows per the 'Hide Fitted' toggle and the search box."""
        hide_fitted = self._btn_hide_fitted.isChecked()
        query = self._search_box.text().strip().lower()
        for row, (entry, visible) in enumerate(self._active_bom):
            hidden = (hide_fitted and self._row_is_complete(visible)) or (
                bool(query) and not _entry_matches(entry, query)
            )
            self._bom_table.setRowHidden(row, hidden)

    # ------------------------------------------------------------------
    # Keyboard navigation
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Undo):
            self._undo_toggle()
        elif event.matches(QKeySequence.StandardKey.Redo):
            self._redo_toggle()
        elif event.key() == Qt.Key.Key_Down and not self._bom_table.hasFocus():
            self._on_next()
        elif event.key() == Qt.Key.Key_Up and not self._bom_table.hasFocus():
            self._on_prev()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if self._confirm_discard_unsaved():
            event.accept()
        else:
            event.ignore()

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
