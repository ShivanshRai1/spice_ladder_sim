"""Flask API for thermal RC ladder simulation + schematic rendering.

Run:
    python app.py

API:
- POST /api/simulate
- POST /api/schematic
- GET  /api/import_captured_graph?graph_id=...
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

from thermal_ladder import simulate_cauer, simulate_foster, validate_inputs

DISCOVEREE_GRAPH_CAPTURE_API = "https://www.discoveree.io/graph_capture_api.php"
_XY_PAIR_RE = re.compile(r"\{x:([^,]+),y:([^}]+)\}")

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


@app.route("/api/health", methods=["GET", "HEAD"])
def health():
    return jsonify({"status": "ok", "message": "Thermal ladder API is running."})


def _parse_xy_points(xy_raw: Any) -> list[dict[str, float]]:
    """Parse DiscoverEE `{x:...,y:...},{x:...,y:...}` strings into point dicts."""
    text = str(xy_raw or "").strip()
    if not text:
        return []
    points: list[dict[str, float]] = []
    for x_raw, y_raw in _XY_PAIR_RE.findall(text):
        try:
            x_val = float(x_raw)
            y_val = float(y_raw)
        except ValueError:
            continue
        if not (np.isfinite(x_val) and np.isfinite(y_val)):
            continue
        points.append({"x": float(x_val), "y": float(y_val)})
    return points


def _is_embedded_or_url_image(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered.startswith("data:image/")
        or lowered.startswith("blob:")
        or lowered.startswith("http://")
        or lowered.startswith("https://")
    )


def _build_graph_image_candidates(graph_img: Any) -> list[str]:
    """Build image URL candidates. Never treat a bare filename as base64."""
    value = str(graph_img or "").strip()
    if not value:
        return []
    rejected = {"0", "null", "undefined", "nan", "none"}
    if value.lower() in rejected:
        return []
    if _is_embedded_or_url_image(value):
        return [value]
    # Long base64 payloads (rare) — only if clearly not a filename.
    compact = re.sub(r"\s+", "", value)
    if (
        len(compact) > 200
        and re.fullmatch(r"[A-Za-z0-9+/=]+", compact)
        and "." not in value
    ):
        return [f"data:image/png;base64,{compact}"]

    candidates: list[str] = []
    name = value.lstrip("/")
    for host in (
        "https://www.discoveree.io",
        "https://www.fet.discoveree.io",
    ):
        candidates.append(f"{host}/{name}")
    if name.startswith("0") and len(name) > 1:
        stripped = name[1:]
        for host in (
            "https://www.discoveree.io",
            "https://www.fet.discoveree.io",
        ):
            candidates.append(f"{host}/{stripped}")
    # De-dupe while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _preprocess_foster_samples(
    t_raw: list[float], z_raw: list[float]
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Keep t>0 finite samples; shift negative Zth so digitization offset does not drop points."""
    t = np.asarray(t_raw, dtype=float)
    z = np.asarray(z_raw, dtype=float)
    meta: dict[str, Any] = {"negative_y_shift": 0.0, "dropped_nonpositive_time": 0}

    total = int(t.size)
    mask = np.isfinite(t) & np.isfinite(z) & (t > 0.0)
    meta["dropped_nonpositive_time"] = int(total - int(np.count_nonzero(mask)))
    t = t[mask]
    z = z[mask]
    if t.size < 3:
        raise ValueError(
            "Need at least 3 valid points with time > 0 to import a thermal curve."
        )

    z_min = float(np.min(z))
    if z_min < 0:
        meta["negative_y_shift"] = z_min
        z = z - z_min
    z = np.maximum(z, 0.0)

    order = np.argsort(t)
    t = t[order]
    z = z[order]
    # Collapse duplicate times (keep last)
    uniq_t: list[float] = []
    uniq_z: list[float] = []
    for ti, zi in zip(t.tolist(), z.tolist()):
        if uniq_t and abs(ti - uniq_t[-1]) <= 1e-15 * max(1.0, abs(ti)):
            uniq_z[-1] = zi
        else:
            uniq_t.append(ti)
            uniq_z.append(zi)
    t = np.asarray(uniq_t, dtype=float)
    z = np.asarray(uniq_z, dtype=float)
    if t.size < 3:
        raise ValueError("Not enough unique time points for Foster fit.")
    return t, z, meta


