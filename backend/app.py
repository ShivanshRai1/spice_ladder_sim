"""Flask API for thermal RC ladder simulation + schematic rendering.

Run:
    python app.py

API:
- POST /api/simulate
- POST /api/schematic
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np
from flask import Flask, jsonify, request

from thermal_ladder import simulate_cauer, simulate_foster, validate_inputs

app = Flask(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RC_BUILDER_DIAGRAM = _PROJECT_ROOT / "RC network builder" / "diagram.py"
_RC_BUILDER_MODULE = None
_RC_BUILDER_ERROR = None


def _get_rc_builder_module():
    """Load the existing RC network builder module from disk once.

    This reuses the original schematic generation logic in
    `RC network builder/diagram.py` as the single source of truth.
    """
    global _RC_BUILDER_MODULE  # noqa: PLW0603
    global _RC_BUILDER_ERROR  # noqa: PLW0603

    if _RC_BUILDER_MODULE is not None:
        return _RC_BUILDER_MODULE
    if _RC_BUILDER_ERROR is not None:
        raise RuntimeError(_RC_BUILDER_ERROR)

    if not _RC_BUILDER_DIAGRAM.exists():
        _RC_BUILDER_ERROR = (
            f"Schematic builder file not found: {_RC_BUILDER_DIAGRAM}"
        )
        raise RuntimeError(_RC_BUILDER_ERROR)

    try:
        spec = importlib.util.spec_from_file_location(
            "rc_network_builder_diagram", _RC_BUILDER_DIAGRAM
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Failed to create import spec for schematic builder.")

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        required = ["DiagramOptions", "foster_svg", "cauer_svg"]
        missing = [name for name in required if not hasattr(module, name)]
        if missing:
            raise RuntimeError(
                "Schematic builder module missing required symbols: "
                + ", ".join(missing)
            )

        _RC_BUILDER_MODULE = module
        return _RC_BUILDER_MODULE
    except ModuleNotFoundError as exc:
        if exc.name == "schemdraw":
            _RC_BUILDER_ERROR = (
                "schemdraw is required for schematic rendering. "
                "Install backend dependencies: pip install -r backend/requirements.txt"
            )
        else:
            _RC_BUILDER_ERROR = f"Missing module for schematic builder: {exc.name}"
        raise RuntimeError(_RC_BUILDER_ERROR) from exc
    except Exception as exc:  # noqa: BLE001
        _RC_BUILDER_ERROR = f"Failed to load schematic builder: {exc}"
        raise RuntimeError(_RC_BUILDER_ERROR) from exc


@app.after_request
def add_cors_headers(response):
    """Allow local frontend calls, including file:// origin."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def _error(message: str, code: int = 400):
    return jsonify({"error": message}), code


def _as_float_array(name: str, values: Any) -> np.ndarray:
    try:
        arr = np.asarray(values, dtype=float)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name} must be numeric.") from exc

    if arr.ndim != 1:
        raise ValueError(f"{name} must be a 1-D array.")
    if arr.size == 0:
        raise ValueError(f"{name} must not be empty.")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite values.")
    return arr


def _parse_model(payload: dict[str, Any]) -> str:
    model = str(payload.get("model", "")).strip().lower()
    if model not in {"foster", "cauer"}:
        raise ValueError("model must be 'foster' or 'cauer'.")
    return model


