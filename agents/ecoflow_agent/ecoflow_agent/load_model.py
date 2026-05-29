"""
Home-load forecast model for the EcoFlow Smart Home Panel.

Predicts the next 24 h of whole-home load (and per-circuit power) on the same
15-min grid the thermostat MPC uses, so the ILC supervisor can anticipate
capacity-management breaches instead of only reacting to the instantaneous
draw. Parallels the HVAC RC model in ecobee_agent: a model class here plus an
offline trainer (train_load_model.py) that writes a config artifact the
smart_home_ilc agent consumes.

Formulation -- a single *direct* multi-horizon regressor (no recursion, no
leakage): each sample is one target timestamp T, and the features are all
knowable at forecast-issue time t0 for any T in the next 24 h:

  * calendar of T   -- hour-of-day & day-of-week (sin/cos), weekend flag
  * outdoor temp(T) -- the main exogenous driver (cooling/heating load); from
                       thermostat_readings history when training, from the
                       weather forecast when predicting
  * seasonal lags   -- load(T-24h) and load(T-168h). With a <=24 h horizon these
                       always fall at or before t0, so they are real observations
                       at inference, never predictions.

One model fits all horizon steps because every feature is computed relative to
the target timestamp. The estimator is sklearn HistGradientBoostingRegressor
(nonlinear, handles the temp/calendar interactions); a seq2seq LSTM is scaffolded
behind the same interface for later comparison, mirroring the HVAC LSTM scaffold.

The fitted estimator is persisted with joblib (.pkl) alongside a JSON metadata
sidecar (features, horizon, holdout metrics) -- unlike the pure-JSON HVAC model,
because a gradient-boosted tree is not JSON-serializable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

try:
    import joblib
    from sklearn.ensemble import HistGradientBoostingRegressor
    _SKLEARN = True
except ImportError:  # let the module import for inspection without sklearn
    _SKLEARN = False


DEFAULT_DT_S = 900.0
DEFAULT_HORIZON_H = 24.0
DEFAULT_SEASONAL_LAGS_H = (24.0, 168.0)

# Conservative GBM defaults: shallow, regularized, fast to fit on ~months of
# 15-min data. Tune later if holdout error warrants it.
DEFAULT_HGB_PARAMS = {
    "max_iter": 400,
    "learning_rate": 0.05,
    "max_depth": 6,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "early_stopping": True,
    "validation_fraction": 0.15,
    "random_state": 0,
}


# =====================================================================
# Feature engineering (shared by training and inference)
# =====================================================================
def calendar_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Cyclical hour-of-day & day-of-week plus a weekend flag for a UTC index."""
    hour = index.hour + index.minute / 60.0
    dow = index.dayofweek.to_numpy(dtype=float)
    return pd.DataFrame(
        {
            "hour_sin": np.sin(2 * np.pi * hour / 24.0),
            "hour_cos": np.cos(2 * np.pi * hour / 24.0),
            "dow_sin": np.sin(2 * np.pi * dow / 7.0),
            "dow_cos": np.cos(2 * np.pi * dow / 7.0),
            "is_weekend": (dow >= 5).astype(float),
        },
        index=index,
    )


FEATURE_NAMES = [
    "hour_sin", "hour_cos", "dow_sin", "dow_cos", "is_weekend", "temp_c",
] + [f"lag_{int(h)}h" for h in DEFAULT_SEASONAL_LAGS_H]


def _lag_values(gridded: pd.Series, target_times: pd.DatetimeIndex,
                lag_h: float, dt_s: float) -> np.ndarray:
    """Value of a regularly-gridded series at (target_times - lag_h), nearest
    within one timestep (NaN if no sample is close enough)."""
    want = target_times - pd.Timedelta(hours=lag_h)
    return gridded.reindex(
        want, method="nearest", tolerance=pd.Timedelta(seconds=dt_s)
    ).to_numpy(dtype=float)


def build_features(target_times: pd.DatetimeIndex, target_temp: np.ndarray,
                   history: pd.Series, dt_s: float,
                   lags_h=DEFAULT_SEASONAL_LAGS_H) -> pd.DataFrame:
    """Assemble the model feature frame for a set of target timestamps.

    `history` is the regularly-gridded past load series used to look up the
    seasonal lags; `target_temp` is the outdoor temperature at each target time
    (observed history when training, forecast when predicting)."""
    feats = calendar_features(target_times)
    feats["temp_c"] = np.asarray(target_temp, dtype=float)
    for h in lags_h:
        feats[f"lag_{int(h)}h"] = _lag_values(history, target_times, h, dt_s)
    return feats


