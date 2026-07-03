"""
pcb_model.py — Pure data layer: PCB loading, BOM grouping, SVG dimming.
No Qt imports. All altium_monkey interaction lives here.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
_MIL_TO_MM = 0.0254

ET.register_namespace("", SVG_NS)

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
    top_refs: list[str] = field(default_factory=list)
    bot_refs: list[str] = field(default_factory=list)


class PcbModel:
    """
    Loads an Altium .PcbDoc or .PrjPcb file and provides BOM data + SVG rendering.

    Raises on load errors — the UI layer catches and shows dialogs.

    The processed SVG is parsed once at load time; the ElementTree root and an
    index of component elements are cached so that per-interaction rendering
    (dimming, hiding, markers) never re-parses the document.
    """

    def __init__(self) -> None:
        self._pcbdoc = None
        self._bom: list[BomEntry] = []
        self._base_svg: str = ""
        self.filepath: Path | None = None
        self._component_svg_bounds: dict[str, tuple[float, float, float, float]] = {}
        self._viewbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._root: ET.Element | None = None
        self._comp_elems: dict[str, list[ET.Element]] = {}
        # designator → per-instance bboxes (one per data-component-uid)
        self._instance_bounds: dict[str, list[tuple[float, float, float, float]]] = {}
        self._hidden_by_side: dict[str, frozenset[str]] = {}

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

    def hidden_designators_for_side(self, view_side: str) -> frozenset[str]:
        """Return designators that should be hidden for the given view side (TOP or BOTTOM)."""
        cached = self._hidden_by_side.get(view_side)
        if cached is not None:
            return cached
        if view_side == "BOTH":
            result: frozenset[str] = frozenset()
        else:
            result = frozenset(
                d
                for entry in self._bom
                for d in (entry.bot_refs if view_side == "TOP" else entry.top_refs)
            )
        self._hidden_by_side[view_side] = result
        return result

    def side_filtered_svg(self, view_side: str) -> str:
        """Return the base SVG with wrong-side components hidden."""
        hidden = self.hidden_designators_for_side(view_side)
        return self._svg_with_opacity({d: "0" for d in hidden})

    @property
    def viewbox(self) -> tuple[float, float, float, float]:
        """SVG viewBox as (x, y, width, height)."""
        return self._viewbox

    @property
    def viewbox_origin(self) -> tuple[float, float]:
        return (self._viewbox[0], self._viewbox[1])

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
        if not placed:
            return svg
        frags = []
        for desig in placed:
            attr = html.escape(desig, quote=True)
            for x0, y0, x1, y1 in self._instance_bounds.get(desig, ()):
                frags.append(
                    f'<g data-placed-marker="{attr}">'
                    f'<rect x="{x0:.4f}" y="{y0:.4f}"'
                    f' width="{x1 - x0:.4f}" height="{y1 - y0:.4f}"'
                    f' fill="none" stroke="#00cc44" stroke-width="0.250" /></g>'
                )
        return _insert_before_svg_close(svg, "".join(frags))

    def add_dnp_markers(self, svg: str, dnp: frozenset[str]) -> str:
        """Overlay red X markers on DNP (No Fit) components."""
        if not dnp:
            return svg
        frags = []
        for desig in dnp:
            attr = html.escape(desig, quote=True)
            for x0, y0, x1, y1 in self._instance_bounds.get(desig, ()):
                lines = "".join(
                    f'<line x1="{ax:.4f}" y1="{ay:.4f}" x2="{bx:.4f}" y2="{by:.4f}"'
                    f' stroke="#dd0000" stroke-width="0.250" stroke-linecap="round" />'
                    for ax, ay, bx, by in ((x0, y0, x1, y1), (x1, y0, x0, y1))
                )
                frags.append(f'<g data-dnp-marker="{attr}">{lines}</g>')
        return _insert_before_svg_close(svg, "".join(frags))

    def svg_for_designators(self, designators: set[str], hide_designators: set[str] | None = None) -> str:
        """Return the base SVG dimmed to the given designator set, with hide_designators fully hidden."""
        if not designators and not hide_designators:
            return self._base_svg
        hide = hide_designators or frozenset()
        opacity: dict[str, str] = {}
        for desig in self._comp_elems:
            if desig in hide:
                opacity[desig] = "0"
            elif desig not in designators:
                opacity[desig] = "0.04"
        return self._svg_with_opacity(opacity)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _svg_with_opacity(self, opacity_by_desig: dict[str, str]) -> str:
        """Serialize the cached tree with per-component opacity applied, then revert."""
        if self._root is None or not opacity_by_desig:
            return self._base_svg
        touched: list[tuple[ET.Element, str | None]] = []
        for desig, op in opacity_by_desig.items():
            for elem in self._comp_elems.get(desig, ()):
                touched.append((elem, elem.get("opacity")))
                elem.set("opacity", op)
        try:
            return ET.tostring(self._root, encoding="unicode", xml_declaration=False)
        finally:
            for elem, old in touched:
                if old is None:
                    elem.attrib.pop("opacity", None)
                else:
                    elem.set("opacity", old)

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
                top_refs=[d for d in designators if "BOTTOM" not in comp_layers[d]],
                bot_refs=[d for d in designators if "BOTTOM" in comp_layers[d]],
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
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            self._base_svg = raw
            return
        _strip_non_assembly_elements(root)
        _add_pin1_markers(root)

        _report(92, "Applying layout corrections…")
        self._apply_channel_routing_corrections(root)
        _refit_viewbox_to_board_outline(root)

        try:
            vb = (root.get("viewBox") or "0 0 0 0").split()
            self._viewbox = (float(vb[0]), float(vb[1]), float(vb[2]), float(vb[3]))
        except (ValueError, IndexError):
            self._viewbox = (0.0, 0.0, 0.0, 0.0)

        self._comp_elems = _index_component_elements(root)
        self._component_svg_bounds, self._instance_bounds = _compute_bounds(self._comp_elems)
        self._root = root
        self._base_svg = ET.tostring(root, encoding="unicode", xml_declaration=False)

    def _apply_channel_routing_corrections(self, root: ET.Element) -> None:
        """
        Fix components whose SVG positions are in the sub-sheet template coordinate
        system instead of the board coordinate system (missing room definition).
        """
        board_name = self.filepath.name if self.filepath else ""
        anchors = _CHANNEL_ROUTING_ANCHORS.get(board_name, {})
        if not anchors:
            return

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
            _translate_displaced_components(root, affected, dx, dy)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _natural_key(designator: str) -> tuple[str, int, str]:
    m = re.match(r"([A-Za-z]+)(\d+)(.*)", designator.strip())
    if m is None:
        return (designator.upper(), -1, "")
    prefix, num, suffix = m.groups()
    return (prefix.upper(), int(num), suffix.upper())


def _insert_before_svg_close(svg: str, fragment: str) -> str:
    """Insert an XML fragment immediately before the closing </svg> tag."""
    if not fragment:
        return svg
    i = svg.rfind("</svg>")
    if i < 0:
        return svg
    return f"{svg[:i]}{fragment}{svg[i:]}"


def _strip_non_assembly_elements(root: ET.Element) -> None:
    """
    Single-pass SVG cleanup (in place):
    - Via drill holes (data-hole-owner="via")
    - Copper tracks, fills, and via rings (data-net-index present, no data-component)
    - Mechanical footprint elements such as courtyard (data-layer-role="mechanical" with data-component)
    - Silkscreen designator text strokes (data-primitive="text")
    """
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


def _translate_displaced_components(
    root: ET.Element,
    affected: set[str],
    delta_x_mm: float,
    delta_y_mm: float,
) -> None:
    """Apply translate(dx dy) to elements with data-component in the affected set (in place)."""
    translate = f"translate({delta_x_mm:.4f} {delta_y_mm:.4f})"

    for elem in root.iter():
        comp = elem.get("data-component")
        if comp is not None and comp in affected:
            existing = elem.get("transform", "")
            elem.set("transform", f"{translate} {existing}".strip())


def _add_pin1_markers(root: ET.Element) -> None:
    """Apply a diagonal stripe fill to pads with designator '1' to mark component pin 1 (in place)."""
    pin1_elems = [e for e in root.iter() if e.get("data-pad-designator") == "1"]
    if not pin1_elems:
        return

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


def _refit_viewbox_to_board_outline(root: ET.Element) -> None:
    """
    Update the SVG viewBox, width, and height to be centred on the board outline (in place).

    This ensures the board centroid sits at exactly (W/2, H/2) in scene coordinates,
    so horizontal flip (used for bottom-side view) mirrors around the board centroid
    rather than an arbitrary SVG origin.
    """
    bounds = None
    for elem in root.iter():
        if elem.get("data-feature") == "board-outline":
            bounds = _svg_path_bounds(elem.get("d", ""))
            break
    if bounds is None:
        return
    x0, y0, x1, y1 = bounds
    if x1 <= x0 or y1 <= y0:
        return
    pad = 1.0
    vw = x1 - x0 + 2 * pad
    vh = y1 - y0 + 2 * pad
    root.set("viewBox", f"{x0 - pad:.4f} {y0 - pad:.4f} {vw:.4f} {vh:.4f}")
    root.set("width", f"{vw:.4f}")
    root.set("height", f"{vh:.4f}")


def _elem_translate(elem) -> tuple[float, float]:
    """Return (tx, ty) from a translate(...) transform attribute, or (0, 0)."""
    t = elem.get("transform", "")
    m = re.search(r'translate\(\s*([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s*\)', t)
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def _index_component_elements(root: ET.Element) -> dict[str, list[ET.Element]]:
    """Map designator → all elements carrying that data-component attribute."""
    index: dict[str, list[ET.Element]] = {}
    for elem in root.iter():
        comp = elem.get("data-component")
        if comp is not None:
            index.setdefault(comp, []).append(elem)
    return index


def _element_bounds(elem: ET.Element) -> tuple[float, float, float, float] | None:
    """Axis-aligned bbox of a single rect/circle/ellipse/path element, or None."""
    tag = elem.tag.rsplit("}", 1)[-1]
    tx, ty = _elem_translate(elem)
    try:
        if tag == "rect":
            x = float(elem.get("x", 0)) + tx
            y = float(elem.get("y", 0)) + ty
            w = float(elem.get("width", 0))
            h = float(elem.get("height", 0))
            return (x, y, x + w, y + h)
        if tag == "circle":
            cx = float(elem.get("cx", 0)) + tx
            cy = float(elem.get("cy", 0)) + ty
            r = float(elem.get("r", 0))
            return (cx - r, cy - r, cx + r, cy + r)
        if tag == "ellipse":
            cx = float(elem.get("cx", 0)) + tx
            cy = float(elem.get("cy", 0)) + ty
            rx = float(elem.get("rx", 0))
            ry = float(elem.get("ry", 0))
            return (cx - rx, cy - ry, cx + rx, cy + ry)
        if tag == "path":
            b = _svg_path_bounds(elem.get("d", ""))
            if b:
                return (b[0] + tx, b[1] + ty, b[2] + tx, b[3] + ty)
    except (ValueError, TypeError):
        return None
    return None


def _compute_bounds(
    comp_elems: dict[str, list[ET.Element]],
) -> tuple[
    dict[str, tuple[float, float, float, float]],
    dict[str, list[tuple[float, float, float, float]]],
]:
    """Compute per-designator and per-instance bounding boxes in one pass.

    Instances are separated by data-component-uid, which distinguishes components
    that share the same designator (channel routing can rename a component to a
    colliding name). The per-designator box is the union of its instance boxes.
    """
    desig_bounds: dict[str, tuple[float, float, float, float]] = {}
    instance_bounds: dict[str, list[tuple[float, float, float, float]]] = {}

    for desig, elems in comp_elems.items():
        per_uid: dict[str, list[tuple[float, float, float, float]]] = {}
        for elem in elems:
            b = _element_bounds(elem)
            if b is None:
                continue
            uid = elem.get("data-component-uid") or desig
            per_uid.setdefault(uid, []).append(b)

        boxes = [
            (
                min(b[0] for b in bs),
                min(b[1] for b in bs),
                max(b[2] for b in bs),
                max(b[3] for b in bs),
            )
            for bs in per_uid.values()
        ]
        if boxes:
            desig_bounds[desig] = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            instance_bounds[desig] = boxes

    return desig_bounds, instance_bounds


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