def _fit_foster_from_zth(
    t_raw: list[float], z_raw: list[float], n: int
) -> tuple[list[float], list[float], list[float], dict[str, Any]]:
    """Fit Foster R/C from Zth(t) ≈ sum Ri*(1-exp(-t/tau_i)), Ci=tau_i/Ri."""
    if n < 1:
        raise ValueError("Branch count N must be >= 1.")

    t, z, preprocess_meta = _preprocess_foster_samples(t_raw, z_raw)
    if t.size < max(3, n):
        raise ValueError(
            f"Need at least {max(3, n)} valid curve points to fit N={n} "
            f"(have {int(t.size)} after preprocessing)."
        )

    # Digitized Zth curves can be non-monotone; enforce physical non-decrease.
    z = np.maximum.accumulate(np.maximum(z, 0.0))
    z_inf = float(max(z[-1], 1e-12))

    t_min = float(max(t[0], 1e-12))
    t_max = float(t[-1])
    if not (t_max > t_min):
        raise ValueError("Invalid time span for Foster fit.")

    # Interior log-spaced taus (avoid extreme ends of the window).
    tau_lo = t_min * 1.05
    tau_hi = t_max * 0.95
    if not (tau_hi > tau_lo):
        tau_lo, tau_hi = t_min, t_max
    taus = np.logspace(np.log10(tau_lo), np.log10(tau_hi), n)
    design = 1.0 - np.exp(-np.outer(t, 1.0 / taus))

    # Multiplicative non-negative LS (no scipy dependency).
    r = np.full(n, z_inf / float(n), dtype=float)
    for _ in range(250):
        pred = design @ r
        numer = design.T @ z
        denom = design.T @ pred + 1e-12
        r *= numer / denom
        r = np.maximum(r, 1e-12)

    # Keep every branch usable for the simulator (avoid near-zero R / huge C).
    r_floor = z_inf / float(max(n * 40.0, 40.0))
    r = np.maximum(r, r_floor)

    # Scale so DC resistance matches final Zth.
    r_sum = float(np.sum(r))
    if r_sum > 0:
        r *= z_inf / r_sum
        r = np.maximum(r, 1e-12)

    c = np.maximum(taus / r, 1e-12)
    sort_idx = np.argsort(taus)
    return (
        r[sort_idx].tolist(),
        c[sort_idx].tolist(),
        taus[sort_idx].tolist(),
        preprocess_meta,
    )


def _resolve_requested_branch_count(
    detail: dict[str, Any], request_args: Any, point_count: int
) -> int:
    """Branches from query (?branches / ?return_NoOfbranches) or DiscoverEE detail fields."""
    candidates = [
        request_args.get("branches"),
        request_args.get("return_NoOfbranches"),
        request_args.get("return_noofbranches"),
        request_args.get("NoOfbranches"),
        detail.get("df_noofbranches"),
        detail.get("df_NoOfbranches"),
    ]
    requested = 4
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            requested = int(float(text))
            break
        except (TypeError, ValueError):
            continue
    requested = max(1, min(requested, 50))
    # Cannot fit more branches than unique time samples.
    return max(1, min(requested, max(1, point_count)))


def _resolve_requested_timestep(detail: dict[str, Any], request_args: Any) -> float | None:
    candidates = [
        request_args.get("timestep"),
        request_args.get("return_timeStep"),
        request_args.get("return_timestep"),
        request_args.get("timeStep"),
        detail.get("df_timestep"),
        detail.get("df_timeStep"),
    ]
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            value = float(text)
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and value > 0:
            return value
    return None


def _fetch_discoveree_graph(graph_id: str) -> dict[str, Any]:
    query = urlencode({"graph_id": graph_id})
    url = f"{DISCOVEREE_GRAPH_CAPTURE_API}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "spice-ladder-sim/1.0",
            "Accept": "application/json,text/plain,*/*",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"DiscoverEE graph API HTTP {exc.code}: {body[:240]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"DiscoverEE graph API unreachable: {exc.reason}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DiscoverEE graph API did not return JSON.") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("DiscoverEE graph API returned an unexpected payload.")
    if str(payload.get("status", "")).lower() not in {"success", "ok", "1"}:
        message = payload.get("message") or payload.get("error") or "unknown error"
        raise RuntimeError(f"DiscoverEE graph API error: {message}")
    return payload


