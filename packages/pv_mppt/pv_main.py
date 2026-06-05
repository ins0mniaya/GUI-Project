from __future__ import annotations

import time as wall_clock
from pathlib import Path

import numpy as np

from .irr_tem_extract import get_predicted_irr_temp_future_frame
from .pv_control import (
    BoostConverter,
    DEFAULT_MPPT_V_REF_MAX,
    DEFAULT_MPPT_V_REF_MIN,
    DEFAULT_MPPT_V_REF_STEP,
    POMPPTController,
    VoltagePIController,
    sapm_cell_temperature_target,
)
from .pv_module_params import PVCellModel, PVModuleParams
from .pv_plotter import (
    HAS_MATPLOTLIB,
    cleanup_previous_plot_outputs,
    get_plot_paths,
    plot_control_results,
    plot_validation_results,
    run_operating_condition_validation,
)

DEFAULT_CONTROLLER_DT = 0.1
DEFAULT_PREDICTION_INTERP_DT = 1.0
DEFAULT_MPPT_SAMPLE_PERIOD = 1.0
DEFAULT_MPPT_ENABLE_IRRADIANCE = 20.0
DEFAULT_DC_BUS_VOLTAGE = 370.0
DEFAULT_MPP_SEARCH_POINTS = 240
DEFAULT_BOOST_ELECTRICAL_DT = 0.005
DEFAULT_PV_TEMPERATURE_WIND_SPEED = 1.5

DEFAULT_IRRADIANCE_VARIABILITY_INTENSITY = 0.06
DEFAULT_IRRADIANCE_VARIABILITY_TIME_CONSTANT = 35.0
DEFAULT_IRRADIANCE_VARIABILITY_MAX_FRACTION = 0.18
DEFAULT_IRRADIANCE_VARIABILITY_MAX_W_M2 = 120.0
DEFAULT_TEMPERATURE_VARIABILITY_STD_C = 0.12
DEFAULT_PV_VARIABILITY_SEED = 202406

RESULTS_DIR = Path(__file__).resolve().parent / "rescults"


def _callback_requested(callback) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return False


def _pause_requested(pause_callback) -> bool:
    return _callback_requested(pause_callback)


def _stop_requested(stop_callback) -> bool:
    return _callback_requested(stop_callback)


def _wait_while_paused(pause_callback) -> float:
    pause_start = None
    while _pause_requested(pause_callback):
        if pause_start is None:
            pause_start = wall_clock.monotonic()
        wall_clock.sleep(0.05)
    if pause_start is None:
        return 0.0
    return wall_clock.monotonic() - pause_start


def infer_weather_step_seconds(frame) -> float:
    if len(frame) >= 2:
        diffs = frame["timestamp"].diff().dt.total_seconds().to_numpy(dtype=float)
        valid = diffs[np.isfinite(diffs) & (diffs > 0.0)]
        if len(valid) > 0:
            return float(np.median(valid))
    return 15.0 * 60.0


def _second_level_series(start_value: float, end_value: float, source_step: float) -> tuple[np.ndarray, np.ndarray]:
    """把相邻预测点按 1 s 粒度线性插值，控制器小步长运行时再读取秒级输入。"""
    interp_dt = max(float(DEFAULT_PREDICTION_INTERP_DT), 1e-9)
    source_step = max(float(source_step), interp_dt)
    times = np.arange(0.0, source_step + interp_dt * 0.5, interp_dt, dtype=float)
    times = times[times <= source_step + 1e-9]
    if len(times) == 0 or times[-1] < source_step - 1e-9:
        times = np.append(times, source_step)
    fraction = np.clip(times / max(source_step, 1e-9), 0.0, 1.0)
    values = float(start_value) + (float(end_value) - float(start_value)) * fraction
    return times, values


def estimate_model_mpp(pv: PVCellModel, G: float, T_cell: float) -> tuple[float, float]:
    if G <= 0.0:
        return pv.params.Vmp, 0.0
    v_mpp, _, p_mpp = pv.get_mpp(G, T_cell, num_points=DEFAULT_MPP_SEARCH_POINTS)
    return float(v_mpp), max(float(p_mpp), 0.0)


