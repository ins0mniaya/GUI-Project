from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# 根据环境温度和气压估算空气密度
AIR_GAS_CONSTANT = 287.05  
def air_density_from_weather(temperature_c: float | np.ndarray,
                             pressure_pa: float | np.ndarray = 101325.0) -> float | np.ndarray:
    temperature_k = np.asarray(temperature_c) + 273.15
    density = np.asarray(pressure_pa) / (AIR_GAS_CONSTANT * np.maximum(temperature_k, 1.0))
    return density if isinstance(temperature_c, np.ndarray) or isinstance(pressure_pa, np.ndarray) else float(density)

# 采用幂律风切变关系，把测风高度风速修正到轮毂高度
def wind_speed_at_hub_height(reference_speed: np.ndarray | float,
                             reference_height: float,
                             hub_height: float,
                             shear_exponent: float = 0.14) -> np.ndarray | float:

    ratio = (hub_height / max(reference_height, 0.1)) ** shear_exponent
    return np.asarray(reference_speed) * ratio


# ==================== 风机模型参数 ====================
@dataclass
class WindTurbineParams:
    # 风资源与叶轮几何参数
    air_density: float = 1.225
    hub_height: float = 30.0
    reference_wind_height: float = 10.0
    shear_exponent: float = 0.14
    blade_radius: float = 1.35
    inertia: float = 0.85
    viscous_friction: float = 0.012
    pitch_angle: float = 0.0
    yaw_misalignment_deg: float = 3.0
    turbulence_intensity: float = 0.03
    turbulence_time_constant: float = 20.0
    turbulence_seed: int = 202405
    turbulence_max_deviation_ratio: float = 0.12

    # MPPT根据当前桨距角和空气密度动态更新 lambda、Cp 和 k。
    use_dynamic_optimum: bool = True
    lambda_opt: float = 6.33
    cp_opt: float = 0.438
    lambda_search_min: float = 0.5
    lambda_search_max: float = 14.0
    lambda_search_points: int = 500


    # 风机运行边界与功率限制
    cut_in_speed: float = 2.5
    rated_speed: float = 11.0
    cut_out_speed: float = 25.0
    rated_power: float = 2000.0


    # 转速、转矩与桨距控制参数
    omega_min: float = 8.0
    omega_initial: float = 42.0
    omega_max: float = 95.0
    torque_max: float = 45.0
    torque_rate_limit: float = 300.0
    startup_torque_coefficient: float = 0.04
    pitch_max: float = 25.0
    pitch_rate_limit: float = 8.0
    pitch_kp: float = 0.65
    pitch_ki: float = 0.18
    aero_capture_efficiency: float = 0.94

    @property
    def swept_area(self) -> float:
        return float(np.pi * self.blade_radius**2)

    @property
    def omega_rated(self) -> float:
        return float(self.lambda_opt * self.rated_speed / self.blade_radius)

    @property
    def k_opt(self) -> float:
        return (
            0.5
            * self.air_density
            * self.swept_area
            * self.blade_radius**3
            * self.cp_opt
            / self.lambda_opt**3
        )


#永磁同步发电机与二极管整流等效模型参数
@dataclass
class GeneratorParams:
    # 发电机本体与整流损耗参数
    pole_pairs: int = 4
    flux_linkage: float = 0.55
    phase_resistance: float = 0.08
    q_axis_inductance: float = 0.006
    current_time_constant: float = 0.025
    max_phase_current: float = 18.0
    rectifier_drop: float = 1.2
    rectifier_resistance: float = 0.02
    mechanical_efficiency: float = 0.97
    iron_loss_coeff: float = 0.0006
    stray_loss_ratio: float = 0.010
    torque_ripple_ratio: float = 0.012

    @property
    def torque_constant(self) -> float:
        return 1.5 * self.pole_pairs * self.flux_linkage
