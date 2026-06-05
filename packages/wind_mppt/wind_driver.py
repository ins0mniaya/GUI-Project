"""
本文件包含风机气动、桨距控制、MPPT 控制、PMSG 整流、Boost 升压、
系统级仿真器以及 CSV 导出辅助函数
1. 公共辅助函数与结果缓存区
2. 风机空气动力学模型
3. 桨距控制与最优转矩 MPPT 控制
4. 发电机、整流器和 Boost 变换器等效模型
5. 系统级动态仿真器
6. 结果统计与 CSV 导出函数
"""
from __future__ import annotations
import csv
import os
import time as wall_clock
from typing import Optional

import numpy as np

from .wind_parameter import GeneratorParams, WindTurbineParams

# ------------------------------------公共辅助函数与结果缓存区-----------------------------------------
# 仿真过程中需要连续记录的主要状态量与功率量。
_RESULT_SERIES_KEYS = (
    "omega", "omega_mpp", "lambda", "lambda_opt", "Cp", "Cp_opt", "k_opt",
    "pitch_angle", "P_aero", "P_mpp", "T_aero", "T_ref", "T_cmd", "T_gen",
    "V_rect", "I_rect", "P_rect", "P_gen_loss", "V_out", "I_L", "P_out",
    "duty", "boost_efficiency",
)

_EXPORT_COLUMNS = (
    "time", "wind_speed", "air_density", "omega", "omega_mpp", "lambda",
    "lambda_opt", "Cp", "Cp_opt", "k_opt", "pitch_angle", "P_mpp",
    "P_aero", "P_rect", "P_out", "P_gen_loss", "V_rect", "I_rect",
    "V_out", "I_L", "duty", "boost_efficiency", "state",
)

def _stop_requested(stop_callback) -> bool:
    if stop_callback is None:
        return False
    try:
        return bool(stop_callback())
    except Exception as exc:
        print(f"[警告] 停止回调执行失败: {exc}")
        return False


def _pause_requested(pause_callback) -> bool:
    if pause_callback is None:
        return False
    try:
        return bool(pause_callback())
    except Exception as exc:
        print(f"[警告] 暂停回调执行失败: {exc}")
        return False


def _wait_while_paused(pause_callback, stop_callback) -> float:
    pause_start = None
    while _pause_requested(pause_callback) and not _stop_requested(stop_callback):
        if pause_start is None:
            pause_start = wall_clock.monotonic()
        wall_clock.sleep(0.05)
    if pause_start is None:
        return 0.0
    return wall_clock.monotonic() - pause_start


def _trim_simulation_results(results: dict, stop_count: int, total_count: int) -> dict:
    stop_count = max(0, min(int(stop_count), int(total_count)))
    trimmed = {}
    for key, value in results.items():
        if isinstance(value, np.ndarray) and len(value) == total_count:
            trimmed[key] = value[:stop_count].copy()
        else:
            trimmed[key] = value
    trimmed["stopped"] = True
    trimmed["completed_steps"] = int(stop_count)
    trimmed["total_steps"] = int(total_count)
    return trimmed


def _new_result_buffer(time_array: np.ndarray, wind_array: np.ndarray, density: np.ndarray) -> dict:
    n = len(time_array)
    results = {
        "time": time_array,
        "wind_speed": wind_array,
        "air_density": density,
        **{key: np.zeros(n) for key in _RESULT_SERIES_KEYS},
        "state": np.empty(n, dtype=object),
    }
    return results

# 计算气动跟踪、发电机捕获和 Boost 传输三个功率链路比例。
def _power_chain_ratios(results: dict, min_mpp_power: float = 1.0) -> tuple[float, float, float]:
    valid = results["P_mpp"] > float(min_mpp_power)
    if not np.any(valid):
        return 0.0, 0.0, 0.0

    aero_tracking = np.sum(results["P_aero"][valid]) / max(np.sum(results["P_mpp"][valid]), 1e-9)
    generator_capture = np.sum(results["P_rect"][valid]) / max(np.sum(results["P_aero"][valid]), 1e-9)
    boost_efficiency = np.sum(results["P_out"][valid]) / max(np.sum(results["P_rect"][valid]), 1e-9)
    return float(aero_tracking), float(generator_capture), float(boost_efficiency)

