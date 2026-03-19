"""Transient thermal RC network simulation (Foster and Cauer ladders).

This module implements two thermal-network solvers driven by an arbitrary power profile
sampled on a user-provided time grid.

Thermal-electrical analogy used throughout:
- Temperature rise [K] <-> Voltage [V]
- Power [W] <-> Current [A]
- Thermal resistance Rth [K/W] <-> Resistance [Ohm]
- Thermal capacitance Cth [J/K] <-> Capacitance [F]

Supported models:
- Foster network: parallel sum of independent first-order RC branches.
- Cauer ladder: series thermal ladder with capacitors at each internal node.

Units:
- Rth: K/W
- Cth: J/K
- p: W
- t: s
- ambient: K or degC (same unit system as output)
- output temperatures: absolute K or absolute degC (if ambient is provided in degC)
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

try:
    import scipy.sparse as _sp  # type: ignore
    import scipy.sparse.linalg as _splinalg  # type: ignore

    _SCIPY_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _SCIPY_AVAILABLE = False


def _as_1d_float_array(name: str, x: np.ndarray) -> np.ndarray:
    """Convert input to a 1-D float64 NumPy array."""
    arr = np.asarray(x, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be a 1-D array; got shape {arr.shape}.")
    if arr.size == 0:
        raise ValueError(f"{name} must not be empty.")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite values.")
    return arr


def _prepare_ambient(ambient, t_arr: np.ndarray) -> np.ndarray:
    """Return ambient profile as a float array with same length as t."""
    amb = np.asarray(ambient, dtype=float)
    if amb.ndim == 0:
        return np.full_like(t_arr, float(amb), dtype=float)
    if amb.ndim != 1:
        raise ValueError(
            f"ambient must be a scalar or 1-D array; got shape {amb.shape}."
        )
    if amb.size != t_arr.size:
        raise ValueError(
            "ambient array length must equal t length; "
            f"got len(ambient)={amb.size}, len(t)={t_arr.size}."
        )
    if not np.all(np.isfinite(amb)):
        raise ValueError("ambient contains non-finite values.")
    return amb


def validate_inputs(t, p, Rth, Cth, ambient=0.0):
    """Validate common inputs for thermal transient simulations.

    Parameters
    ----------
    t : array_like
        Time samples [s], strictly increasing.
    p : array_like
        Power samples [W], one value per time sample.
    Rth : array_like
        Thermal resistances [K/W], positive values.
    Cth : array_like
        Thermal capacitances [J/K], positive values.
    ambient : float or array_like, optional
        Ambient temperature profile. Can be scalar or array of length len(t).

    Raises
    ------
    ValueError
        If dimensions, lengths, monotonicity, or positivity checks fail.
    """
    t_arr = _as_1d_float_array("t", t)
    p_arr = _as_1d_float_array("p", p)
    r_arr = _as_1d_float_array("Rth", Rth)
    c_arr = _as_1d_float_array("Cth", Cth)

    if t_arr.size < 2:
        raise ValueError("t must contain at least two samples.")
    if p_arr.size != t_arr.size:
        raise ValueError(
            f"p length must equal t length; got len(p)={p_arr.size}, len(t)={t_arr.size}."
        )
    if r_arr.size != c_arr.size:
        raise ValueError(
            f"Rth and Cth lengths must match; got len(Rth)={r_arr.size}, len(Cth)={c_arr.size}."
        )
    if np.any(r_arr <= 0.0):
        idx = int(np.where(r_arr <= 0.0)[0][0])
        raise ValueError(f"Rth must be strictly positive; found Rth[{idx}]={r_arr[idx]}.")
    if np.any(c_arr <= 0.0):
        idx = int(np.where(c_arr <= 0.0)[0][0])
        raise ValueError(f"Cth must be strictly positive; found Cth[{idx}]={c_arr[idx]}.")

    dt = np.diff(t_arr)
    if np.any(dt <= 0.0):
        idx = int(np.where(dt <= 0.0)[0][0])
        raise ValueError(
            "t must be strictly increasing; "
            f"found t[{idx}]={t_arr[idx]} and t[{idx+1}]={t_arr[idx+1]}."
        )
    _prepare_ambient(ambient, t_arr)


def dc_rth(Rth: np.ndarray) -> float:
    """Return total DC thermal resistance [K/W] for either ladder representation."""
    r_arr = _as_1d_float_array("Rth", Rth)
    if np.any(r_arr <= 0.0):
        idx = int(np.where(r_arr <= 0.0)[0][0])
        raise ValueError(f"Rth must be strictly positive; found Rth[{idx}]={r_arr[idx]}.")
    return float(np.sum(r_arr))


def _clamp_small_negative_inplace(x: np.ndarray, tol: float = 1e-12) -> None:
    """Clamp tiny numerical negatives to zero, preserving meaningful negative values."""
    mask = (x < 0.0) & (x > -tol)
    x[mask] = 0.0


def simulate_foster(
    t: np.ndarray,
    p: np.ndarray,
    Rth: np.ndarray,
    Cth: np.ndarray,
    method: str = "exact_zoh",
    ambient: float | np.ndarray = 0.0,
) -> np.ndarray:
    """Simulate transient junction temperature for a Foster thermal network.

    Foster model:
        Tj(t) = sum_i x_i(t)
        dx_i/dt = (-x_i + R_i * p(t)) / tau_i,  tau_i = R_i * C_i

    Numerical method:
    - "exact_zoh" (default): exact exponential update over each interval with zero-order
      hold on power (p_eff = p[k-1]). Unconditionally stable.
    - "exact_trap": same exponential update but with average interval power
      p_eff = 0.5 * (p[k-1] + p[k]) for better forcing approximation.

    Parameters
    ----------
    t, p, Rth, Cth
        See `validate_inputs`.
    method : str
        Integration method: "exact_zoh" or "exact_trap".
    ambient : float or np.ndarray, optional
        Ambient temperature. Scalar for constant ambient, or length-M array for a
        time-varying ambient profile.

    Returns
    -------
    np.ndarray
        Junction absolute temperature array Tj(t), shape (M,).
    """
    validate_inputs(t, p, Rth, Cth, ambient=ambient)

    t_arr = np.asarray(t, dtype=float)
    p_arr = np.asarray(p, dtype=float)
    r_arr = np.asarray(Rth, dtype=float)
    c_arr = np.asarray(Cth, dtype=float)
    ambient_arr = _prepare_ambient(ambient, t_arr)

    if method not in {"exact_zoh", "exact_trap"}:
        raise ValueError(
            f"Unknown foster method '{method}'. Use 'exact_zoh' or 'exact_trap'."
        )

    m = t_arr.size
    n = r_arr.size

    tau = r_arr * c_arr
    x = np.zeros(n, dtype=float)
    tj = np.zeros(m, dtype=float)

    non_negative_power = bool(np.all(p_arr >= 0.0))

    for k in range(1, m):
        dt = t_arr[k] - t_arr[k - 1]
        a = np.exp(-dt / tau)

        if method == "exact_zoh":
            p_eff = p_arr[k - 1]
        else:  # exact_trap
            p_eff = 0.5 * (p_arr[k - 1] + p_arr[k])

        x = a * x + (1.0 - a) * (r_arr * p_eff)

        if non_negative_power:
            _clamp_small_negative_inplace(x)

        tj[k] = np.sum(x)

    if non_negative_power:
        _clamp_small_negative_inplace(tj)

    return tj + ambient_arr


def _build_cauer_matrices(Rth: np.ndarray, Cth: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Construct C, G, B matrices for Cauer ladder state equation.

    C * dT/dt + G * T = B * p(t)

    Topology:
        Node0 --R0-- Node1 --R1-- ... --R(N-2)-- Node(N-1) --R(N-1)-- Ambient(0)
        Each node i has C_i to ambient reference.
    """
    n = Rth.size

    c_diag = Cth.copy()
    g = np.zeros((n, n), dtype=float)

    # Internal resistors between adjacent nodes.
    for i in range(n - 1):
        gij = 1.0 / Rth[i]
        g[i, i] += gij
        g[i + 1, i + 1] += gij
        g[i, i + 1] -= gij
        g[i + 1, i] -= gij

    # Last resistor to ambient.
    g_last = 1.0 / Rth[n - 1]
    g[n - 1, n - 1] += g_last

    b = np.zeros(n, dtype=float)
    b[0] = 1.0

    return c_diag, g, b


