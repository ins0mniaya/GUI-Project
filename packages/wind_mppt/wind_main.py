# 构造风速/空气密度输入序列；
# 调用 WindPowerSystemSimulator 完成风力发电系统动态仿真；
# 导出 CSV 汇总数据和时序数据；
# 调用 wind_plotter.py 生成主结果图片，并返回 GUI 进度。
from __future__ import annotations
import os
from pathlib import Path

import numpy as np

from .wind_driver import (
    WindPowerSystemSimulator,
    export_parameter_table,
    export_summary,
    export_timeseries,
    summarize_results,
)
from .wind_parameter import (
    WindTurbineParams,
)
from .wind_speed_density_extract import get_predicted_wind_future_frame
from .wind_plotter import (
    HAS_MATPLOTLIB,
    get_scenario_validation_plot_paths,
    plot_mppt_tracking,
    plot_power_output,
    plot_system_performance_summary,
)

# 默认控制器步长
DEFAULT_CONTROLLER_DT = 5e-3
DEFAULT_PREDICTION_INTERP_DT = 1.0
DEFAULT_DC_BUS_VOLTAGE = 370.0

RESULTS_DIR = Path(__file__).resolve().parent / "rescults"

def _default_plot_paths() -> list[str]:
    names = [
        "wind_mppt_tracking.png",
        "wind_power_output.png",
        "wind_system_performance_summary.png",
    ]
    return [str(RESULTS_DIR / name) for name in names]


# 返回典型工况验证图片的默认输出路径
def _default_scenario_validation_paths() -> list[str]:
    return get_scenario_validation_plot_paths(RESULTS_DIR)


# 读取滚动预测区间的起始值
def _rolling_start_value(row, column: str, fallback_column: str) -> float:
    if column in row.index and np.isfinite(float(row[column])):
        return float(row[column])
    return float(row[fallback_column])

# 根据预测数据时间戳推断相邻天气预测点的时间间隔，默认 15 分钟
def infer_wind_step_seconds(frame) -> float:
    if len(frame) >= 2:
        diffs = frame["timestamp"].diff().dt.total_seconds().to_numpy(dtype=float)
        valid = diffs[np.isfinite(diffs) & (diffs > 0.0)]
        if len(valid) > 0:
            return float(np.median(valid))
    return 15.0 * 60.0


def _second_level_series(start_value: float, end_value: float, source_step: float, min_value: float) -> tuple[np.ndarray, np.ndarray]:
    """把相邻风资源预测点按 1 s 粒度线性插值，控制器小步长运行时读取秒级输入。"""
    interp_dt = max(float(DEFAULT_PREDICTION_INTERP_DT), 1e-9)
    source_step = max(float(source_step), interp_dt)
    times = np.arange(0.0, source_step + interp_dt * 0.5, interp_dt, dtype=float)
    times = times[times <= source_step + 1e-9]
    if len(times) == 0 or times[-1] < source_step - 1e-9:
        times = np.append(times, source_step)
    fraction = np.clip(times / max(source_step, 1e-9), 0.0, 1.0)
    values = float(start_value) + (float(end_value) - float(start_value)) * fraction
    return times, np.maximum(values, float(min_value))

# 在 15 分钟均值风速上叠加低频合成湍流
def apply_synthetic_turbulence(
    mean_wind_speed: np.ndarray,
    controller_dt: float,
    turbine_params: WindTurbineParams,
) -> np.ndarray:
    mean = np.asarray(mean_wind_speed, dtype=float)
    if len(mean) == 0:
        return mean.copy()

    intensity = max(float(getattr(turbine_params, "turbulence_intensity", 0.03)), 0.0)
    if intensity <= 0.0:
        return np.maximum(mean, 0.0)

    controller_dt = max(float(controller_dt), 1e-9)
    tau = max(float(getattr(turbine_params, "turbulence_time_constant", 20.0)), controller_dt, 1e-6)
    limit_ratio = max(float(getattr(turbine_params, "turbulence_max_deviation_ratio", 0.12)), 0.0)
    coarse_dt = max(controller_dt, min(0.1, tau / 20.0))
    stride = max(1, int(round(coarse_dt / controller_dt)))
    coarse_indices = np.arange(0, len(mean), stride, dtype=int)
    if coarse_indices[-1] != len(mean) - 1:
        coarse_indices = np.append(coarse_indices, len(mean) - 1)
    coarse_mean = mean[coarse_indices]
    effective_dt = controller_dt * stride
    phi = float(np.exp(-effective_dt / tau))
    innovation_scale = float(np.sqrt(max(1.0 - phi * phi, 0.0)))
    rng = np.random.default_rng(int(getattr(turbine_params, "turbulence_seed", 202405)))
    fluctuation = 0.0
    coarse_result = np.empty_like(coarse_mean)

    # 使用一阶相关随机过程生成低频湍流
    for idx, base in enumerate(coarse_mean):
        base = max(float(base), 0.0)
        sigma = intensity * base
        fluctuation = phi * fluctuation + innovation_scale * sigma * float(rng.normal())
        max_delta = limit_ratio * max(base, 0.1)
        limited = float(np.clip(fluctuation, -max_delta, max_delta))
        coarse_result[idx] = max(base + limited, 0.0)

    if stride == 1:
        return coarse_result
    return np.interp(np.arange(len(mean), dtype=float), coarse_indices.astype(float), coarse_result)