def initialize_mppt_controller(
    pv: PVCellModel,
    V_init: float,
    G: float,
    T_cell: float,
    v_min: float,
    v_max: float,
) -> tuple[POMPPTController, float]:
    v_ref = float(np.clip(V_init, v_min, v_max))
    mppt = POMPPTController(
        V_init=v_ref,
        delta_V=DEFAULT_MPPT_V_REF_STEP,
        sample_period=DEFAULT_MPPT_SAMPLE_PERIOD,
        V_min=v_min,
        V_max=v_max,
    )
    mppt.P_prev = v_ref * float(pv.current_at(G, T_cell, v_ref))
    mppt.V_prev = v_ref
    return mppt, v_ref


def _boost_hardware_loss_w(boost: BoostConverter, duty: float, i_l: float) -> float:
    # 根据 Boost 器件参数估算硬件转换损耗
    duty = float(np.clip(duty, 0.0, 0.95))
    k = 1.0 - duty
    current = max(float(i_l), 0.0)
    conduction_resistance = (
        boost.inductor_resistance
        + duty * boost.switch_resistance
        + k * boost.diode_resistance
    )
    conduction_loss = conduction_resistance * current * current
    diode_drop_loss = k * boost.diode_drop * current
    return max(float(conduction_loss + diode_drop_loss), 0.0)


# 在一个控制周期内，将 Boost 电路按更小电气步长细分计算
# 避免控制步长较大时电感/电容状态更新过大导致估算误差
def step_boost_controller_interval(
    boost: BoostConverter,
    pv: PVCellModel,
    G: float,
    T_cell: float,
    duty: float,
    controller_dt: float,
) -> tuple[float, float, float, float, float, float]:
    substeps = max(1, int(np.ceil(controller_dt / DEFAULT_BOOST_ELECTRICAL_DT)))
    sub_dt = controller_dt / substeps
    p_pv_sum = 0.0
    p_out_sum = 0.0

    for _ in range(substeps):
        v_pv_before = boost.v_pv
        v_out_before = boost.v_C
        i_l_before = boost.i_L
        i_pv_before = float(pv.current_at(G, T_cell, v_pv_before))
        i_out_before = max((1.0 - duty) * i_l_before, 0.0)
        loss_before = _boost_hardware_loss_w(boost, duty, i_l_before)

        v_pv, i_pv, i_l, v_out = boost.step(pv, G, T_cell, duty, sub_dt)

        i_out_after = max((1.0 - duty) * i_l, 0.0)
        loss_after = _boost_hardware_loss_w(boost, duty, i_l)
        p_pv_avg = 0.5 * max(v_pv_before * i_pv_before, 0.0) + 0.5 * max(v_pv * i_pv, 0.0)
        p_out_avg = 0.5 * max(v_out_before * i_out_before, 0.0) + 0.5 * max(v_out * i_out_after, 0.0)
        hardware_loss_avg = 0.5 * (loss_before + loss_after)
        available_after_loss = max(p_pv_avg - hardware_loss_avg, 0.0)
        p_pv_sum += p_pv_avg
        p_out_sum += min(p_out_avg, available_after_loss)

    return v_pv, i_pv, i_l, v_out, p_pv_sum / substeps, p_out_sum / substeps


