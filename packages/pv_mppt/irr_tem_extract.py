# 加载 irr_temp_train.py 训练得到的两个 pth：
# - irr_temp_model_best_irradiance.pth：辐照度验证 MAE 最优；
# - irr_temp_model_best_temperature.pth：温度验证 MAE 最优；
# 从历史天气数据中构造输入窗口；
# 滚动输出 pred_irradiance、pred_temperature 序列
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import torch

try:
    from .predict.irr_temp_train import (
        DEFAULT_IRRADIANCE_MODEL_PATH,
        DEFAULT_TEMPERATURE_MODEL_PATH,
        DEVICE,
        HISTORY_DAYS,
        SafeMinMaxScaler,
        WeatherLSTMModel,
        forecast_steps_for_days,
        history_steps_for_days,
        postprocess_predictions,
        prepare_weather_dataframe,
        resolve_weather_data_path,
    )
except ImportError:
    from predict.irr_temp_train import (
        DEFAULT_IRRADIANCE_MODEL_PATH,
        DEFAULT_TEMPERATURE_MODEL_PATH,
        DEVICE,
        HISTORY_DAYS,
        SafeMinMaxScaler,
        WeatherLSTMModel,
        forecast_steps_for_days,
        history_steps_for_days,
        postprocess_predictions,
        prepare_weather_dataframe,
        resolve_weather_data_path,
    )


PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_IRRADIANCE_MODEL_PATH = PROJECT_ROOT / "PVmodels" / "irr_temp_model_best_irradiance.pth"
DEFAULT_TEMPERATURE_MODEL_PATH = PROJECT_ROOT / "PVmodels" / "irr_temp_model_best_temperature.pth"

# 默认从历史窗口后偏移一段再取预测段，避免一开始就取边界位
DEFAULT_ROLLING_WINDOW_START_INDEX = 24


def _resolve_model_paths(
    checkpoint_path: Optional[str | Path] = None,
    irradiance_checkpoint_path: Optional[str | Path] = None,
    temperature_checkpoint_path: Optional[str | Path] = None,
) -> tuple[Path, Path]:
    # 统一解析两个模型路径
    # 分别传 irradiance_checkpoint_path 和 temperature_checkpoint_path

    if checkpoint_path is not None:
        path = Path(checkpoint_path)
        return path, path

    irr_path = Path(irradiance_checkpoint_path) if irradiance_checkpoint_path is not None else DEFAULT_IRRADIANCE_MODEL_PATH
    temp_path = Path(temperature_checkpoint_path) if temperature_checkpoint_path is not None else DEFAULT_TEMPERATURE_MODEL_PATH
    return irr_path, temp_path


def _runtime_state_dict(checkpoint: dict[str, Any]) -> dict[str, Any]:
    state = dict(checkpoint["model_state_dict"])
    if "fc1.weight" in state and "head.0.weight" not in state:
        state["head.0.weight"] = state.pop("fc1.weight")
        state["head.0.bias"] = state.pop("fc1.bias")
        state["head.3.weight"] = state.pop("fc2.weight")
        state["head.3.bias"] = state.pop("fc2.bias")
    return state


