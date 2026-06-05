# 读取原始风场 Excel/CSV 数据；
# 统一识别风速、风向、温度、气压、湿度等字段；
# 构造 LSTM 预测模型需要的时间特征、差分特征和滚动均值特征；
# 加载 pth 检查点，输出未来风速和空气密度预测边界点；
# 提供运行期缓存，减少 GUI 多次调用时的重复计算。
from __future__ import annotations
import hashlib
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn as nn


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PACKAGE_ROOT / "predict"
DEFAULT_MODEL_DIR = PACKAGE_ROOT / "Windmodels"
DEFAULT_RESULTS_DIR = PACKAGE_ROOT / "rescults"
DEFAULT_RAW_XLSX_PATH = DEFAULT_DATA_DIR / "Wind farm site 1 (Nominal capacity-99MW).xlsx"
DEFAULT_BEST_MODEL_PATH = DEFAULT_MODEL_DIR / "wind_speed_density_model_best.pth"
DEFAULT_WIND_SPEED_MODEL_PATH = DEFAULT_MODEL_DIR / "wind_speed_density_model_best_wind_speed.pth"
DEFAULT_AIR_DENSITY_MODEL_PATH = DEFAULT_MODEL_DIR / "wind_speed_density_model_best_air_density.pth"
DEFAULT_METRICS_PATH = DEFAULT_RESULTS_DIR / "wind_speed_density_model_metrics.json"
DEFAULT_TEST_CSV_PATH = DEFAULT_RESULTS_DIR / "wind_speed_density_model_test_predictions.csv"
DEFAULT_RUNTIME_CACHE_DIR = DEFAULT_RESULTS_DIR / ".runtime_prediction_cache"

# 自动选择推理设备：有 CUDA 时使用 GPU，否则使用 CPU
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TARGET_COLS = ["wind_speed", "air_density"]
INPUT_LEN = 96
OUTPUT_LEN = 1
HIDDEN_SIZE = 128
DROPOUT = 0.25
DEFAULT_ROLLING_WINDOW_START_INDEX = 0
ROLLING_HISTORY_RATIO = 0.50
DEFAULT_WIND_SPEED_MODEL_WEIGHT = 1.0
WIND_SPEED_TARGET_MODE_ABSOLUTE = "absolute"
WIND_SPEED_TARGET_MODE_DELTA = "delta_from_last_observed"
WIND_SPEED_TARGET_MODE = WIND_SPEED_TARGET_MODE_DELTA
_PREDICTION_CACHE_LOCK = RLock()

# 统一匹配原始数据列名
SOURCE_ALIASES = {
    "timestamp": ("timeyearmonthdayhms", "timestamp", "datetime"),
    "wind_speed_10m": ("windspeedatheightof10metersms",),
    "wind_dir_10m_deg": ("winddirectionatheightof10meters",),
    "wind_speed_30m": ("windspeedatheightof30metersms",),
    "wind_dir_30m_deg": ("winddirectionatheightof30meters",),
    "wind_speed_50m": ("windspeedatheightof50metersms",),
    "wind_dir_50m_deg": ("winddirectionatheightof50meters",),
    "wind_speed": ("windspeedattheheightofwheelhubms",),
    "hub_wind_dir_deg": ("windspeedattheheightofwheelhub",),
    "air_temperature_c": ("airtemperaturec", "airtemperature"),
    "atmosphere_hpa": ("atmospherehpa", "pressurehpa", "pressure"),
    "relative_humidity_pct": ("relativehumidity",),
    "power_mw": ("powermw", "power"),
}
# 构造风切变、方向正余弦等输入特征
SPEED_COLS = ["wind_speed_10m", "wind_speed_30m", "wind_speed_50m", "wind_speed"]
DIRECTION_COLS = ["wind_dir_10m_deg", "wind_dir_30m_deg", "wind_dir_50m_deg", "hub_wind_dir_deg"]


def forecast_steps_for_days(step_minutes: float, days: float = 1.0) -> int:
    return max(1, int(math.ceil(days * 1440.0 / max(float(step_minutes), 1e-9))))


def resolve_wind_data_path(data_path: str | Path | None = None) -> Path:
    path = Path(data_path) if data_path is not None else DEFAULT_RAW_XLSX_PATH
    if not path.exists():
        raise FileNotFoundError(f"Wind data file not found: {path}")
    return path


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="gbk")


def _key(name: str) -> str:
    return "".join(char.lower() for char in str(name) if char.isalnum())


