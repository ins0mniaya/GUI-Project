# 本文件负责构造工况功率曲线、执行仿真、保存 CSV，并生成结果图
import argparse
import os
import time as wall_clock
from pathlib import Path

import numpy as np

try:
    from .flywheel_control import FlywheelPowerController, FlywheelSystemSimulator
    from .flywheel_params import FlywheelParams
except ImportError:
    from flywheel_control import FlywheelPowerController, FlywheelSystemSimulator
    from flywheel_params import FlywheelParams

# 默认控制步长
DEFAULT_CONTROLLER_DT = 0.1
DEFAULT_SIM_DURATION = 900.0
DEFAULT_SCENARIO = "field"

RESULTS_DIR = Path(__file__).resolve().parent / "results"


# 允许通过环境变量关闭绘图，便于在无图形依赖环境中只运行仿真
if os.environ.get("FLYWHEEL_SKIP_PLOTS", "0") == "1":
    HAS_MATPLOTLIB = False
    plt = None
else:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        HAS_MATPLOTLIB = True
    except ImportError:
        HAS_MATPLOTLIB = False
        plt = None
        print("[Warning] matplotlib is not installed. Simulation still works, plots will be skipped.")


def _configure_chinese_fonts() -> None:
    if not HAS_MATPLOTLIB:
        return
    # 强制指定全局中文字体，优先微软雅黑
    plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Droid Sans Fallback", "Microsoft YaHei", "SimHei", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False


def build_time(duration: float, controller_dt: float) -> np.ndarray:
    # 按仿真总时长和控制步长生成时间序列
    return np.arange(0.0, max(float(duration), controller_dt), controller_dt, dtype=float)


def _smooth_window(t: np.ndarray, start_s: float, end_s: float, edge_s: float = 8.0) -> np.ndarray:
    # 生成平滑的开关窗口
    edge = max(float(edge_s), 1e-6)
    x_on = np.clip((t - float(start_s)) / edge, 0.0, 1.0)
    x_off = np.clip((float(end_s) - t) / edge, 0.0, 1.0)
    on = x_on * x_on * (3.0 - 2.0 * x_on)
    off = x_off * x_off * (3.0 - 2.0 * x_off)
    return on * off


def _colored_noise(time: np.ndarray, scale: float, seed: int, knot_s: float = 9.0) -> np.ndarray:
    # 生成低频随机扰动
    if len(time) == 0 or scale <= 0.0:
        return np.zeros_like(time, dtype=float)
    rng = np.random.default_rng(seed)
    t = np.asarray(time, dtype=float)
    knots = np.arange(float(t[0]), float(t[-1]) + max(knot_s, 1.0), max(knot_s, 1.0))
    values = rng.normal(0.0, scale, size=len(knots))
    return np.interp(t, knots, values)


