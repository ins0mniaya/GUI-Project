# 统一管理光伏主仿真图绘制；
# 统一管理补充验证图和 5 组典型工况验证图；
from __future__ import annotations
import os
import tempfile
import time as wall_clock
from pathlib import Path

import numpy as np

from .pv_module_params import PVModuleParams


PACKAGE_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = PACKAGE_ROOT / "results"

#默认绘图/仿真参数
DEFAULT_DC_BUS_VOLTAGE = 370.0
DEFAULT_CONTROLLER_DT = 0.1
DEFAULT_MPPT_ENABLE_IRRADIANCE = 20.0
DEFAULT_PV_VARIABILITY_SEED = 202406

MAIN_PLOT_FILENAMES = (
    "pv_mppt_tracking.png",
    "pv_electrical_power_output.png",
    "pv_final_performance_summary.png",
)

SUPPLEMENTAL_VALIDATION_FILENAMES = (
    "pv_weather_temperature_validation.png",
    "pv_voltage_tracking_validation.png",
    "pv_duty_efficiency_validation.png",
    "pv_energy_validation.png",
)

SCENARIO_VALIDATION_FILENAMES = (
    "pv_condition_sunny.png",
    "pv_condition_cloudy.png",
    "pv_condition_fast_cloud.png",
    "pv_condition_low_irradiance.png",
    "pv_condition_temperature_step.png",
)


# 允许通过环境变量 PV_SKIP_PLOTS=1 跳过绘图，适合无图形依赖或只想快速仿真的场景
if os.environ.get("PV_SKIP_PLOTS", "0") == "1":
    HAS_MATPLOTLIB = False
    plt = None
else:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # 设置中文字体和基础样式
        plt.rcParams["font.sans-serif"] = [
            "WenQuanYi Micro Hei",
            "WenQuanYi Zen Hei",
            "Droid Sans Fallback",
            "Microsoft YaHei",
            "SimHei",
            "sans-serif",
        ]
        plt.rcParams["axes.unicode_minus"] = False
        plt.rcParams["lines.linewidth"] = 0.95
        plt.rcParams["axes.titlesize"] = 11.2
        plt.rcParams["axes.labelsize"] = 9.6
        plt.rcParams["xtick.labelsize"] = 8.8
        plt.rcParams["ytick.labelsize"] = 8.8
        HAS_MATPLOTLIB = True
    except ImportError:
        HAS_MATPLOTLIB = False
        plt = None
        print("[警告] 未安装 matplotlib，光伏仿真仍可运行，但会跳过图片生成。")


def _result_paths(*names: str, output_dir: str | Path | None = None) -> list[str]:
    plot_dir = Path(output_dir) if output_dir else RESULTS_DIR
    return [str(plot_dir / name) for name in names]


def get_plot_paths(output_dir: str | Path | None = None) -> list[str]:
    return _result_paths(*MAIN_PLOT_FILENAMES, output_dir=output_dir)


def get_validation_plot_paths(output_dir: str | Path | None = None) -> list[str]:
    return _result_paths(*SUPPLEMENTAL_VALIDATION_FILENAMES, output_dir=output_dir)


def get_scenario_validation_plot_paths(output_dir: str | Path | None = None) -> list[str]:
    return _result_paths(*SCENARIO_VALIDATION_FILENAMES, output_dir=output_dir)


def cleanup_previous_plot_outputs(output_dir: str | Path | None = None) -> None:
    #清理上一轮光伏仿真遗留的图片
    plot_dir = Path(output_dir) if output_dir else RESULTS_DIR
    plot_dir.mkdir(parents=True, exist_ok=True)
    patterns = (
        "pv_mppt_tracking*.png",
        "pv_electrical_power_output*.png",
        "boost_output_voltage*.png",
        "pv_final_performance_summary*.png",
        "pv_voltage_tracking*.png",
        "pv_weather_temperature_validation*.png",
        "pv_voltage_tracking_validation*.png",
        "pv_duty_efficiency_validation*.png",
        "pv_energy_validation*.png",
        "pv_condition_*.png",
        ".pv_*.png",
        ".boost_*.png",
        "*.png.tmp",
    )
    for pattern in patterns:
        for path in plot_dir.glob(pattern):
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass


def generate_scenario_validation_plots(
    *,
    params: PVModuleParams,
    controller_dt: float = DEFAULT_CONTROLLER_DT,
    dc_bus_voltage: float = DEFAULT_DC_BUS_VOLTAGE,
    mppt_enable_irradiance: float = DEFAULT_MPPT_ENABLE_IRRADIANCE,
    output_dir: str | Path | None = None,
) -> dict:
    #单独生成光伏典型工况验证图片，并返回图片路径和指标摘要
    paths, metrics = run_operating_condition_validation(
        params=params,
        controller_dt=controller_dt,
        dc_bus_voltage=dc_bus_voltage,
        mppt_enable_irradiance=mppt_enable_irradiance,
        output_dir=output_dir,
    )
    return {"scenario_validation_paths": paths, "scenario_validation_metrics": metrics}


def _time_axis(time: np.ndarray, physical_time: np.ndarray | None = None) -> tuple[np.ndarray, str]:
    axis = np.asarray(physical_time if physical_time is not None else time, dtype=float) / 60.0
    return axis, "当前15分钟窗口 [分钟]"