# ------------------------------------风机空气动力学模型-----------------------------------------
# 基于 Cp(lambda, beta) 经验公式的风机空气动力学模型
class WindTurbineModel:
    def __init__(self, params: Optional[WindTurbineParams] = None):
        self.params = params or WindTurbineParams()
        self._optimal_cp_cache: dict[float, tuple[float, float]] = {}

    def cp(self, tip_speed_ratio: np.ndarray | float, pitch_angle: float = 0.0):
        # Cp 曲线由尖速比和桨距角共同决定，最终限制在 0~0.48 的物理范围内。
        beta = pitch_angle
        lam = np.maximum(tip_speed_ratio, 0.1)
        inv_lam_i = 1.0 / (lam + 0.08 * beta) - 0.035 / (beta**3 + 1.0)
        cp = 0.22 * (116.0 * inv_lam_i - 0.4 * beta - 5.0) * np.exp(-12.5 * inv_lam_i)
        return np.clip(cp, 0.0, 0.48)

    def optimal_cp_point(self, pitch_angle: float = 0.0) -> tuple[float, float]:
        # 对同一桨距角的最优 Cp 点做缓存，避免每个控制步重复搜索。
        pitch_key = round(pitch_angle, 2)
        if pitch_key in self._optimal_cp_cache:
            return self._optimal_cp_cache[pitch_key]
            
        p = self.params
        n_points = max(20, int(p.lambda_search_points))
        lam = np.linspace(p.lambda_search_min, p.lambda_search_max, n_points)
        cp_values = self.cp(lam, pitch_key)
        idx = int(np.argmax(cp_values))
        result = float(lam[idx]), float(cp_values[idx])
        self._optimal_cp_cache[pitch_key] = result
        return result

    def optimal_torque_coefficient(
        self,
        air_density: Optional[float] = None,
        pitch_angle: float = 0.0,
    ) -> tuple[float, float, float]:
        p = self.params
        rho = p.air_density if air_density is None else float(air_density)
        if p.use_dynamic_optimum:
            lambda_opt, cp_opt = self.optimal_cp_point(pitch_angle)
        else:
            lambda_opt, cp_opt = p.lambda_opt, p.cp_opt
        k_opt = (
            0.5
            * rho
            * p.swept_area
            * p.blade_radius**3
            * cp_opt
            / max(lambda_opt, 1e-6) ** 3
        )
        return float(k_opt), float(lambda_opt), float(cp_opt)

    def aerodynamic_point(self, wind_speed: float, omega: float,
                          air_density: Optional[float] = None,
                          pitch_angle: Optional[float] = None) -> tuple[float, float, float, float]:
        p = self.params
        if wind_speed > p.cut_out_speed:
            return 0.0, 0.0, 0.0, 0.0

        rho = p.air_density if air_density is None else air_density
        beta = p.pitch_angle if pitch_angle is None else pitch_angle
        yaw_factor = max(np.cos(np.deg2rad(p.yaw_misalignment_deg)), 0.0) ** 3
        omega_eff = max(float(omega), 0.0)
        tip_speed_ratio = omega_eff * p.blade_radius / max(wind_speed, 0.1)
        cp = float(self.cp(tip_speed_ratio, beta))
        if omega_eff < p.omega_min:
            startup_torque = (
                0.5
                * rho
                * p.swept_area
                * p.blade_radius
                * wind_speed**2
                * float(np.clip(p.startup_torque_coefficient, 0.0, 0.2))
                * yaw_factor
                * float(np.clip(p.aero_capture_efficiency, 0.0, 1.0))
            )
            return float(startup_torque), float(startup_torque * omega_eff), cp, tip_speed_ratio
        power = 0.5 * rho * p.swept_area * cp * wind_speed**3 * yaw_factor
        power *= float(np.clip(p.aero_capture_efficiency, 0.0, 1.0))
        torque = power / max(omega_eff, 1e-6)
        return torque, power, cp, tip_speed_ratio

    def get_mpp(
        self,
        wind_speed: float,
        air_density: Optional[float] = None,
        pitch_angle: float = 0.0,
        optimum: Optional[tuple[float, float, float]] = None,
        cap_rated: bool = True,
    ) -> tuple[float, float, float]:
        p = self.params
        rho = p.air_density if air_density is None else air_density
        yaw_factor = max(np.cos(np.deg2rad(p.yaw_misalignment_deg)), 0.0) ** 3
        if optimum is None:
            _, lambda_opt, cp_opt = self.optimal_torque_coefficient(rho, pitch_angle)
        else:
            _, lambda_opt, cp_opt = optimum
        omega_mpp = lambda_opt * wind_speed / p.blade_radius
        power_mpp = 0.5 * rho * p.swept_area * cp_opt * wind_speed**3 * yaw_factor
        if cap_rated:
            power_mpp = min(power_mpp, p.rated_power)
        torque_mpp = power_mpp / max(omega_mpp, p.omega_min)
        return float(omega_mpp), float(torque_mpp), float(power_mpp)

    def print_model_info(self):
        p = self.params
        print("\nWind turbine parameters:")
        print("  Rated branch: 2 kW-class wind generation connected to 370 V DC bus")
        print(f"  Blade radius = {p.blade_radius:.2f} m, swept area = {p.swept_area:.2f} m^2")
        print(f"  Rated power = {p.rated_power:.0f} W")
        print(f"  Cut-in/rated/cut-out speed = {p.cut_in_speed}/{p.rated_speed}/{p.cut_out_speed} m/s")
        print(f"  Hub/reference height = {p.hub_height:.1f}/{p.reference_wind_height:.1f} m")
        print(f"  Turbulence intensity = {p.turbulence_intensity:.2f}, wind time constant = {p.turbulence_time_constant:.1f} s")
        k0, lambda0, cp0 = self.optimal_torque_coefficient(p.air_density, p.pitch_angle)
        mode = "dynamic" if p.use_dynamic_optimum else "fixed"
        print(f"  MPPT optimum mode = {mode}")
        print(f"  nominal lambda_opt = {lambda0:.2f}, Cp_opt = {cp0:.3f}")
        print(f"  nominal k_opt = {k0:.5f}, torque limit = {p.torque_max:.1f} N*m")
        print(f"  lambda search range = {p.lambda_search_min:.1f} - {p.lambda_search_max:.1f}")
        print(f"  Pitch limit/rate = {p.pitch_max:.1f} deg / {p.pitch_rate_limit:.1f} deg/s")

