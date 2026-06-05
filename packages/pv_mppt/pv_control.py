"""光伏控制与升压电路模型：包含扰动观察法最大功率点跟踪、电压 PI 控制、平均状态升压变换器和系统级仿真器"""

import math
from typing import Optional, Tuple

import numpy as np

from .pv_module_params import PVCellModel, PVModuleParams


DEFAULT_MPPT_V_REF_MIN = 220.0
DEFAULT_MPPT_V_REF_MAX = 320.0
DEFAULT_MPPT_V_REF_STEP = 1.5
DEFAULT_CELL_TEMP_WIND_SPEED = 1.5
# SAPM 开架式玻璃-聚合物背板组件温度模型经验参数
SAPM_OPEN_RACK_GLASS_POLYMER = {"a": -3.56, "b": -0.075, "deltaT": 3.0}

# 扰动观察法最大功率点跟踪控制器，根据功率和电压变化调整参考电压
class POMPPTController:

    def __init__(
        self,
        V_init: float,
        delta_V: float = DEFAULT_MPPT_V_REF_STEP,
        sample_period: float = 0.05,
        V_min: float = DEFAULT_MPPT_V_REF_MIN,
        V_max: float = DEFAULT_MPPT_V_REF_MAX,
    ):
        # 初始化扰动观察法控制器的参考电压、扰动步长、采样周期和电压限幅
        self.V_ref = float(np.clip(V_init, V_min, V_max))
        self.delta_V = delta_V
        self.sample_period = sample_period
        self.V_min = V_min
        self.V_max = V_max
        self.P_prev = 0.0
        self.V_prev = self.V_ref
        self.direction = 1

    def update(self, V_pv: float, I_pv: float) -> float:
        # 根据当前光伏电压和电流计算功率变化，并更新最大功率点参考电压
        V_pv = float(V_pv)
        P = V_pv * I_pv
        dP = P - self.P_prev
        dV = V_pv - self.V_prev
        if abs(dV) < 1e-9:
            self.V_ref += self.direction * self.delta_V
        elif dP > 0.0:
            self.direction = 1 if dV > 0.0 else -1
            self.V_ref += self.direction * self.delta_V
        else:
            self.direction = -1 if dV > 0.0 else 1
            self.V_ref += self.direction * self.delta_V
        self.P_prev = P
        self.V_prev = V_pv
        self.V_ref = float(np.clip(self.V_ref, self.V_min, self.V_max))
        return self.V_ref

#光伏侧电压 PI 控制器，将最大功率点参考电压转换为升压电路占空比
class VoltagePIController:

    def __init__(
        self,
        kp: float = 0.0025,
        ki: float = 0.35,
        duty_min: float = 0.05,
        duty_max: float = 0.90,
        duty_rate_limit: float = 120.0,
    ):
        #初始化电压 PI 控制器的比例系数、积分系数、占空比限幅和变化率限制
        self.kp = kp
        self.ki = ki
        self.duty_min = duty_min
        self.duty_max = duty_max
        self.duty_rate_limit = duty_rate_limit
        self.integral = 0.0
        self.duty = 0.5

    def reset(self, duty_init: float = 0.5) -> None:
        self.integral = 0.0
        self.duty = float(np.clip(duty_init, self.duty_min, self.duty_max))

    def update(self, V_ref: float, V_pv: float, V_out: float, dt: float) -> float:
        #根据参考电压、实际光伏电压和输出电压计算新的升压占空比
        error = V_pv - V_ref
        feedforward = 1.0 - V_ref / max(V_out, V_ref + 1.0)

        candidate_integral = self.integral + error * dt
        duty_unsat = feedforward + self.kp * error + self.ki * candidate_integral
        duty_limited = float(np.clip(duty_unsat, self.duty_min, self.duty_max))

        anti_windup = (
            duty_unsat == duty_limited
            or (duty_unsat > self.duty_max and error < 0.0)
            or (duty_unsat < self.duty_min and error > 0.0)
        )
        if anti_windup:
            self.integral = candidate_integral

        max_step = self.duty_rate_limit * dt
        self.duty = float(np.clip(duty_limited, self.duty - max_step, self.duty + max_step))
        self.duty = float(np.clip(self.duty, self.duty_min, self.duty_max))
        return self.duty