def moving_average(values: np.ndarray, time_seconds: np.ndarray, window_seconds: float = 1.0) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) < 3 or window_seconds <= 0.0:
        return values.copy()
    dt = float(np.median(np.diff(np.asarray(time_seconds, dtype=float)))) if len(time_seconds) > 1 else window_seconds
    window = max(1, int(round(window_seconds / max(dt, 1e-9))))
    if window <= 1:
        return values.copy()
    kernel = np.ones(window, dtype=float) / float(window)
    padded = np.pad(values, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")

def plot_stride(time: np.ndarray, max_points: int = 6000) -> slice:
    return slice(None, None, max(1, int(np.ceil(len(time) / max(max_points, 1)))))

def _style_axes(ax, title: str, xlabel: str, ylabel: str, add_legend: bool = True) -> None:
    ax.set_title(title, fontsize=11.2, fontweight="semibold", color="#0f172a", pad=8)
    ax.set_xlabel(xlabel, fontsize=9.6, color="#334155", labelpad=4)
    ax.set_ylabel(ylabel, fontsize=9.6, color="#334155", labelpad=4)
    ax.grid(True, color="#cbd5e1", alpha=0.35, linewidth=0.55)
    ax.tick_params(axis="both", labelsize=8.8, colors="#475569", width=0.6, length=3.0)
    for spine in ax.spines.values():
        spine.set_color("#cbd5e1")
        spine.set_linewidth(0.75)
    if add_legend:
        legend = ax.legend(loc="best", fontsize=8.6, frameon=True)
        legend.get_frame().set_facecolor("#ffffff")
        legend.get_frame().set_edgecolor("#e2e8f0")
        legend.get_frame().set_alpha(0.92)

def _set_series_ylim(ax, series: list[np.ndarray]) -> None:
    parts = [np.asarray(item, dtype=float) for item in series if len(item)]
    valid = [item[np.isfinite(item)] for item in parts if np.any(np.isfinite(item))]
    if not valid:
        return
    merged = np.concatenate(valid)
    y_min = float(np.min(merged))
    y_max = float(np.max(merged))
    span = max(y_max - y_min, max(abs(y_max), 1.0) * 0.01)
    ax.set_ylim(y_min - span * 0.35, y_max + span * 0.35)

def _new_figure():
    from matplotlib.figure import Figure

    fig = Figure(figsize=(7.2, 3.6), facecolor="#f8fafc")
    ax = fig.subplots()
    ax.set_facecolor("#ffffff")
    return fig, ax

def _add_stat_box(ax, text: str) -> None:
    ax.text(
        0.018,
        0.02,
        text,
        transform=ax.transAxes,
        va="bottom",
        ha="left",
        fontsize=8.2,
        color="#334155",
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "#f8fafc", "edgecolor": "#e2e8f0", "alpha": 0.95},
    )

# 该评分用于电压跟踪、功率跟踪等验证图
def _per_second_gap_score(
    reference: np.ndarray,
    actual: np.ndarray,
    time_seconds: np.ndarray,
    valid_mask: np.ndarray,
    min_reference: float = 1.0,
) -> float:

    reference = np.asarray(reference, dtype=float)
    actual = np.asarray(actual, dtype=float)
    time_seconds = np.asarray(time_seconds, dtype=float)
    valid_mask = np.asarray(valid_mask, dtype=bool)
    valid_mask = (
        valid_mask
        & np.isfinite(reference)
        & np.isfinite(actual)
        & np.isfinite(time_seconds)
        & (reference > float(min_reference))
    )
    if not np.any(valid_mask):
        return 0.0

    seconds = np.floor(time_seconds[valid_mask]).astype(int)
    ref_valid = reference[valid_mask]
    act_valid = actual[valid_mask]
    errors: list[float] = []
    for sec in np.unique(seconds):
        sec_mask = seconds == sec
        ref_mean = float(np.mean(ref_valid[sec_mask]))
        if ref_mean <= float(min_reference) or not np.isfinite(ref_mean):
            continue
        act_mean = float(np.mean(act_valid[sec_mask]))
        errors.append(abs(act_mean - ref_mean) / max(abs(ref_mean), float(min_reference)))
    if not errors:
        return 0.0
    score = (1.0 - float(np.mean(errors))) * 100.0
    return float(np.clip(score, 0.0, 100.0))

# 给柱状图补充顶部标签
def _annotate_bars(ax, bars, values: list[float], suffix: str, decimals: int = 1) -> None:
    y0, y1 = ax.get_ylim()
    offset = max((y1 - y0) * 0.018, 1e-6)
    for bar, value in zip(bars, values):
        if suffix == "%":
            label = f"{value:.{decimals}f}%"
        elif suffix == "W":
            label = f"{value:.0f} W"
        else:
            label = f"{value:.{decimals}f}{suffix}"
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + offset,
            label,
            ha="center",
            va="bottom",
            fontsize=8.2,
            color="#334155",
        )

def _save_figure(fig, path: Path, *, adjust_kwargs: dict | None = None, dpi: int = 100) -> str:
    if adjust_kwargs:
        fig.subplots_adjust(**adjust_kwargs)
    path = _atomic_save_figure(fig, path, dpi=dpi)
    plt.close(fig)
    return str(path)

