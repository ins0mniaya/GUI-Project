# 统一管理 6 组典型工况验证图和指标统计
from __future__ import annotations
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np

from .wind_driver import WindPowerSystemSimulator
from .wind_parameter import WindTurbineParams

PACKAGE_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = PACKAGE_ROOT / "rescults"
DEFAULT_CONTROLLER_DT = 0.005
DEFAULT_DC_BUS_VOLTAGE = 370.0

# 6 组典型工况验证图的固定文件名
SCENARIO_VALIDATION_FILENAMES = (
    "wind_condition_cut_in_transition.png",
    "wind_condition_mppt_nominal.png",
    "wind_condition_mppt_to_rated_transition.png",
    "wind_condition_gust_transition.png",
    "wind_condition_rated_pitch_limit.png",
    "wind_condition_density_step.png",
)

# 允许通过环境变量 WIND_SKIP_PLOTS=1 跳过绘图，适合无图形依赖或只想快速仿真的场景
if os.environ.get("WIND_SKIP_PLOTS", "0") == "1":
    HAS_MATPLOTLIB = False
    plt = None
else:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # 强制指定全局中文字体，优先微软雅黑
        plt.rcParams["font.sans-serif"] = ["WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Droid Sans Fallback", "Microsoft YaHei", "SimHei", "sans-serif"]
        plt.rcParams["axes.unicode_minus"] = False
        HAS_MATPLOTLIB = True
    except ImportError:
        HAS_MATPLOTLIB = False
        plt = None
        print("[Warning] matplotlib is not installed. 仿真仍可运行，但会跳过绘图。")


# 将相对的 rescults/... 路径转换为当前文件所在目录下的结果目录路径
def _result_path(filename: str) -> str:
    path = Path(filename)
    if not path.is_absolute() and path.parts and path.parts[0] == "rescults":
        return str(RESULTS_DIR / Path(*path.parts[1:]))
    return filename

# 对曲线做简单滑动平均，用于降低绘图噪声，不改变原始仿真数据
def moving_average(values: np.ndarray, time: np.ndarray, window_seconds: float = 0.3) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) < 3 or window_seconds <= 0.0:
        return values.copy()
    dt = float(np.median(np.diff(time))) if len(time) > 1 else window_seconds
    window = max(1, int(round(window_seconds / max(dt, 1e-9))))
    if window <= 1:
        return values.copy()
    kernel = np.ones(window, dtype=float) / float(window)
    padded = np.pad(values, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def plot_stride(time: np.ndarray, max_points: int = 6000) -> slice:
    step = max(1, int(np.ceil(len(time) / max(max_points, 1))))
    return slice(None, None, step)

# 根据 results 中已有字段选择合适的横坐标单位：分钟、天或秒
def plot_time_axis(results: dict) -> tuple[np.ndarray, str]:
    if "display_time_minutes" in results:
        return results["display_time_minutes"], "当前15分钟窗口 [分钟]"
    if "physical_time_days" in results:
        return results["physical_time_days"], "滚动预测时间 [天]"
    return results["time"], "时间 [秒]"

# 实时滚动窗口绘图时，固定横坐标范围为当前 15 分钟窗口
def _apply_display_window_xlim(ax, results: dict) -> None:
    if "display_window_minutes" not in results:
        return
    try:
        window_minutes = float(results["display_window_minutes"])
    except (TypeError, ValueError):
        return
    if np.isfinite(window_minutes) and window_minutes > 0.0:
        ax.set_xlim(0.0, window_minutes)


# 检查绘图依赖是否可用，并确保输出目录存在
def _plot_ready(filename: str) -> bool:
    filename = _result_path(filename)
    if not HAS_MATPLOTLIB:
        print("[跳过] 未安装 matplotlib。")
        return False
    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    return True

# 保存图片到临时文件后原子替换目标文件
def _finish_plot(fig, filename: str, message: str, *, tight_kwargs=None):
    import tempfile
    
    filename = _result_path(filename)
    fig.tight_layout()
    
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=os.path.dirname(filename))
    os.close(tmp_fd)
    
    fig.savefig(tmp_path, dpi=100, **(tight_kwargs or {}))
    plt.close(fig)
    
    try:
        os.replace(tmp_path, filename)
    except Exception:
        import shutil
        shutil.move(tmp_path, filename)
        
    print(f"{message}: {filename}")

