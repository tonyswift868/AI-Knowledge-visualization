"""Native Qt knowledge-map canvas with a readable, domain-independent DAG layout."""

from __future__ import annotations

from collections import defaultdict, deque
import math
from typing import Any, Mapping

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)


NODE_WIDTH = 236.0
NODE_MIN_HEIGHT = 100.0
X_GAP = 76.0
Y_GAP = 34.0
COMPONENT_X_GAP = 110.0
COMPONENT_Y_GAP = 82.0
MIN_INITIAL_SCALE = 0.68
MAX_INITIAL_SCALE = 1.0

LAYER_LABELS = {
    "material": "资料层",
    "model_common": "通用模型层",
    "theme": "主题层",
    "concept": "概念层",
}
STATUS_LABELS = {
    "confirmed": "已确认",
    "active": "生效",
    "pending": "待审核",
    "needs_review": "需复核",
    "rejected": "已拒绝",
    "staged": "暂存",
}
TYPE_LABELS = {
    "theme": "主题",
    "concept": "概念",
    "model": "模型",
}
RELATION_LABELS = {
    "contains": "包含",
    "prerequisite": "前置依赖",
    "related": "相关",
    "contrast": "对比",
    "depends_on": "依赖",
}


def _node_dimensions(node: Mapping[str, Any]) -> tuple[float, float]:
    """Estimate a stable card size from readable title/summary content."""

    title, explanation = _node_display_content(node)
    title_lines = max(1, min(3, math.ceil(len(title) / 18)))
    summary_lines = min(4, math.ceil(len(explanation) / 31)) if explanation else 0
    # Card metadata occupies one line; dynamic title/summary lines avoid
    # clipping Chinese labels while keeping large maps compact enough to pan.
    height = max(
        NODE_MIN_HEIGHT,
        18.0 + title_lines * 23.0 + summary_lines * 18.0 + 24.0,
    )
    return NODE_WIDTH, height


def _node_display_content(node: Mapping[str, Any]) -> tuple[str, str]:
    """Return non-empty, evidence-backed card text for any map payload."""

    evidence = node.get("evidence") or node.get("source_evidence") or []
    fallback = ""
    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        fallback = str(item.get("heading") or item.get("quote") or "").strip()
        if fallback:
            break
    fallback = " ".join(fallback.split())
    title = str(node.get("name") or node.get("label") or "").strip()
    if not title:
        title = (fallback[:96] + "…") if len(fallback) > 96 else fallback
    if not title:
        title = str(node.get("id") or "未命名概念").strip() or "未命名概念"
    summary = str(node.get("explanation") or node.get("summary") or "").strip()
    if not summary:
        summary = fallback[:320]
    return title, summary


def _edge_endpoints(edge: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(edge.get("source_id") or edge.get("prerequisite_id") or ""),
        str(edge.get("target_id") or edge.get("dependent_id") or ""),
    )