def _apply_display_window_xlim(ax, display_window_minutes: float | None) -> None:
    if display_window_minutes is None:
        return
    try:
        window_minutes = float(display_window_minutes)
    except (TypeError, ValueError):
        return
    if np.isfinite(window_minutes) and window_minutes > 0.0:
        ax.set_xlim(0.0, window_minutes)

def _style_secondary_axis(ax) -> None:
    ax.tick_params(axis="both", labelsize=8.6, colors="#64748b", width=0.55, length=2.8)
    for spine in ax.spines.values():
        spine.set_color("#cbd5e1")
        spine.set_linewidth(0.7)

def _tracking_efficiency_percent(
    actual_power: np.ndarray,
    reference_power: np.ndarray,
    *,
    min_reference_w: float = 1.0,
    clip_max: float = 1.2,
) -> tuple[np.ndarray, np.ndarray]:
    actual = np.asarray(actual_power, dtype=float)
    reference = np.asarray(reference_power, dtype=float)
    valid = np.isfinite(actual) & np.isfinite(reference) & (reference > float(min_reference_w))
    efficiency = np.zeros_like(reference, dtype=float)
    np.divide(actual, reference, out=efficiency, where=valid)
    efficiency = np.clip(efficiency, 0.0, clip_max) * 100.0
    return efficiency, valid

def _mean_percent(values: np.ndarray, valid_mask: np.ndarray) -> float:
    mask = np.asarray(valid_mask, dtype=bool) & np.isfinite(values)
    if not np.any(mask):
        return 0.0
    return float(np.mean(np.asarray(values, dtype=float)[mask]))

