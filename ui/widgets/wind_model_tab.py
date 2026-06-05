# -*- coding: utf-8 -*-
import os

from PySide6.QtCore import Qt
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
    QTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from packages.wind_mppt.gui_runner import (
    DEFAULT_DATA_PATH,
    DEFAULT_RUNTIME_PARAMS,
    DEFAULT_WIND_PARAMS,
    clear_plot_outputs,
)
from packages.simulation_process_runtime import run_wind_process
from .common_widgets import PlotImageGallery, ProcessSimulationWorkerBase, start_simulation_process, wrap_scroll_area


# Keep the log panel visually aligned with the two-column simulation image gallery.
# Increase these values when the main window is wide and the log box looks too narrow.
CONTENT_MIN_WIDTH = 1180
LOG_MIN_WIDTH = 1140
LOG_MIN_HEIGHT = 180
LOG_MAX_HEIGHT = 240


class WindSimulationWorker(ProcessSimulationWorkerBase):
    def __init__(self, data_path: str, wind_params: dict, runtime_params: dict):
        """保存风电滚动仿真子进程所需的输入参数。"""
        super().__init__(
            run_wind_process,
            {
                "data_path": data_path,
                "wind_params": wind_params,
                "runtime_params": runtime_params,
            },
        )
class WindModelTab(QWidget):
    def __init__(self, host):
        """初始化风力发电模型页并立即构建界面。"""
        super().__init__()
        self.host = host
        self.thread = None
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        """构建风力发电模型页的输入区、图表区与日志区。"""
        inner = QWidget()
        inner.setMinimumWidth(CONTENT_MIN_WIDTH)
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        file_group = QGroupBox("数据文件选择")
        file_layout = QGridLayout(file_group)
        file_layout.setColumnStretch(1, 1)
        self.edit_data_path = QLineEdit(str(DEFAULT_DATA_PATH))
        btn_data = QPushButton("选择数据")
        btn_data.clicked.connect(self._choose_data_file)
        file_layout.addWidget(QLabel("历史风速/密度数据"), 0, 0)
        file_layout.addWidget(self.edit_data_path, 0, 1)
        file_layout.addWidget(btn_data, 0, 2)
        layout.addWidget(file_group)

        wind_group = QGroupBox("风机参数")
        wind_layout = QGridLayout(wind_group)
        self.spin_blade_radius = self._add_double_spin(wind_layout, 0, 0, "叶片半径", DEFAULT_WIND_PARAMS["blade_radius"], 0.1, 50.0, 2, " m")
        self.spin_rated_power = self._add_double_spin(wind_layout, 0, 1, "额定功率", DEFAULT_WIND_PARAMS["rated_power"], 100.0, 200000.0, 1, " W")
        self.spin_cut_in = self._add_double_spin(wind_layout, 1, 0, "切入风速", DEFAULT_WIND_PARAMS["cut_in_speed"], 0.1, 20.0, 2, " m/s")
        self.spin_rated_speed = self._add_double_spin(wind_layout, 1, 1, "额定风速", DEFAULT_WIND_PARAMS["rated_speed"], 1.0, 40.0, 2, " m/s")
        self.spin_cut_out = self._add_double_spin(wind_layout, 2, 0, "切出风速", DEFAULT_WIND_PARAMS["cut_out_speed"], 5.0, 60.0, 2, " m/s")
        self.spin_hub_height = self._add_double_spin(wind_layout, 2, 1, "轮毂高度", DEFAULT_WIND_PARAMS["hub_height"], 1.0, 200.0, 1, " m")
        self.spin_ref_height = self._add_double_spin(wind_layout, 3, 0, "参考测风高度", DEFAULT_WIND_PARAMS["reference_wind_height"], 1.0, 200.0, 1, " m")
        self.spin_shear = self._add_double_spin(wind_layout, 3, 1, "风切变指数", DEFAULT_WIND_PARAMS["shear_exponent"], 0.0, 1.0, 3, "")
        self.spin_omega_initial = self._add_double_spin(wind_layout, 4, 0, "初始转速", DEFAULT_WIND_PARAMS["omega_initial"], 1.0, 300.0, 1, " rad/s")
        self.spin_omega_max = self._add_double_spin(wind_layout, 4, 1, "最大转速", DEFAULT_WIND_PARAMS["omega_max"], 1.0, 500.0, 1, " rad/s")
        layout.addWidget(wind_group)

        runtime_group = QGroupBox("运行参数")
        runtime_layout = QGridLayout(runtime_group)
        self.spin_controller_dt = self._add_double_spin(
            runtime_layout,
            0,
            0,
            "控制步长",
            DEFAULT_RUNTIME_PARAMS["controller_dt"],
            0.001,
            1.0,
            4,
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
        layout.addWidget(runtime_group)

        chart_group = QGroupBox("风力发电模型仿真图")
        chart_group.setMinimumWidth(CONTENT_MIN_WIDTH)
        chart_layout = QVBoxLayout(chart_group)
        self.image_gallery = PlotImageGallery(
            ["MPPT转速跟踪", "输出功率对比", "系统性能汇总"],
            "运行风力发电模型后显示仿真图",
            min_image_height=250,
        )
        chart_layout.addWidget(self.image_gallery)
        layout.addWidget(chart_group)

        control_group = QGroupBox("运行控制")
        control_layout = QHBoxLayout(control_group)
        self.btn_run = QPushButton("运行风力发电模型")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #0052D9; color: white; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #003BB3; }"
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
        log_group.setMinimumWidth(CONTENT_MIN_WIDTH)
        log_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(6, 8, 6, 6)
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setMinimumWidth(LOG_MIN_WIDTH)
        self.text_log.setMinimumHeight(LOG_MIN_HEIGHT)
        self.text_log.setMaximumHeight(LOG_MAX_HEIGHT)
        self.text_log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_layout.addWidget(self.text_log)
        layout.addWidget(log_group)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrap_scroll_area(inner))

    def _choose_data_file(self):
        """选择风电仿真使用的历史风速数据文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择历史风速/密度数据文件",
            os.path.dirname(self.edit_data_path.text()) or os.getcwd(),
            "Data Files (*.xlsx *.xls *.csv);;All Files (*)",
        )
        if path:
            self.edit_data_path.setText(path)

    def _start_simulation(self):
        """启动或停止风电滚动仿真线程。"""
        if self.thread is not None:
            self._stop_simulation()
            return

        data_path = self.edit_data_path.text().strip()
        if not os.path.exists(data_path):
            QMessageBox.warning(self, "数据文件不存在", "请先选择有效的历史风速/密度数据文件。")
            return

        self.btn_run.setText("停止风力发电模型")
        self.btn_run.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停仿真")
        self.image_gallery.clear()
        clear_plot_outputs()
        self._append_log("启动风力发电模型。")

        worker = WindSimulationWorker(
            data_path,
            self._collect_wind_params(),
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
            self._append_log("正在停止风力发电滚动仿真，将立即退出。")

    def _toggle_pause_simulation(self):
        """在暂停与继续之间切换风电仿真状态。"""
        if self.worker is None:
            return
        if self.worker.is_paused():
            self.worker.request_resume()
            self.btn_pause.setText("暂停仿真")
            self._append_log("风力发电仿真继续运行。")
        else:
            self.worker.request_pause()
            self.btn_pause.setText("继续仿真")
            self._append_log("风力发电仿真已暂停。")

    def _on_rolling_update(self, snapshot: dict):
        """接收滚动周期快照并刷新图像与日志。"""
        plot_paths = snapshot.get("plot_paths", [])
        if plot_paths:
            self.image_gallery.set_plot_paths(plot_paths)
        step = snapshot.get("step")
        total = snapshot.get("total_steps")
        cycle = snapshot.get("rolling_cycle")
        if cycle is not None:
            peak = self._format_value(snapshot.get("p_out_max_w"), 2)
            mean = self._format_value(snapshot.get("p_out_mean_w"), 2)
            wind = self._format_value(snapshot.get("wind_mean_mps"), 2)
            self._append_log(f"风电滚动仿真更新：第 {cycle} 个15分钟周期，峰值 {peak} W，平均 {mean} W，平均风速 {wind} m/s")
            return
        if step is not None and total is not None:
            peak = self._format_value(snapshot.get("p_out_max_w"), 2)
            mean = self._format_value(snapshot.get("p_out_mean_w"), 2)
            wind = self._format_value(snapshot.get("wind_mean_mps"), 2)
            self._append_log(f"风电滚动仿真更新：{step}/{total} 个15分钟周期，峰值 {peak} W，平均 {mean} W，平均风速 {wind} m/s")

    def _on_simulation_finished(self, result: dict):
        """在仿真完成后刷新最终结果并输出摘要。"""
        self.image_gallery.set_plot_paths(result.get("plot_paths", []))
        if result.get("stopped"):
            self._append_log(f"风力发电滚动仿真已停止，共运行 {result.get('rolling_cycles', 0)} 个15分钟周期。")
        self._append_log(f"输出文件：{result.get('output_csv')}")
        self._append_log(
            "仿真摘要："
            f"峰值 {self._format_value(result.get('p_out_max_w'), 2)} W，"
            f"平均 {self._format_value(result.get('p_out_mean_w'), 2)} W，"
            f"平均风速 {self._format_value(result.get('wind_mean_mps'), 2)} m/s"
        )
    def _on_simulation_failed(self, detail: str):
        """在仿真线程报错时显示错误详情。"""
        self._append_log("运行失败，详细错误如下：")
        self._append_log(detail)
        QMessageBox.warning(self, "运行失败", "风力发电模型运行失败，请查看运行日志。")

    def _on_thread_finished(self):
        """在线程退出后恢复按钮状态并释放引用。"""
        self.btn_run.setEnabled(True)
        self.btn_run.setText("运行风力发电模型")
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

    def _collect_wind_params(self):
        """从界面控件收集当前风机参数。"""
        return {
            "blade_radius": self.spin_blade_radius.value(),
            "rated_power": self.spin_rated_power.value(),
            "cut_in_speed": self.spin_cut_in.value(),
            "rated_speed": self.spin_rated_speed.value(),
            "cut_out_speed": self.spin_cut_out.value(),
            "hub_height": self.spin_hub_height.value(),
            "reference_wind_height": self.spin_ref_height.value(),
            "shear_exponent": self.spin_shear.value(),
            "omega_initial": self.spin_omega_initial.value(),
            "omega_max": self.spin_omega_max.value(),
        }

    def _collect_runtime_params(self):
        """从界面控件收集当前仿真运行参数。"""
        return {
            "controller_dt": self.spin_controller_dt.value(),
            "dc_bus_voltage": self.spin_dc_bus_voltage.value(),
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

    @staticmethod
    def _format_value(value, decimals: int = 2) -> str:
        """把数值格式化成便于日志显示的字符串。"""
        if value is None:
            return "--"
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)
