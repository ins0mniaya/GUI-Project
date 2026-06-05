# 风速/空气密度预测模型训练文件
# 读取历史风速和空气密度数据；
# 构造 LSTM 训练样本；
# 分别保存“风速验证指标最优”和“空气密度验证指标最优”两个 pth；
# 训练结束后只生成最终测试集预测 CSV、指标 JSON 和两张预测对比图。
from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.optim.lr_scheduler import CosineAnnealingWarmRestarts
from torch.utils.data import DataLoader, Dataset

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    plt = None

try:
    from ..wind_speed_density_extract import (
        DEFAULT_AIR_DENSITY_MODEL_PATH,
        DEFAULT_WIND_SPEED_MODEL_WEIGHT,
        DEFAULT_METRICS_PATH,
        DEFAULT_TEST_CSV_PATH,
        DEFAULT_WIND_SPEED_MODEL_PATH,
        DEVICE,
        DROPOUT,
        HIDDEN_SIZE,
        INPUT_LEN,
        OUTPUT_LEN,
        ROLLING_HISTORY_RATIO,
        SafeMinMaxScaler,
        TARGET_COLS,
        WIND_SPEED_TARGET_MODE,
        WindLSTMModel,
        prepare_wind_dataframe,
        postprocess_predictions,
        uses_delta_wind_speed_target,
    )
except ImportError:
    import sys

    PACKAGE_ROOT = Path(__file__).resolve().parents[1]
    if str(PACKAGE_ROOT) not in sys.path:
        sys.path.insert(0, str(PACKAGE_ROOT))

    from wind_speed_density_extract import (
        DEFAULT_AIR_DENSITY_MODEL_PATH,
        DEFAULT_WIND_SPEED_MODEL_WEIGHT,
        DEFAULT_METRICS_PATH,
        DEFAULT_TEST_CSV_PATH,
        DEFAULT_WIND_SPEED_MODEL_PATH,
        DEVICE,
        DROPOUT,
        HIDDEN_SIZE,
        INPUT_LEN,
        OUTPUT_LEN,
        ROLLING_HISTORY_RATIO,
        SafeMinMaxScaler,
        TARGET_COLS,
        WIND_SPEED_TARGET_MODE,
        WindLSTMModel,
        prepare_wind_dataframe,
        postprocess_predictions,
        uses_delta_wind_speed_target,
    )


# 路径和训练参数
# 在这里修改风电训练数据文件路径
WIND_DATA_PATH = Path(__file__).resolve().parent / "风速空气密度预测.xlsx"

def resolve_training_wind_data_path(data_path: str | Path | None = None) -> Path:
    path = Path(data_path) if data_path is not None else WIND_DATA_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"风电训练数据文件不存在: {path}\n"
            f"请在 wind_speed_density_train.py 文件前面修改 WIND_DATA_PATH，"
            f"或者运行时通过 --data 指定数据文件。"
        )
    return path


SEED = 42
DATA_FRACTION = 1.0
TRAIN_RATIO = 0.60
VAL_RATIO = 0.15
BATCH_SIZE = 128
NUM_EPOCHS = 120
INIT_LR = 8e-4
MIN_LR = 1e-6
WARMUP_EPOCHS = 5
COSINE_T0 = 20
COSINE_T_MULT = 1
EARLY_STOPPING_PATIENCE = 20
L1_MIX = 0.50
WEIGHT_DECAY = 1e-4
INPUT_NOISE_STD = 0.01
WIND_SPEED_LOSS_WEIGHT = 1.5
AIR_DENSITY_LOSS_WEIGHT = 1.0
WIND_SPEED_MODEL_WEIGHT_MIN = 0.0
WIND_SPEED_MODEL_WEIGHT_MAX = 1.5


def set_seed(seed: int = SEED) -> None:
    # 固定随机种子，尽量保证每次训练结果可复现。
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


class WindWindowDataset(Dataset):
    # 把连续风电数据切成 LSTM 可用的滑动窗口样本
    def __init__(self, features: np.ndarray, targets: np.ndarray, timestamps: np.ndarray, input_len: int) -> None:
        # 保存归一化后的特征、目标值和对应时间戳
        self.features = torch.tensor(features, dtype=torch.float32)
        self.targets = torch.tensor(targets, dtype=torch.float32)
        self.timestamps = np.asarray(timestamps, dtype="datetime64[ns]")
        self.input_len = input_len

    def __len__(self) -> int:
        return max(len(self.features) - self.input_len, 0)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        end = idx + self.input_len
        return self.features[idx:end], self.targets[end]

    def target_timestamps(self) -> np.ndarray:
        return self.timestamps[self.input_len : self.input_len + len(self)]


def build_target_frame(frame: pd.DataFrame) -> pd.DataFrame:
    targets = frame[TARGET_COLS].copy()
    if uses_delta_wind_speed_target(WIND_SPEED_TARGET_MODE):
        targets["wind_speed"] = frame["wind_speed"].diff().fillna(0.0)
    return targets