@dataclass
class WeatherPredictor:
    # 加载单个 pth，并输出辐照度/温度预测结果

    model: WeatherLSTMModel
    feature_scaler: SafeMinMaxScaler
    target_scaler: SafeMinMaxScaler
    feature_cols: list[str]
    target_cols: list[str]
    input_len: int
    device: torch.device
    checkpoint: dict[str, Any]

    @classmethod
    def load(cls, checkpoint_path: str | Path, device: str | torch.device | None = None) -> "WeatherPredictor":
        resolved_device = torch.device(device or DEVICE)
        resolved_path = Path(checkpoint_path)
        if not resolved_path.exists():
            raise FileNotFoundError(f"Checkpoint file not found: {resolved_path}")

        checkpoint = torch.load(str(resolved_path), map_location=resolved_device, weights_only=False)
        model = WeatherLSTMModel(
            input_size=int(checkpoint["n_features_per_timestep"]),
            output_size=len(checkpoint["target_cols"]),
        )
        model.load_state_dict(_runtime_state_dict(checkpoint))
        model.to(resolved_device)
        model.eval()

        feature_scaler = SafeMinMaxScaler()
        feature_scaler.min_ = np.asarray(checkpoint["feature_scaler_min"], dtype=np.float32)
        feature_scaler.max_ = np.asarray(checkpoint["feature_scaler_max"], dtype=np.float32)
        feature_scaler.scale_ = np.asarray(checkpoint["feature_scaler_scale"], dtype=np.float32)

        target_scaler = SafeMinMaxScaler()
        target_scaler.min_ = np.asarray(checkpoint["target_scaler_min"], dtype=np.float32)
        target_scaler.max_ = np.asarray(checkpoint["target_scaler_max"], dtype=np.float32)
        target_scaler.scale_ = np.asarray(checkpoint["target_scaler_scale"], dtype=np.float32)

        checkpoint_input_len = int(checkpoint["input_len"])
        runtime_input_len = min(
            checkpoint_input_len,
            history_steps_for_days(float(checkpoint.get("step_minutes", 15.0)), HISTORY_DAYS),
        )

        return cls(
            model=model,
            feature_scaler=feature_scaler,
            target_scaler=target_scaler,
            feature_cols=list(checkpoint["feature_cols"]),
            target_cols=list(checkpoint["target_cols"]),
            input_len=runtime_input_len,
            device=resolved_device,
            checkpoint=checkpoint,
        )

    def predict_next_from_window(self, feature_window: np.ndarray) -> dict[str, float]:
        # 根据一个历史窗口预测下一时刻辐照度/温度
        expected_shape = (self.input_len, len(self.feature_cols))
        if feature_window.shape != expected_shape:
            raise ValueError(f"Expected window shape {expected_shape}, got {feature_window.shape}")

        scaled_window = self.feature_scaler.transform(feature_window)
        x = torch.tensor(scaled_window[None, :, :], dtype=torch.float32, device=self.device)
        with torch.no_grad():
            pred_scaled = self.model(x).cpu().numpy()
        pred = postprocess_predictions(self.target_scaler.inverse_transform(pred_scaled))[0]
        return {name: float(value) for name, value in zip(self.target_cols, pred)}

    def predict_next_from_file(self, data_path: str | Path | None = None) -> dict[str, Any]:
        # 读取数据文件最后一段历史窗口，预测下一时刻
        frame, metadata = prepare_weather_dataframe(resolve_weather_data_path(data_path))
        aligned = frame.reindex(columns=self.feature_cols, fill_value=0.0)

        if len(aligned) < self.input_len:
            raise ValueError(f"Need at least {self.input_len} rows for inference.")

        window = aligned.iloc[-self.input_len :].values.astype(np.float32)
        next_timestamp = metadata["timestamps"].iloc[-1] + pd.to_timedelta(metadata["step_minutes"], unit="m")
        result = self.predict_next_from_window(window)
        result["next_timestamp"] = str(pd.Timestamp(next_timestamp))
        return result

    def walk_forward_future_from_file(
        self,
        data_path: str | Path | None = None,
        max_steps: int | None = None,
        start_offset: int = 0,
    ) -> pd.DataFrame:
        # 在数据文件中滚动预测一段未来序列
        frame, metadata = prepare_weather_dataframe(resolve_weather_data_path(data_path))
        aligned = frame.reindex(columns=self.feature_cols, fill_value=0.0)
        timestamps = metadata["timestamps"].reset_index(drop=True)

        future_start_idx = int(DEFAULT_ROLLING_WINDOW_START_INDEX) + self.input_len + max(0, int(start_offset))
        future_start_idx = max(self.input_len, min(future_start_idx, len(aligned) - 1))

        total_steps = len(aligned) - future_start_idx
        if total_steps <= 0:
            raise ValueError("No future samples are available for walk-forward prediction.")
        if max_steps is not None:
            total_steps = min(total_steps, int(max_steps))

        rows = []
        for step in range(total_steps):
            target_idx = future_start_idx + step
            window = aligned.iloc[target_idx - self.input_len : target_idx].values.astype(np.float32)
            pred = self.predict_next_from_window(window)

            true_irr = float(frame.iloc[target_idx]["irradiance"])
            true_temp = float(frame.iloc[target_idx]["temperature"])
            start_idx = max(target_idx - 1, 0)

            rows.append(
                {
                    "step": step + 1,
                    "timestamp": timestamps.iloc[target_idx],
                    "source_start_timestamp": timestamps.iloc[target_idx - self.input_len],
                    "source_end_timestamp": timestamps.iloc[target_idx - 1],
                    "start_irradiance": float(frame.iloc[start_idx]["irradiance"]),
                    "start_temperature": float(frame.iloc[start_idx]["temperature"]),
                    "pred_irradiance": float(pred["irradiance"]),
                    "pred_temperature": float(pred["temperature"]),
                    "true_irradiance": true_irr,
                    "true_temperature": true_temp,
                    "irradiance_error": float(pred["irradiance"]) - true_irr,
                    "temperature_error": float(pred["temperature"]) - true_temp,
                }
            )

        return pd.DataFrame(rows)