def build_power_reference(time: np.ndarray, scenario: str, rated_power_w: float) -> np.ndarray:
    # 根据工况名称生成参考功率曲线，正值为放电，负值为充电
    t = np.asarray(time, dtype=float)
    scenario = scenario.lower()
    p = np.zeros_like(t)
    total = float(t[-1]) if len(t) else 0.0

    def scaled_window(start_frac: float, end_frac: float, gain: float, edge_s: float = 8.0) -> np.ndarray:
        if total <= 0.0:
            return np.zeros_like(t)
        start = max(0.0, start_frac * total)
        end = max(start + 1e-6, end_frac * total)
        return gain * rated_power_w * _smooth_window(t, start, end, edge_s=edge_s)

    # 阶跃工况
    if scenario == "step":
        period = 180.0
        phase = np.mod(t, period)
        scale = 0.92 + 0.08 * np.sin(2.0 * np.pi * t / max(3.0 * period, 1.0))
        masks = [
            ((phase >= 18.0) & (phase < 48.0), 0.42),
            ((phase >= 58.0) & (phase < 85.0), 0.72),
            ((phase >= 103.0) & (phase < 128.0), -0.58),
            ((phase >= 138.0) & (phase < 163.0), -0.84),
            ((phase >= 168.0) & (phase < 178.0), 0.28),
        ]
        for mask, level in masks:
            p[mask] = level * scale[mask] * rated_power_w
    # 周期工况
    elif scenario == "cycle":
        p = 0.46 * rated_power_w * np.sin(2.0 * np.pi * t / 120.0)
        p += 0.16 * rated_power_w * np.sin(2.0 * np.pi * t / 55.0 + 0.8)
        p += 0.06 * rated_power_w * np.sin(2.0 * np.pi * t / 22.0)
        p = np.clip(p, -0.82 * rated_power_w, 0.82 * rated_power_w)
    # 随机工况
    elif scenario == "random":
        rng = np.random.default_rng(42)
        knots = np.arange(0.0, max(float(t[-1]) if len(t) else 0.0, 1.0) + 12.0, 12.0)
        values = rng.normal(0.0, 0.36 * rated_power_w, size=len(knots))
        values = np.clip(values, -0.82 * rated_power_w, 0.82 * rated_power_w)
        p = np.interp(t, knots, values)
        p += 0.14 * rated_power_w * np.sin(2.0 * np.pi * t / 54.0 + 0.25)
        p += 0.08 * rated_power_w * np.sin(2.0 * np.pi * t / 18.0)
        p += _colored_noise(t, 0.028 * rated_power_w, seed=42, knot_s=3.5)
        p = np.clip(p, -0.95 * rated_power_w, 0.95 * rated_power_w)
    # 脉冲工况
    elif scenario == "pulse":
        period = 72.0
        phase = np.mod(t, period)
        scale = 0.94 + 0.06 * np.sin(2.0 * np.pi * t / 360.0)
        p[phase < 14.0] = 0.72 * scale[phase < 14.0] * rated_power_w
        p[(phase >= 24.0) & (phase < 43.0)] = -0.68 * scale[(phase >= 24.0) & (phase < 43.0)] * rated_power_w
        p[(phase >= 52.0) & (phase < 64.0)] = 0.88 * scale[(phase >= 52.0) & (phase < 64.0)] * rated_power_w
    # DC 母线工况
    elif scenario == "dc_bus":
        # DC 母线工况下，p 表示负载功率。
        # 飞轮通过母线电压 PI 控制补偿功率不平衡
        p[:] = 0.18 * rated_power_w
        p += 0.04 * rated_power_w * np.sin(2.0 * np.pi * t / 90.0 + 0.4)
        p += scaled_window(0.06, 0.16, 0.15, edge_s=10.0)
        p += scaled_window(0.24, 0.39, 0.14, edge_s=10.0)
        p -= scaled_window(0.42, 0.52, 0.06, edge_s=10.0)
        p += scaled_window(0.58, 0.72, 0.22, edge_s=8.0)
        p -= scaled_window(0.77, 0.85, 0.05, edge_s=8.0)
        p += scaled_window(0.88, 0.96, 0.25, edge_s=8.0)
        p = np.clip(p, 0.04 * rated_power_w, 0.78 * rated_power_w)
    # 现场扰动工况
    elif scenario == "field":
        rng = np.random.default_rng(20260518)
        knots = np.arange(0.0, max(float(t[-1]) if len(t) else 0.0, 1.0) + 18.0, 18.0)
        values = rng.normal(0.0, 0.30 * rated_power_w, size=len(knots))
        values = np.clip(values, -0.62 * rated_power_w, 0.62 * rated_power_w)
        p = np.interp(t, knots, values)
        p += 0.16 * rated_power_w * np.sin(2.0 * np.pi * t / 90.0 + 0.55)
        p += 0.065 * rated_power_w * np.sin(2.0 * np.pi * t / 18.0)
        p += _colored_noise(t, 0.032 * rated_power_w, seed=74, knot_s=3.5)

        # 这里故意加入几个超过额定值的扰动片段
        p += scaled_window(0.07, 0.16, 0.84, edge_s=5.0)
        p -= scaled_window(0.22, 0.31, 0.76, edge_s=5.0)
        p += scaled_window(0.39, 0.48, 1.02, edge_s=4.0)
        p -= scaled_window(0.56, 0.64, 0.58, edge_s=5.0)
        p += scaled_window(0.72, 0.80, 0.93, edge_s=5.0)
        p -= scaled_window(0.86, 0.93, 0.62, edge_s=5.0)
        p = np.clip(p, -1.16 * rated_power_w, 1.16 * rated_power_w)
    else:
        raise ValueError(f"Unknown scenario: {scenario}")
    return p