def last_observed_wind_speed_from_features(values, feature_scaler: SafeMinMaxScaler, feature_cols: list[str]) -> np.ndarray:
    values = values.detach().cpu().numpy() if isinstance(values, torch.Tensor) else np.asarray(values)
    index = feature_cols.index("wind_speed")
    return values[:, -1, index] * feature_scaler.scale_[index] + feature_scaler.min_[index]


def apply_wind_speed_persistence_blend(y_pred: np.ndarray, last_wind: np.ndarray, model_weight: float) -> np.ndarray:
    # 将 LSTM 风速预测与上一时刻风速做加权融合
    output = np.asarray(y_pred, dtype=np.float32).copy()
    weight = float(np.clip(model_weight, WIND_SPEED_MODEL_WEIGHT_MIN, WIND_SPEED_MODEL_WEIGHT_MAX))
    output[:, 0] = weight * output[:, 0] + (1.0 - weight) * last_wind
    return postprocess_predictions(output)


def optimize_wind_speed_model_weight(y_true: np.ndarray, y_pred: np.ndarray, last_wind: np.ndarray) -> tuple[float, float]:
    # 在验证集上搜索最佳融合权重
    denominator = max(float(np.mean(np.abs(y_true[:, 0]))), 1e-8)
    best_weight, best_nmae = DEFAULT_WIND_SPEED_MODEL_WEIGHT, float("inf")
    for weight in np.linspace(WIND_SPEED_MODEL_WEIGHT_MIN, WIND_SPEED_MODEL_WEIGHT_MAX, 51):
        blended = weight * y_pred[:, 0] + (1.0 - weight) * last_wind
        nmae = float(np.mean(np.abs(blended - y_true[:, 0])) / denominator)
        if nmae < best_nmae:
            best_weight, best_nmae = float(weight), nmae
    return best_weight, best_nmae


def build_datasets(file_path: str | Path, input_len: int = INPUT_LEN, data_fraction: float = DATA_FRACTION):
    # 读取数据、划分训练/验证/测试集，并完成归一化和窗口化
    frame, metadata = prepare_wind_dataframe(file_path)
    original_rows = len(frame)
    data_fraction = float(np.clip(data_fraction, 0.05, 1.0))
    if data_fraction < 1.0:
        used_rows = min(original_rows, max(input_len + OUTPUT_LEN + 10, int(round(original_rows * data_fraction))))
        frame = frame.iloc[:used_rows].copy()
        metadata["timestamps"] = metadata["timestamps"].iloc[:used_rows].reset_index(drop=True)
    timestamps = metadata["timestamps"].to_numpy(dtype="datetime64[ns]")
    future_start = int(len(frame) * TRAIN_RATIO)
    val_rows = max(input_len + OUTPUT_LEN, int(future_start * VAL_RATIO))
    train_rows = future_start - val_rows
    if train_rows <= input_len + OUTPUT_LEN:
        raise ValueError("Training split is too small for the selected input length.")

    targets = build_target_frame(frame)
    splits = {
        "train": (frame.iloc[:train_rows], targets.iloc[:train_rows], timestamps[:train_rows]),
        "val": (frame.iloc[train_rows:future_start], targets.iloc[train_rows:future_start], timestamps[train_rows:future_start]),
        "test": (frame.iloc[future_start - input_len :], targets.iloc[future_start - input_len :], timestamps[future_start - input_len :]),
    }
    feature_scaler, target_scaler = SafeMinMaxScaler(), SafeMinMaxScaler()
    feature_scaler.fit(splits["train"][0].values)
    target_scaler.fit(splits["train"][1][TARGET_COLS].values)
    datasets = []
    for feature_frame, target_frame, split_timestamps in splits.values():
        datasets.append(
            WindWindowDataset(
                feature_scaler.transform(feature_frame.values),
                target_scaler.transform(target_frame[TARGET_COLS].values),
                split_timestamps,
                input_len,
            )
        )
    meta = {
        **metadata,
        "wind_speed_target_mode": WIND_SPEED_TARGET_MODE,
        "n_features": len(metadata["feature_cols"]),
        "original_rows": original_rows,
        "used_rows": len(frame),
        "data_fraction": data_fraction,
        "n_train": train_rows,
        "n_val": val_rows,
        "n_test": len(frame) - future_start,
        "n_future": len(frame) - future_start,
        "future_start_idx": future_start,
        "train_ratio": TRAIN_RATIO,
        "rolling_history_ratio": ROLLING_HISTORY_RATIO,
        "input_len": input_len,
        "output_len": OUTPUT_LEN,
    }
    return *datasets, feature_scaler, target_scaler, meta


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    # 计算 MAE、RMSE 和归一化 MAE
    metrics = {}
    for index, name in enumerate(TARGET_COLS):
        error = y_pred[:, index] - y_true[:, index]
        denominator = max(float(np.mean(np.abs(y_true[:, index]))), 1e-8)
        metrics[f"{name}_mae"] = float(np.mean(np.abs(error)))
        metrics[f"{name}_rmse"] = float(np.sqrt(np.mean(error**2)))
        metrics[f"{name}_nmae"] = float(np.mean(np.abs(error)) / denominator)
    return metrics