# 绘制一组曲线到同一个坐标轴，并统一设置标题、图例、网格和纵轴范围
def _line_panel(ax, time: np.ndarray, results: dict, specs: list[tuple[str, str, str]], ylabel: str, title: str):
    sl = plot_stride(time)
    plotted = []
    for key, style, label in specs:
        values = np.asarray(results[key], dtype=float)
        plotted.append(values)
        ax.plot(time[sl], values[sl], style, linewidth=1.2, label=label)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(loc="best")
    ax.grid(True, alpha=0.25)
    _expand_y_limits(ax, plotted)
    _apply_display_window_xlim(ax, results)

# 根据有效数据自动扩展纵轴范围，让曲线不贴边显示
def _expand_y_limits(ax, series: list[np.ndarray], *, pad_ratio: float = 0.8, min_pad_ratio: float = 0.03) -> None:
    valid_parts = []
    for item in series:
        arr = np.asarray(item, dtype=float)
        if np.any(np.isfinite(arr)):
            valid_parts.append(arr[np.isfinite(arr)])
    if not valid_parts:
        return
    merged = np.concatenate(valid_parts)
    y_min = float(np.min(merged))
    y_max = float(np.max(merged))
    span = y_max - y_min
    ref = max(abs(y_max), abs(y_min), 1.0)
    pad = max(span * pad_ratio, ref * min_pad_ratio)
    ax.set_ylim(y_min - pad, y_max + pad)


# 绘制实际转速与最大功率点转速的 MPPT 跟踪对比图
def plot_mppt_tracking(results: dict, filename: str = "rescults/wind_mppt_tracking.png"):
    if not _plot_ready(filename):
        return

    t, xlabel = plot_time_axis(results)
    from matplotlib.figure import Figure
    fig = Figure(figsize=(7.2, 3.6))
    ax = fig.subplots()
    _line_panel(
        ax,
        t,
        results,
        [("omega", "-", "实际转速"), ("omega_mpp", "--", "最大功率点转速")],
        "转子转速 [rad/s]",
        "MPPT转速跟踪",
    )
    ax.set_xlabel(xlabel)
    _finish_plot(fig, filename, "MPPT tracking plot saved")

def _plot_power_lines(ax, results: dict, specs: list[tuple], smooth_seconds: float) -> None:
    t, _ = plot_time_axis(results)
    sim_t = results["time"]
    sl = plot_stride(t)
    plotted = []
    for spec in specs:
        key, style, label = spec[:3]
        kwargs = spec[3] if len(spec) > 3 else {}
        values = moving_average(results[key], sim_t, smooth_seconds)
        plotted.append(values)
        ax.plot(t[sl], values[sl], style, label=label, **kwargs)
    _expand_y_limits(ax, plotted)
    _apply_display_window_xlim(ax, results)

# 绘制理论最大功率点功率和实际直流输出功率的对比图
def plot_power_output(
    results: dict,
    filename: str = "rescults/wind_power_output.png",
    smooth_seconds: float = 0.3,
):
    if not _plot_ready(filename):
        return

    _, xlabel = plot_time_axis(results)
    from matplotlib.figure import Figure
    fig = Figure(figsize=(7.2, 3.6))
    ax = fig.subplots()
    _plot_power_lines(
        ax,
        results,
        [
            ("P_mpp", "--", "理论最大功率点", {"color": "tab:orange", "linewidth": 1.1, "alpha": 0.85, "zorder": 2}),
            ("P_out", "-", "实际直流输出功率", {"color": "tab:blue", "linewidth": 1.45, "alpha": 0.95, "zorder": 3}),
        ],
        smooth_seconds,
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel("功率 [W]")
    ax.set_title(f"理论最大功率点与实际直流输出功率对比（{smooth_seconds:.1f} 秒滑动平均）")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.25)
    _finish_plot(fig, filename, "Power output plot saved")



# 计算系统性能汇总图需要的转换比例和输出功率指标
def _performance_metrics(results: dict) -> tuple[list[str], list[float], list[str], list[float]]:
    valid = results["P_mpp"] > 50.0
    if not np.any(valid):
        valid = results["P_mpp"] > 1.0
    if np.any(valid):
        mpp = results["P_mpp"][valid]
        aero = results["P_aero"][valid]
        rect = results["P_rect"][valid]
        out = results["P_out"][valid]

        sum_mpp = float(np.sum(mpp))
        sum_aero = float(np.sum(aero))
        sum_rect = float(np.sum(rect))
        sum_out = float(np.sum(out))

        ratio_values = [
            (sum_aero / max(sum_mpp, 1.0)) * 100.0,
            (sum_rect / max(sum_aero, 1.0)) * 100.0,
            (sum_out / max(sum_rect, 1.0)) * 100.0,
        ]
    else:
        ratio_values = [0.0, 0.0, 0.0]
        out = results["P_out"]
    return (
        ["平均气动功率/最大功率点", "平均整流输出/气动功率", "平均直流输出/整流输出"],
        ratio_values,
        ["平均输出功率", "最大输出功率"],
        [float(np.mean(out)) if len(out) else 0.0, float(np.max(results["P_out"])) if len(results["P_out"]) else 0.0],
    )

