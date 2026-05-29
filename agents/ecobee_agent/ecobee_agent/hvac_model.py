"""
HVAC plant + equipment models for thermostat MPC.

Two parts:

1. Equipment models — map an HVAC actuation `u` (single-stage on/off, or
   variable-speed modulation 0..1) to delivered thermal power (W) and
   electrical power (W), derived from nameplate tonnage and SEER.

2. Zone thermal models — forecast indoor temperature.
   * RCModel: gray-box 1R1C, fit by linear least squares. LINEAR in state
     and control, so the MPC stays a (MI)LP. This is the plant model the
     MPC uses.
   * LSTMForecaster: optional deep-learning comparison forecaster (requires
     torch; degrades gracefully if torch is absent). NOT used by the MPC.

Sign convention: thermal power into the zone is positive. Cooling delivers
NEGATIVE thermal power; heating positive.

Note on identifiability: if the training window contains no HVAC runtime
(e.g. the AC never cycled), only the passive time constant tau = R*C is
identifiable from data. The capacitance C is then set from a physical
estimate and R = tau / C; the HVAC gain (dt/C) follows. Once real cooling/
heating runtime is logged, `RCModel.fit` recovers the full model directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence

import numpy as np

# 1 ton of cooling = 12,000 BTU/h = 3516.85 W thermal
W_PER_TON = 3516.85
# BTU/h per Watt (to convert EER [BTU/(W·h)] into a unitless COP)
BTU_PER_WH = 3.412142


# =====================================================================
# Equipment models
# =====================================================================
def seer_to_cop(seer: float) -> float:
    """Approximate steady-state COP from a SEER rating.

    Uses the common DOE EER<->SEER regression EER = -0.02*SEER^2 + 1.12*SEER,
    then COP = EER / 3.412.
    """
    eer = -0.02 * seer * seer + 1.12 * seer
    return eer / BTU_PER_WH


@dataclass
class EquipmentModel:
    """Base HVAC equipment. `u` is the actuation in [0, 1]."""
    name: str
    tons: float
    seer: float
    modulating: bool            # True = variable-speed (continuous u); False = single-stage (binary u)
    mode: str                   # 'cool', 'heat', or 'both'
    min_plr: float = 0.0        # minimum part-load ratio when running (variable-speed)

    def __post_init__(self):
        self.capacity_w = self.tons * W_PER_TON      # nameplate thermal capacity (W)
        self.cop = seer_to_cop(self.seer)            # steady-state COP
        self.rated_electrical_w = self.capacity_w / self.cop

    def thermal_power_w(self, u: float, mode: str = "cool") -> float:
        """Delivered thermal power into the zone (W). Cooling is negative."""
        q = self.capacity_w * u
        return -q if mode == "cool" else q

    def electrical_power_w(self, u: float) -> float:
        """Electrical draw (W). Linear in u (constant COP)."""
        return self.rated_electrical_w * u

    def to_dict(self) -> dict:
        d = asdict(self)
        d["_type"] = type(self).__name__
        return d

    @staticmethod
    def from_dict(d: dict) -> "EquipmentModel":
        d = {k: v for k, v in d.items() if not k.startswith("_")}
        kind = d.pop("_type", "SingleStageCooling") if "_type" in d else "SingleStageCooling"
        cls = {"SingleStageCooling": SingleStageCooling,
               "VariableSpeedHeatPump": VariableSpeedHeatPump}.get(kind, SingleStageCooling)
        return cls(**d)


@dataclass
class SingleStageCooling(EquipmentModel):
    """Single-stage, cooling-only central AC (e.g. 3-ton, 14 SEER).

    Actuation is on/off; over an MPC timestep `u` is the duty fraction
    (binary in the MILP, but a duty-cycle interpretation is also valid).
    """
    name: str = "single_stage_ac"
    tons: float = 3.0
    seer: float = 14.0
    modulating: bool = False
    mode: str = "cool"


@dataclass
class VariableSpeedHeatPump(EquipmentModel):
    """Variable-speed inverter heat pump (e.g. 2-ton, 18.7 SEER), heat+cool.

    Continuous modulation in [min_plr, 1]. COP is treated as constant for
    the MPC (keeps it an LP); `part_load_cop` exposes the inverter's
    efficiency gain at low load for offline analysis.
    """
    name: str = "vs_heat_pump"
    tons: float = 2.0
    seer: float = 18.7
    modulating: bool = True
    mode: str = "both"
    min_plr: float = 0.15

    def part_load_cop(self, u: float) -> float:
        """Inverter COP rises modestly at part load; ~+25% near min load."""
        if u <= 0:
            return self.cop
        boost = 1.0 + 0.30 * (1.0 - u)
        return self.cop * boost


# =====================================================================
# Gray-box RC zone model (1R1C)
# =====================================================================
@dataclass
class RCModel:
    """1R1C thermal model fit by linear least squares.

    Discrete update over a fixed step dt_s (seconds):

        T[k+1] = T[k] + a*(Tout[k] - T[k]) + g*Qhvac[k] + s*solar[k] + d

      a = dt / (R*C)   (dimensionless)   -- passive coupling to outdoors
      g = dt / C       (K/J)             -- HVAC thermal gain
      s                                  -- solar/irradiance gain (optional)
      d                                  -- constant offset (internal gains)

    Physical params: tau = R*C = dt/a; C set from estimate; R = tau/C.
    """
    dt_s: float = 900.0                 # timestep (s); default 15 min
    a: float = 0.0                      # outdoor coupling coefficient
    g: float = 0.0                      # HVAC thermal gain (K per W)
    s: float = 0.0                      # solar gain
    d: float = 0.0                      # constant offset (K/step)
    capacitance_j_per_k: float = 5.0e6  # zone thermal capacitance (J/K)
    fit_used_hvac: bool = False         # whether HVAC gain came from data
    metrics: dict = field(default_factory=dict)

    # ---- derived physical params -------------------------------------
    @property
    def tau_s(self) -> float:
        return self.dt_s / self.a if self.a > 0 else float("inf")

    @property
    def resistance_k_per_w(self) -> float:
        return self.tau_s / self.capacitance_j_per_k

    # ---- fitting ------------------------------------------------------
    def fit(self, T_in, T_out, q_hvac_w=None, solar=None, dt_s=None):
        """Least-squares fit on regularly-sampled series.

        T_in, T_out: indoor/outdoor temperature (°C), length N.
        q_hvac_w:    delivered HVAC thermal power per step (W, signed). If all
                     ~0 (equipment never ran), the HVAC gain g is left to be
                     set from the equipment spec via `set_capacitance`.
        solar:       optional irradiance proxy, length N.
        """
        if dt_s is not None:
            self.dt_s = float(dt_s)

        T_in = np.asarray(T_in, dtype=float)
        T_out = np.asarray(T_out, dtype=float)
        N = len(T_in)
        dT = T_in[1:] - T_in[:-1]              # target: ΔT over each step

        cols = [(T_out[:-1] - T_in[:-1])]      # -> a
        names = ["a"]

        has_hvac = q_hvac_w is not None and np.nanmax(np.abs(q_hvac_w)) > 1.0
        if has_hvac:
            q = np.asarray(q_hvac_w, dtype=float)[:-1]
            cols.append(q)
            names.append("g")

        if solar is not None:
            sol = np.asarray(solar, dtype=float)[:-1]
            cols.append(sol)
            names.append("s")

        cols.append(np.ones(N - 1))            # -> d (intercept)
        names.append("d")

        A = np.column_stack(cols)
        coef, *_ = np.linalg.lstsq(A, dT, rcond=None)
        sol_map = dict(zip(names, coef))

        self.a = float(sol_map.get("a", 0.0))
        self.s = float(sol_map.get("s", 0.0))
        self.d = float(sol_map.get("d", 0.0))
        self.fit_used_hvac = has_hvac
        if has_hvac:
            self.g = float(sol_map.get("g", 0.0))
            # back out capacitance from the fitted gain: g = dt/C
            if self.g != 0:
                self.capacitance_j_per_k = self.dt_s / abs(self.g)
        else:
            # no runtime in data — HVAC gain from physical capacitance estimate
            self.set_capacitance(self.capacitance_j_per_k)

        # one-step prediction RMSE on the training data
        pred = A @ coef
        rmse = float(np.sqrt(np.mean((pred - dT) ** 2)))
        self.metrics = {
            "onestep_dT_rmse_c": rmse,
            "n_samples": int(N - 1),
            "tau_hours": self.tau_s / 3600.0,
            "fit_used_hvac": has_hvac,
        }
        return self

    def set_capacitance(self, c_j_per_k: float):
        """Set zone capacitance and derive the HVAC gain g = dt/C."""
        self.capacitance_j_per_k = float(c_j_per_k)
        self.g = self.dt_s / self.capacitance_j_per_k
        return self

    # ---- simulation ---------------------------------------------------
    def step(self, T, T_out, q_hvac_w=0.0, solar=0.0):
        return (T + self.a * (T_out - T)
                + self.g * q_hvac_w + self.s * solar + self.d)

    def simulate(self, T0, T_out, q_hvac_w=None, solar=None):
        """Free-run rollout. Returns predicted indoor temp series."""
        T_out = np.asarray(T_out, dtype=float)
        n = len(T_out)
        q = np.zeros(n) if q_hvac_w is None else np.asarray(q_hvac_w, dtype=float)
        sol = np.zeros(n) if solar is None else np.asarray(solar, dtype=float)
        T = np.empty(n)
        T[0] = T0
        for k in range(n - 1):
            T[k + 1] = self.step(T[k], T_out[k], q[k], sol[k])
        return T

    def freerun_rmse(self, T_in, T_out, solar=None):
        """Multi-step (open-loop) prediction RMSE vs measured indoor temp."""
        T_in = np.asarray(T_in, dtype=float)
        pred = self.simulate(T_in[0], T_out, None, solar)
        return float(np.sqrt(np.mean((pred - T_in) ** 2)))

    # ---- serialization ------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "dt_s": self.dt_s, "a": self.a, "g": self.g, "s": self.s,
            "d": self.d, "capacitance_j_per_k": self.capacitance_j_per_k,
            "fit_used_hvac": self.fit_used_hvac, "metrics": self.metrics,
        }

    @staticmethod
    def from_dict(d: dict) -> "RCModel":
        return RCModel(
            dt_s=d.get("dt_s", 900.0), a=d.get("a", 0.0), g=d.get("g", 0.0),
            s=d.get("s", 0.0), d=d.get("d", 0.0),
            capacitance_j_per_k=d.get("capacitance_j_per_k", 5.0e6),
            fit_used_hvac=d.get("fit_used_hvac", False),
            metrics=d.get("metrics", {}),
        )


# =====================================================================
# Optional LSTM forecaster (comparison only; not used by the MPC)
# =====================================================================
class LSTMForecaster:
    """Sequence forecaster for indoor temperature, for comparison with the
    RC model. Requires torch; if torch is unavailable, construction raises a
    clear error so the RC path is unaffected.
    """

    def __init__(self, n_features=3, hidden=32, layers=1, lookback=24, horizon=24):
        try:
            import torch  # noqa: F401
            import torch.nn as nn
        except Exception as e:  # pragma: no cover - depends on env
            raise ImportError(
                "LSTMForecaster requires PyTorch. Install torch, or use RCModel "
                "(the MPC plant model) which has no deep-learning dependency."
            ) from e
        self._torch = __import__("torch")
        self.n_features = n_features
        self.hidden = hidden
        self.layers = layers
        self.lookback = lookback
        self.horizon = horizon
        self.model = self._build()
        self._feat_mean = None
        self._feat_std = None

    def _build(self):
        import torch.nn as nn

        class _Net(nn.Module):
            def __init__(self, nf, hid, nl, horizon):
                super().__init__()
                self.lstm = nn.LSTM(nf, hid, nl, batch_first=True)
                self.head = nn.Linear(hid, horizon)

            def forward(self, x):
                out, _ = self.lstm(x)
                return self.head(out[:, -1, :])

        return _Net(self.n_features, self.hidden, self.layers, self.horizon)

    def _windows(self, feats, target):
        torch = self._torch
        X, Y = [], []
        for i in range(len(feats) - self.lookback - self.horizon):
            X.append(feats[i:i + self.lookback])
            Y.append(target[i + self.lookback:i + self.lookback + self.horizon])
        X = torch.tensor(np.array(X), dtype=torch.float32)
        Y = torch.tensor(np.array(Y), dtype=torch.float32)
        return X, Y

    def fit(self, feats, target, epochs=40, lr=1e-3, batch=64):
        torch = self._torch
        feats = np.asarray(feats, dtype=float)
        target = np.asarray(target, dtype=float)
        self._feat_mean = feats.mean(0)
        self._feat_std = feats.std(0) + 1e-6
        feats = (feats - self._feat_mean) / self._feat_std
        X, Y = self._windows(feats, target)
        opt = torch.optim.Adam(self.model.parameters(), lr=lr)
        loss_fn = torch.nn.MSELoss()
        n = len(X)
        for ep in range(epochs):
            perm = torch.randperm(n)
            for i in range(0, n, batch):
                idx = perm[i:i + batch]
                opt.zero_grad()
                loss = loss_fn(self.model(X[idx]), Y[idx])
                loss.backward()
                opt.step()
        with torch.no_grad():
            rmse = float(torch.sqrt(loss_fn(self.model(X), Y)).item())
        self.metrics = {"train_rmse_c": rmse, "n_windows": int(n)}
        return self