def run_model_on_loader(
    model: nn.Module,
    loader,
    target_scaler: SafeMinMaxScaler,
    device: str = DEVICE,
    feature_scaler: SafeMinMaxScaler | None = None,
    feature_cols: list[str] | None = None,
    wind_speed_model_weight: float | None = None,
    wind_speed_target_mode: str = WIND_SPEED_TARGET_MODE,
    return_last_wind_speed: bool = False,
):
    # 在 DataLoader 上批量推理，并把结果反归一化为真实物理量
    true_batches, pred_batches, last_batches = [], [], []
    model.eval()
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            true = target_scaler.inverse_transform(y.cpu().numpy())
            pred = target_scaler.inverse_transform(model(x).cpu().numpy())
            last_wind = last_observed_wind_speed_from_features(x, feature_scaler, feature_cols)
            if uses_delta_wind_speed_target(wind_speed_target_mode):
                true[:, 0] += last_wind
                pred[:, 0] += last_wind
            pred = postprocess_predictions(pred)
            true = postprocess_predictions(true)
            if wind_speed_model_weight is not None:
                pred = apply_wind_speed_persistence_blend(pred, last_wind, wind_speed_model_weight)
            true_batches.append(true)
            pred_batches.append(pred)
            last_batches.append(last_wind)
    empty = np.empty((0, len(TARGET_COLS)), dtype=np.float32)
    true = np.vstack(true_batches) if true_batches else empty
    pred = np.vstack(pred_batches) if pred_batches else empty
    if return_last_wind_speed:
        return true, pred, np.concatenate(last_batches) if last_batches else np.empty((0,), dtype=np.float32)
    return true, pred


def build_prediction_frame(dataset: WindWindowDataset, y_true: np.ndarray, y_pred: np.ndarray) -> pd.DataFrame:
    # 把预测值和真实值整理成便于保存的表格
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(dataset.target_timestamps()[: len(y_pred)]),
            "pred_wind_speed": y_pred[:, 0],
            "pred_air_density": y_pred[:, 1],
            "true_wind_speed": y_true[:, 0],
            "true_air_density": y_true[:, 1],
        }
    )


def save_checkpoint(
    path: str | Path,
    model: nn.Module,
    optimizer: torch.optim.Optimizer,
    feature_scaler,
    target_scaler,
    meta: dict,
    history: dict,
) -> None:
    # 保存模型权重、优化器状态、归一化参数和训练元信息
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
            "original_wind_speed_col": meta["original_wind_speed_col"],
            "original_air_density_col": meta["original_air_density_col"],
            "time_col": meta["time_col"],
            "step_minutes": meta["step_minutes"],
            "data_fraction": meta["data_fraction"],
            "original_rows": meta["original_rows"],
            "used_rows": meta["used_rows"],
            "wind_speed_loss_weight": meta["wind_speed_loss_weight"],
            "air_density_loss_weight": meta["air_density_loss_weight"],
            "wind_speed_model_weight": meta.get("wind_speed_model_weight", DEFAULT_WIND_SPEED_MODEL_WEIGHT),
            "wind_speed_target_mode": meta.get("wind_speed_target_mode", WIND_SPEED_TARGET_MODE),
            "train_ratio": meta["train_ratio"],
            "future_start_idx": meta["future_start_idx"],
            "feature_scaler_min": feature_scaler.min_.tolist(),
            "feature_scaler_max": feature_scaler.max_.tolist(),
            "feature_scaler_scale": feature_scaler.scale_.tolist(),
            "target_scaler_min": target_scaler.min_.tolist(),
            "target_scaler_max": target_scaler.max_.tolist(),
            "target_scaler_scale": target_scaler.scale_.tolist(),
            "history": history,
        },
        str(path),
    )


def weighted_prediction_loss(output, target, criterion_huber, criterion_l1, target_weights):
    # 按照风速和空气密度权重计算组合损失
    base_loss = (1.0 - L1_MIX) * criterion_huber(output, target) + L1_MIX * criterion_l1(output, target)
    return (base_loss * target_weights).mean()


def _is_cuda_device() -> bool:
    return str(DEVICE).startswith("cuda") and torch.cuda.is_available()


def _autocast_context(use_amp: bool):
    if use_amp and _is_cuda_device():
        if hasattr(torch, "amp"):
            return torch.amp.autocast("cuda")
        return torch.cuda.amp.autocast()
    return nullcontext()


def _make_grad_scaler(use_amp: bool):
    if not (use_amp and _is_cuda_device()):
        return None
    try:
        return torch.amp.GradScaler("cuda", enabled=True)
    except TypeError:
        return torch.cuda.amp.GradScaler(enabled=True)


def _to_device(tensor: torch.Tensor) -> torch.Tensor:
    return tensor.to(DEVICE, non_blocking=_is_cuda_device())


def make_data_loader(dataset, batch_size: int, shuffle: bool, drop_last: bool = False, num_workers: int = 0) -> DataLoader:
    kwargs = {
        "batch_size": batch_size,
        "shuffle": shuffle,
        "drop_last": drop_last,
        "pin_memory": _is_cuda_device(),
    }
    if num_workers > 0:
        kwargs["num_workers"] = int(num_workers)
        kwargs["persistent_workers"] = True
        kwargs["prefetch_factor"] = 2
    return DataLoader(dataset, **kwargs)