def _stop_requested(stop_callback) -> bool:

    return bool(stop_callback and stop_callback())


def _wait_while_paused(pause_callback, stop_callback) -> float:
    paused_start = wall_clock.monotonic()
    while pause_callback and pause_callback() and not _stop_requested(stop_callback):
        wall_clock.sleep(0.05)
    return wall_clock.monotonic() - paused_start


def _arrays_from_samples(samples: dict[str, list[float]]) -> dict[str, np.ndarray]:
    # 将逐步采样的列表结果转换为 numpy 数组
    return {key: np.asarray(values, dtype=float) for key, values in samples.items()}


def simulate_flywheel_operation(
    params: FlywheelParams,
    time: np.ndarray,
    p_ref: np.ndarray,
    controller_dt: float,
    response_tau_s: float = 1.0,
    scenario: str = "step",
    progress_callback=None,
    plot_update_interval_seconds: float | None = None,
    pause_callback=None,
    stop_callback=None,
    real_time_playback: bool = False,
    playback_speed: float = 1.0,
) -> dict:
    # 飞轮仿真核心入口
    # 无界面回调时直接快速仿真；有界面回调时支持暂停、停止和实时播放
    controller = FlywheelPowerController(params=params, response_tau_s=response_tau_s)
    simulator = FlywheelSystemSimulator(params=params, controller=controller)
    simulator.initialize()
    if (
        scenario != "dc_bus"
        and progress_callback is None
        and pause_callback is None
        and stop_callback is None
        and not real_time_playback
    ):
        return simulator.simulate(time, p_ref, controller_dt)

    time = np.asarray(time, dtype=float)
    input_power = np.asarray(p_ref, dtype=float)
    samples: dict[str, list[float]] = {"time": []}
    start_wall_time = wall_clock.monotonic()
    last_publish_wall_time = start_wall_time
    last_publish_physical_time = float(time[0]) if len(time) else 0.0
    playback_speed = max(float(playback_speed), 1e-6)
    interval = None if plot_update_interval_seconds is None else max(float(plot_update_interval_seconds), 0.0)

    for i, simulation_time in enumerate(time):
        if _stop_requested(stop_callback):
            break
        start_wall_time += _wait_while_paused(pause_callback, stop_callback)
        if _stop_requested(stop_callback):
            break
        if real_time_playback:
            target_elapsed = float(simulation_time) / playback_speed
            while wall_clock.monotonic() - start_wall_time < target_elapsed:
                if _stop_requested(stop_callback):
                    return _arrays_from_samples(samples)
                start_wall_time += _wait_while_paused(pause_callback, stop_callback)
                wall_clock.sleep(0.02)
        dt = controller_dt if i == 0 else max(float(time[i] - time[i - 1]), 0.0)
        if scenario == "dc_bus":
            state = simulator.step_dc_bus(float(input_power[i]), dt)
        else:
            state = simulator.step(float(input_power[i]), dt)
        samples["time"].append(float(simulation_time))
        for key, value in state.items():
            if isinstance(value, (int, float, np.number)):
                samples.setdefault(key, []).append(float(value))

        if interval is None:
            interval_due = True
        else:
            physical_due = (float(simulation_time) - last_publish_physical_time) >= interval - 1e-9
            wall_due = (wall_clock.monotonic() - last_publish_wall_time) >= interval - 1e-9
            if real_time_playback:
                interval_due = physical_due and wall_due
            else:
                interval_due = physical_due
                
        should_publish = i == len(time) - 1 or interval_due
        if progress_callback is not None and should_publish:
            progress_callback(_arrays_from_samples(samples))
            last_publish_wall_time = wall_clock.monotonic()
            last_publish_physical_time = float(simulation_time)

    return _arrays_from_samples(samples)