def plot_validation_results(
    arrays: dict[str, np.ndarray],
    sim: dict[str, np.ndarray],
    time: np.ndarray,
    *,
    output_dir: str | Path | None = None,
    physical_time: np.ndarray | None = None,
) -> list[str]:
    if not HAS_MATPLOTLIB or len(time) == 0:
        return []

    from matplotlib.figure import Figure

    plot_dir = Path(output_dir) if output_dir else RESULTS_DIR
    plot_dir.mkdir(parents=True, exist_ok=True)
    t, xlabel = _time_axis(np.asarray(time, dtype=float), physical_time)
    sl = plot_stride(t)
    time_seconds = np.asarray(physical_time if physical_time is not None else time, dtype=float)
    display_window_minutes = float(t[-1]) if len(t) else None

    irradiance = moving_average(sim["G"], time_seconds, 1.0)
    ambient = moving_average(sim["T_ambient"], time_seconds, 1.5)
    cell_temp = moving_average(sim["T_cell"], time_seconds, 1.5)
    v_ref = moving_average(sim["V_ref"], time_seconds, 0.8)
    v_pv = moving_average(sim["V_pv"], time_seconds, 0.8)
    v_error = v_pv - v_ref
    duty_pct = moving_average(sim["duty"], time_seconds, 0.8) * 100.0
    boost_eff_pct = moving_average(np.clip(sim["boost_efficiency"], 0.0, 1.0), time_seconds, 0.8) * 100.0
    mppt_eff_pct, mppt_valid = _tracking_efficiency_percent(sim["P_pv"], sim["P_mpp"], min_reference_w=50.0)
    mppt_eff_pct = moving_average(mppt_eff_pct, time_seconds, 0.8)

    array_energy = np.cumsum(np.asarray(arrays["array_energy_kwh"], dtype=float))
    boost_energy = np.cumsum(np.asarray(arrays["boost_energy_kwh"], dtype=float))
    loss_energy = np.maximum(array_energy - boost_energy, 0.0)

    paths: list[str] = []

    fig = Figure(figsize=(7.2, 4.6), facecolor="#f8fafc")
    ax_g, ax_t = fig.subplots(2, 1, sharex=True)
    ax_g.set_facecolor("#ffffff")
    ax_t.set_facecolor("#ffffff")
    ax_g.plot(t[sl], irradiance[sl], color="#f59e0b", linewidth=0.95, label="预测辐照度")
    _style_axes(ax_g, "预测气象输入", "", "辐照度 [W/m2]")
    _apply_display_window_xlim(ax_g, display_window_minutes)
    ax_t.plot(t[sl], ambient[sl], color="#2563eb", linewidth=0.9, label="环境温度")
    ax_t.plot(t[sl], cell_temp[sl], color="#dc2626", linewidth=0.95, label="电池片温度")
    _style_axes(ax_t, "环境温度与电池片温度响应", xlabel, "温度 [degC]")
    _apply_display_window_xlim(ax_t, display_window_minutes)
    paths.append(
        _save_figure(
            fig,
            plot_dir / SUPPLEMENTAL_VALIDATION_FILENAMES[0],
            adjust_kwargs={"left": 0.10, "right": 0.97, "top": 0.92, "bottom": 0.12, "hspace": 0.34},
        )
    )

    fig = Figure(figsize=(7.2, 4.6), facecolor="#f8fafc")
    ax_v, ax_e = fig.subplots(2, 1, sharex=True)
    ax_v.set_facecolor("#ffffff")
    ax_e.set_facecolor("#ffffff")
    ax_v.plot(t[sl], v_ref[sl], "--", color="#f59e0b", linewidth=0.9, alpha=0.92, label="MPPT参考电压")
    ax_v.plot(t[sl], v_pv[sl], color="#2563eb", linewidth=0.98, label="阵列端电压")
    _style_axes(ax_v, "MPPT参考电压与阵列端电压", "", "电压 [V]")
    _apply_display_window_xlim(ax_v, display_window_minutes)
    ax_e.plot(t[sl], v_error[sl], color="#dc2626", linewidth=0.92, label="Vpv - Vref")
    ax_e.axhline(0.0, color="#94a3b8", linewidth=0.75, linestyle=":")
    _style_axes(ax_e, "端电压跟踪误差", xlabel, "误差 [V]")
    _add_stat_box(ax_e, f"平均绝对误差 {np.mean(np.abs(v_error)):.2f} V\n最大绝对误差 {np.max(np.abs(v_error)):.2f} V")
    _set_series_ylim(ax_e, [v_error, np.array([0.0])])
    _apply_display_window_xlim(ax_e, display_window_minutes)
    paths.append(
        _save_figure(
            fig,
            plot_dir / SUPPLEMENTAL_VALIDATION_FILENAMES[1],
            adjust_kwargs={"left": 0.10, "right": 0.97, "top": 0.92, "bottom": 0.12, "hspace": 0.34},
        )
    )

    fig = Figure(figsize=(7.2, 4.6), facecolor="#f8fafc")
    ax_d, ax_eta = fig.subplots(2, 1, sharex=True)
    ax_d.set_facecolor("#ffffff")
    ax_eta.set_facecolor("#ffffff")
    ax_d.plot(t[sl], duty_pct[sl], color="#7c3aed", linewidth=0.92, label="Boost占空比")
    _style_axes(ax_d, "Boost占空比变化", "", "占空比 [%]")
    _apply_display_window_xlim(ax_d, display_window_minutes)
    ax_eta.plot(t[sl], boost_eff_pct[sl], color="#16a34a", linewidth=0.95, label="Boost效率")
    ax_eta.plot(t[sl], mppt_eff_pct[sl], color="#2563eb", linewidth=0.88, linestyle="--", alpha=0.9, label="MPPT跟踪效率")
    _style_axes(ax_eta, "变换效率与MPPT效率", xlabel, "效率 [%]")
    _add_stat_box(
        ax_eta,
        f"平均Boost效率 {_mean_percent(boost_eff_pct, boost_eff_pct > 0.0):.2f}%\n平均MPPT效率 {_mean_percent(mppt_eff_pct, mppt_valid):.2f}%",
    )
    _set_series_ylim(ax_eta, [boost_eff_pct, mppt_eff_pct])
    _apply_display_window_xlim(ax_eta, display_window_minutes)
    paths.append(
        _save_figure(
            fig,
            plot_dir / SUPPLEMENTAL_VALIDATION_FILENAMES[2],
            adjust_kwargs={"left": 0.10, "right": 0.97, "top": 0.92, "bottom": 0.12, "hspace": 0.34},
        )
    )

    fig = Figure(figsize=(7.2, 4.6), facecolor="#f8fafc")
    ax_energy, ax_loss = fig.subplots(2, 1, sharex=True)
    ax_energy.set_facecolor("#ffffff")
    ax_loss.set_facecolor("#ffffff")
    ax_energy.plot(t[sl], array_energy[sl], color="#2563eb", linewidth=0.95, label="阵列侧累计电量")
    ax_energy.plot(t[sl], boost_energy[sl], color="#16a34a", linewidth=0.95, label="Boost侧累计电量")
    _style_axes(ax_energy, "阵列侧与Boost侧累计电量", "", "电量 [kWh]")
    _apply_display_window_xlim(ax_energy, display_window_minutes)
    ax_loss.plot(t[sl], loss_energy[sl], color="#dc2626", linewidth=0.92, label="累计损耗")
    _style_axes(ax_loss, "累计损耗", xlabel, "电量 [kWh]")
    _add_stat_box(ax_loss, f"最终累计损耗 {loss_energy[-1]:.4f} kWh")
    _apply_display_window_xlim(ax_loss, display_window_minutes)
    paths.append(
        _save_figure(
            fig,
            plot_dir / SUPPLEMENTAL_VALIDATION_FILENAMES[3],
            adjust_kwargs={"left": 0.10, "right": 0.97, "top": 0.92, "bottom": 0.12, "hspace": 0.34},
        )
    )

    return paths