def _bar_panel(ax, labels: list[str], values: list[float], colors: list[str], ylabel: str, title: str, suffix: str):
    ax.bar(labels, values, color=colors, width=0.58)
    ax.set_ylim(0, max(values) * 1.18 if max(values) > 0 else 1.0)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.25)
    ax.tick_params(axis="x", rotation=18)
    for idx, value in enumerate(values):
        ax.text(idx, value, f"{value:.1f}{suffix}" if suffix == "%" else f"{value:.0f} {suffix}", ha="center", va="bottom", fontsize=9)

# 绘制系统整体性能汇总图，包括转换比例和输出能力
def plot_system_performance_summary(results: dict, filename: str = "rescults/wind_system_performance_summary.png"):
    if not _plot_ready(filename):
        return

    ratio_labels, ratio_values, power_labels, power_values = _performance_metrics(results)
    from matplotlib.figure import Figure
    fig = Figure(figsize=(7.2, 3.6))
    axes = fig.subplots(1, 2)
    _bar_panel(axes[0], ratio_labels, ratio_values, ["tab:blue", "tab:green", "tab:purple"], "比例 [%]", "转换比例", "%")
    axes[0].set_ylim(0, max(110.0, max(ratio_values) * 1.15))
    _bar_panel(axes[1], power_labels, power_values, ["tab:orange", "tab:red"], "功率 [W]", "输出能力", "W")
    fig.suptitle("系统性能汇总", y=1.02, fontsize=13)
    _finish_plot(fig, filename, "System performance summary plot saved", tight_kwargs={"bbox_inches": "tight"})


def get_scenario_validation_plot_paths(output_dir: str | Path | None = None) -> list[str]:
    plot_dir = Path(output_dir) if output_dir else RESULTS_DIR
    return [str(plot_dir / name) for name in SCENARIO_VALIDATION_FILENAMES]


def _scenario_profile_from_anchors(
    time_seconds: np.ndarray,
    anchor_minutes: list[float],
    anchor_values: list[float],
    *,
    min_value: float,
) -> np.ndarray:
    anchors_s = np.asarray(anchor_minutes, dtype=float) * 60.0
    anchors_v = np.asarray(anchor_values, dtype=float)
    if anchors_s.ndim != 1 or anchors_v.ndim != 1 or len(anchors_s) != len(anchors_v) or len(anchors_s) == 0:
        raise ValueError("工况锚点必须是一维非空数组，且时间锚点和值锚点长度一致。")

    order = np.argsort(anchors_s)
    anchors_s = anchors_s[order]
    anchors_v = anchors_v[order]

    if anchors_s[0] > 0.0:
        anchors_s = np.insert(anchors_s, 0, 0.0)
        anchors_v = np.insert(anchors_v, 0, anchors_v[0])
    if anchors_s[-1] < float(time_seconds[-1]):
        anchors_s = np.append(anchors_s, float(time_seconds[-1]))
        anchors_v = np.append(anchors_v, anchors_v[-1])

    interpolated = np.interp(np.asarray(time_seconds, dtype=float), anchors_s, anchors_v)
    return np.maximum(interpolated, float(min_value))


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


def apply_validation_wind_perturbation(
    mean_wind_speed: np.ndarray,
    controller_dt: float,
    turbine_params: WindTurbineParams,
    *,
    seed_offset: int = 0,
) -> np.ndarray:
    mean = np.asarray(mean_wind_speed, dtype=float)
    if len(mean) == 0:
        return mean.copy()

    base_seed = int(getattr(turbine_params, "turbulence_seed", 202405))
    scenario_params = replace(
        turbine_params,
        turbulence_seed=base_seed + 97 * max(int(seed_offset), 0),
        turbulence_intensity=max(float(getattr(turbine_params, "turbulence_intensity", 0.03)), 0.04),
        turbulence_time_constant=min(max(float(getattr(turbine_params, "turbulence_time_constant", 20.0)), 6.0), 14.0),
        turbulence_max_deviation_ratio=max(float(getattr(turbine_params, "turbulence_max_deviation_ratio", 0.12)), 0.16),
    )
    perturbed = apply_synthetic_turbulence(mean, controller_dt, scenario_params)

    time_seconds = np.arange(len(mean), dtype=float) * max(float(controller_dt), 1e-6)
    ref_scale = np.maximum(mean, 0.8)
    harmonic = (
        0.012 * ref_scale * np.sin(2.0 * np.pi * time_seconds / 14.0 + 0.8 * seed_offset)
        + 0.008 * ref_scale * np.sin(2.0 * np.pi * time_seconds / 5.5 + 1.6 * seed_offset)
    )
    harmonic = np.clip(harmonic, -0.035 * ref_scale, 0.035 * ref_scale)
    blended = 0.82 * perturbed + 0.18 * (mean + harmonic)
    return np.maximum(blended, 0.0)