#平均状态升压变换器模型，包含光伏输入电容、电感和输出电容动态
class BoostConverter:
    def __init__(
        self,
        L: float = 5e-3,
        C: float = 2200e-6,
        C_in: float = 2200e-6,
        R_load: float = 45.0,
        fs: float = 50e3,
        i_L_max: float = 80.0,
        inductor_resistance: float = 0.18,
        switch_resistance: float = 0.05,
        diode_resistance: float = 0.04,
        diode_drop: float = 0.85,
        dc_bus_voltage: float = 370.0,
        bus_regulation_tau: float = 0.08,
        stiff_bus: bool = True,
        input_leakage_resistance: float = 15000.0,
    ):
        #初始化平均状态升压电路参数、损耗参数和状态变量
        self.L = L
        self.C_out = C
        self.C_in = C_in
        self.R_load = R_load
        self.fs = fs
        self.i_L_max = i_L_max
        self.inductor_resistance = inductor_resistance
        self.switch_resistance = switch_resistance
        self.diode_resistance = diode_resistance
        self.diode_drop = diode_drop
        self.dc_bus_voltage = float(dc_bus_voltage)
        self.bus_regulation_tau = float(bus_regulation_tau)
        self.stiff_bus = bool(stiff_bus)
        self.input_leakage_resistance = float(input_leakage_resistance)
        self.v_pv = 0.0
        self.i_L = 0.0
        self.v_C = 0.0

    def initialize(self, v_pv: float, i_L: float, v_out: float) -> None:
        #设置升压电路初始光伏输入电压、电感电流和输出电压
        self.v_pv = max(float(v_pv), 0.1)
        self.i_L = float(np.clip(i_L, 0.0, self.i_L_max))
        self.v_C = max(float(v_out), 0.0)

    def step(
        self,
        pv_model: PVCellModel,
        G: float,
        T_cell: float,
        duty: float,
        dt: float,
    ) -> Tuple[float, float, float, float]:
#推进一个时间步的升压电路平均状态，并返回光伏电压、电流、电感电流和输出电压
        duty = float(np.clip(duty, 0.0, 0.95))
        dt = max(float(dt), 0.0)
        k = 1.0 - duty
        conduction_resistance = self.inductor_resistance + duty * self.switch_resistance + k * self.diode_resistance
        diode_voltage = k * self.diode_drop
        v_max = pv_model.params.Voc * 1.08

        i_source = float(pv_model.current_at(G, T_cell, self.v_pv))
        #离散化
        cin_gain = dt / max(self.C_in, 1e-12)
        l_gain = dt / max(self.L, 1e-12)
        cout_gain = dt / max(self.C_out, 1e-12)
        out_denom = 1.0 + cout_gain / max(self.R_load, 1e-12)
        input_leak_denom = 1.0
        if self.input_leakage_resistance > 0.0 and np.isfinite(self.input_leakage_resistance):
            input_leak_denom += cin_gain / self.input_leakage_resistance

        #   使用隐式欧拉法同时求解输入电容、电感和输出电容的平均状态方程：
        #   光伏发出的电流，一部分进入电感，一部分改变输入电容电压，可能还有一小部分漏掉
        #   光伏侧电压、输出侧折算电压、导通损耗和二极管压降共同决定电感电流变化
        #   电感向输出侧提供的电流与负载电流差值决定输出电压变化
        rhs_v = (self.v_pv + cin_gain * i_source) / input_leak_denom
        rhs_i = self.i_L
        rhs_out = self.v_C
        input_current_gain = cin_gain / input_leak_denom
        current_denom = 1.0 + l_gain * conduction_resistance + l_gain * input_current_gain + l_gain * cout_gain * k * k / out_denom
        i_L_next = (rhs_i + l_gain * rhs_v - l_gain * k * rhs_out / out_denom - l_gain * diode_voltage) / max(current_denom, 1e-12)
        v_pv_next = rhs_v - input_current_gain * i_L_next
        v_out_dynamic = (rhs_out + cout_gain * k * i_L_next) / out_denom
        if self.stiff_bus:
            v_out_next = self.dc_bus_voltage
        else:
            v_out_next = v_out_dynamic

        self.v_pv = float(np.clip(v_pv_next, 0.1, v_max))
        self.i_L = float(np.clip(i_L_next, 0.0, self.i_L_max))
        self.v_C = float(np.clip(v_out_next, 0.0, max(self.dc_bus_voltage, 1.0) * 1.25))

        i_pv = float(pv_model.current_at(G, T_cell, self.v_pv))
        return self.v_pv, i_pv, self.i_L, self.v_C


