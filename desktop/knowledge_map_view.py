"""Native Qt knowledge-map canvas with domain-independent DAG layout."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)


NODE_WIDTH = 220.0
NODE_HEIGHT = 82.0
X_GAP = 100.0
Y_GAP = 46.0


def layered_positions(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    """Return deterministic positions; cycles and dangling edges degrade safely."""
    ids = [str(item.get("id") or "") for item in nodes if item.get("id")]
    known = set(ids)
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in ids}
    for edge in edges:
        source = str(edge.get("source_id") or edge.get("prerequisite_id") or "")
        target = str(edge.get("target_id") or edge.get("dependent_id") or "")
        if source not in known or target not in known or source == target:
            continue
        if target not in outgoing[source]:
            outgoing[source].append(target)
            indegree[target] += 1

    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    level = {node_id: 0 for node_id in queue}
    visited: set[str] = set()
    while queue:
        source = queue.popleft()
        visited.add(source)
        for target in sorted(outgoing[source]):
            level[target] = max(level.get(target, 0), level[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)

    # Candidate or legacy data may contain a cycle. Keep it visible in a final
    # column rather than inventing or deleting relationships in the UI.
    fallback_level = max(level.values(), default=-1) + 1
    for node_id in ids:
        if node_id not in visited:
            level[node_id] = fallback_level

    columns: dict[int, list[str]] = defaultdict(list)
    for node_id in ids:
        columns[level[node_id]].append(node_id)
    positions: dict[str, tuple[float, float]] = {}
    for column in sorted(columns):
        for row, node_id in enumerate(sorted(columns[column])):
            positions[node_id] = (column * (NODE_WIDTH + X_GAP), row * (NODE_HEIGHT + Y_GAP))
    return positions


class KnowledgeMapView(QGraphicsView):
    node_selected = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#f4f1e9"))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._node_items: dict[str, QGraphicsRectItem] = {}

    def set_graph(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        scene = self.scene()
        scene.clear()
        self._node_items.clear()
        positions = layered_positions(nodes, edges)

        for node in nodes:
            node_id = str(node.get("id") or "")
            if node_id not in positions:
                continue
            x, y = positions[node_id]
            layer = str(node.get("layer") or "material")
            status = str(node.get("status") or "confirmed")
            fill = QColor("#fffdf8")
            border = QColor("#397a64")
            if layer == "model_common":
                fill, border = QColor("#f2ecfa"), QColor("#79589f")
            if status not in {"confirmed", "active"}:
                fill, border = QColor("#fff5de"), QColor("#b47b22")
            item = scene.addRect(QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT), QPen(border, 2), fill)
            item.setPos(x, y)
            item.setData(0, node)
            item.setToolTip(str(node.get("explanation") or node.get("name") or ""))
            title = QGraphicsTextItem(str(node.get("name") or "未命名概念"), item)
            title.setDefaultTextColor(QColor("#243b33"))
            title.setTextWidth(NODE_WIDTH - 24)
            title.setPos(12, 8)
            meta = QGraphicsTextItem(f"{layer} · {status}", item)
            meta.setDefaultTextColor(QColor("#66766f"))
            meta.setPos(12, 48)
            self._node_items[node_id] = item

        # Add edges after calculating endpoints but keep them visually behind nodes.
        for edge in edges:
            source_id = str(edge.get("source_id") or edge.get("prerequisite_id") or "")
            target_id = str(edge.get("target_id") or edge.get("dependent_id") or "")
            source = self._node_items.get(source_id)
            target = self._node_items.get(target_id)
            if source is None or target is None or source is target:
                continue
            source_rect = source.sceneBoundingRect()
            target_rect = target.sceneBoundingRect()
            start = QPointF(source_rect.right(), source_rect.center().y())
            end = QPointF(target_rect.left(), target_rect.center().y())
            path = QPainterPath(start)
            offset = max(35.0, abs(end.x() - start.x()) * 0.45)
            path.cubicTo(start + QPointF(offset, 0), end - QPointF(offset, 0), end)
            relation = str(edge.get("relation") or "related")
            color = QColor("#397a64") if relation in {"contains", "prerequisite"} else QColor("#8b8175")
            line = QGraphicsPathItem(path)
            line.setPen(QPen(color, 2))
            line.setZValue(-1)
            line.setToolTip(f"{relation}: {source_id} → {target_id}")
            line.setData(0, {"_kind": "edge", **edge})
            scene.addItem(line)

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-60, -60, 60, 60))
        if nodes:
            self.fitInView(scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        item = self.itemAt(event.position().toPoint())
        while item is not None:
            data = item.data(0)
            if isinstance(data, dict):
                self.node_selected.emit(data)
                break
            item = item.parentItem()
        super().mousePressEvent(event)
