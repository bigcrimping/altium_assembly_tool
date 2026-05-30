"""web_server.py — Flask web server providing browser-based UI."""
from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from pcb_model import PcbModel, SVG_NS, parse_prjpcb_dnp
from population_state import PopulationState

ET.register_namespace("", SVG_NS)


class WebServer:
    def __init__(self, port: int = 4321) -> None:
        self._port = port
        self._model: PcbModel | None = None
        self._placement = PopulationState()
        self._dnp: frozenset[str] = frozenset()
        self._lock = threading.Lock()
        self._state_path: Path | None = None
        self._app = self._build_app()

    # ------------------------------------------------------------------ public

    def load(self, pcb_path: Path, prj_path: Path | None = None) -> str:
        """Load PCB (and optionally .PrjPcb). Returns board name."""
        model = PcbModel.load(pcb_path)
        dnp: frozenset[str] = frozenset()
        if prj_path:
            dnp = parse_prjpcb_dnp(prj_path)
        state_path = pcb_path.with_suffix(".popstate.json")
        with self._lock:
            self._model = model
            self._dnp = dnp
            self._placement.clear()
            self._state_path = state_path
            if state_path.exists():
                self._placement.load(state_path)
        return model.board_name

    def run(self) -> None:
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)
        self._app.run(host="127.0.0.1", port=self._port, threaded=True, use_reloader=False)

    # ------------------------------------------------------------------ helpers

    def _bom_row_dict(self, idx: int, entry, placed: frozenset[str], dnp: frozenset[str]) -> dict:
        visible = entry.designators
        top_refs = [d for d in visible if "BOTTOM" not in entry.designator_layers.get(d, "TOP").upper()]
        bot_refs = [d for d in visible if "BOTTOM" in entry.designator_layers.get(d, "TOP").upper()]
        effective = placed | dnp
        placed_count = sum(1 for d in visible if d in effective)
        all_done = bool(visible) and all(d in effective for d in visible)
        top_done = bool(top_refs) and all(d in effective for d in top_refs)
        bot_done = bool(bot_refs) and all(d in effective for d in bot_refs)
        return {
            "index": idx,
            "comment": entry.comment,
            "quantity": len(visible),
            "designators": visible,
            "top_refs": top_refs,
            "bot_refs": bot_refs,
            "placed_count": placed_count,
            "to_place_count": len(visible) - placed_count,
            "all_done": all_done,
            "top_done": top_done,
            "bot_done": bot_done,
        }

    def _build_svg(self, model: PcbModel, step: int, side: str,
                   placed: frozenset[str], dnp: frozenset[str]) -> str:
        hidden = model.hidden_designators_for_side(side)
        if 0 <= step < len(model.bom):
            entry = model.bom[step]
            visible = set(entry.designators)
            svg = model.svg_for_designators(visible, hidden or None)
            placed_here = frozenset(d for d in visible if d in placed and d not in hidden)
            dnp_here = frozenset(d for d in visible if d in dnp and d not in hidden)
        else:
            svg = model.side_filtered_svg(side)
            placed_here = frozenset()
            dnp_here = frozenset()
        if placed_here:
            svg = model.add_placed_markers(svg, placed_here)
        if dnp_here:
            svg = model.add_dnp_markers(svg, dnp_here)
        if side == "BOTTOM":
            svg = _flip_svg_horizontal(svg)
        return svg

    def _build_app(self) -> Flask:
        web_dir = Path(__file__).parent / "web"
        # static_folder/static_url_path tell Flask to serve web/ at /static/
        app = Flask(__name__, static_folder=str(web_dir), static_url_path="/static")

        @app.route("/")
        def index():
            return send_from_directory(str(web_dir), "index.html")

        @app.route("/api/data")
        def api_data():
            with self._lock:
                model = self._model
                if model is None:
                    return jsonify({"loaded": False})
                placed = self._placement.placed
                dnp = self._dnp
                bom = [self._bom_row_dict(i, e, placed, dnp) for i, e in enumerate(model.bom)]
                bounds = {k: list(v) for k, v in model.component_bounds.items()}
                return jsonify({
                    "loaded": True,
                    "board_name": model.board_name,
                    "bom": bom,
                    "bounds": bounds,
                    "placed": list(placed),
                    "dnp": list(dnp),
                })

        @app.route("/api/svg")
        def api_svg():
            step = request.args.get("step", "-1", type=int)
            side = request.args.get("side", "TOP").upper()
            with self._lock:
                model = self._model
                if model is None:
                    return ("No model loaded", 503)
                svg = self._build_svg(model, step, side, self._placement.placed, self._dnp)
            return svg, 200, {"Content-Type": "image/svg+xml; charset=utf-8"}

        @app.route("/api/toggle", methods=["POST"])
        def api_toggle():
            data = request.get_json(force=True)
            desig = data.get("designator", "")
            step = data.get("step", -1)
            side = data.get("side", "TOP").upper()
            with self._lock:
                model = self._model
                if model is None or not desig:
                    return jsonify({"ok": False, "error": "no model"})
                hidden = model.hidden_designators_for_side(side)
                if desig in hidden:
                    return jsonify({"ok": False, "error": "wrong side"})
                if 0 <= step < len(model.bom):
                    entry = model.bom[step]
                    if desig not in entry.designators:
                        return jsonify({"ok": False, "error": "not in current step"})
                now_placed = self._placement.toggle(desig)
                if self._state_path:
                    self._placement.save(self._state_path)
                placed = self._placement.placed
                dnp = self._dnp
                row_data = None
                for i, e in enumerate(model.bom):
                    if desig in e.designators:
                        row_data = self._bom_row_dict(i, e, placed, dnp)
                        break
                bounds = list(model.component_bounds.get(desig, [0, 0, 0, 0]))
                # Flip bounds X for bottom-side view so marker aligns with flipped SVG
                if side == "BOTTOM" and bounds and model.component_bounds.get(desig):
                    vb_x, _ = model.viewbox_origin
                    try:
                        root = ET.fromstring(model.base_svg)
                        vb = root.get("viewBox", "").split()
                        if len(vb) >= 3:
                            vw = float(vb[2])
                            x0_vb = float(vb[0])
                            x0, y0, x1, y1 = bounds
                            new_x0 = x0_vb + (x0_vb + vw - x1)
                            new_x1 = x0_vb + (x0_vb + vw - x0)
                            bounds = [new_x0, y0, new_x1, y1]
                    except Exception:
                        pass
                return jsonify({
                    "ok": True,
                    "designator": desig,
                    "now_placed": now_placed,
                    "bounds": bounds,
                    "bom_row": row_data,
                })

        @app.route("/api/load", methods=["POST"])
        def api_load():
            data = request.get_json(force=True)
            pcb_path = data.get("pcb_path", "").strip()
            prj_path = data.get("prj_path", "").strip() or None
            if not pcb_path:
                return jsonify({"ok": False, "error": "No path provided"})
            try:
                board_name = self.load(Path(pcb_path), Path(prj_path) if prj_path else None)
                return jsonify({"ok": True, "board_name": board_name})
            except FileNotFoundError as exc:
                return jsonify({"ok": False, "error": str(exc)})
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Load error: {exc}"})

        return app


# ------------------------------------------------------------------
# SVG flip helper (module-level, no class state needed)
# ------------------------------------------------------------------

def _flip_svg_horizontal(svg: str) -> str:
    """Mirror SVG content horizontally around the board centre (for bottom-side view)."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg
    vb = root.get("viewBox", "").split()
    if len(vb) < 4:
        return svg
    x0, y0, vw, vh = float(vb[0]), float(vb[1]), float(vb[2]), float(vb[3])
    # Mirror: translate right by (2*x0 + vw), then scale(-1, 1)
    children = list(root)
    g = ET.SubElement(root, f"{{{SVG_NS}}}g")
    g.set("transform", f"translate({2 * x0 + vw:.4f}, 0) scale(-1, 1)")
    for child in children:
        root.remove(child)
        g.append(child)
    return ET.tostring(root, encoding="unicode", xml_declaration=False)
