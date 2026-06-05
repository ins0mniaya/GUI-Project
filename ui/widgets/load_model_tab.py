# -*- coding: utf-8 -*-
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from packages.load_forecast.gui_runner import (
    DEFAULT_DATA_PATH,
    DEFAULT_LOAD_PLOT_UPDATE_INTERVAL_SECONDS,
    DEFAULT_SHORT_MODEL_PATH,
    DEFAULT_OUTPUT_PATH,
    LONG_FORECAST_MODE,
    SHORT_FORECAST_MODE,
    clear_plot_outputs,
    default_rolling_start_index,
)
from packages.simulation_process_runtime import run_load_process
from .common_widgets import PlotImageGallery, ProcessSimulationWorkerBase, start_simulation_process, wrap_scroll_area


CONTENT_MIN_WIDTH = 1180
LOG_MIN_WIDTH = 1140


class LoadPredictionWorker(ProcessSimulationWorkerBase):

    def __init__(self, data_path: str, model_path: str, runtime_params: dict):
        """保存负荷预测子进程所需的数据路径、模型路径和运行参数。"""
        super().__init__(
            run_load_process,
            {
                "data_path": data_path,
                "model_path": model_path,
                "output_path": str(DEFAULT_OUTPUT_PATH),
                "runtime_params": runtime_params,
            },
        )