# 读取滚动预测风速/密度，并插值到控制器步长，构造仿真输入
def build_rolling_prediction_input(
    controller_dt: float,
    max_weather_steps: int | None,
    forecast_days: float,
    data_path: str | None = None,
    turbine_params: WindTurbineParams | None = None,
    rolling_start_offset: int = 0,
) -> dict:
    turbine = turbine_params or WindTurbineParams()
    future_frame = get_predicted_wind_future_frame(
        data_path=data_path,
        max_steps=max_weather_steps,
        forecast_days=forecast_days,
        start_offset=rolling_start_offset,
    )
    visual_start_offset = 0
    # 原始气象预测点先按 1 s 插值；控制器小步长运行时读取对应秒级输入
    source_step = infer_wind_step_seconds(future_frame)
    local_times = np.arange(0.0, max(source_step, controller_dt), controller_dt, dtype=float)
    if len(local_times) == 0:
        local_times = np.array([0.0], dtype=float)

    time_parts = []
    physical_parts = []
    wind_parts = []
    density_parts = []
    
    previous_pred_wind = None
    previous_pred_density = None
    
    # 对每一个预测区间做线性插值：区间起点为上一预测值，终点为当前预测值
    for weather_step, (_, row) in enumerate(future_frame.iterrows()):
        if previous_pred_wind is not None:
            start_wind = previous_pred_wind
        else:
            start_wind = max(_rolling_start_value(row, "start_wind_speed", "pred_wind_speed"), 0.0)
             
        if previous_pred_density is not None:
            start_density = previous_pred_density
        else:
            start_density = max(_rolling_start_value(row, "start_air_density", "pred_air_density"), 0.3)
             
        pred_wind = max(float(row["pred_wind_speed"]), 0.0)
        pred_density = max(float(row["pred_air_density"]), 0.3)
         
        previous_pred_wind = pred_wind
        previous_pred_density = pred_density

        base_time = weather_step * source_step
        interp_times, interp_wind = _second_level_series(start_wind, pred_wind, source_step, 0.0)
        _, interp_density = _second_level_series(start_density, pred_density, source_step, 0.3)
        interp_indices = np.minimum(
            np.floor(local_times / max(DEFAULT_PREDICTION_INTERP_DT, 1e-9)).astype(int),
            len(interp_times) - 1,
        )
        time_parts.append(base_time + local_times)
        physical_parts.append(base_time + local_times)
        # 风速不允许小于 0，空气密度不允许低于 0.3 kg/m3；预测基准值按秒级插值后供控制小步长读取
        wind_parts.append(interp_wind[interp_indices])
        density_parts.append(interp_density[interp_indices])

    if time_parts:
        time = np.concatenate(time_parts)
        physical_time = np.concatenate(physical_parts)
        wind_speed_mean = np.concatenate(wind_parts)
        wind_speed = apply_synthetic_turbulence(wind_speed_mean, controller_dt, turbine)
        air_density = np.concatenate(density_parts)
    else:
        time = np.array([], dtype=float)
        physical_time = np.array([], dtype=float)
        wind_speed = np.array([], dtype=float)
        air_density = np.array([], dtype=float)

    return {
        "time": time,
        "physical_time": physical_time,
        "source_step_seconds": source_step,
        "physical_dt": controller_dt,
        "wind_speed": wind_speed,
        "air_density": air_density,
        "future_frame": future_frame,
        "visual_start_offset": int(visual_start_offset),
    }

