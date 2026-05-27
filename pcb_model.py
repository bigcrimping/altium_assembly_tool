"""
pcb_model.py — Pure data layer: PCB loading, BOM grouping, SVG dimming.
No Qt imports. All altium_monkey interaction lives here.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
_MIL_TO_MM = 0.0254

# ComponentKind values to exclude from assembly BOM
_BOM_EXCLUDE_KIND_VALUES = frozenset({1, 2, 4, 5})

# Anchor data for PCB files where channel routing room definitions are missing.
# Altium stores component positions in sub-sheet template coordinates when the
# room definition for a channel block has been deleted. Without a stored room
# position, the only way to recover the correct placement is from a known
# reference component whose correct board position is known externally.
# Format: board_filename → {designator: (x_mm_from_board_origin, y_mm_from_board_origin)}
_CHANNEL_ROUTING_ANCHORS: dict[str, dict[str, tuple[float, float]]] = {
    "Oven_controller.PcbDoc": {"U2": (13.329, 94.066)},
}


@dataclass
class BomEntry:
    comment: str
    quantity: int
    designators: list[str]
    description: str = ""
    designator_layers: dict[str, str] = field(default_factory=dict)


class PcbModel:
    """
    Loads an Altium .PcbDoc or .PrjPcb file and provides BOM data + SVG rendering.

    Raises on load errors — the UI layer catches and shows dialogs.
    """

    def __init__(self) -> None:
        self._pcbdoc = None
        self._bom: list[BomEntry] = []
        self._base_svg: str = ""
        self.filepath: Path | None = None
        self._component_svg_bounds: dict[str, tuple[float, float, float, float]] = {}
        self._viewbox_x: float = 0.0
        self._viewbox_y: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str, *, progress=None) -> "PcbModel":
        """Load a .PcbDoc or .PrjPcb and return a ready PcbModel.

        progress: optional callable(percent: int, message: str) called at each stage.
        """
        def report(pct: int, msg: str) -> None:
            if progress:
                progress(pct, msg)

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        model = cls()
        model.filepath = path
        ext = path.suffix.lower()

        report(5, "Parsing PCB file…")
        if ext == ".pcbdoc":
            from altium_monkey import AltiumPcbDoc
            model._pcbdoc = AltiumPcbDoc.from_file(str(path))
        else:
            raise ValueError(f"Unsupported file type: {path.suffix!r}. Expected .PcbDoc")

        report(40, "Building component data…")
        model._build_bom()

        model._render_base_svg(report)

        report(100, "Done")
        return model

    @property
    def bom(self) -> list[BomEntry]:
        return self._bom

    @property
    def base_svg(self) -> str:
        return self._base_svg

    @property
    def board_name(self) -> str:
        return self.filepath.name if self.filepath else ""

    def svg_for_entry(self, entry: BomEntry | None) -> str:
        """Return the base SVG with non-selected components dimmed, or the base SVG if None."""
        if entry is None:
            return self._base_svg
        return _dim_unselected(self._base_svg, set(entry.designators))

    def hidden_designators_for_side(self, view_side: str) -> set[str]:
        """Return designators that should be hidden for the given view side (TOP or BOTTOM)."""
        if view_side == "BOTH":
            return set()
        hide_side = "BOTTOM" if view_side == "TOP" else "TOP"
        result: set[str] = set()
        for entry in self._bom:
            for d, layer in entry.designator_layers.items():
                layer_up = layer.upper()
                if "BOTTOM" in layer_up:
                    normalized = "BOTTOM"
                else:
                    normalized = "TOP"
                if normalized == hide_side:
                    result.add(d)
        return result

    def side_filtered_svg(self, view_side: str) -> str:
        """Return the base SVG with wrong-side components hidden."""
        hidden = self.hidden_designators_for_side(view_side)
        return _hide_components(self._base_svg, hidden)

    @property
    def viewbox_origin(self) -> tuple[float, float]:
        return (self._viewbox_x, self._viewbox_y)

    @property
    def component_bounds(self) -> dict[str, tuple[float, float, float, float]]:
        return self._component_svg_bounds

    def component_at_svg(
        self,
        x: float,
        y: float,
        exclude: set[str] | None = None,
        allowed: set[str] | None = None,
    ) -> str | None:
        """Return the designator whose bounding box contains (x, y).

        allowed: if given, only these designators are considered (takes priority over exclude).
        exclude: designators to skip when allowed is None.
        """
        for desig, (x0, y0, x1, y1) in self._component_svg_bounds.items():
            if allowed is not None:
                if desig not in allowed:
                    continue
            elif exclude and desig in exclude:
                continue
            if x0 <= x <= x1 and y0 <= y <= y1:
                return desig
        return None

    def add_placed_markers(self, svg: str, placed: frozenset[str]) -> str:
        """Overlay green border boxes on placed components."""
        return _add_placed_markers(svg, placed, self._component_svg_bounds)

    def add_dnp_markers(self, svg: str, dnp: frozenset[str]) -> str:
        """Overlay red X markers on DNP (No Fit) components."""
        return _add_dnp_markers(svg, dnp, self._component_svg_bounds)

    def svg_for_designators(self, designators: set[str], hide_designators: set[str] | None = None) -> str:
        """Return the base SVG dimmed to the given designator set, with hide_designators fully hidden."""
        if not designators and not hide_designators:
            return self._base_svg
        return _dim_unselected(self._base_svg, designators, hide_designators)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_bom(self) -> None:
        doc = self._pcbdoc
        groups: dict[str, list[str]] = {}
        descriptions: dict[str, str] = {}
        comp_layers: dict[str, str] = {}

        for comp in doc.components:
            kind = getattr(comp, "component_kind", None)
            if kind is not None:
                kind_val = kind.value if hasattr(kind, "value") else int(kind)
                if kind_val in _BOM_EXCLUDE_KIND_VALUES:
                    continue

            params = comp.parameters or {}
            comment = str(
                params.get("Comment")
                or params.get("Value")
                or params.get("Manufacturer Part Number")
                or params.get("Manufacturer_Part_Number")
                or params.get("MP")
                or params.get("Design Item ID")
                or getattr(comp, "description", None)
                or ""
            ).strip()
            if not comment:
                comment = "(No Comment)"

            groups.setdefault(comment, []).append(comp.designator)
            if comment not in descriptions:
                descriptions[comment] = str(getattr(comp, "description", "") or "").strip()
            comp_layers[comp.designator] = str(getattr(comp, "layer", "TOP") or "TOP").upper()

        bom: list[BomEntry] = []
        for comment, designators in groups.items():
            designators.sort(key=_natural_key)
            bom.append(BomEntry(
                comment=comment,
                quantity=len(designators),
                designators=designators,
                description=descriptions.get(comment, ""),
                designator_layers={d: comp_layers[d] for d in designators},
            ))

        bom.sort(key=lambda e: _natural_key(e.designators[0]))
        self._bom = bom

    def _render_base_svg(self, report=None) -> None:
        def _report(pct: int, msg: str) -> None:
            if report:
                report(pct, msg)

        from altium_monkey import PcbSvgRenderOptions

        options = PcbSvgRenderOptions(
            show_board_outline=True,
            include_view_box=True,
            drill_hole_mode="none",  # skip drill holes; vias are stripped anyway
        )

        _report(50, "Rendering SVG…")
        raw = self._pcbdoc.to_svg(options=options)

        _report(82, "Post-processing SVG…")
        processed = _post_process_svg(raw)
        processed = _add_pin1_markers(processed)

        _report(92, "Applying layout corrections…")
        processed = self._apply_channel_routing_corrections(processed)
        self._base_svg = _refit_viewbox_to_board_outline(processed)

        try:
            vb = ET.fromstring(self._base_svg).get("viewBox", "0 0 0 0").split()
            self._viewbox_x, self._viewbox_y = float(vb[0]), float(vb[1])
        except Exception:
            pass
        self._component_svg_bounds = _compute_component_svg_bounds(self._base_svg)

    def _apply_channel_routing_corrections(self, svg: str) -> str:
        """
        Fix components whose SVG positions are in the sub-sheet template coordinate
        system instead of the board coordinate system (missing room definition).
        """
        board_name = self.filepath.name if self.filepath else ""
        anchors = _CHANNEL_ROUTING_ANCHORS.get(board_name, {})
        if not anchors:
            return svg

        doc = self._pcbdoc
        bbox = doc.board.outline.bounding_box
        board_x_min, board_y_min, board_x_max, board_y_max = bbox

        comp_by_desig = {c.designator: c for c in doc.components}

        # Group outside-board components by hierarchical path
        outside_by_path: dict[str, list] = defaultdict(list)
        for comp in doc.components:
            x = comp.get_x_mils()
            y = comp.get_y_mils()
            if x < board_x_min or x > board_x_max or y < board_y_min or y > board_y_max:
                hp = (getattr(comp, "source_hierarchical_path", "") or "").strip()
                outside_by_path[hp].append(comp)

        # For each anchor, compute the correction and find affected components
        delta_to_designators: dict[tuple[float, float], set[str]] = defaultdict(set)

        for desig, (correct_x_mm, correct_y_mm) in anchors.items():
            anchor = comp_by_desig.get(desig)
            if anchor is None:
                continue
            correct_abs_x = doc.board.origin_x + correct_x_mm / _MIL_TO_MM
            correct_abs_y = doc.board.origin_y + correct_y_mm / _MIL_TO_MM
            delta_x_mils = correct_abs_x - anchor.get_x_mils()
            delta_y_mils = correct_abs_y - anchor.get_y_mils()
            delta_x_mm = delta_x_mils * _MIL_TO_MM
            delta_y_mm = -delta_y_mils * _MIL_TO_MM  # SVG Y axis is inverted

            anchor_hp = (getattr(anchor, "source_hierarchical_path", "") or "").strip()
            for comp in outside_by_path.get(anchor_hp, []):
                delta_to_designators[(delta_x_mm, delta_y_mm)].add(comp.designator)

        for (dx, dy), affected in delta_to_designators.items():
            svg = _translate_displaced_components(svg, affected, dx, dy)

        return svg


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _natural_key(designator: str) -> tuple[str, int, str]:
    m = re.match(r"([A-Za-z]+)(\d+)(.*)", designator.strip())
    if m is None:
        return (designator.upper(), -1, "")
    prefix, num, suffix = m.groups()
    return (prefix.upper(), int(num), suffix.upper())


def _post_process_svg(svg: str) -> str:
    """
    Single-pass SVG cleanup replacing four separate parse/serialize cycles:
    - Via drill holes (data-hole-owner="via")
    - Copper tracks, fills, and via rings (data-net-index present, no data-component)
    - Mechanical footprint elements such as courtyard (data-layer-role="mechanical" with data-component)
    - Silkscreen designator text strokes (data-primitive="text")
    """
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    for parent in root.iter():
        to_remove = []
        for child in parent:
            net_index = child.get("data-net-index")
            component = child.get("data-component")
            if (
                child.get("data-hole-owner") == "via"
                or (net_index is not None and component is None)
                or (child.get("data-layer-role") == "mechanical" and component is not None)
                or child.get("data-primitive") == "text"
                or child.get("data-layer-role") == "silkscreen"
            ):
                to_remove.append(child)
        for child in to_remove:
            parent.remove(child)

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _translate_displaced_components(
    svg: str,
    affected: set[str],
    delta_x_mm: float,
    delta_y_mm: float,
) -> str:
    """Apply translate(dx dy) to elements with data-component in the affected set."""
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    translate = f"translate({delta_x_mm:.4f} {delta_y_mm:.4f})"

    for elem in root.iter():
        comp = elem.get("data-component")
        if comp is not None and comp in affected:
            existing = elem.get("transform", "")
            elem.set("transform", f"{translate} {existing}".strip())

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _hide_components(svg: str, hide_set: set[str]) -> str:
    """Set opacity=0 for all elements whose data-component is in hide_set."""
    if not hide_set:
        return svg
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    for elem in root.iter():
        comp = elem.get("data-component")
        if comp is not None and comp in hide_set:
            elem.set("opacity", "0")
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _add_pin1_markers(svg: str) -> str:
    """Apply a diagonal stripe fill to pads with designator '1' to mark component pin 1."""
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    pin1_elems = [e for e in root.iter() if e.get("data-pad-designator") == "1"]
    if not pin1_elems:
        return svg

    defs_tag = f"{{{SVG_NS}}}defs"
    defs = root.find(defs_tag)
    if defs is None:
        defs = ET.Element(defs_tag)
        root.insert(0, defs)

    # linearGradient with objectBoundingBox is transform-independent, so it renders
    # correctly for both top-side and bottom-side pads (bottom pads live inside
    # mirrored groups that break patternUnits="userSpaceOnUse" in Qt's SVG renderer).
    grad = ET.SubElement(defs, f"{{{SVG_NS}}}linearGradient")
    grad.set("id", "pin1-stripe")
    grad.set("gradientUnits", "objectBoundingBox")
    grad.set("x1", "0")
    grad.set("y1", "0")
    grad.set("x2", "1")
    grad.set("y2", "1")

    for offset, color in [
        ("0",   "#ff8800"), ("0.2", "#ff8800"),
        ("0.2", "#ffffff"), ("0.4", "#ffffff"),
        ("0.4", "#ff8800"), ("0.6", "#ff8800"),
        ("0.6", "#ffffff"), ("0.8", "#ffffff"),
        ("0.8", "#ff8800"), ("1",   "#ff8800"),
    ]:
        stop = ET.SubElement(grad, f"{{{SVG_NS}}}stop")
        stop.set("offset", offset)
        stop.set("stop-color", color)

    for elem in pin1_elems:
        elem.set("fill", "url(#pin1-stripe)")

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _svg_path_bounds(d: str) -> tuple[float, float, float, float] | None:
    """Extract bounding box from SVG path d attribute (M, L, A, C commands)."""
    xs: list[float] = []
    ys: list[float] = []
    tokens = re.findall(
        r'[MLCQAZTSmqlcqazt]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d
    )
    cmd = ''
    j = 0
    while j < len(tokens):
        t = tokens[j]
        if t.isalpha():
            cmd = t.upper()
            j += 1
            continue
        try:
            if cmd in ('M', 'L'):
                xs.append(float(tokens[j])); ys.append(float(tokens[j + 1]))
                j += 2
            elif cmd == 'C':
                for k in range(3):
                    xs.append(float(tokens[j + k * 2]))
                    ys.append(float(tokens[j + k * 2 + 1]))
                j += 6
            elif cmd == 'A':
                xs.append(float(tokens[j + 5])); ys.append(float(tokens[j + 6]))
                j += 7
            else:
                j += 1
        except (IndexError, ValueError):
            j += 1
    return (min(xs), min(ys), max(xs), max(ys)) if xs and ys else None


def _refit_viewbox_to_board_outline(svg: str) -> str:
    """
    Update the SVG viewBox, width, and height to be centred on the board outline.

    This ensures the board centroid sits at exactly (W/2, H/2) in scene coordinates,
    so horizontal flip (used for bottom-side view) mirrors around the board centroid
    rather than an arbitrary SVG origin.
    """
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    bounds = None
    for elem in root.iter():
        if elem.get("data-feature") == "board-outline":
            bounds = _svg_path_bounds(elem.get("d", ""))
            break
    if bounds is None:
        return ET.tostring(root, encoding="unicode", xml_declaration=False)
    x0, y0, x1, y1 = bounds
    if x1 <= x0 or y1 <= y0:
        return ET.tostring(root, encoding="unicode", xml_declaration=False)
    pad = 1.0
    vw = x1 - x0 + 2 * pad
    vh = y1 - y0 + 2 * pad
    root.set("viewBox", f"{x0 - pad:.4f} {y0 - pad:.4f} {vw:.4f} {vh:.4f}")
    root.set("width", f"{vw:.4f}")
    root.set("height", f"{vh:.4f}")
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _elem_translate(elem) -> tuple[float, float]:
    """Return (tx, ty) from a translate(...) transform attribute, or (0, 0)."""
    t = elem.get("transform", "")
    m = re.search(r'translate\(\s*([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s*\)', t)
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def _compute_component_svg_bounds(svg: str) -> dict[str, tuple[float, float, float, float]]:
    """Return axis-aligned bounding boxes (SVG coords) for every data-component designator."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return {}

    xs: dict[str, list[float]] = {}
    ys: dict[str, list[float]] = {}

    for elem in root.iter():
        comp = elem.get("data-component")
        if comp is None:
            continue
        tx, ty = _elem_translate(elem)
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        ex, ey = xs.setdefault(comp, []), ys.setdefault(comp, [])
        try:
            if tag == "rect":
                x = float(elem.get("x", 0)) + tx
                y = float(elem.get("y", 0)) + ty
                w = float(elem.get("width", 0))
                h = float(elem.get("height", 0))
                ex.extend([x, x + w]); ey.extend([y, y + h])
            elif tag == "circle":
                cx = float(elem.get("cx", 0)) + tx
                cy = float(elem.get("cy", 0)) + ty
                r  = float(elem.get("r", 0))
                ex.extend([cx - r, cx + r]); ey.extend([cy - r, cy + r])
            elif tag == "ellipse":
                cx = float(elem.get("cx", 0)) + tx
                cy = float(elem.get("cy", 0)) + ty
                rx = float(elem.get("rx", 0)); ry = float(elem.get("ry", 0))
                ex.extend([cx - rx, cx + rx]); ey.extend([cy - ry, cy + ry])
            elif tag == "path":
                b = _svg_path_bounds(elem.get("d", ""))
                if b:
                    ex.extend([b[0] + tx, b[2] + tx]); ey.extend([b[1] + ty, b[3] + ty])
        except (ValueError, TypeError):
            pass

    return {
        comp: (min(xs[comp]), min(ys[comp]), max(xs[comp]), max(ys[comp]))
        for comp in xs if xs[comp]
    }


