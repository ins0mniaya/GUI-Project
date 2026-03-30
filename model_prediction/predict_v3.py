import argparse
import os

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

DEFAULT_DATA_FILE = "model_prediction/data1.xlsx"
DEFAULT_CHECKPOINT_FILE = "model_prediction/solar_power_lstm_model_v3.pth"


def series_to_supervised(data, n_in=1, n_out=1, dropnan=True):
    n_vars = 1 if type(data) is list else data.shape[1]
    df = pd.DataFrame(data)
    cols, names = list(), list()

    for i in range(n_in, 0, -1):
        cols.append(df.shift(i))
        names += [("var%d(t-%d)" % (j + 1, i)) for j in range(n_vars)]

    for i in range(0, n_out):
        cols.append(df.shift(-i))
        if i == 0:
            names += [("var%d(t)" % (j + 1)) for j in range(n_vars)]
        else:
            names += [("var%d(t+%d)" % (j + 1, i)) for j in range(n_vars)]

    agg = pd.concat(cols, axis=1)
    agg.columns = names

    if dropnan:
        agg.dropna(inplace=True)

    return agg


def normalize_data(dataset, data_min, data_max):
    if data_min == data_max:
        return np.zeros_like(dataset)
    return (dataset - data_min) / (data_max - data_min)


def denormalize_data(normalized_dataset, data_min, data_max):
    return normalized_dataset * (data_max - data_min) + data_min


def import_data_from_single_file(file_path, train_ratio=0.7, val_ratio=0.15, n_in=24, n_out=1):
    # 1) 输入数据 + 2) 解析数据
    if file_path.lower().endswith(".xlsx") or file_path.lower().endswith(".xls"):
        df = pd.read_excel(file_path)
    else:
        encodings_to_try = ["utf-8", "latin1", "gbk", "gb2312", "cp1252", "iso-8859-1"]
        for encoding in encodings_to_try:
            try:
                df = pd.read_csv(file_path, sep=",", engine="python", encoding=encoding)
                break
            except Exception:
                continue
        else:
            raise ValueError("无法读取CSV文件，请检查文件格式或编码")

    if "TIMESTAMP" in df.columns:
        timestamp_series = pd.to_datetime(df["TIMESTAMP"].astype(str), format="%Y%m%d %H:%M", errors="coerce")
        if timestamp_series.isna().any():
            timestamp_series = pd.to_datetime(df["TIMESTAMP"].astype(str), errors="coerce")

        df["YEAR"] = timestamp_series.dt.year
        df["MONTH"] = timestamp_series.dt.month
        df["DAY"] = timestamp_series.dt.day
        df["HOUR"] = timestamp_series.dt.hour
        df.drop(columns=["TIMESTAMP"], inplace=True)

    if "ZONEID" in df.columns:
        df.drop(columns=["ZONEID"], inplace=True)

    for col in df.columns:
        if df[col].dtype == "object":
            converted_col = pd.to_numeric(df[col], errors="coerce")
            if not converted_col.isna().all():
                df[col] = converted_col
            else:
                df[col] = pd.factorize(df[col])[0]

    missing_values = df.isnull().sum()
    if missing_values.sum() > 0:
        df.ffill(inplace=True)
        df.bfill(inplace=True)

    dataset = df.values.astype("float32")

    n_features = dataset.shape[1]
    if "POWER" in df.columns:
        target_col = df.columns.get_loc("POWER")
    else:
        target_col = n_features - 1

    supervised_data = series_to_supervised(dataset, n_in=n_in, n_out=n_out)

    n_vars = dataset.shape[1]
    target_idx = n_vars * n_in + target_col

    input_cols = list(range(supervised_data.shape[1]))
    for i in range(n_out):
        col_to_remove = n_vars * n_in + target_col + i * n_vars
        if col_to_remove in input_cols:
            input_cols.remove(col_to_remove)

    features = supervised_data.iloc[:, input_cols].values
    targets = supervised_data.iloc[:, target_idx].values.reshape(-1, 1)

    n_samples = len(features)
    n_train = int(n_samples * train_ratio)
    n_val = int(n_samples * val_ratio)

    train_features = features[:n_train]
    train_targets = targets[:n_train]

    val_features = features[n_train : n_train + n_val]
    val_targets = targets[n_train : n_train + n_val]

    test_features = features[n_train + n_val :]
    test_targets = targets[n_train + n_val :]

    feature_mins = np.min(train_features, axis=0)
    feature_maxs = np.max(train_features, axis=0)
    target_min = np.min(train_targets)
    target_max = np.max(train_targets)

    normalized_train_features = np.zeros_like(train_features)
    for i in range(train_features.shape[1]):
        normalized_train_features[:, i] = normalize_data(train_features[:, i], feature_mins[i], feature_maxs[i])
    normalized_train_targets = normalize_data(train_targets, target_min, target_max)

    normalized_val_features = np.zeros_like(val_features)
    for i in range(val_features.shape[1]):
        normalized_val_features[:, i] = normalize_data(val_features[:, i], feature_mins[i], feature_maxs[i])
    normalized_val_targets = normalize_data(val_targets, target_min, target_max)

    normalized_test_features = np.zeros_like(test_features)
    for i in range(test_features.shape[1]):
        normalized_test_features[:, i] = normalize_data(test_features[:, i], feature_mins[i], feature_maxs[i])
    normalized_test_targets = normalize_data(test_targets, target_min, target_max)

    return (
        normalized_train_features,
        normalized_train_targets,
        normalized_val_features,
        normalized_val_targets,
        normalized_test_features,
        normalized_test_targets,
        target_max,
        target_min,
    )


