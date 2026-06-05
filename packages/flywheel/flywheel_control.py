# 飞轮储能平均功率模型与控制器
# 功率跟踪控制器：根据参考功率、SOC、转矩和电流约束生成实际输出功率；
# 飞轮本体仿真器：根据功率、损耗和能量方程更新转速、SOC 和母线电压。

import math
import numpy as np

try:
    from .flywheel_params import FlywheelParams, J_PER_KWH
except ImportError:
    from flywheel_params import FlywheelParams, J_PER_KWH


# 限幅原因编码
LIMIT_REASON_CODE = {
    "none": 0,
    "soc_low": 1,
    "soc_high": 2,
    "torque_limit": 3,
    "motor_current_limit": 4,
    "converter_current_limit": 5,
    "rated_power": 6,
    "rate_limit": 7,
    "tracking_lag": 8,
    "dc_bus_voltage_limit": 9,
}

# 限幅原因中文说明
LIMIT_REASON_TEXT = {
    "none": "未限幅",
    "soc_low": "SOC过低，限制放电",
    "soc_high": "SOC过高，限制充电",
    "torque_limit": "机械转矩限制",
    "motor_current_limit": "电机电流限制",
    "converter_current_limit": "变流器电流限制",
    "rated_power": "额定功率限制",
    "rate_limit": "功率爬坡限制",
    "tracking_lag": "一阶响应滞后",
    "dc_bus_voltage_limit": "DC母线电压边界限制",
}


class FlywheelPowerController:
    # 飞轮功率跟踪控制器
    # 主要完成参考功率限幅、SOC 保护、硬件限功、功率爬坡限制和一阶响应滞后
    def __init__(self, params: FlywheelParams, response_tau_s: float = 1.0, soc_guard_band: float = 0.08):
        self.params = params
        self.response_tau_s = max(float(response_tau_s), 1e-6)
        self.soc_guard_band = max(float(soc_guard_band), 1e-6)
        self.p_command_w = 0.0
        self.p_actual_w = 0.0
        self.charge_enabled = True
        self.discharge_enabled = True
        self.dc_bus_integral_v_s = 0.0
        self.last_limit_reason = "none"
        self.last_limit_reason_text = LIMIT_REASON_TEXT["none"]
        self.last_limit_details: dict = {}

    def reset(self, p_init_w: float = 0.0) -> None:
        # 每次重新开始仿真时，清空控制器内部状态
        self.p_command_w = float(p_init_w)
        self.p_actual_w = float(p_init_w)
        self.charge_enabled = True
        self.discharge_enabled = True
        self.dc_bus_integral_v_s = 0.0
        self.last_limit_reason = "none"
        self.last_limit_reason_text = LIMIT_REASON_TEXT["none"]
        self.last_limit_details = {}

    def dc_bus_power_reference(
        self,
        v_dc: float,
        dt: float,
        load_power_w: float = 0.0,
        source_power_w: float = 0.0,
        v_ref: float | None = None,
    ) -> float:
        # DC 母线稳压 PI 控制器
        # 返回值为飞轮参考功率：
        # 这里仅根据母线电压误差生成修正功率
        dt = max(float(dt), 0.0)
        v_ref = self.params.dc_bus_nominal_v if v_ref is None else float(v_ref)
        err_v = v_ref - float(v_dc)
        self.dc_bus_integral_v_s += err_v * dt
        max_int = self.params.rated_power_w / max(self.params.dc_bus_ki_w_per_vs, 1e-9)
        self.dc_bus_integral_v_s = float(np.clip(self.dc_bus_integral_v_s, -max_int, max_int))
        p_correction = self.params.dc_bus_kp_w_per_v * err_v + self.params.dc_bus_ki_w_per_vs * self.dc_bus_integral_v_s
        p_ref = p_correction
        return float(np.clip(p_ref, -self.params.rated_power_w, self.params.rated_power_w))

    def _choose_hardware_reason(self, limits: dict) -> str:
        # 当功率被硬件条件限制时，找出最先触发的限制来源
        hw = limits["hardware_limit_w"]
        eps = max(1e-6, 1e-6 * max(self.params.rated_power_w, 1.0))
        candidates = [
            ("torque_limit", limits["torque_limit_w"]),
            ("motor_current_limit", limits["motor_current_limit_w"]),
            ("converter_current_limit", limits["converter_current_limit_w"]),
            ("rated_power", limits["rated_limit_w"]),
        ]
        for reason, value in candidates:
            if value <= hw + eps:
                return reason
        return "rated_power"

    def update(self, p_ref_w: float, omega_rad_s: float, soc: float, dt: float, dc_bus_voltage_v: float | None = None) -> float:
        # 根据当前转速、SOC 和母线电压计算飞轮允许的充放电功率
        dt = max(float(dt), 0.0)
        p_ref_w = float(p_ref_w)
        limits = self.params.power_limits(omega_rad_s, soc, self.soc_guard_band, dc_bus_voltage_v=dc_bus_voltage_v)
        self.discharge_enabled = limits["discharge_limit_w"] > 1e-6
        self.charge_enabled = limits["charge_limit_w"] > 1e-6

        # 按照 SOC、额定功率、转矩和电流约束对参考功率进行硬限幅
        p_limited = float(np.clip(p_ref_w, -limits["charge_limit_w"], limits["discharge_limit_w"]))
        max_step = self.params.power_rate_limit_w_s * dt
        p_command_old = self.p_command_w
        self.p_command_w = float(np.clip(p_limited, self.p_command_w - max_step, self.p_command_w + max_step))

        # 通过一阶惯性环节表示功率执行机构的响应滞后
        alpha = 1.0 - math.exp(-dt / self.response_tau_s) if dt > 0.0 else 0.0
        self.p_actual_w += alpha * (self.p_command_w - self.p_actual_w)

        # 根据参考功率和实际命令之间的差异，记录当前限幅或滞后的主要原因
        reason = "none"
        eps = max(1e-6, 1e-6 * max(self.params.rated_power_w, 1.0))
        if p_ref_w > limits["soc_discharge_limit_w"] + eps:
            reason = "soc_low"
        elif p_ref_w < -limits["soc_charge_limit_w"] - eps:
            reason = "soc_high"
        elif abs(p_ref_w) > limits["hardware_limit_w"] + eps:
            reason = self._choose_hardware_reason(limits)
        elif abs(self.p_command_w - p_limited) > eps:
            reason = "rate_limit"
        elif abs(self.p_actual_w - self.p_command_w) > max(1.0, 0.002 * self.params.rated_power_w):
            reason = "tracking_lag"

        self.last_limit_reason = reason
        self.last_limit_reason_text = LIMIT_REASON_TEXT.get(reason, reason)
        self.last_limit_details = {
            "p_ref_w": p_ref_w,
            "omega_rad_s": float(omega_rad_s),
            "soc": float(soc),
            "p_limited_w": p_limited,
            "p_command_old_w": p_command_old,
            "p_command_w": self.p_command_w,
            "p_actual_w": self.p_actual_w,
            "rate_step_w": max_step,
            "response_alpha": alpha,
            **limits,
        }
        return self.p_actual_w