def _iter_instance_bounds(root, target: frozenset[str]):
    """Yield (designator, x0, y0, x1, y1) once per unique component instance (by UID).

    Uses data-component-uid to separate instances that share the same designator,
    which can happen when channel routing renames a component to a colliding name.
    """
    uid_desig: dict[str, str] = {}
    uid_xs: dict[str, list[float]] = {}
    uid_ys: dict[str, list[float]] = {}

    for elem in root.iter():
        comp = elem.get("data-component")
        if comp is None or comp not in target:
            continue
        uid = elem.get("data-component-uid") or comp
        uid_desig[uid] = comp
        tx, ty = _elem_translate(elem)
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        ex, ey = uid_xs.setdefault(uid, []), uid_ys.setdefault(uid, [])
        try:
            if tag == "rect":
                x = float(elem.get("x", 0)) + tx
                y = float(elem.get("y", 0)) + ty
                w = float(elem.get("width", 0))
                h = float(elem.get("height", 0))
                ex.extend([x, x + w]); ey.extend([y, y + h])
            elif tag == "circle":
                cx = float(elem.get("cx", 0)) + tx
                cy = float(elem.get("cy", 0)) + ty
                r = float(elem.get("r", 0))
                ex.extend([cx - r, cx + r]); ey.extend([cy - r, cy + r])
            elif tag == "ellipse":
                cx = float(elem.get("cx", 0)) + tx
                cy = float(elem.get("cy", 0)) + ty
                rx = float(elem.get("rx", 0)); ry = float(elem.get("ry", 0))
                ex.extend([cx - rx, cx + rx]); ey.extend([cy - ry, cy + ry])
            elif tag == "path":
                b = _svg_path_bounds(elem.get("d", ""))
                if b:
                    ex.extend([b[0] + tx, b[2] + tx]); ey.extend([b[1] + ty, b[3] + ty])
        except (ValueError, TypeError):
            pass

    for uid, comp in uid_desig.items():
        if uid_xs.get(uid):
            yield comp, min(uid_xs[uid]), min(uid_ys[uid]), max(uid_xs[uid]), max(uid_ys[uid])