def simulate_cauer(
    t: np.ndarray,
    p: np.ndarray,
    Rth: np.ndarray,
    Cth: np.ndarray,
    method: str = "backward_euler",
    ambient: float | np.ndarray = 0.0,
) -> np.ndarray:
    """Simulate transient node temperatures for a Cauer thermal ladder.

    Cauer model in matrix form:
        C * dT/dt + G * T = B * p(t)

    Numerical method options:
    - "backward_euler" (default):
          (C/dt + G) T[k] = (C/dt) T[k-1] + B * p[k-1]
      A-stable and robust for stiff systems.
    - "trapezoidal" (bilinear / Crank-Nicolson):
          (C/dt + 0.5 G) T[k] = (C/dt - 0.5 G) T[k-1] + 0.5 B (p[k] + p[k-1])
      Second-order for smooth responses, also A-stable.

    Parameters
    ----------
    t, p, Rth, Cth
        See `validate_inputs`.
    method : str
        Integration method: "backward_euler" or "trapezoidal".
    ambient : float or np.ndarray, optional
        Ambient temperature. Scalar for constant ambient, or length-M array for a
        time-varying ambient profile.

    Returns
    -------
    np.ndarray
        Node absolute temperatures, shape (M, N). Column 0 is junction node.
    """
    validate_inputs(t, p, Rth, Cth, ambient=ambient)

    t_arr = np.asarray(t, dtype=float)
    p_arr = np.asarray(p, dtype=float)
    r_arr = np.asarray(Rth, dtype=float)
    c_arr = np.asarray(Cth, dtype=float)
    ambient_arr = _prepare_ambient(ambient, t_arr)

    if method not in {"backward_euler", "trapezoidal"}:
        raise ValueError(
            "Unknown cauer method "
            f"'{method}'. Use 'backward_euler' or 'trapezoidal'."
        )

    m = t_arr.size
    n = r_arr.size

    c_diag, g_mat, b_vec = _build_cauer_matrices(r_arr, c_arr)

    t_nodes = np.zeros((m, n), dtype=float)
    non_negative_power = bool(np.all(p_arr >= 0.0))

    use_sparse = _SCIPY_AVAILABLE and n >= 200

    for k in range(1, m):
        dt = t_arr[k] - t_arr[k - 1]
        c_over_dt = c_diag / dt

        if method == "backward_euler":
            lhs = np.diag(c_over_dt) + g_mat
            rhs = c_over_dt * t_nodes[k - 1] + b_vec * p_arr[k - 1]
        else:  # trapezoidal
            lhs = np.diag(c_over_dt) + 0.5 * g_mat
            rhs = (
                (c_over_dt * t_nodes[k - 1])
                - 0.5 * (g_mat @ t_nodes[k - 1])
                + 0.5 * b_vec * (p_arr[k - 1] + p_arr[k])
            )

        if use_sparse:
            lhs_sparse = _sp.csr_matrix(lhs)
            t_nodes[k] = _splinalg.spsolve(lhs_sparse, rhs)
        else:
            t_nodes[k] = np.linalg.solve(lhs, rhs)

        if non_negative_power:
            _clamp_small_negative_inplace(t_nodes[k])

    if non_negative_power:
        _clamp_small_negative_inplace(t_nodes)

    return t_nodes + ambient_arr[:, None]


