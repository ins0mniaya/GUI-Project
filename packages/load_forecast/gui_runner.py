# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn


PACKAGE_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = PACKAGE_ROOT / "results"

# ===================== 数据文件配置 =====================
# 默认使用 GEFCom2012 Zone 1 负荷-温度数据。
# 如果以后更换数据，只需要把新的 Excel/CSV 放到 data 目录，并修改这里的文件名。
DEFAULT_DATA_PATH = PACKAGE_ROOT / "data" / "gefcom2012_load_temp_zone1.xlsx"
DEFAULT_MODEL_PATH = PACKAGE_ROOT / "model" / "load_gru_model_load_weather_hourly.pth"
DEFAULT_SHORT_MODEL_PATH = PACKAGE_ROOT / "model" / "load_gru_short_4h.pth"
DEFAULT_LONG_MODEL_PATH = PACKAGE_ROOT / "model" / "load_lstm_long_24h.pth"
DEFAULT_OUTPUT_PATH = RESULTS_DIR / "load_prediction_vs_true.csv"
DEFAULT_PLOT_PATH = RESULTS_DIR / "load_prediction_vs_true.png"
DEFAULT_SHORT_OUTPUT_PATH = RESULTS_DIR / "load_prediction_vs_true_short.csv"
DEFAULT_SHORT_PLOT_PATH = RESULTS_DIR / "load_prediction_vs_true_short.png"
DEFAULT_LONG_OUTPUT_PATH = RESULTS_DIR / "load_prediction_vs_true_long.csv"
DEFAULT_LONG_PLOT_PATH = RESULTS_DIR / "load_prediction_vs_true_long.png"
DEFAULT_COMBINED_PLOT_PATH = RESULTS_DIR / "load_prediction_vs_true_combined.png"

DEFAULT_CODE = 0
DEFAULT_INPUT_LEN = 24
LONG_INPUT_LEN = 168
DEFAULT_OUTPUT_LEN = 1
SHORT_FORECAST_MODE = "short"
LONG_FORECAST_MODE = "long"
SHORT_FORECAST_HOURS = 4
LONG_FORECAST_HOURS = 24
DEFAULT_PREDICT_EVERY_HOURS = LONG_FORECAST_HOURS
DEFAULT_FORECAST_MODE = LONG_FORECAST_MODE
DEFAULT_LOAD_PLOT_UPDATE_INTERVAL_SECONDS = 2.0
FORECAST_MODE_CONFIG = {
    SHORT_FORECAST_MODE: {
        "horizon": SHORT_FORECAST_HOURS,
        "label": "短期预测",
        "algorithm": "4h多输出GRU",
    },
    LONG_FORECAST_MODE: {
        "horizon": LONG_FORECAST_HOURS,
        "label": "长期预测",
        "algorithm": "历史168h直接多输出LSTM",
    },
}