def _source_columns(columns: list[str]) -> dict[str, str]:
    available = {_key(column): column for column in columns}
    found: dict[str, str] = {}
    for field, aliases in SOURCE_ALIASES.items():
        match = next((available[alias] for alias in aliases if alias in available), None)
        if match is None:
            match = next(
                (original for normalized, original in available.items() if any(alias in normalized for alias in aliases)),
                None,
            )
        if match is not None:
            found[field] = match
    missing = [field for field in SOURCE_ALIASES if field != "power_mw" and field not in found]
    if missing:
        raise ValueError(f"Missing expected wind source columns: {missing}")
    return found


def _numeric(series: pd.Series, low: float | None = None, high: float | None = None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    values = values.mask(values <= -90.0)
    if low is not None:
        values = values.mask(values < low)
    if high is not None:
        values = values.mask(values > high)
    return values

# 对缺失值进行线性插值，并用前后向填充处理边界缺口
def _fill(series: pd.Series) -> pd.Series:
    return series.interpolate(method="linear", limit_direction="both").ffill().bfill()


def _direction(series: pd.Series) -> pd.Series:
    #对风向角进行圆周插值
    degrees = _numeric(series, 0.0, 360.0) % 360.0
    radians = np.deg2rad(degrees)
    sine = _fill(pd.Series(np.sin(radians), index=series.index).mask(degrees.isna()))
    cosine = _fill(pd.Series(np.cos(radians), index=series.index).mask(degrees.isna()))
    return (np.rad2deg(np.arctan2(sine, cosine)) + 360.0) % 360.0

# 从原始风场数据中构造基础物理特征和派生气象特征
def _raw_features(raw: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    columns = _source_columns([str(column) for column in raw.columns])
    frame = pd.DataFrame({"timestamp": pd.to_datetime(raw[columns["timestamp"]], errors="coerce")})
    for name in SPEED_COLS:
        frame[name] = _numeric(raw[columns[name]], 0.0)
    for name in DIRECTION_COLS:
        frame[name] = _direction(raw[columns[name]])
    frame["air_temperature_c"] = _numeric(raw[columns["air_temperature_c"]], -80.0, 70.0)
    frame["atmosphere_hpa"] = _numeric(raw[columns["atmosphere_hpa"]], 300.0, 1100.0)
    frame["relative_humidity_pct"] = _numeric(raw[columns["relative_humidity_pct"]], 0.0, 100.0)
    frame["power_mw"] = _numeric(raw[columns["power_mw"]], 0.0) if "power_mw" in columns else 0.0
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for name in SPEED_COLS + ["air_temperature_c", "atmosphere_hpa", "relative_humidity_pct", "power_mw"]:
        frame[name] = _fill(frame[name])

    pressure = frame["atmosphere_hpa"] * 100.0
    temperature_k = frame["air_temperature_c"] + 273.15
    vapor_hpa = 6.112 * np.exp((17.67 * frame["air_temperature_c"]) / (frame["air_temperature_c"] + 243.5))
    vapor_pa = vapor_hpa * 100.0 * frame["relative_humidity_pct"].clip(0.0, 100.0) / 100.0
    frame["pressure_pa"] = pressure
    frame["air_density"] = pressure / (287.05 * temperature_k.clip(lower=1.0))
    frame["air_density_humid"] = (pressure - vapor_pa) / (287.05 * temperature_k) + vapor_pa / (461.495 * temperature_k)
    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = np.log(frame["wind_speed_50m"] / frame["wind_speed_10m"]) / np.log(5.0)
    frame["wind_shear_alpha_10_50"] = _fill(alpha.replace([np.inf, -np.inf], np.nan).clip(-0.5, 0.8))
    frame["wind_speed_30m_powerlaw_from_10m"] = frame["wind_speed_10m"] * 3.0**0.14
    for name in DIRECTION_COLS:
        radians = np.deg2rad(frame[name])
        base = name.removesuffix("_deg")
        frame[f"{base}_sin"] = np.sin(radians)
        frame[f"{base}_cos"] = np.cos(radians)
    _time_features(frame, frame["timestamp"], include_weekday=False)
    return frame, columns["timestamp"]

# 加入小时、分钟、月份、星期等周期正余弦编码
def _time_features(frame: pd.DataFrame, timestamps: pd.Series, include_weekday: bool = True) -> None:
    minute = timestamps.dt.hour * 60 + timestamps.dt.minute
    values = {
        "hour_sin": np.sin(2.0 * np.pi * timestamps.dt.hour / 24.0),
        "hour_cos": np.cos(2.0 * np.pi * timestamps.dt.hour / 24.0),
        "minute_sin": np.sin(2.0 * np.pi * minute / 1440.0),
        "minute_cos": np.cos(2.0 * np.pi * minute / 1440.0),
        "month_sin": np.sin(2.0 * np.pi * (timestamps.dt.month - 1) / 12.0),
        "month_cos": np.cos(2.0 * np.pi * (timestamps.dt.month - 1) / 12.0),
    }
    if include_weekday:
        values.update(
            {
                "dow_sin": np.sin(2.0 * np.pi * timestamps.dt.dayofweek / 7.0),
                "dow_cos": np.cos(2.0 * np.pi * timestamps.dt.dayofweek / 7.0),
            }
        )
    for name, value in values.items():
        if name not in frame:
            frame[name] = value


def _step_minutes(timestamps: pd.Series) -> float:
    minutes = timestamps.diff().dt.total_seconds().dropna() / 60.0
    minutes = minutes[minutes > 0]
    return float(minutes.median()) if len(minutes) else 15.0


@lru_cache(maxsize=4)
def _prepare_cached(path_text: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = _read_table(Path(path_text))
    raw.columns = [str(column) for column in raw.columns]
    if {"wind_speed", "air_density"}.issubset(raw.columns):
        time_col = next((column for column in raw.columns if "time" in column.lower()), "timestamp")
        timestamps = pd.to_datetime(raw[time_col], errors="coerce").ffill().bfill()
        frame = raw.drop(columns=[time_col]).apply(pd.to_numeric, errors="coerce")
    else:
        prepared, time_col = _raw_features(raw)
        timestamps = prepared.pop("timestamp")
        frame = prepared
    frame = frame.replace([np.inf, -np.inf], np.nan).ffill().bfill().fillna(0.0)
    frame["wind_speed"] = frame["wind_speed"].clip(lower=0.0)
    frame["air_density"] = frame["air_density"].clip(lower=0.3)
    for name in TARGET_COLS:
        frame[f"{name}_diff"] = frame[name].diff().fillna(0.0)
        frame[f"{name}_roll_4"] = frame[name].rolling(4, min_periods=1).mean()
        frame[f"{name}_roll_12"] = frame[name].rolling(12, min_periods=1).mean()
    _time_features(frame, timestamps)
    metadata = {
        "time_col": time_col,
        "original_wind_speed_col": "wind_speed",
        "original_air_density_col": "air_density",
        "feature_cols": list(frame.columns),
        "target_cols": TARGET_COLS.copy(),
        "timestamps": timestamps.reset_index(drop=True),
        "step_minutes": _step_minutes(timestamps),
    }
    return frame, metadata


def prepare_wind_dataframe(file_path: str | Path | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = str(resolve_wind_data_path(file_path).resolve())
    frame, metadata = _prepare_cached(path)
    copied = {key: value.copy() if hasattr(value, "copy") else value for key, value in metadata.items()}
    return frame.copy(), copied


class SafeMinMaxScaler:
    def __init__(self) -> None:
        self.min_: np.ndarray | None = None
        self.max_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, data: np.ndarray) -> None:
        self.min_, self.max_ = np.min(data, axis=0), np.max(data, axis=0)
        self.scale_ = self.max_ - self.min_
        self.scale_[self.scale_ == 0] = 1.0

    def transform(self, data: np.ndarray) -> np.ndarray:
        return (data - self.min_) / self.scale_

    def inverse_transform(self, data: np.ndarray) -> np.ndarray:
        return data * self.scale_ + self.min_


class WindLSTMModel(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = HIDDEN_SIZE, output_size: int = 2, dropout: float = DROPOUT) -> None:
        super().__init__()
        self.lstm1 = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.dropout1 = nn.Dropout(dropout)
        self.lstm2 = nn.LSTM(hidden_size, hidden_size // 2, batch_first=True)
        self.dropout2 = nn.Dropout(dropout)
        self.fc1 = nn.Linear(hidden_size // 2, hidden_size // 4)
        self.relu = nn.ReLU()
        self.dropout3 = nn.Dropout(dropout)
        self.fc2 = nn.Linear(hidden_size // 4, output_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x, _ = self.lstm1(x)
        x, _ = self.lstm2(self.dropout1(x))
        return self.fc2(self.dropout3(self.relu(self.fc1(self.dropout2(x)[:, -1, :]))))


def postprocess_predictions(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32).copy()
    values[..., 0] = np.maximum(values[..., 0], 0.0)
    values[..., 1] = np.maximum(values[..., 1], 0.3)
    return values


def uses_delta_wind_speed_target(target_mode: str | None) -> bool:
    return str(target_mode or WIND_SPEED_TARGET_MODE_ABSOLUTE) == WIND_SPEED_TARGET_MODE_DELTA


@dataclass
class WindPredictor:
    model: WindLSTMModel
    feature_scaler: SafeMinMaxScaler
    target_scaler: SafeMinMaxScaler
    feature_cols: list[str]
    target_cols: list[str]
    input_len: int
    device: torch.device
    checkpoint: dict[str, Any]

    @classmethod
    def load(cls, checkpoint_path: str | Path, device: str | torch.device | None = None) -> "WindPredictor":
        target_device = torch.device(device or DEVICE)
        checkpoint = torch.load(str(checkpoint_path), map_location=target_device, weights_only=False)
        model = WindLSTMModel(int(checkpoint["n_features_per_timestep"]), output_size=len(checkpoint["target_cols"]))
        model.load_state_dict(checkpoint["model_state_dict"])
        model.to(target_device).eval()
        feature_scaler, target_scaler = SafeMinMaxScaler(), SafeMinMaxScaler()
        for scaler, prefix in ((feature_scaler, "feature"), (target_scaler, "target")):
            scaler.min_ = np.asarray(checkpoint[f"{prefix}_scaler_min"], dtype=np.float32)
            scaler.max_ = np.asarray(checkpoint[f"{prefix}_scaler_max"], dtype=np.float32)
            scaler.scale_ = np.asarray(checkpoint[f"{prefix}_scaler_scale"], dtype=np.float32)
        return cls(model, feature_scaler, target_scaler, list(checkpoint["feature_cols"]), list(checkpoint["target_cols"]), int(checkpoint["input_len"]), target_device, checkpoint)

    def _predict_window(self, window: np.ndarray) -> np.ndarray:
        x = torch.tensor(self.feature_scaler.transform(window)[None], dtype=torch.float32, device=self.device)
        with torch.no_grad():
            result = self.target_scaler.inverse_transform(self.model(x).cpu().numpy())
        last_wind = float(window[-1, self.feature_cols.index("wind_speed")])
        if uses_delta_wind_speed_target(self.checkpoint.get("wind_speed_target_mode")):
            result[0, 0] += last_wind
        weight = float(np.clip(self.checkpoint.get("wind_speed_model_weight", 1.0), 0.0, 1.5))
        result[0, 0] = weight * result[0, 0] + (1.0 - weight) * last_wind
        return postprocess_predictions(result)[0]

    def walk_forward_future_from_file(
        self,
        data_path: str | Path | None = None,
        future_ratio: float | None = None,
        max_steps: int | None = None,
        start_offset: int = 0,
    ) -> pd.DataFrame:
        frame, meta = prepare_wind_dataframe(data_path)
        features = frame.reindex(columns=self.feature_cols, fill_value=0.0).to_numpy(dtype=np.float32)
        timestamps = meta["timestamps"]
        start = self.input_len + DEFAULT_ROLLING_WINDOW_START_INDEX + max(0, int(start_offset))
        if future_ratio is not None:
            start = len(frame) - int(round(len(frame) * float(np.clip(future_ratio, 0.05, 0.95))))
        start = max(self.input_len, min(start, len(frame) - 1))
        stop = len(frame) if max_steps is None else min(len(frame), start + int(max_steps))
        rows = []
        for step, target_idx in enumerate(range(start, stop), start=1):
            predicted = self._predict_window(features[target_idx - self.input_len : target_idx])
            truth = frame.iloc[target_idx]
            previous = frame.iloc[target_idx - 1]
            rows.append(
                {
                    "step": step,
                    "timestamp": timestamps.iloc[target_idx],
                    "source_start_timestamp": timestamps.iloc[target_idx - self.input_len],
                    "source_end_timestamp": timestamps.iloc[target_idx - 1],
                    "start_wind_speed": float(previous["wind_speed"]),
                    "start_air_density": float(previous["air_density"]),
                    "pred_wind_speed": float(predicted[0]),
                    "pred_air_density": float(predicted[1]),
                    "true_wind_speed": float(truth["wind_speed"]),
                    "true_air_density": float(truth["air_density"]),
                    "wind_speed_error": float(predicted[0] - truth["wind_speed"]),
                    "air_density_error": float(predicted[1] - truth["air_density"]),
                }
            )
        return pd.DataFrame(rows)


@dataclass
class DualWindPredictor:
    wind_speed_predictor: WindPredictor
    air_density_predictor: WindPredictor

    @classmethod
    def load(cls, wind_path: str | Path | None = None, density_path: str | Path | None = None, device=None) -> "DualWindPredictor":
        return cls(
            WindPredictor.load(wind_path or DEFAULT_WIND_SPEED_MODEL_PATH, device),
            WindPredictor.load(density_path or DEFAULT_AIR_DENSITY_MODEL_PATH, device),
        )

    @property
    def checkpoint(self) -> dict[str, Any]:
        return self.wind_speed_predictor.checkpoint

    @property
    def feature_cols(self) -> list[str]:
        return self.wind_speed_predictor.feature_cols

    @property
    def target_cols(self) -> list[str]:
        return self.wind_speed_predictor.target_cols

    @property
    def input_len(self) -> int:
        return self.wind_speed_predictor.input_len

    def walk_forward_future_from_file(self, data_path=None, future_ratio=None, max_steps=None, start_offset=0) -> pd.DataFrame:
        wind = self.wind_speed_predictor.walk_forward_future_from_file(data_path, future_ratio, max_steps, start_offset)
        density = self.air_density_predictor.walk_forward_future_from_file(data_path, future_ratio, max_steps, start_offset)
        wind["pred_air_density"] = density["pred_air_density"].to_numpy(dtype=float)
        wind["air_density_error"] = wind["pred_air_density"] - wind["true_air_density"]
        return wind


@lru_cache(maxsize=4)
def _predictor(wind_path: str, density_path: str) -> DualWindPredictor:
    return DualWindPredictor.load(wind_path, density_path)


def get_predicted_wind_future_frame(
    data_path: str | Path | None = None,
    checkpoint_path: str | Path | None = None,
    wind_speed_checkpoint_path: str | Path | None = None,
    air_density_checkpoint_path: str | Path | None = None,
    max_steps: int | None = None,
    forecast_days: float = 1.0,
    start_offset: int = 0,
) -> pd.DataFrame:
    resolved_data_path = str(resolve_wind_data_path(data_path).resolve())
    data_mtime = Path(resolved_data_path).stat().st_mtime_ns
    wind_path = str(Path(wind_speed_checkpoint_path or checkpoint_path or DEFAULT_WIND_SPEED_MODEL_PATH).resolve())
    density_path = str(Path(air_density_checkpoint_path or checkpoint_path or DEFAULT_AIR_DENSITY_MODEL_PATH).resolve())
    disk_cache_path = _runtime_prediction_cache_path(
        resolved_data_path,
        wind_path,
        density_path,
        max_steps,
        float(forecast_days),
        int(start_offset),
    )
    with _PREDICTION_CACHE_LOCK:
        if disk_cache_path.exists():
            try:
                return pd.read_pickle(disk_cache_path).copy(deep=True)
            except (OSError, ValueError, EOFError):
                pass
        frame = _get_predicted_future_frame_cached(
            resolved_data_path,
            data_mtime,
            wind_path,
            density_path,
            max_steps,
            float(forecast_days),
            int(start_offset),
        )
        try:
            disk_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = disk_cache_path.with_suffix(".tmp")
            frame.to_pickle(tmp_path)
            os.replace(tmp_path, disk_cache_path)
        except OSError:
            pass
    return frame.copy(deep=True)


def _runtime_prediction_cache_path(
    data_path: str,
    wind_path: str,
    density_path: str,
    max_steps: int | None,
    forecast_days: float,
    start_offset: int,
) -> Path:
    def fingerprint(path_text: str) -> tuple[str, int, int]:
        path = Path(path_text)
        stat = path.stat()
        return str(path), int(stat.st_mtime_ns), int(stat.st_size)

    cache_key = (
        fingerprint(data_path),
        fingerprint(wind_path),
        fingerprint(density_path),
        max_steps,
        forecast_days,
        start_offset,
    )
    digest = hashlib.sha256(repr(cache_key).encode("utf-8")).hexdigest()[:24]
    return DEFAULT_RUNTIME_CACHE_DIR / f"wind_future_{digest}.pkl"


@lru_cache(maxsize=64)
def _get_predicted_future_frame_cached(
    data_path: str,
    data_mtime_ns: int,
    wind_path: str,
    density_path: str,
    max_steps: int | None,
    forecast_days: float,
    start_offset: int,
) -> pd.DataFrame:
    del data_mtime_ns
    predictor = _predictor(wind_path, density_path)
    if max_steps is None and forecast_days > 0:
        max_steps = forecast_steps_for_days(float(predictor.checkpoint.get("step_minutes", 15.0)), forecast_days)
    return predictor.walk_forward_future_from_file(data_path, max_steps=max_steps, start_offset=start_offset)
