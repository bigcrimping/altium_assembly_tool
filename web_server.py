"""web_server.py — Flask web server providing browser-based UI.

The server sends the base SVG once; all per-step highlighting (dimming,
side hiding, flip, placed/DNP markers) happens client-side in app.js.
"""
from __future__ import annotations

import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from pcb_model import PcbModel, parse_prjpcb_dnp
from population_state import PopulationState


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
        top_refs = entry.top_refs
        bot_refs = entry.bot_refs
        effective = placed | dnp
        placed_count = sum(1 for d in visible if d in effective)
        all_done = bool(visible) and all(d in effective for d in visible)
        top_done = bool(top_refs) and all(d in effective for d in top_refs)
        bot_done = bool(bot_refs) and all(d in effective for d in bot_refs)
        return {
            "index": idx,
            "comment": entry.comment,
            "description": entry.description,
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

    def _build_app(self) -> Flask:
        web_dir = Path(__file__).parent / "web"
        # static_folder/static_url_path tell Flask to serve web/ at /static/
        app = Flask(__name__, static_folder=str(web_dir), static_url_path="/static")

        @app.route("/")
        def index():
            return send_from_directory(str(web_dir), "index.html")

        @app.route("/favicon.svg")
        def favicon():
            return send_from_directory(str(Path(__file__).parent / "assets"), "icon.svg")

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
                    "viewbox": list(model.viewbox),
                    # Designators hidden when viewing each side
                    "hidden": {
                        "TOP": sorted(model.hidden_designators_for_side("TOP")),
                        "BOTTOM": sorted(model.hidden_designators_for_side("BOTTOM")),
                    },
                })

        @app.route("/api/svg")
        def api_svg():
            with self._lock:
                model = self._model
                if model is None:
                    return ("No model loaded", 503)
                svg = model.base_svg
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
                return jsonify({
                    "ok": True,
                    "designator": desig,
                    "now_placed": now_placed,
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