# ----------------------------------------桨距控制与 MPPT 控制器---------------------------------------------
# 额定风速以上使用的桨距限功控制器，包含 PI 调节和变化率限制
class PitchController:
    def __init__(self, initial_pitch: float = 0.0):
        self.pitch_angle = initial_pitch
        self.integrator = 0.0

    def update(self, omega: float, aero_power: float, wind_speed: float,
               params: WindTurbineParams, dt: float) -> tuple[float, str]:
        speed_error = max((omega - params.omega_rated) / max(params.omega_rated, 1e-6), 0.0)
        power_error = max((aero_power - params.rated_power) / max(params.rated_power, 1e-6), 0.0)

        if wind_speed < params.rated_speed * 0.98 and power_error <= 0.01 and speed_error <= 0.01:
            self.integrator *= np.exp(-dt / 0.7)
            target_pitch = 0.0
            state = "below_rated"
        else:
            control_error = speed_error + 0.65 * power_error
            self.integrator = float(np.clip(self.integrator + control_error * dt, 0.0, 20.0))
            target_pitch = (
                params.pitch_kp * speed_error * 25.0
                + params.pitch_ki * self.integrator * 25.0
                + 10.0 * power_error
            )
            target_pitch = float(np.clip(target_pitch, 0.0, params.pitch_max))
            state = "pitch_limit" if target_pitch > 0.05 else "mppt"

        max_step = params.pitch_rate_limit * dt
        self.pitch_angle += float(np.clip(target_pitch - self.pitch_angle, -max_step, max_step))
        self.pitch_angle = float(np.clip(self.pitch_angle, 0.0, params.pitch_max))
        return self.pitch_angle, state

#最优转矩 MPPT 控制器，核心关系为 T_ref = k_opt * omega^2
class OptimalTorqueMPPT:
    def __init__(self, k_opt: float, sample_period: float = 0.01,
                 speed_filter_tau: float = 0.04,
                 speed_noise_std: float = 0.001,
                 seed: int = 23):
        self.k_opt = k_opt
        self.sample_period = sample_period
        self.torque_ref = 0.0
        self.speed_filter_tau = speed_filter_tau
        self.speed_noise_std = speed_noise_std
        self.omega_estimate: Optional[float] = None
        self.rng = np.random.default_rng(seed)

    def update(
        self,
        omega: float,
        wind_speed: float,
        params: WindTurbineParams,
        k_opt: Optional[float] = None,
    ) -> tuple[float, str]:
        if self.omega_estimate is None:
            self.omega_estimate = omega

        measured_omega = omega * (1.0 + self.rng.normal(0.0, self.speed_noise_std))
        alpha = self.sample_period / max(self.speed_filter_tau + self.sample_period, 1e-6)
        self.omega_estimate += alpha * (measured_omega - self.omega_estimate)
        omega_control = max(self.omega_estimate, params.omega_min)

        # 切入、切出和超速保护优先级高于 MPPT
        if wind_speed < params.cut_in_speed:
            self.torque_ref = 0.0
            return self.torque_ref, "below_cut_in"
        if wind_speed > params.cut_out_speed:
            self.torque_ref = 0.0
            return self.torque_ref, "cut_out"
        if omega >= params.omega_max:
            self.torque_ref = params.torque_max
            return self.torque_ref, "overspeed_brake"

        if k_opt is not None:
            self.k_opt = float(k_opt)

        mppt_torque = self.k_opt * omega_control**2
        rated_power_torque = params.rated_power / max(omega_control, params.omega_min)
        self.torque_ref = float(np.clip(min(mppt_torque, rated_power_torque), 0.0, params.torque_max))

        if rated_power_torque <= mppt_torque:
            return self.torque_ref, "rated_power_limit"
        return self.torque_ref, "mppt"