# =====================================================================
# Single-target forecaster (used for aggregate load and each circuit)
# =====================================================================
@dataclass
class TargetForecaster:
    """Direct multi-horizon regressor for one load series (home or one circuit)."""
    name: str
    dt_s: float = DEFAULT_DT_S
    lags_h: tuple = DEFAULT_SEASONAL_LAGS_H
    hgb_params: dict = field(default_factory=lambda: dict(DEFAULT_HGB_PARAMS))
    estimator: object = None
    feature_names: list = field(default_factory=lambda: list(FEATURE_NAMES))
    metrics: dict = field(default_factory=dict)

    def fit(self, series: pd.Series, temp: pd.Series, holdout_days: float = 7.0):
        """Fit on a gridded (load, temp) history. The final `holdout_days` are
        held out chronologically to report an honest forward-looking error."""
        if not _SKLEARN:
            raise RuntimeError("scikit-learn is required to fit the load model")
        df = pd.concat([series.rename("y"), temp.rename("temp")], axis=1).dropna()
        if len(df) < 200:
            raise ValueError(f"[{self.name}] too few samples to fit ({len(df)})")
        X = build_features(df.index, df["temp"].to_numpy(), series, self.dt_s, self.lags_h)
        Xy = pd.concat([X, df["y"]], axis=1).dropna()
        y = Xy.pop("y")
        X = Xy[self.feature_names]

        split = X.index.max() - pd.Timedelta(days=holdout_days)
        tr, te = X.index <= split, X.index > split
        est = HistGradientBoostingRegressor(**self.hgb_params)
        est.fit(X[tr], y[tr])
        self.estimator = est

        self.metrics = {"n_train": int(tr.sum()), "n_samples": int(len(X))}
        if te.sum() > 20:
            pred = est.predict(X[te])
            err = pred - y[te].to_numpy()
            self.metrics.update({
                "holdout_days": holdout_days,
                "holdout_n": int(te.sum()),
                "holdout_mae_w": float(np.mean(np.abs(err))),
                "holdout_rmse_w": float(np.sqrt(np.mean(err ** 2))),
                "mean_load_w": float(y[te].mean()),
            })
        return self

    def predict(self, target_times: pd.DatetimeIndex, target_temp: np.ndarray,
                history: pd.Series) -> np.ndarray:
        """Predict the load at each target timestamp (W), clipped to >= 0."""
        if self.estimator is None:
            raise RuntimeError(f"[{self.name}] estimator not fitted/loaded")
        X = build_features(target_times, target_temp, history, self.dt_s, self.lags_h)
        # Seasonal lags can be NaN at the very start of history; fall back to the
        # series mean so a prediction is still produced.
        X = X[self.feature_names].fillna(float(np.nanmean(history.to_numpy())))
        return np.clip(self.estimator.predict(X), 0.0, None)