def evaluate_loss_and_metrics(
    model,
    loader,
    target_scaler,
    criterion_huber,
    criterion_l1,
    target_weights,
    feature_scaler=None,
    feature_cols: list[str] | None = None,
    wind_speed_model_weight: float | None = None,
    optimize_wind_blend: bool = False,
    use_amp: bool = False,
) -> tuple[float, dict]:
    # 在验证集或测试集上计算损失和误差指标
    model.eval()
    losses: list[float] = []
    y_true_list: list[np.ndarray] = []
    y_pred_list: list[np.ndarray] = []
    last_wind_list: list[np.ndarray] = []
    with torch.no_grad():
        for x, y in loader:
            x = _to_device(x)
            y = _to_device(y)
            with _autocast_context(use_amp):
                output = model(x)
                loss = weighted_prediction_loss(output, y, criterion_huber, criterion_l1, target_weights)
            losses.append(loss.item())

            y_true_batch = target_scaler.inverse_transform(y.detach().cpu().numpy())
            y_pred_batch = target_scaler.inverse_transform(output.detach().float().cpu().numpy())
            needs_last_wind = (
                uses_delta_wind_speed_target(WIND_SPEED_TARGET_MODE)
                or optimize_wind_blend
                or wind_speed_model_weight is not None
            )
            last_wind = None
            if needs_last_wind:
                if feature_scaler is None or feature_cols is None:
                    raise ValueError("feature_scaler and feature_cols are required for wind-speed delta/blend evaluation.")
                last_wind = last_observed_wind_speed_from_features(x, feature_scaler, feature_cols)
            if uses_delta_wind_speed_target(WIND_SPEED_TARGET_MODE):
                y_true_batch = y_true_batch.copy()
                y_pred_batch = y_pred_batch.copy()
                y_true_batch[:, 0] = y_true_batch[:, 0] + last_wind
                y_pred_batch[:, 0] = y_pred_batch[:, 0] + last_wind
            y_true_batch = postprocess_predictions(y_true_batch)
            y_pred_batch = postprocess_predictions(y_pred_batch)
            if last_wind is not None:
                last_wind_list.append(last_wind)
            y_true_list.append(y_true_batch)
            y_pred_list.append(y_pred_batch)

    if y_true_list:
        y_true = np.vstack(y_true_list)
        y_pred_raw = np.vstack(y_pred_list)
    else:
        y_true = np.empty((0, len(TARGET_COLS)), dtype=np.float32)
        y_pred_raw = np.empty((0, len(TARGET_COLS)), dtype=np.float32)

    if optimize_wind_blend:
        last_wind = np.concatenate(last_wind_list) if last_wind_list else np.empty((0,), dtype=np.float32)
        best_weight, best_wind_nmae = optimize_wind_speed_model_weight(y_true, y_pred_raw, last_wind)
        y_pred = apply_wind_speed_persistence_blend(y_pred_raw, last_wind, best_weight)
        denom = float(np.mean(np.abs(y_true[:, 0]))) if len(y_true) > 0 else 1.0
        denom = denom if denom >= 1e-8 else 1.0
        raw_wind_nmae = float(np.mean(np.abs(y_pred_raw[:, 0] - y_true[:, 0])) / denom) if len(y_true) > 0 else float("inf")
        persistence_nmae = float(np.mean(np.abs(last_wind - y_true[:, 0])) / denom) if len(y_true) > 0 else float("inf")
    elif wind_speed_model_weight is not None:
        last_wind = np.concatenate(last_wind_list) if last_wind_list else np.empty((0,), dtype=np.float32)
        best_weight = wind_speed_model_weight
        best_wind_nmae = None
        raw_wind_nmae = None
        persistence_nmae = None
        y_pred = apply_wind_speed_persistence_blend(y_pred_raw, last_wind, wind_speed_model_weight)
    else:
        best_weight = wind_speed_model_weight
        best_wind_nmae = None
        raw_wind_nmae = None
        persistence_nmae = None
        y_pred = y_pred_raw

    metrics = compute_metrics(y_true, y_pred) if len(y_true) > 0 else {}
    if optimize_wind_blend:
        metrics["wind_speed_model_weight"] = float(best_weight)
        metrics["wind_speed_blended_nmae"] = float(best_wind_nmae)
        metrics["wind_speed_raw_model_nmae"] = float(raw_wind_nmae)
        metrics["wind_speed_persistence_nmae"] = float(persistence_nmae)
        metrics["wind_speed_skill_vs_persistence"] = float(
            (persistence_nmae - best_wind_nmae) / max(persistence_nmae, 1e-9)
        )
    return (float(np.mean(losses)) if losses else float("inf")), metrics


def _nmae_percent(y_true, y_pred) -> float:
    # 把归一化平均绝对误差转换成百分比
    denom = float(np.mean(np.abs(y_true)))
    if denom < 1e-9:
        denom = 1.0
    return float(np.mean(np.abs(y_pred - y_true)) / denom * 100.0)