# 辐照度和温度扰动模型：在 15 分钟预测均值上叠加小幅随机波动
class PVIrradianceVariability:
    def __init__(
        self,
        controller_dt: float,
        intensity: float = DEFAULT_IRRADIANCE_VARIABILITY_INTENSITY,
        time_constant: float = DEFAULT_IRRADIANCE_VARIABILITY_TIME_CONSTANT,
        max_fraction: float = DEFAULT_IRRADIANCE_VARIABILITY_MAX_FRACTION,
        max_w_m2: float = DEFAULT_IRRADIANCE_VARIABILITY_MAX_W_M2,
        temperature_std_c: float = DEFAULT_TEMPERATURE_VARIABILITY_STD_C,
        seed: int = DEFAULT_PV_VARIABILITY_SEED,
    ):
        self.controller_dt = max(float(controller_dt), 0.0)
        self.intensity = max(float(intensity), 0.0)
        self.time_constant = max(float(time_constant), max(self.controller_dt, 1e-6))
        self.max_fraction = max(float(max_fraction), 0.0)
        self.max_w_m2 = max(float(max_w_m2), 0.0)
        self.temperature_std_c = max(float(temperature_std_c), 0.0)
        self.rng = np.random.default_rng(int(seed))
        self.g_fluctuation = 0.0
        self.t_fluctuation = 0.0

    def step(self, irradiance: float, temperature: float) -> tuple[float, float]:
        base_g = max(float(irradiance), 0.0)
        phi = float(np.exp(-self.controller_dt / self.time_constant))
        innovation = float(np.sqrt(max(1.0 - phi * phi, 0.0)))

        self.g_fluctuation = phi * self.g_fluctuation + innovation * self.intensity * base_g * float(self.rng.normal())
        g_limit = min(self.max_fraction * max(base_g, 1.0), self.max_w_m2)
        g_delta = float(np.clip(self.g_fluctuation, -g_limit, g_limit))

        self.t_fluctuation = phi * self.t_fluctuation + innovation * self.temperature_std_c * float(self.rng.normal())
        t_delta = float(np.clip(self.t_fluctuation, -0.5, 0.5))
        return max(base_g + g_delta, 0.0), float(temperature) + t_delta


# 单步光伏控制运行器：把 PV 模型、MPPT、电压 PI 和 Boost 电路组合在一起
class SampledPVControlRunner:
    def __init__(
        self,
        params: PVModuleParams,
        controller_dt: float,
        dc_bus_voltage: float,
        mppt_enable_irradiance: float = DEFAULT_MPPT_ENABLE_IRRADIANCE,
    ) -> None:
        self.params = params
        self.pv = PVCellModel(params)
        self.controller_dt = float(controller_dt)
        self.dc_bus_voltage = float(dc_bus_voltage)
        self.mppt_enable_irradiance = float(mppt_enable_irradiance)
        self.v_min = DEFAULT_MPPT_V_REF_MIN
        self.v_max = DEFAULT_MPPT_V_REF_MAX
        self.v_ref = float(np.clip(params.Vmp, self.v_min, self.v_max))
        self.mppt, self.v_ref = initialize_mppt_controller(self.pv, self.v_ref, 1000.0, 25.0, self.v_min, self.v_max)
        self.voltage_controller = VoltagePIController(kp=0.0015, ki=0.02, duty_rate_limit=1.0)
        self.voltage_controller.reset(1.0 - self.v_ref / max(self.dc_bus_voltage, self.v_ref + 1.0))
        self.boost = BoostConverter(
            R_load=self.dc_bus_voltage**2 / max(params.Pmax, 1.0),
            dc_bus_voltage=self.dc_bus_voltage,
            stiff_bus=True,
            bus_regulation_tau=0.03,
            input_leakage_resistance=12000.0,
        )
        self.boost.initialize(v_pv=self.v_ref, i_L=0.0, v_out=self.dc_bus_voltage)
        self.duty = self.voltage_controller.duty
        self.mppt_period_steps = max(1, int(round(DEFAULT_MPPT_SAMPLE_PERIOD / max(self.controller_dt, 1e-9))))
        self.mppt_enabled_prev = True
        self.mpp_cache: dict[tuple[float, float], tuple[float, float]] = {}
        self.sample_index = 0
        self.prev_time: float | None = None
        self.t_cell: float | None = None

    def step(self, time_s: float, G: float, ambient_T: float) -> dict[str, float]:
        G_i = max(float(G), 0.0)
        ambient_T_i = float(ambient_T)
        T_cell_i = self._update_cell_temperature(float(time_s), G_i, ambient_T_i)
        v_mpp, p_mpp = self._mpp(G_i, T_cell_i)

        if G_i < self.mppt_enable_irradiance:
            self.mppt_enabled_prev = False
            self.duty = 0.0
            return self._advance(G_i, ambient_T_i, T_cell_i, p_mpp, 0.0)

        if not self.mppt_enabled_prev:
            self.mppt, self.v_ref = initialize_mppt_controller(self.pv, self.boost.v_pv, G_i, T_cell_i, self.v_min, self.v_max)
            self.voltage_controller.reset(1.0 - self.v_ref / max(self.dc_bus_voltage, self.v_ref + 1.0))
            self.duty = self.voltage_controller.duty
            self.mppt_enabled_prev = True

        self.duty = self.voltage_controller.update(self.v_ref, self.boost.v_pv, self.boost.v_C, self.controller_dt)
        row = self._advance(G_i, ambient_T_i, T_cell_i, p_mpp, self.v_ref)
        if self.sample_index % self.mppt_period_steps == 0 and self.sample_index > 0:
            self.v_ref = float(self.mppt.update(row["V_pv"], row["I_pv"]))
            self.mppt.V_ref = self.v_ref
        return row

    def _mpp(self, G: float, T_cell: float) -> tuple[float, float]:
        if G <= 0.0:
            return self.params.Vmp, 0.0
        key = (round(G, 1), round(T_cell, 2))
        if key not in self.mpp_cache:
            self.mpp_cache[key] = estimate_model_mpp(self.pv, G, T_cell)
        return self.mpp_cache[key]

    def _advance(self, G: float, ambient_T: float, T_cell: float, p_mpp: float, v_ref: float) -> dict[str, float]:
        V_pv, I_pv, I_L, V_out, P_pv, P_boost = step_boost_controller_interval(
            self.boost,
            self.pv,
            G,
            T_cell,
            self.duty,
            self.controller_dt,
        )
        efficiency = 0.0 if P_pv <= 1.0 else min(P_boost / P_pv, 1.0)
        self.sample_index += 1
        return {
            "G": float(G),
            "T_ambient": float(ambient_T),
            "T_cell": float(T_cell),
            "V_pv": float(V_pv),
            "I_pv": float(I_pv),
            "P_pv": float(P_pv),
            "P_mpp": float(p_mpp),
            "P_boost_out": float(P_boost),
            "boost_efficiency": float(efficiency),
            "V_ref": float(v_ref),
            "V_out": float(V_out),
            "I_L": float(I_L),
            "duty": float(self.duty),
        }

    def _update_cell_temperature(self, time_s: float, G: float, ambient_T: float) -> float:
        target = sapm_cell_temperature_target(G, ambient_T, DEFAULT_PV_TEMPERATURE_WIND_SPEED)
        if self.t_cell is None:
            self.t_cell = float(target)
        else:
            dt = self.controller_dt if self.prev_time is None else max(float(time_s) - self.prev_time, 0.0)
            self.t_cell = float(self.t_cell + (1.0 - np.exp(-dt / 300.0)) * (target - self.t_cell))
        self.prev_time = float(time_s)
        return float(self.t_cell)