class FlywheelSystemSimulator:
    # 飞轮系统仿真器
    # 将飞轮转子能量方程、功率控制器和可选 DC 母线模型组合在一起
    def __init__(self, params: FlywheelParams | None = None, controller: FlywheelPowerController | None = None):
        self.params = params or FlywheelParams()
        self.controller = controller or FlywheelPowerController(self.params)
        self.omega_rad_s = self.params.omega_init_rad_s
        self.energy_j = self.params.energy_from_omega(self.omega_rad_s)
        self.dc_bus_voltage_v = self.params.dc_bus_voltage_init_v
        self.controller.reset()

    def initialize(self, omega_rad_s: float | None = None, soc: float | None = None, dc_bus_voltage_v: float | None = None) -> None:
        # 初始化飞轮状态
        if soc is not None:
            self.omega_rad_s = self.params.omega_from_soc(soc)
        elif omega_rad_s is not None:
            self.omega_rad_s = float(np.clip(omega_rad_s, self.params.omega_min_rad_s, self.params.omega_max_rad_s))
        else:
            self.omega_rad_s = self.params.omega_init_rad_s
        self.energy_j = self.params.energy_from_omega(self.omega_rad_s)
        if dc_bus_voltage_v is None:
            self.dc_bus_voltage_v = self.params.dc_bus_voltage_init_v
        else:
            self.dc_bus_voltage_v = float(np.clip(dc_bus_voltage_v, self.params.dc_bus_voltage_min_v, self.params.dc_bus_voltage_max_v))
        self.controller.reset()

    def _state_dict(self, p_ref_w: float, p_fw_w: float, p_mech_w: float, p_loss_w: float, loss_components: dict, torque_nm: float, mode: str) -> dict:
        # 将当前仿真状态整理成字典，供保存 CSV、画图和界面刷新使用
        reason = self.controller.last_limit_reason
        details = self.controller.last_limit_details
        return {
            "P_ref": float(p_ref_w),
            "P_cmd": self.controller.p_command_w,
            "P_fw": p_fw_w,
            "P_mech": p_mech_w,
            "P_loss": p_loss_w,
            "P_loss_base": loss_components["base_w"],
            "P_loss_standby": loss_components["standby_w"],
            "P_loss_bearing": loss_components["bearing_w"],
            "P_loss_windage": loss_components["windage_w"],
            "P_loss_motor": loss_components["motor_w"],
            "P_loss_converter": loss_components["converter_w"],
            "P_loss_conversion": loss_components["conversion_w"],
            "P_loss_total": loss_components["total_w"],
            "omega": self.omega_rad_s,
            "rpm": self.params.rpm_from_omega(self.omega_rad_s),
            "soc": self.params.soc_from_omega(self.omega_rad_s),
            "energy_kwh": self.energy_j / J_PER_KWH,
            "usable_energy_kwh": (self.energy_j - self.params.energy_min_j) / J_PER_KWH,
            "torque": torque_nm,
            "dc_bus_voltage_v": self.dc_bus_voltage_v,
            "charge_enabled": float(self.controller.charge_enabled),
            "discharge_enabled": float(self.controller.discharge_enabled),
            "limit_reason_code": LIMIT_REASON_CODE.get(reason, -1),
            "limit_reason": reason,
            "limit_reason_text": self.controller.last_limit_reason_text,
            "rated_limit_w": details.get("rated_limit_w", self.params.rated_power_w),
            "torque_limit_w": details.get("torque_limit_w", self.params.torque_limited_power(self.omega_rad_s)),
            "motor_current_limit_w": details.get("motor_current_limit_w", self.params.motor_current_limited_power(self.omega_rad_s)),
            "converter_current_limit_w": details.get("converter_current_limit_w", self.params.converter_current_limited_power(self.dc_bus_voltage_v)),
            "hardware_limit_w": details.get("hardware_limit_w", self.params.hardware_power_limits(self.omega_rad_s, self.dc_bus_voltage_v)["hardware_limit_w"]),
            "soc_discharge_limit_w": details.get("soc_discharge_limit_w", self.params.rated_power_w),
            "soc_charge_limit_w": details.get("soc_charge_limit_w", self.params.rated_power_w),
            "discharge_limit_w": details.get("discharge_limit_w", self.params.rated_power_w),
            "charge_limit_w": details.get("charge_limit_w", self.params.rated_power_w),
            "mode": mode,
        }

    def step(self, p_ref_w: float, dt: float, dc_bus_voltage_v: float | None = None) -> dict:
        # 功率跟踪模式下的单步仿真：先算输出功率，再更新飞轮能量和转速
        dt = max(float(dt), 0.0)
        soc = self.params.soc_from_omega(self.omega_rad_s)
        vdc = self.dc_bus_voltage_v if dc_bus_voltage_v is None else float(dc_bus_voltage_v)
        p_fw_w = self.controller.update(p_ref_w, self.omega_rad_s, soc, dt, dc_bus_voltage_v=vdc)

        # 功率符号约定：P_fw 为正表示飞轮向外放电，为负表示飞轮吸收功率充电
        if p_fw_w >= 0.0:
            p_mech_w = -p_fw_w / self.params.effective_discharge_efficiency
        else:
            p_mech_w = -p_fw_w * self.params.effective_charge_efficiency

        loss_components = self.params.loss_components(self.omega_rad_s, p_fw_w=p_fw_w, p_mech_w=p_mech_w)
        p_loss_w = loss_components["base_w"]

        # 飞轮转子能量更新：机械功率改变储能，基础机械损耗持续消耗储能
        next_energy_j = self.energy_j + (p_mech_w - p_loss_w) * dt
        next_energy_j = float(np.clip(next_energy_j, self.params.energy_min_j, self.params.energy_max_j))
        self.energy_j = next_energy_j
        self.omega_rad_s = float(np.clip(self.params.omega_from_energy(self.energy_j), self.params.omega_min_rad_s, self.params.omega_max_rad_s))

        torque_nm = 0.0 if self.omega_rad_s <= 1e-9 else abs(p_mech_w) / self.omega_rad_s
        return self._state_dict(p_ref_w, p_fw_w, p_mech_w, p_loss_w, loss_components, torque_nm, mode="power")

    def step_dc_bus(self, load_power_w: float, dt: float, source_power_w: float = 0.0, v_ref: float | None = None) -> dict:
        # DC 母线稳压模式单步仿真
        # load_power_w 为负载消耗功率，source_power_w 为其他电源注入母线的功率
        # 控制器先根据母线电压误差计算飞轮参考功率，再更新飞轮和母线电压状态
        dt = max(float(dt), 0.0)
        p_ref = self.controller.dc_bus_power_reference(
            self.dc_bus_voltage_v,
            dt,
            load_power_w=load_power_w,
            source_power_w=source_power_w,
            v_ref=v_ref,
        )
        state = self.step(p_ref, dt, dc_bus_voltage_v=self.dc_bus_voltage_v)

        # 母线电容能量方程：母线净功率决定电容能量变化，从而得到新的母线电压
        c = self.params.dc_bus_capacitance_f
        bus_energy = 0.5 * c * self.dc_bus_voltage_v * self.dc_bus_voltage_v
        net_bus_power_w = float(source_power_w) + state["P_fw"] - float(load_power_w)
        bus_energy = max(0.0, bus_energy + net_bus_power_w * dt)
        next_v = math.sqrt(2.0 * bus_energy / c) if c > 0.0 else self.dc_bus_voltage_v
        next_v = float(np.clip(next_v, self.params.dc_bus_voltage_min_v, self.params.dc_bus_voltage_max_v))
        self.dc_bus_voltage_v = next_v

        # 如果母线电压已经到达上下边界，则记录为母线电压边界限幅
        if next_v <= self.params.dc_bus_voltage_min_v + 1e-9 or next_v >= self.params.dc_bus_voltage_max_v - 1e-9:
            self.controller.last_limit_reason = "dc_bus_voltage_limit"
            self.controller.last_limit_reason_text = LIMIT_REASON_TEXT["dc_bus_voltage_limit"]
            state["limit_reason_code"] = LIMIT_REASON_CODE["dc_bus_voltage_limit"]
            state["limit_reason"] = "dc_bus_voltage_limit"
            state["limit_reason_text"] = LIMIT_REASON_TEXT["dc_bus_voltage_limit"]
        state["dc_bus_voltage_v"] = self.dc_bus_voltage_v
        state["dc_bus_load_power_w"] = float(load_power_w)
        state["dc_bus_source_power_w"] = float(source_power_w)
        state["dc_bus_balance_power_w"] = float(load_power_w) - float(source_power_w)
        state["dc_bus_net_power_w"] = net_bus_power_w
        state["mode"] = "dc_bus"
        return state

    def simulate(self, time: np.ndarray, p_ref: np.ndarray, controller_dt: float) -> dict:
        # 批量仿真入口，适合无界面回调时快速生成整段仿真结果
        time = np.asarray(time, dtype=float)
        p_ref = np.asarray(p_ref, dtype=float)
        n = len(time)
        if len(p_ref) != n:
            raise ValueError("time and p_ref must have the same length.")

        scalar_keys = [
            "P_ref", "P_cmd", "P_fw", "P_mech", "P_loss", "P_loss_base", "P_loss_standby",
            "P_loss_bearing", "P_loss_windage", "P_loss_motor", "P_loss_converter", "P_loss_conversion",
            "P_loss_total", "omega", "rpm", "soc", "energy_kwh", "usable_energy_kwh", "torque",
            "dc_bus_voltage_v", "charge_enabled", "discharge_enabled", "limit_reason_code", "rated_limit_w",
            "torque_limit_w", "motor_current_limit_w", "converter_current_limit_w", "hardware_limit_w",
            "soc_discharge_limit_w", "soc_charge_limit_w", "discharge_limit_w", "charge_limit_w",
        ]
        results = {"time": time}
        for key in scalar_keys:
            results[key] = np.zeros(n)

        for i in range(n):
            if i == 0:
                dt = controller_dt
            else:
                dt = max(float(time[i] - time[i - 1]), 0.0)
            state = self.step(float(p_ref[i]), dt)
            for key in scalar_keys:
                results[key][i] = float(state.get(key, 0.0))
        return results
