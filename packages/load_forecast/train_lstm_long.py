# -*- coding: utf-8 -*-
# 用电负荷长期直接多输出 LSTM 训练文件
# 功能：读取并清洗负荷数据，训练“历史168h -> 未来24h”的长期负荷预测模型。
# 输出：一个 pth、一个测试集预测 CSV、一个指标 JSON、两张预测分析图。
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ===================== 数据文件配置（优先修改这里） =====================
# 默认使用 GEFCom2012 Zone 1 数据，字段为：Time、Actual Load (MW)、Temperature (Fahrenheit)。
# 需要更换数据时，把新数据放到 packages/load_forecast/data/，然后只改下面这一行即可。
LOAD_DATA_PATH = PROJECT_ROOT / "packages" / "load_forecast" / "data" / "gefcom2012_load_temp_zone1.xlsx"


import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    fm = None
    plt = None

from packages.load_forecast.gui_runner import (
    LONG_INPUT_LEN,
    DEFAULT_LONG_MODEL_PATH,
    DROPOUT,
    HIDDEN_SIZE,
    LONG_FORECAST_HOURS,
    NUM_LAYERS,
    RESULTS_DIR,
    LongHorizonLSTMModel,
    SafeMinMaxScaler,
    build_feature_frame,
    read_load_data,
)


DEFAULT_LONG_METRICS_PATH = DEFAULT_LONG_MODEL_PATH.with_suffix(".metrics.json")
DEFAULT_LONG_TEST_CSV_PATH = RESULTS_DIR / "load_lstm_long_168h_24h_test_predictions.csv"
DEFAULT_LONG_PLOT_PATH = RESULTS_DIR / "load_lstm_long_168h_24h_prediction_vs_true.png"
DEFAULT_LONG_HORIZON_PLOT_PATH = RESULTS_DIR / "load_lstm_long_168h_24h_horizon_mae.png"


LOCAL_LOAD_FEATURE_COL_ORDER = [
    "load",
    "temperature",
    "hour",
    "day_of_week",
    "is_weekend",
    "month",
    "season",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "month_sin",
    "month_cos",
    "load_lag_1",
    "load_lag_2",
    "load_lag_3",
    "load_lag_24",
    "load_lag_48",
    "load_lag_72",
    "load_lag_96",
    "load_lag_120",
    "load_lag_144",
    "load_lag_168",
    "load_same_hour_mean_7d",
    "load_ma_24h",
    "load_ma_168h",
    "load_diff_1h",
    "load_diff_24h",
]


def _load_feature_columns() -> list[str]:
    """返回当前负荷模型使用的特征列顺序。"""
    return list(LOCAL_LOAD_FEATURE_COL_ORDER)

# 数据预处理工具

def _safe_float(value, default: float = 0.0) -> float:
    """把 numpy/pandas 数值安全转成普通 float，便于写入 JSON。"""
    try:
        if pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _infer_median_step_minutes(timestamps: pd.Series) -> float:
    """根据时间戳推断原始采样间隔，主要用于检查是否为小时级数据。"""
    values = pd.to_datetime(timestamps, errors="coerce").dropna().sort_values().to_numpy(dtype="datetime64[ns]")
    if len(values) < 2:
        return 60.0
    diffs = np.diff(values).astype("timedelta64[s]").astype(np.int64) / 60.0
    diffs = diffs[diffs > 0]
    return float(np.median(diffs)) if len(diffs) else 60.0


def _extract_max_history_lag(feature_cols: list[str]) -> int:
    """从特征名中自动识别最大历史跨度，例如 load_lag_168、load_ma_168h。"""
    max_lag = 0
    for col in feature_cols:
        if col.startswith(("load_lag_", "load_ma_", "load_diff_", "load_same_hour_mean_")):
            nums = re.findall(r"\d+", col)
            if nums:
                if col == "load_same_hour_mean_7d":
                    max_lag = max(max_lag, 168)
                else:
                    max_lag = max(max_lag, int(nums[-1]))
    return int(max_lag)


