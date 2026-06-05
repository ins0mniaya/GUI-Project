# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from packages.flywheel.gui_runner import (
    DEFAULT_FLYWHEEL_PARAMS,
    DEFAULT_RUNTIME_PARAMS,
    SCENARIO_LABELS,
    clear_plot_outputs,
)
from packages.simulation_process_runtime import run_flywheel_process
from .common_widgets import PlotImageGallery, ProcessSimulationWorkerBase, start_simulation_process, wrap_scroll_area


CONTENT_MIN_WIDTH = 1180
LOG_MIN_WIDTH = 1140


class FlywheelSimulationWorker(ProcessSimulationWorkerBase):
    def __init__(self, flywheel_params: dict, runtime_params: dict):
        super().__init__(
            run_flywheel_process,
            {
                "flywheel_params": flywheel_params,
                "runtime_params": runtime_params,
            },
        )
class FlywheelModelTab(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.thread = None
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        inner = QWidget()
        inner.setMinimumWidth(CONTENT_MIN_WIDTH)
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        params_group = QGroupBox("飞轮储能参数")
        params_layout = QGridLayout(params_group)
        self.spin_rated_power = self._add_double_spin(params_layout, 0, 0, "额定功率", DEFAULT_FLYWHEEL_PARAMS["rated_power_w"], 100.0, 200000.0, 1, " W")
        self.spin_inertia = self._add_double_spin(params_layout, 0, 1, "转动惯量", DEFAULT_FLYWHEEL_PARAMS["inertia_kg_m2"], 0.01, 500.0, 3, " kg*m2")
        self.spin_omega_min = self._add_double_spin(params_layout, 1, 0, "最小角速度", DEFAULT_FLYWHEEL_PARAMS["omega_min_rad_s"], 1.0, 5000.0, 1, " rad/s")
        self.spin_omega_max = self._add_double_spin(params_layout, 1, 1, "最大角速度", DEFAULT_FLYWHEEL_PARAMS["omega_max_rad_s"], 1.0, 8000.0, 1, " rad/s")
        self.spin_omega_init = self._add_double_spin(params_layout, 2, 0, "初始角速度", DEFAULT_FLYWHEEL_PARAMS["omega_init_rad_s"], 1.0, 8000.0, 1, " rad/s")
        self.spin_dc_bus_v = self._add_double_spin(params_layout, 2, 1, "DC 母线电压", DEFAULT_FLYWHEEL_PARAMS["dc_bus_nominal_v"], 10.0, 5000.0, 1, " V")
        self.spin_charge_eff = self._add_double_spin(params_layout, 3, 0, "充电效率", DEFAULT_FLYWHEEL_PARAMS["charge_efficiency"], 0.01, 1.0, 3, "")
        self.spin_discharge_eff = self._add_double_spin(params_layout, 3, 1, "放电效率", DEFAULT_FLYWHEEL_PARAMS["discharge_efficiency"], 0.01, 1.0, 3, "")
        layout.addWidget(params_group)

        runtime_group = QGroupBox("运行参数")
        runtime_layout = QGridLayout(runtime_group)
        runtime_layout.addWidget(QLabel("工况场景"), 0, 0)
        self.combo_scenario = QComboBox()
        for value, label in SCENARIO_LABELS.items():
            self.combo_scenario.addItem(label, value)
        default_index = self.combo_scenario.findData(DEFAULT_RUNTIME_PARAMS["scenario"])
        self.combo_scenario.setCurrentIndex(default_index if default_index >= 0 else 0)
        runtime_layout.addWidget(self.combo_scenario, 0, 1)
        self.spin_duration = self._add_double_spin(runtime_layout, 0, 1, "仿真时长", DEFAULT_RUNTIME_PARAMS["duration"], 1.0, 86400.0, 1, " s")
        self.spin_controller_dt = self._add_double_spin(runtime_layout, 1, 0, "控制步长", DEFAULT_RUNTIME_PARAMS["controller_dt"], 0.001, 10.0, 3, " s")
        self.spin_response_tau = self._add_double_spin(runtime_layout, 1, 1, "响应时间常数", DEFAULT_RUNTIME_PARAMS["response_tau_s"], 0.001, 60.0, 3, " s")
        layout.addWidget(runtime_group)

        summary_group = QGroupBox("飞轮仿真摘要")
        summary_layout = QGridLayout(summary_group)
        self.label_peak_discharge = self._add_value_label(summary_layout, 0, 0, "峰值放电功率", "W")
        self.label_peak_charge = self._add_value_label(summary_layout, 0, 1, "峰值充电功率", "W")
        self.label_final_soc = self._add_value_label(summary_layout, 0, 2, "最终 SOC", "%")
        self.label_discharge_energy = self._add_value_label(summary_layout, 1, 0, "放电能量", "kWh")
        self.label_charge_energy = self._add_value_label(summary_layout, 1, 1, "充电能量", "kWh")
        self.label_loss_energy = self._add_value_label(summary_layout, 1, 2, "损耗能量", "kWh")
        layout.addWidget(summary_group)

        chart_group = QGroupBox("飞轮储能模型仿真图")
        chart_layout = QVBoxLayout(chart_group)
        self.image_gallery = PlotImageGallery(
            ["功率跟踪", "飞轮转速", "系统性能汇总"],
            "运行飞轮储能模型后显示仿真图",
            min_image_height=250,
        )
        chart_layout.addWidget(self.image_gallery)
        layout.addWidget(chart_group)

        control_group = QGroupBox("运行控制")
        control_layout = QHBoxLayout(control_group)
        self.btn_run = QPushButton("运行飞轮储能模型")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #7C3AED; color: white; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #6D28D9; }"
            "QPushButton:disabled { background-color: #C0C4CC; }"
        )
        self.btn_run.clicked.connect(self._start_simulation)
        self.btn_pause = QPushButton("暂停仿真")
        self.btn_pause.setMinimumHeight(38)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause_simulation)
        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(lambda: self.text_log.clear())
        control_layout.addWidget(self.btn_run)
        control_layout.addWidget(self.btn_pause)
        control_layout.addWidget(self.btn_clear_log)
        control_layout.addStretch(1)
        layout.addWidget(control_group)

        log_group = QGroupBox("运行日志")
        log_group.setMinimumWidth(LOG_MIN_WIDTH)
        log_layout = QVBoxLayout(log_group)
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setMinimumWidth(LOG_MIN_WIDTH)
        self.text_log.setMinimumHeight(170)
        self.text_log.setMaximumHeight(230)
        log_layout.addWidget(self.text_log)
        layout.addWidget(log_group)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrap_scroll_area(inner))

    def _start_simulation(self):
        if self.thread is not None:
            self.worker.request_stop()
            self.btn_run.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self._append_log("正在停止飞轮仿真...")
            return
        if self.spin_omega_max.value() <= self.spin_omega_min.value():
            QMessageBox.warning(self, "参数无效", "最大角速度必须大于最小角速度。")
            return
        if not (self.spin_omega_min.value() <= self.spin_omega_init.value() <= self.spin_omega_max.value()):
            QMessageBox.warning(self, "参数无效", "初始角速度必须位于角速度范围内。")
            return

        self.btn_run.setText("停止仿真")
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停仿真")
        self.image_gallery.clear()
        self._reset_summary()
        clear_plot_outputs()
        self._append_log("启动飞轮储能模型。")
        worker = FlywheelSimulationWorker(self._collect_flywheel_params(), self._collect_runtime_params())
        start_simulation_process(
            self,
            worker,
            on_progress=self._append_log,
            on_finished=self._on_simulation_finished,
            on_failed=self._on_simulation_failed,
            on_thread_finished=self._on_thread_finished,
            extra_signals=((worker.rolling_updated, self._on_rolling_update),),
        )

    def _toggle_pause_simulation(self):
        if self.worker is None:
            return
        if self.worker.is_paused():
            self.worker.request_resume()
            self.btn_pause.setText("暂停仿真")
            self._append_log("继续飞轮仿真。")
        else:
            self.worker.request_pause()
            self.btn_pause.setText("继续仿真")
            self._append_log("飞轮仿真已暂停。")

    def _on_rolling_update(self, result: dict):
        self.image_gallery.set_plot_paths(result.get("plot_paths", []))
        self._update_summary(result)

    def _on_simulation_finished(self, result: dict):
        self.image_gallery.set_plot_paths(result.get("plot_paths", []))
        self._update_summary(result)
        self._append_log(f"输出文件：{result.get('output_csv')}")
        self._append_log(f"工况：{result.get('scenario_label', result.get('scenario'))}")
        validation = result.get("control_validation", {})
        if validation:
            self._append_log(validation.get("message", "控制模块校验完成"))

    def _update_summary(self, result: dict):
        self.label_peak_discharge.setText(self._format_value(result.get("peak_discharge_power_w"), 2))
        self.label_peak_charge.setText(self._format_value(result.get("peak_charge_power_w"), 2))
        self.label_final_soc.setText(self._format_value(100.0 * float(result.get("final_soc", 0.0)), 2))
        self.label_discharge_energy.setText(self._format_value(result.get("discharge_energy_kwh"), 4))
        self.label_charge_energy.setText(self._format_value(result.get("charge_energy_kwh"), 4))
        self.label_loss_energy.setText(self._format_value(result.get("loss_energy_kwh"), 4))

    def _on_simulation_failed(self, detail: str):
        self._append_log("运行失败，详细错误如下：")
        self._append_log(detail)
        QMessageBox.warning(self, "运行失败", "飞轮储能模型运行失败，请查看运行日志。")

    def _on_thread_finished(self):
        self.btn_run.setEnabled(True)
        self.btn_run.setText("运行飞轮储能模型")
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停仿真")
        self.thread = None
        self.worker = None

    def _collect_flywheel_params(self):
        return {
            **DEFAULT_FLYWHEEL_PARAMS,
            "rated_power_w": self.spin_rated_power.value(),
            "inertia_kg_m2": self.spin_inertia.value(),
            "omega_min_rad_s": self.spin_omega_min.value(),
            "omega_max_rad_s": self.spin_omega_max.value(),
            "omega_init_rad_s": self.spin_omega_init.value(),
            "charge_efficiency": self.spin_charge_eff.value(),
            "discharge_efficiency": self.spin_discharge_eff.value(),
            "dc_bus_nominal_v": self.spin_dc_bus_v.value(),
            "dc_bus_voltage_init_v": self.spin_dc_bus_v.value(),
        }

    def _collect_runtime_params(self):
        return {
            "scenario": self.combo_scenario.currentData(),
            "duration": self.spin_duration.value(),
            "controller_dt": self.spin_controller_dt.value(),
            "response_tau_s": self.spin_response_tau.value(),
        }

    def _reset_summary(self):
        for label in (
            self.label_peak_discharge,
            self.label_peak_charge,
            self.label_final_soc,
            self.label_discharge_energy,
            self.label_charge_energy,
            self.label_loss_energy,
        ):
            label.setText("--")

    def _append_log(self, message: str):
        self.text_log.append(str(message))
        self.text_log.moveCursor(QTextCursor.MoveOperation.End)

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.shutdown()
        clear_plot_outputs()
        super().closeEvent(event)

    def _add_double_spin(self, grid_layout, row, col, label_text, value, minimum, maximum, decimals, suffix):
        label = QLabel(label_text)
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(float(value))
        spin.setSuffix(suffix)
        spin.setMinimumWidth(120)
        grid_layout.addWidget(label, row, col * 2)
        grid_layout.addWidget(spin, row, col * 2 + 1)
        return spin

    def _add_value_label(self, grid_layout, row, col, title, unit):
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        value = QLabel("--")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value.setStyleSheet("font-size: 22px; font-weight: bold; color: #1E293B;")
        suffix = QLabel(unit)
        suffix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        suffix.setStyleSheet("color: #64748B;")
        box_layout.addWidget(value)
        box_layout.addWidget(suffix)
        grid_layout.addWidget(box, row, col)
        return value

    @staticmethod
    def _format_value(value, decimals: int = 2) -> str:
        if value is None:
            return "--"
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)
