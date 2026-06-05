"""光伏 GUI 调用入口：负责参数整理、路径管理和仿真结果补充。"""
from __future__ import annotations
import os
from pathlib import Path
import numpy as np
import pandas as pd

if __package__ is None or __package__ == "":
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))

    __package__ = "packages.pv_mppt"
from .pv_plotter import (
    generate_scenario_validation_plots as _generate_scenario_validation_plots,
    get_plot_paths as _plotter_main_paths,
    get_scenario_validation_plot_paths as _plotter_scenario_paths,
    get_validation_plot_paths as _plotter_validation_paths,
    plot_validation_results,
)

PACKAGE_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = PACKAGE_ROOT / "rescults"
DEFAULT_DATA_PATH = PACKAGE_ROOT / "predict" / "weather_data.xlsx"
DEFAULT_IRRADIANCE_MODEL_PATH = PACKAGE_ROOT / "PVmodels" / "irr_temp_model_best_irradiance.pth"
DEFAULT_TEMPERATURE_MODEL_PATH = PACKAGE_ROOT / "PVmodels" / "irr_temp_model_best_temperature.pth"
DEFAULT_OUTPUT_PATH = RESULTS_DIR / "pv_output.csv"


DEFAULT_PV_PARAMS = {
    "module_Pmax": 590.0,
    "module_Vmp": 44.17,
    "module_Imp": 13.36,
    "module_Voc": 52.90,
    "module_Isc": 14.07,
    "modules_series": 6,
    "modules_parallel": 2,
    "cells_series_per_module": 72,
}

DEFAULT_RUNTIME_PARAMS = {
    "forecast_days": 0.0,
    "rolling_batch_steps": 1,
    "controller_dt": 0.1,
    "dc_bus_voltage": 370.0,
    "mppt_enable_irradiance": 20.0,
    "plot_update_interval_seconds": 1.0,
    "real_time_playback": True,
    "playback_speed": 1.0,
}



def clear_plot_outputs() -> None:
    if not RESULTS_DIR.exists():
        return

    protected: set[str] = set()
    patterns = [
        "pv_mppt_tracking*.png",
        "pv_electrical_power_output*.png",
        "boost_output_voltage*.png",
        "pv_final_performance_summary*.png",
        "pv_voltage_tracking*.png",
        "pv_weather_temperature_validation*.png",
        "pv_voltage_tracking_validation*.png",
        "pv_duty_efficiency_validation*.png",
        "pv_energy_validation*.png",
        ".pv_*.png",
        ".boost_*.png",
        "*.png.tmp",
    ]
    for pattern in patterns:
        for path in RESULTS_DIR.glob(pattern):
            if path.name in protected or not path.is_file():
                continue
            try:
                path.unlink()
            except OSError:
                pass


