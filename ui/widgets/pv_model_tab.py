# -*- coding: utf-8 -*-
import os

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFileDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from packages.pv_mppt.gui_runner import (
    DEFAULT_DATA_PATH,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_PV_PARAMS,
    DEFAULT_RUNTIME_PARAMS,
    clear_plot_outputs,
)
from packages.simulation_process_runtime import run_pv_process
from .common_widgets import PlotImageGallery, ProcessSimulationWorkerBase, start_simulation_process, wrap_scroll_area


CONTENT_MIN_WIDTH = 1180
LOG_MIN_WIDTH = 1140


class PVSimulationWorker(ProcessSimulationWorkerBase):
    def __init__(
        self,
        data_path: str,
        output_path: str,
        pv_params: dict,
        runtime_params: dict,
    ):
        """保存光伏滚动仿真子进程所需的输入参数。"""
        super().__init__(
            run_pv_process,
            {
                "data_path": data_path,
                "output_path": output_path,
                "pv_params": pv_params,
                "runtime_params": runtime_params,
            },
        )
class PVModelTab(QWidget):
    def __init__(self, host):
        """初始化光伏模型页并立即构建界面。"""
        super().__init__()
        self.host = host
        self.thread = None
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        """构建光伏模型页的输入区、图表区与日志区。"""
        inner = QWidget()
        inner.setMinimumWidth(CONTENT_MIN_WIDTH)
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        file_group = QGroupBox("数据文件选择")
        file_layout = QGridLayout(file_group)
        file_layout.setColumnStretch(1, 1)

        self.edit_data_path = QLineEdit(str(DEFAULT_DATA_PATH))

        btn_data = QPushButton("选择数据")
        btn_data.clicked.connect(self._choose_data_file)

        file_layout.addWidget(QLabel("历史气象数据"), 0, 0)
        file_layout.addWidget(self.edit_data_path, 0, 1)
        file_layout.addWidget(btn_data, 0, 2)
        layout.addWidget(file_group)


        pv_group = QGroupBox("光伏阵列参数")
        pv_layout = QGridLayout(pv_group)
        self.spin_module_pmax = self._add_double_spin(pv_layout, 0, 0, "组件Pmax", DEFAULT_PV_PARAMS["module_Pmax"], 1.0, 2000.0, 2, " W")
        self.spin_module_vmp = self._add_double_spin(pv_layout, 0, 1, "组件Vmp", DEFAULT_PV_PARAMS["module_Vmp"], 1.0, 200.0, 2, " V")
        self.spin_module_imp = self._add_double_spin(pv_layout, 1, 0, "组件Imp", DEFAULT_PV_PARAMS["module_Imp"], 0.1, 100.0, 2, " A")
        self.spin_module_voc = self._add_double_spin(pv_layout, 1, 1, "组件Voc", DEFAULT_PV_PARAMS["module_Voc"], 1.0, 250.0, 2, " V")
        self.spin_module_isc = self._add_double_spin(pv_layout, 2, 0, "组件Isc", DEFAULT_PV_PARAMS["module_Isc"], 0.1, 120.0, 2, " A")
        self.spin_cells_series = self._add_spin(pv_layout, 2, 1, "组件串联电池数", DEFAULT_PV_PARAMS["cells_series_per_module"], 1, 200, "")
        self.spin_modules_series = self._add_spin(pv_layout, 3, 0, "阵列串联组件数", DEFAULT_PV_PARAMS["modules_series"], 1, 100, "")
        self.spin_modules_parallel = self._add_spin(pv_layout, 3, 1, "阵列并联支路数", DEFAULT_PV_PARAMS["modules_parallel"], 1, 100, "")
        layout.addWidget(pv_group)

        runtime_group = QGroupBox("运行参数")
        runtime_layout = QGridLayout(runtime_group)
        self.spin_controller_dt = self._add_double_spin(
            runtime_layout,
            0,
            0,
            "控制步长",
            DEFAULT_RUNTIME_PARAMS["controller_dt"],
            0.01,
            5.0,
            3,
            " s",
        )
        self.spin_dc_bus_voltage = self._add_double_spin(
            runtime_layout,
            0,
            1,
            "直流母线电压",
            DEFAULT_RUNTIME_PARAMS["dc_bus_voltage"],
            50.0,
            1000.0,
            1,
            " V",
        )
        self.spin_mppt_enable_irr = self._add_double_spin(
            runtime_layout,
            1,
            0,
            "MPPT启用辐照度",
            DEFAULT_RUNTIME_PARAMS["mppt_enable_irradiance"],
            0.0,
            1000.0,
            1,
            " W/m2",
        )
        layout.addWidget(runtime_group)

        chart_group = QGroupBox("光伏发电模型仿真图")
        chart_group.setMinimumWidth(CONTENT_MIN_WIDTH)
        chart_layout = QVBoxLayout(chart_group)
        self.image_gallery = PlotImageGallery(
            ["MPPT跟踪与阵列功率", "光伏电气输出功率", "系统性能汇总"],
            "运行光伏发电模型后显示仿真图",
            image_size=QSize(560, 300),
        )
        chart_layout.addWidget(self.image_gallery)
        layout.addWidget(chart_group)

        control_group = QGroupBox("运行控制")
        control_layout = QHBoxLayout(control_group)
        self.btn_run = QPushButton("运行光伏发电模型")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #059669; color: white; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #047857; }"
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
        self.text_log.setMinimumHeight(180)
        self.text_log.setMaximumHeight(240)
        self.text_log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        log_layout.addWidget(self.text_log)
        layout.addWidget(log_group)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrap_scroll_area(inner))

    def _choose_data_file(self):
        """选择光伏仿真使用的历史气象数据文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择历史气象数据文件",
            os.path.dirname(self.edit_data_path.text()) or os.getcwd(),
            "Data Files (*.xlsx *.xls *.csv);;All Files (*)",
        )
        if path:
            self.edit_data_path.setText(path)

    def _start_simulation(self):
        """启动或停止光伏滚动仿真线程。"""
        if self.thread is not None:
            self._stop_simulation()
            return

        data_path = self.edit_data_path.text().strip()
        output_path = str(DEFAULT_OUTPUT_PATH)
        if not os.path.exists(data_path):
            QMessageBox.warning(self, "数据文件不存在", "请先选择有效的历史气象数据文件。")
            return
        if not output_path:
            QMessageBox.warning(self, "输出路径为空", "请设置仿真输出 CSV 保存位置。")
            return

        self.btn_run.setText("停止光伏发电模型")
        self.btn_run.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停仿真")
        self.image_gallery.clear()
        clear_plot_outputs()
        self._append_log("启动光伏发电模型。")

        worker = PVSimulationWorker(
            data_path,
            output_path,
            self._collect_pv_params(),
            self._collect_runtime_params(),
        )
        start_simulation_process(
            self,
            worker,
            on_progress=self._append_log,
            extra_signals=((worker.rolling_updated, self._on_rolling_update),),
            on_finished=self._on_simulation_finished,
            on_failed=self._on_simulation_failed,
            on_thread_finished=self._on_thread_finished,
        )

    def _stop_simulation(self):
        """向工作线程发送停止请求，并临时禁用按钮。"""
        if self.worker is not None:
            self.worker.request_stop()
            self.btn_run.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self._append_log("正在停止光伏发电滚动仿真，将在当前节拍退出。")

    def _toggle_pause_simulation(self):
        """在暂停与继续之间切换光伏仿真状态。"""
        if self.worker is None:
            return
        if self.worker.is_paused():
            self.worker.request_resume()
            self.btn_pause.setText("暂停仿真")
            self._append_log("光伏仿真继续运行。")
        else:
            self.worker.request_pause()
            self.btn_pause.setText("继续仿真")
            self._append_log("光伏仿真已暂停。")

    def _on_rolling_update(self, snapshot: dict):
        """接收滚动周期快照并刷新图像与日志。"""
        plot_paths = snapshot.get("plot_paths", [])
        if plot_paths:
            self.image_gallery.set_plot_paths(plot_paths)
        step = snapshot.get("step")
        total = snapshot.get("total_steps")
        cycle = snapshot.get("rolling_cycle")
        if cycle is not None:
            energy = self._format_value(snapshot.get("energy_kwh"), 3)
            peak = self._format_value(snapshot.get("peak_power_w"), 2)
            self._append_log(f"光伏滚动仿真更新：第 {cycle} 个15分钟周期，累计发电量 {energy} kWh，峰值 {peak} W")
            return
        if step is not None and total is not None:
            energy = self._format_value(snapshot.get("energy_kwh"), 3)
            peak = self._format_value(snapshot.get("peak_power_w"), 2)
            self._append_log(f"滚动仿真更新：{step}/{total} 个15分钟周期，累计发电量 {energy} kWh，峰值 {peak} W")

    def _on_simulation_finished(self, result: dict):
        """在仿真完成后刷新最终结果并输出摘要。"""
        self.image_gallery.set_plot_paths(result.get("plot_paths", []))
        if result.get("stopped"):
            self._append_log(f"光伏发电滚动仿真已停止，共运行 {result.get('rolling_cycles', 0)} 个15分钟周期。")
        self._append_log(f"输出文件：{result.get('output_csv')}")
        self._append_log(f"天气预测点数：{result.get('weather_points')}")
        self._append_log(
            "仿真摘要："
            f"峰值 {self._format_value(result.get('peak_power_w'), 2)} W，"
            f"平均 {self._format_value(result.get('mean_power_w'), 2)} W，"
            f"发电量 {self._format_value(result.get('energy_kwh'), 3)} kWh"
        )
        validation = result.get("control_validation", {})
        if validation:
            self._append_log(validation.get("message", "控制模块校验完成"))
            self._append_log(
                "控制校验："
                f"样本 {validation.get('samples', '--')}，"
                f"duty范围 {'正常' if validation.get('duty_range') else '异常'}，"
                f"效率范围 {'正常' if validation.get('efficiency_range') else '异常'}，"
                f"电压非负 {'正常' if validation.get('voltage_nonnegative') else '异常'}，"
                f"功率非负 {'正常' if validation.get('power_nonnegative') else '异常'}"
            )

        if validation:
            self._append_log(
                f"MPPT\u5e73\u5747\u6548\u7387 {self._format_value(validation.get('mppt_efficiency_mean_pct'), 2)}%\uff0c"
                f"Boost\u5e73\u5747\u6548\u7387 {self._format_value(validation.get('boost_efficiency_mean_pct'), 2)}%\uff0c"
                f"\u5e73\u5747\u7535\u538b\u8bef\u5dee {self._format_value(validation.get('mean_abs_voltage_error_v'), 2)} V"
            )


    def _on_simulation_failed(self, detail: str):
        """在仿真线程报错时显示错误详情。"""
        self._append_log("运行失败，详细错误如下：")
        self._append_log(detail)
        QMessageBox.warning(self, "运行失败", "光伏发电模型运行失败，请查看运行日志。")

    def _on_thread_finished(self):
        """在线程退出后恢复按钮状态并释放引用。"""
        self.btn_run.setEnabled(True)
        self.btn_run.setText("运行光伏发电模型")
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停仿真")
        self.thread = None
        self.worker = None

    def _append_log(self, message: str):
        """向日志框追加一条文本消息。"""
        self.text_log.append(str(message))

    def closeEvent(self, event):
        """在页面关闭时确保工作线程退出并清理旧图。"""
        if self.worker is not None:
            self.worker.shutdown()
        clear_plot_outputs()
        super().closeEvent(event)

    def _collect_pv_params(self):
        """从界面控件收集当前光伏阵列参数。"""
        return {
            "module_Pmax": self.spin_module_pmax.value(),
            "module_Vmp": self.spin_module_vmp.value(),
            "module_Imp": self.spin_module_imp.value(),
            "module_Voc": self.spin_module_voc.value(),
            "module_Isc": self.spin_module_isc.value(),
            "cells_series_per_module": self.spin_cells_series.value(),
            "modules_series": self.spin_modules_series.value(),
            "modules_parallel": self.spin_modules_parallel.value(),
        }

    def _collect_runtime_params(self):
        """从界面控件收集当前仿真运行参数。"""
        return {
            "controller_dt": self.spin_controller_dt.value(),
            "dc_bus_voltage": self.spin_dc_bus_voltage.value(),
            "mppt_enable_irradiance": self.spin_mppt_enable_irr.value(),
            "forecast_days": DEFAULT_RUNTIME_PARAMS.get("forecast_days", 0.0),
            "rolling_batch_steps": DEFAULT_RUNTIME_PARAMS.get("rolling_batch_steps", 1),
            "plot_update_interval_seconds": DEFAULT_RUNTIME_PARAMS.get("plot_update_interval_seconds", 1.0),
            "real_time_playback": DEFAULT_RUNTIME_PARAMS.get("real_time_playback", True),
            "playback_speed": DEFAULT_RUNTIME_PARAMS.get("playback_speed", 1.0),
        }

    def _add_double_spin(self, grid_layout, row, col, label_text, value, minimum, maximum, decimals, suffix):
        """创建带标签的浮点输入框并加入网格布局。"""
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

    def _add_spin(self, grid_layout, row, col, label_text, value, minimum, maximum, suffix):
        """创建带标签的整数输入框并加入网格布局。"""
        label = QLabel(label_text)
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(int(value))
        spin.setSuffix(suffix)
        spin.setMinimumWidth(120)
        grid_layout.addWidget(label, row, col * 2)
        grid_layout.addWidget(spin, row, col * 2 + 1)
        return spin

    @staticmethod
    def _format_value(value, decimals: int = 2) -> str:
        """把数值格式化成便于日志显示的字符串。"""
        if value is None:
            return "--"
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)
