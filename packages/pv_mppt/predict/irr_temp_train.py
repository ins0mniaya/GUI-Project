# 光伏辐照度/温度预测训练文件
# 功能：读取指定天气数据，训练 LSTM，同时保存辐照度最佳模型和温度最佳模型。
# 输出：两个 pth、一个测试集预测 CSV、一个指标 JSON、两张预测对比图。
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    mdates = None
    plt = None

# 路径配置

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_DIR = PROJECT_ROOT / "PVmodels"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "rescults"

# 在这里修改训练数据文件路径
WEATHER_DATA_PATH = Path(__file__).resolve().parent / "辐照度温度预测.xlsx"

DEFAULT_IRRADIANCE_MODEL_PATH = DEFAULT_MODEL_DIR / "irr_temp_model_best_irradiance.pth"
DEFAULT_TEMPERATURE_MODEL_PATH = DEFAULT_MODEL_DIR / "irr_temp_model_best_temperature.pth"
DEFAULT_METRICS_PATH = DEFAULT_RESULTS_DIR / "irr_temp_model_metrics.json"
DEFAULT_TEST_CSV_PATH = DEFAULT_RESULTS_DIR / "irr_temp_model_test_predictions.csv"

# 训练参数

SEED = 42
STEPS_PER_DAY_15MIN = 96
HISTORY_DAYS = 1
INPUT_LEN = STEPS_PER_DAY_15MIN * HISTORY_DAYS
OUTPUT_LEN = 1

TRAIN_RATIO = 0.60
VAL_RATIO = 0.15
DATA_FRACTION = 1.00

BATCH_SIZE = 128
NUM_EPOCHS = 120
EARLY_STOPPING_PATIENCE = 20

INIT_LR = 8e-4
WEIGHT_DECAY = 1e-4
INPUT_NOISE_STD = 0.01
L1_MIX = 0.50
IRRADIANCE_LOSS_WEIGHT = 2.5
TEMPERATURE_LOSS_WEIGHT = 1.0

HIDDEN_SIZE = 128
DROPOUT = 0.25

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
TARGET_COLS = ["irradiance", "temperature"]



# 基础工具