class LoadModelTab(QWidget):
    def __init__(self, host):
        """初始化负荷预测页并立即构建界面。"""
        super().__init__()
        self.host = host
        self.thread = None
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        """构建负荷预测页的文件区、摘要区、图表区与日志区。"""
        inner = QWidget()
        inner.setMinimumWidth(CONTENT_MIN_WIDTH)
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        file_group = QGroupBox("数据与模型文件")
        file_layout = QGridLayout(file_group)
        file_layout.setColumnStretch(1, 1)
        self.edit_data_path = QLineEdit(str(DEFAULT_DATA_PATH))
        self.edit_model_path = QLineEdit(str(DEFAULT_SHORT_MODEL_PATH))
        btn_data = QPushButton("选择数据")
        btn_model = QPushButton("选择模型")
        btn_data.clicked.connect(self._choose_data_file)
        btn_model.clicked.connect(self._choose_model_file)
        file_layout.addWidget(QLabel("历史负荷数据"), 0, 0)
        file_layout.addWidget(self.edit_data_path, 0, 1)
        file_layout.addWidget(btn_data, 0, 2)
        file_layout.addWidget(QLabel("短期GRU权重"), 1, 0)
        file_layout.addWidget(self.edit_model_path, 1, 1)
        file_layout.addWidget(btn_model, 1, 2)
        layout.addWidget(file_group)

        runtime_group = QGroupBox("预测参数")
        runtime_layout = QGridLayout(runtime_group)
        self.spin_max_windows = self._add_spin(
            runtime_layout,
            0,
            0,
            "预测起点序号",
            default_rolling_start_index(DEFAULT_DATA_PATH),
            0,
            100000,
            " h",
        )
        note = QLabel("界面会同时显示短期4小时GRU预测和长期历史7天输入、未来24小时LSTM预测。")
        note.setStyleSheet("color: #64748B;")
        runtime_layout.addWidget(note, 0, 2, 1, 2)
        layout.addWidget(runtime_group)

        summary_group = QGroupBox("负荷预测摘要")
        summary_layout = QGridLayout(summary_group)
        self.label_next = self._add_value_label(summary_layout, 0, 0, "下一预测负荷", "MW")
        self.label_peak = self._add_value_label(summary_layout, 0, 1, "预测峰值负荷", "MW")
        self.label_energy = self._add_value_label(summary_layout, 0, 2, "预测累计用电", "MWh")
        self.label_short_mae = self._add_value_label(summary_layout, 1, 0, "短期4h MAE", "MW")
        self.label_short_nmae = self._add_value_label(summary_layout, 1, 1, "短期4h NMAE", "%")
        self.label_short_accuracy = self._add_value_label(summary_layout, 1, 2, "短期4h 准确率", "%")
        self.label_long_mae = self._add_value_label(summary_layout, 2, 0, "长期24h MAE", "MW")
        self.label_long_nmae = self._add_value_label(summary_layout, 2, 1, "长期24h NMAE", "%")
        self.label_long_accuracy = self._add_value_label(summary_layout, 2, 2, "长期24h 准确率", "%")
        layout.addWidget(summary_group)

        chart_group = QGroupBox("用电负荷模型预测图")
        chart_layout = QVBoxLayout(chart_group)
        self.image_gallery = PlotImageGallery(
            ["短期4小时与长期24小时（历史7天输入）预测"],
            "运行用电负荷模型后显示短期4h与长期168h→24h预测曲线",
            min_image_height=560,
            columns=1,
        )
        chart_layout.addWidget(self.image_gallery)
        layout.addWidget(chart_group)

        control_group = QGroupBox("运行控制")
        control_layout = QHBoxLayout(control_group)
        self.btn_run = QPushButton("运行用电负荷模型")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #0052D9; color: white; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #003BB3; }"
            "QPushButton:disabled { background-color: #C0C4CC; }"
        )
        self.btn_run.clicked.connect(self._start_prediction)
        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(lambda: self.text_log.clear())
        control_layout.addWidget(self.btn_run)
        self.btn_pause = QPushButton("暂停预测")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause_prediction)
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
        self.text_log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        log_layout.addWidget(self.text_log)
        layout.addWidget(log_group)
        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrap_scroll_area(inner))

    def _choose_data_file(self):
        """选择负荷预测使用的历史负荷数据文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择历史负荷数据文件",
            os.path.dirname(self.edit_data_path.text()) or os.getcwd(),
            "Data Files (*.csv *.xlsx *.xls);;All Files (*)",
        )
        if path:
            self.edit_data_path.setText(path)
            self.spin_max_windows.setValue(default_rolling_start_index(path))

    def _choose_model_file(self):
        """选择负荷预测使用的 GRU 权重文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择GRU负荷预测模型权重",
            os.path.dirname(self.edit_model_path.text()) or os.getcwd(),
            "PyTorch Model (*.pth *.pt);;All Files (*)",
        )
        if path:
            self.edit_model_path.setText(path)

    def _start_prediction(self):
        """启动负荷滚动预测线程，运行中再次点击则请求停止。"""
        if self.thread is not None:
            self._stop_prediction()
            return

        data_path = self.edit_data_path.text().strip()
        model_path = self.edit_model_path.text().strip()
        if not os.path.exists(data_path):
            QMessageBox.warning(self, "数据文件不存在", "请先选择有效的历史负荷数据文件。")
            return
        if not os.path.exists(model_path):
            QMessageBox.warning(self, "模型文件不存在", "请先选择有效的负荷预测模型权重文件。")
            return

        self.btn_run.setText("停止用电负荷模型")
        self.btn_run.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("暂停预测")
        self.image_gallery.clear()
        self._reset_summary()
        clear_plot_outputs()
        self._append_log("启动用电负荷模型滚动预测。")

        worker = LoadPredictionWorker(data_path, model_path, self._collect_runtime_params())
        start_simulation_process(
            self,
            worker,
            on_progress=self._append_log,
            extra_signals=((worker.rolling_updated, self._on_rolling_update),),
            on_finished=self._on_prediction_finished,
            on_failed=self._on_prediction_failed,
            on_thread_finished=self._on_thread_finished,
        )

    def _stop_prediction(self):
        """请求负荷滚动预测在当前节拍后停止。"""
        if self.worker is not None:
            self.worker.request_stop()
            self.btn_run.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self._append_log("正在停止用电负荷滚动预测，将在当前节拍退出。")

    def _toggle_pause_prediction(self):
        """暂停或继续负荷滚动预测。"""
        if self.worker is None:
            return
        if self.worker.is_paused():
            self.worker.request_resume()
            self.btn_pause.setText("暂停预测")
            self._append_log("用电负荷滚动预测继续运行。")
        else:
            self.worker.request_pause()
            self.btn_pause.setText("继续预测")
            self._append_log("用电负荷滚动预测已暂停。")

    def _on_rolling_update(self, snapshot: dict):
        """接收滚动预测快照并刷新图像、摘要和日志。"""
        plot_paths = snapshot.get("plot_paths", [])
        if plot_paths:
            self.image_gallery.set_plot_paths(plot_paths)
        self._update_summary(snapshot)
        cycle = snapshot.get("rolling_cycle")
        forecast_type = snapshot.get("forecast_type", "负荷预测")
        algorithm = snapshot.get("algorithm", "GRU")
        start_index = snapshot.get("start_index")
        next_load = self._format_value(snapshot.get("next_load_kw"), 2)
        peak_load = self._format_value(snapshot.get("peak_load_kw"), 2)
        power_unit = snapshot.get("power_unit", "MW")
        if cycle is not None:
            self._append_log(
                f"负荷滚动预测更新：第 {cycle} 个小时窗口，"
                f"{forecast_type}，{algorithm}，起点 {start_index}，"
                f"下一时刻 {next_load} {power_unit}，峰值 {peak_load} {power_unit}"
            )

    def _on_prediction_finished(self, result: dict):
        """在预测完成后刷新图表、摘要指标和日志。"""
        self.image_gallery.set_plot_paths(result.get("plot_paths", []))
        self._update_summary(result)
        if result.get("stopped"):
            self._append_log(f"用电负荷滚动预测已停止，共运行 {result.get('rolling_cycles', 0)} 个小时窗口。")
        self._append_log(f"输出文件：{result.get('output_csv')}")
        self._append_log(f"模型文件：{result.get('model_path')}")
        self._append_log(
            "预测摘要："
            f"{result.get('forecast_type', '负荷预测')}，"
            f"{result.get('algorithm', 'GRU')}，"
            f"样本 {result.get('samples')}，"
            f"输入窗口 {result.get('input_len')} h，"
            f"预测时长 {result.get('forecast_horizon', result.get('output_len'))} h，"
            f"设备 {result.get('device')}"
        )

    def _on_prediction_failed(self, detail: str):
        """在预测线程报错时显示错误详情。"""
        self._append_log("运行失败，详细错误如下：")
        self._append_log(detail)
        QMessageBox.warning(self, "运行失败", "用电负荷模型运行失败，请查看运行日志。")

    def _on_thread_finished(self):
        """在线程退出后恢复按钮状态并释放引用。"""
        self.btn_run.setText("运行用电负荷模型")
        self.btn_run.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("暂停预测")
        self.thread = None
        self.worker = None

    def _collect_runtime_params(self):
        """从界面控件收集当前预测运行参数。"""
        return {
            "prediction_modes": [SHORT_FORECAST_MODE, LONG_FORECAST_MODE],
            "max_windows": self.spin_max_windows.value(),
            "rolling_enabled": True,
            "plot_update_interval_seconds": DEFAULT_LOAD_PLOT_UPDATE_INTERVAL_SECONDS,
        }

    def _update_summary(self, result: dict):
        """用当前预测结果刷新摘要区。"""
        mode_results = result.get("mode_results", {})
        short_result = mode_results.get(SHORT_FORECAST_MODE)
        long_result = mode_results.get(LONG_FORECAST_MODE)
        display_result = long_result or result

        self.label_next.setText(self._format_value(display_result.get("next_load_kw"), 2))
        self.label_peak.setText(self._format_value(display_result.get("peak_load_kw"), 2))
        self.label_energy.setText(self._format_value(display_result.get("energy_kwh"), 2))

        if not short_result and result.get("forecast_mode") == SHORT_FORECAST_MODE:
            short_result = result
        if not long_result and result.get("forecast_mode") == LONG_FORECAST_MODE:
            long_result = result

        self._set_metric_triplet(
            short_result,
            self.label_short_mae,
            self.label_short_nmae,
            self.label_short_accuracy,
        )
        self._set_metric_triplet(
            long_result,
            self.label_long_mae,
            self.label_long_nmae,
            self.label_long_accuracy,
        )

    def _set_metric_triplet(self, result: dict | None, mae_label: QLabel, nmae_label: QLabel, accuracy_label: QLabel):
        metrics = result.get("metrics", {}) if result else {}
        default_style = "font-size: 22px; font-weight: bold; color: #1E293B;"
        for label in (mae_label, nmae_label, accuracy_label):
            label.setStyleSheet(default_style)
            label.setToolTip("")
        mae_label.setText(self._format_value(metrics.get("mae"), 3))
        nmae_label.setText(self._format_value(metrics.get("nmae"), 2))
        accuracy_label.setText(self._format_value(self._accuracy_value(metrics), 2))
        accuracy_label.setToolTip("预测准确率 = 100% - MAPE，越接近 100% 表示预测值整体越接近真实值。")

    def _append_log(self, message: str):
        """向日志框追加一条文本消息。"""
        self.text_log.append(str(message))

    def _reset_summary(self):
        """把摘要区指标重置为占位状态。"""
        for label in (
            self.label_next,
            self.label_peak,
            self.label_energy,
            self.label_short_mae,
            self.label_short_nmae,
            self.label_short_accuracy,
            self.label_long_mae,
            self.label_long_nmae,
            self.label_long_accuracy,
        ):
            label.setText("--")

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.shutdown()
        clear_plot_outputs()
        super().closeEvent(event)

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

    def _add_value_label(self, grid_layout, row, col, title, unit):
        """创建用于展示摘要指标的只读数值卡片。"""
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
        """把数值格式化成便于摘要区显示的字符串。"""
        if value is None:
            return "--"
        try:
            return f"{float(value):.{decimals}f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _accuracy_value(metrics: dict):
        """返回更直观的预测接近度百分比：100 - MAPE。"""
        if not metrics:
            return None
        value = metrics.get("accuracy")
        if value is not None:
            return value
        mape = metrics.get("mape")
        if mape is None:
            return None
        try:
            return max(0.0, 100.0 - float(mape))
        except (TypeError, ValueError):
            return None
