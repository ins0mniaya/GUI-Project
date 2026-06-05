#   1. 作为 GUI 与风电仿真主流程之间的封装层；
#   2. 统一管理默认数据路径、默认运行参数和默认风机参数；
#   3. 将界面传入的停止/暂停/进度回调转换为主仿真可识别的形式；
#   4. 返回 GUI 需要的图片路径、CSV 路径和工况验证结果字段。
from __future__ import annotations
import os
from pathlib import Path

from .wind_speed_density_extract import DEFAULT_RAW_XLSX_PATH, resolve_wind_data_path
from .wind_plotter import (
    generate_scenario_validation_plots as _generate_validation_plots,
    get_scenario_validation_plot_paths as _scenario_plot_paths,
)


PACKAGE_ROOT = Path(__file__).resolve().parent

# 结果输出目录
RESULTS_DIR = PACKAGE_ROOT / "results"

# 默认原始风场数据和默认时序输出文件
DEFAULT_DATA_PATH = DEFAULT_RAW_XLSX_PATH
DEFAULT_OUTPUT_PATH = RESULTS_DIR / "wind_mppt_timeseries.csv"

# GUI 使用的风机参数
DEFAULT_WIND_PARAMS = {
    "blade_radius": 1.35, "rated_power": 2000.0, "cut_in_speed": 2.5,
    "rated_speed": 11.0, "cut_out_speed": 25.0, "hub_height": 30.0,
    "reference_wind_height": 10.0, "shear_exponent": 0.14,
    "omega_initial": 42.0, "omega_max": 95.0,
}
# GUI 默认运行参数
DEFAULT_RUNTIME_PARAMS = {
    "forecast_days": 0.0, "rolling_batch_steps": 1, "controller_dt": 0.005,
    "dc_bus_voltage": 370.0, "plot_update_interval_seconds": 1.0,
    "real_time_playback": True, "playback_speed": 1.0,
}
# 主界面固定展示的三张核心结果图
MAIN_PLOTS = ("wind_mppt_tracking.png", "wind_power_output.png", "wind_system_performance_summary.png")

# 每次重新运行前需要清理的旧图片模式，避免 GUI 误读上一轮结果
CLEAN_PATTERNS = (
    "wind_speed_prediction_vs_true*.png", "air_density_prediction_vs_true*.png",
    "wind_mppt_tracking*.png", "wind_power_output*.png", "wind_voltage_output*.png",
    "wind_system_performance_summary*.png", "wind_condition_*.png",
    "wind_mppt_simulation_370v*.png", "wind_power_chain_validation*.png", "tmp*.png", "*.png.tmp",
)


def _paths(*names: str) -> list[str]:
    return [str(RESULTS_DIR / name) for name in names]


def resolve_default_data_path() -> Path:
    try:
        return resolve_wind_data_path(None)
    except FileNotFoundError:
        return DEFAULT_DATA_PATH

#合并 GUI 停止按钮、线程事件等不同形式的停止信号
def _merge_stop_sources(stop_callback=None, stop_event=None):
    if stop_callback is None and stop_event is None:
        return None

    def _should_stop() -> bool:
        if stop_callback is not None and bool(stop_callback()):
            return True
        if stop_event is None:
            return False
        if hasattr(stop_event, "is_set"):
            return bool(stop_event.is_set())
        return bool(stop_event() if callable(stop_event) else stop_event)

    return _should_stop


def _make_turbine_params(wind_params: dict | None = None):
    from .wind_parameter import WindTurbineParams
    return WindTurbineParams(**{**DEFAULT_WIND_PARAMS, **(wind_params or {})})


def clear_plot_outputs() -> None:
    if not RESULTS_DIR.exists():
        return
    for pattern in CLEAN_PATTERNS:
        for path in RESULTS_DIR.glob(pattern):
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass


def get_plot_paths() -> list[str]:
    return _paths(*MAIN_PLOTS)


