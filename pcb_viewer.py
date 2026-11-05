"""
pcb_viewer.py — QGraphicsView-based SVG display widget with zoom and pan.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

_ZOOM_FACTOR = 1.15
_ZOOM_MIN = 0.02
_ZOOM_MAX = 80.0

# Net-label screen sizing (pixels, independent of zoom).
_LABEL_MIN_PX = 7.0    # below this a label is hidden until you zoom in further
_LABEL_MAX_PX = 15.0   # cap so labels never dominate when zoomed way in
_LABEL_COLOR = "#ffffff"


class _PinLabel(QGraphicsSimpleTextItem):
    """A net-name label that keeps a constant on-screen size and centres on its
    pad. ItemIgnoresTransformations detaches the glyph size from the view zoom;
    the owner resizes/hides it per zoom so it always fits inside the pad."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setBrush(QBrush(QColor(_LABEL_COLOR)))
        font = QFont("Segoe UI")
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)
        self.setZValue(1000)

    def boundingRect(self) -> QRectF:
        br = super().boundingRect()
        return br.translated(-br.width() / 2, -br.height() / 2)

    def paint(self, painter, option, widget=None) -> None:
        br = super().boundingRect()
        painter.translate(-br.width() / 2, -br.height() / 2)
        super().paint(painter, option, widget)


