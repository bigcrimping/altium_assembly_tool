"""
pcb_viewer.py — QGraphicsView-based SVG display widget with zoom and pan.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

_ZOOM_FACTOR = 1.15
_ZOOM_MIN = 0.02
_ZOOM_MAX = 80.0


class PcbViewer(QGraphicsView):
    """
    Zoomable, pannable SVG board viewer.

    - Mouse wheel: zoom centered on cursor
    - Left-button drag: pan
    - Ctrl+0: fit board to view
    - Double-click: emits double_clicked_item(item_x, item_y) in SVG item space
    """

    double_clicked_item = Signal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._svg_item: QGraphicsSvgItem | None = None
        self._renderer: QSvgRenderer | None = None
        self._cumulative_scale: float = 1.0
        self._flipped: bool = False

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