def sapm_cell_temperature_target(
    poa_global: np.ndarray | float,
    temp_air: np.ndarray | float,
    wind_speed: np.ndarray | float = DEFAULT_CELL_TEMP_WIND_SPEED,
    params: dict[str, float] | None = None,
) -> np.ndarray | float:
    #使用 SAPM 开架式组件温度模型估算电池片稳态温度
    model = SAPM_OPEN_RACK_GLASS_POLYMER if params is None else params
    poa = np.asarray(poa_global, dtype=float)
    temp = np.asarray(temp_air, dtype=float)
    wind = np.maximum(np.asarray(wind_speed, dtype=float), 0.0)
    irradiance = np.maximum(poa, 0.0)
    module_temp = temp + irradiance * np.exp(float(model["a"]) + float(model["b"]) * wind)
    cell_temp = module_temp + irradiance / 1000.0 * float(model["deltaT"])
    if np.isscalar(poa_global) and np.isscalar(temp_air) and np.isscalar(wind_speed):
        return float(cell_temp)
    return cell_temp


def cell_temperature_profile(
    time_array: np.ndarray,
    G_array: np.ndarray,
    ambient_T_array: np.ndarray,
    noct: float = 45.0,
    tau: float = 300.0,
    wind_speed: np.ndarray | float = DEFAULT_CELL_TEMP_WIND_SPEED,
) -> np.ndarray:
    #把环境温度和辐照度转换为电池片温度，并用一阶惯性模拟温度滞后
    time_array = np.asarray(time_array, dtype=float)
    G_array = np.asarray(G_array, dtype=float)
    ambient_T_array = np.asarray(ambient_T_array, dtype=float)
    if len(time_array) == 0:
        return np.array([], dtype=float)

    _ = noct
    target = np.asarray(sapm_cell_temperature_target(G_array, ambient_T_array, wind_speed), dtype=float)
    T_cell = np.zeros_like(ambient_T_array, dtype=float)
    T_cell[0] = target[0]
    for i in range(1, len(time_array)):
        dt_i = max(float(time_array[i] - time_array[i - 1]), 0.0)
        alpha = 1.0 - math.exp(-dt_i / max(tau, 1e-9))
        T_cell[i] = T_cell[i - 1] + alpha * (target[i] - T_cell[i - 1])
    return T_cell