HIDDEN_SIZE = 128
NUM_LAYERS = 2
DROPOUT = 0.2
LOAD_LAGS = [1, 2, 3, 24, 48, 72, 96, 120, 144, 168]
MAX_CONTEXT_LAG = max(LOAD_LAGS)
DEFAULT_FEATURE_COL_ORDER = [
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


class SafeMinMaxScaler:
    def __init__(self):
        """保存与训练阶段一致的最小值归一化参数。"""
        self.min_ = None
        self.max_ = None
        self.scale_ = None

    def fit(self, data: np.ndarray):
        """根据输入数据拟合逐列最小值和缩放系数。"""
        self.min_ = np.min(data, axis=0)
        self.max_ = np.max(data, axis=0)
        self.scale_ = self.max_ - self.min_
        self.scale_[self.scale_ == 0] = 1.0

    def transform(self, data: np.ndarray) -> np.ndarray:
        """把原始特征缩放到训练时的归一化区间。"""
        return (data - self.min_) / self.scale_

    def inverse_col(self, values: np.ndarray, col_idx: int) -> np.ndarray:
        """仅反归一化指定列，便于恢复负荷预测值。"""
        return values * self.scale_[col_idx] + self.min_[col_idx]


class GRUModel(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int = HIDDEN_SIZE,
        num_layers: int = NUM_LAYERS,
        output_size: int = DEFAULT_OUTPUT_LEN,
        dropout: float = DROPOUT,
    ):
        """构建与训练阶段一致的双向 GRU 负荷预测网络。"""
        super().__init__()
        self.gru = nn.GRU(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
            bidirectional=True,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Sequential(
            nn.Linear(hidden_size * 2, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, output_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """执行一次前向传播并输出最后时刻的预测结果。"""
        out, _ = self.gru(x)
        out = self.dropout(out)
        out = out[:, -1, :]
        return self.fc(out)


class LongHorizonLSTMModel(nn.Module):
    def __init__(
        self,
        input_size: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        output_size: int = LONG_FORECAST_HOURS,
        dropout: float = 0.2,
    ):
        """长期24小时直接多输出 LSTM 负荷预测网络，默认使用历史168h窗口。"""
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, output_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        out = self.dropout(out[:, -1, :])
        return self.fc(out)


class Seq2SeqAttentionLSTMModel(nn.Module):
    def __init__(
        self,
        encoder_input_size: int,
        decoder_input_size: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        output_size: int = LONG_FORECAST_HOURS,
        dropout: float = 0.2,
    ):
        """带注意力机制的 Encoder-Decoder LSTM，用历史168h特征生成未来24h负荷曲线。"""
        super().__init__()
        self.output_size = int(output_size)
        self.encoder = nn.LSTM(
            input_size=encoder_input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.decoder_cell = nn.LSTMCell(decoder_input_size + 1 + hidden_size, hidden_size)
        self.attn_query = nn.Linear(hidden_size, hidden_size, bias=False)
        self.attn_key = nn.Linear(hidden_size, hidden_size, bias=False)
        self.out = nn.Sequential(
            nn.Linear(hidden_size * 2 + decoder_input_size, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, 1),
        )

    def forward(
        self,
        encoder_x: torch.Tensor,
        decoder_x: torch.Tensor,
        target_y: torch.Tensor | None = None,
        teacher_forcing_ratio: float = 0.0,
    ) -> torch.Tensor:
        encoder_out, (hidden, cell) = self.encoder(encoder_x)
        h = hidden[-1]
        c = cell[-1]
        prev_y = encoder_x[:, -1, 0:1]
        keys = self.attn_key(encoder_out)
        preds = []

        for step in range(decoder_x.shape[1]):
            query = self.attn_query(h).unsqueeze(1)
            attn_scores = torch.sum(torch.tanh(query + keys), dim=-1)
            attn_weights = torch.softmax(attn_scores, dim=1)
            context = torch.bmm(attn_weights.unsqueeze(1), encoder_out).squeeze(1)
            dec_step = decoder_x[:, step, :]
            h, c = self.decoder_cell(torch.cat([prev_y, dec_step, context], dim=1), (h, c))
            pred = self.out(torch.cat([h, context, dec_step], dim=1))
            preds.append(pred)
            if target_y is not None and teacher_forcing_ratio > 0.0 and torch.rand((), device=encoder_x.device) < teacher_forcing_ratio:
                prev_y = target_y[:, step:step + 1]
            else:
                prev_y = pred

        return torch.cat(preds, dim=1)


def clear_plot_outputs() -> None:
    """清理负荷预测生成的 CSV 与图片结果。"""
    for path in (
        DEFAULT_OUTPUT_PATH,
        DEFAULT_PLOT_PATH,
        DEFAULT_SHORT_OUTPUT_PATH,
        DEFAULT_SHORT_PLOT_PATH,
        DEFAULT_LONG_OUTPUT_PATH,
        DEFAULT_LONG_PLOT_PATH,
        DEFAULT_COMBINED_PLOT_PATH,
    ):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def get_plot_paths() -> list[str]:
    """返回负荷预测主结果图路径。"""
    return [str(DEFAULT_COMBINED_PLOT_PATH)]


def read_load_data(data_path: str | Path) -> pd.DataFrame:
    """读取 CSV/Excel 负荷数据，并规整成 datetime/load/temperature。"""
    data_path = Path(data_path)
    if data_path.suffix.lower() in {".xlsx", ".xls"}:
        raw_df = pd.read_excel(data_path)
    else:
        raw_df = pd.read_csv(data_path)

    col_lookup = {str(col).strip().lower(): col for col in raw_df.columns}

    def pick(candidates: list[str]) -> str | None:
        for candidate in candidates:
            key = candidate.strip().lower()
            if key in col_lookup:
                return col_lookup[key]
        for key, original in col_lookup.items():
            if any(candidate.strip().lower() in key for candidate in candidates):
                return original
        return None

    datetime_col = pick(["datetime", "time", "timestamp", "date"])
    load_col = pick(["load", "actual load", "actual load (mw)", "负荷"])
    temp_col = pick(["temperature", "temperature (fahrenheit)", "temp", "气温"])

    if datetime_col is None or load_col is None:
        raise ValueError("负荷数据至少需要时间列和负荷列，可识别列名包括 datetime/time 与 load/actual load。")

    df = pd.DataFrame()
    df["datetime"] = pd.to_datetime(raw_df[datetime_col], errors="coerce")
    df["load"] = pd.to_numeric(raw_df[load_col], errors="coerce")
    df["temperature"] = pd.to_numeric(raw_df[temp_col], errors="coerce") if temp_col is not None else 0.0
    df = df.dropna(subset=["datetime", "load"]).sort_values("datetime").reset_index(drop=True)
    df["temperature"] = df["temperature"].ffill().bfill().fillna(0.0)

    load_col_name = str(load_col).strip().lower()
    if "mw" in load_col_name:
        df.attrs["load_unit"] = "MW"
    elif "kw" in load_col_name:
        df.attrs["load_unit"] = "kW"
    else:
        df.attrs["load_unit"] = None
    return df


def default_rolling_start_index(data_path: str | Path = DEFAULT_DATA_PATH, input_len: int = DEFAULT_INPUT_LEN) -> int:
    """默认从留出的测试段附近开始展示，避免界面一打开就在训练段评估。"""
    try:
        row_count = len(read_load_data(data_path))
    except Exception:
        return 0
    return max(0, int(row_count * 0.85) - int(input_len))


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """从时间戳与负荷序列构造时序预测所需的统计特征。"""
    df = df.copy()
    ts = pd.to_datetime(df["datetime"], errors="coerce")

    df["hour"] = ts.dt.hour.fillna(0).astype(int)
    df["day_of_week"] = ts.dt.dayofweek.fillna(0).astype(int)
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month"] = ts.dt.month.fillna(1).astype(int)
    df["season"] = df["month"].apply(
        lambda x: 1 if x in [12, 1, 2] else 2 if x in [3, 4, 5] else 3 if x in [6, 7, 8] else 4
    )

    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    for lag in LOAD_LAGS:
        df[f"load_lag_{lag}"] = df["load"].shift(lag)

    same_hour_lags = [24, 48, 72, 96, 120, 144, 168]
    df["load_same_hour_mean_7d"] = pd.concat(
        [df["load"].shift(lag) for lag in same_hour_lags],
        axis=1,
    ).mean(axis=1)
    df["load_ma_24h"] = df["load"].shift(1).rolling(window=24, min_periods=1).mean()
    df["load_ma_168h"] = df["load"].shift(1).rolling(window=168, min_periods=1).mean()
    df["load_diff_1h"] = df["load"].diff(1)
    df["load_diff_24h"] = df["load"].diff(24)

    return df.drop(columns=["datetime"])


def build_feature_frame(df: pd.DataFrame, feature_col_order: list[str]) -> pd.DataFrame:
    """按模型训练时的列顺序生成完整特征表。"""
    required_raw = ["datetime", "load"]
    missing_raw = [col for col in required_raw if col not in df.columns]
    if missing_raw:
        raise ValueError(f"负荷数据缺少必要字段: {', '.join(missing_raw)}")

    feature_df = build_features(df).ffill().bfill()
    missing_cols = [col for col in feature_col_order if col not in feature_df.columns]
    if missing_cols:
        raise ValueError(f"负荷数据缺少模型所需特征: {missing_cols}")
    return feature_df[feature_col_order].copy()


def scaler_from_checkpoint(checkpoint: dict) -> SafeMinMaxScaler | None:
    """优先复用 checkpoint 内保存的归一化参数。"""
    required = ("scaler_min", "scaler_max", "scaler_scale")
    if not all(key in checkpoint for key in required):
        return None

    scaler = SafeMinMaxScaler()
    scaler.min_ = np.asarray(checkpoint["scaler_min"], dtype=float)
    scaler.max_ = np.asarray(checkpoint["scaler_max"], dtype=float)
    scaler.scale_ = np.asarray(checkpoint["scaler_scale"], dtype=float)
    return scaler


def update_load_dependent_features(feature_df: pd.DataFrame, row_idx: int):
    """在回填预测值后，增量更新依赖负荷历史的派生特征。"""
    if row_idx < 0 or row_idx >= len(feature_df):
        return

    row_label = feature_df.index[row_idx]
    load_series = feature_df["load"]

    for lag in LOAD_LAGS:
        col = f"load_lag_{lag}"
        if col in feature_df.columns and row_idx - lag >= 0:
            feature_df.at[row_label, col] = float(load_series.iat[row_idx - lag])

    if "load_ma_24h" in feature_df.columns and row_idx > 0:
        feature_df.at[row_label, "load_ma_24h"] = float(load_series.iloc[max(0, row_idx - 24):row_idx].mean())
    if "load_ma_168h" in feature_df.columns and row_idx > 0:
        feature_df.at[row_label, "load_ma_168h"] = float(load_series.iloc[max(0, row_idx - 168):row_idx].mean())
    if "load_diff_1h" in feature_df.columns and row_idx - 1 >= 0:
        feature_df.at[row_label, "load_diff_1h"] = float(load_series.iat[row_idx] - load_series.iat[row_idx - 1])
    if "load_diff_24h" in feature_df.columns and row_idx - 24 >= 0:
        feature_df.at[row_label, "load_diff_24h"] = float(load_series.iat[row_idx] - load_series.iat[row_idx - 24])


def rolling_predict_with_feedback(
    model: GRUModel,
    feature_df: pd.DataFrame,
    scaler: SafeMinMaxScaler,
    start_idx: int,
    horizon: int,
    input_len: int,
    device: str,
) -> list[float]:
    """逐步把预测值写回特征表，适合多步滚动预测。"""
    model.eval()
    predictions: list[float] = []
    load_col_idx = feature_df.columns.get_loc("load")

    context_start = max(0, start_idx - MAX_CONTEXT_LAG)
    end_idx = min(start_idx + input_len + horizon, len(feature_df))
    working_df = feature_df.iloc[context_start:end_idx].copy()
    relative_start_idx = start_idx - context_start

    for step in range(horizon):
        current_rel_idx = relative_start_idx + input_len + step
        if current_rel_idx >= len(working_df):
            break

        window = working_df.iloc[current_rel_idx - input_len:current_rel_idx].values
        window_scaled = scaler.transform(window)
        x = torch.tensor(window_scaled, dtype=torch.float32).unsqueeze(0).to(device)

        with torch.no_grad():
            pred_scaled = model(x).cpu().numpy()[0, 0]

        pred = scaler.inverse_col(np.array([[pred_scaled]]), load_col_idx)[0, 0]
        predictions.append(float(pred))
        working_df.iat[current_rel_idx, load_col_idx] = float(pred)

        for update_idx in range(current_rel_idx, len(working_df)):
            update_load_dependent_features(working_df, update_idx)

    return predictions


def predict_lstm_direct_24h(
    model: LongHorizonLSTMModel,
    feature_df: pd.DataFrame,
    scaler: SafeMinMaxScaler,
    start_idx: int,
    input_len: int,
    device: str,
) -> list[float]:
    """使用长期 LSTM 一次直接输出未来24小时负荷。"""
    model.eval()
    load_col_idx = feature_df.columns.get_loc("load")
    window = feature_df.iloc[start_idx:start_idx + input_len].values
    window_scaled = scaler.transform(window)
    x = torch.tensor(window_scaled, dtype=torch.float32).unsqueeze(0).to(device)
    with torch.no_grad():
        pred_scaled = model(x).cpu().numpy().reshape(-1, 1)
    pred = scaler.inverse_col(pred_scaled, load_col_idx).reshape(-1)
    return [float(value) for value in pred]


def predict_gru_direct_multi(
    model: GRUModel,
    feature_df: pd.DataFrame,
    scaler: SafeMinMaxScaler,
    start_idx: int,
    input_len: int,
    output_len: int,
    device: str,
) -> list[float]:
    """使用多输出 GRU 一次直接输出未来多个小时负荷。"""
    model.eval()
    load_col_idx = feature_df.columns.get_loc("load")
    window = feature_df.iloc[start_idx:start_idx + input_len].values
    window_scaled = scaler.transform(window)
    x = torch.tensor(window_scaled, dtype=torch.float32).unsqueeze(0).to(device)
    with torch.no_grad():
        pred_scaled = model(x).cpu().numpy().reshape(-1, 1)
    pred = scaler.inverse_col(pred_scaled, load_col_idx).reshape(-1)
    return [float(value) for value in pred[:output_len]]


def predict_seq2seq_attention(
    model: Seq2SeqAttentionLSTMModel,
    feature_df: pd.DataFrame,
    scaler: SafeMinMaxScaler,
    start_idx: int,
    input_len: int,
    output_len: int,
    decoder_feature_cols: list[str],
    device: str,
) -> list[float]:
    """使用 Seq2Seq Attention LSTM 一次输出未来多小时负荷。"""
    model.eval()
    load_col_idx = feature_df.columns.get_loc("load")
    encoder_window = feature_df.iloc[start_idx:start_idx + input_len].values
    future_features = feature_df.iloc[start_idx + input_len:start_idx + input_len + output_len][decoder_feature_cols].values
    encoder_scaled = scaler.transform(encoder_window)
    decoder_indices = [feature_df.columns.get_loc(col) for col in decoder_feature_cols]
    decoder_scaled = (future_features - scaler.min_[decoder_indices]) / scaler.scale_[decoder_indices]

    encoder_x = torch.tensor(encoder_scaled, dtype=torch.float32).unsqueeze(0).to(device)
    decoder_x = torch.tensor(decoder_scaled, dtype=torch.float32).unsqueeze(0).to(device)
    with torch.no_grad():
        pred_scaled = model(encoder_x, decoder_x).cpu().numpy().reshape(-1, 1)
    pred = scaler.inverse_col(pred_scaled, load_col_idx).reshape(-1)
    return [float(value) for value in pred]


def predict_one_step_batch(
    model: GRUModel,
    feature_df: pd.DataFrame,
    scaler: SafeMinMaxScaler,
    start_indices: list[int],
    input_len: int,
    device: str,
    batch_size: int = 512,
) -> list[float]:
    """批量执行单步预测，适合连续多个起点的一小时预测。"""
    model.eval()
    scaled_values = scaler.transform(feature_df.values)
    predictions: list[float] = []
    load_col_idx = feature_df.columns.get_loc("load")

    for batch_start in range(0, len(start_indices), batch_size):
        batch_indices = start_indices[batch_start:batch_start + batch_size]
        windows = np.stack([scaled_values[start:start + input_len] for start in batch_indices], axis=0)
        x = torch.tensor(windows, dtype=torch.float32).to(device)
        with torch.no_grad():
            pred_scaled = model(x).cpu().numpy()
        pred = scaler.inverse_col(pred_scaled, load_col_idx).reshape(-1)
        predictions.extend(float(value) for value in pred)

    return predictions


def _load_checkpoint(model_path: str | Path, device: str) -> dict:
    """兼容不同 PyTorch 版本的 checkpoint 读取方式。"""
    try:
        return torch.load(Path(model_path), map_location=device, weights_only=False)
    except TypeError:
        return torch.load(Path(model_path), map_location=device)


def _load_gru_model(checkpoint: dict, device: str) -> tuple[GRUModel, int, int]:
    """从 checkpoint 中恢复 GRU 结构和关键维度信息。"""
    n_features = int(checkpoint.get("n_features", 0) or 0)
    if n_features <= 0:
        state = checkpoint.get("model_state_dict", {})
        weight = state.get("gru.weight_ih_l0")
        if weight is None:
            raise ValueError("模型 checkpoint 缺少 n_features 和 gru.weight_ih_l0，无法识别输入维度。")
        n_features = int(weight.shape[1])

    input_len = int(checkpoint.get("input_len", DEFAULT_INPUT_LEN) or DEFAULT_INPUT_LEN)
    output_len = int(checkpoint.get("output_len", DEFAULT_OUTPUT_LEN) or DEFAULT_OUTPUT_LEN)
    model = GRUModel(input_size=n_features, output_size=output_len)
    model.load_state_dict(checkpoint["model_state_dict"])
    model = model.to(device)
    model.eval()
    return model, input_len, output_len


def _load_lstm_long_model(checkpoint: dict, device: str) -> tuple[LongHorizonLSTMModel, int, int]:
    """从 checkpoint 中恢复长期24小时 LSTM 多输出模型。"""
    n_features = int(checkpoint.get("n_features", 0) or 0)
    if n_features <= 0:
        state = checkpoint.get("model_state_dict", {})
        weight = state.get("lstm.weight_ih_l0")
        if weight is None:
            raise ValueError("长期 LSTM checkpoint 缺少 n_features 和 lstm.weight_ih_l0，无法识别输入维度。")
        n_features = int(weight.shape[1])

    input_len = int(checkpoint.get("input_len", LONG_INPUT_LEN) or LONG_INPUT_LEN)
    output_len = int(checkpoint.get("output_len", LONG_FORECAST_HOURS) or LONG_FORECAST_HOURS)
    model = LongHorizonLSTMModel(
        input_size=n_features,
        hidden_size=int(checkpoint.get("hidden_size", HIDDEN_SIZE) or HIDDEN_SIZE),
        num_layers=int(checkpoint.get("num_layers", NUM_LAYERS) or NUM_LAYERS),
        output_size=output_len,
        dropout=float(checkpoint.get("dropout", DROPOUT) or DROPOUT),
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model = model.to(device)
    model.eval()
    return model, input_len, output_len


def _load_seq2seq_long_model(checkpoint: dict, device: str) -> tuple[Seq2SeqAttentionLSTMModel, int, int, list[str]]:
    """从 checkpoint 中恢复长期24小时 Seq2Seq Attention LSTM。"""
    encoder_cols = checkpoint.get("feature_col_order") or checkpoint.get("encoder_feature_col_order")
    decoder_cols = checkpoint.get("decoder_feature_col_order")
    if not encoder_cols or not decoder_cols:
        raise ValueError("长期 Seq2Seq checkpoint 缺少 encoder/decoder 特征列信息。")

    input_len = int(checkpoint.get("input_len", LONG_INPUT_LEN) or LONG_INPUT_LEN)
    output_len = int(checkpoint.get("output_len", LONG_FORECAST_HOURS) or LONG_FORECAST_HOURS)
    model = Seq2SeqAttentionLSTMModel(
        encoder_input_size=int(checkpoint.get("n_features", len(encoder_cols))),
        decoder_input_size=int(checkpoint.get("n_decoder_features", len(decoder_cols))),
        hidden_size=int(checkpoint.get("hidden_size", HIDDEN_SIZE) or HIDDEN_SIZE),
        num_layers=int(checkpoint.get("num_layers", NUM_LAYERS) or NUM_LAYERS),
        output_size=output_len,
        dropout=float(checkpoint.get("dropout", DROPOUT) or DROPOUT),
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    model = model.to(device)
    model.eval()
    return model, input_len, output_len, list(decoder_cols)


def _nrmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """计算归一化均方根误差。"""
    rmse = np.sqrt(np.mean((y_true - y_pred) ** 2))
    denom = np.max(y_true) - np.min(y_true)
    if denom <= 1e-8:
        denom = np.std(y_true)
    return float(rmse / (denom + 1e-8))


def _resolve_display_scale(values: np.ndarray, unit_hint: str | None = None) -> tuple[float, str, str]:
    """根据负荷量级自动选择展示单位。"""
    hint = (unit_hint or "").strip().lower()
    if hint == "mw":
        return 1.0, "MW", "MWh"
    if hint == "kw":
        return 1000.0, "MW", "MWh"

    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size and float(np.nanmedian(np.abs(finite))) >= 10000.0:
        return 1000.0, "MW", "MWh"
    return 1.0, "MW", "MWh"


def _resolve_forecast_mode(prediction_mode: str | None, predict_every_hours: int | None) -> tuple[str, int, str, str]:
    """Resolve UI mode to horizon and algorithm while keeping old hour values compatible."""
    mode = (prediction_mode or "").strip().lower()
    if mode not in FORECAST_MODE_CONFIG:
        hours = int(predict_every_hours or DEFAULT_PREDICT_EVERY_HOURS)
        mode = SHORT_FORECAST_MODE if hours == SHORT_FORECAST_HOURS else LONG_FORECAST_MODE
    config = FORECAST_MODE_CONFIG[mode]
    return mode, int(config["horizon"]), str(config["label"]), str(config["algorithm"])


def _configure_chinese_font():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    # ========= 跨平台字体适配 =========
    preferred_fonts = [
        "Microsoft YaHei",
        "SimHei",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "Droid Sans Fallback",
    ]
    try:
        installed_fonts = {f.name for f in fm.fontManager.ttflist}
        chosen_font = next((f for f in preferred_fonts if f in installed_fonts), None)
    except Exception:
        chosen_font = None
    plt.rcParams["font.sans-serif"] = preferred_fonts + ["sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt, fm.FontProperties(family=chosen_font) if chosen_font else fm.FontProperties()


def _draw_prediction_axis(
    ax,
    pred_df: pd.DataFrame,
    metrics: dict,
    display_scale: float,
    unit_label: str,
    history_load: np.ndarray | list[float] | None,
    zh_font,
    history_time: list[str] | None = None,
):
    import matplotlib.dates as mdates

    plot_df = pred_df.copy()
    horizon = len(plot_df)
    future_time = pd.to_datetime(plot_df.get("datetime"), errors="coerce") if "datetime" in plot_df.columns else None
    use_datetime_axis = future_time is not None and future_time.notna().all()
    x = future_time if use_datetime_axis else np.arange(1, horizon + 1)
    true_display = plot_df["true_load"].to_numpy(dtype=float) / display_scale
    pred_display = plot_df["pred_load"].to_numpy(dtype=float) / display_scale

    hist_y = None
    current_x = 0
    if history_load is not None and len(history_load) > 0:
        hist_y = np.asarray(history_load, dtype=float) / display_scale
        hist_time = pd.to_datetime(history_time, errors="coerce") if history_time else None
        if use_datetime_axis and hist_time is not None and len(hist_time) == len(hist_y) and not hist_time.isna().any():
            hist_x = hist_time
            current_x = hist_time[-1]
        else:
            hist_x = np.arange(-len(hist_y) + 1, 1)
            current_x = 0
        ax.plot(hist_x, hist_y, color="#64748B", linewidth=1.6, alpha=0.6, marker=".", markersize=4.5, label="历史输入")

    current_y = hist_y[-1] if hist_y is not None and len(hist_y) > 0 else true_display[0]
    ax.axvline(x=current_x, color="#0F172A", linestyle="--", linewidth=1.35, alpha=0.78, label="当前时刻")
    ax.scatter([current_x], [current_y], color="#0F172A", s=32, zorder=5)
    ax.plot(x, true_display, color="#2563EB", linewidth=1.9, linestyle="-.", marker="o", markersize=4.8, alpha=0.9, label="未来实际负荷")
    ax.plot(x, pred_display, color="#DC2626", linewidth=2.0, linestyle="-.", marker="o", markersize=4.8, alpha=0.92, label="未来预测负荷")
    ax.fill_between(x, true_display, pred_display, color="#94A3B8", alpha=0.18, linewidth=0)
    forecast_type = metrics.get("forecast_type", "负荷预测")
    algorithm = metrics.get("algorithm", "GRU")
    ax.set_title(f"{forecast_type}：{algorithm}，未来{horizon}小时", fontproperties=zh_font, fontsize=14, pad=10)
    ax.set_xlabel("时间", fontproperties=zh_font, fontsize=11)
    ax.set_ylabel(f"负荷 ({unit_label})", fontproperties=zh_font, fontsize=11)
    if use_datetime_axis:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        ax.tick_params(axis="x", rotation=25)
    ax.grid(alpha=0.22)
    ax.legend(loc="upper right", prop=zh_font)

    y_parts = [true_display, pred_display]
    if hist_y is not None:
        y_parts.append(hist_y)
    y_values = np.concatenate([np.asarray(part, dtype=float).reshape(-1) for part in y_parts])
    y_values = y_values[np.isfinite(y_values)]
    if y_values.size:
        y_min = float(np.min(y_values))
        y_max = float(np.max(y_values))
        y_range = max(y_max - y_min, abs(y_max) * 0.12, 1.0)
        ax.set_ylim(y_min - y_range * 0.75, y_max + y_range * 0.75)

    metric_text = (
        f"平均绝对误差 MAE: {metrics['mae']:.2f} {unit_label}\n"
        f"均方根误差 RMSE: {metrics['rmse']:.2f} {unit_label}\n"
        f"平均绝对百分比误差 MAPE: {metrics['mape']:.2f}%\n"
        f"预测准确率: {metrics.get('accuracy', max(0.0, 100.0 - metrics['mape'])):.2f}%\n"
        f"归一化平均绝对误差 NMAE: {metrics['nmae']:.2f}%\n"
        f"决定系数 R2: {metrics['r2']:.4f}"
    )
    ax.text(
        0.012,
        0.965,
        metric_text,
        transform=ax.transAxes,
        va="top",
        fontsize=10.8,
        bbox={"facecolor": "white", "alpha": 0.86, "edgecolor": "#94A3B8", "boxstyle": "round,pad=0.35"},
    )


def _write_prediction_plot(
    pred_df: pd.DataFrame,
    metrics: dict,
    plot_path: str | Path,
    display_scale: float,
    unit_label: str,
    history_df: pd.DataFrame | None = None,
    history_datetime: list[str] | None = None,
) -> str:
    """绘制负荷预测曲线，并把关键误差指标写进图中。"""
    plt, zh_font = _configure_chinese_font()
    # =========================================

    plot_path = Path(plot_path)
    plot_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax1 = plt.subplots(1, 1, figsize=(15.2, 7.0))
    history_load = history_df["load"].to_numpy(dtype=float) if history_df is not None and len(history_df) > 0 else None
    _draw_prediction_axis(ax1, pred_df, metrics, display_scale, unit_label, history_load, zh_font, history_datetime)

    fig.subplots_adjust(left=0.075, right=0.985, top=0.91, bottom=0.12)
    fig.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(plot_path)


def write_combined_prediction_plot(mode_results: dict, combined_path: str | Path) -> str:
    """把短期和长期预测曲线叠加到同一个坐标轴中。"""
    import matplotlib.dates as mdates

    plt, zh_font = _configure_chinese_font()
    combined_path = Path(combined_path)
    combined_path.parent.mkdir(parents=True, exist_ok=True)

    short_result = mode_results.get(SHORT_FORECAST_MODE)
    long_result = mode_results.get(LONG_FORECAST_MODE)
    if not short_result and not long_result:
        raise FileNotFoundError("没有可合成的负荷预测结果。")

    base_result = long_result or short_result
    base_metrics = dict(base_result.get("metrics", {}))
    display_scale = float(base_metrics.get("display_scale", 1.0) or 1.0)
    unit_label = str(base_metrics.get("power_unit", base_result.get("power_unit", "kW")))

    fig, ax = plt.subplots(1, 1, figsize=(17.2, 9.4), facecolor="white")

    history_load = base_result.get("history_load")
    history_time = base_result.get("history_datetime")
    hist_time = pd.to_datetime(history_time, errors="coerce") if history_time else None
    y_parts: list[np.ndarray] = []
    current_x = None
    if history_load:
        hist_y = np.asarray(history_load, dtype=float) / display_scale
        if hist_time is not None and len(hist_time) == len(hist_y) and not hist_time.isna().any():
            hist_x = hist_time
            current_x = hist_time[-1]
        else:
            hist_x = np.arange(-len(hist_y) + 1, 1)
            current_x = 0
        ax.plot(hist_x, hist_y, color="#64748B", linewidth=1.7, alpha=0.62, marker=".", markersize=4.6, label="历史输入")
        ax.scatter([current_x], [hist_y[-1]], color="#0F172A", s=34, zorder=5)
        y_parts.append(hist_y)

    if current_x is None:
        current_x = 0
    ax.axvline(x=current_x, color="#0F172A", linestyle="--", linewidth=1.4, alpha=0.78, label="当前时刻")

    if long_result:
        long_df = pd.read_csv(long_result["output_csv"])
        long_time = pd.to_datetime(long_df.get("datetime"), errors="coerce") if "datetime" in long_df.columns else None
        x_long = long_time if long_time is not None and long_time.notna().all() else long_df["forecast_step"].to_numpy(dtype=float)
        true_long = long_df["true_load"].to_numpy(dtype=float) / display_scale
        pred_long = long_df["pred_load"].to_numpy(dtype=float) / display_scale
        ax.plot(x_long, true_long, color="#2563EB", linewidth=1.9, linestyle="-.", marker="o", markersize=4.4, alpha=0.78, label="未来实际负荷")
        long_input_len = long_result.get("input_len")
        long_label = f"长期24h预测(历史{long_input_len}h)" if long_input_len else "长期24h预测"
        ax.plot(x_long, pred_long, color="#7C3AED", linewidth=2.0, linestyle="-.", marker="o", markersize=4.2, alpha=0.9, label=long_label)
        ax.fill_between(x_long, true_long, pred_long, color="#A78BFA", alpha=0.13, linewidth=0)
        y_parts.extend([true_long, pred_long])

    if short_result:
        short_df = pd.read_csv(short_result["output_csv"])
        short_time = pd.to_datetime(short_df.get("datetime"), errors="coerce") if "datetime" in short_df.columns else None
        x_short = short_time if short_time is not None and short_time.notna().all() else short_df["forecast_step"].to_numpy(dtype=float)
        pred_short = short_df["pred_load"].to_numpy(dtype=float) / display_scale
        ax.plot(x_short, pred_short, color="#DC2626", linewidth=2.4, linestyle="-.", marker="o", markersize=5.4, alpha=0.95, label="短期4h预测")
        y_parts.append(pred_short)

    ax.set_title("用电负荷滚动预测：短期4小时与长期24小时", fontproperties=zh_font, fontsize=18, pad=12)
    ax.set_xlabel("时间", fontproperties=zh_font, fontsize=12)
    ax.set_ylabel(f"负荷 ({unit_label})", fontproperties=zh_font, fontsize=12)
    if isinstance(current_x, pd.Timestamp):
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=10))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        ax.tick_params(axis="x", rotation=25)
    ax.grid(alpha=0.22)
    ax.legend(loc="upper right", prop=zh_font)

    y_values = np.concatenate([np.asarray(part, dtype=float).reshape(-1) for part in y_parts])
    y_values = y_values[np.isfinite(y_values)]
    if y_values.size:
        y_min = float(np.min(y_values))
        y_max = float(np.max(y_values))
        y_range = max(y_max - y_min, abs(y_max) * 0.12, 1.0)
        ax.set_ylim(y_min - y_range * 0.75, y_max + y_range * 0.75)

    metric_lines = []
    if short_result:
        short_metrics = short_result.get("metrics", {})
        metric_lines.append(
            f"短期4h  MAE: {short_metrics.get('mae', 0.0):.2f} {unit_label}  "
            f"RMSE: {short_metrics.get('rmse', 0.0):.2f} {unit_label}  "
            f"MAPE: {short_metrics.get('mape', 0.0):.2f}%  "
            f"NMAE: {short_metrics.get('nmae', 0.0):.2f}%"
        )
    if long_result:
        long_metrics = long_result.get("metrics", {})
        metric_lines.append(
            f"长期24h  MAE: {long_metrics.get('mae', 0.0):.2f} {unit_label}  "
            f"RMSE: {long_metrics.get('rmse', 0.0):.2f} {unit_label}  "
            f"MAPE: {long_metrics.get('mape', 0.0):.2f}%  "
            f"NMAE: {long_metrics.get('nmae', 0.0):.2f}%"
        )
    if metric_lines:
        ax.text(
            0.012,
            0.965,
            "\n".join(metric_lines),
            transform=ax.transAxes,
            va="top",
            fontsize=11.2,
            bbox={"facecolor": "white", "alpha": 0.88, "edgecolor": "#94A3B8", "boxstyle": "round,pad=0.35"},
        )

    fig.subplots_adjust(left=0.065, right=0.985, top=0.90, bottom=0.11)
    fig.savefig(combined_path, dpi=175, bbox_inches="tight")
    plt.close(fig)
    return str(combined_path)


def run_gui_load_prediction(
    data_path: str | Path | None = None,
    model_path: str | Path | None = None,
    output_csv: str | Path | None = None,
    code: int = DEFAULT_CODE,
    predict_every_hours: int = DEFAULT_PREDICT_EVERY_HOURS,
    prediction_mode: str | None = None,
    max_windows: int = 0,
    progress_callback=None,
) -> dict:
    """供界面调用的负荷预测主入口。"""
    del code
    resolved_output_csv = Path(output_csv) if output_csv else DEFAULT_OUTPUT_PATH
    resolved_plot_path = resolved_output_csv.with_suffix(".png")
    resolved_output_csv.parent.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    forecast_mode, forecast_horizon, forecast_type, algorithm = _resolve_forecast_mode(
        prediction_mode,
        predict_every_hours,
    )
    seq2seq_model_path = None
    if forecast_mode == LONG_FORECAST_MODE and DEFAULT_LONG_MODEL_PATH.exists():
        seq2seq_model_path = DEFAULT_LONG_MODEL_PATH
    use_seq2seq = seq2seq_model_path is not None
    use_short_direct_gru = forecast_mode == SHORT_FORECAST_MODE and DEFAULT_SHORT_MODEL_PATH.exists()
    if use_seq2seq:
        resolved_model_path = seq2seq_model_path
    elif use_short_direct_gru:
        resolved_model_path = DEFAULT_SHORT_MODEL_PATH
    else:
        resolved_model_path = Path(model_path or DEFAULT_MODEL_PATH)

    if not resolved_model_path.exists():
        raise FileNotFoundError(f"未找到负荷预测模型：{resolved_model_path}")

    if progress_callback:
        progress_callback(f"正在加载 {algorithm} checkpoint。")
    checkpoint = _load_checkpoint(resolved_model_path, device)
    long_decoder_cols = None
    model_type = str(checkpoint.get("model_type", "")).lower()
    if use_seq2seq and "seq2seq" in model_type:
        model, input_len, output_len, long_decoder_cols = _load_seq2seq_long_model(checkpoint, device)
        algorithm = f"历史{input_len}h Seq2Seq Attention LSTM(未来日历+历史同小时参考特征)"
    elif forecast_mode == LONG_FORECAST_MODE and resolved_model_path == DEFAULT_LONG_MODEL_PATH:
        model, input_len, output_len = _load_lstm_long_model(checkpoint, device)
        algorithm = f"历史{input_len}h直接多输出LSTM"
    else:
        model, input_len, output_len = _load_gru_model(checkpoint, device)
        if use_short_direct_gru:
            algorithm = "4h多输出GRU"
        if forecast_mode == LONG_FORECAST_MODE:
            algorithm = "递归多步GRU(未找到长期LSTM)"

    checkpoint_test_path = checkpoint.get("test_data_path")
    resolved_data_path = Path(data_path) if data_path else Path(checkpoint_test_path or DEFAULT_DATA_PATH)
    if not data_path and not resolved_data_path.exists():
        resolved_data_path = DEFAULT_DATA_PATH
    if not resolved_data_path.exists():
        raise FileNotFoundError(f"未找到负荷数据文件：{resolved_data_path}")

    feature_col_order = checkpoint.get("feature_col_order")
    if not feature_col_order:
        raise ValueError("模型 checkpoint 缺少 feature_col_order，无法按训练顺序构造特征。")

    if progress_callback:
        progress_callback("正在读取负荷与气象数据，并构造时间/滞后特征。")
    raw_df = read_load_data(resolved_data_path)
    feature_df = build_feature_frame(raw_df, feature_col_order)

    eval_offset = checkpoint.get("test_eval_offset")
    if eval_offset:
        feature_df = feature_df.iloc[int(eval_offset):].reset_index(drop=True)
        raw_df = raw_df.iloc[int(eval_offset):].reset_index(drop=True)

    n_features = int(checkpoint.get("n_features", len(feature_col_order)))
    if feature_df.shape[1] != n_features:
        raise ValueError(f"特征数量不匹配：模型需要 {n_features}，当前数据得到 {feature_df.shape[1]}。")

    scaler = scaler_from_checkpoint(checkpoint)
    if scaler is None:
        scaler = SafeMinMaxScaler()
        scaler.fit(feature_df.values)
        if progress_callback:
            progress_callback("checkpoint 未包含归一化参数，已按当前数据拟合 MinMaxScaler。")

    if use_seq2seq:
        forecast_horizon = output_len
    elif use_short_direct_gru:
        forecast_horizon = output_len

    if forecast_mode == LONG_FORECAST_MODE and progress_callback and input_len != LONG_INPUT_LEN:
        progress_callback(
            f"提示：当前长期模型 checkpoint 的输入窗口为 {input_len}h，"
            f"若要使用历史7天预测未来24h，请先用新的 train_lstm_long.py 重新训练生成 168h checkpoint。"
        )
    max_start = len(feature_df) - input_len - forecast_horizon + 1
    if max_start <= 0:
        raise ValueError(f"数据长度不足，至少需要 {input_len + forecast_horizon} 行。")

    start_idx = min(max(0, int(max_windows or 0)), max_start - 1)
    if progress_callback:
        progress_callback(f"开始{forecast_type}：{algorithm}，输入 {input_len} 小时，预测未来 {forecast_horizon} 小时。")

    rated_capacity = float(feature_df["load"].max())
    if use_seq2seq and long_decoder_cols:
        forecast_start_indices = [start_idx] * forecast_horizon
        preds = predict_seq2seq_attention(
            model,
            feature_df,
            scaler,
            start_idx,
            input_len,
            forecast_horizon,
            long_decoder_cols,
            device,
        )
    elif forecast_mode == LONG_FORECAST_MODE and resolved_model_path == DEFAULT_LONG_MODEL_PATH:
        forecast_start_indices = [start_idx] * forecast_horizon
        preds = predict_lstm_direct_24h(model, feature_df, scaler, start_idx, input_len, device)
    elif use_short_direct_gru:
        forecast_start_indices = [start_idx] * forecast_horizon
        preds = predict_gru_direct_multi(model, feature_df, scaler, start_idx, input_len, forecast_horizon, device)
    elif forecast_mode == SHORT_FORECAST_MODE:
        forecast_start_indices = [start_idx] * forecast_horizon
        preds = rolling_predict_with_feedback(model, feature_df, scaler, start_idx, forecast_horizon, input_len, device)
    else:
        forecast_start_indices = [start_idx] * forecast_horizon
        preds = rolling_predict_with_feedback(model, feature_df, scaler, start_idx, forecast_horizon, input_len, device)
    pred_rows = []
    for step, pred in enumerate(preds, start=1):
        target_idx = start_idx + input_len + step - 1
        if target_idx >= len(feature_df):
            continue
        timestamp = raw_df["datetime"].iloc[target_idx] if "datetime" in raw_df.columns else target_idx
        true_load = float(feature_df["load"].iloc[target_idx])
        error = float(pred - true_load)
        pred_rows.append(
            {
                "start_index": start_idx,
                "forecast_step": step,
                "window_start_index": forecast_start_indices[step - 1],
                "target_index": target_idx,
                "datetime": timestamp,
                "pred_load": float(pred),
                "true_load": true_load,
                "abs_error": abs(error),
                "error_pct": error / rated_capacity * 100.0 if rated_capacity > 0 else 0.0,
            }
        )

    if not pred_rows:
        raise ValueError("预测结果为空，无法生成图表。")

    pred_df = pd.DataFrame(pred_rows)
    true_load_all = pred_df["true_load"].to_numpy(dtype=float)
    pred_load_all = pred_df["pred_load"].to_numpy(dtype=float)
    abs_error = pred_df["abs_error"].to_numpy(dtype=float)
    display_scale, power_unit, energy_unit = _resolve_display_scale(true_load_all, raw_df.attrs.get("load_unit"))
    rmse_raw = float(np.sqrt(np.mean((pred_load_all - true_load_all) ** 2)))
    mae_raw = float(np.mean(abs_error))
    mape = float(np.mean(abs_error / (np.abs(true_load_all) + 1e-8)) * 100)
    accuracy = float(max(0.0, 100.0 - mape))
    mean_abs_true = float(np.mean(np.abs(true_load_all)))
    nmae = float(mae_raw / max(mean_abs_true, 1e-8) * 100.0)
    r2 = float(
        1.0
        - np.sum((pred_load_all - true_load_all) ** 2)
        / (np.sum((true_load_all - np.mean(true_load_all)) ** 2) + 1e-8)
    )
    metrics = {
        "rmse": rmse_raw / display_scale,
        "mae": mae_raw / display_scale,
        "mape": mape,
        "accuracy": accuracy,
        "nmae": nmae,
        "nrmse": _nrmse(true_load_all, pred_load_all),
        "r2": r2,
        "display_scale": display_scale,
        "power_unit": power_unit,
        "energy_unit": energy_unit,
        "forecast_type": forecast_type,
        "algorithm": algorithm,
    }

    pred_df[f"pred_load_{power_unit.lower()}"] = pred_df["pred_load"] / display_scale
    pred_df[f"true_load_{power_unit.lower()}"] = pred_df["true_load"] / display_scale
    pred_df.to_csv(resolved_output_csv, index=False, encoding="utf-8-sig")
    history_df = feature_df.iloc[start_idx:start_idx + input_len].copy()
    history_datetime = (
        [str(value) for value in raw_df["datetime"].iloc[start_idx:start_idx + input_len].tolist()]
        if "datetime" in raw_df.columns
        else []
    )
    plot_path = _write_prediction_plot(
        pred_df,
        metrics,
        resolved_plot_path,
        display_scale,
        power_unit,
        history_df,
        history_datetime,
    )

    if progress_callback:
        progress_callback(f"{forecast_type}完成，结果图和 CSV 已生成。")

    return {
        "samples": int(len(pred_df)),
        "windows": 1,
        "input_len": int(input_len),
        "output_len": int(output_len),
        "forecast_horizon": int(forecast_horizon),
        "forecast_mode": forecast_mode,
        "forecast_type": forecast_type,
        "algorithm": algorithm,
        "start_index": int(start_idx),
        "max_start": int(max_start),
        "peak_load_kw": float(np.max(pred_load_all) / display_scale),
        "mean_load_kw": float(np.mean(pred_load_all) / display_scale),
        "next_load_kw": float(pred_load_all[0] / display_scale),
        "energy_kwh": float(np.sum(pred_load_all) / display_scale),
        "power_unit": power_unit,
        "energy_unit": energy_unit,
        "metrics": metrics,
        "data_path": str(resolved_data_path),
        "model_path": str(resolved_model_path),
        "output_csv": str(resolved_output_csv),
        "plot_paths": [plot_path],
        "history_load": [float(value) for value in history_df["load"].to_numpy(dtype=float)],
        "history_datetime": history_datetime,
        "device": device,
    }
