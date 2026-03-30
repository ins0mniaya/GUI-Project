# main_window.py
# -*- coding: utf-8 -*-
import copy
import logging
from datetime import datetime
from PySide6.QtCore import QMetaObject, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QLabel, 
    QPushButton, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget
)
from config import CAN_CONFIG
from model_prediction.predict_v3 import run_prediction
from ui.widgets import EMSMonitorTab, EMSParamTab, PredictionTab, AboutTab

logger = logging.getLogger(__name__)

class MainWindow(QWidget):
    def __init__(self, device_manager, ui_signaller):
        super().__init__()
        self.device_manager = device_manager
        self.ui_signaller = ui_signaller
        
        # 初始化 UI 更新节流机制：最多 100ms 更新一次
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._process_buffered_data)
        self.update_timer.setInterval(100)  # 100ms
        self.pending_data = None  # 缓存待处理的最新数据
        self._last_ui_system_time = None
        
        self.setup_ui(self)
        if self.ui_signaller is not None:
            self.ui_signaller.log_signal.connect(self._on_ui_log_received)
        
        # 直接监听源信号（方案 A）
        self.device_manager.pcs_driver.data_received.connect(self._on_ems_data_update)
        
        # 本地缓存：维护 EMS 数据
        self._local_cache = {
            "ems": {"EMS_Realtime_Data": {}, "EMS_Running_Data": {}},
        }

    def setup_ui(self, main_widget):
        """全量 UI 逻辑整合函数：包含样式定义、多层级布局、子页面构建及底部控制栏"""
        
        # --- 1. 基础属性与样式表 ---
        if not main_widget.objectName():
            main_widget.setObjectName(u"main_widget")
        main_widget.resize(950, 700)
        main_widget.setWindowTitle("EMS 上位机系统 - 主界面")
        main_widget.setStyleSheet("""
            QWidget { font-family: 'Microsoft YaHei UI'; font-size: 10pt; background-color: #F8F9FB; }
            QGroupBox { 
                font-weight: bold; border: 1px solid #C0C4CC; 
                border-radius: 4px; margin-top: 12px; padding-top: 10px;
                background-color: #FFFFFF;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #2F3E4C; }
            QPushButton { 
                background-color: #4A5D73; color: white; border-radius: 2px; 
                padding: 4px 12px; border: 1px solid #3E4E60;
            }
            QPushButton:hover { background-color: #5D738F; }
            QPushButton:pressed { background-color: #344252; }
            QTabWidget::pane { border: 1px solid #C0C4CC; background: white; }
            QTabBar::tab { 
                background: #E9EDF2; padding: 10px 25px; border: 1px solid #C0C4CC; 
                border-bottom: none; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px;
            }
            QTabBar::tab:selected { background: white; color: #409EFF; font-weight: bold; }
            QComboBox {
                border: 1px solid #C0C4CC; border-radius: 2px;
                background: white; padding-left: 5px; color: #333333;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox:on { background-color: #FFFFFF; color: #409EFF; }
            QComboBox QAbstractItemView {
                background-color: white; selection-background-color: #4A5D73;
                selection-color: white; border: 1px solid #C0C4CC; outline: 0px;
            }
        """)

        # --- 2. 主体垂直布局 ---
        self.layout_main_outer = QVBoxLayout(main_widget)
        self.tabs_main = QTabWidget(main_widget)

        # ================================================================
        # tabs
        # ================================================================
        self.tab_monitor = EMSMonitorTab(self)
        self.tabs_main.addTab(self.tab_monitor, "EMS参数监测")

        self.tab_param = EMSParamTab(self)
        self.tabs_main.addTab(self.tab_param, "EMS参数设置")

        self.tab_pred = PredictionTab(self)
        self.tabs_main.addTab(self.tab_pred, "光伏/负荷预测")

        self.tab_about = AboutTab()
        self.tabs_main.addTab(self.tab_about, "关于我们")

        # ================================================================
        # 底部控制栏
        # ================================================================
        self.widget_bottom_bar = QWidget()
        layout_bottom = QHBoxLayout(self.widget_bottom_bar)
        layout_bottom.addWidget(QLabel("合肥工业大学"))
        lbl_lab = QLabel("交通能源协同控制实验室")
        lbl_lab.setStyleSheet("color: #606266;")
        layout_bottom.addWidget(lbl_lab)
        
        # 使用 Stretch 将左右两端撑开，保持原有的分布感
        layout_bottom.addStretch(1)

        layout_bottom.addWidget(QLabel("连接状态:"))
        self.label_status_indicator = QLabel("未连接")
        self.label_status_indicator.setStyleSheet("font-weight: bold; color: #D92D20;")
        layout_bottom.addWidget(self.label_status_indicator)

        self.btn_connect = QPushButton("连接总线")
        self.btn_connect.setMinimumWidth(100)
        self.btn_connect.clicked.connect(self.on_btn_connect_clicked)
        layout_bottom.addWidget(self.btn_connect)

        # 最终组装
        self.layout_main_outer.addWidget(self.tabs_main)
        self.layout_main_outer.addWidget(self.widget_bottom_bar)
        # 注意: 不使用 QMetaObject.connectSlotsByName，因为所有信号已通过 clicked.connect() 明确连接

    # ================================================================
    # 槽函数：数据刷新、连接按钮、状态更新
    # ================================================================

    def _on_ems_data_update(self, data_type: str, data_dict: dict):
        """处理 EMS 数据更新：直接更新本地缓存，让定时器处理 UI 刷新"""
        try:
            # 更新本地缓存中的 EMS 数据
            if data_type in ["EMS_Running_Data", "EMS_Realtime_Data"]:
                self._local_cache["ems"][data_type] = copy.deepcopy(data_dict)
                # 缓存快照供 UI 定时器使用（避免在这里直接更新 UI）
                self.pending_data = copy.deepcopy(self._local_cache)
                logger.debug(f"EMS 数据已缓存: {data_type}, 字段数={len(data_dict)}")
        except Exception as e:
            logger.error(f"EMS 数据处理异常: {e}", exc_info=True)

    def _process_buffered_data(self):
        """定时器回调：处理缓冲的数据，刷新 UI"""
        if self.pending_data is None:
            return
        
        try:
            self.refresh_realtime_display(self.pending_data)
        except Exception as e:
            logger.error(f"UI 刷新异常: {e}", exc_info=True)

    def refresh_realtime_display(self, pending_data: dict):
        """
        从缓冲数据中提取参数并更新所有 UI 标签
        支持 EMS 实时参数与设备信息展示
        """
        # 提取 EMS 数据
        ems_data = pending_data.get("ems", {})
        realtime_data = ems_data.get("EMS_Realtime_Data", {})
        running_data = ems_data.get("EMS_Running_Data", {})

        # ========== EMS 实时采集参数（ems_monitor_tab） ==========
        # 电压(V) - dc_voltage_1
        # dc_voltage = realtime_data.get("dc_voltage_1", None)
        # if dc_voltage is not None:
        #     self.tab_monitor.host.label_dc_volt_val.setText(f"{dc_voltage:.2f}")
        # else:
        #     self.tab_monitor.host.label_dc_volt_val.setText("--")

        # # 电流(A) - dc_current_1 (需要组合 high 和 low 字节)
        # dc_current = realtime_data.get("dc_current_1", None)
        # if dc_current is not None:
        #     self.tab_monitor.host.label_dc_current_val.setText(f"{dc_current:.2f}")
        # else:
        #     self.tab_monitor.host.label_dc_current_val.setText("--")

        # # 功率(KW) - 由电压*电流/1000 计算（或直接取字段如果有）
        # if dc_voltage is not None and dc_current is not None:
        #     power_kw = (dc_voltage * dc_current) / 1000.0
        #     self.tab_monitor.host.label_power_val.setText(f"{power_kw:.2f}")
        # else:
        #     self.tab_monitor.host.label_power_val.setText("--")

        # # 故障字 - fault_1_high 和 fault_1_low
        # fault_high = realtime_data.get("fault_1_high", None)
        # fault_low = realtime_data.get("fault_1_low", None)
        # if fault_high is not None and fault_low is not None:
        #     fault_code = (fault_high << 16) | fault_low
        #     self.tab_monitor.host.label_fault_val.setText(f"{fault_code:04X}")
        # else:
        #     self.tab_monitor.host.label_fault_val.setText("0000")

        # # ========== 设备配置参数 ==========
        # # 设备类型 (device_type)
        # device_type = realtime_data.get("device_type", None)
        # if device_type is not None:
        #     self.tab_monitor.host.label_device_type_val.setText(str(device_type))
        # else:
        #     self.tab_monitor.host.label_device_type_val.setText("--")

        # # 协议版本 (protocol_version)
        # protocol_ver = realtime_data.get("protocol_version", None)
        # if protocol_ver is not None:
        #     self.tab_monitor.host.label_protocol_ver_val.setText(str(protocol_ver))
        # else:
        #     self.tab_monitor.host.label_protocol_ver_val.setText("--")

        # # 额定电压 (grid_voltage_level)
        # rated_volt = realtime_data.get("grid_voltage_level", None)
        # if hasattr(self.tab_monitor.host, "label_rated_volt_val"):
        #     if rated_volt is not None:
        #         self.tab_monitor.host.label_rated_volt_val.setText(str(rated_volt))
        #     else:
        #         self.tab_monitor.host.label_rated_volt_val.setText("--")

        # 功率因数 (power_factor)
        # power_factor = realtime_data.get("power_factor", None)
        # if hasattr(self.tab_monitor.host, "label_power_factor_val"):
        #     if power_factor is not None:
        #         self.tab_monitor.host.label_power_factor_val.setText(f"{power_factor:.3f}")
        #     else:
        #         self.tab_monitor.host.label_power_factor_val.setText("--")

        # 每日发电 (total_gen_wh_low)
        # day_power = realtime_data.get("total_gen_wh_low", None)
        # if hasattr(self.tab_monitor.host, "label_day_power_val"):
        #     if day_power is not None:
        #         self.tab_monitor.host.label_day_power_val.setText(f"{day_power:.0f}")
        #     else:
        #         self.tab_monitor.host.label_day_power_val.setText("--")

        # 运行状态 (run_state_high + run_state_low)
        # run_state_high = realtime_data.get("run_state_high", None)
        # run_state_low = realtime_data.get("run_state_low", None)
        # if hasattr(self.tab_monitor.host, "label_run_status_val"):
        #     if run_state_high is not None and run_state_low is not None:
        #         run_state = (run_state_high << 16) | run_state_low
        #         status_text = self._get_run_state_text(run_state)
        #         self.tab_monitor.host.label_run_status_val.setText(status_text)
        #     else:
        #         self.tab_monitor.host.label_run_status_val.setText("待机")

        # # IGBT 温度 (igbt_temp)
        # igbt_temp = realtime_data.get("igbt_temp", None)
        # if hasattr(self.tab_monitor.host, "label_temp_igbt_val"):
        #     if igbt_temp is not None:
        #         self.tab_monitor.host.label_temp_igbt_val.setText(f"{igbt_temp:.0f} °C")
        #     else:
        #         self.tab_monitor.host.label_temp_igbt_val.setText("-- °C")

        # # 散热器温度 (radiator_temp)
        # radiator_temp = realtime_data.get("radiator_temp", None)
        # if hasattr(self.tab_monitor.host, "label_temp_radiator_val"):
        #     if radiator_temp is not None:
        #         self.tab_monitor.host.label_temp_radiator_val.setText(f"{radiator_temp:.0f} °C")
        #     else:
        #         self.tab_monitor.host.label_temp_radiator_val.setText("-- °C")

        # ========== EMS_Running_Data ==========
        # 系统时间 (system_time) 来自 EMS_Running_Data
        system_time = running_data.get("system_time", None)
        if hasattr(self.tab_monitor.host, "label_sys_time_val"):
            if system_time is not None:
                self.tab_monitor.host.label_sys_time_val.setText(str(system_time))
            else:
                self.tab_monitor.host.label_sys_time_val.setText("00:00:00")


    def on_btn_connect_clicked(self):
        """连接按钮点击事件：连接/断开 CAN 总线"""
        if self.device_manager.is_connected:
            # 已连接，执行断开操作
            self.device_manager.disconnect_can()
            self.update_timer.stop()
            self.btn_connect.setText("连接总线")
            self.label_status_indicator.setText("未连接")
            self.label_status_indicator.setStyleSheet("font-weight: bold; color: #D92D20;")
            logger.info("CAN 总线已断开连接")
        else:
            # 未连接，执行连接操作
            # 从 CAN_CONFIG 中获取通道和波特率（这里假设使用默认配置）
            channel = CAN_CONFIG.get("channel", "can0")
            bitrate = CAN_CONFIG.get("bitrate", 500000)
            bitrate_str = f"{bitrate // 1000}K"
            
            # 调用设备管理器的连接方法
            self.device_manager.connect_can(channel, bitrate_str)
            
            # 启动 UI 更新定时器
            if not self.update_timer.isActive():
                self.update_timer.start()
            
            self.btn_connect.setText("断开连接")
            logger.info(f"正在连接 CAN 总线: channel={channel}, bitrate={bitrate_str}")

    def update_connection_ui(self, is_connected: bool, channel: str, status_msg: str):
        """更新连接状态 UI（由 device_manager.status_changed 信号触发）"""
        try:
            if is_connected:
                self.label_status_indicator.setText("已连接")
                self.label_status_indicator.setStyleSheet("font-weight: bold; color: #059669;")
                self.btn_connect.setText("断开连接")
                if not self.update_timer.isActive():
                    self.update_timer.start()
                logger.info(f"连接状态已更新: {status_msg} ({channel})")
            else:
                self.label_status_indicator.setText("未连接")
                self.label_status_indicator.setStyleSheet("font-weight: bold; color: #D92D20;")
                self.btn_connect.setText("连接总线")
                self.update_timer.stop()
                logger.info(f"连接状态已更新: {status_msg}")
        except Exception as e:
            logger.error(f"更新连接UI异常: {e}", exc_info=True)

    def _get_run_state_text(self, run_state: int) -> str:
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：运行状态文案映射逻辑。
        return "待机"

    def _on_ui_log_received(self, level: str, message: str):
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：UI 日志处理逻辑。
        pass

    # ================================================================
    # 槽函数：EMS参数管理（参数设置Tab）
    # ================================================================

    def _on_read_current_params(self):
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：参数读取槽函数。
        pass

    def _on_save_params(self):
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：参数保存槽函数。
        pass

    def _on_reset_params(self):
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：参数重置槽函数。
        pass

    # ================================================================
    # 槽函数：光伏/负荷预测（预测Tab）
    # ================================================================

    def _on_start_pv_predict(self):
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：光伏预测槽函数。
        pass

    def _on_start_load_predict(self):
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：负荷预测槽函数。
        pass

    def _on_clear_pred_log(self):
        """已停用：仅保留系统时间与连接状态的实时显示链路。"""
        # 注释停用：预测日志清理槽函数。
        pass
    
    