def _empty_records() -> dict[str, list[float]]:
    keys = [
        "time",
        "physical_time",
        "pred_G",
        "pred_T_ambient",
        "T_cell",
        "V_pv",
        "V_ref",
        "I_pv",
        "P_pv",
        "P_mpp",
        "P_boost_out",
        "boost_efficiency",
        "V_out",
        "I_L",
        "duty",
        "array_energy_kwh",
        "boost_energy_kwh",
    ]
    return {key: [] for key in keys}


def _records_to_arrays(records: dict[str, list[float]]) -> dict[str, np.ndarray]:
    return {key: np.asarray(value, dtype=float) for key, value in records.items()}


def _slice_records(records: dict[str, list[float]], start: int) -> dict[str, list[float]]:
    return {key: list(value[start:]) for key, value in records.items()}


def _sim_from_records(records: dict[str, list[float]]) -> dict[str, np.ndarray]:
    arrays = _records_to_arrays(records)
    return {
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


def _append_csv_rows(csv_file, records: dict[str, list[float]], start: int) -> int:
    if csv_file is None or len(records["time"]) <= start:
        return start
    arrays = _records_to_arrays(records)
    rows = np.column_stack(
        [
            arrays["time"][start:],
            arrays["physical_time"][start:],
            arrays["pred_G"][start:],
            arrays["pred_T_ambient"][start:],
            arrays["T_cell"][start:],
            arrays["V_pv"][start:],
            arrays["V_ref"][start:],
            arrays["I_pv"][start:],
            arrays["P_pv"][start:],
            arrays["P_mpp"][start:],
            arrays["P_boost_out"][start:],
            arrays["boost_efficiency"][start:],
            arrays["V_out"][start:],
            arrays["I_L"][start:],
            arrays["duty"][start:],
            arrays["array_energy_kwh"][start:],
            arrays["boost_energy_kwh"][start:],
        ]
    )
    np.savetxt(csv_file, rows, delimiter=",", fmt="%.8g")
    csv_file.flush()
    return len(records["time"])


def _extract_interval_values(row, previous_true_G: float | None, previous_true_T: float | None):
    pred_G = max(float(row.get("pred_irradiance", 0.0)), 0.0)
    pred_T = float(row.get("pred_temperature", 25.0))
    start_G = max(float(row.get("start_irradiance", pred_G)), 0.0) if previous_true_G is None else max(previous_true_G, 0.0)
    start_T = float(row.get("start_temperature", pred_T)) if previous_true_T is None else float(previous_true_T)
    true_G = max(float(row.get("true_irradiance", pred_G)), 0.0)
    true_T = float(row.get("true_temperature", pred_T))
    return start_G, start_T, true_G, true_T


# 从滚动预测 DataFrame 出发执行光伏 MPPT + Boost 仿真
# 该函数同时负责实时回调、CSV 增量写入和当前 15 分钟窗口绘图刷新
def run_rolling_control_simulation_from_frame(
    future_frame,
    params: PVModuleParams,
    controller_dt: float,
    dc_bus_voltage: float,
    mppt_enable_irradiance: float = DEFAULT_MPPT_ENABLE_IRRADIANCE,
    output_csv: str | None = None,
    progress_callback=None,
    plot_update_interval_seconds: float | None = 1.0,
    pause_callback=None,
    stop_callback=None,
    real_time_playback: bool = True,
    playback_speed: float = 1.0,
) -> tuple[dict[str, list[float]], dict[str, np.ndarray], np.ndarray, np.ndarray, list[str]]:
    # 原始气象预测点先按 1 s 插值；控制器小步长运行时读取对应秒级输入
    source_step = infer_weather_step_seconds(future_frame)
    local_times = np.arange(0.0, max(source_step, controller_dt), controller_dt, dtype=float)
    if len(local_times) == 0:
        local_times = np.array([0.0], dtype=float)

    runner = SampledPVControlRunner(params, controller_dt, dc_bus_voltage, mppt_enable_irradiance)
    variability = PVIrradianceVariability(controller_dt)
    records = _empty_records()
    latest_plot_paths: list[str] = []
    csv_file = None
    csv_index = 0
    previous_true_G = None
    previous_true_T = None
    current_window_start = 0
    last_plot_wall_time = wall_clock.monotonic()
    last_plot_physical_time = 0.0
    wall_start_time = wall_clock.monotonic()
    playback_speed = max(float(playback_speed), 1e-6)

    def wait_for_resume() -> None:
        nonlocal wall_start_time
        if _stop_requested(stop_callback):
            return
        paused_seconds = _wait_while_paused(pause_callback)
        if paused_seconds > 0.0:
            wall_start_time += paused_seconds

    def pace(physical_time: float) -> bool:
        nonlocal wall_start_time
        if not real_time_playback:
            return not _stop_requested(stop_callback)
        target_elapsed = max(float(physical_time), 0.0) / playback_speed
        while True:
            if _stop_requested(stop_callback):
                return False
            wait_for_resume()
            remaining = target_elapsed - (wall_clock.monotonic() - wall_start_time)
            if remaining <= 0.0:
                return True
            wall_clock.sleep(min(remaining, 0.05))

    def publish(weather_step: int, row) -> None:
        nonlocal latest_plot_paths, csv_index, last_plot_wall_time, last_plot_physical_time
        if not records["time"]:
            return
        window_records = _slice_records(records, current_window_start)
        arrays_all = _records_to_arrays(records)
        arrays_window = _records_to_arrays(window_records)
        if HAS_MATPLOTLIB:
            window_start_time = float(arrays_window["physical_time"][0])
            latest_plot_paths = plot_control_results(
                arrays_window["time"] - window_start_time,
                _sim_from_records(window_records),
                dc_bus_voltage,
                physical_time=arrays_window["physical_time"] - window_start_time,
                display_window_minutes=source_step / 60.0,
            )
        csv_index = _append_csv_rows(csv_file, records, csv_index)
        if progress_callback is not None:
            progress_callback(
                {
                    "step": weather_step + 1,
                    "total_steps": len(future_frame),
                    "timestamp": str(row.get("timestamp", "")),
                    "weather_points": weather_step + 1,
                    "current_window_minutes": float(arrays_window["physical_time"][-1] - arrays_window["physical_time"][0]) / 60.0,
                    "energy_kwh": float(np.sum(arrays_all["boost_energy_kwh"])),
                    "peak_power_w": float(np.max(arrays_window["P_boost_out"])),
                    "mean_power_w": float(np.mean(arrays_window["P_boost_out"])),
                    "plot_paths": latest_plot_paths,
                    "output_csv": output_csv,
                }
            )
        last_plot_wall_time = wall_clock.monotonic()
        last_plot_physical_time = float(arrays_all["physical_time"][-1])

    def should_publish(physical_time: float) -> bool:
        if plot_update_interval_seconds is None:
            return False
        interval = max(float(plot_update_interval_seconds), 0.0)
        physical_due = (float(physical_time) - last_plot_physical_time) >= interval - 1e-9
        wall_due = (wall_clock.monotonic() - last_plot_wall_time) >= interval - 1e-9
        return physical_due and wall_due

    try:
        if output_csv:
            csv_file = open(output_csv, "w", encoding="utf-8", newline="")
            csv_file.write(
                "time_s,physical_time_s,pred_G_w_m2,pred_T_ambient_degC,"
                "T_cell_degC,V_pv_v,V_ref_v,I_pv_a,P_pv_w,P_mpp_w,"
                "P_boost_out_w,boost_efficiency,V_out_v,I_L_a,duty,array_energy_kwh,boost_energy_kwh\n"
            )

        for weather_step, (_, row) in enumerate(future_frame.iterrows()):
            if _stop_requested(stop_callback):
                break
            wait_for_resume()
            current_window_start = len(records["time"])
            start_G, start_T, end_G, end_T = _extract_interval_values(row, previous_true_G, previous_true_T)
            physical_base = weather_step * source_step
            interp_times, interp_G = _second_level_series(start_G, end_G, source_step)
            _, interp_T = _second_level_series(start_T, end_T, source_step)

            for local_time in local_times:
                if _stop_requested(stop_callback):
                    break
                wait_for_resume()
                interp_idx = min(int(np.floor(float(local_time) / max(DEFAULT_PREDICTION_INTERP_DT, 1e-9))), len(interp_times) - 1)
                G_i = max(float(interp_G[interp_idx]), 0.0)
                T_i = float(interp_T[interp_idx])
                G_i, T_i = variability.step(G_i, T_i)
                physical_time = physical_base + float(local_time)
                if not pace(physical_time):
                    break

                sim_row = runner.step(physical_time, G_i, T_i)
                records["time"].append(physical_time)
                records["physical_time"].append(physical_time)
                records["pred_G"].append(G_i)
                records["pred_T_ambient"].append(T_i)
                for key in ("T_cell", "V_pv", "V_ref", "I_pv", "P_pv", "P_mpp", "P_boost_out", "boost_efficiency", "V_out", "I_L", "duty"):
                    records[key].append(sim_row[key])
                dt_hours = float(controller_dt) / 3600.0
                records["array_energy_kwh"].append(sim_row["P_pv"] * dt_hours / 1000.0)
                records["boost_energy_kwh"].append(sim_row["P_boost_out"] * dt_hours / 1000.0)

                if (len(records["time"]) == 2 and not latest_plot_paths) or should_publish(physical_time):
                    publish(weather_step, row)

            previous_true_G = end_G
            previous_true_T = end_T

        if len(future_frame) > 0:
            publish(len(future_frame) - 1, future_frame.iloc[-1])
        csv_index = _append_csv_rows(csv_file, records, csv_index)
    finally:
        if csv_file is not None:
            csv_file.close()

    arrays = _records_to_arrays(records)
    return records, _sim_from_records(records), arrays["time"], arrays["physical_time"], latest_plot_paths

# GUI 和上层模块调用的主入口：读取预测天气、执行滚动控制仿真
# 并汇总能量、功率、图片路径和工况验证结果
def run_long_term_simulation(
    controller_dt: float = DEFAULT_CONTROLLER_DT,
    max_weather_steps: int | None = None,
    forecast_days: float = 0.0,
    output_csv: str | None = None,
    data_path: str | None = None,
    irradiance_checkpoint_path: str | None = None,
    temperature_checkpoint_path: str | None = None,
    pv_params: PVModuleParams | None = None,
    dc_bus_voltage: float = DEFAULT_DC_BUS_VOLTAGE,
    mppt_enable_irradiance: float = DEFAULT_MPPT_ENABLE_IRRADIANCE,
    progress_callback=None,
    plot_update_interval_seconds: float | None = 1.0,
    pause_callback=None,
    stop_callback=None,
    rolling_start_offset: int = 0,
    real_time_playback: bool = True,
    playback_speed: float = 1.0,
    generate_validation_outputs: bool = True,
) -> dict:
    params = pv_params or PVModuleParams()
    cleanup_previous_plot_outputs(RESULTS_DIR)
    if progress_callback is not None:
        progress_callback({"step": 0, "total_steps": int(max_weather_steps or 0), "plot_paths": [], "output_csv": output_csv})

    future_frame = get_predicted_irr_temp_future_frame(
        data_path=data_path,
        irradiance_checkpoint_path=irradiance_checkpoint_path,
        temperature_checkpoint_path=temperature_checkpoint_path,
        max_steps=max_weather_steps,
        forecast_days=forecast_days,
        start_offset=rolling_start_offset,
    )

    records, sim, time, physical_time, latest_plot_paths = run_rolling_control_simulation_from_frame(
        future_frame=future_frame,
        params=params,
        controller_dt=controller_dt,
        dc_bus_voltage=dc_bus_voltage,
        mppt_enable_irradiance=mppt_enable_irradiance,
        output_csv=output_csv,
        progress_callback=progress_callback,
        plot_update_interval_seconds=plot_update_interval_seconds,
        pause_callback=pause_callback,
        stop_callback=stop_callback,
        real_time_playback=real_time_playback,
        playback_speed=playback_speed,
    )

    arrays = _records_to_arrays(records)
    total_energy = float(np.sum(arrays["boost_energy_kwh"])) if len(time) else 0.0
    array_energy = float(np.sum(arrays["array_energy_kwh"])) if len(time) else 0.0
    peak_power = float(np.max(sim["P_boost_out"])) if len(time) else 0.0
    mean_power = float(np.mean(sim["P_boost_out"])) if len(time) else 0.0
    latest_irradiance = float(future_frame["pred_irradiance"].iloc[-1]) if len(future_frame) else 0.0
    latest_temperature = float(future_frame["pred_temperature"].iloc[-1]) if len(future_frame) else 0.0
    validation_plot_paths: list[str] = []
    scenario_validation_paths: list[str] = []
    scenario_validation_metrics: list[dict[str, float | str]] = []

    if generate_validation_outputs and HAS_MATPLOTLIB and len(time):
        validation_plot_paths = plot_validation_results(
            arrays,
            sim,
            time,
            physical_time=physical_time,
            output_dir=RESULTS_DIR,
        )
        scenario_validation_paths, scenario_validation_metrics = run_operating_condition_validation(
            params=params,
            controller_dt=controller_dt,
            dc_bus_voltage=dc_bus_voltage,
            mppt_enable_irradiance=mppt_enable_irradiance,
            output_dir=RESULTS_DIR,
        )

    return {
        "energy_kwh": total_energy,
        "array_energy_kwh": array_energy,
        "peak_power_w": peak_power,
        "mean_power_w": mean_power,
        "weather_points": int(len(future_frame)),
        "latest_irradiance": latest_irradiance,
        "latest_temperature": latest_temperature,
        "output_csv": output_csv,
        "plot_paths": latest_plot_paths or get_plot_paths(RESULTS_DIR),
        "validation_plot_paths": validation_plot_paths,
        "scenario_validation_paths": scenario_validation_paths,
        "scenario_validation_metrics": scenario_validation_metrics,
        "rolling_prediction": True,
    }