def _summarize_flywheel_results(sim: dict, controller_dt: float, params: FlywheelParams) -> dict:
    # 从完整仿真序列中提取放电能量、充电能量、损耗、峰值功率和最终 SOC
    time = np.asarray(sim.get("time", []), dtype=float)
    power = np.asarray(sim.get("P_fw", []), dtype=float)
    losses = np.asarray(sim.get("P_loss_base", []), dtype=float)
    soc = np.asarray(sim.get("soc", []), dtype=float)
    dt_hours = controller_dt / 3600.0
    return {
        "discharge_energy_kwh": float(np.sum(np.maximum(power, 0.0) * dt_hours / 1000.0)) if len(power) else 0.0,
        "charge_energy_kwh": float(np.sum(np.maximum(-power, 0.0) * dt_hours / 1000.0)) if len(power) else 0.0,
        "loss_energy_kwh": float(np.sum(losses * dt_hours / 1000.0)) if len(losses) else 0.0,
        "peak_discharge_power_w": float(np.max(power)) if len(power) else 0.0,
        "peak_charge_power_w": float(np.min(power)) if len(power) else 0.0,
        "final_soc": float(soc[-1]) if len(soc) else params.soc_from_omega(params.omega_init_rad_s),
    }


def run_flywheel_simulation(
    scenario: str = DEFAULT_SCENARIO,
    duration: float = DEFAULT_SIM_DURATION,
    controller_dt: float = DEFAULT_CONTROLLER_DT,
    output_csv: str | None = None,
    response_tau_s: float = 1.0,
    params: FlywheelParams | None = None,
    progress_callback=None,
    plot_update_interval_seconds: float | None = None,
    pause_callback=None,
    stop_callback=None,
    real_time_playback: bool = False,
    playback_speed: float = 1.0,
) -> dict:
    # 对外主入口：构造工况、运行仿真、保存结果并生成图像
    params = params or FlywheelParams()
    time = build_time(duration, controller_dt)
    p_ref = build_power_reference(time, scenario, params.rated_power_w)

    def publish_partial(partial_sim: dict) -> None:
        if HAS_MATPLOTLIB and len(partial_sim["time"]):
            plot_flywheel_results(partial_sim["time"], partial_sim, params, scenario)
        if progress_callback is not None:
            snapshot = _summarize_flywheel_results(partial_sim, controller_dt, params)
            snapshot["plot_paths"] = _plot_paths()
            progress_callback(snapshot)

    sim = simulate_flywheel_operation(
        params,
        time,
        p_ref,
        controller_dt,
        response_tau_s=response_tau_s,
        scenario=scenario,
        progress_callback=publish_partial if progress_callback is not None else None,
        plot_update_interval_seconds=plot_update_interval_seconds,
        pause_callback=pause_callback,
        stop_callback=stop_callback,
        real_time_playback=real_time_playback,
        playback_speed=playback_speed,
    )
    summary = _summarize_flywheel_results(sim, controller_dt, params)

    if output_csv:
        save_results_csv(output_csv, sim)

    print("=" * 60)
    print("Flywheel energy storage power simulation")
    print("=" * 60)
    print(f"Scenario: {scenario}")
    print(f"Simulation duration: {duration:.2f} s")
    print(f"Controller timestep: {controller_dt:.3f} s")
    print("Model: flywheel + split losses + motor/converter current limits + optional DC-bus regulation")
    print(f"Rated power: {params.rated_power_w:.1f} W")
    print(f"Usable energy in model: {params.usable_energy_kwh:.3f} kWh")
    print(f"Peak discharge power: {summary['peak_discharge_power_w']:.2f} W")
    print(f"Peak charge power: {summary['peak_charge_power_w']:.2f} W")
    print(f"Final SOC: {100.0 * summary['final_soc']:.2f}%")
    if output_csv:
        print(f"Saved table: {Path(output_csv)}")

    if HAS_MATPLOTLIB and len(sim["time"]):
        plot_flywheel_results(sim["time"], sim, params, scenario)

    return summary