def _scenario_definitions(duration_s: float) -> list[dict[str, object]]:
    two_pi = 2.0 * np.pi
    return [
        {
            "key": "sunny",
            "title": "晴天工况输入与响应",
            "irradiance": lambda t: np.clip(
                792.9 + 55.0 * np.sin(two_pi * t / duration_s - 0.25) + 22.0 * np.sin(two_pi * t / 180.0),
                620.0,
                920.0,
            ),
            "temperature": lambda t: 21.5 + 2.4 * np.sin(two_pi * t / duration_s - 0.6) + 0.6 * (t / duration_s),
        },
        {
            "key": "cloudy",
            "title": "阴天工况输入与响应",
            "irradiance": lambda t: np.clip(
                354.1 + 34.0 * np.sin(two_pi * t / 280.0) + 15.0 * np.sin(two_pi * t / 110.0 + 0.8),
                220.0,
                470.0,
            ),
            "temperature": lambda t: 18.2 + 0.8 * np.sin(two_pi * t / duration_s - 0.3) + 0.35 * np.sin(two_pi * t / 220.0),
        },
        {
            "key": "fast_cloud",
            "title": "多云/快速云影工况输入与响应",
            "irradiance": lambda t: np.clip(
                693.0
                + 38.0 * np.sin(two_pi * t / 330.0)
                - 165.0 * np.exp(-((t - 260.0) / 70.0) ** 2)
                - 130.0 * np.exp(-((t - 610.0) / 75.0) ** 2)
                + 24.0 * np.sin(two_pi * t / 85.0),
                320.0,
                860.0,
            ),
            "temperature": lambda t: 22.0 + 1.0 * np.sin(two_pi * t / duration_s - 0.45) + 0.25 * np.sin(two_pi * t / 150.0),
        },
        {
            "key": "low_irradiance",
            "title": "低辐照工况输入与响应",
            "irradiance": lambda t: np.clip(
                119.9 + 13.0 * np.sin(two_pi * t / 240.0) + 6.5 * np.sin(two_pi * t / 78.0),
                60.0,
                185.0,
            ),
            "temperature": lambda t: 16.4 + 0.75 * np.sin(two_pi * t / duration_s - 0.2),
        },
        {
            "key": "temperature_step",
            "title": "温度突变工况输入与响应",
            "irradiance": lambda t: np.clip(
                686.1 + 22.0 * np.sin(two_pi * t / 290.0) + 10.0 * np.sin(two_pi * t / 95.0),
                560.0,
                820.0,
            ),
            "temperature": lambda t: 18.0 + 10.5 / (1.0 + np.exp(-(t - duration_s * 0.52) / 38.0)) + 0.35 * np.sin(two_pi * t / 160.0),
        },
    ]