# 截取从仿真开始到当前步的局部结果，用于实时刷新
def _partial_results(results: dict, end_index: int) -> dict:
    stop = max(1, int(end_index) + 1)
    partial = {}
    for key, value in results.items():
        if isinstance(value, np.ndarray) and len(value) >= stop:
            partial[key] = value[:stop].copy()
        else:
            partial[key] = value
    if "physical_time" in partial:
        partial["physical_time_days"] = partial["physical_time"] / (24.0 * 3600.0)
    return partial

def _windowed_partial_results(
    results: dict,
    end_index: int,
    physical_time: np.ndarray,
    source_step_seconds: float,
) -> dict:
    partial = _partial_results(results, end_index)
    stop = len(partial.get("time", []))
    physical = np.asarray(physical_time[:stop], dtype=float)
    if stop == 0 or len(physical) == 0:
        return partial

    source_step = max(float(source_step_seconds), 1e-9)
    current_physical = float(physical[-1])
    window_index = int(np.floor(current_physical / source_step))
    window_start = float(window_index * source_step)
    mask = physical >= (window_start - 1e-9)

    windowed = {}
    for key, value in partial.items():
        if isinstance(value, np.ndarray) and len(value) == len(physical):
            windowed[key] = value[mask].copy()
        else:
            windowed[key] = value

    window_physical = physical[mask].copy()
    windowed["physical_time"] = window_physical
    windowed["physical_time_days"] = window_physical / (24.0 * 3600.0)
    windowed["display_time_minutes"] = (window_physical - window_start) / 60.0
    windowed["display_window_minutes"] = source_step / 60.0
    windowed["current_window_index"] = window_index + 1
    windowed["current_window_sample"] = int(len(window_physical))
    return windowed

# 安全调用停止回调，避免回调异常中断主流程
def _stop_requested(stop_callback) -> bool:
    if stop_callback is None:
        return False
    try:
        return bool(stop_callback())
    except Exception as exc:
        print(f"[警告] 停止回调执行失败: {exc}")
        return False

# 仿真结束或停止时，向 GUI/上层调用方发送最后一次进度信息。
def _publish_final_progress(progress_callback, summary: dict, plot_paths: list[str]) -> None:
    if progress_callback is None:
        return
    total_steps = int(summary.get("weather_points", 0))
    payload = {
        "step": total_steps,
        "total_steps": total_steps,
        "weather_points": total_steps,
        "p_out_mean_w": float(summary.get("p_out_mean_w", 0.0)),
        "p_out_max_w": float(summary.get("p_out_max_w", 0.0)),
        "wind_mean_mps": float(summary.get("wind_mean_mps", 0.0)),
        "plot_paths": plot_paths,
        "output_csv": summary.get("output_csv", str(RESULTS_DIR / "wind_mppt_timeseries.csv")),
        "stopped": bool(summary.get("stopped", False)),
    }
    try:
        progress_callback(payload)
    except Exception as exc:
        print(f"[Warning] final progress callback failed: {exc}")