def _add_placed_markers(
    svg: str,
    placed: frozenset[str],
    bounds: dict[str, tuple[float, float, float, float]],
) -> str:
    """Append green border boxes around each placed component instance."""
    if not placed:
        return svg
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    sw = 0.25
    for desig, x0, y0, x1, y1 in _iter_instance_bounds(root, placed):
        g = ET.SubElement(root, f"{{{SVG_NS}}}g")
        g.set("data-placed-marker", desig)
        rect = ET.SubElement(g, f"{{{SVG_NS}}}rect")
        rect.set("x", f"{x0:.4f}")
        rect.set("y", f"{y0:.4f}")
        rect.set("width", f"{x1 - x0:.4f}")
        rect.set("height", f"{y1 - y0:.4f}")
        rect.set("fill", "none")
        rect.set("stroke", "#00cc44")
        rect.set("stroke-width", f"{sw:.3f}")

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _add_dnp_markers(
    svg: str,
    dnp: frozenset[str],
    bounds: dict[str, tuple[float, float, float, float]],
) -> str:
    """Append red X markers over each DNP component instance."""
    if not dnp:
        return svg
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    sw = 0.25
    for desig, x0, y0, x1, y1 in _iter_instance_bounds(root, dnp):
        g = ET.SubElement(root, f"{{{SVG_NS}}}g")
        g.set("data-dnp-marker", desig)
        for ax, ay, bx, by in [(x0, y0, x1, y1), (x1, y0, x0, y1)]:
            ln = ET.SubElement(g, f"{{{SVG_NS}}}line")
            ln.set("x1", f"{ax:.4f}"); ln.set("y1", f"{ay:.4f}")
            ln.set("x2", f"{bx:.4f}"); ln.set("y2", f"{by:.4f}")
            ln.set("stroke", "#dd0000")
            ln.set("stroke-width", f"{sw:.3f}")
            ln.set("stroke-linecap", "round")

    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def parse_prjpcb_dnp(path: Path) -> frozenset[str]:
    """Parse a .PrjPcb file and return designators marked Kind=1 (Not Fitted) in the active variant."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    current_variant = ""
    for line in lines:
        s = line.strip()
        if s.startswith("CurrentVariant="):
            current_variant = s.split("=", 1)[1].strip()
            break

    dnp: set[str] = set()
    in_project_variant = False
    section_desc = ""
    section_variations: list[tuple[str, str]] = []

    def _flush() -> None:
        if section_desc == current_variant:
            for desig, kind in section_variations:
                if kind == "1":
                    dnp.add(desig)

    for line in lines:
        s = line.strip()
        if s.startswith("["):
            if in_project_variant:
                _flush()
            in_project_variant = s.startswith("[ProjectVariant")
            section_desc = ""
            section_variations = []
        elif in_project_variant:
            if s.startswith("Description="):
                section_desc = s.split("=", 1)[1].strip()
            elif s.split("=")[0].startswith("Variation") and "=" in s:
                val = s.split("=", 1)[1]
                parts: dict[str, str] = {}
                for part in val.split("|"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        parts[k.strip()] = v.strip()
                if "Designator" in parts:
                    section_variations.append((parts["Designator"], parts.get("Kind", "0")))

    if in_project_variant:
        _flush()

    return frozenset(dnp)


def _dim_unselected(svg: str, selected: set[str], hide_designators: set[str] | None = None) -> str:
    """
    Dim everything except the selected components and the board structure.

    Rules:
    - data-component in hide_designators  → invisible (wrong-side components)
    - data-component in selected set      → full opacity (keep)
    - data-component not in selected set  → dim (non-selected parts)
    - data-net-index, no data-component   → dim (tracks, via stubs, copper pours)
    - data-layer-role="silkscreen"        → dim
    - anything else (outline, mechanical) → full opacity (keep)
    """
    ET.register_namespace("", SVG_NS)
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg

    for elem in root.iter():
        comp = elem.get("data-component")
        if comp is not None:
            if hide_designators and comp in hide_designators:
                elem.set("opacity", "0")
            elif comp not in selected:
                elem.set("opacity", "0.04")

    return ET.tostring(root, encoding="unicode", xml_declaration=False)