def make_pulse(t: np.ndarray, t_on: float, t_off: float, P: float) -> np.ndarray:
    """Build a rectangular power pulse profile on an existing time grid.

    Parameters
    ----------
    t : np.ndarray
        Time grid [s].
    t_on : float
        Turn-on time [s], inclusive.
    t_off : float
        Turn-off time [s], exclusive.
    P : float
        Pulse amplitude [W].

    Returns
    -------
    np.ndarray
        Power samples p(t), shape matches `t`.
    """
    t_arr = _as_1d_float_array("t", t)
    p = np.zeros_like(t_arr)
    mask = (t_arr >= t_on) & (t_arr < t_off)
    p[mask] = float(P)
    return p


def _max_abs_error_on_grid(
    t_ref: np.ndarray, y_ref: np.ndarray, t_query: np.ndarray, y_query: np.ndarray
) -> float:
    """Compute max abs error by interpolating reference solution onto query grid."""
    if y_ref.ndim == 1:
        y_interp = np.interp(t_query, t_ref, y_ref)
        return float(np.max(np.abs(y_interp - y_query)))

    # Matrix output: compare each column after interpolation, then take global max.
    errs = []
    for col in range(y_ref.shape[1]):
        y_interp = np.interp(t_query, t_ref, y_ref[:, col])
        errs.append(np.max(np.abs(y_interp - y_query[:, col])))
    return float(np.max(errs))


