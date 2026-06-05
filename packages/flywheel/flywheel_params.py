# 飞轮储能参数与状态换算工具
# 集中存放飞轮额定功率、转动惯量、转速范围、效率、损耗和电气约束
# 同时提供能量、转速、SOC 之间的换算，以及统一的功率限制计算
from dataclasses import dataclass
import math
import numpy as np

# 1 kWh 对应的焦耳数
J_PER_KWH = 3.6e6

@dataclass
class FlywheelParams:
    # 飞轮储能系统平均功率模型参数
    # 功率符号约定：
    #   P_fw > 0：飞轮放电，向外部母线输出功率；
    #   P_fw < 0：飞轮充电，从外部母线吸收功率。
    system_name: str = "Independent flywheel energy storage system"
    rated_power_w: float = 5000.0
    rated_usable_energy_kwh: float = 1.0

    inertia_kg_m2: float = 2.5
    omega_min_rad_s: float = 600.0
    omega_max_rad_s: float = 1800.0
    omega_init_rad_s: float = 1300.0

    charge_efficiency: float = 0.94
    discharge_efficiency: float = 0.94
    converter_efficiency: float = 0.97

    # 基础空载和机械损耗，用于飞轮转子能量更新
    standby_loss_w: float = 50.0
    viscous_loss_coeff: float = 1.0e-5       # 轴承或黏滞损耗系数
    windage_loss_coeff: float = 1.0e-8

    # 机械侧和电气侧限制参数
    max_torque_nm: float = 8.5
    motor_torque_constant_nm_per_a: float = 0.20
    max_motor_current_a: float = 80.0
    dc_bus_nominal_v: float = 400.0
    converter_current_limit_a: float = 40.0

    # DC 母线稳压模型参数
    dc_bus_voltage_init_v: float = 400.0
    dc_bus_voltage_min_v: float = 300.0
    dc_bus_voltage_max_v: float = 480.0
    dc_bus_capacitance_f: float = 2.0
    dc_bus_kp_w_per_v: float = 200.0
    dc_bus_ki_w_per_vs: float = 3.0

    power_rate_limit_w_s: float = 3000.0
    min_soc: float = 0.05
    max_soc: float = 0.95

    def __post_init__(self) -> None:
        # 参数初始化后做基本合法性检查
        if self.omega_max_rad_s <= self.omega_min_rad_s:
            raise ValueError("omega_max_rad_s must be greater than omega_min_rad_s.")
        if self.inertia_kg_m2 <= 0.0:
            raise ValueError("inertia_kg_m2 must be positive.")
        if self.motor_torque_constant_nm_per_a <= 0.0:
            raise ValueError("motor_torque_constant_nm_per_a must be positive.")
        if self.dc_bus_voltage_max_v <= self.dc_bus_voltage_min_v:
            raise ValueError("dc_bus_voltage_max_v must be greater than dc_bus_voltage_min_v.")
        if self.dc_bus_capacitance_f <= 0.0:
            raise ValueError("dc_bus_capacitance_f must be positive.")
        self.omega_init_rad_s = float(np.clip(self.omega_init_rad_s, self.omega_min_rad_s, self.omega_max_rad_s))
        self.min_soc = float(np.clip(self.min_soc, 0.0, 1.0))
        self.max_soc = float(np.clip(self.max_soc, self.min_soc, 1.0))
        self.dc_bus_voltage_init_v = float(np.clip(self.dc_bus_voltage_init_v, self.dc_bus_voltage_min_v, self.dc_bus_voltage_max_v))

    @property
    def effective_charge_efficiency(self) -> float:
        # 充电链路总效率 = 电机充电效率 × 变流器效率
        return float(np.clip(self.charge_efficiency * self.converter_efficiency, 1e-6, 1.0))

    @property
    def effective_discharge_efficiency(self) -> float:
        # 放电链路总效率 = 电机放电效率 × 变流器效率
        return float(np.clip(self.discharge_efficiency * self.converter_efficiency, 1e-6, 1.0))

    @property
    def energy_min_j(self) -> float:
        return self.energy_from_omega(self.omega_min_rad_s)

    @property
    def energy_max_j(self) -> float:
        return self.energy_from_omega(self.omega_max_rad_s)

    @property
    def usable_energy_j(self) -> float:
        return self.energy_max_j - self.energy_min_j

    @property
    def usable_energy_kwh(self) -> float:
        return self.usable_energy_j / J_PER_KWH

    def energy_from_omega(self, omega_rad_s: float) -> float:
        # 根据转动动能公式 E = 0.5 * J * ω² 计算飞轮储能
        omega = max(float(omega_rad_s), 0.0)
        return 0.5 * self.inertia_kg_m2 * omega * omega

    def omega_from_energy(self, energy_j: float) -> float:
        # 根据能量反算角速度，用于能量更新后的转速恢复
        energy = max(float(energy_j), 0.0)
        return math.sqrt(2.0 * energy / self.inertia_kg_m2)

    def soc_from_omega(self, omega_rad_s: float) -> float:
        # 将当前角速度映射为 0~1 的 SOC
        omega = float(np.clip(omega_rad_s, self.omega_min_rad_s, self.omega_max_rad_s))
        numerator = omega * omega - self.omega_min_rad_s * self.omega_min_rad_s
        denominator = self.omega_max_rad_s * self.omega_max_rad_s - self.omega_min_rad_s * self.omega_min_rad_s
        return float(np.clip(numerator / max(denominator, 1e-12), 0.0, 1.0))

    def omega_from_soc(self, soc: float) -> float:
        # 根据 SOC 反算角速度，常用于按指定初始 SOC 启动仿真
        soc = float(np.clip(soc, 0.0, 1.0))
        omega_sq = self.omega_min_rad_s * self.omega_min_rad_s
        omega_sq += soc * (self.omega_max_rad_s * self.omega_max_rad_s - self.omega_min_rad_s * self.omega_min_rad_s)
        return math.sqrt(max(omega_sq, 0.0))

    def rpm_from_omega(self, omega_rad_s: float) -> float:
        return float(omega_rad_s) * 60.0 / (2.0 * math.pi)

    def omega_from_rpm(self, rpm: float) -> float:
        return float(rpm) * (2.0 * math.pi) / 60.0

    def loss_power(self, omega_rad_s: float) -> float:
        # 返回用于飞轮能量更新的基础机械和空载损耗
        return self.loss_components(omega_rad_s)["base_w"]

    def loss_components(self, omega_rad_s: float, p_fw_w: float = 0.0, p_mech_w: float = 0.0) -> dict:
        # 返回各类损耗分量
        # standby、bearing、windage 直接从飞轮转子能量中扣除；
        # motor、converter 换算损耗主要用于诊断展示
        omega = max(float(omega_rad_s), 0.0)
        standby = max(float(self.standby_loss_w), 0.0)
        bearing = max(float(self.viscous_loss_coeff) * omega * omega, 0.0)
        windage = max(float(self.windage_loss_coeff) * omega**3, 0.0)
        base = standby + bearing + windage

        p_fw_abs = abs(float(p_fw_w))
        motor_loss = 0.0
        converter_loss = 0.0
        if p_fw_abs > 1e-12:
            eta_c = float(np.clip(self.converter_efficiency, 1e-6, 1.0))
            if p_fw_w >= 0.0:
                # 放电方向：飞轮转子 -> 电机 -> 变流器 -> 外部母线
                converter_input_w = p_fw_abs / eta_c
                converter_loss = max(converter_input_w - p_fw_abs, 0.0)
                motor_loss = max(abs(float(p_mech_w)) - converter_input_w, 0.0)
            else:
                # 充电方向：外部母线 -> 变流器 -> 电机 -> 飞轮转子
                converter_output_w = p_fw_abs * eta_c
                converter_loss = max(p_fw_abs - converter_output_w, 0.0)
                motor_loss = max(converter_output_w - abs(float(p_mech_w)), 0.0)

        conversion = motor_loss + converter_loss
        return {
            "standby_w": standby,
            "bearing_w": bearing,
            "windage_w": windage,
            "base_w": base,
            "motor_w": motor_loss,
            "converter_w": converter_loss,
            "conversion_w": conversion,
            "total_w": base + conversion,
        }

    def torque_limited_power(self, omega_rad_s: float) -> float:
        # 由最大机械转矩和当前角速度计算机械侧允许功率
        return max(self.max_torque_nm * max(float(omega_rad_s), 0.0), 0.0)

    def motor_current_limited_power(self, omega_rad_s: float) -> float:
        # 电机最大电流先换算成最大电磁转矩，再换算成功率限制
        max_motor_torque = max(self.max_motor_current_a, 0.0) * max(self.motor_torque_constant_nm_per_a, 0.0)
        return max(max_motor_torque * max(float(omega_rad_s), 0.0), 0.0)

    def converter_current_limited_power(self, dc_bus_voltage_v: float | None = None) -> float:
        # 变流器电流限制对应的功率上限约等于母线电压乘最大电流
        vdc = self.dc_bus_nominal_v if dc_bus_voltage_v is None else float(dc_bus_voltage_v)
        return max(abs(vdc) * max(self.converter_current_limit_a, 0.0), 0.0)

    def hardware_power_limits(self, omega_rad_s: float, dc_bus_voltage_v: float | None = None) -> dict:
        # 将额定功率、机械转矩、电机电流和变流器电流限制统一取最小值
        torque_limit_w = self.torque_limited_power(omega_rad_s)
        motor_current_limit_w = self.motor_current_limited_power(omega_rad_s)
        converter_current_limit_w = self.converter_current_limited_power(dc_bus_voltage_v)
        rated_limit_w = max(float(self.rated_power_w), 0.0)
        hardware_limit_w = min(rated_limit_w, torque_limit_w, motor_current_limit_w, converter_current_limit_w)
        return {
            "rated_limit_w": rated_limit_w,
            "torque_limit_w": torque_limit_w,
            "motor_current_limit_w": motor_current_limit_w,
            "converter_current_limit_w": converter_current_limit_w,
            "hardware_limit_w": hardware_limit_w,
        }

    def power_limits(self, omega_rad_s: float, soc: float, soc_guard_band: float = 0.08, dc_bus_voltage_v: float | None = None) -> dict:
        # 综合 SOC、转矩、电机电流和变流器电流限制，返回当前允许的充放电功率上限
        guard = max(float(soc_guard_band), 1e-9)
        discharge_scale = float(np.clip((float(soc) - self.min_soc) / guard, 0.0, 1.0))
        charge_scale = float(np.clip((self.max_soc - float(soc)) / guard, 0.0, 1.0))
        hardware = self.hardware_power_limits(omega_rad_s, dc_bus_voltage_v=dc_bus_voltage_v)
        hw = hardware["hardware_limit_w"]
        discharge_limit_w = min(hw, self.rated_power_w * discharge_scale)
        charge_limit_w = min(hw, self.rated_power_w * charge_scale)
        return {
            **hardware,
            "soc_discharge_limit_w": self.rated_power_w * discharge_scale,
            "soc_charge_limit_w": self.rated_power_w * charge_scale,
            "discharge_limit_w": max(discharge_limit_w, 0.0),
            "charge_limit_w": max(charge_limit_w, 0.0),
            "soc_discharge_scale": discharge_scale,
            "soc_charge_scale": charge_scale,
        }

    def print_model_info(self) -> None:
        # 打印模型参数，主要用于命令行调试和模型检查
        print("\nFlywheel system parameters:")
        print(f"  System: {self.system_name}")
        print(f"  Rated power: {self.rated_power_w:.1f} W")
        print(f"  Model usable energy: {self.usable_energy_kwh:.3f} kWh")
        print(f"  Inertia: {self.inertia_kg_m2:.3f} kg*m2")
        print(
            f"  Speed range: {self.omega_min_rad_s:.1f}-{self.omega_max_rad_s:.1f} rad/s "
            f"({self.rpm_from_omega(self.omega_min_rad_s):.0f}-{self.rpm_from_omega(self.omega_max_rad_s):.0f} rpm)"
        )
        print(f"  Initial SOC: {100.0 * self.soc_from_omega(self.omega_init_rad_s):.1f}%")
        print(
            f"  Effective charge/discharge efficiency: "
            f"{100.0 * self.effective_charge_efficiency:.1f}% / {100.0 * self.effective_discharge_efficiency:.1f}%"
        )
        print(
            f"  Electrical limits: Kt={self.motor_torque_constant_nm_per_a:.3f} N*m/A, "
            f"I_motor_max={self.max_motor_current_a:.1f} A, "
            f"Vdc_nom={self.dc_bus_nominal_v:.1f} V, I_converter_max={self.converter_current_limit_a:.1f} A"
        )