# ---------------------------------------发电机、整流器与 Boost 变换器等效模型------------------------------------
# 永磁同步发电机与二极管整流器的降阶等效模型
class PMSGRectifierEquivalent:
    def __init__(self, params: Optional[GeneratorParams] = None):
        self.params = params or GeneratorParams()
        self.phase_current = 0.0
        self.electrical_angle = 0.0
        self.last_losses = {
            "copper": 0.0,
            "iron": 0.0,
            "stray": 0.0,
            "rectifier": 0.0,
        }

    def output(self, omega: float, torque_command: float, dt: float) -> tuple[float, float, float, float, float]:
        p = self.params
        # 将发电机转矩指令换算成目标相电流，并用一阶滞后模拟电流建立过程
        target_current = torque_command / max(p.torque_constant, 1e-6)
        target_current = float(np.clip(target_current, 0.0, p.max_phase_current))
        current_alpha = min(dt / max(p.current_time_constant, dt), 1.0)
        self.phase_current += current_alpha * (target_current - self.phase_current)

        self.electrical_angle = (self.electrical_angle + p.pole_pairs * omega * dt) % (2.0 * np.pi)
        ripple = 1.0 + p.torque_ripple_ratio * np.sin(6.0 * self.electrical_angle)
        electromagnetic_torque = p.torque_constant * self.phase_current * ripple

        mechanical_power = max(0.0, electromagnetic_torque * omega * p.mechanical_efficiency)
        copper_loss = 3.0 * self.phase_current**2 * p.phase_resistance
        electrical_speed = p.pole_pairs * omega
        iron_loss = p.iron_loss_coeff * electrical_speed**2
        stray_loss = p.stray_loss_ratio * mechanical_power

        # 先估算空载整流电压，再依次扣除铜耗、铁耗、杂散损耗和整流损耗
        no_load_voltage = 1.35 * p.pole_pairs * p.flux_linkage * omega
        pre_rectifier_power = max(0.0, mechanical_power - copper_loss - iron_loss - stray_loss)
        estimated_dc_current = pre_rectifier_power / max(no_load_voltage, 1e-6)
        rectifier_loss = p.rectifier_drop * estimated_dc_current + p.rectifier_resistance * estimated_dc_current**2
        dc_power = max(0.0, pre_rectifier_power - rectifier_loss)
        dc_voltage = max(0.0, no_load_voltage - p.rectifier_drop - p.rectifier_resistance * estimated_dc_current)
        dc_current = dc_power / max(dc_voltage, 1e-6)

        loss_power = copper_loss + iron_loss + stray_loss + rectifier_loss
        loss_torque = iron_loss / max(omega, 1e-6)
        braking_torque = electromagnetic_torque + loss_torque
        self.last_losses = {
            "copper": float(copper_loss),
            "iron": float(iron_loss),
            "stray": float(stray_loss),
            "rectifier": float(rectifier_loss),
        }
        return float(dc_voltage), float(dc_current), float(dc_power), float(braking_torque), float(loss_power)

# 接入 370 V 直流母线的 Boost 平均状态变换器模型
class WindBoostConverter:
    def __init__(self, dc_bus_voltage: float = 370.0, rated_power: float = 2000.0,
                 L: float = 0.012, C: float = 0.035, R_load: Optional[float] = None,
                 inductor_resistance: float = 0.08,
                 duty_rate_limit: float = 4.0,
                 bus_regulation_tau: float = 0.08,
                 stiff_bus: bool = True):
        self.dc_bus_voltage = dc_bus_voltage
        self.rated_power = rated_power
        self.L = L
        self.C = C
        self.R_load = R_load or dc_bus_voltage**2 / max(rated_power, 1.0)
        self.inductor_resistance = inductor_resistance
        self.duty_rate_limit = duty_rate_limit
        self.bus_regulation_tau = bus_regulation_tau
        self.stiff_bus = stiff_bus
        self.v_C = dc_bus_voltage
        self.i_L = 0.0
        self.duty = 0.1

    def duty_from_voltage(self, v_in: float, dt: Optional[float] = None) -> float:
        # 根据输入电压估算 Boost 占空比，并对占空比变化率进行限制
        if v_in <= 1e-6:
            target = 0.05
        else:
            target = float(np.clip(1.0 - v_in / max(self.dc_bus_voltage, v_in + 1.0), 0.05, 0.97))

        if dt is None:
            self.duty = target
        else:
            max_step = self.duty_rate_limit * dt
            self.duty += float(np.clip(target - self.duty, -max_step, max_step))
        self.duty = float(np.clip(self.duty, 0.01, 0.97))
        return self.duty

    def efficiency(self, input_power: float) -> float:
        if input_power <= 1.0:
            return 0.0

        load_ratio = np.clip(input_power / max(self.rated_power, 1.0), 0.0, 1.5)
        efficiency = 0.80 + 0.17 * (1.0 - np.exp(-4.0 * load_ratio))
        efficiency -= 0.035 * max(load_ratio - 0.85, 0.0)
        return float(np.clip(efficiency, 0.78, 0.965))

    def step(self, v_in: float, available_power: float, duty: float, dt: float) -> tuple[float, float, float, float]:
        duty = float(np.clip(duty, 0.01, 0.97))

        if v_in <= 1.0 or available_power <= 1e-6:
            self.i_L *= np.exp(-dt / 0.03)
            output_power = 0.0
            converter_efficiency = 0.0
        else:
            di = (v_in - self.inductor_resistance * self.i_L - (1.0 - duty) * self.v_C) / max(self.L, 1e-6)
            source_current_limit = available_power / max(v_in, 1e-6)
            current_tau = max(self.L / max(self.inductor_resistance + 0.4, 1e-6), 0.018)
            current_alpha = min(dt / current_tau, 1.0)
            self.i_L += current_alpha * (source_current_limit - self.i_L)
            self.i_L += 0.12 * di * dt
            self.i_L = float(np.clip(self.i_L, 0.0, source_current_limit * 1.08))

            input_power = min(max(v_in * self.i_L, 0.0), max(available_power, 0.0))
            converter_efficiency = self.efficiency(input_power)
            output_power = input_power * converter_efficiency

        load_power = self.v_C**2 / max(self.R_load, 1e-6)
        dv_power = (output_power - load_power) / max(self.C * max(self.v_C, 1.0), 1e-6)

        if self.stiff_bus:
            self.v_C = self.dc_bus_voltage
        else:
            self.v_C += dv_power * dt

        self.v_C = float(np.clip(self.v_C, 0.0, self.dc_bus_voltage * 1.25))
        return float(self.i_L), float(self.v_C), float(output_power), float(converter_efficiency)