# 定义 6 组典型风电运行工况的风速和空气密度锚点
def _wind_validation_scenarios(duration_s: float) -> list[dict[str, object]]:
    duration_minutes = duration_s / 60.0
    return [
        {
            "key": "cut_in_transition",
            "title": "切入风速过渡工况输入与响应",
            "purpose": "验证风速从低于切入到进入 MPPT 区间时，转矩参考、整流输出和直流母线功率能否平滑建立。",
            "wind_anchor_minutes": [0.0, 1.5, 3.0, 4.5, 6.0, 7.5, 9.5, 11.5, 13.5, duration_minutes],
            "wind_anchor_values": [1.70, 1.85, 2.05, 2.28, 2.52, 2.74, 3.02, 3.28, 3.08, 2.92],
            "air_density_anchor_minutes": [0.0, 3.0, 6.0, 9.0, 12.0, duration_minutes],
            "air_density_anchor_values": [1.186, 1.187, 1.188, 1.189, 1.188, 1.187],
        },
        {
            "key": "mppt_nominal",
            "title": "额定以下MPPT工况输入与响应",
            "purpose": "验证中等风速下最优转矩 MPPT 的稳态跟踪效果，以及功率链路在常见运行区间内的能量传递效率。",
            "wind_anchor_minutes": [0.0, 1.8, 3.5, 5.2, 7.0, 8.8, 10.5, 12.3, 13.8, duration_minutes],
            "wind_anchor_values": [5.40, 5.85, 6.18, 6.62, 7.05, 7.36, 7.12, 6.72, 6.18, 5.76],
            "air_density_anchor_minutes": [0.0, 2.5, 5.0, 7.5, 10.0, 12.5, duration_minutes],
            "air_density_anchor_values": [1.192, 1.194, 1.196, 1.198, 1.197, 1.195, 1.193],
        },
        {
            "key": "mppt_to_rated_transition",
            "title": "MPPT向额定限功过渡工况输入与响应",
            "purpose": "验证风速由额定以下逐步抬升至额定区上方时，控制状态能否从最优转矩 MPPT 平稳切换到桨距限功。",
            "wind_anchor_minutes": [0.0, 1.5, 3.0, 4.5, 6.0, 7.5, 9.0, 10.5, 12.0, 13.5, duration_minutes],
            "wind_anchor_values": [7.80, 8.35, 9.05, 10.05, 10.90, 11.65, 12.35, 12.95, 12.10, 10.95, 9.65],
            "air_density_anchor_minutes": [0.0, 3.0, 6.0, 9.0, 12.0, duration_minutes],
            "air_density_anchor_values": [1.197, 1.199, 1.201, 1.202, 1.200, 1.198],
        },
        {
            "key": "gust_transition",
            "title": "阵风扰动工况输入与响应",
            "purpose": "验证风速快速突增和回落时，MPPT、桨距和发电机转矩链路对阵风扰动的过渡响应。",
            "wind_anchor_minutes": [0.0, 1.2, 2.8, 4.2, 5.8, 6.6, 8.0, 9.8, 11.3, 12.8, duration_minutes],
            "wind_anchor_values": [7.40, 7.75, 8.05, 8.52, 10.35, 11.75, 9.35, 8.62, 10.85, 9.12, 8.22],
            "air_density_anchor_minutes": [0.0, 4.0, 7.5, 11.0, duration_minutes],
            "air_density_anchor_values": [1.184, 1.186, 1.188, 1.187, 1.185],
        },
        {
            "key": "rated_pitch_limit",
            "title": "额定区桨距限功工况输入与响应",
            "purpose": "验证持续高于额定风速时，桨距 PI、转矩约束和 Boost 输出之间的限功协同效果。",
            "wind_anchor_minutes": [0.0, 1.4, 2.8, 4.4, 6.1, 7.8, 9.4, 11.0, 12.5, 13.8, duration_minutes],
            "wind_anchor_values": [12.20, 12.85, 13.55, 14.25, 15.05, 15.85, 15.35, 14.70, 14.08, 13.55, 12.95],
            "air_density_anchor_minutes": [0.0, 3.0, 6.0, 9.0, 12.0, duration_minutes],
            "air_density_anchor_values": [1.201, 1.204, 1.207, 1.209, 1.207, 1.204],
        },
        {
            "key": "density_step",
            "title": "空气密度突变工况输入与响应",
            "purpose": "验证风速基本稳定但空气密度抬升时，理论最大功率点、最优转矩系数和输出功率会随之变化。",
            "wind_anchor_minutes": [0.0, 1.8, 3.6, 5.4, 7.2, 9.0, 10.8, 12.6, 14.0, duration_minutes],
            "wind_anchor_values": [8.10, 8.32, 8.56, 8.78, 8.88, 8.74, 8.48, 8.22, 8.08, 8.00],
            "air_density_anchor_minutes": [0.0, 3.0, 5.5, 7.0, 8.5, 10.0, 12.0, 13.5, duration_minutes],
            "air_density_anchor_values": [1.150, 1.151, 1.154, 1.166, 1.184, 1.203, 1.219, 1.226, 1.228],
        },
    ]