def example() -> None:
    """Run a self-contained demonstration and sanity checks.

    Demonstrates:
    - Foster and Cauer transient simulation from a pulse power profile.
    - DC thermal resistance check: final temperature rise under long constant power
      approaches P * sum(Rth).
    - Basic non-negativity check for non-negative power input.
    - Time-step robustness: compare dt vs dt/10 trajectories.

    If matplotlib is available, plots are shown; otherwise key values are printed.
    """
    # Example 4-stage thermal ladder (same Rth/Cth arrays used for both forms).
    rth = np.array([0.15, 0.10, 0.08, 0.07], dtype=float)  # K/W
    cth = np.array([0.20, 1.00, 4.00, 12.0], dtype=float)  # J/K

    n = rth.size
    p_step = 50.0  # W
    tamb = 25.0  # degC (or K offset reference)
    total_rth = dc_rth(rth)
    target_dc = p_step * total_rth

    # 1) DC sanity check with long step input.
    t_dc = np.linspace(0.0, 120.0, 6001)
    p_dc = make_pulse(t_dc, t_on=0.0, t_off=t_dc[-1] + 1.0, P=p_step)

    tj_dc = simulate_foster(t_dc, p_dc, rth, cth, method="exact_zoh", ambient=tamb)
    tn_dc = simulate_cauer(
        t_dc, p_dc, rth, cth, method="backward_euler", ambient=tamb
    )

    foster_rel_err = abs((tj_dc[-1] - tamb) - target_dc) / target_dc
    cauer_rel_err = abs((tn_dc[-1, 0] - tamb) - target_dc) / target_dc

    print("=== DC Thermal Resistance Check ===")
    print(f"N stages: {n}")
    print(f"Ambient temperature = {tamb:.3f}")
    print(f"sum(Rth) = {total_rth:.6f} K/W")
    print(f"Target steady DeltaT = P*sum(Rth) = {target_dc:.6f}")
    print(
        f"Foster final Tj = {tj_dc[-1]:.6f} "
        f"(DeltaT {tj_dc[-1]-tamb:.6f}, rel err {100*foster_rel_err:.3f}%)"
    )
    print(
        f"Cauer final Tj(node0) = {tn_dc[-1,0]:.6f} "
        f"(DeltaT {tn_dc[-1,0]-tamb:.6f}, rel err {100*cauer_rel_err:.3f}%)"
    )

    # Required tolerance: 1-2%.
    assert foster_rel_err <= 0.02, "Foster DC check failed (>2% error)."
    assert cauer_rel_err <= 0.02, "Cauer DC check failed (>2% error)."

    # 2) Transient pulse response and non-negativity check.
    t_coarse = np.linspace(0.0, 20.0, 2001)   # dt = 0.01 s
    t_fine = np.linspace(0.0, 20.0, 20001)    # dt = 0.001 s
    p_coarse = make_pulse(t_coarse, t_on=1.0, t_off=10.0, P=p_step)
    p_fine = make_pulse(t_fine, t_on=1.0, t_off=10.0, P=p_step)

    tj_coarse = simulate_foster(
        t_coarse, p_coarse, rth, cth, method="exact_zoh", ambient=tamb
    )
    tj_fine = simulate_foster(t_fine, p_fine, rth, cth, method="exact_zoh", ambient=tamb)

    tn_coarse = simulate_cauer(
        t_coarse, p_coarse, rth, cth, method="backward_euler", ambient=tamb
    )
    tn_fine = simulate_cauer(
        t_fine, p_fine, rth, cth, method="backward_euler", ambient=tamb
    )

    if np.all(p_coarse >= 0.0):
        if np.min(tj_coarse - tamb) < -1e-9:
            raise AssertionError("Foster non-negativity check failed.")
        if np.min(tn_coarse - tamb) < -1e-9:
            raise AssertionError("Cauer non-negativity check failed.")

    # 3) Time-step robustness: compare dt vs dt/10.
    foster_max_err = _max_abs_error_on_grid(t_fine, tj_fine, t_coarse, tj_coarse)
    cauer_max_err = _max_abs_error_on_grid(t_fine, tn_fine, t_coarse, tn_coarse)

    print("\n=== Time-Step Robustness ===")
    print(f"Foster max |error| (dt vs dt/10): {foster_max_err:.6e}")
    print(f"Cauer max |error| (dt vs dt/10): {cauer_max_err:.6e}")

    try:
        import matplotlib.pyplot as plt

        fig, axs = plt.subplots(2, 1, figsize=(9, 7), sharex=True)

        axs[0].plot(t_coarse, p_coarse, "k", lw=1.5, label="Power [W]")
        axs[0].set_ylabel("Power [W]")
        axs[0].grid(True, alpha=0.3)
        axs[0].legend(loc="best")

        axs[1].plot(t_coarse, tj_coarse, label="Foster Tj", lw=2.0)
        axs[1].plot(t_coarse, tn_coarse[:, 0], label="Cauer Node0 (Junction)", lw=2.0)
        for i in range(1, n):
            axs[1].plot(t_coarse, tn_coarse[:, i], "--", alpha=0.8, label=f"Cauer Node{i}")

        axs[1].set_xlabel("Time [s]")
        axs[1].set_ylabel("Temperature [degC or K]")
        axs[1].grid(True, alpha=0.3)
        axs[1].legend(loc="best", ncol=2)

        fig.suptitle("Transient Thermal Response: Foster vs Cauer")
        fig.tight_layout()
        plt.show()
    except Exception:
        # Matplotlib is optional; print fallback values.
        print("\nmatplotlib not available; printing sample outputs instead.")
        print("First 8 Foster Tj samples:", np.array2string(tj_coarse[:8], precision=5))
        print(
            "First 8 Cauer Node0 samples:",
            np.array2string(tn_coarse[:8, 0], precision=5),
        )


if __name__ == "__main__":
    example()