def _parse_order(payload: dict[str, Any]) -> int:
    try:
        n = int(payload.get("N"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("N must be an integer >= 1.") from exc
    if n < 1:
        raise ValueError("N must be >= 1.")
    return n


def _parse_rc(payload: dict[str, Any], n: int) -> tuple[np.ndarray, np.ndarray]:
    rth = _as_float_array("Rth", payload.get("Rth"))
    cth = _as_float_array("Cth", payload.get("Cth"))

    if rth.size != n or cth.size != n:
        raise ValueError(
            f"N must match parameter lengths; got N={n}, len(Rth)={rth.size}, len(Cth)={cth.size}."
        )
    if np.any(rth <= 0.0):
        idx = int(np.where(rth <= 0.0)[0][0])
        raise ValueError(f"Rth must be strictly positive; found Rth[{idx}]={rth[idx]}.")
    if np.any(cth <= 0.0):
        idx = int(np.where(cth <= 0.0)[0][0])
        raise ValueError(f"Cth must be strictly positive; found Cth[{idx}]={cth[idx]}.")

    return rth, cth


def _parse_ambient(payload: dict[str, Any], t_len: int):
    ambient = payload.get("ambient", 0.0)
    if isinstance(ambient, list):
        amb_arr = _as_float_array("ambient", ambient)
        if amb_arr.size != t_len:
            raise ValueError(
                f"ambient array length must equal len(t); got {amb_arr.size} vs {t_len}."
            )
    else:
        try:
            amb_scalar = float(ambient)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("ambient must be numeric (scalar or array).") from exc
        if not np.isfinite(amb_scalar):
            raise ValueError("ambient must be finite.")


def _parse_sim_payload(payload: dict[str, Any]):
    model = _parse_model(payload)
    n = _parse_order(payload)
    rth, cth = _parse_rc(payload, n)

    t = _as_float_array("t", payload.get("t"))
    p = _as_float_array("p", payload.get("p"))
    _parse_ambient(payload, t_len=t.size)

    # Uses simulator-side validation for monotonic t and matching p/t.
    validate_inputs(t, p, rth, cth)

    method = payload.get("method")
    if method is None:
        method = "exact_zoh" if model == "foster" else "backward_euler"
    method = str(method).strip().lower()

    return model, n, rth, cth, t, p, method


def _parse_schematic_payload(payload: dict[str, Any]):
    model = _parse_model(payload)
    n = _parse_order(payload)
    rth, cth = _parse_rc(payload, n)

    theme = str(payload.get("theme", "light")).strip().lower()
    if theme not in {"light", "dark"}:
        raise ValueError("theme must be 'light' or 'dark'.")

    return model, n, rth, cth, theme


@app.route("/api/simulate", methods=["POST", "OPTIONS"])
def simulate():
    """Simulate Foster or Cauer thermal network from posted JSON inputs."""
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("Request body must be valid JSON.")

    try:
        model, n, rth, cth, t, p, method = _parse_sim_payload(payload)

        # Keep API outputs as temperature rise. thermal_ladder currently defaults ambient=0.
        if model == "foster":
            if method == "trapezoidal":
                method = "exact_trap"
            if method not in {"exact_zoh", "exact_trap"}:
                raise ValueError(
                    "Invalid method for foster. Use 'exact_zoh', 'exact_trap', or 'trapezoidal'."
                )

            tj = simulate_foster(t, p, rth, cth, method=method)
            return jsonify(
                {
                    "model": "foster",
                    "t": t.tolist(),
                    "Tj": np.asarray(tj, dtype=float).tolist(),
                }
            )

        if method not in {"backward_euler", "trapezoidal"}:
            raise ValueError(
                "Invalid method for cauer. Use 'backward_euler' or 'trapezoidal'."
            )

        t_nodes = simulate_cauer(t, p, rth, cth, method=method)
        t_nodes = np.asarray(t_nodes, dtype=float)
        if t_nodes.shape != (t.size, n):
            raise ValueError(
                f"Unexpected simulator output shape {t_nodes.shape}, expected ({t.size}, {n})."
            )

        # Return node-major arrays for simpler plotting in the browser.
        node_major = [t_nodes[:, i].tolist() for i in range(n)]
        return jsonify(
            {
                "model": "cauer",
                "t": t.tolist(),
                "T_nodes": node_major,
                "format": "node_major",  # T_nodes[i] -> node i over time
            }
        )

    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected simulation error")
        return _error(f"Internal server error: {exc}", 500)


@app.route("/api/schematic", methods=["POST", "OPTIONS"])
def schematic():
    """Generate schematic SVG via the existing RC network builder module."""
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("Request body must be valid JSON.")

    try:
        model, n, _rth, _cth, _theme = _parse_schematic_payload(payload)

        builder = _get_rc_builder_module()
        if model == "foster":
            # Show branch/node indices on ladder nodes and skip the final ambient node label.
            label_names = [str(i + 1) for i in range(n)] + [""]
            opts = builder.DiagramOptions(labels=True, label_names=label_names)
            svg = builder.foster_svg(n, opts)
        else:
            # Show branch/node indices on ladder nodes and skip the final ambient node label.
            label_names = [str(i + 1) for i in range(n)] + [""]
            opts = builder.DiagramOptions(labels=True, label_names=label_names)
            svg = builder.cauer_svg(n, opts)

        if not isinstance(svg, str) or "<svg" not in svg.lower():
            raise RuntimeError("Schematic builder did not return valid SVG.")

        return jsonify({"svg": svg, "model": model, "N": n})
    except ValueError as exc:
        return _error(str(exc), 400)
    except RuntimeError as exc:
        return _error(str(exc), 500)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected schematic generation error")
        return _error(f"Internal server error: {exc}", 500)


@app.route("/")
def health():
    return jsonify({"status": "ok", "message": "Thermal ladder API is running."})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