def run_gui_pv_simulation(
    data_path: str | Path | None = None,
    output_csv: str | Path | None = None,
    forecast_days: float = 0.0,
    max_weather_steps: int | None = 1,
    controller_dt: float = 0.1,
    irradiance_checkpoint_path: str | Path | None = None,
    temperature_checkpoint_path: str | Path | None = None,
    pv_params: dict | None = None,
    dc_bus_voltage: float = 370.0,
    mppt_enable_irradiance: float = 20.0,
    progress_callback=None,
    plot_update_interval_seconds: float | None = 1.0,
    pause_callback=None,
    stop_callback=None,
    rolling_start_offset: int = 0,
    real_time_playback: bool = True,
    playback_speed: float = 1.0,
    generate_validation_outputs: bool = False,
) -> dict:
    #供界面调用的光伏仿真主入口
    os.environ["PV_SKIP_PLOTS"] = "0"

    from .pv_main import run_long_term_simulation
    from .pv_module_params import PVModuleParams

    resolved_data_path = Path(data_path) if data_path else DEFAULT_DATA_PATH
    resolved_output_csv = Path(output_csv) if output_csv else DEFAULT_OUTPUT_PATH
    resolved_output_csv.parent.mkdir(parents=True, exist_ok=True)

    merged_pv_params = {**DEFAULT_PV_PARAMS, **(pv_params or {})}
    pv_module_params = PVModuleParams(**merged_pv_params)

    result = run_long_term_simulation(
        controller_dt=controller_dt,
        max_weather_steps=max_weather_steps,
        forecast_days=forecast_days,
        output_csv=str(resolved_output_csv),
        data_path=str(resolved_data_path),
        irradiance_checkpoint_path=str(irradiance_checkpoint_path or DEFAULT_IRRADIANCE_MODEL_PATH),
        temperature_checkpoint_path=str(temperature_checkpoint_path or DEFAULT_TEMPERATURE_MODEL_PATH),
        pv_params=pv_module_params,
        dc_bus_voltage=dc_bus_voltage,
        mppt_enable_irradiance=mppt_enable_irradiance,
        progress_callback=progress_callback,
        plot_update_interval_seconds=plot_update_interval_seconds,
        pause_callback=pause_callback,
        stop_callback=stop_callback,
        rolling_start_offset=rolling_start_offset,
        real_time_playback=real_time_playback,
        playback_speed=playback_speed,
        generate_validation_outputs=generate_validation_outputs,
    )
    result["data_path"] = str(resolved_data_path)
    result["output_csv"] = str(resolved_output_csv)
    result["plot_paths"] = result.get("plot_paths", get_plot_paths())
    result["validation_plot_paths"] = result.get("validation_plot_paths", get_validation_plot_paths())
    result["scenario_validation_paths"] = result.get("scenario_validation_paths", get_scenario_validation_plot_paths())
    result["control_validation"] = validate_control_output(resolved_output_csv, dc_bus_voltage=dc_bus_voltage)
    result["accuracy_plot_paths"] = []
    result.pop("metrics", None)
    return result


def get_accuracy_plot_paths() -> list[str]:
    return []


def get_plot_paths() -> list[str]:
    return _plotter_main_paths(RESULTS_DIR)


def get_validation_plot_paths() -> list[str]:
    return _plotter_validation_paths(RESULTS_DIR)


def get_scenario_validation_plot_paths() -> list[str]:
    return _plotter_scenario_paths(RESULTS_DIR)


def _load_output_csv_arrays(output_csv: str | Path) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """从光伏输出 CSV 中恢复绘图和校验所需的数组。"""
    path = Path(output_csv)
    df = pd.read_csv(path)
    column_map = {
        "time": "time_s",
        "physical_time": "physical_time_s",
        "pred_G": "pred_G_w_m2",
        "pred_T_ambient": "pred_T_ambient_degC",
        "T_cell": "T_cell_degC",
        "V_pv": "V_pv_v",
        "V_ref": "V_ref_v",
        "I_pv": "I_pv_a",
        "P_pv": "P_pv_w",
        "P_mpp": "P_mpp_w",
        "P_boost_out": "P_boost_out_w",
        "boost_efficiency": "boost_efficiency",
        "V_out": "V_out_v",
        "I_L": "I_L_a",
        "duty": "duty",
        "array_energy_kwh": "array_energy_kwh",
        "boost_energy_kwh": "boost_energy_kwh",
    }
    arrays = {
        key: pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)
        for key, column in column_map.items()
    }
    sim = {
        "time": arrays["time"],
        "G": arrays["pred_G"],
        "T_ambient": arrays["pred_T_ambient"],
        "T_cell": arrays["T_cell"],
        "V_pv": arrays["V_pv"],
        "I_pv": arrays["I_pv"],
        "P_pv": arrays["P_pv"],
        "P_mpp": arrays["P_mpp"],
        "P_boost_out": arrays["P_boost_out"],
        "boost_efficiency": arrays["boost_efficiency"],
        "V_ref": arrays["V_ref"],
        "V_out": arrays["V_out"],
        "I_L": arrays["I_L"],
        "duty": arrays["duty"],
    }
    return arrays, sim

def generate_scenario_validation_plots(
    pv_params: dict | None = None,
    controller_dt: float = 0.1,
    dc_bus_voltage: float = 370.0,
    mppt_enable_irradiance: float = 20.0,
) -> dict:
    from .pv_module_params import PVModuleParams

    merged_pv_params = {**DEFAULT_PV_PARAMS, **(pv_params or {})}
    params = PVModuleParams(**merged_pv_params)
    return _generate_scenario_validation_plots(
        params=params,
        controller_dt=controller_dt,
        dc_bus_voltage=dc_bus_voltage,
        mppt_enable_irradiance=mppt_enable_irradiance,
        output_dir=RESULTS_DIR,
    )