def _plot_paths() -> list[str]:
    # 返回飞轮主结果图路径，供 GUI 刷新使用
    return [
        str(RESULTS_DIR / "flywheel_power_tracking.png"),
        str(RESULTS_DIR / "flywheel_speed.png"),
        str(RESULTS_DIR / "flywheel_final_performance_summary.png"),
    ]


def save_results_csv(output_csv: str, sim: dict) -> None:
    # 将仿真结果保存为 CSV，第一列为时间，其余列为各状态量
    keys = [key for key in sim.keys() if key != "time"]
    rows = np.column_stack([sim["time"]] + [sim[key] for key in keys])
    header = "time_s," + ",".join(f"{key}" for key in keys)
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(output_path, rows, delimiter=",", header=header, comments="", fmt="%.8g")


def plot_flywheel_results(time: np.ndarray, sim: dict, params: FlywheelParams, scenario: str) -> None:
    # 根据仿真结果生成三张主要图：功率跟踪、转速变化和能量汇总
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_plot_outputs()
    _apply_plot_style()
    t = np.asarray(time, dtype=float)
    p_ref_kw = sim["P_ref"] / 1000.0
    p_fw_kw = sim["P_fw"] / 1000.0
    soc_pct = 100.0 * sim["soc"]
    dt_s = float(np.median(np.diff(t))) if len(t) > 1 else 0.0

    fig, ax = _new_figure()
    rated_kw = params.rated_power_w / 1000.0
    ax.plot(t, p_ref_kw, "--", label="参考功率", color="#f59e0b", linewidth=1.1)
    ax.plot(t, p_fw_kw, label="飞轮输出功率", color="#2563eb", linewidth=1.35)
    ax.axhline(0.0, color="#64748b", linewidth=0.9, linestyle=":")
    ax.axhline(rated_kw, color="#94a3b8", linewidth=0.8, linestyle=":", label="额定功率上限")
    ax.axhline(-rated_kw, color="#94a3b8", linewidth=0.8, linestyle=":")
    _style_axes(ax, "飞轮功率跟踪", "仿真时间 [秒]", "功率 [kW]")
    _add_stat_box(ax, f"峰值放电功率: {np.max(p_fw_kw):.2f} kW\n峰值充电功率: {np.min(p_fw_kw):.2f} kW")
    _save_plot(fig, ax, RESULTS_DIR / "flywheel_power_tracking.png")

    fig, ax = _new_figure()
    rpm = sim["rpm"]
    ax.plot(t, rpm, label="飞轮转速", color="#2563eb", linewidth=1.35)
    ax.axhline(params.rpm_from_omega(params.omega_min_rad_s), color="#94a3b8", linewidth=0.8, linestyle=":", label="转速边界")
    ax.axhline(params.rpm_from_omega(params.omega_max_rad_s), color="#94a3b8", linewidth=0.8, linestyle=":")
    _style_axes(ax, "飞轮转速", "仿真时间 [秒]", "转速 [rpm]")
    _add_stat_box(ax, f"初始转速: {rpm[0]:.0f} rpm\n最终转速: {rpm[-1]:.0f} rpm")
    _save_plot(fig, ax, RESULTS_DIR / "flywheel_speed.png")

    fig, ax = _new_figure()
    values = [
        float(np.sum(np.maximum(sim["P_fw"], 0.0)) * dt_s / 3600.0 / 1000.0),
        float(np.sum(np.maximum(-sim["P_fw"], 0.0)) * dt_s / 3600.0 / 1000.0),
        float(np.sum(sim["P_loss_base"]) * dt_s / 3600.0 / 1000.0),
    ]
    bars = ax.bar(["放电", "充电", "空载损耗"], values, color=["#2563eb", "#16a34a", "#f97316"], width=0.56)
    _style_axes(ax, "飞轮性能汇总", "", "能量 [kWh]", add_legend=False)
    ax.set_ylim(0.0, max(0.01, max(values) * 1.18 if values else 0.01))
    _annotate_bars(ax, bars, values, " kWh")

    _save_plot(fig, ax, RESULTS_DIR / "flywheel_final_performance_summary.png")