def _error_metrics(y_true, y_pred) -> dict[str, float]:
    # 计算单个目标量的误差指标
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err * err)))
    return {"mae": mae, "rmse": rmse, "nmae": _nmae_percent(y_true, y_pred)}


def plot_prediction_results(prediction_frame, output_dir: Path, max_points: int = 2880) -> None:
    # 保存最终测试集风速和空气密度预测对比图
    if not HAS_MATPLOTLIB:
        print("[提示] 未安装 matplotlib，跳过预测对比图生成。")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    frame = prediction_frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
    full_frame = frame.copy()
    if max_points > 0 and len(frame) > max_points:
        frame = frame.iloc[:max_points].copy()
    time = frame["timestamp"]
    wind_metrics = _error_metrics(
        full_frame["true_wind_speed"].to_numpy(dtype=float),
        full_frame["pred_wind_speed"].to_numpy(dtype=float),
    )
    density_metrics = _error_metrics(
        full_frame["true_air_density"].to_numpy(dtype=float),
        full_frame["pred_air_density"].to_numpy(dtype=float),
    )

    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    formatter = mdates.ConciseDateFormatter(locator)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(time, frame["true_wind_speed"], label="True wind speed", color="tab:blue", linewidth=1.4)
    ax.plot(time, frame["pred_wind_speed"], label="Predicted wind speed", color="tab:orange", linestyle="--", linewidth=1.4)
    ax.set_title(
        "Wind speed prediction vs true, "
        f"test MAE = {wind_metrics['mae']:.3f} m/s, "
        f"RMSE = {wind_metrics['rmse']:.3f} m/s, "
        f"NMAE = {wind_metrics['nmae']:.2f}%"
    )
    ax.set_xlabel("Time")
    ax.set_ylabel("Wind speed [m/s]")
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    ax.legend()
    ax.grid(True)
    fig.tight_layout()
    wind_path = output_dir / "wind_speed_prediction_vs_true.png"
    fig.savefig(wind_path, dpi=150)
    plt.close(fig)
    print(f"Saved plot: {wind_path}")

    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    formatter = mdates.ConciseDateFormatter(locator)
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(time, frame["true_air_density"], label="True air density", color="tab:blue", linewidth=1.4)
    ax.plot(time, frame["pred_air_density"], label="Predicted air density", color="tab:orange", linestyle="--", linewidth=1.4)
    ax.set_title(
        "Air density prediction vs true, "
        f"test MAE = {density_metrics['mae']:.5f} kg/m3, "
        f"RMSE = {density_metrics['rmse']:.5f} kg/m3, "
        f"NMAE = {density_metrics['nmae']:.2f}%"
    )
    ax.set_xlabel("Time")
    ax.set_ylabel("Air density [kg/m3]")
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    ax.legend()
    ax.grid(True)
    fig.tight_layout()
    density_path = output_dir / "air_density_prediction_vs_true.png"
    fig.savefig(density_path, dpi=150)
    plt.close(fig)
    print(f"Saved plot: {density_path}")