def _component_levels(
    component: list[str],
    outgoing: Mapping[str, list[str]],
) -> dict[str, int]:
    component_set = set(component)
    indegree = {node_id: 0 for node_id in component}
    for source in component:
        for target in outgoing.get(source, []):
            if target in component_set:
                indegree[target] += 1
    queue = deque(sorted(node_id for node_id in component if indegree[node_id] == 0))
    levels = {node_id: 0 for node_id in queue}
    visited: set[str] = set()
    while queue:
        source = queue.popleft()
        visited.add(source)
        for target in sorted(outgoing.get(source, [])):
            if target not in component_set:
                continue
            levels[target] = max(levels.get(target, 0), levels[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    # Candidate data can contain a cycle. Keep cyclic nodes visible in a
    # deterministic final column instead of silently dropping a relation.
    fallback_level = max(levels.values(), default=-1) + 1
    for node_id in component:
        if node_id not in visited:
            levels[node_id] = fallback_level
    return levels


def _component_is_cyclic(component: list[str], outgoing: Mapping[str, list[str]]) -> bool:
    component_set = set(component)
    indegree = {node_id: 0 for node_id in component}
    for source in component:
        for target in outgoing.get(source, []):
            if target in component_set:
                indegree[target] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        source = queue.popleft()
        visited += 1
        for target in outgoing.get(source, []):
            if target not in component_set:
                continue
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited != len(component)


def layered_positions(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    node_sizes: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, tuple[float, float]]:
    """Pack each disconnected DAG component into a balanced grid.

    A plain topological layout puts every isolated node in one very tall
    column. Components are laid out internally by DAG level, then packed into
    approximately square component rows/columns for readable large maps.
    """

    ids = list(dict.fromkeys(str(item.get("id") or "") for item in nodes if item.get("id")))
    known = set(ids)
    sizes = node_sizes or {}
    outgoing: dict[str, list[str]] = defaultdict(list)
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in ids}
    for edge in edges:
        source, target = _edge_endpoints(edge)
        if source not in known or target not in known or source == target:
            continue
        if target not in outgoing[source]:
            outgoing[source].append(target)
            adjacency[source].add(target)
            adjacency[target].add(source)

    components: list[list[str]] = []
    unseen = set(ids)
    while unseen:
        root = min(unseen)
        stack = [root]
        component: list[str] = []
        unseen.remove(root)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(adjacency[current], reverse=True):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))
    # Keep ordinary source-grounded components first; cyclic review candidates
    # occupy a deterministic trailing column just like the legacy layout.
    components.sort(
        key=lambda component: (
            1 if _component_is_cyclic(component, outgoing) else 0,
            component[0] if component else "",
        )
    )

    component_positions: list[tuple[list[str], dict[str, tuple[float, float]], float, float]] = []
    for component in components:
        levels = _component_levels(component, outgoing)
        columns: dict[int, list[str]] = defaultdict(list)
        for node_id in component:
            columns[levels[node_id]].append(node_id)
        local: dict[str, tuple[float, float]] = {}
        column_x = 0.0
        for column in sorted(columns):
            row_y = 0.0
            column_nodes = sorted(columns[column])
            column_width = max(
                (float((sizes.get(node_id) or (NODE_WIDTH, NODE_MIN_HEIGHT))[0]) for node_id in column_nodes),
                default=NODE_WIDTH,
            )
            for node_id in column_nodes:
                width, height = sizes.get(node_id) or (NODE_WIDTH, NODE_MIN_HEIGHT)
                local[node_id] = (column_x, row_y)
                row_y += float(height) + Y_GAP
            column_x += column_width + X_GAP
        width = max(
            (x + float((sizes.get(node_id) or (NODE_WIDTH, NODE_MIN_HEIGHT))[0]) for node_id, (x, _y) in local.items()),
            default=NODE_WIDTH,
        )
        height = max(
            (y + float((sizes.get(node_id) or (NODE_WIDTH, NODE_MIN_HEIGHT))[1]) for node_id, (_x, y) in local.items()),
            default=NODE_MIN_HEIGHT,
        )
        component_positions.append((component, local, width, height))

    if not component_positions:
        return {}
    grid_columns = max(1, min(len(component_positions), math.ceil(math.sqrt(len(component_positions)))))
    grid_rows = math.ceil(len(component_positions) / grid_columns)
    cell_widths = [0.0] * grid_columns
    cell_heights = [0.0] * grid_rows
    for index, (_component, _local, width, height) in enumerate(component_positions):
        cell_widths[index % grid_columns] = max(cell_widths[index % grid_columns], width)
        cell_heights[index // grid_columns] = max(cell_heights[index // grid_columns], height)
    x_offsets: list[float] = []
    cursor = 0.0
    for width in cell_widths:
        x_offsets.append(cursor)
        cursor += width + COMPONENT_X_GAP
    y_offsets: list[float] = []
    cursor = 0.0
    for height in cell_heights:
        y_offsets.append(cursor)
        cursor += height + COMPONENT_Y_GAP

    positions: dict[str, tuple[float, float]] = {}
    for index, (component, local, width, height) in enumerate(component_positions):
        grid_column = index % grid_columns
        grid_row = index // grid_columns
        x = x_offsets[grid_column] + max(0.0, (cell_widths[grid_column] - width) / 2.0)
        y = y_offsets[grid_row] + max(0.0, (cell_heights[grid_row] - height) / 2.0)
        for node_id in component:
            local_x, local_y = local[node_id]
            positions[node_id] = (x + local_x, y + local_y)
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
        node_sizes = {
            str(node.get("id") or ""): _node_dimensions(node)
            for node in nodes
            if node.get("id")
        }
        positions = layered_positions(nodes, edges, node_sizes)

        for node in nodes:
            node_id = str(node.get("id") or "")
            if node_id not in positions:
                continue
            x, y = positions[node_id]
            width, height = node_sizes.get(node_id, (NODE_WIDTH, NODE_MIN_HEIGHT))
            layer = str(node.get("layer") or "material")
            status = str(node.get("status") or "confirmed")
            layer_label = LAYER_LABELS.get(layer, layer or "资料层")
            status_label = STATUS_LABELS.get(status, status or "待审核")
            node_type = str(node.get("node_type") or node.get("kind") or "concept")
            node_type_label = TYPE_LABELS.get(node_type, node_type or "概念")
            fill = QColor("#fffdf8")
            border = QColor("#397a64")
            if layer == "model_common":
                fill, border = QColor("#f2ecfa"), QColor("#79589f")
            if status not in {"confirmed", "active"}:
                fill, border = QColor("#fff5de"), QColor("#b47b22")
            item = scene.addRect(QRectF(0, 0, width, height), QPen(border, 2), fill)
            item.setPos(x, y)
            item.setData(0, node)
            evidence_count = len(node.get("evidence") or node.get("source_evidence") or [])
            item.setToolTip(
                f"{layer_label} · {status_label}\n"
                f"证据 {evidence_count} 条\n"
                f"{_node_display_content(node)[1]}"
            )
            title_text, summary_text = _node_display_content(node)
            display_title = title_text if len(title_text) <= 54 else title_text[:51] + "…"
            # Add text directly to the scene.  PySide may delete a temporary
            # child QGraphicsTextItem when its Python wrapper falls out of
            # scope, leaving the rectangle visible but completely blank.
            # Scene ownership keeps every label alive for the map lifetime.
            title = scene.addText(display_title)
            title.setDefaultTextColor(QColor("#243b33"))
            title.setTextWidth(width - 24)
            title.setPos(x + 12, y + 8)
            title.setZValue(1)
            title.setData(0, node)
            title_font = title.font()
            title_font.setPointSizeF(12.0)
            title_font.setBold(True)
            title.setFont(title_font)
            title_lines = min(3, max(1, math.ceil(len(title_text) / 18)))
            if summary_text:
                display_summary = summary_text if len(summary_text) <= 124 else summary_text[:121] + "…"
                summary = scene.addText(display_summary)
                summary.setDefaultTextColor(QColor("#4f6259"))
                summary.setTextWidth(width - 24)
                summary.setPos(x + 12, y + 8 + title_lines * 23)
                summary.setZValue(1)
                summary.setData(0, node)
                summary_font = summary.font()
                summary_font.setPointSizeF(9.5)
                summary.setFont(summary_font)
            meta = scene.addText(f"{layer_label} · {node_type_label} · {status_label}")
            meta.setDefaultTextColor(QColor("#66766f"))
            meta.setPos(x + 12, y + height - 26)
            meta.setZValue(1)
            meta.setData(0, node)
            meta_font = meta.font()
            meta_font.setPointSizeF(8.5)
            meta.setFont(meta_font)
            self._node_items[node_id] = item

        # Add edges after calculating endpoints but keep them visually behind nodes.
        for edge in edges:
            source_id, target_id = _edge_endpoints(edge)
            source = self._node_items.get(source_id)
            target = self._node_items.get(target_id)
            if source is None or target is None or source is target:
                continue
            source_rect = source.sceneBoundingRect()
            target_rect = target.sceneBoundingRect()
            forward = target_rect.center().x() >= source_rect.center().x()
            start = QPointF(source_rect.right() if forward else source_rect.left(), source_rect.center().y())
            end = QPointF(target_rect.left() if forward else target_rect.right(), target_rect.center().y())
            distance = abs(end.x() - start.x())
            direction = 1.0 if forward else -1.0
            offset = max(35.0, distance * 0.45) * direction
            path = QPainterPath(start)
            path.cubicTo(start + QPointF(offset, 0), end - QPointF(offset, 0), end)
            relation = str(edge.get("relation") or "related")
            relation_label = RELATION_LABELS.get(relation, relation or "相关")
            color = QColor("#397a64") if relation in {"contains", "prerequisite", "depends_on"} else QColor("#8b8175")
            line = QGraphicsPathItem(path)
            line.setPen(QPen(color, 2))
            line.setZValue(-1)
            line.setToolTip(f"{relation_label}: {source_id} → {target_id}")
            line.setData(0, {"_kind": "edge", **edge})
            scene.addItem(line)
            label = scene.addText(relation_label)
            label.setDefaultTextColor(color)
            midpoint = path.pointAtPercent(0.5)
            label.setPos(midpoint + QPointF(5, -12))
            label.setZValue(-0.5)
            label.setData(0, {"_kind": "edge", **edge})

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-60, -60, 60, 60))
        self.resetTransform()
        if nodes:
            bounds = scene.sceneRect()
            viewport_size = self.viewport().size()
            if bounds.width() > 0 and bounds.height() > 0 and viewport_size.width() > 0 and viewport_size.height() > 0:
                fit_scale = min(
                    viewport_size.width() / bounds.width(),
                    viewport_size.height() / bounds.height(),
                ) * 0.92
                scale = max(MIN_INITIAL_SCALE, min(MAX_INITIAL_SCALE, fit_scale))
                self.scale(scale, scale)
            # Opening a large map in its geometric centre can land on a wide
            # empty gap between disconnected topic groups.  Start on the first
            # topic card (or first node) so the learner immediately sees a
            # populated title, explanation and its outgoing relations.
            focus_node = next(
                (
                    str(node.get("id") or "")
                    for node in nodes
                    if str(node.get("node_type") or node.get("kind") or "") == "theme"
                    and str(node.get("id") or "") in self._node_items
                ),
                next(iter(self._node_items), ""),
            )
            focus_item = self._node_items.get(focus_node)
            self.centerOn(focus_item.sceneBoundingRect().center() if focus_item is not None else bounds.center())

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
