"""光伏组件参数与单二极管模型：保存组件数据，并根据辐照度、温度和工作电压计算输出电流与最大功率点"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

#保存光伏组件数据手册参数和阵列串并联配置，并提供阵列等效额定参数
@dataclass
class PVModuleParams:
    module_name: str = "Jinko Tiger Neo JKM590N-72HL4-BDV"
    module_Pmax: float = 590.0
    module_Vmp: float = 44.17
    module_Imp: float = 13.36
    module_Voc: float = 52.90
    module_Isc: float = 14.07
    cells_series_per_module: int = 72

    modules_series: int = 6
    modules_parallel: int = 2

    alpha_Isc: float = 0.00045
    beta_Voc: float = -0.0025
    gamma_Pmax: float = -0.0029
    T_ref: float = 25.0
    G_ref: float = 1000.0

    diode_ideality: float = 1.2
    Rsh: float = 1000.0

    @property
    def Pmax(self) -> float:
        return self.module_Pmax * self.modules_series * self.modules_parallel

    @property
    def Vmp(self) -> float:
        return self.module_Vmp * self.modules_series

    @property
    def Imp(self) -> float:
        return self.module_Imp * self.modules_parallel

    @property
    def Voc(self) -> float:
        return self.module_Voc * self.modules_series

    @property
    def Isc(self) -> float:
        return self.module_Isc * self.modules_parallel

    @property
    def Ns(self) -> int:
        return self.cells_series_per_module * self.modules_series

#使用单二极管等效模型描述光伏阵列的电流、电压、功率和最大功率点
class PVCellModel:
    def __init__(self, params: Optional[PVModuleParams] = None):
        #创建光伏单二极管模型，并根据组件参数计算模型内部参数
        self.params = params or PVModuleParams()
        self._calc_params()

    @staticmethod
    def _thermal_voltage(T_K: float) -> float:
        #根据电池片绝对温度计算热电压
        return 8.617333262e-5 * T_K

    @staticmethod
    def _safe_exp(x: float) -> float:
        return math.exp(max(min(float(x), 100.0), -100.0))

    def _calc_params(self) -> None:
        #根据数据手册参数计算参考光生电流、反向饱和电流、并联电阻和串联电阻
        p = self.params
        self.n = p.diode_ideality
        self.Rsh = p.Rsh
        self.Iph_ref = p.Isc

        T_ref_K = p.T_ref + 273.15
        Vt_ref = self._thermal_voltage(T_ref_K)
        a_ref = self.n * p.Ns * Vt_ref

        numerator = self.Iph_ref - p.Voc / self.Rsh
        denominator = self._safe_exp(p.Voc / a_ref) - 1.0
        self.I0_ref = max(numerator / denominator, 1e-18)
        self.Rs = self._fit_series_resistance(a_ref)

    def _fit_series_resistance(self, a_ref: float) -> float:
        #通过二分搜索拟合串联电阻，使模型在最大功率点处接近数据手册电流
        p = self.params

        def residual(Rs: float) -> float:
            v_diode = p.Vmp + p.Imp * Rs
            diode_current = self.I0_ref * (self._safe_exp(v_diode / a_ref) - 1.0)
            shunt_current = v_diode / self.Rsh
            return self.Iph_ref - diode_current - shunt_current - p.Imp

        lo, hi = 0.0, 20.0
        f_lo, f_hi = residual(lo), residual(hi)
        if f_lo * f_hi > 0:
            return 0.1 * p.modules_series / max(p.modules_parallel, 1)

        for _ in range(80):
            mid = 0.5 * (lo + hi)
            f_mid = residual(mid)
            if f_lo * f_mid <= 0:
                hi = mid
                f_hi = f_mid
            else:
                lo = mid
                f_lo = f_mid
        return 0.5 * (lo + hi)

    def _current_at_voltage(self, v: float, Iph_T: float, I0_T: float, a_T: float) -> float:
        #在给定光生电流、反向饱和电流和热电压参数下求解指定电压对应的输出电流
        if Iph_T <= 0:
            return 0.0

        def f(current: float) -> float:
            v_diode = v + current * self.Rs
            diode_current = I0_T * (self._safe_exp(v_diode / a_T) - 1.0)
            shunt_current = v_diode / self.Rsh
            return Iph_T - diode_current - shunt_current - current

        if f(0.0) <= 0:
            return 0.0

        lo = 0.0
        hi = max(Iph_T * 1.2, self.params.Isc * 0.01)
        if f(hi) > 0:
            return hi

        for _ in range(28):
            mid = 0.5 * (lo + hi)
            if f(mid) > 0:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def current_at(self, G: float, T: float, voltage: float) -> float:
        #根据辐照度、电池片温度和端电压计算光伏阵列输出电流。
        p = self.params
        if G <= 0:
            return 0.0
        delta_T = T - p.T_ref
        T_K = T + 273.15
        Vt_T = self._thermal_voltage(T_K)
        a_T = self.n * p.Ns * Vt_T
        Iph_T = self.Iph_ref * (G / p.G_ref) * (1.0 + p.alpha_Isc * delta_T)
        Voc_T = p.Voc * (1.0 + p.beta_Voc * delta_T)
        numerator = Iph_T - Voc_T / self.Rsh
        denominator = self._safe_exp(Voc_T / a_T) - 1.0
        I0_T = max(numerator / denominator, 1e-18)
        return self._current_at_voltage(float(voltage), Iph_T, I0_T, a_T)

    def iv_curve(self, G: float, T: float, V: np.ndarray) -> np.ndarray:
        #计算一组电压点对应的光伏阵列电流，生成电流-电压曲线
        p = self.params
        if G <= 0:
            return np.zeros_like(V, dtype=float)

        delta_T = T - p.T_ref
        T_K = T + 273.15
        Vt_T = self._thermal_voltage(T_K)
        a_T = self.n * p.Ns * Vt_T
        Iph_T = self.Iph_ref * (G / p.G_ref) * (1.0 + p.alpha_Isc * delta_T)
        Voc_T = p.Voc * (1.0 + p.beta_Voc * delta_T)
        numerator = Iph_T - Voc_T / self.Rsh
        denominator = self._safe_exp(Voc_T / a_T) - 1.0
        I0_T = max(numerator / denominator, 1e-18)

        I = np.zeros_like(V, dtype=float)
        for idx, voltage in enumerate(V):
            I[idx] = self._current_at_voltage(float(voltage), Iph_T, I0_T, a_T)
        return I

    def get_mpp(self, G: float, T: float, num_points: int = 1500) -> Tuple[float, float, float]:
        #扫描电流-电压曲线并返回最大功率点电压、电流和功率。
        V = np.linspace(0.0, self.params.Voc * 1.05, num_points)
        I = self.iv_curve(G, T, V)
        P = V * I
        idx = int(np.argmax(P))
        return float(V[idx]), float(I[idx]), float(P[idx])

    def print_model_info(self) -> None:
        p = self.params
        print("\nPV array parameters:")
        print(f"  Module: {p.module_name}")
        print(f"  Connection: {p.modules_series} series x {p.modules_parallel} parallel")
        print(f"  STC Pmax = {p.Pmax:.2f} W")
        print(f"  Vmp = {p.Vmp:.2f} V, Imp = {p.Imp:.2f} A")
        print(f"  Voc = {p.Voc:.2f} V, Isc = {p.Isc:.2f} A")
        print(f"  Equivalent series cells Ns = {p.Ns}")
        print(
            f"  Temperature coefficients: Pmax={p.gamma_Pmax*100:.3f}%/degC, "
            f"Voc={p.beta_Voc*100:.3f}%/degC, Isc={p.alpha_Isc*100:.3f}%/degC"
        )
        print(
            f"  Fitted single-diode parameters: I0={self.I0_ref:.3e} A, "
            f"n={self.n:.2f}, Rs={self.Rs:.4f} Ohm, Rsh={self.Rsh:.1f} Ohm"
        )


if __name__ == "__main__":
    # 若直接运行本文件，则打印模型基本信息并测试求一次 STC 状态下的最大功率点
    pv_model = PVCellModel()
    pv_model.print_model_info()
    v_mp, i_mp, p_max = pv_model.get_mpp(G=1000.0, T=25.0)
    print("\nSTC Test (G=1000W/m2, T=25C):")
    print(f"  Calculated MPP -> V={v_mp:.2f} V, I={i_mp:.2f} A, Pmax={p_max:.2f} W")