#---------------------------------------系统级动态仿真器---------------------------------------------
# 风机、MPPT、整流器和 Boost 组成的系统级动态仿真器
class WindPowerSystemSimulator:

    def __init__(
        self,
        dc_bus_voltage: float = 370.0,
        turbine_params: Optional[WindTurbineParams] = None,
        generator_params: Optional[GeneratorParams] = None,
    ):
        self.turbine = WindTurbineModel(turbine_params)
        self.mppt = OptimalTorqueMPPT(self.turbine.params.k_opt)
        self.pitch = PitchController(self.turbine.params.pitch_angle)
        self.generator = PMSGRectifierEquivalent(generator_params)
        self.boost = WindBoostConverter(dc_bus_voltage=dc_bus_voltage, rated_power=self.turbine.params.rated_power)
        self.omega = self.turbine.params.omega_initial
        self.t_gen = 0.0

    def simulate(
        self,
        time_array: np.ndarray,
        wind_array: np.ndarray,
        dt: float = 1e-3,
        density_array: Optional[np.ndarray] = None,
        progress_callback=None,
        progress_interval_seconds: float | None = None,
        pause_callback=None,
        stop_callback=None,
        real_time_playback: bool = False,
        playback_speed: float = 1.0,
    ) -> dict:
        n = len(time_array)
        p = self.turbine.params
        density = np.full(n, p.air_density) if density_array is None else np.asarray(density_array, dtype=float)
        
        if n > 0:
            initial_wind = float(wind_array[0])
            initial_rho = float(density[0])
            omega_opt, _, _ = self.turbine.get_mpp(initial_wind, initial_rho, self.pitch.pitch_angle)
            self.omega = max(float(omega_opt), 0.0)
        else:
            self.omega = p.omega_initial

        results = _new_result_buffer(time_array, wind_array, density)

        mppt_period_steps = max(1, int(self.mppt.sample_period / dt))
        torque_ref = 0.0
        state = "init"
        progress_interval_seconds = None if progress_interval_seconds is None else max(float(progress_interval_seconds), 0.1)
        last_progress_time = float(time_array[0]) if n else 0.0
        playback_speed = max(float(playback_speed), 1e-6)
        wall_start_time = wall_clock.monotonic()
        last_progress_wall_time = wall_start_time

        for i in range(n):
            if _stop_requested(stop_callback):
                return _trim_simulation_results(results, i, n)
            paused_seconds = _wait_while_paused(pause_callback, stop_callback)
            if paused_seconds > 0.0:
                wall_start_time += paused_seconds
            if _stop_requested(stop_callback):
                return _trim_simulation_results(results, i, n)
            if real_time_playback:
                current_sim_time = float(time_array[i]) if len(time_array) else float(i) * float(dt)
                target_elapsed = current_sim_time / playback_speed
                while not _stop_requested(stop_callback):
                    remaining = target_elapsed - (wall_clock.monotonic() - wall_start_time)
                    if remaining <= 0.0:
                        break
                    wall_clock.sleep(min(remaining, 0.05))
                if _stop_requested(stop_callback):
                    return _trim_simulation_results(results, i, n)

            wind_speed = float(wind_array[i])
            rho = float(density[i])
            t_aero_raw, p_aero_raw, _, _ = self.turbine.aerodynamic_point(
                wind_speed, self.omega, rho, self.pitch.pitch_angle
            )
            pitch_angle, pitch_state = self.pitch.update(self.omega, p_aero_raw, wind_speed, p, dt)
            t_aero, p_aero, cp, lam = self.turbine.aerodynamic_point(
                wind_speed, self.omega, rho, pitch_angle
            )
            k_opt, lambda_opt, cp_opt = self.turbine.optimal_torque_coefficient(rho, pitch_angle)
            omega_mpp, _, _ = self.turbine.get_mpp(
                wind_speed,
                rho,
                pitch_angle,
                optimum=(k_opt, lambda_opt, cp_opt),
            )
            k_ideal, lambda_ideal, cp_ideal = self.turbine.optimal_torque_coefficient(rho, 0.0)
            _, _, p_mpp = self.turbine.get_mpp(
                wind_speed,
                rho,
                0.0,
                optimum=(k_ideal, lambda_ideal, cp_ideal),
                cap_rated=False,
            )

            if i % mppt_period_steps == 0:
                torque_ref, state = self.mppt.update(self.omega, wind_speed, p, k_opt=k_opt)
            control_state = "pitch_limit" if pitch_state == "pitch_limit" else state

            max_torque_step = p.torque_rate_limit * dt
            self.t_gen += np.clip(torque_ref - self.t_gen, -max_torque_step, max_torque_step)
            self.t_gen = float(np.clip(self.t_gen, 0.0, p.torque_max))

            v_rect, i_rect, p_rect, t_gen_actual, p_gen_loss = self.generator.output(self.omega, self.t_gen, dt)
            duty = self.boost.duty_from_voltage(v_rect, dt)
            i_L, v_out, p_out, boost_efficiency = self.boost.step(v_rect, p_rect, duty, dt)

            domega = (t_aero - t_gen_actual - p.viscous_friction * self.omega) / p.inertia
            self.omega = float(np.clip(self.omega + domega * dt, 0.0, p.omega_max * 1.1))

            results["omega"][i] = self.omega
            results["omega_mpp"][i] = omega_mpp
            results["lambda"][i] = lam
            results["lambda_opt"][i] = lambda_opt
            results["Cp"][i] = cp
            results["Cp_opt"][i] = cp_opt
            results["k_opt"][i] = k_opt
            results["pitch_angle"][i] = pitch_angle
            results["P_aero"][i] = p_aero
            results["P_mpp"][i] = p_mpp
            results["T_aero"][i] = t_aero
            results["T_ref"][i] = torque_ref
            results["T_cmd"][i] = self.t_gen
            results["T_gen"][i] = t_gen_actual
            results["V_rect"][i] = v_rect
            results["I_rect"][i] = i_rect
            results["P_rect"][i] = p_rect
            results["P_gen_loss"][i] = p_gen_loss
            results["V_out"][i] = v_out
            results["I_L"][i] = i_L
            results["P_out"][i] = p_out
            results["duty"][i] = duty
            results["boost_efficiency"][i] = boost_efficiency
            results["state"][i] = control_state
            
            if progress_callback is not None:
                current_sim_time = float(time_array[i]) if len(time_array) else float(i) * float(dt)
                if progress_interval_seconds is None:
                    interval_due = True
                else:
                    physical_due = (current_sim_time - last_progress_time) >= progress_interval_seconds - 1e-9
                    wall_due = (wall_clock.monotonic() - last_progress_wall_time) >= progress_interval_seconds - 1e-9
                    if real_time_playback:
                        interval_due = physical_due and wall_due
                    else:
                        interval_due = physical_due
                
                should_publish = (
                    i == n - 1
                    or interval_due
                )
                if should_publish:
                    callback_result = progress_callback(results, i)
                    last_progress_time = current_sim_time
                    last_progress_wall_time = wall_clock.monotonic()
                    if callback_result is False or _stop_requested(stop_callback):
                        return _trim_simulation_results(results, i + 1, n)

        results["stopped"] = False
        results["completed_steps"] = int(n)
        results["total_steps"] = int(n)
        return results

    def print_statistics(self, results: dict):
        if len(results.get("time", [])) == 0:
            print("\nWind simulation stopped before any sample was produced.")
            return

        print("\n" + "=" * 50)
        print("Wind simulation statistics")
        print("=" * 50)

        print("\nOverall simulation:")
        print(f"  Wind mean/std: {np.mean(results['wind_speed']):.2f} / {np.std(results['wind_speed']):.2f} m/s")
        print(f"  Air density:   {np.mean(results['air_density']):.3f} kg/m^3")
        print(f"  Rotor speed:   {np.mean(results['omega']):.2f} rad/s")
        print(f"  Cp:            {np.mean(results['Cp']):.3f}")
        print(f"  Pitch angle:   {np.mean(results['pitch_angle']):.2f} deg")
        print(f"  Rect power:    {np.mean(results['P_rect']):.2f} W")
        print(f"  DC power:      {np.mean(results['P_out']):.2f} W")
        print(f"  DC voltage:    {np.mean(results['V_out']):.2f} V")
        print(f"  Gen loss:      {np.mean(results['P_gen_loss']):.2f} W")

        aero_mppt_ratio, rect_capture_ratio, boost_efficiency = _power_chain_ratios(results)

        print("\nTheoretical wind MPP comparison:")
        print(f"  Max theoretical MPP: {np.max(results['P_mpp']):.2f} W")
        print(f"  Max raw aerodynamic power: {np.max(results['P_aero']):.2f} W")
        print(f"  Max rectifier power: {np.max(results['P_rect']):.2f} W")
        print(f"  Max DC output power: {np.max(results['P_out']):.2f} W")
        print(f"  DC bus voltage range: {np.min(results['V_out']):.2f} - {np.max(results['V_out']):.2f} V")
        print(f"  Mean aerodynamic MPPT tracking ratio: {aero_mppt_ratio * 100:.1f}%")
        print(f"  Mean electromechanical capture ratio: {rect_capture_ratio * 100:.1f}%")
        print(f"  Mean Boost conversion efficiency: {boost_efficiency * 100:.1f}%")
        print(f"  Max pitch angle: {np.max(results['pitch_angle']):.2f} deg")