def _simulate_validation_profile(
    time_seconds: np.ndarray,
    wind_profile: np.ndarray,
    density_profile: np.ndarray,
    turbine_params: WindTurbineParams,
    controller_dt: float,
    dc_bus_voltage: float,
) -> dict[str, np.ndarray]:
    # 使用与主仿真相同的系统模型
    sim = WindPowerSystemSimulator(dc_bus_voltage=dc_bus_voltage, turbine_params=turbine_params)
    results = sim.simulate(
        time_seconds,
        np.asarray(wind_profile, dtype=float),
        controller_dt,
        np.asarray(density_profile, dtype=float),
        real_time_playback=False,
    )
    results["physical_time"] = np.asarray(time_seconds, dtype=float).copy()
    results["physical_time_days"] = results["physical_time"] / (24.0 * 3600.0)
    results["cut_in_speed"] = float(sim.turbine.params.cut_in_speed)
    results["rated_power"] = float(sim.turbine.params.rated_power)
    return results


def _validation_state_label(state: str) -> str:
    labels = {
        "below_cut_in": "低于切入",
        "mppt": "MPPT",
        "rated_power_limit": "额定限功",
        "pitch_limit": "桨距限功",
        "cut_out": "切出停机",
        "overspeed_brake": "超速制动",
        "below_rated": "额定以下",
        "init": "初始化",
    }
    return labels.get(str(state), str(state))


