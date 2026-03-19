from __future__ import annotations

from flask import Flask, Response, jsonify, render_template, request

from diagram import DiagramOptions, cauer_svg, foster_svg

app = Flask(__name__)


MAX_ORDER = 50


def _parse_order(value: str | None) -> int:
    if value is None:
        return 3
    try:
        order = int(value)
    except ValueError as exc:
        raise ValueError("order must be an integer") from exc
    if order < 1 or order > MAX_ORDER:
        raise ValueError(f"order must be between 1 and {MAX_ORDER}")
    return order


def _parse_type(value: str | None) -> str:
    if value is None:
        return "foster"
    value = value.strip().lower()
    if value not in {"foster", "cauer"}:
        raise ValueError("type must be 'foster' or 'cauer'")
    return value


def _parse_labels(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/diagram")
def diagram() -> Response:
    try:
        network_type = _parse_type(request.args.get("type"))
        order = _parse_order(request.args.get("order"))
        labels = _parse_labels(request.args.get("labels"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    opts = DiagramOptions(labels=labels)
    if network_type == "foster":
        svg = foster_svg(order, opts)
    else:
        svg = cauer_svg(order, opts)

    return Response(svg, mimetype="image/svg+xml")


@app.get("/embed")
def embed() -> str:
    return render_template("index.html", embedded=True)


if __name__ == "__main__":
    app.run(debug=True)
