# pcs_single_tab.py
# -*- coding: utf-8 -*-
"""
单台 PCS 完整控制页（PCSSingleTab）
- 组合 EMSMonitorTab（实时监测）+ EMSParamTab（参数设置）两个子 Tab
- 持有自身对应的 DeviceManager 引用，数据刷新/命令完全隔离
- 本身充当 host 角色，供 EMSMonitorTab/EMSParamTab 在其上注册控件

与原 MainWindow 的主要差异：
  - 每台 PCS 有独立的连接按钮、对时按钮、连接状态标签
  - data_received 信号 pcs_id 过滤后再更新本地缓存 & UI
  - 底部状态栏只展示本台 PCS 的信息
"""

import logging
import time
from datetime import datetime, timedelta

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTabWidget, QVBoxLayout, QWidget
)

from .ems_monitor_tab import EMSMonitorTab
from .ems_param_tab import EMSParamTab

logger = logging.getLogger(__name__)


def _set_spin(widget, value):
    if value is not None:
        widget.setValue(float(value))


def _set_combo(widget, value):
    if value is not None:
        idx = int(value)
        if 0 <= idx < widget.count():
            widget.setCurrentIndex(idx)


class PCSSingleTab(QWidget):
    """单台 PCS 的完整监控与配置页面，充当 host。"""

    def __init__(self, device_manager, pcs_id: int, pcs_name: str, parent=None):
        """
        device_manager : 该 PCS 的 DeviceManager 实例
        pcs_id         : 数字 ID（1-5）
        pcs_name       : 显示名称（"PCS-1" 等）
        """
        super().__init__(parent)
        self.device_manager = device_manager
        self.pcs_id   = pcs_id
        self.pcs_name = pcs_name

        # UI 更新节流（250ms）
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._process_buffered_data)
        self.update_timer.setInterval(250)
        self.pending_ems = False
        self._last_ui_system_time = None
        self._pcs_time_anchor_dt = None
        self._pcs_time_anchor_monotonic = None

        # 本地数据缓存
        self._local_cache: dict = {"Total_PCS_Info": {}}

        self._build_ui()
        self._connect_signals()

    # ================================================================
    # UI 构建
    # ================================================================

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 顶部：醒目设备标题栏 ──
        title_bar = QWidget()
        title_bar.setFixedHeight(48)
        title_bar.setStyleSheet("background-color: #2C3E50;")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(16, 0, 16, 0)
        title_layout.setSpacing(10)

        lbl_title = QLabel(self.pcs_name)
        lbl_title.setStyleSheet(
            "font-size: 15pt; font-weight: bold; color: white; background: transparent; letter-spacing: 2px;"
        )
        title_layout.addWidget(lbl_title)

        lbl_subtitle = QLabel("EMS 上位机监控与参数配置")
        lbl_subtitle.setStyleSheet("font-size: 10pt; color: #E0F0FF; background: transparent;")
        title_layout.addWidget(lbl_subtitle)

        title_layout.addStretch()

        # 返回总览按钮
        btn_back = QPushButton("返回总览")
        btn_back.setFixedHeight(32)
        btn_back.setFixedWidth(120)
        btn_back.setStyleSheet("""
            QPushButton {
                background-color: #4A6582;
                color: white;
                border: 1px solid #607D9F;
                border-radius: 4px;
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #607D9F;
            }
        """)
        btn_back.clicked.connect(self._on_back_to_overview)
        title_layout.addWidget(btn_back)

        outer.addWidget(title_bar)

        # ── 主 Tab：监测 + 设置 ──
        self.main_tabs = QTabWidget()
        self.tab_monitor = EMSMonitorTab(self)
        self.tab_param   = EMSParamTab(self)
        self.main_tabs.addTab(self.tab_monitor, "EMS 参数监测")
        self.main_tabs.addTab(self.tab_param,   "EMS 参数设置")
        outer.addWidget(self.main_tabs)

        # ── 底部状态栏 ──
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("""
            QWidget#statusBar { background-color: #F0F2F5; border-top: 1px solid #E4E7ED; }
            QPushButton {
                background-color: #4A5D73; color: white; border-radius: 2px;
                padding: 3px 10px; border: 1px solid #3E4E60;
            }
            QPushButton:hover   { background-color: #5D738F; }
            QPushButton:pressed { background-color: #344252; }
            QLabel { background-color: transparent; }
        """)
        bar.setObjectName("statusBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 0, 12, 0)
        bar_layout.setSpacing(8)

        # 左：PCS 名称
        lbl_name = QLabel(self.pcs_name)
        lbl_name.setStyleSheet("font-weight: bold; color: #409EFF;")
        bar_layout.addWidget(lbl_name)

        bar_layout.addSpacing(8)

        # 运行状态
        bar_layout.addWidget(QLabel("运行状态:"))
        self.label_run_status_val = QLabel("未知")
        self.label_run_status_val.setStyleSheet(
            "font: 10pt 'Microsoft YaHei UI'; color: #64748B; font-weight: bold;"
        )
        bar_layout.addWidget(self.label_run_status_val)

        bar_layout.addSpacing(16)

        # PCS 时间
        bar_layout.addWidget(QLabel("PCS时间:"))
        self.label_sys_time_val = QLabel("xxxx-xx-xx xx:xx:xx")
        self.label_sys_time_val.setStyleSheet(
            "font: 10pt 'Microsoft YaHei UI'; color: #1E293B; font-weight: bold;"
        )
        bar_layout.addWidget(self.label_sys_time_val)

        # 对时按钮
        self.btn_sync_time = QPushButton("对时")
        self.btn_sync_time.setFixedWidth(60)
        self.btn_sync_time.clicked.connect(self._on_sync_time_clicked)
        bar_layout.addWidget(self.btn_sync_time)

        bar_layout.addStretch()

        # 连接状态
        bar_layout.addWidget(QLabel("连接状态:"))
        self.label_status_indicator = QLabel("未连接")
        self.label_status_indicator.setStyleSheet("font-weight: bold; color: #D92D20;")
        bar_layout.addWidget(self.label_status_indicator)

        self.btn_connect = QPushButton("连接总线")
        self.btn_connect.setFixedWidth(90)
        self.btn_connect.clicked.connect(self._toggle_connection)
        bar_layout.addWidget(self.btn_connect)

        outer.addWidget(bar)

    # ================================================================
    # 信号连接
    # ================================================================

    def _connect_signals(self):
        # status_changed(pcs_id, is_connected, desc)
        self.device_manager.status_changed.connect(self._on_status_changed)
        # data_received(pcs_id, data_type, data_dict)  — 直接连接驱动层原始信号
        self.device_manager.rs485_driver.data_received.connect(
            self._on_raw_data_received
        )

    # ================================================================
    # 槽函数
    # ================================================================

    def _on_status_changed(self, pcs_id: int, is_connected: bool, desc: str):
        if pcs_id != self.pcs_id:
            return
        self._update_connection_ui(is_connected)

    def _on_raw_data_received(self, data_type: str, data_dict: dict):
        """直接从 rs485_driver 收到原始数据（不含 pcs_id），直接缓存"""
        self._local_cache["Total_PCS_Info"][data_type] = data_dict
        self.pending_ems = True

        # 参数回填（即时，不经过节流）
        if data_type == "basic setting parameters":
            self._fill_basic_setting_params(data_dict)
        elif data_type == "system work mode1 parameters":
            self._fill_system_work_mode1_params(data_dict)
        elif data_type == "system work mode2 parameters":
            self._fill_system_work_mode2_params(data_dict)
        elif data_type == "advanced setting parameters":
            self._fill_advanced_setting_params(data_dict)
        elif data_type == "grid setting parameters":
            self._fill_grid_setting_params(data_dict)
        elif data_type == "battery setting parameters":
            self._fill_battery_setting_params(data_dict)
        elif data_type == "protection setting parameters":
            self._fill_protection_setting_params(data_dict)
        elif data_type.endswith(" write result"):
            self._on_write_result(data_type, data_dict)

    def _toggle_connection(self):
        if self.device_manager.is_connected:
            self.device_manager.disconnect_device()
        else:
            self.device_manager.connect_device()

    def _on_sync_time_clicked(self):
        if not self.device_manager.is_connected:
            QMessageBox.warning(self, "未连接", f"{self.pcs_name} 未连接，无法对时")
            return
        self.device_manager.sync_time()

    def _on_back_to_overview(self):
        """返回总览页"""
        main_win = self
        while main_win.parent() is not None:
            main_win = main_win.parent()
        if hasattr(main_win, 'tabs_main'):
            main_win.tabs_main.setCurrentIndex(0)

    def _update_connection_ui(self, is_connected: bool):
        if is_connected:
            self.label_status_indicator.setText("已连接")
            self.label_status_indicator.setStyleSheet("font-weight: bold; color: #059669;")
            self.btn_connect.setText("断开连接")
            if not self.update_timer.isActive():
                self.update_timer.start()
        else:
            self.label_status_indicator.setText("未连接")
            self.label_status_indicator.setStyleSheet("font-weight: bold; color: #D92D20;")
            self.btn_connect.setText("连接总线")
            self.update_timer.stop()
            self._pcs_time_anchor_dt = None
            self._pcs_time_anchor_monotonic = None
            self._last_ui_system_time = None

    # ================================================================
    # 定时刷新
    # ================================================================

    def _process_buffered_data(self):
        if self.pending_ems:
            self._update_ems_display(self._local_cache["Total_PCS_Info"])
            self.pending_ems = False
        self._refresh_system_time_display()

    def _refresh_system_time_display(self):
        if self._pcs_time_anchor_dt is None or self._pcs_time_anchor_monotonic is None:
            return
        elapsed = max(0.0, time.monotonic() - self._pcs_time_anchor_monotonic)
        current_dt = self._pcs_time_anchor_dt + timedelta(seconds=int(elapsed))
        current_text = current_dt.strftime("%Y-%m-%d %H:%M:%S")
        if current_text != self._last_ui_system_time:
            self.label_sys_time_val.setText(current_text)
            self._last_ui_system_time = current_text

    # ================================================================
    # EMS 数据刷新（与原 MainWindow 保持同样的 label 绑定逻辑）
    # ================================================================

    def _update_ems_display(self, pcs_data: dict):
        monitor = self.tab_monitor.host   # host == self

        PCS_info = pcs_data.get("PCS device information parameters", {})
        if PCS_info:
            run_state_str = str(PCS_info.get("run_state_str", "未知"))
            self.label_run_status_val.setText(run_state_str)
            if "故障" in run_state_str:
                color = "#D92D20"
            elif "正常" in run_state_str:
                color = "#059669"
            elif "自检" in run_state_str or "预警" in run_state_str:
                color = "#D97706"
            else:
                color = "#64748B"
            self.label_run_status_val.setStyleSheet(
                f"font: 10pt 'Microsoft YaHei UI'; color: {color}; font-weight: bold;"
            )
            monitor.label_fault_code.setText(
                str(PCS_info.get("merged_fault_code_binary"))
            )

        time_params = pcs_data.get("time synchronization parameters", {})
        if time_params:
            pcs_time_text = str(time_params.get("system_time_str", "")).strip()
            if pcs_time_text:
                try:
                    pcs_dt = datetime.strptime(pcs_time_text, "%Y-%m-%d %H:%M:%S")
                    self._pcs_time_anchor_dt = pcs_dt
                    self._pcs_time_anchor_monotonic = time.monotonic()
                except ValueError:
                    self.label_sys_time_val.setText(pcs_time_text)
                    self._last_ui_system_time = pcs_time_text

        PV_params = pcs_data.get("PV parameters", {})
        if PV_params:
            monitor.label_pv_power.setText(str(PV_params.get("p_pv_total")))
            monitor.label_pv_power_detail.setText(str(PV_params.get("p_pv_total")))

        grid_params = pcs_data.get("grid parameters", {})
        if grid_params:
            monitor.label_freq_grid.setText(str(grid_params.get("freq_grid")))
            monitor.label_grid_status.setText(str(grid_params.get("grid_status_str")))
            monitor.label_grid_power.setText(str(grid_params.get("p_grid")))
            monitor.label_grid_power_detail.setText(str(grid_params.get("p_grid")))
            monitor.label_v_grid_a.setText(str(grid_params.get("v_grid_a")))
            monitor.label_v_grid_b.setText(str(grid_params.get("v_grid_b")))
            monitor.label_v_grid_c.setText(str(grid_params.get("v_grid_c")))
            monitor.label_freq_grid_a.setText(str(grid_params.get("freq_grid_a")))
            monitor.label_freq_grid_b.setText(str(grid_params.get("freq_grid_b")))
            monitor.label_freq_grid_c.setText(str(grid_params.get("freq_grid_c")))
            monitor.label_i_grid_a.setText(str(grid_params.get("i_grid_a")))
            monitor.label_i_grid_b.setText(str(grid_params.get("i_grid_b")))
            monitor.label_i_grid_c.setText(str(grid_params.get("i_grid_c")))
            monitor.label_p_grid_a.setText(str(grid_params.get("p_grid_a")))
            monitor.label_p_grid_b.setText(str(grid_params.get("p_grid_b")))
            monitor.label_p_grid_c.setText(str(grid_params.get("p_grid_c")))
            monitor.label_i_limiter_l1.setText(str(grid_params.get("i_limiter_l1")))
            monitor.label_i_limiter_l2.setText(str(grid_params.get("i_limiter_l2")))
            monitor.label_p_limiter_l1.setText(str(grid_params.get("p_limiter_l1")))
            monitor.label_p_limiter_l2.setText(str(grid_params.get("p_limiter_l2")))
            monitor.label_p_limiter_total.setText(str(grid_params.get("p_limiter_total")))
            monitor.label_e_grid_buy_day.setText(str(grid_params.get("e_grid_buy_day")))
            monitor.label_e_grid_buy_month.setText(str(grid_params.get("e_grid_buy_month")))
            monitor.label_e_grid_buy_year.setText(str(grid_params.get("e_grid_buy_year")))
            monitor.label_e_grid_buy_total.setText(str(grid_params.get("e_grid_buy_total")))
            monitor.label_e_grid_sell_day.setText(str(grid_params.get("e_grid_sell_day")))
            monitor.label_e_grid_sell_month.setText(str(grid_params.get("e_grid_sell_month")))
            monitor.label_e_grid_sell_year.setText(str(grid_params.get("e_grid_sell_year")))
            monitor.label_e_grid_sell_total.setText(str(grid_params.get("e_grid_sell_total")))

        load_params = pcs_data.get("load parameters", {})
        if load_params:
            monitor.label_load_power.setText(str(load_params.get("p_load_total")))

        bat_params = pcs_data.get("battery parameters", {})
        if bat_params:
            monitor.label_bat_status_val.setText(str(bat_params.get("bat_status_str")))
            monitor.label_bat_soc.setText(str(bat_params.get("soc_bat")))
            monitor.label_bat_soc_detail.setText(str(bat_params.get("soc_bat")))
            monitor.label_dc_volt_val.setText(str(bat_params.get("v_bat")))
            monitor.label_dc_current_val.setText(str(bat_params.get("i_bat")))
            monitor.label_bat_power.setText(str(bat_params.get("p_bat")))
            monitor.label_bat_temp.setText(str(bat_params.get("temp_bat")))
            monitor.label_bat_chg_day.setText(str(bat_params.get("e_bat_chg_day")))
            monitor.label_bat_dis_day.setText(str(bat_params.get("e_bat_dis_day")))
            monitor.label_bat_chg_total.setText(str(bat_params.get("e_bat_chg_total")))
            monitor.label_bat_dis_total.setText(str(bat_params.get("e_bat_dis_total")))

        bms_params = pcs_data.get("group1 BMS parameters", {})
        if bms_params:
            monitor.label_v_cell_mean.setText(str(bms_params.get("v_cell_mean")))
            monitor.label_i_cell_total.setText(str(bms_params.get("i_cell_total")))
            monitor.label_soc_bms.setText(str(bms_params.get("soc_bms")))
            monitor.label_dump_energy.setText(str(bms_params.get("dump_energy")))
            monitor.label_soh_bms.setText(str(bms_params.get("soh_bms")))
            monitor.label_temp_cell_avg.setText(str(bms_params.get("temp_cell_avg")))
            monitor.label_charging_voltage.setText(str(bms_params.get("charging_voltage")))
            monitor.label_discharge_voltage.setText(str(bms_params.get("discharge_voltage")))
            monitor.label_charging_current_limiting.setText(
                str(bms_params.get("charging_current_limiting"))
            )
            monitor.label_discharge_current_limiting.setText(
                str(bms_params.get("discharge_current_limiting"))
            )
            monitor.label_lithium_battery_alarm_position.setText(
                str(bms_params.get("lithium_battery_alarm_position"))
            )
            monitor.label_lithium_battery_fault_location.setText(
                str(bms_params.get("lithium_battery_fault_location"))
            )
            monitor.label_lithium_battery_symbol_2.setText(
                str(bms_params.get("lithium_battery_symbol_2"))
            )
            monitor.label_module_numbers.setText(str(bms_params.get("module_numbers")))

        other_params = pcs_data.get("other information parameters", {})
        if other_params:
            monitor.label_temp_trans_val.setText(
                str(other_params.get("transformer_temperature"))
            )
            monitor.label_temp_boost_val.setText(
                str(other_params.get("BOOST_inductance _temperature"))
            )
            monitor.label_temp_inv_val.setText(
                str(other_params.get("INV_inductance_temperature"))
            )
            monitor.label_temp_internal_val.setText(
                str(other_params.get("internal_temperature"))
            )
            monitor.label_temp_rad1_val.setText(
                str(other_params.get("radiator_temperature1"))
            )
            monitor.label_temp_rad2_val.setText(
                str(other_params.get("radiator_temperature2"))
            )
            monitor.label_temp_rad3_val.setText(
                str(other_params.get("radiator_temperature3"))
            )
            monitor.label_temp_rad4_val.setText(
                str(other_params.get("radiator_temperature4"))
            )

    # ================================================================
    # 写结果提示
    # ================================================================

    def _on_write_result(self, data_type: str, data_dict: dict):
        success = data_dict.get("success", False)
        group_name = data_dict.get("group_name", data_type)
        title = f"[{self.pcs_name}] {'保存成功' if success else '保存失败'}"
        if success:
            QMessageBox.information(self, title, f"参数已成功写入\n组名：{group_name}")
        else:
            QMessageBox.warning(self, title, f"参数写入失败，请检查通讯\n组名：{group_name}")

    # ================================================================
    # 参数回填（host 接口，供 EMSParamTab 通过 host. 访问）
    # ================================================================

    def _fill_basic_setting_params(self, data: dict):
        """填充基础设置参数 (16001)"""
        h = self
        _set_spin(h.spin_power_factor_regulation,   data.get("power_factor_regulation"))
        _set_spin(h.spin_active_power_regulation,   data.get("active_power_regulation"))
        _set_spin(h.spin_reactive_power_regulation, data.get("reactive_power_regulation"))
        _set_spin(h.spin_apparent_power_regulation, data.get("apparent_power_regulation"))
        _set_combo(h.combo_switch_on_off,  data.get("switch_on_off_enable"))
        _set_combo(h.combo_factory_reset,  data.get("factory_reset_enable"))
        _set_spin(h.spin_self_checking_time,        data.get("self_checking_time"))
        _set_combo(h.combo_pv_shadow_scanning,     data.get("pv_shadow_scanning_function"))
        _set_spin(h.spin_scan_period,               data.get("scan_period"))
        _set_spin(h.spin_mppt_numbers,              data.get("mppt_numbers"))
        _set_combo(h.combo_meter_enable,            data.get("meter_enable"))
        _set_combo(h.combo_rcd_enable,              data.get("rcd_enable"))
        _set_combo(h.combo_riso_enable,             data.get("riso_enable"))
        _set_combo(h.combo_open_loop_instruction,   data.get("open_loop_instruction"))
        _set_combo(h.combo_manual_removal_fault,    data.get("manual_removal_permanent_fault"))

    def _fill_system_work_mode1_params(self, data: dict):
        """填充系统工作模式参数1 (16067)"""
        h = self
        _set_combo(h.combo_battery_type,           data.get("battery_type"))
        _set_combo(h.combo_battery_mode,           data.get("battery_mode"))
        _set_combo(h.combo_system_work_mode,       data.get("system_work_mode"))
        _set_combo(h.combo_energy_pattern,         data.get("energy_pattern"))
        _set_combo(h.combo_solar_sell,            data.get("solar_sell"))
        _set_spin(h.spin_max_solar_power,         data.get("max_solar_power"))
        _set_spin(h.spin_max_sell_power,          data.get("max_sell_power"))
        _set_spin(h.spin_zero_export_power,       data.get("zero_export_power"))

    def _fill_system_work_mode2_params(self, data: dict):
        """填充系统工作模式参数2 (16075)"""
        h = self
        _set_combo(h.combo_grid_output_limit_enable, data.get("grid_output_power_limit_enable"))
        _set_spin(h.spin_grid_output_power_limit, data.get("grid_output_power_limit"))
        _set_combo(h.combo_time_of_use_enable,    data.get("time_of_use_enable"))
        # 功率段 1-6
        for i in range(1, 7):
            _set_spin(getattr(h, f"spin_power{i}"), data.get(f"power{i}"))
        # 电池电压限制 1-6
        for i in range(1, 7):
            _set_spin(getattr(h, f"spin_batt_v{i}"), data.get(f"batt_v{i}"))
        # 电池电量限制 1-6
        for i in range(1, 7):
            _set_spin(getattr(h, f"spin_batt_percent{i}"), data.get(f"batt_percent{i}"))

    def _fill_advanced_setting_params(self, data: dict):
        """填充高级设置参数 (16164)"""
        h = self
        _set_combo(h.combo_parallel_enable,         data.get("parallel_enable"))
        _set_spin(h.spin_parallel_serial_number,    data.get("parallel_serial_number"))
        _set_combo(h.combo_master_slave,            data.get("master_slave"))
        _set_combo(h.combo_phase_select,            data.get("phase_select"))
        _set_combo(h.combo_three_phase_parallel_enable, data.get("three_phase_parallel_enable"))
        _set_combo(h.combo_a_phase_enable,          data.get("a_phase_enable"))
        _set_combo(h.combo_b_phase_enable,          data.get("b_phase_enable"))
        _set_combo(h.combo_c_phase_enable,          data.get("c_phase_enable"))

    def _fill_grid_setting_params(self, data: dict):
        h = self
        _set_combo(h.combo_grid_mode, data.get("grid_mode"))
        _set_combo(h.combo_grid_type, data.get("grid_type"))
        _set_spin(h.spin_over_frequency_protection,  data.get("over_frequency_protection"))
        _set_spin(h.spin_under_frequency_protection, data.get("under_frequency_protection"))
        _set_spin(h.spin_over_voltage_protection,    data.get("over_voltage_protection"))
        _set_spin(h.spin_under_voltage_protection,   data.get("under_voltage_protection"))
        _set_combo(h.combo_off_grid_voltage,         data.get("off_grid_voltage"))
        _set_combo(h.combo_grid_frequency, data.get("grid_frequency"))
        _set_spin(h.spin_restore_connection_time,  data.get("restore_connection_time"))

    def _fill_battery_setting_params(self, data: dict):
        h = self
        _set_combo(h.combo_battery_type, data.get("battery_type"))
        _set_combo(h.combo_battery_mode, data.get("battery_mode"))
        _set_combo(h.combo_activate_battery, data.get("activate_battery"))
        _set_spin(h.spin_battery_capacity, data.get("battery_capacity"))
        _set_spin(h.spin_max_a_charge, data.get("max_a_charge"))
        _set_spin(h.spin_max_a_discharge, data.get("max_a_discharge"))
        _set_combo(h.combo_gen_charge, data.get("gen_charge"))
        _set_combo(h.combo_gen_signal, data.get("gen_signal"))
        _set_spin(h.spin_generator_charging_start_capacity_point, data.get("generator_charging_start_capacity_point"))
        _set_spin(h.spin_generator_to_battery_charging_current, data.get("generator_to_battery_charging_current"))
        _set_spin(h.spin_max_run_time, data.get("max_run_time"))
        _set_spin(h.spin_cooling_time, data.get("cooling_time"))
        _set_combo(h.combo_grid_charge, data.get("grid_charge"))
        _set_combo(h.combo_grid_signal, data.get("grid_signal"))
        _set_spin(h.spin_utility_charging_start_capacity_point, data.get("utility_charging_start_capacity_point"))
        _set_spin(h.spin_utility_to_battery_charging_current, data.get("utility_to_battery_charging_current"))
        _set_spin(h.spin_generator_charging_start_voltage_point, data.get("generator_charging_start_voltage_point"))
        _set_spin(h.spin_utility_charging_start_voltage_point, data.get("utility_charging_start_voltage_point"))
        lithium_protocol = data.get("lithium_protocol")
        if lithium_protocol is not None:
            protocol_index = h.combo_lithium_protocol.findData(lithium_protocol)
            if protocol_index >= 0:
                h.combo_lithium_protocol.setCurrentIndex(protocol_index)
        _set_spin(h.spin_shutdown_percent, data.get("shutdown_percent"))
        _set_spin(h.spin_restart_percent, data.get("restart_percent"))
        _set_spin(h.spin_low_batt_percent, data.get("low_batt_percent"))
        _set_spin(h.spin_shutdown_voltage, data.get("shutdown_voltage"))
        _set_spin(h.spin_restart_voltage, data.get("restart_voltage"))
        _set_spin(h.spin_low_batt_voltage, data.get("low_batt_voltage"))
        _set_spin(h.spin_float_voltage, data.get("float_voltage"))
        _set_spin(h.spin_absorption_voltage, data.get("absorption_voltage"))
        _set_spin(h.spin_equalization_voltage, data.get("equalization_voltage"))
        _set_spin(h.spin_equalization_days, data.get("equalization_days"))
        _set_spin(h.spin_equalization_hours, data.get("equalization_hours"))
        _set_spin(h.spin_tempco, data.get("tempco"))
        _set_spin(h.spin_battery_resistance_value, data.get("battery_resistance_value"))

    def _fill_protection_setting_params(self, data: dict):
        h = self
        _set_combo(h.combo_single_multiple_level_selection, data.get("single_multiple_level_selection"))
        _set_spin(h.spin_uvp_recovery, data.get("uvp_recovery"))
        _set_spin(h.spin_ovp_recovery, data.get("ovp_recovery"))
        _set_spin(h.spin_ufp_recovery, data.get("ufp_recovery"))
        _set_spin(h.spin_ofp_recovery, data.get("ofp_recovery"))
        for level in range(1, 6):
            for prot in ("uvp", "ovp", "ufp", "ofp"):
                _set_spin(getattr(h, f"spin_{prot}_l{level}_value"), data.get(f"{prot}_l{level}_value"))
                _set_spin(getattr(h, f"spin_{prot}_l{level}_time"),  data.get(f"{prot}_l{level}_time"))
        _set_combo(h.combo_ovp_10min_enable,   data.get("ovp_10min_enable"))
        _set_spin(h.spin_ovp_10min_value,      data.get("ovp_10min_value"))
        _set_spin(h.spin_ovp_10min_recovery,   data.get("ovp_10min_recovery"))

    # ================================================================
    # EMSParamTab host 接口：读/写命令转发给本台 DM
    # ================================================================

    def _on_read_current_params(self):
        if not self.device_manager.is_connected:
            QMessageBox.warning(self, "未连接", f"{self.pcs_name} 未连接，请先连接设备")
            return
        tab_index = self.tab_param.param_tabs.currentIndex()
        cmd_map = {
            0: "get_basic_setting_parameters",
            1: "get_system_work_mode1_parameters",
            2: "get_system_work_mode2_parameters",
            3: "get_advanced_setting_parameters",
            4: "get_battery_setting_parameters",
            5: "get_grid_setting_parameters",
            6: "get_protection_setting_parameters",
        }
        cmd = cmd_map.get(tab_index)
        if cmd:
            ok = self.device_manager.enqueue_read_command(cmd)
            if not ok:
                QMessageBox.warning(self, "入队失败", "读取请求未能入队，请检查连接状态")

    def _on_save_params(self):
        if not self.device_manager.is_connected:
            QMessageBox.warning(self, "未连接", f"{self.pcs_name} 未连接，请先连接设备")
            return
        tab_index = self.tab_param.param_tabs.currentIndex()
        save_map = {
            0: (self._save_basic_setting_params,        "basic setting parameters"),
            1: (self._save_system_work_mode1_params,    "system work mode1 parameters"),
            2: (self._save_system_work_mode2_params,    "system work mode2 parameters"),
            3: (self._save_advanced_setting_params,      "advanced setting parameters"),
            4: (self._save_battery_setting_params,      "battery setting parameters"),
            5: (self._save_grid_setting_params,          "grid setting parameters"),
            6: (self._save_protection_setting_params,   "protection setting parameters"),
        }
        result = save_map.get(tab_index)
        if result:
            save_func, group_name = result
            save_func()

    def _on_reset_params(self):
        if not self.device_manager.is_connected:
            QMessageBox.warning(self, "未连接", f"{self.pcs_name} 未连接，请先连接设备")
            return
        reply = QMessageBox.question(
            self, "确认恢复出厂",
            f"将恢复 {self.pcs_name} 出厂设置，所有参数将被清除，是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            h = self
            payload = {
                "power_factor_regulation":        h.spin_power_factor_regulation.value(),
                "active_power_regulation":        h.spin_active_power_regulation.value(),
                "reactive_power_regulation":      h.spin_reactive_power_regulation.value(),
                "apparent_power_regulation":      h.spin_apparent_power_regulation.value(),
                "switch_on_off_enable":           h.combo_switch_on_off.currentIndex(),
                "factory_reset_enable":           1,  # 恢复出厂设置
                "self_checking_time":             h.spin_self_checking_time.value(),
                "pv_shadow_scanning_function":    h.combo_pv_shadow_scanning.currentIndex(),
                "scan_period":                    h.spin_scan_period.value(),
                "mppt_numbers":                  h.spin_mppt_numbers.value(),
                "meter_enable":                  h.combo_meter_enable.currentIndex(),
                "rcd_enable":                    h.combo_rcd_enable.currentIndex(),
                "riso_enable":                   h.combo_riso_enable.currentIndex(),
                "open_loop_instruction":          h.combo_open_loop_instruction.currentIndex(),
                "manual_removal_permanent_fault": h.combo_manual_removal_fault.currentIndex(),
            }
            if not self.device_manager.enqueue_write_parameters("basic setting parameters", payload):
                QMessageBox.warning(self, "入队失败", "恢复出厂请求未能入队")

    def _save_basic_setting_params(self):
        h = self
        payload = {
            "power_factor_regulation":        h.spin_power_factor_regulation.value(),
            "active_power_regulation":        h.spin_active_power_regulation.value(),
            "reactive_power_regulation":      h.spin_reactive_power_regulation.value(),
            "apparent_power_regulation":      h.spin_apparent_power_regulation.value(),
            "switch_on_off_enable":           h.combo_switch_on_off.currentIndex(),
            "factory_reset_enable":           h.combo_factory_reset.currentIndex(),
            "self_checking_time":             h.spin_self_checking_time.value(),
            "pv_shadow_scanning_function":    h.combo_pv_shadow_scanning.currentIndex(),
            "scan_period":                    h.spin_scan_period.value(),
            "mppt_numbers":                  h.spin_mppt_numbers.value(),
            "meter_enable":                  h.combo_meter_enable.currentIndex(),
            "rcd_enable":                    h.combo_rcd_enable.currentIndex(),
            "riso_enable":                   h.combo_riso_enable.currentIndex(),
            "open_loop_instruction":          h.combo_open_loop_instruction.currentIndex(),
            "manual_removal_permanent_fault": h.combo_manual_removal_fault.currentIndex(),
        }
        if not self.device_manager.enqueue_write_parameters("basic setting parameters", payload):
            QMessageBox.warning(self, "入队失败", "参数写入请求未能入队")

    def _save_system_work_mode1_params(self):
        h = self
        payload = {
            "battery_type":                  h.combo_battery_type.currentIndex(),
            "battery_mode":                  h.combo_battery_mode.currentIndex(),
            "system_work_mode":              h.combo_system_work_mode.currentIndex(),
            "solar_sell":                   h.combo_solar_sell.currentIndex(),
            "max_solar_power":              h.spin_max_solar_power.value(),
            "max_sell_power":               h.spin_max_sell_power.value(),
            "zero_export_power":            h.spin_zero_export_power.value(),
            "energy_pattern":               h.combo_energy_pattern.currentIndex(),
        }
        if not self.device_manager.enqueue_write_parameters("system work mode1 parameters", payload):
            QMessageBox.warning(self, "入队失败", "参数写入请求未能入队")

    def _save_system_work_mode2_params(self):
        h = self
        payload = {
            "grid_output_power_limit_enable": h.combo_grid_output_limit_enable.currentIndex(),
            "grid_output_power_limit":       h.spin_grid_output_power_limit.value(),
            "time_of_use_enable":           h.combo_time_of_use_enable.currentIndex(),
            # 功率段 1-6
            "power1": h.spin_power1.value(),
            "power2": h.spin_power2.value(),
            "power3": h.spin_power3.value(),
            "power4": h.spin_power4.value(),
            "power5": h.spin_power5.value(),
            "power6": h.spin_power6.value(),
            # 电池电压限制 1-6
            "batt_v1": h.spin_batt_v1.value(),
            "batt_v2": h.spin_batt_v2.value(),
            "batt_v3": h.spin_batt_v3.value(),
            "batt_v4": h.spin_batt_v4.value(),
            "batt_v5": h.spin_batt_v5.value(),
            "batt_v6": h.spin_batt_v6.value(),
            # 电池电量限制 1-6
            "batt_percent1": h.spin_batt_percent1.value(),
            "batt_percent2": h.spin_batt_percent2.value(),
            "batt_percent3": h.spin_batt_percent3.value(),
            "batt_percent4": h.spin_batt_percent4.value(),
            "batt_percent5": h.spin_batt_percent5.value(),
            "batt_percent6": h.spin_batt_percent6.value(),
        }
        if not self.device_manager.enqueue_write_parameters("system work mode2 parameters", payload):
            QMessageBox.warning(self, "入队失败", "参数写入请求未能入队")

    def _save_advanced_setting_params(self):
        h = self
        payload = {
            "parallel_enable":               h.combo_parallel_enable.currentIndex(),
            "parallel_serial_number":        h.spin_parallel_serial_number.value(),
            "master_slave":                  h.combo_master_slave.currentIndex(),
            "phase_select":                  h.combo_phase_select.currentIndex(),
            "three_phase_parallel_enable":    h.combo_three_phase_parallel_enable.currentIndex(),
            "a_phase_enable":               h.combo_a_phase_enable.currentIndex(),
            "b_phase_enable":               h.combo_b_phase_enable.currentIndex(),
            "c_phase_enable":               h.combo_c_phase_enable.currentIndex(),
        }
        if not self.device_manager.enqueue_write_parameters("advanced setting parameters", payload):
            QMessageBox.warning(self, "入队失败", "参数写入请求未能入队")

    def _save_grid_setting_params(self):
        h = self
        payload = {
            "grid_mode":                  h.combo_grid_mode.currentIndex(),
            "grid_type":                  h.combo_grid_type.currentIndex(),
            "over_frequency_protection":  h.spin_over_frequency_protection.value(),
            "under_frequency_protection": h.spin_under_frequency_protection.value(),
            "over_voltage_protection":    h.spin_over_voltage_protection.value(),
            "under_voltage_protection":   h.spin_under_voltage_protection.value(),
            "off_grid_voltage":           h.combo_off_grid_voltage.currentIndex(),
            "grid_frequency":             h.combo_grid_frequency.currentIndex(),
            "restore_connection_time":    h.spin_restore_connection_time.value(),
        }
        if not self.device_manager.enqueue_write_parameters("grid setting parameters", payload):
            QMessageBox.warning(self, "入队失败", "参数写入请求未能入队")

    def _save_battery_setting_params(self):
        h = self
        payload = {
            "battery_type": h.combo_battery_type.currentIndex(),
            "battery_mode": h.combo_battery_mode.currentIndex(),
            "activate_battery": h.combo_activate_battery.currentIndex(),
            "battery_capacity": h.spin_battery_capacity.value(),
            "max_a_charge": h.spin_max_a_charge.value(),
            "max_a_discharge": h.spin_max_a_discharge.value(),
            "gen_charge": h.combo_gen_charge.currentIndex(),
            "gen_signal": h.combo_gen_signal.currentIndex(),
            "generator_charging_start_capacity_point": h.spin_generator_charging_start_capacity_point.value(),
            "generator_to_battery_charging_current": h.spin_generator_to_battery_charging_current.value(),
            "max_run_time": h.spin_max_run_time.value(),
            "cooling_time": h.spin_cooling_time.value(),
            "grid_charge": h.combo_grid_charge.currentIndex(),
            "grid_signal": h.combo_grid_signal.currentIndex(),
            "utility_charging_start_capacity_point": h.spin_utility_charging_start_capacity_point.value(),
            "utility_to_battery_charging_current": h.spin_utility_to_battery_charging_current.value(),
            "generator_charging_start_voltage_point": h.spin_generator_charging_start_voltage_point.value(),
            "utility_charging_start_voltage_point": h.spin_utility_charging_start_voltage_point.value(),
            "lithium_protocol": h.combo_lithium_protocol.currentData(),
            "shutdown_percent": h.spin_shutdown_percent.value(),
            "restart_percent": h.spin_restart_percent.value(),
            "low_batt_percent": h.spin_low_batt_percent.value(),
            "shutdown_voltage": h.spin_shutdown_voltage.value(),
            "restart_voltage": h.spin_restart_voltage.value(),
            "low_batt_voltage": h.spin_low_batt_voltage.value(),
            "float_voltage": h.spin_float_voltage.value(),
            "absorption_voltage": h.spin_absorption_voltage.value(),
            "equalization_voltage": h.spin_equalization_voltage.value(),
            "equalization_days": h.spin_equalization_days.value(),
            "equalization_hours": h.spin_equalization_hours.value(),
            "tempco": h.spin_tempco.value(),
            "battery_resistance_value": h.spin_battery_resistance_value.value(),
        }
        if not self.device_manager.enqueue_write_parameters("battery setting parameters", payload):
            QMessageBox.warning(self, "入队失败", "参数写入请求未能入队")

    def _save_protection_setting_params(self):
        h = self
        payload = {
            "single_multiple_level_selection": h.combo_single_multiple_level_selection.currentIndex(),
            "uvp_recovery":  h.spin_uvp_recovery.value(),
            "ovp_recovery":  h.spin_ovp_recovery.value(),
            "ufp_recovery":  h.spin_ufp_recovery.value(),
            "ofp_recovery":  h.spin_ofp_recovery.value(),
        }
        for level in range(1, 6):
            for prot in ("uvp", "ovp", "ufp", "ofp"):
                payload[f"{prot}_l{level}_value"] = getattr(h, f"spin_{prot}_l{level}_value").value()
                payload[f"{prot}_l{level}_time"]  = getattr(h, f"spin_{prot}_l{level}_time").value()
        payload["ovp_10min_enable"]   = h.combo_ovp_10min_enable.currentIndex()
        payload["ovp_10min_value"]    = h.spin_ovp_10min_value.value()
        payload["ovp_10min_recovery"] = h.spin_ovp_10min_recovery.value()
        if not self.device_manager.enqueue_write_parameters("protection setting parameters", payload):
            QMessageBox.warning(self, "入队失败", "参数写入请求未能入队")