def validate_control_output(output_csv: str | Path, dc_bus_voltage: float = 370.0) -> dict:
    # 检查光伏控制输出的数值范围是否满足基本物理约束
    path = Path(output_csv)
    if not path.exists():
        return {"ok": False, "message": "仿真输出CSV不存在"}

    df = pd.read_csv(path)
    required_cols = [
        "P_pv_w",
        "P_mpp_w",
        "P_boost_out_w",
        "boost_efficiency",
        "V_out_v",
        "I_L_a",
        "duty",
        "V_pv_v",
        "V_ref_v",
        "array_energy_kwh",
        "boost_energy_kwh",
    ]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        return {"ok": False, "message": f"缺少字段: {', '.join(missing)}"}

    numeric = df[required_cols].apply(pd.to_numeric, errors="coerce")
    mppt_valid = numeric["P_mpp_w"] > 50.0
    if not bool(mppt_valid.any()):
        mppt_valid = numeric["P_mpp_w"] > 1.0
    boost_valid = mppt_valid & (numeric["P_pv_w"] > 1.0)
    mppt_efficiency = (numeric["P_pv_w"] / numeric["P_mpp_w"].where(mppt_valid, 1.0)).clip(lower=0.0, upper=1.2) * 100.0
    boost_efficiency_pct = numeric["boost_efficiency"].clip(lower=0.0, upper=1.2) * 100.0
    voltage_error = (numeric["V_pv_v"] - numeric["V_ref_v"]).abs()
    cumulative_array = numeric["array_energy_kwh"].fillna(0.0).cumsum()
    cumulative_boost = numeric["boost_energy_kwh"].fillna(0.0).cumsum()
    cumulative_loss = cumulative_array - cumulative_boost

    checks = {
        "samples": int(len(df)),
        "has_nan": bool(numeric.isna().any().any()),
        "power_nonnegative": bool((numeric["P_boost_out_w"] >= -1e-6).all()),
        "efficiency_range": bool(((numeric["boost_efficiency"] >= -1e-6) & (numeric["boost_efficiency"] <= 1.0001)).all()),
        "duty_range": bool(((numeric["duty"] >= -1e-6) & (numeric["duty"] <= 0.9501)).all()),
        "voltage_nonnegative": bool(((numeric["V_out_v"] >= -1e-6) & (numeric["V_pv_v"] >= -1e-6)).all()),
        "inductor_current_nonnegative": bool((numeric["I_L_a"] >= -1e-6).all()),
        "boost_not_exceed_array": bool((numeric["P_boost_out_w"] <= numeric["P_pv_w"] + 25.0).all()),
        "dc_bus_stable": bool((numeric["V_out_v"] - float(dc_bus_voltage)).abs().le(max(5.0, 0.02 * float(dc_bus_voltage))).all()),
        "energy_loss_nonnegative": bool(cumulative_loss.ge(-1e-9).all()),
        "mppt_efficiency_reasonable": bool(mppt_efficiency[mppt_valid].mean() >= 85.0) if bool(mppt_valid.any()) else True,
    }
    ok = all(value for key, value in checks.items() if key not in ("samples", "has_nan")) and not checks["has_nan"]
    checks["ok"] = bool(ok)
    checks["mppt_efficiency_mean_pct"] = float(mppt_efficiency[mppt_valid].mean()) if bool(mppt_valid.any()) else 0.0
    checks["boost_efficiency_mean_pct"] = float(boost_efficiency_pct[boost_valid].mean()) if bool(boost_valid.any()) else 0.0
    checks["mean_abs_voltage_error_v"] = float(voltage_error[mppt_valid].mean()) if bool(mppt_valid.any()) else float(voltage_error.mean())
    checks["max_abs_voltage_error_v"] = float(voltage_error.max()) if len(voltage_error) else 0.0
    checks["loss_energy_kwh"] = float(cumulative_loss.iloc[-1]) if len(cumulative_loss) else 0.0
    checks["message"] = "控制模块校验通过" if ok else "控制模块校验发现异常，请检查CSV数值范围"
    return checks