@dataclass
class DualBestWeatherPredictor:
    # 分别使用辐照度最佳和温度最佳 checkpoint 的轻量组合

    irradiance_predictor: WeatherPredictor
    temperature_predictor: WeatherPredictor

    @classmethod
    def load(
        cls,
        irradiance_checkpoint_path: str | Path | None = None,
        temperature_checkpoint_path: str | Path | None = None,
        device: str | torch.device | None = None,
    ) -> "DualBestWeatherPredictor":
        irr_path = Path(irradiance_checkpoint_path or DEFAULT_IRRADIANCE_MODEL_PATH)
        temp_path = Path(temperature_checkpoint_path or DEFAULT_TEMPERATURE_MODEL_PATH)
        return cls(
            irradiance_predictor=WeatherPredictor.load(irr_path, device=device),
            temperature_predictor=WeatherPredictor.load(temp_path, device=device),
        )

    @property
    def checkpoint(self) -> dict[str, Any]:
        checkpoint = dict(self.irradiance_predictor.checkpoint)
        checkpoint["irradiance_checkpoint"] = self.irradiance_predictor.checkpoint
        checkpoint["temperature_checkpoint"] = self.temperature_predictor.checkpoint
        return checkpoint

    def predict_next_from_file(self, data_path: str | Path | None = None) -> dict[str, Any]:
        irr = self.irradiance_predictor.predict_next_from_file(data_path)
        temp = self.temperature_predictor.predict_next_from_file(data_path)
        return {
            "irradiance": float(irr["irradiance"]),
            "temperature": float(temp["temperature"]),
            "next_timestamp": irr.get("next_timestamp", temp.get("next_timestamp")),
        }

    def walk_forward_future_from_file(
        self,
        data_path: str | Path | None = None,
        max_steps: int | None = None,
        start_offset: int = 0,
    ) -> pd.DataFrame:
        irr_frame = self.irradiance_predictor.walk_forward_future_from_file(
            data_path=data_path,
            max_steps=max_steps,
            start_offset=start_offset,
        )
        temp_frame = self.temperature_predictor.walk_forward_future_from_file(
            data_path=data_path,
            max_steps=max_steps,
            start_offset=start_offset,
        )

        n = min(len(irr_frame), len(temp_frame))
        if n <= 0:
            return irr_frame.iloc[:0].copy()

        frame = irr_frame.iloc[:n].copy().reset_index(drop=True)
        temp_frame = temp_frame.iloc[:n].reset_index(drop=True)

        # 只替换温度预测相关字段；辐照度预测字段保留辐照度最佳 checkpoint 的结果
        frame["pred_temperature"] = temp_frame["pred_temperature"].to_numpy(dtype=float)
        frame["temperature_error"] = frame["pred_temperature"] - frame["true_temperature"]
        return frame


def _load_predictor(
    checkpoint_path: Optional[str | Path] = None,
    irradiance_checkpoint_path: Optional[str | Path] = None,
    temperature_checkpoint_path: Optional[str | Path] = None,
):
    # 加载并缓存双最佳预测器
    irr_path, temp_path = _resolve_model_paths(checkpoint_path, irradiance_checkpoint_path, temperature_checkpoint_path)
    return _load_predictor_cached(str(irr_path), str(temp_path))


@lru_cache(maxsize=4)
def _load_predictor_cached(irradiance_checkpoint_path: str, temperature_checkpoint_path: str):
    # 同一路径重复调用时直接复用已加载模型
    return DualBestWeatherPredictor.load(Path(irradiance_checkpoint_path), Path(temperature_checkpoint_path))


def get_predicted_irr_temp_future_frame(
    data_path: Optional[str | Path] = None,
    checkpoint_path: Optional[str | Path] = None,
    irradiance_checkpoint_path: Optional[str | Path] = None,
    temperature_checkpoint_path: Optional[str | Path] = None,
    max_steps: Optional[int] = None,
    forecast_days: float = 1.0,
    start_offset: int = 0,
):
    # 返回未来辐照度/温度预测序列，供光伏主仿真使用
    data_text = str(Path(data_path).resolve()) if data_path is not None else None
    data_mtime = Path(data_text).stat().st_mtime_ns if data_text is not None and Path(data_text).exists() else None

    irr_path, temp_path = _resolve_model_paths(checkpoint_path, irradiance_checkpoint_path, temperature_checkpoint_path)
    frame = _get_predicted_future_frame_cached(
        data_text,
        data_mtime,
        str(irr_path),
        str(temp_path),
        max_steps,
        float(forecast_days),
        int(start_offset),
    )
    return frame.copy(deep=True)


@lru_cache(maxsize=64)
def _get_predicted_future_frame_cached(
    data_path: str | None,
    data_mtime_ns: int | None,
    irradiance_checkpoint_path: str,
    temperature_checkpoint_path: str,
    max_steps: Optional[int],
    forecast_days: float,
    start_offset: int,
):
    # 缓存预测结果；数据文件修改时间参与缓存键，避免读到过期预测
    del data_mtime_ns

    predictor = _load_predictor(
        irradiance_checkpoint_path=irradiance_checkpoint_path,
        temperature_checkpoint_path=temperature_checkpoint_path,
    )
    if max_steps is None and forecast_days > 0:
        step_minutes = float(predictor.checkpoint.get("step_minutes", 15.0))
        max_steps = forecast_steps_for_days(step_minutes, forecast_days)

    return predictor.walk_forward_future_from_file(
        data_path=data_path,
        max_steps=max_steps,
        start_offset=start_offset,
    )


if __name__ == "__main__":
    # 直接运行本文件时，简单打印一段预测结果，方便快速检查 pth 和数据路径是否正确
    preview = get_predicted_irr_temp_future_frame(max_steps=10)
    print(preview.head(10).to_string(index=False))