@app.route("/api/import_captured_graph", methods=["GET", "OPTIONS"])
def import_captured_graph():
    """Proxy DiscoverEE graph_id fetch + Foster R/C fit for RC Ladder return flow.

    Fixes the common host-side bug of treating graph_img filenames as base64.
    Does not alter /api/simulate or /api/schematic behavior.
    """
    if request.method == "OPTIONS":
        return ("", 204)

    graph_id = str(request.args.get("graph_id") or "").strip()
    if not graph_id or not graph_id.isdigit():
        return _error("graph_id must be a positive integer.", 400)

    try:
        payload = _fetch_discoveree_graph(graph_id)
        graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else {}
        details = payload.get("details") if isinstance(payload.get("details"), list) else []
        if not details:
            return _error(f"No curve details found for graph_id={graph_id}.", 404)

        # Prefer R_th_C_th / rth_cth curves when present.
        preferred = None
        for detail_item in details:
            if not isinstance(detail_item, dict):
                continue
            title = str(detail_item.get("curve_title") or "").strip().lower()
            if (
                "r_th_c_th" in title
                or "rth_cth" in title
                or "rth" in title
                or "r_th" in title
                or "cth" in title
                or "c_th" in title
            ):
                preferred = detail_item
                break
        detail = preferred or next(
            (d for d in details if isinstance(d, dict)),
            None,
        )
        if detail is None:
            return _error(f"No usable curve detail for graph_id={graph_id}.", 404)

        points = _parse_xy_points(detail.get("xy"))
        if len(points) < 3:
            return _error(
                f"Curve for graph_id={graph_id} has too few xy points to import.",
                400,
            )

        t_vals = [p["x"] for p in points]
        z_vals = [p["y"] for p in points]
        n = _resolve_requested_branch_count(detail, request.args, len(points))
        timestep = _resolve_requested_timestep(detail, request.args)
        rth, cth, taus, preprocess_meta = _fit_foster_from_zth(t_vals, z_vals, n)

        # Report simple fit residual for transparency (does not block import).
        t_arr = np.asarray(t_vals, dtype=float)
        z_arr = np.asarray(z_vals, dtype=float)
        order = np.argsort(t_arr)
        t_arr = t_arr[order]
        z_arr = np.maximum.accumulate(np.maximum(z_arr[order], 0.0))
        design = 1.0 - np.exp(
            -np.outer(t_arr, 1.0 / np.asarray(taus, dtype=float))
        )
        pred = design @ np.asarray(rth, dtype=float)
        residual_rms = float(np.sqrt(np.mean((pred - z_arr) ** 2))) if z_arr.size else None

        image_candidates = _build_graph_image_candidates(graph.get("graph_img"))

        return jsonify(
            {
                "status": "success",
                "graph_id": str(graph.get("graph_id") or graph_id),
                "graph_title": graph.get("graph_title"),
                "curve_title": detail.get("curve_title"),
                "graph_img": graph.get("graph_img"),
                "image_candidates": image_candidates,
                "points": points,
                "N": n,
                "Rth": rth,
                "Cth": cth,
                "tau": taus,
                "timestep": timestep,
                "fit_residual_rms": residual_rms,
                "preprocess": preprocess_meta,
                "notes": [
                    "graph_img is treated as a filename/URL candidate list, never as raw base64.",
                    "Rth/Cth come from a Foster multi-exponential fit of the captured Zth(t) curve.",
                ],
            }
        )
    except ValueError as exc:
        return _error(str(exc), 400)
    except RuntimeError as exc:
        return _error(str(exc), 502)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected import_captured_graph error")
        return _error(f"Internal server error: {exc}", 500)


_FRONTEND_DIR = _PROJECT_ROOT / "frontend"


@app.route("/", defaults={"path": "index.html"}, methods=["GET", "HEAD"])
@app.route("/<path:path>", methods=["GET", "HEAD"])
def serve_frontend(path):
    """Serve the static frontend SPA."""
    return send_from_directory(_FRONTEND_DIR, path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