class PcbViewer(QGraphicsView):
    """
    Zoomable, pannable SVG board viewer.

    - Mouse wheel: zoom centered on cursor
    - Left-button drag: pan
    - Ctrl+0: fit board to view
    - Click (no drag): emits clicked_item(item_x, item_y) in SVG item space
    - Double-click: emits double_clicked_item(item_x, item_y) in SVG item space
    """

    double_clicked_item = Signal(float, float)
    clicked_item = Signal(float, float)

    # A press/release pair closer together than this is a click, not a pan.
    _CLICK_SLOP_PX = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._svg_item: QGraphicsSvgItem | None = None
        self._renderer: QSvgRenderer | None = None
        self._cumulative_scale: float = 1.0
        self._flipped: bool = False
        self._press_pos: QPoint | None = None
        # Zoom-aware net labels: each entry is (_PinLabel, pad_w, pad_h, side).
        self._pin_labels: list[tuple[_PinLabel, float, float, str]] = []
        self._pin_labels_enabled: bool = False

        self.setScene(self._scene)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setStyleSheet("background-color: #1e1e1e;")

    def load_svg(self, svg: str) -> None:
        """Load or replace the displayed SVG. Preserves zoom/pan across BOM selection changes."""
        svg_bytes = QByteArray(svg.encode("utf-8"))

        new_renderer = QSvgRenderer(svg_bytes)
        if not new_renderer.isValid():
            return
        self._renderer = new_renderer

        if self._svg_item is None:
            self._svg_item = QGraphicsSvgItem()
            # Cache the rendered SVG as a pixmap at device resolution so panning
            # blits the cache instead of re-rendering the whole SVG every frame.
            # The cache is regenerated automatically when the zoom level changes.
            self._svg_item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
            self._scene.addItem(self._svg_item)

        self._svg_item.setSharedRenderer(self._renderer)
        self._svg_item.update()  # invalidate the device-coordinate cache for the new content
        self._apply_flip()

    def set_flip(self, flipped: bool) -> None:
        """Horizontally mirror the board (use for bottom-side view)."""
        self._flipped = flipped
        self._apply_flip()
        QTimer.singleShot(0, self._fit_to_view)

    def fit_to_view(self) -> None:
        self._fit_to_view()

    def zoom_to_item_rect(self, rect: QRectF) -> None:
        """Fit the view to a rectangle given in SVG item coordinates."""
        if self._svg_item is None or rect.isEmpty():
            return
        scene_rect = self._svg_item.mapRectToScene(rect)
        self.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._cumulative_scale = 1.0
        self._update_pin_labels()

    # ------------------------------------------------------------------
    # Zoom-aware net labels
    # ------------------------------------------------------------------

    def set_pin_labels(self, specs: list[tuple[str, float, float, float, float, str]]) -> None:
        """Install net-name labels. Each spec is
        (text, x, y, pad_w, pad_h, side) in SVG-item coordinates."""
        for item, *_ in self._pin_labels:
            item.setParentItem(None)
            self._scene.removeItem(item)
        self._pin_labels = []
        if self._svg_item is None:
            return
        for text, x, y, w, h, side in specs:
            item = _PinLabel(text)
            item.setParentItem(self._svg_item)
            item.setPos(x, y)
            item.setVisible(False)
            self._pin_labels.append((item, w, h, side))
        self._update_pin_labels()

    def set_pin_labels_enabled(self, enabled: bool) -> None:
        self._pin_labels_enabled = bool(enabled)
        self._update_pin_labels()

    def _update_pin_labels(self) -> None:
        """Resize/show/hide each label from the pad's current on-screen size."""
        if not self._pin_labels:
            return
        enabled = self._pin_labels_enabled
        side_now = "BOTTOM" if self._flipped else "TOP"
        scale = abs(self.transform().m11())  # device px per scene unit (mm)
        for item, w, h, side in self._pin_labels:
            if not enabled or (side != side_now and side != "BOTH"):
                item.setVisible(False)
                continue
            pad_w_px = w * scale
            pad_h_px = h * scale
            n = max(len(item.text()), 1)
            # Largest pixel size that still fits the pad in both axes (0.58 is an
            # approximate average glyph width / height ratio for sans-serif).
            fit_w = (pad_w_px * 0.92) / (n * 0.58)
            fit_h = pad_h_px * 0.78
            px = min(fit_w, fit_h, _LABEL_MAX_PX)
            if px < _LABEL_MIN_PX:
                item.setVisible(False)
            else:
                font = item.font()
                font.setPixelSize(max(1, round(px)))
                item.setFont(font)
                item.setVisible(True)

    def _apply_flip(self) -> None:
        if self._svg_item is None:
            return
        if self._flipped:
            w = self._svg_item.boundingRect().width()
            t = QTransform()
            t.translate(w, 0)
            t.scale(-1, 1)
            self._svg_item.setTransform(t)
        else:
            self._svg_item.setTransform(QTransform())
        self._scene.setSceneRect(self._svg_item.sceneBoundingRect())

    def _fit_to_view(self) -> None:
        if self._svg_item is None:
            return
        self.resetTransform()
        self._cumulative_scale = 1.0
        rect = self._svg_item.sceneBoundingRect()
        self._scene.setSceneRect(rect)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_pin_labels()

    # ------------------------------------------------------------------
    # Event overrides
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._svg_item is not None:
            self._fit_to_view()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        factor = _ZOOM_FACTOR if delta > 0 else 1.0 / _ZOOM_FACTOR
        new_scale = self._cumulative_scale * factor
        if _ZOOM_MIN <= new_scale <= _ZOOM_MAX:
            self._cumulative_scale = new_scale
            self.scale(factor, factor)
            self._update_pin_labels()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._press_pos is not None
            and self._svg_item is not None
            and (event.pos() - self._press_pos).manhattanLength() < self._CLICK_SLOP_PX
        ):
            scene_pos = self.mapToScene(event.pos())
            item_pos = self._svg_item.mapFromScene(scene_pos)
            self.clicked_item.emit(item_pos.x(), item_pos.y())
        self._press_pos = None

    def mouseDoubleClickEvent(self, event) -> None:
        if self._svg_item is not None and event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            item_pos = self._svg_item.mapFromScene(scene_pos)
            self.double_clicked_item.emit(item_pos.x(), item_pos.y())
        # Do not call super — removes the old fit-to-view-on-double-click behaviour

    def keyPressEvent(self, event) -> None:
        if (
            event.key() == Qt.Key.Key_0
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._fit_to_view()
        else:
            super().keyPressEvent(event)