# =====================================================================
# Home model: aggregate + per-circuit forecasters
# =====================================================================
class HomeLoadModel:
    """Aggregate home-load forecaster plus one forecaster per circuit channel."""

    def __init__(self, home, home_id, dt_s=DEFAULT_DT_S, horizon_h=DEFAULT_HORIZON_H,
                 lags_h=DEFAULT_SEASONAL_LAGS_H):
        self.home = home
        self.home_id = home_id
        self.dt_s = dt_s
        self.horizon_h = horizon_h
        self.lags_h = tuple(lags_h)
        self.aggregate = TargetForecaster("home_load", dt_s, self.lags_h)
        self.circuits: dict[int, TargetForecaster] = {}   # channel_num -> forecaster
        self.circuit_names: dict[int, str] = {}

    @property
    def horizon_steps(self) -> int:
        return int(round(self.horizon_h * 3600.0 / self.dt_s))

    def predict_horizon(self, start_time, history, temp_forecast,
                        circuit_histories=None, reconcile=True):
        """Forecast the next horizon on the dt grid from `start_time` (UTC).

        history          : gridded whole-home load series (>= max lag of past).
        temp_forecast    : outdoor temp at each target step (len horizon_steps).
        circuit_histories: optional {channel_num: gridded series} for per-circuit.
        reconcile        : scale circuit forecasts so they sum to the aggregate.

        Returns a dict with target_times, home_load_w, and per-circuit vectors."""
        start = pd.Timestamp(start_time)
        if start.tz is not None:
            start = start.tz_convert("UTC").tz_localize(None)
        steps = self.horizon_steps
        target_times = pd.DatetimeIndex(
            [start + pd.Timedelta(seconds=self.dt_s * (k + 1)) for k in range(steps)])
        temp = np.asarray(temp_forecast, dtype=float)
        if len(temp) != steps:
            raise ValueError(f"temp_forecast len {len(temp)} != horizon_steps {steps}")

        home_pred = self.aggregate.predict(target_times, temp, history)
        out = {
            "home": self.home,
            "home_id": self.home_id,
            "dt_s": self.dt_s,
            "start_utc": start.isoformat(),
            "target_times": [t.isoformat() for t in target_times],
            "home_load_w": [round(float(v), 1) for v in home_pred],
            "peak_load_w": round(float(home_pred.max()), 1),
            "circuits": {},
        }

        if circuit_histories:
            circ_pred = {}
            for ch, fc in self.circuits.items():
                hist = circuit_histories.get(ch)
                if hist is None:
                    continue
                circ_pred[ch] = fc.predict(target_times, temp, hist)
            if reconcile and circ_pred:
                stacked = np.vstack(list(circ_pred.values()))
                ssum = stacked.sum(axis=0)
                scale = np.divide(home_pred, ssum, out=np.ones_like(home_pred),
                                  where=ssum > 1e-6)
                circ_pred = {ch: v * scale for ch, v in circ_pred.items()}
            for ch, v in circ_pred.items():
                out["circuits"][str(ch)] = {
                    "name": self.circuit_names.get(ch),
                    "power_w": [round(float(x), 1) for x in v],
                }
        return out

    # ---- persistence: joblib estimators + JSON sidecar ----
    def save(self, pkl_path: str, json_path: str):
        if not _SKLEARN:
            raise RuntimeError("scikit-learn/joblib required to save the load model")
        bundle = {
            "aggregate": self.aggregate,
            "circuits": self.circuits,
        }
        joblib.dump(bundle, pkl_path, compress=3)
        meta = {
            "home": self.home,
            "home_id": self.home_id,
            "dt_s": self.dt_s,
            "horizon_hours": self.horizon_h,
            "seasonal_lags_h": list(self.lags_h),
            "feature_names": self.aggregate.feature_names,
            "estimator_pkl": pkl_path.rsplit("/", 1)[-1],
            "aggregate_metrics": self.aggregate.metrics,
            "circuits": {
                str(ch): {"name": self.circuit_names.get(ch), "metrics": fc.metrics}
                for ch, fc in self.circuits.items()
            },
        }
        with open(json_path, "w") as fh:
            json.dump(meta, fh, indent=2)

    @classmethod
    def load(cls, pkl_path: str, json_path: str):
        if not _SKLEARN:
            raise RuntimeError("scikit-learn/joblib required to load the load model")
        with open(json_path) as fh:
            meta = json.load(fh)
        m = cls(meta["home"], meta["home_id"], dt_s=meta["dt_s"],
                horizon_h=meta["horizon_hours"], lags_h=meta["seasonal_lags_h"])
        bundle = joblib.load(pkl_path)
        m.aggregate = bundle["aggregate"]
        m.circuits = bundle["circuits"]
        m.circuit_names = {int(ch): v.get("name")
                           for ch, v in meta.get("circuits", {}).items()}
        return m


# =====================================================================
# LSTM scaffold (placeholder for a later seq2seq comparison)
# =====================================================================
class LSTMLoadForecaster:
    """Stub for a future sequence-to-sequence load forecaster.

    Kept as a placeholder so the trainer/consumer can switch model families
    behind the same predict() contract once a deep-learning baseline is wanted,
    mirroring the LSTM scaffold next to the HVAC RC model."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "LSTM load forecaster not implemented yet; use HomeLoadModel (GBM).")