def _preprocess_load_dataframe(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    对原始负荷数据做训练前清洗，处理思路和光伏训练文件类似：
    1）时间排序、去重、补齐小时级时间轴；
    2）识别负荷零值/负值/缺失值；
    3）用较宽的 IQR 边界识别明显极端异常，避免误删真实峰谷；
    4）对负荷、温度进行时间插值和前后补全；
    5）全流程仅使用负荷、温度和时间周期特征，不生成无实际来源的占位字段；
    6）保留清洗统计信息，写入 metrics.json，便于报告说明。
    """
    report: dict[str, int | float | str] = {"raw_rows": int(len(raw_df))}
    data = raw_df.copy()
    load_unit = data.attrs.get("load_unit")

    # 时间列必须存在，并统一转换为 datetime。
    if "datetime" not in data.columns:
        raise ValueError("负荷数据缺少 datetime 列，无法进行时间序列清洗。")
    data["datetime"] = pd.to_datetime(data["datetime"], errors="coerce")
    before_time_drop = len(data)
    data = data.dropna(subset=["datetime"]).sort_values("datetime")
    report["invalid_datetime_rows_removed"] = int(before_time_drop - len(data))

    # 同一小时如果重复，保留最后一条记录，避免窗口构造时一小时出现多行。
    before_dedup = len(data)
    data = data.drop_duplicates(subset=["datetime"], keep="last")
    report["duplicate_datetime_rows_removed"] = int(before_dedup - len(data))
    report["median_step_minutes_before_reindex"] = _safe_float(_infer_median_step_minutes(data["datetime"]), 60.0)

    if data.empty:
        raise ValueError("负荷数据清洗后为空，请检查时间列和负荷列。")

    # 补齐小时级时间轴。如果原始数据中缺少某些小时，这里会产生空行，后面再插值。
    data = data.set_index("datetime")
    full_index = pd.date_range(start=data.index.min(), end=data.index.max(), freq="h")
    report["missing_hour_slots_filled"] = int(max(len(full_index) - len(data), 0))
    data = data.reindex(full_index)
    data.index.name = "datetime"

    # 保证核心字段存在并转为数值。
    # 当前用户负荷数据只使用负荷、温度和时间周期特征。
    for col in ("load", "temperature"):
        if col not in data.columns:
            data[col] = 0.0
        data[col] = pd.to_numeric(data[col], errors="coerce")

    # 负荷不能为负，也不应为 0；这类点一般来自缺测、通信异常或数据占位。
    bad_load = data["load"].isna() | ~np.isfinite(data["load"]) | (data["load"] <= 0.0)
    report["invalid_zero_or_negative_load_points"] = int(bad_load.sum())
    data.loc[bad_load, "load"] = np.nan

    # 使用较宽的 6 倍 IQR 识别极端异常。边界设置宽一些，避免把真实高峰负荷误判为异常。
    valid_load = data["load"].dropna()
    if len(valid_load) >= 20:
        q1 = float(valid_load.quantile(0.25))
        q3 = float(valid_load.quantile(0.75))
        iqr = max(q3 - q1, 1e-8)
        lower = max(0.0, q1 - 6.0 * iqr)
        upper = q3 + 6.0 * iqr
        outlier_mask = data["load"].lt(lower) | data["load"].gt(upper)
        report["load_outlier_points_iqr6"] = int(outlier_mask.sum())
        report["load_outlier_lower_bound"] = float(lower)
        report["load_outlier_upper_bound"] = float(upper)
        data.loc[outlier_mask, "load"] = np.nan
    else:
        report["load_outlier_points_iqr6"] = 0
        report["load_outlier_lower_bound"] = 0.0
        report["load_outlier_upper_bound"] = 0.0

    # 温度做物理范围保护，明显不合理的温度点置为空值，后续按时间插值。
    bad_temperature = data["temperature"].isna() | ~np.isfinite(data["temperature"]) | data["temperature"].lt(-80.0) | data["temperature"].gt(120.0)
    report["invalid_temperature_points"] = int(bad_temperature.sum())
    data.loc[bad_temperature, "temperature"] = np.nan

    # 时间序列不能随意删除行，优先采用时间插值，再用前后补全处理首尾缺口。
    for col in ("load", "temperature"):
        data[col] = data[col].interpolate(method="time", limit_direction="both").ffill().bfill()
    data["load"] = data["load"].clip(lower=0.0)

    clean_df = data.reset_index()
    clean_df.attrs["load_unit"] = load_unit
    report["clean_rows_before_feature_warmup"] = int(len(clean_df))
    report["clean_start_time"] = str(clean_df["datetime"].iloc[0])
    report["clean_end_time"] = str(clean_df["datetime"].iloc[-1])
    report["load_min_after_clean"] = float(clean_df["load"].min())
    report["load_max_after_clean"] = float(clean_df["load"].max())
    return clean_df, report


def _prepare_training_data(
    data_path: str | Path,
    feature_cols: list[str],
    input_len: int,
    output_len: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """读取、清洗、构造特征，并删除最前面的历史滞后预热段。"""
    raw_df = read_load_data(data_path)
    clean_raw_df, preprocess_report = _preprocess_load_dataframe(raw_df)
    feature_df = build_feature_frame(clean_raw_df, feature_cols)

    # 特征里包含 load_lag_168、load_ma_168h 等历史统计量，最前面 168 行没有真实历史。
    # 删除这段预热行，可避免用 bfill 补出来的滞后特征参与训练。
    warmup_rows = _extract_max_history_lag(feature_cols)
    max_allowed_warmup = max(0, len(feature_df) - int(input_len) - int(output_len) - 10)
    warmup_rows = int(min(warmup_rows, max_allowed_warmup))
    if warmup_rows > 0:
        feature_df = feature_df.iloc[warmup_rows:].reset_index(drop=True)
        clean_raw_df = clean_raw_df.iloc[warmup_rows:].reset_index(drop=True)

    preprocess_report["feature_warmup_rows_removed"] = int(warmup_rows)
    preprocess_report["final_rows_for_training"] = int(len(feature_df))
    if len(feature_df) < input_len + output_len + 10:
        raise ValueError(
            f"清洗后的数据长度不足，当前 {len(feature_df)} 行，"
            f"至少需要 {input_len + output_len + 10} 行。"
        )
    return clean_raw_df, feature_df, preprocess_report


def _make_direct_windows(
    values: np.ndarray,
    load_col_idx: int,
    input_len: int,
    output_len: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """把连续时序样本切成直接多输出 LSTM 的历史窗口和未来24h标签。"""
    enc_x, y, starts = [], [], []
    max_start = len(values) - input_len - output_len + 1
    for start in range(max_start):
        enc_x.append(values[start:start + input_len])
        y.append(values[start + input_len:start + input_len + output_len, load_col_idx])
        starts.append(start)
    return (
        np.asarray(enc_x, dtype=np.float32),
        np.asarray(y, dtype=np.float32),
        np.asarray(starts, dtype=np.int64),
    )


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """计算负荷预测常用评价指标。"""
    error = y_pred - y_true
    abs_error = np.abs(error)
    rmse = float(np.sqrt(np.mean(error ** 2)))
    mae = float(np.mean(abs_error))
    mape = float(np.mean(abs_error / (np.abs(y_true) + 1e-8)) * 100.0)
    denom = max(float(np.mean(np.abs(y_true))), 1e-8)
    nmae = float(mae / denom * 100.0)
    r2 = float(1.0 - np.sum(error ** 2) / (np.sum((y_true - np.mean(y_true)) ** 2) + 1e-8))
    return {"mae": mae, "rmse": rmse, "mape": mape, "nmae": nmae, "r2": r2}


def _horizon_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> list[dict]:
    """分别统计未来 1h~24h 每个预测步长上的误差。"""
    return [
        {"horizon": idx + 1, **_metrics(y_true[:, idx], y_pred[:, idx])}
        for idx in range(y_true.shape[1])
    ]


def _configure_chinese_font():
    """设置 matplotlib 中文字体，避免保存图片时中文乱码。"""
    if not HAS_MATPLOTLIB:
        return None, None
    preferred_fonts = [
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "Droid Sans Fallback",
    ]
    try:
        installed_fonts = {f.name for f in fm.fontManager.ttflist}
        chosen_font = next((font for font in preferred_fonts if font in installed_fonts), None)
    except Exception:
        chosen_font = None
    plt.rcParams["font.sans-serif"] = preferred_fonts + ["Times New Roman", "sans-serif"]
    plt.rcParams["font.family"] = ["Times New Roman"]
    plt.rcParams["axes.unicode_minus"] = False
    zh_font = fm.FontProperties(family=chosen_font) if chosen_font else fm.FontProperties()
    return plt, zh_font


def _build_prediction_frame(
    raw_df: pd.DataFrame,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    sample_starts: np.ndarray,
    input_len: int,
) -> pd.DataFrame:
    """整理测试集每个窗口、每个预测步长的真实值和预测值。"""
    rows = []
    for sample_no, start in enumerate(sample_starts):
        for step_idx in range(y_true.shape[1]):
            target_idx = int(start) + int(input_len) + step_idx
            timestamp = raw_df["datetime"].iloc[target_idx] if "datetime" in raw_df.columns and target_idx < len(raw_df) else target_idx
            true_load = float(y_true[sample_no, step_idx])
            pred_load = float(y_pred[sample_no, step_idx])
            error = pred_load - true_load
            rows.append(
                {
                    "sample_no": int(sample_no),
                    "window_start_index": int(start),
                    "forecast_step": int(step_idx + 1),
                    "target_index": int(target_idx),
                    "datetime": timestamp,
                    "true_load": true_load,
                    "pred_load": pred_load,
                    "error": float(error),
                    "abs_error": float(abs(error)),
                    "error_pct": float(abs(error) / (abs(true_load) + 1e-8) * 100.0),
                }
            )
    return pd.DataFrame(rows)


def _plot_sample_prediction(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    output_path: str | Path,
    title: str,
    sample_index: int | None = None,
) -> str | None:
    """保存一个典型测试窗口的未来 24h 曲线预测图。"""
    if not HAS_MATPLOTLIB or len(y_true) == 0:
        return None
    plt_obj, zh_font = _configure_chinese_font()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    idx = int(sample_index if sample_index is not None else len(y_true) // 2)
    idx = max(0, min(idx, len(y_true) - 1))
    x = np.arange(1, y_true.shape[1] + 1)

    fig, ax = plt_obj.subplots(figsize=(10.5, 5.0))
    ax.plot(x, y_true[idx], marker="o", linewidth=1.8, label="真实负荷")
    ax.plot(x, y_pred[idx], marker="o", linestyle="--", linewidth=1.8, label="预测负荷")
    ax.set_title(title, fontproperties=zh_font)
    ax.set_xlabel("预测步长 / h", fontproperties=zh_font)
    ax.set_ylabel("负荷 / MW", fontproperties=zh_font)
    ax.grid(alpha=0.25)
    ax.legend(prop=zh_font)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt_obj.close(fig)
    print(f"saved plot: {output_path}")
    return str(output_path)


def _plot_horizon_mae(horizon_metrics: list[dict], output_path: str | Path, title: str) -> str | None:
    """保存未来 1h~24h 各预测步长上的 MAE 曲线。"""
    if not HAS_MATPLOTLIB or not horizon_metrics:
        return None
    plt_obj, zh_font = _configure_chinese_font()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    horizons = [item["horizon"] for item in horizon_metrics]
    mae_values = [item["mae"] for item in horizon_metrics]

    fig, ax = plt_obj.subplots(figsize=(10.5, 5.0))
    ax.plot(horizons, mae_values, marker="o", linewidth=1.8)
    ax.set_title(title, fontproperties=zh_font)
    ax.set_xlabel("预测步长 / h", fontproperties=zh_font)
    ax.set_ylabel("MAE / MW", fontproperties=zh_font)
    ax.set_xticks(horizons)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt_obj.close(fig)
    print(f"saved plot: {output_path}")
    return str(output_path)


def train_lstm_long(
    data_path: str | Path = LOAD_DATA_PATH,
    output_path: str | Path = DEFAULT_LONG_MODEL_PATH,
    input_len: int = LONG_INPUT_LEN,
    output_len: int = LONG_FORECAST_HOURS,
    epochs: int = 70,
    batch_size: int = 128,
    lr: float = 8e-4,
    patience: int = 10,
    teacher_forcing: float = 0.45,
    metrics_out: str | Path = DEFAULT_LONG_METRICS_PATH,
    test_csv_out: str | Path = DEFAULT_LONG_TEST_CSV_PATH,
    plot_out: str | Path = DEFAULT_LONG_PLOT_PATH,
    horizon_plot_out: str | Path = DEFAULT_LONG_HORIZON_PLOT_PATH,
) -> dict:
    """训练长期 168h -> 24h 直接多输出 LSTM，并同步输出 pth、CSV、JSON 和图片。"""
    torch.manual_seed(2026)
    np.random.seed(2026)

    data_path = Path(data_path)
    output_path = Path(output_path)
    metrics_out = Path(metrics_out)
    test_csv_out = Path(test_csv_out)
    plot_out = Path(plot_out)
    horizon_plot_out = Path(horizon_plot_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_out.parent.mkdir(parents=True, exist_ok=True)
    test_csv_out.parent.mkdir(parents=True, exist_ok=True)

    feature_cols = _load_feature_columns()

    # 读取并清洗负荷数据：补齐小时级时间轴、处理负荷异常值、处理温度异常值，
    # 同时删除 load_lag_168 等特征对应的历史预热段。
    raw_df, feature_df, preprocess_report = _prepare_training_data(
        data_path,
        feature_cols,
        input_len,
        output_len,
    )
    load_col_idx = feature_df.columns.get_loc("load")
    split_train_end = int(len(feature_df) * 0.70)
    split_val_end = int(len(feature_df) * 0.85)

    scaler = SafeMinMaxScaler()
    scaler.fit(feature_df.iloc[:split_train_end].values)
    scaled_values = scaler.transform(feature_df.values)
    enc_all, y_all, sample_starts = _make_direct_windows(
        scaled_values,
        load_col_idx,
        input_len,
        output_len,
    )

    train_mask = sample_starts + input_len + output_len <= split_train_end
    val_mask = (sample_starts >= split_train_end - input_len) & (sample_starts + input_len + output_len <= split_val_end)
    test_mask = sample_starts >= split_val_end - input_len

    enc_train, y_train = enc_all[train_mask], y_all[train_mask]
    enc_val, y_val = enc_all[val_mask], y_all[val_mask]
    enc_test, y_test = enc_all[test_mask], y_all[test_mask]
    test_starts = sample_starts[test_mask]

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Load file: {data_path}")
    print(f"Rows train/val/test split end: {split_train_end} / {split_val_end} / {len(feature_df)}")
    print(f"Windows train/val/test: {len(enc_train)} / {len(enc_val)} / {len(enc_test)}")
    print(f"Encoder input length: {input_len}h, forecast horizon: {output_len}h")
    print("Model: direct multi-output LSTM")
    print(f"Data preprocessing: {preprocess_report}")
    print(f"Device: {device}")

    model = LongHorizonLSTMModel(
        input_size=len(feature_cols),
        hidden_size=HIDDEN_SIZE,
        num_layers=NUM_LAYERS,
        output_size=output_len,
        dropout=DROPOUT,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    criterion = nn.SmoothL1Loss()
    train_loader = DataLoader(
        TensorDataset(torch.tensor(enc_train), torch.tensor(y_train)),
        batch_size=batch_size,
        shuffle=True,
    )

    best_val = float("inf")
    best_state = None
    stale_epochs = 0
    history = []

    for epoch in range(1, epochs + 1):
        model.train()
        train_losses = []
        for enc_b, y_b in train_loader:
            enc_b = enc_b.to(device)
            y_b = y_b.to(device)
            optimizer.zero_grad(set_to_none=True)
            pred = model(enc_b)
            loss = criterion(pred, y_b)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(float(loss.item()))

        model.eval()
        with torch.no_grad():
            val_pred = model(
                torch.tensor(enc_val, dtype=torch.float32).to(device),
            ).cpu()
            val_loss = float(criterion(val_pred, torch.tensor(y_val, dtype=torch.float32)).item())

        train_loss = float(np.mean(train_losses)) if train_losses else float("nan")
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        print(f"epoch {epoch:03d} train_loss={train_loss:.6f} val_loss={val_loss:.6f}")

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        test_pred_scaled = model(
            torch.tensor(enc_test, dtype=torch.float32).to(device),
        ).cpu().numpy()

    true_load = scaler.inverse_col(y_test.reshape(-1, 1), load_col_idx).reshape(y_test.shape)
    pred_load = scaler.inverse_col(test_pred_scaled.reshape(-1, 1), load_col_idx).reshape(test_pred_scaled.shape)
    pred_load = np.clip(pred_load, 0.0, None)

    test_metrics = _metrics(true_load.reshape(-1), pred_load.reshape(-1))
    horizon_metrics = _horizon_metrics(true_load, pred_load)
    prediction_frame = _build_prediction_frame(raw_df, true_load, pred_load, test_starts, input_len)
    prediction_frame.to_csv(test_csv_out, index=False, encoding="utf-8-sig")

    sample_plot_path = _plot_sample_prediction(
        true_load,
        pred_load,
        plot_out,
        "长期168h→24h直接多输出LSTM测试集窗口预测对比",
    )
    horizon_plot_path = _plot_horizon_mae(
        horizon_metrics,
        horizon_plot_out,
        "长期168h→24h测试集各预测步长 MAE",
    )

    checkpoint = {
        "model_type": "lstm_direct_168h_to_24h",
        "model_state_dict": model.state_dict(),
        "feature_col_order": feature_cols,
        "target_col": "load",
        "input_len": int(input_len),
        "output_len": int(output_len),
        "n_features": int(len(feature_cols)),
        "hidden_size": HIDDEN_SIZE,
        "num_layers": NUM_LAYERS,
        "dropout": DROPOUT,
        "scaler_min": scaler.min_,
        "scaler_max": scaler.max_,
        "scaler_scale": scaler.scale_,
        "n_train": int(len(enc_train)),
        "n_val": int(len(enc_val)),
        "n_test": int(len(enc_test)),
        "split_train_end": int(split_train_end),
        "split_val_end": int(split_val_end),
        "history": history,
        "test_metrics": test_metrics,
        "horizon_metrics": horizon_metrics,
        "data_path": str(data_path),
        "preprocess_report": preprocess_report,
    }
    torch.save(checkpoint, output_path)

    summary = {
        "model_type": checkpoint["model_type"],
        "checkpoint": str(output_path),
        "data_path": str(data_path),
        "preprocess_report": preprocess_report,
        "input_len": int(input_len),
        "output_len": int(output_len),
        "n_train": int(len(enc_train)),
        "n_val": int(len(enc_val)),
        "n_test": int(len(enc_test)),
        "test_csv_path": str(test_csv_out),
        "sample_plot_path": sample_plot_path,
        "horizon_plot_path": horizon_plot_path,
        "test_metrics": test_metrics,
        "horizon_metrics": horizon_metrics,
        "history": history,
    }
    metrics_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"saved checkpoint: {output_path}")
    print(f"history input length: {input_len}h, forecast horizon: {output_len}h")
    print(f"saved test csv: {test_csv_out}")
    print(f"saved metrics summary: {metrics_out}")
    print(f"test metrics: {test_metrics}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="训练长期 168h -> 24h 直接多输出 LSTM 负荷预测模型，并同步输出 pth、CSV、JSON 和图片。")
    parser.add_argument("--data-path", default=str(LOAD_DATA_PATH))
    parser.add_argument("--output-path", default=str(DEFAULT_LONG_MODEL_PATH))
    parser.add_argument("--input-len", type=int, default=LONG_INPUT_LEN)
    parser.add_argument("--output-len", type=int, default=LONG_FORECAST_HOURS)
    parser.add_argument("--epochs", type=int, default=70)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=8e-4)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--teacher-forcing", type=float, default=0.45)
    parser.add_argument("--metrics-out", default=str(DEFAULT_LONG_METRICS_PATH))
    parser.add_argument("--test-csv-out", default=str(DEFAULT_LONG_TEST_CSV_PATH))
    parser.add_argument("--plot-out", default=str(DEFAULT_LONG_PLOT_PATH))
    parser.add_argument("--horizon-plot-out", default=str(DEFAULT_LONG_HORIZON_PLOT_PATH))
    args = parser.parse_args()

    train_lstm_long(
        data_path=args.data_path,
        output_path=args.output_path,
        input_len=args.input_len,
        output_len=args.output_len,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        patience=args.patience,
        teacher_forcing=args.teacher_forcing,
        metrics_out=args.metrics_out,
        test_csv_out=args.test_csv_out,
        plot_out=args.plot_out,
        horizon_plot_out=args.horizon_plot_out,
    )


if __name__ == "__main__":
    main()
