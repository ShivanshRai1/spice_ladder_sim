# How To Run

This project has a Python backend API and a browser frontend.

## 1) Install dependencies

From the project root (`/Users/amolverma/Cursor/Spice_Ladder_sim`):

### macOS
```bash
cd /Users/amolverma/Cursor/Spice_Ladder_sim
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### Windows (PowerShell)
```powershell
cd C:\path\to\Spice_Ladder_sim
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r backend\requirements.txt
```

## 2) Start backend (Flask API)

In one terminal:

### macOS
```bash
cd /Users/amolverma/Cursor/Spice_Ladder_sim
source .venv/bin/activate
python3 backend/app.py
```

### Windows (PowerShell)
```powershell
cd C:\path\to\Spice_Ladder_sim
.venv\Scripts\Activate.ps1
py backend\app.py
```

Backend runs at:
- `http://127.0.0.1:5000`
- API endpoint: `POST http://127.0.0.1:5000/api/simulate`

## 3) Open frontend

Option A (direct open):
- Open `frontend/index.html` in your browser.

Option B (recommended local static server):

### macOS
```bash
cd /Users/amolverma/Cursor/Spice_Ladder_sim/frontend
python3 -m http.server 8080
```

### Windows (PowerShell)
```powershell
cd C:\path\to\Spice_Ladder_sim\frontend
py -m http.server 8080
```

Then open:
- `http://127.0.0.1:8080`

## 4) Run a simulation in UI

Default values are preloaded:
- `N = 4`
- `Rth = [0.2, 0.3, 0.1, 0.4]`
- `Cth = [5, 10, 2, 20]`
- pulse power profile from `0.1s` to `1.0s` at `100W`

Click **Run Simulation**.

## Notes

- Keep backend running while using the frontend.
- Frontend calls backend at `http://127.0.0.1:5000/api/simulate`.
- If browser blocks local file access, use the static server option (`python3 -m http.server` on macOS, `py -m http.server` on Windows).
- The API returns:
  - Foster: `Tj` array
  - Cauer: `T_nodes` in **node-major** format (`T_nodes[i]` is node `i` over time)
