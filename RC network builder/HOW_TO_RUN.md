# How To Run

This guide explains how to launch the Foster/Cauer schematic generator.

## Prerequisites

- macOS/Linux/Windows
- Python 3.9+ (macOS users typically use `python3`)

## Setup

From the project root:

```bash
cd "RC network builder"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run the Server

```bash
python app.py
```

Open in your browser:

- `http://127.0.0.1:5000/?type=cauer&order=5`
- `http://127.0.0.1:5000/diagram?type=foster&order=3`

## Query Parameters

- `type`: `foster` or `cauer`
- `order`: integer 1..50
- `labels`: `on` or `off` (optional)

## Stop the Server

Press `Ctrl+C` in the terminal.