def _validation_metrics(results: dict[str, np.ndarray]) -> dict[str, float | str]:
    time_seconds = np.asarray(results.get("time", []), dtype=float)
    if len(time_seconds) == 0:
        return {
            "min_wind_speed_mps": 0.0,
            "avg_wind_speed_mps": 0.0,
            "max_wind_speed_mps": 0.0,
            "min_air_density_kg_m3": 0.0,
            "avg_air_density_kg_m3": 0.0,
            "max_air_density_kg_m3": 0.0,
            "avg_dc_power_kw": 0.0,
            "peak_dc_power_kw": 0.0,
            "peak_mpp_power_kw": 0.0,
            "avg_aero_tracking_pct": 0.0,
            "avg_generator_capture_pct": 0.0,
            "avg_boost_efficiency_pct": 0.0,
            "mean_abs_speed_error_rad_s": 0.0,
            "max_pitch_deg": 0.0,
            "pitch_active_ratio_pct": 0.0,
            "limit_active_ratio_pct": 0.0,
            "loss_energy_kwh": 0.0,
            "dominant_state": "无数据",
        }

    valid = np.asarray(results["P_mpp"], dtype=float) > 50.0
    if not np.any(valid):
        valid = np.asarray(results["P_mpp"], dtype=float) > 1.0

    p_mpp = np.asarray(results["P_mpp"], dtype=float)
    p_aero = np.asarray(results["P_aero"], dtype=float)
    p_rect = np.asarray(results["P_rect"], dtype=float)
    p_out = np.asarray(results["P_out"], dtype=float)
    omega = np.asarray(results["omega"], dtype=float)
    omega_mpp = np.asarray(results["omega_mpp"], dtype=float)
    pitch_angle = np.asarray(results["pitch_angle"], dtype=float)
    states = np.asarray(results["state"], dtype=object)

    if np.any(valid):
        aero_tracking_pct = 100.0 * float(np.sum(p_aero[valid])) / max(float(np.sum(p_mpp[valid])), 1e-9)
        generator_capture_pct = 100.0 * float(np.sum(p_rect[valid])) / max(float(np.sum(p_aero[valid])), 1e-9)
        boost_efficiency_pct = 100.0 * float(np.sum(p_out[valid])) / max(float(np.sum(p_rect[valid])), 1e-9)
    else:
        aero_tracking_pct = 0.0
        generator_capture_pct = 0.0
        boost_efficiency_pct = 0.0

    dt_hours = float(np.median(np.diff(time_seconds))) / 3600.0 if len(time_seconds) > 1 else 0.0
    loss_energy_kwh = (
        float(np.sum(np.maximum(p_aero - p_out, 0.0)) * dt_hours / 1000.0)
        if dt_hours > 0.0
        else 0.0
    )
    pitch_active_ratio_pct = 100.0 * float(np.mean(pitch_angle > 0.5))
    limit_states = np.isin(states.astype(str), ["rated_power_limit", "pitch_limit", "overspeed_brake", "cut_out"])
    limit_active_ratio_pct = 100.0 * float(np.mean(limit_states))
    state_counts = Counter(str(item) for item in states if str(item))
    dominant_state = _validation_state_label(state_counts.most_common(1)[0][0]) if state_counts else "无数据"

    return {
        "min_wind_speed_mps": float(np.min(results["wind_speed"])),
        "avg_wind_speed_mps": float(np.mean(results["wind_speed"])),
        "max_wind_speed_mps": float(np.max(results["wind_speed"])),
        "min_air_density_kg_m3": float(np.min(results["air_density"])),
        "avg_air_density_kg_m3": float(np.mean(results["air_density"])),
        "max_air_density_kg_m3": float(np.max(results["air_density"])),
        "avg_dc_power_kw": float(np.mean(p_out)) / 1000.0,
        "peak_dc_power_kw": float(np.max(p_out)) / 1000.0,
        "peak_mpp_power_kw": float(np.max(p_mpp)) / 1000.0,
        "avg_aero_tracking_pct": aero_tracking_pct,
        "avg_generator_capture_pct": generator_capture_pct,
        "avg_boost_efficiency_pct": boost_efficiency_pct,
        "mean_abs_speed_error_rad_s": float(np.mean(np.abs(omega - omega_mpp))),
        "max_pitch_deg": float(np.max(pitch_angle)),
        "pitch_active_ratio_pct": pitch_active_ratio_pct,
        "limit_active_ratio_pct": limit_active_ratio_pct,
        "loss_energy_kwh": loss_energy_kwh,
        "dominant_state": dominant_state,
    }