#光伏阵列、最大功率点跟踪、电压 PI 控制和升压电路的系统级仿真器
class PVSystemSimulator:
    def __init__(
        self,
        pv_params: Optional[PVModuleParams] = None,
        boost_L: float = 5e-3,
        boost_C: float = 2200e-6,
        boost_Cin: float = 2200e-6,
        boost_R: Optional[float] = None,
        dc_bus_voltage: Optional[float] = None,
        voltage_kp: float = 0.0025,
        voltage_ki: float = 0.35,
    ):
        #创建系统级仿真对象，初始化光伏模型、控制器、升压电路和直流母线参数
        self.pv = PVCellModel(pv_params)
        p = self.pv.params

        self.dc_bus_voltage = dc_bus_voltage or max(320.0, 1.7 * p.Vmp)
        if boost_R is None:
            boost_R = self.dc_bus_voltage ** 2 / max(p.Pmax, 1.0)

        v_min = DEFAULT_MPPT_V_REF_MIN
        v_max = DEFAULT_MPPT_V_REF_MAX
        v_init = float(np.clip(p.Vmp, v_min, v_max))

        self.mppt = POMPPTController(V_init=v_init, delta_V=DEFAULT_MPPT_V_REF_STEP, V_min=v_min, V_max=v_max)
        self.voltage_controller = VoltagePIController(kp=voltage_kp, ki=voltage_ki)
        self.boost = BoostConverter(
            L=boost_L,
            C=boost_C,
            C_in=boost_Cin,
            R_load=boost_R,
            dc_bus_voltage=self.dc_bus_voltage,
            stiff_bus=True,
            bus_regulation_tau=0.03,
            input_leakage_resistance=12000.0,
        )

        initial_duty = 1.0 - v_init / max(self.dc_bus_voltage, v_init + 1.0)
        self.voltage_controller.reset(initial_duty)
        self.boost.initialize(v_pv=v_init, i_L=p.Imp, v_out=self.dc_bus_voltage)

    def simulate(
        self,
        time_array: np.ndarray,
        G_array: np.ndarray,
        T_array: np.ndarray,
        dt: float = 1e-4,
        use_cell_temperature_model: bool = True,
    ) -> dict:
        #按给定时间序列运行系统级动态仿真，并记录电压、电流、功率、参考电压和占空比
        n = len(time_array)
        if use_cell_temperature_model:
            T_cell_array = cell_temperature_profile(time_array, G_array, T_array)
        else:
            T_cell_array = np.asarray(T_array, dtype=float)

        results = {
            "time": time_array,
            "G": G_array,
            "T": T_cell_array,
            "T_ambient": T_array,
            "T_cell": T_cell_array,
            "V_pv": np.zeros(n),
            "I_pv": np.zeros(n),
            "P_pv": np.zeros(n),
            "V_ref": np.zeros(n),
            "V_out": np.zeros(n),
            "I_L": np.zeros(n),
            "duty": np.zeros(n),
        }

        mppt_period_steps = max(1, int(self.mppt.sample_period / dt))
        control_period = 1.0 / max(self.boost.fs, 1.0)
        control_period_steps = max(1, int(control_period / dt))

        if n > 0:
            v_init = float(np.clip(self.mppt.V_ref, 0.1, self.pv.params.Voc * 1.05))
            i_init = float(self.pv.current_at(float(G_array[0]), float(T_cell_array[0]), v_init))
            self.boost.initialize(v_pv=v_init, i_L=i_init, v_out=self.dc_bus_voltage)
            initial_duty = 1.0 - v_init / max(self.dc_bus_voltage, v_init + 1.0)
            self.voltage_controller.reset(initial_duty)
            duty = self.voltage_controller.duty
        else:
            duty = self.voltage_controller.duty

        for i in range(n):
            G = float(G_array[i])
            T_cell = float(T_cell_array[i])
            V_ref = float(self.mppt.V_ref)

            if i % control_period_steps == 0:
                duty = self.voltage_controller.update(V_ref, self.boost.v_pv, self.boost.v_C, dt)

            V_pv, I_pv, i_L, v_out = self.boost.step(self.pv, G, T_cell, duty, dt)

            if i % mppt_period_steps == 0 and i > 0:
                self.mppt.update(V_pv, I_pv)

            results["V_pv"][i] = V_pv
            results["I_pv"][i] = I_pv
            results["P_pv"][i] = V_pv * I_pv
            results["V_ref"][i] = V_ref
            results["V_out"][i] = v_out
            results["I_L"][i] = i_L
            results["duty"][i] = duty
        return results