class LSTMModel(nn.Module):
    def __init__(self, input_size=12, hidden_size=128, num_layers=2, output_size=1, dropout=0.2):
        super(LSTMModel, self).__init__()

        self.lstm1 = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=1,
            batch_first=True,
            dropout=0,
        )
        self.dropout1 = nn.Dropout(dropout)

        self.lstm2 = nn.LSTM(
            input_size=hidden_size,
            hidden_size=hidden_size // 2,
            num_layers=1,
            batch_first=True,
            dropout=0,
        )
        self.dropout2 = nn.Dropout(dropout)

        self.fc1 = nn.Linear(hidden_size // 2, hidden_size // 4)
        self.relu = nn.ReLU()
        self.dropout3 = nn.Dropout(dropout)
        self.fc2 = nn.Linear(hidden_size // 4, output_size)

    def forward(self, x):
        output1, _ = self.lstm1(x)
        output1 = self.dropout1(output1)

        output2, _ = self.lstm2(output1)
        output2 = self.dropout2(output2)

        last_output = output2[:, -1, :]

        x = self.fc1(last_output)
        x = self.relu(x)
        x = self.dropout3(x)
        x = self.fc2(x)

        return x


def resolve_existing_path(*candidates):
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def load_model_from_checkpoint(checkpoint_path, device):
    # 3) 导入模型
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    state_dict = checkpoint["model_state_dict"]

    input_size = state_dict["lstm1.weight_ih_l0"].shape[1]
    hidden_size = state_dict["lstm1.weight_ih_l0"].shape[0] // 4

    model = LSTMModel(
        input_size=input_size,
        hidden_size=hidden_size,
        num_layers=2,
        output_size=1,
        dropout=0.3,
    )
    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    return model, checkpoint


def reshape_features(features, n_hours, features_per_timestep, device):
    reshaped = np.zeros((features.shape[0], n_hours, features_per_timestep), dtype=np.float32)
    for i in range(n_hours):
        start_idx = i * features_per_timestep
        end_idx = (i + 1) * features_per_timestep
        reshaped[:, i, :] = features[:, start_idx:end_idx]
    return torch.FloatTensor(reshaped).to(device)


def predict_tensor(model, x_tensor):
    # 4) 模型预测
    with torch.no_grad():
        pred = model(x_tensor)
    return pred.cpu().numpy().reshape(-1)


def run_prediction(data_path=None, checkpoint_path=None, output_path="predictions_v3.csv"):
    """CPU-only prediction pipeline for UI and CLI."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    resolved_data_path = resolve_existing_path(
        data_path,
        os.path.join(script_dir, DEFAULT_DATA_FILE),
        os.path.join(desktop_dir, DEFAULT_DATA_FILE),
    )
    if resolved_data_path is None:
        raise FileNotFoundError("找不到数据文件，请通过 data_path 指定路径。")

    resolved_checkpoint_path = resolve_existing_path(
        checkpoint_path,
        os.path.join(script_dir, DEFAULT_CHECKPOINT_FILE),
        os.path.join(desktop_dir, DEFAULT_CHECKPOINT_FILE),
    )
    if resolved_checkpoint_path is None:
        raise FileNotFoundError("找不到模型文件，请通过 checkpoint_path 指定路径。")

    # 禁用 CUDA，固定使用 CPU 推理。
    device = torch.device("cpu")

    model, checkpoint = load_model_from_checkpoint(resolved_checkpoint_path, device)

    n_hours = int(checkpoint.get("n_hours", 24))

    (
        train_features,
        train_targets,
        val_features,
        val_targets,
        test_features,
        test_targets,
        data_target_max,
        data_target_min,
    ) = import_data_from_single_file(
        resolved_data_path,
        train_ratio=0.7,
        val_ratio=0.15,
        n_in=n_hours,
        n_out=1,
    )

    total_features = train_features.shape[1]
    features_per_timestep = total_features // n_hours

    x_train = reshape_features(train_features, n_hours, features_per_timestep, device)
    x_val = reshape_features(val_features, n_hours, features_per_timestep, device)
    x_test = reshape_features(test_features, n_hours, features_per_timestep, device)

    train_pred_norm = predict_tensor(model, x_train)
    val_pred_norm = predict_tensor(model, x_val)
    test_pred_norm = predict_tensor(model, x_test)

    target_min = checkpoint.get("min_test", data_target_min)
    target_max = checkpoint.get("max_test", data_target_max)

    train_pred = denormalize_data(train_pred_norm, target_min, target_max)
    val_pred = denormalize_data(val_pred_norm, target_min, target_max)
    test_pred = denormalize_data(test_pred_norm, target_min, target_max)

    train_true = denormalize_data(train_targets.reshape(-1), target_min, target_max)
    val_true = denormalize_data(val_targets.reshape(-1), target_min, target_max)
    test_true = denormalize_data(test_targets.reshape(-1), target_min, target_max)

    split_train = np.full(train_pred.shape[0], "train")
    split_val = np.full(val_pred.shape[0], "validation")
    split_test = np.full(test_pred.shape[0], "test")

    result_df = pd.DataFrame(
        {
            "split": np.concatenate([split_train, split_val, split_test]),
            "actual_power": np.concatenate([train_true, val_true, test_true]),
            "predicted_power": np.concatenate([train_pred, val_pred, test_pred]),
        }
    )

    if output_path:
        result_df.to_csv(output_path, index=False, encoding="utf-8-sig")

    latest_prediction = float(result_df["predicted_power"].iloc[-1])
    latest_actual = float(result_df["actual_power"].iloc[-1])
    peak_prediction = float(result_df["predicted_power"].max())
    total_prediction_kwh = float(result_df["predicted_power"].sum() / 1000.0)

    # 5) 预测结果
    return {
        "device": str(device),
        "data_path": resolved_data_path,
        "checkpoint_path": resolved_checkpoint_path,
        "output_path": os.path.abspath(output_path) if output_path else None,
        "samples": int(len(result_df)),
        "latest_prediction": latest_prediction,
        "latest_actual": latest_actual,
        "peak_prediction": peak_prediction,
        "total_prediction_kwh": total_prediction_kwh,
    }


def main():
    parser = argparse.ArgumentParser(description="Fast prediction using trained solar power LSTM model")
    parser.add_argument("--data", type=str, default=None, help="Excel/CSV data path")
    parser.add_argument("--checkpoint", type=str, default=None, help="Model checkpoint path (.pth)")
    parser.add_argument("--output", type=str, default="predictions_v3.csv", help="Prediction output CSV path")
    parser.add_argument("--latest-only", action="store_true", help="Only print latest prediction")
    args = parser.parse_args()

    result = run_prediction(
        data_path=args.data,
        checkpoint_path=args.checkpoint,
        output_path=args.output,
    )

    if args.latest_only:
        print(f"latest_predicted_power={result['latest_prediction']:.4f}")
    else:
        print(f"Using device: {result['device']}")
        print(f"数据文件: {result['data_path']}")
        print(f"模型文件: {result['checkpoint_path']}")
        print(f"预测完成，总样本数: {result['samples']}")
        print(f"最新一个时刻预测功率: {result['latest_prediction']:.4f}")
        print(f"最新一个时刻真实功率: {result['latest_actual']:.4f}")
        if result["output_path"]:
            print(f"结果已保存: {result['output_path']}")
    