def _plot_operating_condition_result(
    title: str,
    results: dict[str, np.ndarray],
    metrics: dict[str, float | str],
    path: Path,
    dc_bus_voltage: float,
) -> str:
    from matplotlib.figure import Figure

    time_seconds = np.asarray(results["time"], dtype=float)
    time_minutes = time_seconds / 60.0
    sl = plot_stride(time_minutes)

    wind_speed = moving_average(results["wind_speed"], time_seconds, 1.0)
    air_density = moving_average(results["air_density"], time_seconds, 1.5)
    omega = moving_average(results["omega"], time_seconds, 0.4)
    omega_mpp = moving_average(results["omega_mpp"], time_seconds, 0.4)
    p_mpp_kw = moving_average(results["P_mpp"], time_seconds, 0.5) / 1000.0
    p_rect_kw = moving_average(results["P_rect"], time_seconds, 0.5) / 1000.0
    p_out_kw = moving_average(results["P_out"], time_seconds, 0.5) / 1000.0
    pitch_angle = moving_average(results["pitch_angle"], time_seconds, 0.4)
    speed_error = moving_average(results["omega"] - results["omega_mpp"], time_seconds, 0.4)

    fig = Figure(figsize=(8.7, 9.4), facecolor="#eef2f6")
    axes = fig.subplots(
        5,
        1,
        sharex=True,
        gridspec_kw={"height_ratios": [1.08, 1.0, 1.0, 1.0, 0.74]},
    )
    for axis in axes:
        axis.set_facecolor("#ffffff")
        axis.grid(True, alpha=0.18, linewidth=0.55, color="#cbd5e1")
        axis.tick_params(labelsize=8.8, colors="#334155")
        axis.spines["top"].set_alpha(0.35)
        axis.spines["right"].set_alpha(0.35)
        axis.spines["left"].set_alpha(0.45)
        axis.spines["bottom"].set_alpha(0.45)

    ax_input, ax_mppt, ax_boost, ax_speed, ax_error = axes

    ax_input.plot(time_minutes[sl], wind_speed[sl], color="#f59e0b", linewidth=0.86, label="轮毂风速")
    ax_input.set_ylabel("m/s", fontsize=9.6, color="#0f172a")
    ax_input_density = ax_input.twinx()
    ax_input_density.plot(time_minutes[sl], air_density[sl], color="#94a3b8", linewidth=0.82, label="空气密度")
    ax_input_density.set_ylabel("kg/m3", fontsize=9.4, color="#475569")
    ax_input_density.tick_params(labelsize=8.6, colors="#475569")
    handles_a, labels_a = ax_input.get_legend_handles_labels()
    handles_b, labels_b = ax_input_density.get_legend_handles_labels()
    legend = ax_input.legend(handles_a + handles_b, labels_a + labels_b, loc="upper right", fontsize=8.5, frameon=True)
    legend.get_frame().set_facecolor("#ffffff")
    legend.get_frame().set_edgecolor("#e2e8f0")
    _expand_y_limits(ax_input, [wind_speed], pad_ratio=0.12, min_pad_ratio=0.04)
    _expand_y_limits(ax_input_density, [air_density], pad_ratio=0.25, min_pad_ratio=0.002)

    ax_mppt.plot(time_minutes[sl], p_mpp_kw[sl], "--", color="#f59e0b", linewidth=0.84, label="理论Pmpp")
    ax_mppt.plot(time_minutes[sl], p_rect_kw[sl], color="#2563eb", linewidth=0.92, label="实际Prect")
    ax_mppt.set_ylabel("kW", fontsize=9.6, color="#0f172a")
    legend = ax_mppt.legend(loc="lower left", fontsize=8.5, frameon=True, ncol=2)
    legend.get_frame().set_facecolor("#ffffff")
    legend.get_frame().set_edgecolor("#e2e8f0")
    _expand_y_limits(ax_mppt, [p_mpp_kw, p_rect_kw], pad_ratio=0.12, min_pad_ratio=0.03)

    ax_boost.plot(time_minutes[sl], p_rect_kw[sl], color="#2563eb", linewidth=0.90, label="实际Prect")
    ax_boost.plot(time_minutes[sl], p_out_kw[sl], color="#16a34a", linewidth=0.92, label="Boost输出")
    ax_boost.set_ylabel("kW", fontsize=9.6, color="#0f172a")
    legend = ax_boost.legend(loc="lower left", fontsize=8.5, frameon=True, ncol=2)
    legend.get_frame().set_facecolor("#ffffff")
    legend.get_frame().set_edgecolor("#e2e8f0")
    _expand_y_limits(ax_boost, [p_rect_kw, p_out_kw], pad_ratio=0.12, min_pad_ratio=0.03)

    ax_speed.plot(time_minutes[sl], omega_mpp[sl], "--", color="#f59e0b", linewidth=0.84, label="理论转速")
    ax_speed.plot(time_minutes[sl], omega[sl], color="#2563eb", linewidth=0.92, label="实际转速")
    ax_speed.set_ylabel("rad/s", fontsize=9.6, color="#0f172a")
    legend = ax_speed.legend(loc="lower left", fontsize=8.5, frameon=True, ncol=2)
    legend.get_frame().set_facecolor("#ffffff")
    legend.get_frame().set_edgecolor("#e2e8f0")
    _expand_y_limits(ax_speed, [omega_mpp, omega], pad_ratio=0.12, min_pad_ratio=0.03)

    ax_error.plot(time_minutes[sl], speed_error[sl], color="#7c3aed", linewidth=0.86, label="实际ω - 理论ω")
    ax_error.axhline(0.0, color="#94a3b8", linewidth=0.72, linestyle=":")
    ax_error.set_ylabel("误差", fontsize=9.6, color="#0f172a")
    ax_error.set_xlabel("时间 / min", fontsize=10.0, color="#0f172a")
    legend = ax_error.legend(loc="lower right", fontsize=8.5, frameon=True)
    legend.get_frame().set_facecolor("#ffffff")
    legend.get_frame().set_edgecolor("#e2e8f0")
    _expand_y_limits(ax_error, [speed_error, np.array([0.0])], pad_ratio=0.16, min_pad_ratio=0.04)
    ax_error.text(
        0.985,
        0.92,
        (
            f"主导状态: {metrics['dominant_state']}\n"
            f"最大桨距 {float(metrics['max_pitch_deg']):.2f}° | 限功占比 {float(metrics['limit_active_ratio_pct']):.1f}%\n"
            f"平均Boost效率 {float(metrics['avg_boost_efficiency_pct']):.2f}% | 母线目标 {float(dc_bus_voltage):.0f} V"
        ),
        transform=ax_error.transAxes,
        ha="right",
        va="top",
        fontsize=8.1,
        color="#334155",
        linespacing=1.35,
        bbox={"boxstyle": "round,pad=0.26", "facecolor": "#ffffff", "edgecolor": "#e2e8f0", "alpha": 0.96},
    )

    x_max = float(time_minutes[-1]) if len(time_minutes) else 1.0
    for axis in axes:
        axis.set_xlim(0.0, x_max)
    fig.suptitle(title, fontsize=13.0, fontweight="semibold", color="#0f172a", y=0.985)
    return _finish_validation_figure(fig, path)