# 在训练结束后评估最终测试集
def predict_with_checkpoint(
    model: nn.Module,
    checkpoint_path: Path,
    loader,
    target_scaler,
    feature_scaler,
    feature_cols: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    # 加载指定 checkpoint，并只在传入的 DataLoader 上推理一次
    checkpoint = torch.load(str(checkpoint_path), map_location=DEVICE, weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    wind_speed_model_weight = float(checkpoint.get("wind_speed_model_weight", DEFAULT_WIND_SPEED_MODEL_WEIGHT))
    wind_speed_target_mode = str(checkpoint.get("wind_speed_target_mode", WIND_SPEED_TARGET_MODE))
    return run_model_on_loader(
        model,
        loader,
        target_scaler,
        device=DEVICE,
        feature_scaler=feature_scaler,
        feature_cols=feature_cols,
        wind_speed_model_weight=wind_speed_model_weight,
        wind_speed_target_mode=wind_speed_target_mode,
    )


def combine_test_predictions(
    dataset: WindWindowDataset,
    y_true_wind: np.ndarray,
    y_pred_wind: np.ndarray,
    y_pred_density: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, float]]:
    # 最终输出时，风速取风速最佳模型，空气密度取空气密度最佳模型
    n = min(len(y_true_wind), len(y_pred_wind), len(y_pred_density))
    if n <= 0:
        empty = np.empty((0, len(TARGET_COLS)), dtype=np.float32)
        return build_prediction_frame(dataset, empty, empty), {}
    y_true = y_true_wind[:n]
    y_pred = np.column_stack([y_pred_wind[:n, 0], y_pred_density[:n, 1]])
    return build_prediction_frame(dataset, y_true, y_pred), compute_metrics(y_true, y_pred)

def train_model(args: argparse.Namespace) -> dict:
    # 训练主流程：训练模型、保存两个最佳 checkpoint、生成测试结果和图片
    set_seed()
    data_path = resolve_training_wind_data_path(args.data)
    train_dataset, val_dataset, test_dataset, feature_scaler, target_scaler, meta = build_datasets(
        data_path,
        input_len=args.input_len,
        data_fraction=args.data_fraction,
    )

    print(f"Wind data file: {data_path}")
    print(f"Detected targets: {meta['original_wind_speed_col']} + {meta['original_air_density_col']}")
    print(f"Data fraction: {meta['data_fraction']:.2f} ({meta['used_rows']} / {meta['original_rows']} rows)")
    print(f"Rows train/val/test: {meta['n_train']} / {meta['n_val']} / {meta['n_test']}")
    print(f"Future stream rows: {meta['n_future']} starting at row index {meta['future_start_idx']}")
    print(f"Windows train/val/test: {len(train_dataset)} / {len(val_dataset)} / {len(test_dataset)}")
    print(f"Feature columns: {meta['feature_cols']}")
    print(f"Step minutes: {meta['step_minutes']}")
    meta["wind_speed_loss_weight"] = args.wind_speed_loss_weight
    meta["air_density_loss_weight"] = args.air_density_loss_weight
    meta["wind_speed_model_weight"] = DEFAULT_WIND_SPEED_MODEL_WEIGHT
    meta["wind_speed_target_mode"] = meta.get("wind_speed_target_mode", WIND_SPEED_TARGET_MODE)
    print(f"Loss weights: wind_speed={args.wind_speed_loss_weight:.2f}, air_density={args.air_density_loss_weight:.2f}")
    print(f"Wind-speed target mode: {meta['wind_speed_target_mode']}")
    use_amp = bool(_is_cuda_device() if args.amp is None else args.amp)
    if use_amp:
        try:
            torch.set_float32_matmul_precision("high")
        except Exception:
            pass
    print(f"Device: {DEVICE} | AMP: {'on' if use_amp else 'off'} | DataLoader workers: {args.num_workers}")

    train_loader = make_data_loader(train_dataset, batch_size=args.batch_size, shuffle=True, drop_last=True, num_workers=args.num_workers)
    val_loader = make_data_loader(val_dataset, batch_size=args.batch_size * 2, shuffle=False, num_workers=args.num_workers)
    test_loader = make_data_loader(test_dataset, batch_size=args.batch_size * 2, shuffle=False, num_workers=args.num_workers)

    model = WindLSTMModel(input_size=meta["n_features"], hidden_size=HIDDEN_SIZE, output_size=len(TARGET_COLS), dropout=DROPOUT).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=INIT_LR, weight_decay=WEIGHT_DECAY)
    scaler = _make_grad_scaler(use_amp)
    scheduler = CosineAnnealingWarmRestarts(optimizer, T_0=COSINE_T0, T_mult=COSINE_T_MULT, eta_min=MIN_LR)
    criterion_huber = nn.HuberLoss(delta=1.0, reduction="none")
    criterion_l1 = nn.L1Loss(reduction="none")
    target_weights = torch.tensor(
        [args.wind_speed_loss_weight, args.air_density_loss_weight],
        dtype=torch.float32,
        device=DEVICE,
    )

    best_wind_speed_model_path = Path(args.wind_speed_model_out or DEFAULT_WIND_SPEED_MODEL_PATH)
    best_air_density_model_path = Path(args.air_density_model_out or DEFAULT_AIR_DENSITY_MODEL_PATH)
    metrics_path = Path(args.metrics_out or DEFAULT_METRICS_PATH)
    test_csv_path = Path(args.test_csv_out or DEFAULT_TEST_CSV_PATH)

    history = {
        "train_loss": [],
        "val_loss": [],
        "val_wind_speed_mae": [],
        "val_wind_speed_rmse": [],
        "val_wind_speed_nmae": [],
        "val_air_density_mae": [],
        "val_air_density_rmse": [],
        "val_air_density_nmae": [],
        "val_weighted_nmae": [],
        "val_wind_speed_model_weight": [],
        "val_wind_speed_persistence_nmae": [],
        "val_wind_speed_skill_vs_persistence": [],
    }
    best_val_weighted_nmae = float("inf")
    best_val_wind_speed_nmae = float("inf")
    best_val_air_density_nmae = float("inf")
    best_wind_speed_model_weight = DEFAULT_WIND_SPEED_MODEL_WEIGHT
    patience_counter = 0

    for epoch in range(args.epochs):
        if epoch < WARMUP_EPOCHS:
            warmup_lr = INIT_LR * float(epoch + 1) / float(max(1, WARMUP_EPOCHS))
            for param_group in optimizer.param_groups:
                param_group["lr"] = warmup_lr
        else:
            scheduler.step(epoch - WARMUP_EPOCHS)

        model.train()
        train_losses: list[float] = []
        for x, y in train_loader:
            x = _to_device(x)
            y = _to_device(y)
            if INPUT_NOISE_STD > 0:
                x = x + torch.randn_like(x) * INPUT_NOISE_STD
            optimizer.zero_grad(set_to_none=True)
            with _autocast_context(use_amp):
                output = model(x)
                loss = weighted_prediction_loss(output, y, criterion_huber, criterion_l1, target_weights)
            if scaler is not None:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
            train_losses.append(loss.item())

        avg_train_loss = float(np.mean(train_losses)) if train_losses else float("inf")
        avg_val_loss, val_metrics = evaluate_loss_and_metrics(
            model,
            val_loader,
            target_scaler,
            criterion_huber,
            criterion_l1,
            target_weights,
            feature_scaler=feature_scaler,
            feature_cols=meta["feature_cols"],
            optimize_wind_blend=True,
            use_amp=use_amp,
        )

        history["train_loss"].append(avg_train_loss)
        history["val_loss"].append(avg_val_loss)
        val_wind_speed_mae = val_metrics.get("wind_speed_mae", float("inf"))
        val_wind_speed_rmse = val_metrics.get("wind_speed_rmse", float("inf"))
        val_air_density_mae = val_metrics.get("air_density_mae", float("inf"))
        val_air_density_rmse = val_metrics.get("air_density_rmse", float("inf"))
        val_wind_speed_nmae = val_metrics.get("wind_speed_nmae", float("inf"))
        val_air_density_nmae = val_metrics.get("air_density_nmae", float("inf"))
        val_wind_speed_model_weight = val_metrics.get("wind_speed_model_weight", DEFAULT_WIND_SPEED_MODEL_WEIGHT)
        val_wind_speed_persistence_nmae = val_metrics.get("wind_speed_persistence_nmae", float("inf"))
        val_wind_speed_skill = val_metrics.get("wind_speed_skill_vs_persistence", float("nan"))
        val_weighted_nmae = (
            args.wind_speed_loss_weight * val_wind_speed_nmae
            + args.air_density_loss_weight * val_air_density_nmae
        ) / max(args.wind_speed_loss_weight + args.air_density_loss_weight, 1e-9)
        history["val_wind_speed_mae"].append(float(val_wind_speed_mae))
        history["val_wind_speed_rmse"].append(float(val_wind_speed_rmse))
        history["val_wind_speed_nmae"].append(float(val_wind_speed_nmae))
        history["val_air_density_mae"].append(float(val_air_density_mae))
        history["val_air_density_rmse"].append(float(val_air_density_rmse))
        history["val_air_density_nmae"].append(float(val_air_density_nmae))
        history["val_weighted_nmae"].append(float(val_weighted_nmae))
        history["val_wind_speed_model_weight"].append(float(val_wind_speed_model_weight))
        history["val_wind_speed_persistence_nmae"].append(float(val_wind_speed_persistence_nmae))
        history["val_wind_speed_skill_vs_persistence"].append(float(val_wind_speed_skill))

        print(
            f"[Epoch {epoch + 1:03d}/{args.epochs}] "
            f"LR: {optimizer.param_groups[0]['lr']:.2e} | "
            f"Train Loss: {avg_train_loss:.6f} | "
            f"Val Loss: {avg_val_loss:.6f} | "
            f"Val Wind MAE: {val_metrics.get('wind_speed_mae', float('nan')):.4f} m/s | "
            f"Val Wind RMSE: {val_metrics.get('wind_speed_rmse', float('nan')):.4f} m/s | "
            f"Val Wind NMAE: {val_metrics.get('wind_speed_nmae', float('nan'))*100:.2f}% | "
            f"Persistence NMAE: {val_wind_speed_persistence_nmae*100:.2f}% | "
            f"Skill: {val_wind_speed_skill*100:.1f}% | "
            f"Wind Model Gain: {val_wind_speed_model_weight:.2f} | "
            f"Val Density MAE: {val_metrics.get('air_density_mae', float('nan')):.6f} kg/m3 | "
            f"Val Density RMSE: {val_metrics.get('air_density_rmse', float('nan')):.6f} kg/m3 | "
            f"Val Density NMAE: {val_metrics.get('air_density_nmae', float('nan'))*100:.2f}% | "
            f"Val Weighted NMAE: {val_weighted_nmae*100:.2f}%"
        )

        if val_weighted_nmae < best_val_weighted_nmae:
            best_val_weighted_nmae = val_weighted_nmae
            patience_counter = 0
        else:
            patience_counter += 1

        if val_wind_speed_nmae < best_val_wind_speed_nmae:
            best_val_wind_speed_nmae = val_wind_speed_nmae
            best_wind_speed_model_weight = float(val_wind_speed_model_weight)
            meta["wind_speed_model_weight"] = float(val_wind_speed_model_weight)
            save_checkpoint(best_wind_speed_model_path, model, optimizer, feature_scaler, target_scaler, meta, history)
            print(f"Saved new wind-speed-best model by NMAE: {best_wind_speed_model_path}")
            print(f"  Wind-speed formula: {val_wind_speed_model_weight:.2f} * model + {1.0 - val_wind_speed_model_weight:.2f} * persistence")

        if val_air_density_nmae < best_val_air_density_nmae:
            best_val_air_density_nmae = val_air_density_nmae
            meta["wind_speed_model_weight"] = float(val_wind_speed_model_weight)
            save_checkpoint(best_air_density_model_path, model, optimizer, feature_scaler, target_scaler, meta, history)
            print(f"Saved new air-density-best model by NMAE: {best_air_density_model_path}")

        if patience_counter >= args.patience:
            print(f"Early stopping triggered after {args.patience} epochs without validation improvement.")
            break

    # 训练结束后在测试集上评估一次
    y_true_test_wind, y_pred_test_wind = predict_with_checkpoint(
        model,
        best_wind_speed_model_path,
        test_loader,
        target_scaler,
        feature_scaler,
        meta["feature_cols"],
    )
    _, y_pred_test_density = predict_with_checkpoint(
        model,
        best_air_density_model_path,
        test_loader,
        target_scaler,
        feature_scaler,
        meta["feature_cols"],
    )
    test_frame, test_metrics = combine_test_predictions(
        test_dataset,
        y_true_test_wind,
        y_pred_test_wind,
        y_pred_test_density,
    )

    print(
        "[FINAL TEST] "
        f"Wind MAE={test_metrics.get('wind_speed_mae', float('nan')):.4f} m/s, "
        f"Wind NMAE={test_metrics.get('wind_speed_nmae', float('nan'))*100:.2f}%, "
        f"Density MAE={test_metrics.get('air_density_mae', float('nan')):.6f} kg/m3, "
        f"Density NMAE={test_metrics.get('air_density_nmae', float('nan'))*100:.2f}%"
    )

    test_csv_path.parent.mkdir(parents=True, exist_ok=True)
    test_frame.to_csv(test_csv_path, index=False, encoding="utf-8-sig")
    print(f"Saved test predictions: {test_csv_path}")
    # 在训练结束后保存最终测试集两张对比图
    plot_prediction_results(test_frame, test_csv_path.parent, max_points=args.plot_points)

    summary = {
        "data_path": str(data_path),
        "best_wind_speed_model_path": str(best_wind_speed_model_path),
        "best_air_density_model_path": str(best_air_density_model_path),
        "best_val_weighted_nmae": best_val_weighted_nmae,
        "best_val_wind_speed_nmae": best_val_wind_speed_nmae,
        "best_val_air_density_nmae": best_val_air_density_nmae,
        "best_wind_speed_model_weight": best_wind_speed_model_weight,
        "wind_speed_prediction_strategy": "风速最佳模型 + 持久性融合",
        "air_density_prediction_strategy": "空气密度最佳模型",
        "wind_speed_target_mode": meta["wind_speed_target_mode"],
        "selection_metric": "validation NMAE",
        "test_csv_path": str(test_csv_path),
        "test_metrics": test_metrics,
        "input_len": args.input_len,
        "data_fraction": meta["data_fraction"],
        "original_rows": meta["original_rows"],
        "used_rows": meta["used_rows"],
        "wind_speed_loss_weight": args.wind_speed_loss_weight,
        "air_density_loss_weight": args.air_density_loss_weight,
        "epochs_requested": args.epochs,
        "early_stopping_patience": args.patience,
        "history": history,
        "feature_cols": meta["feature_cols"],
        "target_cols": meta["target_cols"],
        "step_minutes": meta["step_minutes"],
        "train_ratio": meta["train_ratio"],
        "future_start_idx": meta["future_start_idx"],
        "n_future": meta["n_future"],
    }
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved metrics summary: {metrics_path}")
    return summary