# -------------------------------------------------结果统计与 CSV 导出函数-----------------------------------------------
def summarize_results(results: dict) -> dict[str, float]:
    if len(results.get("time", [])) == 0:
        return {
            "wind_mean_mps": 0.0,
            "wind_std_mps": 0.0,
            "air_density_min_kg_m3": 0.0,
            "air_density_max_kg_m3": 0.0,
            "omega_mean_rad_s": 0.0,
            "lambda_opt_mean": 0.0,
            "k_opt_min": 0.0,
            "k_opt_max": 0.0,
            "cp_mean": 0.0,
            "cp_opt_mean": 0.0,
            "pitch_max_deg": 0.0,
            "p_mpp_max_w": 0.0,
            "p_aero_max_w": 0.0,
            "p_rect_max_w": 0.0,
            "p_out_max_w": 0.0,
            "p_out_mean_w": 0.0,
            "dc_bus_min_v": 0.0,
            "dc_bus_max_v": 0.0,
            "aero_tracking_ratio": 0.0,
            "generator_capture_ratio": 0.0,
            "boost_efficiency_ratio": 0.0,
        }

    aero_tracking, generator_capture, boost_efficiency = _power_chain_ratios(results)

    return {
        "wind_mean_mps": float(np.mean(results["wind_speed"])),
        "wind_std_mps": float(np.std(results["wind_speed"])),
        "air_density_min_kg_m3": float(np.min(results["air_density"])),
        "air_density_max_kg_m3": float(np.max(results["air_density"])),
        "omega_mean_rad_s": float(np.mean(results["omega"])),
        "lambda_opt_mean": float(np.mean(results["lambda_opt"])),
        "k_opt_min": float(np.min(results["k_opt"])),
        "k_opt_max": float(np.max(results["k_opt"])),
        "cp_mean": float(np.mean(results["Cp"])),
        "cp_opt_mean": float(np.mean(results["Cp_opt"])),
        "pitch_max_deg": float(np.max(results["pitch_angle"])),
        "p_mpp_max_w": float(np.max(results["P_mpp"])),
        "p_aero_max_w": float(np.max(results["P_aero"])),
        "p_rect_max_w": float(np.max(results["P_rect"])),
        "p_out_max_w": float(np.max(results["P_out"])),
        "p_out_mean_w": float(np.mean(results["P_out"])),
        "dc_bus_min_v": float(np.min(results["V_out"])),
        "dc_bus_max_v": float(np.max(results["V_out"])),
        "aero_tracking_ratio": float(aero_tracking),
        "generator_capture_ratio": float(generator_capture),
        "boost_efficiency_ratio": float(boost_efficiency),
    }