def set_seed(seed: int = SEED) -> None:
    # 固定随机种子，减少每次训练结果的随机差异
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_weather_data_path(data_path: str | Path | None = None) -> Path:
    # 优先使用命令行传入的数据路径；没有传入时使用文件开头 WEATHER_DATA_PATH
    path = Path(data_path) if data_path is not None else Path(WEATHER_DATA_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if not path.exists():
        raise FileNotFoundError(f"天气数据文件不存在，请在文件开头修改 WEATHER_DATA_PATH: {path}")
    return path


def read_table(file_path: str | Path) -> pd.DataFrame:
    file_path = str(file_path)
    if file_path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(file_path)
    try:
        return pd.read_csv(file_path, encoding="utf-8")
    except Exception:
        return pd.read_csv(file_path, encoding="gbk")


def find_time_col(columns: list[str]) -> str | None:
    # 从表头中识别时间列
    keys = ("time", "date", "timestamp", "year-month-day", "时间", "日期")
    for col in columns:
        if any(key in col.lower() for key in keys):
            return col
    return None


def find_target_cols(columns: list[str]) -> tuple[str, str]:
    # 从表头中识别辐照度列和温度列
    irr_keys = ("irradiance", "solar", "ghi", "radiation", "辐照", "辐射")
    temp_keys = ("air temperature", "temperature", "temp", "温度", "气温")
    irr_col = next((col for col in columns if any(key in col.lower() for key in irr_keys)), None)
    temp_col = next((col for col in columns if any(key in col.lower() for key in temp_keys)), None)
    if irr_col is None or temp_col is None:
        raise ValueError(f"无法从表头中识别辐照度列和温度列: {columns}")
    return irr_col, temp_col


def infer_step_minutes(timestamps: pd.Series, default_minutes: float = 60.0) -> float:
    # 根据时间戳间隔推断采样周期，单位为分钟
    if len(timestamps) < 2:
        return default_minutes
    values = timestamps.to_numpy(dtype="datetime64[ns]")
    diffs = np.diff(values).astype("timedelta64[s]").astype(np.int64) / 60.0
    valid = diffs[diffs > 0]
    return float(np.median(valid)) if valid.size else default_minutes


def steps_for_days(step_minutes: float, days: float) -> int:
    step_minutes = float(step_minutes)
    days = float(days)
    if step_minutes <= 0:
        raise ValueError(f"step_minutes must be positive, got {step_minutes}")
    if days <= 0:
        return 0
    return max(1, int(round(days * 24.0 * 60.0 / step_minutes)))


def history_steps_for_days(step_minutes: float, days: float = HISTORY_DAYS) -> int:
    return max(1, steps_for_days(step_minutes, days))


def forecast_steps_for_days(step_minutes: float, days: float) -> int:
    return steps_for_days(step_minutes, days)


class SafeMinMaxScaler:
    # 简单 Min-Max 归一化器，训练和运行阶段都可以复用

    def __init__(self) -> None:
        self.min_: np.ndarray | None = None
        self.max_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def fit(self, data: np.ndarray) -> None:
        self.min_ = np.min(data, axis=0)
        self.max_ = np.max(data, axis=0)
        self.scale_ = self.max_ - self.min_
        self.scale_[self.scale_ == 0.0] = 1.0

    def transform(self, data: np.ndarray) -> np.ndarray:
        if self.min_ is None or self.scale_ is None:
            raise ValueError("Scaler is not fitted.")
        return (data - self.min_) / self.scale_

    def inverse_transform(self, data: np.ndarray) -> np.ndarray:
        if self.min_ is None or self.scale_ is None:
            raise ValueError("Scaler is not fitted.")
        return data * self.scale_ + self.min_


# 数据清洗和特征构造

def clean_weather_targets(frame: pd.DataFrame) -> pd.DataFrame:
    # 清洗异常辐照度和温度，避免明显错误值影响模型训练
    data = frame.copy()
    data["irradiance"] = pd.to_numeric(data["irradiance"], errors="coerce")
    data["temperature"] = pd.to_numeric(data["temperature"], errors="coerce")

    irr_bad = data["irradiance"].eq(-99.0) | data["irradiance"].lt(0.0) | data["irradiance"].gt(1500.0)
    temp_bad = data["temperature"].eq(-99.0) | data["temperature"].lt(-60.0) | data["temperature"].gt(80.0)
    data.loc[irr_bad | ~np.isfinite(data["irradiance"]), "irradiance"] = np.nan
    data.loc[temp_bad | ~np.isfinite(data["temperature"]), "temperature"] = np.nan

    data = data.interpolate(method="linear", limit_direction="both").ffill().bfill()
    data["irradiance"] = data["irradiance"].clip(lower=0.0)
    return data


def prepare_weather_dataframe(file_path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    # 读取原始数据，并构造 LSTM 输入特征
    raw = read_table(file_path)
    raw.columns = [str(col) for col in raw.columns]

    time_col = find_time_col(list(raw.columns))
    irr_col, temp_col = find_target_cols(list(raw.columns))
    if time_col is None:
        raise ValueError("天气数据中没有找到时间列。")

    timestamps = pd.to_datetime(raw[time_col], errors="coerce").ffill().bfill().reset_index(drop=True)
    if timestamps.notna().sum() == 0:
        raise ValueError(f"无法解析时间列中的时间戳: {time_col}")

    frame = pd.DataFrame(
        {
            "irradiance": pd.to_numeric(raw[irr_col], errors="coerce"),
            "temperature": pd.to_numeric(raw[temp_col], errors="coerce"),
        }
    )
    frame = clean_weather_targets(frame)

    # 模型识别辐照度突变、短期趋势和温度变化
    frame["irradiance_diff"] = frame["irradiance"].diff().fillna(0.0)
    frame["irradiance_roll_4"] = frame["irradiance"].rolling(window=4, min_periods=1).mean()
    frame["irradiance_roll_12"] = frame["irradiance"].rolling(window=12, min_periods=1).mean()
    frame["temperature_diff"] = frame["temperature"].diff().fillna(0.0)

    # 周期时间特征：用 sin/cos 表示小时、分钟、星期和月份
    minute_of_day = timestamps.dt.hour * 60 + timestamps.dt.minute
    frame["hour_sin"] = np.sin(2.0 * np.pi * timestamps.dt.hour / 24.0)
    frame["hour_cos"] = np.cos(2.0 * np.pi * timestamps.dt.hour / 24.0)
    frame["minute_sin"] = np.sin(2.0 * np.pi * minute_of_day / 1440.0)
    frame["minute_cos"] = np.cos(2.0 * np.pi * minute_of_day / 1440.0)
    frame["dow_sin"] = np.sin(2.0 * np.pi * timestamps.dt.dayofweek / 7.0)
    frame["dow_cos"] = np.cos(2.0 * np.pi * timestamps.dt.dayofweek / 7.0)
    frame["month_sin"] = np.sin(2.0 * np.pi * (timestamps.dt.month - 1) / 12.0)
    frame["month_cos"] = np.cos(2.0 * np.pi * (timestamps.dt.month - 1) / 12.0)
    frame = frame.ffill().bfill()

    meta = {
        "time_col": time_col,
        "original_irradiance_col": irr_col,
        "original_temperature_col": temp_col,
        "feature_cols": list(frame.columns),
        "target_cols": TARGET_COLS.copy(),
        "timestamps": timestamps,
        "step_minutes": infer_step_minutes(timestamps),
    }
    return frame, meta


# 数据集和模型

class WeatherWindowDataset(Dataset):
    # 把连续时间序列切成：过去 input_len 个点 -> 下一时刻目标值

    def __init__(self, features: np.ndarray, targets: np.ndarray, timestamps: np.ndarray, input_len: int) -> None:
        self.features = torch.tensor(features, dtype=torch.float32)
        self.targets = torch.tensor(targets, dtype=torch.float32)
        self.timestamps = np.asarray(timestamps, dtype="datetime64[ns]")
        self.input_len = int(input_len)

    def __len__(self) -> int:
        return max(len(self.features) - self.input_len, 0)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        end = idx + self.input_len
        return self.features[idx:end], self.targets[end]

    def target_timestamps(self) -> np.ndarray:
        return self.timestamps[self.input_len : self.input_len + len(self)]


class WeatherLSTMModel(nn.Module):
    # 双层 LSTM，同时输出下一时刻辐照度和温度

    def __init__(self, input_size: int, hidden_size: int = HIDDEN_SIZE, output_size: int = 2) -> None:
        super().__init__()
        self.lstm1 = nn.LSTM(input_size=input_size, hidden_size=hidden_size, batch_first=True)
        self.dropout1 = nn.Dropout(DROPOUT)
        self.lstm2 = nn.LSTM(input_size=hidden_size, hidden_size=hidden_size // 2, batch_first=True)
        self.dropout2 = nn.Dropout(DROPOUT)
        self.head = nn.Sequential(
            nn.Linear(hidden_size // 2, hidden_size // 4),
            nn.ReLU(),
            nn.Dropout(DROPOUT),
            nn.Linear(hidden_size // 4, output_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x, _ = self.lstm1(x)
        x = self.dropout1(x)
        x, _ = self.lstm2(x)
        x = self.dropout2(x)
        return self.head(x[:, -1, :])


def postprocess_predictions(values: np.ndarray) -> np.ndarray:
    # 辐照度不允许小于 0，温度不做额外裁剪
    output = np.asarray(values, dtype=np.float32).copy()
    if output.ndim == 1:
        output[0] = max(0.0, output[0])
    else:
        output[:, 0] = np.maximum(output[:, 0], 0.0)
    return output


def make_dataset(feature_frame: pd.DataFrame, target_frame: pd.DataFrame, timestamps: np.ndarray, scaler_x, scaler_y, input_len: int):
    # 使用训练集上拟合好的归一化器构造 Dataset
    return WeatherWindowDataset(
        scaler_x.transform(feature_frame.values),
        scaler_y.transform(target_frame[TARGET_COLS].values),
        timestamps,
        input_len,
    )


def build_datasets(file_path: str | Path, input_len: int = INPUT_LEN, data_fraction: float = DATA_FRACTION):
    # 按时间顺序划分训练集、验证集和测试集
    frame, meta = prepare_weather_dataframe(file_path)
    original_rows = len(frame)
    data_fraction = float(np.clip(data_fraction, 0.05, 1.0))

    if data_fraction < 1.0:
        used_rows = min(original_rows, max(input_len + OUTPUT_LEN + 10, int(round(original_rows * data_fraction))))
        frame = frame.iloc[:used_rows].copy()
        meta["timestamps"] = meta["timestamps"].iloc[:used_rows].reset_index(drop=True)

    if len(frame) < input_len + OUTPUT_LEN + 10:
        raise ValueError(f"样本数量不足，当前行数: {len(frame)}")

    timestamps = meta["timestamps"].to_numpy(dtype="datetime64[ns]")
    future_start = int(len(frame) * TRAIN_RATIO)
    val_rows = max(input_len + OUTPUT_LEN, int(future_start * VAL_RATIO))
    train_rows = future_start - val_rows
    if train_rows <= input_len + OUTPUT_LEN:
        raise ValueError("当前 input_len 下训练集样本过少。")

    train_df = frame.iloc[:train_rows].copy()
    val_df = frame.iloc[train_rows:future_start].copy()
    test_df = frame.iloc[future_start - input_len :].copy()

    scaler_x = SafeMinMaxScaler()
    scaler_y = SafeMinMaxScaler()
    scaler_x.fit(train_df.values)
    scaler_y.fit(train_df[TARGET_COLS].values)

    train_set = make_dataset(train_df, train_df, timestamps[:train_rows], scaler_x, scaler_y, input_len)
    val_set = make_dataset(val_df, val_df, timestamps[train_rows:future_start], scaler_x, scaler_y, input_len)
    test_set = make_dataset(test_df, test_df, timestamps[future_start - input_len :], scaler_x, scaler_y, input_len)

    meta.update(
        {
            "n_features": len(meta["feature_cols"]),
            "original_rows": original_rows,
            "used_rows": len(frame),
            "data_fraction": data_fraction,
            "n_train": len(train_df),
            "n_val": len(val_df),
            "n_test": max(0, len(test_df) - input_len),
            "future_start_idx": future_start,
            "train_ratio": TRAIN_RATIO,
            "input_len": input_len,
            "output_len": OUTPUT_LEN,
        }
    )
    return train_set, val_set, test_set, scaler_x, scaler_y, meta


# 训练和评估

def make_loader(dataset, batch_size: int, shuffle: bool, drop_last: bool = False) -> DataLoader:
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, drop_last=drop_last)


def weighted_loss(output, target, huber, l1, weights: torch.Tensor) -> torch.Tensor:
    # 辐照度和温度使用不同权重
    base = (1.0 - L1_MIX) * huber(output, target) + L1_MIX * l1(output, target)
    return (base * weights).mean()


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    # 计算 MAE、RMSE、NMAE 三个常用误差指标
    metrics: dict[str, float] = {}
    for idx, name in enumerate(TARGET_COLS):
        err = y_pred[:, idx] - y_true[:, idx]
        denom = max(float(np.mean(np.abs(y_true[:, idx]))), 1e-8)
        metrics[f"{name}_mae"] = float(np.mean(np.abs(err)))
        metrics[f"{name}_rmse"] = float(math.sqrt(np.mean(err * err)))
        metrics[f"{name}_nmae"] = float(np.mean(np.abs(err)) / denom)
    return metrics


def run_model(model: nn.Module, loader, scaler_y: SafeMinMaxScaler) -> tuple[np.ndarray, np.ndarray]:
    # 在指定数据集上推理，并把归一化结果还原成真实物理量
    model.eval()
    y_true_list, y_pred_list = [], []
    with torch.no_grad():
        for x, y in loader:
            x = x.to(DEVICE)
            pred_scaled = model(x).cpu().numpy()
            true = scaler_y.inverse_transform(y.numpy())
            pred = postprocess_predictions(scaler_y.inverse_transform(pred_scaled))
            y_true_list.append(true)
            y_pred_list.append(pred)
    if not y_true_list:
        empty = np.empty((0, len(TARGET_COLS)), dtype=np.float32)
        return empty, empty
    return np.vstack(y_true_list), np.vstack(y_pred_list)


def evaluate(model, loader, scaler_y, huber, l1, weights) -> tuple[float, dict[str, float]]:
    # 验证阶段既计算归一化损失，也计算还原后的物理量误差
    model.eval()
    losses = []
    with torch.no_grad():
        for x, y in loader:
            x = x.to(DEVICE)
            y = y.to(DEVICE)
            loss = weighted_loss(model(x), y, huber, l1, weights)
            losses.append(float(loss.item()))
    y_true, y_pred = run_model(model, loader, scaler_y)
    return float(np.mean(losses)) if losses else float("inf"), compute_metrics(y_true, y_pred)


def save_checkpoint(path: str | Path, model: nn.Module, optimizer, scaler_x, scaler_y, meta: dict[str, Any], history: dict) -> None:
    # 保存模型权重、归一化参数和运行阶段需要的元信息
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict(),
            "input_len": meta["input_len"],
            "output_len": meta["output_len"],
            "n_features_per_timestep": meta["n_features"],
            "feature_cols": meta["feature_cols"],
            "target_cols": meta["target_cols"],
            "original_irradiance_col": meta["original_irradiance_col"],
            "original_temperature_col": meta["original_temperature_col"],
            "time_col": meta["time_col"],
            "step_minutes": meta["step_minutes"],
            "data_fraction": meta["data_fraction"],
            "original_rows": meta["original_rows"],
            "used_rows": meta["used_rows"],
            "train_ratio": meta["train_ratio"],
            "future_start_idx": meta["future_start_idx"],
            "irradiance_loss_weight": meta["irradiance_loss_weight"],
            "temperature_loss_weight": meta["temperature_loss_weight"],
            "feature_scaler_min": scaler_x.min_.tolist(),
            "feature_scaler_max": scaler_x.max_.tolist(),
            "feature_scaler_scale": scaler_x.scale_.tolist(),
            "target_scaler_min": scaler_y.min_.tolist(),
            "target_scaler_max": scaler_y.max_.tolist(),
            "target_scaler_scale": scaler_y.scale_.tolist(),
            "history": history,
        },
        str(path),
    )


def load_checkpoint_into_model(model: nn.Module, checkpoint_path: str | Path) -> None:
    # 将指定 pth 权重加载到当前模型中
    checkpoint_path = Path(checkpoint_path)
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Checkpoint file not found: {checkpoint_path}")
    checkpoint = torch.load(str(checkpoint_path), map_location=DEVICE, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()


def combine_best_predictions(y_true_irr, y_pred_irr, y_pred_temp) -> tuple[np.ndarray, np.ndarray]:
    # 输出辐照度取辐照度最佳模型，温度取温度最佳模型
    n = min(len(y_true_irr), len(y_pred_irr), len(y_pred_temp))
    if n <= 0:
        empty = np.empty((0, len(TARGET_COLS)), dtype=np.float32)
        return empty, empty
    return y_true_irr[:n], np.column_stack([y_pred_irr[:n, 0], y_pred_temp[:n, 1]])


def build_prediction_frame(dataset: WeatherWindowDataset, y_true: np.ndarray, y_pred: np.ndarray) -> pd.DataFrame:
    # 整理测试集预测结果
    timestamps = dataset.target_timestamps()[: len(y_pred)]
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(timestamps),
            "pred_irradiance": y_pred[:, 0],
            "pred_temperature": y_pred[:, 1],
            "true_irradiance": y_true[:, 0],
            "true_temperature": y_true[:, 1],
            "irradiance_error": y_pred[:, 0] - y_true[:, 0],
            "temperature_error": y_pred[:, 1] - y_true[:, 1],
        }
    )


def print_metrics(prefix: str, metrics: dict[str, float]) -> None:
    # 控制台打印关键误差
    print(
        f"[{prefix}] "
        f"Irr MAE={metrics['irradiance_mae']:.4f} W/m2, "
        f"Irr NMAE={metrics['irradiance_nmae']*100:.2f}%, "
        f"Temp MAE={metrics['temperature_mae']:.4f} C, "
        f"Temp NMAE={metrics['temperature_nmae']*100:.2f}%"
    )


# 结果输出

def nmae_percent(y_true, y_pred) -> float:
    denom = max(float(np.mean(np.abs(y_true))), 1e-9)
    return float(np.mean(np.abs(y_pred - y_true)) / denom * 100.0)


def plot_one_curve(frame: pd.DataFrame, true_col: str, pred_col: str, title: str, ylabel: str, path: Path) -> None:
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    formatter = mdates.ConciseDateFormatter(locator)
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(frame["timestamp"], frame[true_col], label="真实值", linewidth=1.4)
    ax.plot(frame["timestamp"], frame[pred_col], label="预测值", linestyle="--", linewidth=1.4)
    ax.set_title(title)
    ax.set_xlabel("Time")
    ax.set_ylabel(ylabel)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    ax.legend()
    ax.grid(True)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved plot: {path}")


def plot_prediction_results(prediction_frame: pd.DataFrame, output_dir: Path, max_points: int = 2880) -> None:
    # 训练结束后保存图片
    if not HAS_MATPLOTLIB:
        print("[Skip] matplotlib is not installed. Prediction plots will be skipped.")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = prediction_frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
    full = frame.copy()
    if max_points > 0 and len(frame) > max_points:
        frame = frame.iloc[:max_points].copy()

    irr_nmae = nmae_percent(full["true_irradiance"].to_numpy(float), full["pred_irradiance"].to_numpy(float))
    temp_nmae = nmae_percent(full["true_temperature"].to_numpy(float), full["pred_temperature"].to_numpy(float))
    plot_one_curve(
        frame,
        "true_irradiance",
        "pred_irradiance",
        f"Irradiance prediction vs true, test NMAE = {irr_nmae:.2f}%",
        "Irradiance [W/m2]",
        output_dir / "irradiance_prediction_vs_true.png",
    )
    plot_one_curve(
        frame,
        "true_temperature",
        "pred_temperature",
        f"Temperature prediction vs true, test NMAE = {temp_nmae:.2f}%",
        "Temperature [degC]",
        output_dir / "temperature_prediction_vs_true.png",
    )


# 主训练流程

def train_model(args: argparse.Namespace) -> dict[str, Any]:
    set_seed()
    data_path = resolve_weather_data_path(args.data)
    train_set, val_set, test_set, scaler_x, scaler_y, meta = build_datasets(data_path, args.input_len, DATA_FRACTION)
    meta["irradiance_loss_weight"] = IRRADIANCE_LOSS_WEIGHT
    meta["temperature_loss_weight"] = TEMPERATURE_LOSS_WEIGHT

    print(f"Weather file: {data_path}")
    print(f"Rows train/val/test: {meta['n_train']} / {meta['n_val']} / {meta['n_test']}")
    print(f"Windows train/val/test: {len(train_set)} / {len(val_set)} / {len(test_set)}")
    print(f"Feature columns: {meta['feature_cols']}")
    print(f"Device: {DEVICE}")

    train_loader = make_loader(train_set, args.batch_size, shuffle=True, drop_last=True)
    val_loader = make_loader(val_set, args.batch_size * 2, shuffle=False)
    test_loader = make_loader(test_set, args.batch_size * 2, shuffle=False)

    model = WeatherLSTMModel(input_size=meta["n_features"]).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=INIT_LR, weight_decay=WEIGHT_DECAY)
    huber = nn.HuberLoss(delta=1.0, reduction="none")
    l1 = nn.L1Loss(reduction="none")
    weights = torch.tensor([IRRADIANCE_LOSS_WEIGHT, TEMPERATURE_LOSS_WEIGHT], dtype=torch.float32, device=DEVICE)

    best_irr_path = Path(args.irradiance_model_out or DEFAULT_IRRADIANCE_MODEL_PATH)
    best_temp_path = Path(args.temperature_model_out or DEFAULT_TEMPERATURE_MODEL_PATH)
    best_irr_mae = float("inf")
    best_temp_mae = float("inf")
    best_irr_val_metrics: dict[str, float] = {}
    best_temp_val_metrics: dict[str, float] = {}
    patience_counter = 0
    history = {"train_loss": [], "val_loss": [], "val_irradiance_mae": [], "val_temperature_mae": []}

    for epoch in range(args.epochs):
        model.train()
        train_losses = []
        for x, y in train_loader:
            x = x.to(DEVICE)
            y = y.to(DEVICE)
            if INPUT_NOISE_STD > 0.0:
                x = x + torch.randn_like(x) * INPUT_NOISE_STD
            optimizer.zero_grad(set_to_none=True)
            loss = weighted_loss(model(x), y, huber, l1, weights)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(float(loss.item()))

        train_loss = float(np.mean(train_losses)) if train_losses else float("inf")
        val_loss, val_metrics = evaluate(model, val_loader, scaler_y, huber, l1, weights)
        val_irr_mae = float(val_metrics.get("irradiance_mae", float("inf")))
        val_temp_mae = float(val_metrics.get("temperature_mae", float("inf")))
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_irradiance_mae"].append(val_irr_mae)
        history["val_temperature_mae"].append(val_temp_mae)

        print(
            f"[Epoch {epoch + 1:03d}/{args.epochs}] "
            f"Train Loss: {train_loss:.6f} | Val Loss: {val_loss:.6f} | "
            f"Val Irr MAE: {val_irr_mae:.4f} W/m2 | "
            f"Val Temp MAE: {val_temp_mae:.4f} C"
        )

        improved = False
        if val_irr_mae < best_irr_mae:
            best_irr_mae = val_irr_mae
            best_irr_val_metrics = dict(val_metrics)
            save_checkpoint(best_irr_path, model, optimizer, scaler_x, scaler_y, meta, history)
            print(f"Saved new irradiance-best model: {best_irr_path}")
            improved = True
        if val_temp_mae < best_temp_mae:
            best_temp_mae = val_temp_mae
            best_temp_val_metrics = dict(val_metrics)
            save_checkpoint(best_temp_path, model, optimizer, scaler_x, scaler_y, meta, history)
            print(f"Saved new temperature-best model: {best_temp_path}")
            improved = True

        patience_counter = 0 if improved else patience_counter + 1
        if patience_counter >= args.patience:
            print(f"Early stopping triggered after {args.patience} epochs without improvement.")
            break

    # 分别加载两个最佳模型，再组合成最终输出
    load_checkpoint_into_model(model, best_irr_path)
    y_true_irr, y_pred_irr = run_model(model, test_loader, scaler_y)
    irr_test_metrics = compute_metrics(y_true_irr, y_pred_irr)

    load_checkpoint_into_model(model, best_temp_path)
    _, y_pred_temp = run_model(model, test_loader, scaler_y)
    temp_test_metrics = compute_metrics(y_true_irr, y_pred_temp)

    y_true, y_pred = combine_best_predictions(y_true_irr, y_pred_irr, y_pred_temp)
    test_metrics = compute_metrics(y_true, y_pred)
    print_metrics("IRRADIANCE-BEST TEST", irr_test_metrics)
    print_metrics("TEMPERATURE-BEST TEST", temp_test_metrics)
    print_metrics("COMBINED TEST", test_metrics)

    # 保存 CSV、图像和 JSON 指标
    test_csv_path = Path(args.test_csv_out or DEFAULT_TEST_CSV_PATH)
    metrics_path = Path(args.metrics_out or DEFAULT_METRICS_PATH)
    test_csv_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    test_frame = build_prediction_frame(test_set, y_true, y_pred)
    test_frame.to_csv(test_csv_path, index=False, encoding="utf-8-sig")
    plot_prediction_results(test_frame, test_csv_path.parent, max_points=args.plot_points)

    summary = {
        "data_path": str(data_path),
        "best_irradiance_model_path": str(best_irr_path),
        "best_temperature_model_path": str(best_temp_path),
        "test_csv_path": str(test_csv_path),
        "best_val_irradiance_mae": best_irr_mae,
        "best_val_temperature_mae": best_temp_mae,
        "best_irradiance_val_metrics": best_irr_val_metrics,
        "best_temperature_val_metrics": best_temp_val_metrics,
        "metrics": {
            "combined_test": test_metrics,
            "irradiance_best_test": irr_test_metrics,
            "temperature_best_test": temp_test_metrics,
        },
        "history": history,
        "feature_cols": meta["feature_cols"],
        "target_cols": meta["target_cols"],
        "step_minutes": meta["step_minutes"],
        "input_len": args.input_len,
        "train_ratio": meta["train_ratio"],
        "future_start_idx": meta["future_start_idx"],
    }
    metrics_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved combined test predictions: {test_csv_path}")
    print(f"Saved metrics summary: {metrics_path}")
    return summary


# 命令行入口

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练光伏辐照度/温度 LSTM 预测模型，并分别保存两个最佳 checkpoint。")
    parser.add_argument("--data", type=str, default=None, help="天气数据文件路径；为空时使用文件开头 WEATHER_DATA_PATH。")
    parser.add_argument("--epochs", type=int, default=NUM_EPOCHS, help="训练轮数。")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="批大小。")
    parser.add_argument("--patience", type=int, default=EARLY_STOPPING_PATIENCE, help="早停等待轮数。")
    parser.add_argument("--input-len", type=int, default=INPUT_LEN, help="历史输入窗口长度。")
    parser.add_argument("--plot-points", type=int, default=2880, help="预测图中显示的测试点数，0 表示显示全部。")
    parser.add_argument("--irradiance-model-out", type=str, default=str(DEFAULT_IRRADIANCE_MODEL_PATH), help="辐照度最佳 pth 保存路径。")
    parser.add_argument("--temperature-model-out", type=str, default=str(DEFAULT_TEMPERATURE_MODEL_PATH), help="温度最佳 pth 保存路径。")
    parser.add_argument("--metrics-out", type=str, default=str(DEFAULT_METRICS_PATH), help="指标 JSON 保存路径。")
    parser.add_argument("--test-csv-out", type=str, default=str(DEFAULT_TEST_CSV_PATH), help="测试集预测 CSV 保存路径。")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    train_model(args)


if __name__ == "__main__":
    main()