def _finish_validation_figure(fig, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.subplots_adjust(left=0.09, right=0.95, top=0.94, bottom=0.08, hspace=0.14)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return str(path)


def run_operating_condition_validation(
    *,
    turbine_params: WindTurbineParams,
    controller_dt: float,
    dc_bus_voltage: float,
    output_dir: str | Path | None = None,
    duration_s: float = 15.0 * 60.0,
) -> tuple[list[str], list[dict[str, float | str]]]:
    if not HAS_MATPLOTLIB:
        return [], []

    plot_dir = Path(output_dir) if output_dir else RESULTS_DIR
    plot_dir.mkdir(parents=True, exist_ok=True)
    time_seconds = np.arange(0.0, duration_s + controller_dt * 0.5, controller_dt, dtype=float)
    # 获取所有验证工况定义，包括风速锚点、空气密度锚点和文字说明。
    definitions = _wind_validation_scenarios(duration_s)
    paths: list[str] = []
    summaries: list[dict[str, float | str]] = []

    # 按照工况定义顺序逐个仿真和绘图；index 同时用于匹配输出文件名。
    for index, spec in enumerate(definitions):
        wind_profile_mean = _scenario_profile_from_anchors(
            time_seconds,
            list(spec["wind_anchor_minutes"]),
            list(spec["wind_anchor_values"]),
            min_value=0.0,
        )
        wind_profile = apply_validation_wind_perturbation(
            wind_profile_mean,
            controller_dt,
            turbine_params,
            seed_offset=index + 1,
        )
        density_profile = _scenario_profile_from_anchors(
            time_seconds,
            list(spec["air_density_anchor_minutes"]),
            list(spec["air_density_anchor_values"]),
            min_value=0.3,
        )
        results = _simulate_validation_profile(
            time_seconds=time_seconds,
            wind_profile=wind_profile,
            density_profile=density_profile,
            turbine_params=turbine_params,
            controller_dt=controller_dt,
            dc_bus_voltage=dc_bus_voltage,
        )
        metrics = _validation_metrics(results)
        summaries.append(
            {
                "key": str(spec["key"]),
                "title": str(spec["title"]),
                "purpose": str(spec.get("purpose", "")),
                "duration_minutes": float(duration_s / 60.0),
                "wind_anchor_minutes": [float(value) for value in spec["wind_anchor_minutes"]],
                "wind_anchor_values": [float(value) for value in spec["wind_anchor_values"]],
                "air_density_anchor_minutes": [float(value) for value in spec["air_density_anchor_minutes"]],
                "air_density_anchor_values": [float(value) for value in spec["air_density_anchor_values"]],
                **metrics,
            }
        )
        paths.append(
            _plot_operating_condition_result(
                str(spec["title"]),
                results,
                metrics,
                plot_dir / SCENARIO_VALIDATION_FILENAMES[index],
                dc_bus_voltage,
            )
        )

    return paths, summaries



def generate_scenario_validation_plots(
    *,
    controller_dt: float = DEFAULT_CONTROLLER_DT,
    dc_bus_voltage: float = DEFAULT_DC_BUS_VOLTAGE,
    turbine_params: WindTurbineParams | None = None,
    output_dir: str | Path | None = None,
) -> dict:
    #生成工况验证图和指标摘要，供 GUI 或命令行调用
    paths, metrics = run_operating_condition_validation(
        turbine_params=turbine_params or WindTurbineParams(),
        controller_dt=controller_dt,
        dc_bus_voltage=dc_bus_voltage,
        output_dir=output_dir or RESULTS_DIR,
    )
    return {"scenario_validation_paths": paths, "scenario_validation_metrics": metrics}



def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate wind condition-validation plots.")
    parser.add_argument("--controller-dt", type=float, default=DEFAULT_CONTROLLER_DT)
    parser.add_argument("--dc-bus-voltage", type=float, default=DEFAULT_DC_BUS_VOLTAGE)
    args = parser.parse_args()

    result = generate_scenario_validation_plots(
        controller_dt=args.controller_dt,
        dc_bus_voltage=args.dc_bus_voltage,
    )
    paths = result.get("scenario_validation_paths", [])
    print(f"Wind: generated {len(paths)} condition-validation figure(s).")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