def build_parameter_table(sim: WindPowerSystemSimulator) -> list[dict[str, str]]:
    turbine = sim.turbine.params
    generator = sim.generator.params
    boost = sim.boost
    rows = [
        ("Wind resource", "Reference wind height", "H_ref", turbine.reference_wind_height, "m", "10 m wind speed is corrected to hub height"),
        ("Wind resource", "Hub height", "H_hub", turbine.hub_height, "m", "Small wind turbine tower height assumption"),
        ("Wind resource", "Wind shear exponent", "alpha", turbine.shear_exponent, "-", "Power-law height correction"),
        ("Wind resource", "Turbulence intensity", "TI", turbine.turbulence_intensity, "-", "Mild synthetic turbulence added to 15-minute mean wind"),
        ("Wind resource", "Turbulence time constant", "tau_w", turbine.turbulence_time_constant, "s", "First-order wind fluctuation correlation time"),
        ("Wind resource", "Turbulence max deviation", "delta_w_max", turbine.turbulence_max_deviation_ratio, "-", "Maximum synthetic wind deviation ratio when turbulence is enabled"),
        ("Wind turbine", "Blade radius", "R", turbine.blade_radius, "m", "Swept area determines captured wind power"),
        ("Wind turbine", "Swept area", "A", turbine.swept_area, "m2", "A = pi R^2"),
        ("Wind turbine", "Rated power", "P_rated", turbine.rated_power, "W", "2 kW wind branch matched to 370 V bus"),
        ("Wind turbine", "Cut-in speed", "v_ci", turbine.cut_in_speed, "m/s", "No generation below this wind speed"),
        ("Wind turbine", "Rated speed", "v_rated", turbine.rated_speed, "m/s", "Pitch limiting starts near rated condition"),
        ("Wind turbine", "Cut-out speed", "v_co", turbine.cut_out_speed, "m/s", "Generation is stopped above this wind speed"),
        ("Wind turbine", "Actual aero capture efficiency", "eta_aero", turbine.aero_capture_efficiency, "-", "Practical blade/turbulence correction applied to actual P_aero, not to ideal MPP reference"),
        ("Aerodynamics", "Dynamic optimum enabled", "dynamic_opt", float(turbine.use_dynamic_optimum), "-", "1 means lambda/Cp/k are updated during runtime"),
        ("Aerodynamics", "Nominal tip-speed ratio", "lambda_nom", turbine.lambda_opt, "-", "Fallback value when dynamic optimum is disabled"),
        ("Aerodynamics", "Nominal power coefficient", "Cp_nom", turbine.cp_opt, "-", "Fallback value when dynamic optimum is disabled"),
        ("Aerodynamics", "Lambda search minimum", "lambda_min", turbine.lambda_search_min, "-", "Runtime Cp peak search lower bound"),
        ("Aerodynamics", "Lambda search maximum", "lambda_max", turbine.lambda_search_max, "-", "Runtime Cp peak search upper bound"),
        ("Aerodynamics", "Yaw misalignment", "gamma", turbine.yaw_misalignment_deg, "deg", "Applied as cos(gamma)^3 loss"),
        ("Mechanics", "Rotor inertia", "J", turbine.inertia, "kg*m2", "Rotor speed dynamic state"),
        ("Mechanics", "Viscous friction", "B", turbine.viscous_friction, "N*m*s/rad", "Mechanical damping torque"),
        ("Pitch", "Maximum pitch angle", "beta_max", turbine.pitch_max, "deg", "Above-rated aerodynamic limiting"),
        ("Pitch", "Pitch rate limit", "d_beta_max", turbine.pitch_rate_limit, "deg/s", "Actuator slew-rate constraint"),
        ("MPPT", "Nominal torque coefficient", "k_nom", turbine.k_opt, "N*m/(rad/s)^2", "Runtime k_opt is adjusted by air density and pitch when dynamic optimum is enabled"),
        ("MPPT", "Torque rate limit", "d_T_max", turbine.torque_rate_limit, "N*m/s", "Generator torque actuator limit"),
        ("MPPT", "Startup torque coefficient", "Cq_start", turbine.startup_torque_coefficient, "-", "Low-speed aerodynamic starting torque before Region-2 MPPT engages"),
        ("PMSG", "Pole pairs", "p", generator.pole_pairs, "-", "Electrical speed = p * omega"),
        ("PMSG", "Flux linkage", "psi_f", generator.flux_linkage, "Wb", "Sets torque and voltage constants"),
        ("PMSG", "Phase resistance", "R_s", generator.phase_resistance, "ohm", "Copper loss term"),
        ("PMSG", "Current time constant", "tau_i", generator.current_time_constant, "s", "Electrical current lag"),
        ("Rectifier", "Diode voltage drop", "V_d", generator.rectifier_drop, "V", "Rectifier conduction loss"),
        ("Rectifier", "Equivalent resistance", "R_rec", generator.rectifier_resistance, "ohm", "Current-dependent rectifier loss"),
        ("Boost", "DC bus target", "V_bus", boost.dc_bus_voltage, "V", "Common bus shared with PV branch"),
        ("Boost", "Inductance", "L", boost.L, "H", "Average-state inductor current"),
        ("Boost", "Capacitance", "C", boost.C, "F", "DC bus capacitor state"),
        ("Boost", "Load resistance", "R_load", boost.R_load, "ohm", "Equivalent rated load"),
        ("Boost", "Inductor resistance", "R_L", boost.inductor_resistance, "ohm", "Converter conduction loss"),
        ("Boost", "Duty rate limit", "d_D_max", boost.duty_rate_limit, "1/s", "Duty-cycle slew-rate constraint"),
    ]
    return [
        {
            "category": category,
            "name": name,
            "symbol": symbol,
            "value": f"{float(value):.6g}" if isinstance(value, (int, float, np.floating)) else str(value),
            "unit": unit,
            "note": note,
        }
        for category, name, symbol, value, unit, note in rows
    ]


def export_parameter_table(sim: WindPowerSystemSimulator,
                           filename: str = "rescults/wind_parameter_table.csv") -> None:
    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    rows = build_parameter_table(sim)
    with open(filename, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["category", "name", "symbol", "value", "unit", "note"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"Parameter table saved: {filename}")


def export_summary(summary: dict[str, float],
                   filename: str = "rescults/wind_mppt_summary.csv") -> None:
    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    with open(filename, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["metric", "value"])
        for key, value in summary.items():
            writer.writerow([key, f"{value:.8g}"])
    print(f"Summary saved: {filename}")


def export_timeseries(results: dict,
                      filename: str = "rescults/wind_mppt_timeseries.csv") -> None:
    os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
    columns = list(_EXPORT_COLUMNS)
    with open(filename, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(columns)
        for i in range(len(results["time"])):
            row = [
                results[col][i] if col == "state" else f"{float(results[col][i]):.8g}"
                for col in columns
            ]
            writer.writerow(row)
    print(f"Timeseries saved: {filename}")