#让训练文件支持命令行改参数
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练风速和空气密度预测模型。")
    parser.add_argument("--data", type=str, default=None, help="风电数据文件路径；不填时使用文件前面的 WIND_DATA_PATH。")
    parser.add_argument("--wind-speed-model-out", type=str, default=None, help="风速最佳模型输出路径。")
    parser.add_argument("--air-density-model-out", type=str, default=None, help="空气密度最佳模型输出路径。")
    parser.add_argument("--metrics-out", type=str, default=None, help="指标 JSON 输出路径。")
    parser.add_argument("--test-csv-out", type=str, default=None, help="测试集预测 CSV 输出路径。")
    parser.add_argument("--input-len", type=int, default=INPUT_LEN, help="LSTM 历史输入窗口长度。")
    parser.add_argument("--data-fraction", type=float, default=DATA_FRACTION, help="按时间顺序使用多少比例的数据。")
    parser.add_argument("--plot-points", type=int, default=2880, help="预测对比图最多显示多少个测试点；填 0 表示显示完整测试集。")
    parser.add_argument("--wind-speed-loss-weight", type=float, default=WIND_SPEED_LOSS_WEIGHT, help="风速损失权重。")
    parser.add_argument("--air-density-loss-weight", type=float, default=AIR_DENSITY_LOSS_WEIGHT, help="空气密度损失权重。")
    parser.add_argument("--epochs", type=int, default=NUM_EPOCHS, help="训练轮数。")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="每批训练样本数。")
    parser.add_argument("--patience", type=int, default=EARLY_STOPPING_PATIENCE, help="早停等待轮数。")
    parser.add_argument("--num-workers", type=int, default=0, help="DataLoader 工作进程数；Windows 下建议保持 0。")
    parser.add_argument("--amp", dest="amp", action="store_true", default=None, help="启用 CUDA 混合精度训练。")
    parser.add_argument("--no-amp", dest="amp", action="store_false", help="关闭 CUDA 混合精度训练。")
    return parser

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    train_model(args)


if __name__ == "__main__":
    main()
