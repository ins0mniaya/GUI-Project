# 本文件负责把界面参数转换为飞轮仿真参数，并把 CSV、图像路径和校验结果返回给界面
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

PACKAGE_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = PACKAGE_ROOT / "results"
DEFAULT_OUTPUT_PATH = RESULTS_DIR / "flywheel_output.csv"

# GUI 默认飞轮参数；界面传入的参数会覆盖这里的默认值
DEFAULT_FLYWHEEL_PARAMS = {
    "rated_power_w": 5000.0,
    "rated_usable_energy_kwh": 1.0,
    "inertia_kg_m2": 2.5,
    "omega_min_rad_s": 600.0,
    "omega_max_rad_s": 1800.0,
    "omega_init_rad_s": 1300.0,
    "charge_efficiency": 0.94,
    "discharge_efficiency": 0.94,
    "converter_efficiency": 0.97,
    "standby_loss_w": 50.0,
    "viscous_loss_coeff": 1.0e-5,
    "windage_loss_coeff": 1.0e-8,
    "max_torque_nm": 8.5,
    "motor_torque_constant_nm_per_a": 0.20,
    "max_motor_current_a": 80.0,
    "dc_bus_nominal_v": 400.0,
    "converter_current_limit_a": 40.0,
    "dc_bus_voltage_init_v": 400.0,
    "dc_bus_voltage_min_v": 300.0,
    "dc_bus_voltage_max_v": 480.0,
    "dc_bus_capacitance_f": 2.0,
    "dc_bus_kp_w_per_v": 200.0,
    "dc_bus_ki_w_per_vs": 3.0,
    "power_rate_limit_w_s": 3000.0,
    "min_soc": 0.05,
    "max_soc": 0.95,
}

# 默认运行参数，用于界面没有传参时启动标准现场扰动工况
DEFAULT_RUNTIME_PARAMS = {
    "scenario": "field",
    "duration": 900.0,
    "controller_dt": 0.1,
    "response_tau_s": 1.0,
}

# 工况英文标识与中文显示名称的对应关系
SCENARIO_LABELS = {
    "step": "阶跃功率",
    "cycle": "周期波动",
    "random": "随机波动",
    "pulse": "脉冲功率",
    "dc_bus": "DC母线稳压",
    "field": "现场扰动",
}


def _result_paths(*names: str) -> list[str]:
    return [str(RESULTS_DIR / name) for name in names]


def clear_plot_outputs() -> None:
    # 清理上一轮飞轮仿真遗留的图片文件
    if not RESULTS_DIR.exists():
        return
    for pattern in ["flywheel_power_tracking*.png", "flywheel_soc*.png", "flywheel_speed*.png", "flywheel_energy_loss*.png", "flywheel_torque_limit*.png", "flywheel_final_performance_summary*.png", "flywheel_rolling_*.png", "*.png.tmp"]:
        for path in RESULTS_DIR.glob(pattern):
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass


def run_gui_flywheel_simulation(
    scenario: str = "field",
    duration: float = 900.0,
    controller_dt: float = 0.1,
    response_tau_s: float = 1.0,
    flywheel_params: dict | None = None,
    output_csv: str | Path | None = None,
    progress_callback=None,
    plot_update_interval_seconds: float | None = 1.0,
    pause_callback=None,
    stop_callback=None,
    real_time_playback: bool = True,
    playback_speed: float = 1.0,
) -> dict:

    os.environ["FLYWHEEL_SKIP_PLOTS"] = "0"
    from .flywheel_main import run_flywheel_simulation
    from .flywheel_params import FlywheelParams
    # 将界面传入参数覆盖默认参数，保证没有设置的参数仍有合理默认值
    merged_params = {**DEFAULT_FLYWHEEL_PARAMS, **(flywheel_params or {})}
    params = FlywheelParams(**merged_params)
    resolved_output_csv = Path(output_csv) if output_csv else DEFAULT_OUTPUT_PATH
    resolved_output_csv.parent.mkdir(parents=True, exist_ok=True)
    summary = run_flywheel_simulation(
        scenario=scenario,
        duration=duration,
        controller_dt=controller_dt,
        output_csv=str(resolved_output_csv),
        response_tau_s=response_tau_s,
        params=params,
        progress_callback=progress_callback,
        plot_update_interval_seconds=plot_update_interval_seconds,
        pause_callback=pause_callback,
        stop_callback=stop_callback,
        real_time_playback=real_time_playback,
        playback_speed=playback_speed,
    )
    summary["scenario"] = scenario
    summary["scenario_label"] = SCENARIO_LABELS.get(scenario, scenario)
    summary["output_csv"] = str(resolved_output_csv)
    summary["plot_paths"] = get_plot_paths()
    # 仿真完成后读取 CSV 做基本校验
    summary["control_validation"] = validate_control_output(resolved_output_csv, params)
    return summary


def get_plot_paths() -> list[str]:
    # 返回主仿真结果图路径
    return _result_paths(
        "flywheel_power_tracking.png",
        "flywheel_speed.png",
        "flywheel_final_performance_summary.png",
    )


def validate_control_output(output_csv: str | Path, params=None) -> dict:
    # 检查飞轮控制输出是否满足基本物理约束
    path = Path(output_csv)
    if not path.exists():
        return {"ok": False, "message": "仿真输出CSV不存在"}
    df = pd.read_csv(path)
    # 校验这些关键字段是否存在，并检查数值范围是否符合基本物理约束
    required_cols = ["P_ref", "P_fw", "P_loss_total", "rpm", "soc", "torque", "motor_current_limit_w", "converter_current_limit_w", "dc_bus_voltage_v"]
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        return {"ok": False, "message": f"缺少字段: {', '.join(missing)}"}
    numeric = df[required_cols].apply(pd.to_numeric, errors="coerce")
    rated_power = float(getattr(params, "rated_power_w", DEFAULT_FLYWHEEL_PARAMS["rated_power_w"]))
    checks = {
        "samples": int(len(df)),
        "has_nan": bool(numeric.isna().any().any()),
        "soc_range": bool(((numeric["soc"] >= -1e-6) & (numeric["soc"] <= 1.0001)).all()),
        "speed_nonnegative": bool((numeric["rpm"] >= -1e-6).all()),
        "loss_nonnegative": bool((numeric["P_loss_total"] >= -1e-6).all()),
        "power_range": bool((numeric["P_fw"].abs() <= rated_power * 1.0001).all()),
    }
    ok = all(value for key, value in checks.items() if key not in ("samples", "has_nan")) and not checks["has_nan"]
    checks["ok"] = bool(ok)
    checks["message"] = "飞轮控制模块校验通过" if ok else "飞轮控制模块校验发现异常，请检查CSV数值范围"
    return checks