# 主入口函数：组织输入构建、动态仿真、导出数据、绘制图表和工况验证
def run_simulation(
    controller_dt: float = DEFAULT_CONTROLLER_DT,
    max_weather_steps: int | None = None,
    forecast_days: float = 0.0,
    dc_bus_voltage: float = DEFAULT_DC_BUS_VOLTAGE,
    data_path: str | None = None,
    turbine_params: WindTurbineParams | None = None,
    progress_callback=None,
    plot_update_interval_seconds: float | None = 5.0,
    rolling_start_offset: int = 0,
    pause_callback=None,
    stop_callback=None,
    real_time_playback: bool = False,
    playback_speed: float = 1.0,
    generate_validation_outputs: bool = True,
) -> dict:
    print("=" * 60)
    print("Wind system simulation: 2 kW branch, 370 V DC bus")
    print("=" * 60)

    sim = WindPowerSystemSimulator(dc_bus_voltage=dc_bus_voltage, turbine_params=turbine_params)
    sim.turbine.print_model_info()

    # 构建仿真输入：把滚动预测数据转换成与控制器步长一致的时序数组
    wind_input = build_rolling_prediction_input(
        controller_dt=controller_dt,
        max_weather_steps=max_weather_steps,
        forecast_days=forecast_days,
        data_path=data_path,
        turbine_params=sim.turbine.params,
        rolling_start_offset=rolling_start_offset,
    )

    time = wind_input["time"]
    wind = wind_input["wind_speed"]
    density = wind_input["air_density"]

    print("\nSimulation setup:")
    print("  Input source: rolling prediction")
    print(f"  Duration: {float(time[-1] + controller_dt if len(time) else 0.0):.3f} s, dt: {controller_dt * 1e3:.2f} ms, steps: {len(time)}")
    print("  Wind speed: rolling 15-minute prediction + interpolation")
    print(f"  Wind mean/std: {np.mean(wind):.2f}/{np.std(wind):.2f} m/s")
    print(f"  Air density range: {np.min(density):.3f} - {np.max(density):.3f} kg/m^3")
    future_frame = wind_input["future_frame"]
    print(f"  Rolling prediction points: {len(future_frame)}")
    if forecast_days > 0:
        print(f"  Rolling horizon limit: {forecast_days:.2f} day(s)")
    else:
        print("  Rolling horizon limit: full future/test split")
    print(f"  Rolling forecast interval: {wind_input['source_step_seconds']:.3f} s per prediction point")
    print(f"  Physical timestep represented per sample: {wind_input['physical_dt']:.3f} s")
    print(f"  DC bus target: {dc_bus_voltage:.0f} V, matched to Jinko 590 W x 4-series PV simulation")

    print("\n[1/4] Running dynamic simulation...")
    def publish_partial(partial_results: dict, index: int) -> None:
        partial = _windowed_partial_results(
            partial_results,
            index,
            wind_input["physical_time"],
            wind_input["source_step_seconds"],
        )
        if _stop_requested(stop_callback):
            return
        current_physical = float(partial["physical_time"][-1]) if len(partial["physical_time"]) else 0.0
        has_visible_window = int(partial.get("current_window_sample", len(partial.get("time", [])))) > 1
        if HAS_MATPLOTLIB and current_physical > 0.0 and has_visible_window:
            if not _stop_requested(stop_callback):
                plot_mppt_tracking(partial)
            if not _stop_requested(stop_callback):
                plot_power_output(partial)
            if not _stop_requested(stop_callback):
                plot_system_performance_summary(partial)
        if _stop_requested(stop_callback):
            return
        if progress_callback is not None:
            step = int(current_physical // max(wind_input["source_step_seconds"], 1e-9)) + 1
            return progress_callback(
                {
                    "step": min(step, len(future_frame)),
                    "total_steps": len(future_frame),
                    "weather_points": min(step, len(future_frame)),
                    "current_window_index": int(partial.get("current_window_index", step)),
                    "current_window_sample": int(partial.get("current_window_sample", len(partial.get("time", [])))),
                    "current_window_minutes": float(partial["display_time_minutes"][-1]) if len(partial.get("display_time_minutes", [])) else 0.0,
                    "p_out_mean_w": float(np.mean(partial["P_out"])) if len(partial["P_out"]) else 0.0,
                    "p_out_max_w": float(np.max(partial["P_out"])) if len(partial["P_out"]) else 0.0,
                    "wind_mean_mps": float(np.mean(partial["wind_speed"])) if len(partial["wind_speed"]) else 0.0,
                    "plot_paths": _default_plot_paths(),
                    "output_csv": str(RESULTS_DIR / "wind_mppt_timeseries.csv"),
                }
            )

    results = sim.simulate(
        time,
        wind,
        controller_dt,
        density,
        progress_callback=publish_partial,
        progress_interval_seconds=plot_update_interval_seconds,
        pause_callback=pause_callback,
        stop_callback=stop_callback,
        real_time_playback=real_time_playback,
        playback_speed=playback_speed,
    )

    result_len = len(results.get("time", []))
    results["physical_time"] = wind_input["physical_time"][:result_len]
    results["physical_time_days"] = results["physical_time"] / (24.0 * 3600.0)
    results["cut_in_speed"] = float(sim.turbine.params.cut_in_speed)
    results["rated_power"] = float(sim.turbine.params.rated_power)

    # 把完整时序结果压缩成报告/GUI需要的统计摘要
    summary = summarize_results(results)
    summary["weather_points"] = int(len(future_frame))
    summary["rolling_prediction"] = True
    summary["visual_start_offset"] = int(wind_input.get("visual_start_offset", 0))
    summary["stopped"] = bool(results.get("stopped", False))
    summary["completed_steps"] = int(results.get("completed_steps", result_len))
    summary["total_steps"] = int(results.get("total_steps", len(time)))

    if summary["stopped"]:
        print("\n" + "=" * 60)
        print("Simulation stopped by user.")
        print("=" * 60)
        summary["plot_paths"] = _default_plot_paths()
        summary["scenario_validation_paths"] = _default_scenario_validation_paths() if generate_validation_outputs else []
        summary["scenario_validation_metrics"] = []
        summary["output_csv"] = str(RESULTS_DIR / "wind_mppt_timeseries.csv")
        _publish_final_progress(progress_callback, summary, summary["plot_paths"])
        return summary

    sim.print_statistics(results)

    print("\n[2/4] Exporting simulation data...")
    # 导出参数表、摘要表和完整时序数据
    export_parameter_table(sim, str(RESULTS_DIR / "wind_parameter_table.csv"))
    export_summary(summary, str(RESULTS_DIR / "wind_mppt_summary.csv"))
    export_timeseries(results, str(RESULTS_DIR / "wind_mppt_timeseries.csv"))

    print("\n[3/4] Plotting report-ready figures...")
    plot_results = results
    if real_time_playback and progress_callback is not None and result_len:
        plot_results = _windowed_partial_results(
            results,
            result_len - 1,
            wind_input["physical_time"],
            wind_input["source_step_seconds"],
        )
    # 生成主报告图片
    if not _stop_requested(stop_callback):
        plot_mppt_tracking(plot_results)
    if not _stop_requested(stop_callback):
        plot_power_output(plot_results)
    if not _stop_requested(stop_callback):
        plot_system_performance_summary(plot_results)

    if _stop_requested(stop_callback):
        summary["stopped"] = True
        summary["plot_paths"] = _default_plot_paths()
        summary["scenario_validation_paths"] = _default_scenario_validation_paths() if generate_validation_outputs else []
        summary["scenario_validation_metrics"] = []
        summary["output_csv"] = str(RESULTS_DIR / "wind_mppt_timeseries.csv")
        _publish_final_progress(progress_callback, summary, summary["plot_paths"])
        print("\nSimulation stopped by user during final plotting.")
        return summary

    scenario_validation_paths = _default_scenario_validation_paths()
    scenario_validation_metrics: list[dict[str, float | str]] = []
    should_generate_validation = (
        generate_validation_outputs
        and
        HAS_MATPLOTLIB
        and len(time) > 0
        and not _stop_requested(stop_callback)
    )
    # 生成典型工况验证图
    if should_generate_validation:
        print("\n[4/4] Running operating-condition validation...")
        from .wind_plotter import run_operating_condition_validation

        scenario_validation_paths, scenario_validation_metrics = run_operating_condition_validation(
            turbine_params=sim.turbine.params,
            controller_dt=controller_dt,
            dc_bus_voltage=dc_bus_voltage,
            output_dir=RESULTS_DIR,
        )
    elif generate_validation_outputs:
        print("\n[4/4] Operating-condition validation skipped because no completed plotting data is available.")
    else:
        scenario_validation_paths = []
        print("\n[4/4] Operating-condition validation skipped for live GUI simulation.")

    print("\n" + "=" * 60)
    print("Simulation complete.")
    print("Output files:")
    print("  rescults/wind_parameter_table.csv")
    print("  rescults/wind_mppt_summary.csv")
    print("  rescults/wind_mppt_timeseries.csv")
    print("  rescults/wind_mppt_tracking.png")
    print("  rescults/wind_power_output.png")
    print("  rescults/wind_system_performance_summary.png")
    for path in scenario_validation_paths:
        print(f"  {path}")
    print("=" * 60)
    summary["plot_paths"] = _default_plot_paths()
    summary["scenario_validation_paths"] = scenario_validation_paths
    summary["scenario_validation_metrics"] = scenario_validation_metrics
    return summary