def get_accuracy_plot_paths() -> list[str]:
    return []


def get_scenario_validation_plot_paths() -> list[str]:
    return _scenario_plot_paths(RESULTS_DIR)

#GUI 入口：把界面参数转为主仿真参数，并补齐界面需要的返回字段
def run_gui_wind_simulation(
    data_path: str | Path | None = None,
    forecast_days: float = 0.0,
    max_weather_steps: int | None = 1,
    controller_dt: float = 0.005,
    dc_bus_voltage: float = 370.0,
    wind_params: dict | None = None,
    progress_callback=None,
    plot_update_interval_seconds: float | None = 1.0,
    rolling_start_offset: int = 0,
    pause_callback=None,
    stop_callback=None,
    stop_event=None,
    real_time_playback: bool = True,
    playback_speed: float = 1.0,
) -> dict:
    os.environ["WIND_SKIP_PLOTS"] = "0"
    from .wind_main import run_simulation

    data_file = Path(data_path) if data_path else resolve_default_data_path()
    summary = run_simulation(
        controller_dt=controller_dt,
        max_weather_steps=max_weather_steps,
        forecast_days=forecast_days,
        dc_bus_voltage=dc_bus_voltage,
        data_path=str(data_file),
        turbine_params=_make_turbine_params(wind_params),
        progress_callback=progress_callback,
        plot_update_interval_seconds=plot_update_interval_seconds,
        rolling_start_offset=rolling_start_offset,
        pause_callback=pause_callback,
        stop_callback=_merge_stop_sources(stop_callback, stop_event),
        real_time_playback=real_time_playback,
        playback_speed=playback_speed,
        generate_validation_outputs=False,
    )
    summary.update(
        data_path=str(data_file), output_csv=str(DEFAULT_OUTPUT_PATH), plot_paths=get_plot_paths(),
        accuracy_plot_paths=[],
        scenario_validation_paths=summary.get("scenario_validation_paths", get_scenario_validation_plot_paths()),
        scenario_validation_metrics=summary.get("scenario_validation_metrics", []),
    )
    summary.pop("control_validation", None)
    return summary


def generate_scenario_validation_plots(
    *, controller_dt: float = 0.005, dc_bus_voltage: float = 370.0, wind_params: dict | None = None
) -> dict:
    return _generate_validation_plots(
        controller_dt=controller_dt,
        dc_bus_voltage=dc_bus_voltage,
        turbine_params=_make_turbine_params(wind_params),
        output_dir=RESULTS_DIR,
    )


def validate_control_output(output_csv: str | Path) -> dict:
    import pandas as pd

    path = Path(output_csv)
    if not path.exists():
        return {"ok": False, "message": "仿真输出CSV不存在"}
    cols = ["P_out", "boost_efficiency", "V_out", "I_L", "duty", "omega"]
    df = pd.read_csv(path)
    missing = [col for col in cols if col not in df.columns]
    if missing:
        return {"ok": False, "message": f"缺少字段: {', '.join(missing)}"}
    data = df[cols].apply(pd.to_numeric, errors="coerce")
    checks = {
        "samples": int(len(df)),
        "has_nan": bool(data.isna().any().any()),
        "power_nonnegative": bool((data["P_out"] >= -1e-6).all()),
        "efficiency_range": bool(data["boost_efficiency"].between(-1e-6, 1.0001).all()),
        "duty_range": bool(data["duty"].between(-1e-6, 0.9701).all()),
        "voltage_nonnegative": bool((data["V_out"] >= -1e-6).all()),
        "rotor_speed_nonnegative": bool((data["omega"] >= -1e-6).all()),
    }
    checks["ok"] = not checks["has_nan"] and all(v for k, v in checks.items() if k not in {"samples", "has_nan"})
    checks["message"] = "风力控制模块校验通过" if checks["ok"] else "风力控制模块校验发现异常，请检查CSV数值范围"
    return checks