def _apply_plot_style() -> None:
    # 统一设置图像字体、字号和保存分辨率
    _configure_chinese_fonts()
    plt.rcParams.update({"figure.dpi": 120, "savefig.dpi": 220, "font.size": 10, "axes.titlesize": 12, "axes.labelsize": 10, "legend.fontsize": 8.5})


def _new_figure():
    # 创建统一尺寸和背景色的画布
    fig, ax = plt.subplots(figsize=(7.2, 3.6), facecolor="#f8fafc")
    ax.set_facecolor("#ffffff")
    return fig, ax


def _style_axes(ax, title: str, xlabel: str, ylabel: str, add_legend: bool = True) -> None:
    # 统一设置坐标轴标题、网格、边框和图例样式
    ax.set_title(title, loc="left", pad=10)
    ax.set_xlabel(xlabel, labelpad=6)
    ax.set_ylabel(ylabel, labelpad=6)
    ax.grid(True, color="#e5e7eb", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if add_legend:
        ax.legend(loc="best", frameon=True)


def _merge_legends(ax, extra_ax) -> None:
    handles, labels = ax.get_legend_handles_labels()
    extra_handles, extra_labels = extra_ax.get_legend_handles_labels()
    if ax.legend_ is not None:
        ax.legend_.remove()
    extra_ax.tick_params(axis="y", pad=8)
    ax.legend(handles + extra_handles, labels + extra_labels, loc="best", frameon=True)


def _save_plot(fig, ax, path: Path) -> None:
    # 保存临时文件
    fig.subplots_adjust(left=0.15, right=0.97, top=0.88, bottom=0.18, hspace=0.50)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    fig.savefig(temporary_path, format="png", dpi=100, facecolor=fig.get_facecolor())
    plt.close(fig)
    os.replace(temporary_path, path)
    print(f"Saved plot: {path}")


def _cleanup_legacy_plot_outputs() -> None:
    # 清理旧版本生成但当前不再使用的图像文件
    for name in ("flywheel_energy_loss.png", "flywheel_torque_limit.png", "flywheel_speed_soc.png", "flywheel_soc.png"):
        path = RESULTS_DIR / name
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def _add_stat_box(ax, text: str) -> None:
    ax.text(
        0.02,
        0.94,
        text,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8.5,
        bbox={"facecolor": "#f8fafc", "edgecolor": "#cbd5e1", "boxstyle": "round,pad=0.35"},
    )


def _annotate_bars(ax, bars, values: list[float], suffix: str) -> None:
    # 给柱状图顶部添加数值标注
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height(),
            f"{value:.3f}{suffix}",
            ha="center",
            va="bottom",
            fontsize=8.5,
        )


def build_parser() -> argparse.ArgumentParser:
    # 命令行参数解析，便于单独运行本文件进行调试
    parser = argparse.ArgumentParser(description="Flywheel energy storage power simulation.")
    parser.add_argument("--scenario", choices=["step", "cycle", "random", "pulse", "dc_bus", "field"], default=DEFAULT_SCENARIO)
    parser.add_argument("--duration", type=float, default=DEFAULT_SIM_DURATION)
    parser.add_argument("--controller-dt", type=float, default=DEFAULT_CONTROLLER_DT)
    parser.add_argument("--response-tau", type=float, default=1.0)
    parser.add_argument("--output-csv", type=str, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_flywheel_simulation(args.scenario, args.duration, args.controller_dt, args.output_csv, args.response_tau)


if __name__ == "__main__":
    main()