def _simulate_validation_profile(
    time_seconds: np.ndarray,
    irradiance_profile: np.ndarray,
    temperature_profile: np.ndarray,
    params: PVModuleParams,
    controller_dt: float,
    dc_bus_voltage: float,
    mppt_enable_irradiance: float,
    *,
    seed: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    from .pv_main import (
        PVIrradianceVariability,
        SampledPVControlRunner,
        _empty_records,
        _records_to_arrays,
        _sim_from_records,
    )

    runner = SampledPVControlRunner(params, controller_dt, dc_bus_voltage, mppt_enable_irradiance)
    variability = PVIrradianceVariability(controller_dt, seed=seed)
    records = _empty_records()

    for physical_time, base_g, base_t in zip(time_seconds, irradiance_profile, temperature_profile):
        G_i, T_i = variability.step(base_g, base_t)
        sim_row = runner.step(float(physical_time), float(G_i), float(T_i))
        records["time"].append(float(physical_time))
        records["physical_time"].append(float(physical_time))
        records["pred_G"].append(float(G_i))
        records["pred_T_ambient"].append(float(T_i))
        for key in ("T_cell", "V_pv", "V_ref", "I_pv", "P_pv", "P_mpp", "P_boost_out", "boost_efficiency", "V_out", "I_L", "duty"):
            records[key].append(float(sim_row[key]))
        dt_hours = float(controller_dt) / 3600.0
        records["array_energy_kwh"].append(float(sim_row["P_pv"]) * dt_hours / 1000.0)
        records["boost_energy_kwh"].append(float(sim_row["P_boost_out"]) * dt_hours / 1000.0)

    arrays = _records_to_arrays(records)
    return arrays, _sim_from_records(records)

def _scenario_metrics(arrays: dict[str, np.ndarray], sim: dict[str, np.ndarray]) -> dict[str, float]:
    mppt_eff_pct, mppt_valid = _tracking_efficiency_percent(sim["P_pv"], sim["P_mpp"], min_reference_w=50.0)
    boost_eff_pct = np.clip(np.asarray(sim["boost_efficiency"], dtype=float), 0.0, 1.2) * 100.0
    boost_valid = np.isfinite(sim["P_pv"]) & (np.asarray(sim["P_pv"], dtype=float) > 1.0)
    voltage_error = np.asarray(sim["V_pv"], dtype=float) - np.asarray(sim["V_ref"], dtype=float)
    array_energy = np.cumsum(np.asarray(arrays["array_energy_kwh"], dtype=float))
    boost_energy = np.cumsum(np.asarray(arrays["boost_energy_kwh"], dtype=float))
    return {
        "avg_irradiance_w_m2": float(np.mean(arrays["pred_G"])) if len(arrays["pred_G"]) else 0.0,
        "avg_array_power_kw": float(np.mean(sim["P_pv"])) / 1000.0 if len(sim["P_pv"]) else 0.0,
        "avg_boost_power_kw": float(np.mean(sim["P_boost_out"])) / 1000.0 if len(sim["P_boost_out"]) else 0.0,
        "avg_mppt_efficiency_pct": _mean_percent(mppt_eff_pct, mppt_valid),
        "avg_boost_efficiency_pct": _mean_percent(boost_eff_pct, boost_valid),
        "mean_abs_voltage_error_v": float(np.mean(np.abs(voltage_error))) if len(voltage_error) else 0.0,
        "max_abs_voltage_error_v": float(np.max(np.abs(voltage_error))) if len(voltage_error) else 0.0,
        "loss_energy_kwh": float(np.maximum(array_energy - boost_energy, 0.0)[-1]) if len(array_energy) else 0.0,
    }

def _plot_operating_condition_result(
    title: str,
    arrays: dict[str, np.ndarray],
    sim: dict[str, np.ndarray],
    metrics: dict[str, float],
    path: Path,
) -> str:
    from matplotlib.figure import Figure

    t, xlabel = _time_axis(arrays["time"], arrays["physical_time"])
    sl = plot_stride(t)
    time_seconds = arrays["physical_time"]
    p_mpp_kw = moving_average(sim["P_mpp"], time_seconds) / 1000.0
    p_pv_kw = moving_average(sim["P_pv"], time_seconds) / 1000.0
    p_boost_kw = moving_average(sim["P_boost_out"], time_seconds) / 1000.0
    v_ref = moving_average(sim["V_ref"], time_seconds, 0.8)
    v_pv = moving_average(sim["V_pv"], time_seconds, 0.8)
    v_error = moving_average(sim["V_pv"] - sim["V_ref"], time_seconds, 0.8)
    irradiance = moving_average(arrays["pred_G"], time_seconds, 1.0)
    ambient = moving_average(arrays["pred_T_ambient"], time_seconds, 1.2)
    cell_temp = moving_average(arrays["T_cell"], time_seconds, 1.2)
    display_window_minutes = float(t[-1]) if len(t) else None

    fig = Figure(figsize=(8.6, 7.2), facecolor="#f8fafc")
    axes = fig.subplots(3, 2)
    ax_input = axes[0, 0]
    ax_mppt = axes[0, 1]
    ax_power = axes[1, 0]
    ax_voltage = axes[1, 1]
    ax_error = axes[2, 0]
    ax_summary = axes[2, 1]
    for axis in axes.flat:
        axis.set_facecolor("#ffffff")

    ax_input.plot(t[sl], irradiance[sl], color="#f59e0b", linewidth=0.9, label="辐照度")
    _style_axes(ax_input, "输入辐照度", "", "辐照度 [W/m2]")
    _apply_display_window_xlim(ax_input, display_window_minutes)
    ax_input_t = ax_input.twinx()
    ax_input_t.plot(t[sl], ambient[sl], color="#2563eb", linewidth=0.82, label="环境温度")
    ax_input_t.plot(t[sl], cell_temp[sl], color="#dc2626", linewidth=0.88, label="电池片温度")
    ax_input_t.set_ylabel("温度 [degC]", fontsize=9.2, color="#64748b")
    _style_secondary_axis(ax_input_t)
    handles_left, labels_left = ax_input.get_legend_handles_labels()
    handles_right, labels_right = ax_input_t.get_legend_handles_labels()
    legend = ax_input.legend(handles_left + handles_right, labels_left + labels_right, loc="best", fontsize=8.3, frameon=True)
    legend.get_frame().set_facecolor("#ffffff")
    legend.get_frame().set_edgecolor("#e2e8f0")

    ax_mppt.plot(t[sl], p_mpp_kw[sl], "--", color="#f59e0b", linewidth=0.86, label="理论Pmpp")
    ax_mppt.plot(t[sl], p_pv_kw[sl], color="#2563eb", linewidth=0.96, label="实际Ppv")
    _style_axes(ax_mppt, "MPPT跟踪", "", "功率 [kW]")
    _set_series_ylim(ax_mppt, [p_mpp_kw, p_pv_kw])
    _apply_display_window_xlim(ax_mppt, display_window_minutes)

    ax_power.plot(t[sl], p_pv_kw[sl], color="#2563eb", linewidth=0.92, label="阵列功率")
    ax_power.plot(t[sl], p_boost_kw[sl], color="#16a34a", linewidth=0.92, label="Boost输出")
    _style_axes(ax_power, "Boost功率传递", "", "功率 [kW]")
    _set_series_ylim(ax_power, [p_pv_kw, p_boost_kw])
    _apply_display_window_xlim(ax_power, display_window_minutes)

    ax_voltage.plot(t[sl], v_ref[sl], "--", color="#f59e0b", linewidth=0.84, label="Vref")
    ax_voltage.plot(t[sl], v_pv[sl], color="#2563eb", linewidth=0.94, label="Vpv")
    _style_axes(ax_voltage, "端电压跟踪", "", "电压 [V]")
    _set_series_ylim(ax_voltage, [v_ref, v_pv])
    _apply_display_window_xlim(ax_voltage, display_window_minutes)

    ax_error.plot(t[sl], v_error[sl], color="#dc2626", linewidth=0.9, label="电压误差")
    ax_error.axhline(0.0, color="#94a3b8", linewidth=0.72, linestyle=":")
    _style_axes(ax_error, "电压误差", xlabel, "误差 [V]")
    _set_series_ylim(ax_error, [v_error, np.array([0.0])])
    _apply_display_window_xlim(ax_error, display_window_minutes)

    ax_summary.axis("off")
    summary_text = (
        f"平均辐照度 {metrics['avg_irradiance_w_m2']:.1f} W/m2\n"
        f"平均阵列功率 {metrics['avg_array_power_kw']:.2f} kW\n"
        f"平均Boost功率 {metrics['avg_boost_power_kw']:.2f} kW\n"
        f"平均MPPT效率 {metrics['avg_mppt_efficiency_pct']:.2f}%\n"
        f"平均Boost效率 {metrics['avg_boost_efficiency_pct']:.2f}%\n"
        f"平均电压误差 {metrics['mean_abs_voltage_error_v']:.2f} V\n"
        f"最大电压误差 {metrics['max_abs_voltage_error_v']:.2f} V\n"
        f"累计损耗 {metrics['loss_energy_kwh']:.4f} kWh"
    )
    ax_summary.text(
        0.03,
        0.97,
        summary_text,
        va="top",
        ha="left",
        fontsize=9.0,
        color="#334155",
        linespacing=1.45,
        bbox={"boxstyle": "round,pad=0.40", "facecolor": "#ffffff", "edgecolor": "#e2e8f0", "alpha": 0.98},
    )

    fig.suptitle(title, fontsize=13.0, fontweight="semibold", color="#0f172a", y=0.985)
    return _save_figure(fig, path, adjust_kwargs={"left": 0.08, "right": 0.97, "top": 0.92, "bottom": 0.08, "hspace": 0.38, "wspace": 0.20})

def run_operating_condition_validation(
    *,
    params: PVModuleParams,
    controller_dt: float,
    dc_bus_voltage: float,
    mppt_enable_irradiance: float,
    output_dir: str | Path | None = None,
    duration_s: float = 15.0 * 60.0,
) -> tuple[list[str], list[dict[str, float | str]]]:
    if not HAS_MATPLOTLIB:
        return [], []

    plot_dir = Path(output_dir) if output_dir else RESULTS_DIR
    plot_dir.mkdir(parents=True, exist_ok=True)
    time_seconds = np.arange(0.0, duration_s + controller_dt * 0.5, controller_dt, dtype=float)
    definitions = _scenario_definitions(duration_s)
    paths: list[str] = []
    summaries: list[dict[str, float | str]] = []

    for index, spec in enumerate(definitions):
        irradiance_profile = np.asarray(spec["irradiance"](time_seconds), dtype=float)
        temperature_profile = np.asarray(spec["temperature"](time_seconds), dtype=float)
        arrays, sim = _simulate_validation_profile(
            time_seconds=time_seconds,
            irradiance_profile=irradiance_profile,
            temperature_profile=temperature_profile,
            params=params,
            controller_dt=controller_dt,
            dc_bus_voltage=dc_bus_voltage,
            mppt_enable_irradiance=mppt_enable_irradiance,
            seed=DEFAULT_PV_VARIABILITY_SEED + 101 * (index + 1),
        )
        metrics = _scenario_metrics(arrays, sim)
        summaries.append({"key": str(spec["key"]), "title": str(spec["title"]), **metrics})
        paths.append(
            _plot_operating_condition_result(
                str(spec["title"]),
                arrays,
                sim,
                metrics,
                plot_dir / SCENARIO_VALIDATION_FILENAMES[index],
            )
        )

    return paths, summaries

def plot_control_results(
    time: np.ndarray,
    sim: dict[str, np.ndarray],
    dc_bus_voltage: float = DEFAULT_DC_BUS_VOLTAGE,
    output_dir: str | Path | None = None,
    physical_time: np.ndarray | None = None,
    sequence: int | None = None,
    display_window_minutes: float | None = None,
    include_summary: bool = True,
) -> list[str]:
    if not HAS_MATPLOTLIB:
        return []

    plot_dir = Path(output_dir) if output_dir else RESULTS_DIR
    plot_dir.mkdir(parents=True, exist_ok=True)
    t, xlabel = _time_axis(np.asarray(time, dtype=float), physical_time)
    sl = plot_stride(t)
    time_seconds = np.asarray(physical_time if physical_time is not None else time, dtype=float)

    p_pv_w = moving_average(sim["P_pv"], time_seconds)
    p_mpp_w = moving_average(sim["P_mpp"], time_seconds)
    p_boost_w = moving_average(sim["P_boost_out"], time_seconds)
    p_pv_kw = p_pv_w / 1000.0
    p_mpp_kw = p_mpp_w / 1000.0
    p_boost_kw = p_boost_w / 1000.0
    paths: list[str] = []

    fig, ax = _new_figure()
    ax.plot(t[sl], p_mpp_kw[sl], "--", label="理论最大功率点", color="#f59e0b")
    ax.plot(t[sl], p_pv_kw[sl], label="实际阵列功率", color="#2563eb")
    _style_axes(ax, "光伏理论最大功率点与实际阵列功率对比", xlabel, "功率 [kW]")
    _add_stat_box(ax, f"阵列峰值: {np.max(p_pv_kw):.2f} kW\n阵列平均值: {np.mean(p_pv_kw):.2f} kW")
    _set_series_ylim(ax, [p_mpp_kw, p_pv_kw])
    _apply_display_window_xlim(ax, display_window_minutes)
    paths.append(_save_plot(fig, ax, plot_dir / "pv_mppt_tracking.png"))

    fig, ax = _new_figure()
    ax.plot(t[sl], p_pv_kw[sl], label="实际阵列功率", color="#2563eb")
    ax.plot(t[sl], p_boost_kw[sl], label="Boost直流输出", color="#16a34a")
    _style_axes(ax, "光伏电气输出功率", xlabel, "功率 [kW]")
    _add_stat_box(ax, f"直流峰值: {np.max(p_boost_kw):.2f} kW\n直流平均值: {np.mean(p_boost_kw):.2f} kW")
    _set_series_ylim(ax, [p_pv_kw, p_boost_kw])
    _apply_display_window_xlim(ax, display_window_minutes)
    paths.append(_save_plot(fig, ax, plot_dir / "pv_electrical_power_output.png"))

    if not include_summary:
        return paths

    from matplotlib.figure import Figure

    fig = Figure(figsize=(7.2, 3.6), facecolor="#f8fafc")
    ax_ratio, ax_output = fig.subplots(1, 2)
    fig.suptitle("系统性能汇总", fontsize=13, fontweight="semibold", color="#0f172a", y=0.98)
    for axis in (ax_ratio, ax_output):
        axis.set_facecolor("#ffffff")

    raw_p_mpp = np.asarray(sim["P_mpp"], dtype=float)
    raw_p_pv = np.asarray(sim["P_pv"], dtype=float)
    raw_p_boost = np.asarray(sim["P_boost_out"], dtype=float)
    valid = raw_p_mpp > max(50.0, 0.1 * float(np.max(raw_p_mpp)) if len(raw_p_mpp) else 50.0)
    if not np.any(valid):
        valid = raw_p_mpp > 1.0

    if np.any(valid):
        mpp_score = _per_second_gap_score(raw_p_mpp, raw_p_pv, time_seconds, valid, min_reference=1.0)
        boost_valid = valid & (raw_p_pv > 1.0)
        boost_score = _per_second_gap_score(raw_p_pv, raw_p_boost, time_seconds, boost_valid, min_reference=1.0)
        mean_boost = float(np.mean(raw_p_boost[valid]))
        max_boost = float(np.max(raw_p_boost[valid]))
    else:
        mpp_score = boost_score = mean_boost = max_boost = 0.0

    score_values = [mpp_score, boost_score]
    score_bars = ax_ratio.bar(
        ["最大功率点跟踪", "Boost传输"],
        score_values,
        color=["#2563eb", "#16a34a"],
        width=0.58,
    )
    _style_axes(ax_ratio, "转换得分", "", "得分 [%]", add_legend=False)
    ax_ratio.set_ylim(0.0, max(110.0, max(score_values) * 1.12 if score_values else 110.0))
    ax_ratio.tick_params(axis="x", rotation=8)
    _annotate_bars(ax_ratio, score_bars, score_values, "%", decimals=1)

    power_values = [mean_boost, max_boost]
    power_bars = ax_output.bar(
        ["Boost平均输出", "Boost最大输出"],
        power_values,
        color=["#f97316", "#dc2626"],
        width=0.56,
    )
    _style_axes(ax_output, "输出能力", "", "功率 [W]", add_legend=False)
    ax_output.set_ylim(0.0, max(1.0, max(power_values) * 1.16 if power_values else 1.0))
    ax_output.tick_params(axis="x", rotation=8)
    _annotate_bars(ax_output, power_bars, power_values, "W", decimals=0)

    paths.append(
        _save_figure(
            fig,
            plot_dir / "pv_final_performance_summary.png",
            adjust_kwargs={"left": 0.10, "right": 0.97, "top": 0.82, "bottom": 0.18, "wspace": 0.34},
        )
    )
    return paths

def _save_plot(fig, ax, path: Path) -> str:
    return _save_figure(fig, path, adjust_kwargs={"left": 0.10, "right": 0.97, "top": 0.88, "bottom": 0.18})

def _atomic_save_figure(fig, path: Path, dpi: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix=f".{path.stem}.", dir=str(path.parent))
    os.close(fd)
    moved_path: Path | None = None
    try:
        fig.savefig(tmp_path, dpi=dpi, facecolor=fig.get_facecolor())
        try:
            os.replace(tmp_path, path)
            moved_path = path
        except PermissionError:
            fallback = path.with_name(f"{path.stem}_{wall_clock.time_ns()}{path.suffix}")
            os.replace(tmp_path, fallback)
            moved_path = fallback
        return moved_path
    finally:
        if moved_path is None:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

def main() -> None:
    """命令行入口：不启动 GUI，直接生成 5 张典型工况验证图。"""
    import argparse

    parser = argparse.ArgumentParser(description="生成光伏典型工况验证图")
    parser.add_argument("--controller-dt", type=float, default=DEFAULT_CONTROLLER_DT)
    parser.add_argument("--dc-bus-voltage", type=float, default=DEFAULT_DC_BUS_VOLTAGE)
    parser.add_argument("--mppt-enable-irradiance", type=float, default=DEFAULT_MPPT_ENABLE_IRRADIANCE)
    args = parser.parse_args()

    result = generate_scenario_validation_plots(
        params=PVModuleParams(),
        controller_dt=args.controller_dt,
        dc_bus_voltage=args.dc_bus_voltage,
        mppt_enable_irradiance=args.mppt_enable_irradiance,
        output_dir=RESULTS_DIR,
    )
    paths = result.get("scenario_validation_paths", [])
    print(f"光伏：已生成 {len(paths)} 张典型工况验证图。")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()

