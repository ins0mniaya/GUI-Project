from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
    QGridLayout, QTabWidget, QPushButton
)
from .common_widgets import create_card, create_status_item, wrap_scroll_area


class EMSMonitorTab(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._build_ui()

    def _build_ui(self):
        """构建EMS实时监控界面 - 使用嵌入式Tab导航"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 使用QTabWidget作为嵌入式导航
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        

        # 添加各个页面
        self.tabs.addTab(self._create_overview_page(), "总体监测")
        self.tabs.addTab(self._create_battery_page(), "电池参数监测")
        self.tabs.addTab(self._create_grid_page(), "电网参数监测")
        self.tabs.addTab(self._create_pv_page(), "光伏参数监测")
        self.tabs.addTab(self._create_temp_page(), "温度参数监测")
        self.tabs.addTab(self._create_alarm_page(), "告警参数")

        main_layout.addWidget(self.tabs)

    def _create_overview_page(self):
        """概览页"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(15)
        layout.setContentsMargins(6, 6, 6, 6)

        # 4个关键指标卡片
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(15)
        cards_layout.addWidget(create_card(self.host, "PV总功率(kW)", "label_pv_power", "#059669"))
        cards_layout.addWidget(create_card(self.host, "电网功率(kW)", "label_grid_power", "#0052D9"))
        cards_layout.addWidget(create_card(self.host, "负载功率(kW)", "label_load_power", "#D97706"))
        cards_layout.addWidget(create_card(self.host, "电池SOC(%)", "label_bat_soc", "#6C5CE7"))
        layout.addLayout(cards_layout)

        # 占位：功率曲线和SOC曲线（后续添加图表）
        charts_layout = QHBoxLayout()
        charts_layout.setSpacing(15)
        chart1_group = QGroupBox("功率曲线")
        chart1_group.setFixedHeight(320)
        chart1_layout = QVBoxLayout(chart1_group)
        chart1_placeholder = QLabel("[实时功率趋势图 - 待添加]")
        chart1_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chart1_placeholder.setStyleSheet("color: #909399; font-size: 12pt;")
        chart1_layout.addWidget(chart1_placeholder)
        charts_layout.addWidget(chart1_group)

        chart2_group = QGroupBox("SOC曲线")
        chart2_group.setFixedHeight(320)
        chart2_layout = QVBoxLayout(chart2_group)
        chart2_placeholder = QLabel("[SOC变化趋势图 - 待添加]")
        chart2_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chart2_placeholder.setStyleSheet("color: #909399; font-size: 12pt;")
        chart2_layout.addWidget(chart2_placeholder)
        charts_layout.addWidget(chart2_group)
        layout.addLayout(charts_layout)

        # 负载功率趋势图
        load_chart_group = QGroupBox("负载功率趋势图")
        load_chart_group.setFixedHeight(320)
        load_chart_layout = QVBoxLayout(load_chart_group)
        load_chart_placeholder = QLabel("[负载功率变化趋势图 - 待添加]")
        load_chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        load_chart_placeholder.setStyleSheet("color: #909399; font-size: 12pt;")
        load_chart_layout.addWidget(load_chart_placeholder)
        layout.addWidget(load_chart_group)

        # PV功率趋势图
        pv_chart_group = QGroupBox("PV功率趋势图")
        pv_chart_group.setFixedHeight(320)
        pv_chart_layout = QVBoxLayout(pv_chart_group)
        pv_chart_placeholder = QLabel("[PV功率变化趋势图 - 待添加]")
        pv_chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv_chart_placeholder.setStyleSheet("color: #909399; font-size: 12pt;")
        pv_chart_layout.addWidget(pv_chart_placeholder)
        layout.addWidget(pv_chart_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_battery_page(self):
        """电池页"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)

        # 电池基本信息
        basic_group = QGroupBox("电池基本信息")
        basic_layout = QHBoxLayout(basic_group)
        basic_layout.addWidget(create_card(self.host, "电池端口状态", "label_bat_status_val", "#000000"))
        basic_layout.addWidget(create_card(self.host, "电池电压(V)", "label_dc_volt_val", "#0052D9"))
        basic_layout.addWidget(create_card(self.host, "电池输出电流(A)", "label_dc_current_val", "#D97706"))
        basic_layout.addWidget(create_card(self.host, "电池输出功率(W)", "label_bat_power", "#059669"))
        basic_layout.addWidget(create_card(self.host, "电池SOC(%)", "label_bat_soc_detail", "#6C5CE7"))
        basic_layout.addWidget(create_card(self.host, "电池温度(℃)", "label_bat_temp", "#10B981"))
        layout.addWidget(basic_group)

        # 电量统计
        energy_group = QGroupBox("电量统计")
        energy_layout = QHBoxLayout(energy_group)
        energy_layout.addWidget(create_card(self.host, "电池当日充电(kWh)", "label_bat_chg_day", "#27AE60"))
        energy_layout.addWidget(create_card(self.host, "电池当日放电(kWh)", "label_bat_dis_day", "#EF4444"))
        energy_layout.addWidget(create_card(self.host, "电池累计充电(kWh)", "label_bat_chg_total", "#27AE60"))
        energy_layout.addWidget(create_card(self.host, "电池累计放电(kWh)", "label_bat_dis_total", "#EF4444"))
        layout.addWidget(energy_group)

        # BMS信息
        bms_group = QGroupBox("第一组BMS信息")
        bms_layout = QGridLayout(bms_group)
        bms_layout.setHorizontalSpacing(8)
        bms_layout.setVerticalSpacing(8)
        bms_items = [
            ("平均电压(V)", "label_v_cell_mean", "#2C3E50"),
            ("总电流(A)", "label_i_cell_total", "#2C3E50"),
            ("SOC(%)", "label_soc_bms", "#6C5CE7"),
            ("剩余电量(Ah)", "label_dump_energy", "#6C5CE7"),
            ("SOH(%)", "label_soh_bms", "#6C5CE7"),
            ("平均温度(℃)", "label_temp_cell_avg", "#2C3E50"),
            ("充电电压限值(V)", "label_charging_voltage", "#0052D9"),
            ("放电电压限值(V)", "label_discharge_voltage", "#0052D9"),
            ("充电限流(A)", "label_charging_current_limiting", "#D97706"),
            ("放电限流(A)", "label_discharge_current_limiting", "#D97706"),
            ("告警位(位)", "label_lithium_battery_alarm_position", "#EF4444"),
            ("故障位(位)", "label_lithium_battery_fault_location", "#EF4444"),
            ("标志2(位)", "label_lithium_battery_symbol_2", "#27AE60"),
            ("模块数量(个)", "label_module_numbers", "#27AE60"),
        ]
        for idx, (name, obj_name, color) in enumerate(bms_items):
            row, col = divmod(idx, 2)
            bms_layout.addWidget(create_status_item(self.host, name, obj_name, color), row, col)
        layout.addWidget(bms_group)

        # 详细BMS信息（极值信息和探针温度在同一行，单体电压单独一行，报警信息单独一行）
        detailed_group = QGroupBox("详细BMS信息 (CAN)")
        detailed_v_layout = QVBoxLayout(detailed_group)
        detailed_v_layout.setSpacing(10)

        # ── CAN 连接控制行 ───────────────────────────────────────────
        can_ctrl_row = QHBoxLayout()
        self.host.label_can_status = QLabel("CAN: 未连接")
        self.host.label_can_status.setObjectName("label_can_status")
        self.host.btn_can_connect = QPushButton("连接 CAN BMS")
        self.host.btn_can_connect.setObjectName("btn_can_connect")
        self.host.btn_can_connect.setFixedWidth(120)
        can_ctrl_row.addWidget(self.host.label_can_status)
        can_ctrl_row.addStretch()
        can_ctrl_row.addWidget(self.host.btn_can_connect)
        detailed_v_layout.addLayout(can_ctrl_row)

        # 第一行：极值信息 + 探针温度（同一行）
        first_row = QHBoxLayout()
        extremes_group = QGroupBox("极值信息")
        extremes_layout = QGridLayout(extremes_group)
        extremes_layout.setHorizontalSpacing(6)
        extremes_layout.setVerticalSpacing(6)
        # 四个项横向排列：最高 | 最高序号 | 最低 | 最低序号
        extremes_layout.addWidget(create_status_item(self.host, "最高单体(mV)", "label_highest_single_mv", "#EF4444"), 0, 0)
        extremes_layout.addWidget(create_status_item(self.host, "序号", "label_highest_single_idx", "#2C3E50"), 0, 1)
        extremes_layout.addWidget(create_status_item(self.host, "最低单体(mV)", "label_lowest_single_mv", "#0052D9"), 0, 2)
        extremes_layout.addWidget(create_status_item(self.host, "序号", "label_lowest_single_idx", "#2C3E50"), 0, 3)
        extremes_group.setMinimumWidth(420)
        # 不使用固定宽度，改为由布局伸缩比例控制，使两侧各占一半宽度
        # extremes_group.setFixedWidth(220)

        temps_group = QGroupBox("温度(℃)")
        temps_layout = QHBoxLayout(temps_group)
        temps_layout.setSpacing(8)
        for i in range(4):
            obj_name = f"label_detail_temp_probe{i+1}"
            temps_layout.addWidget(create_status_item(self.host, f"探针{i+1}", obj_name, "#10B981"))
        temps_group.setMinimumHeight(100)

        # 使用伸缩比：extremes 和 temps 各占 1
        first_row.addWidget(extremes_group, 1)
        first_row.addWidget(temps_group, 1)
        first_row.setStretch(0, 1)
        first_row.setStretch(1, 1)
        detailed_v_layout.addLayout(first_row)

        # 第二行：1-16单体电压（整行展示）
        cells_group = QGroupBox("1-16号单体电压(mV)")
        cells_layout = QGridLayout(cells_group)
        cells_layout.setHorizontalSpacing(12)
        cells_layout.setVerticalSpacing(12)
        for i in range(16):
            row, col = divmod(i, 4)
            obj_name = f"label_cell_{i+1}"
            item = create_status_item(self.host, f"单体{i+1}", obj_name, "#2C3E50")
            cells_layout.addWidget(item, row, col)
        detailed_v_layout.addWidget(cells_group)

        # 第三行：报警信息（整行）
        alarm_group = QGroupBox("报警信息")
        alarm_layout = QVBoxLayout(alarm_group)
        alarm_layout.setSpacing(6)
        for i in range(3):
            lbl = QLabel("")
            lbl.setObjectName(f"label_alarm_msg_{i+1}")
            lbl.setStyleSheet("font: 10pt 'Microsoft YaHei UI'; color: #27AE60;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            alarm_layout.addWidget(lbl)
            setattr(self.host, f"label_alarm_msg_{i+1}", lbl)
        detailed_v_layout.addWidget(alarm_group)

        layout.addWidget(detailed_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_grid_page(self):
        """电网页"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)

        # 电网状态
        status_group = QGroupBox("电网状态")
        status_layout = QHBoxLayout(status_group)
        status_layout.setSpacing(15)
        status_layout.addWidget(create_status_item(self.host, "并网状态", "label_grid_status", "#059669"), stretch=1)
        status_layout.addWidget(create_status_item(self.host, "电网功率(W)", "label_grid_power_detail", "#0052D9"), stretch=1)
        status_layout.addWidget(create_status_item(self.host, "电网频率(Hz)", "label_freq_grid", "#2C3E50"), stretch=1)
        layout.addWidget(status_group)

        # 三相电压/电流/功率/频率
        phase_group = QGroupBox("三相电压/电流/功率/频率")
        phase_layout = QGridLayout(phase_group)
        phase_headers = ["相别", "电压(V)", "电流(A)", "功率(W)", "频率(Hz)"]
        for col, header in enumerate(phase_headers):
            lbl = QLabel(header)
            lbl.setStyleSheet("font-weight: bold; color: #606266;")
            phase_layout.addWidget(lbl, 0, col)
        
        phases = ["A相", "B相", "C相"]
        for row, phase in enumerate(phases, 1):
            phase_layout.addWidget(QLabel(phase), row, 0)
            for col, suffix in enumerate(["v", "i", "p", "freq"], 1):
                obj_name = f"label_{suffix}_grid_{phase[0].lower()}"
                lbl = QLabel("--")
                lbl.setObjectName(obj_name)
                lbl.setStyleSheet("font: 11pt 'Microsoft YaHei UI'; color: #2C3E50;")
                phase_layout.addWidget(lbl, row, col)
                setattr(self.host, obj_name, lbl)
        layout.addWidget(phase_group)

        # 防逆流监测
        limiter_group = QGroupBox("防逆流监测")
        limiter_layout = QGridLayout(limiter_group)
        limiter_items = [
            ("L1 Limiter电流(A)", "label_i_limiter_l1", "#2C3E50"),
            ("L2 Limiter电流(A)", "label_i_limiter_l2", "#2C3E50"),
            ("L1 Limiter功率(W)", "label_p_limiter_l1", "#0052D9"),
            ("L2 Limiter功率(W)", "label_p_limiter_l2", "#0052D9"),
            ("外置总功率(W)", "label_p_limiter_total", "#059669"),
        ]
        for idx, (name, obj_name, color) in enumerate(limiter_items):
            row, col = idx // 3, idx % 3
            limiter_layout.addWidget(create_status_item(self.host, name, obj_name, color), row, col)
        layout.addWidget(limiter_group)

        # 电量统计
        energy_group = QGroupBox("电量统计")
        energy_layout = QGridLayout(energy_group)
        energy_items = [
            ("当日购电(kWh)", "label_e_grid_buy_day", "#EF4444"),
            ("当月购电(kWh)", "label_e_grid_buy_month", "#EF4444"),
            ("当年购电(kWh)", "label_e_grid_buy_year", "#EF4444"),
            ("累计购电(kWh)", "label_e_grid_buy_total", "#EF4444"),
            ("当日卖电(kWh)", "label_e_grid_sell_day", "#27AE60"),
            ("当月卖电(kWh)", "label_e_grid_sell_month", "#27AE60"),
            ("当年卖电(kWh)", "label_e_grid_sell_year", "#27AE60"),
            ("累计卖电(kWh)", "label_e_grid_sell_total", "#27AE60"),
        ]
        for idx, (name, obj_name, color) in enumerate(energy_items):
            row, col = idx // 4, idx % 4
            energy_layout.addWidget(create_status_item(self.host, name, obj_name, color), row, col)
        layout.addWidget(energy_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_pv_page(self):
        """光伏页"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)

        # PV总览
        overview_group = QGroupBox("PV总览")
        overview_layout = QHBoxLayout(overview_group)
        overview_layout.addWidget(create_status_item(self.host, "PV总功率(kW)", "label_pv_power_detail", "#059669"))
        overview_layout.addWidget(create_status_item(self.host, "当日发电(kWh)", "label_e_pv_day", "#27AE60"))
        overview_layout.addWidget(create_status_item(self.host, "累计发电(MWh)", "label_e_pv_total", "#27AE60"))
        overview_layout.addWidget(create_status_item(self.host, "总有功发电量", "label_e_pv_total_kwh", "#6C5CE7"))
        overview_layout.addWidget(create_status_item(self.host, "MPPT数量", "label_mppt_nums", "#2C3E50"))
        layout.addWidget(overview_group)

        # 各PV通道状态（表格形式）
        pv_group = QGroupBox("各PV通道状态")
        pv_layout = QGridLayout(pv_group)
        
        # 表头
        headers = ["通道", "电压(V)", "电流(A)", "功率(W)", "状态"]
        for col, header in enumerate(headers):
            lbl = QLabel(header)
            lbl.setStyleSheet("font-weight: bold; color: #606266;")
            pv_layout.addWidget(lbl, 0, col)
        
        # 8路PV
        for i in range(8):
            row = i + 1
            pv_layout.addWidget(QLabel(f"PV{i+1}"), row, 0)
            for col, suffix in enumerate(["v", "i", "p"], 1):
                obj_name = f"label_{suffix}_pv{i+1}"
                lbl = QLabel("--")
                lbl.setObjectName(obj_name)
                lbl.setStyleSheet("font: 10pt 'Microsoft YaHei UI'; color: #2C3E50;")
                pv_layout.addWidget(lbl, row, col)
                setattr(self.host, obj_name, lbl)
            # 状态
            status_lbl = QLabel("未连接")
            status_lbl.setObjectName(f"label_pv{i+1}_status")
            status_lbl.setStyleSheet("font: 10pt 'Microsoft YaHei UI'; color: #909399;")
            pv_layout.addWidget(status_lbl, row, 4)
            setattr(self.host, f"label_pv{i+1}_status", status_lbl)
        
        layout.addWidget(pv_group)
        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_temp_page(self):
        """温度页"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)

        # PCS温度监测
        pcs_temp_group = QGroupBox("PCS温度监测")
        pcs_temp_layout = QGridLayout(pcs_temp_group)
        pcs_temps = [
            ("变压器温度", "label_temp_trans_val", "#10B981"),
            ("BOOST电感温度", "label_temp_boost_val", "#10B981"),
            ("INV电感温度", "label_temp_inv_val", "#10B981"),
            ("机箱内部温度", "label_temp_internal_val", "#10B981"),
            ("散热器点1", "label_temp_rad1_val", "#10B981"),
            ("散热器点2", "label_temp_rad2_val", "#10B981"),
            ("散热器点3", "label_temp_rad3_val", "#10B981"),
            ("散热器点4", "label_temp_rad4_val", "#10B981"),
        ]
        for idx, (name, obj_name, color) in enumerate(pcs_temps):
            row, col = idx // 4, idx % 4
            pcs_temp_layout.addWidget(create_status_item(self.host, name, obj_name, color), row, col)
        layout.addWidget(pcs_temp_group)

        # 设备温度
        device_temp_group = QGroupBox("设备温度")
        device_temp_layout = QHBoxLayout(device_temp_group)
        device_temp_layout.addWidget(create_card(self.host, "逆变器温度(℃)", "label_temp_inv_device", "#D97706"))
        device_temp_layout.addWidget(create_card(self.host, "散热器温度(℃)", "label_temp_heatsink", "#D97706"))
        device_temp_layout.addWidget(create_card(self.host, "环境温度(℃)", "label_temp_amb", "#0052D9"))
        layout.addWidget(device_temp_group)

        # # BMS温度监测
        # bms_temp_group = QGroupBox("BMS温度监测 (T1-T8)")
        # bms_temp_layout = QGridLayout(bms_temp_group)
        # for i in range(8):
        #     obj_name = f"label_temp_t{i+1}"
        #     item = create_status_item(self.host, f"T{i+1}", obj_name, "#10B981")
        #     bms_temp_layout.addWidget(item, i // 4, i % 4)
        # layout.addWidget(bms_temp_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_alarm_page(self):
        """告警页"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)

        # 当前告警状态
        status_group = QGroupBox("当前告警状态")
        status_layout = QHBoxLayout(status_group)
        status_layout.addWidget(create_status_item(self.host, "系统状态", "label_system_status", "#059669"))
        status_layout.addWidget(create_status_item(self.host, "运行状态", "label_run_state", "#2C3E50"))
        status_layout.addStretch()
        layout.addWidget(status_group)

        # 告警信息
        warning_group = QGroupBox("告警信息")
        warning_layout = QHBoxLayout(warning_group)
        warning_layout.addWidget(create_status_item(self.host, "告警字1", "label_warning_message_1", "#D97706"))
        warning_layout.addWidget(create_status_item(self.host, "告警字2", "label_warning_message_2", "#D97706"))
        layout.addWidget(warning_group)

        # 故障码显示
        fault_group = QGroupBox("故障码")
        fault_layout = QVBoxLayout(fault_group)
        
        # 4个故障字
        fault_codes_layout = QHBoxLayout()
        for i in range(4):
            obj_name = f"label_fault_code_{i+1}"
            item = create_status_item(self.host, f"故障字{i+1}", obj_name, "#EF4444")
            fault_codes_layout.addWidget(item)
        fault_layout.addLayout(fault_codes_layout)
        
        # 故障码文本显示
        fault_text = QLabel("未知")
        fault_text.setObjectName("label_fault_code")
        fault_text.setStyleSheet("font: 10pt 'Microsoft YaHei UI'; color: #EF4444; font-weight: bold;")
        fault_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fault_layout.addWidget(fault_text)
        setattr(self.host, "label_fault_code", fault_text)
        
        layout.addWidget(fault_group)

        # 设备信息
        device_group = QGroupBox("设备信息")
        device_layout = QGridLayout(device_group)
        device_items = [
            ("设备类型", "label_device_type", "#2C3E50"),
            ("Modbus地址", "label_modbus_addr", "#2C3E50"),
            ("协议版本", "label_version", "#2C3E50"),
            ("交流输出类型", "label_AC_output_type", "#2C3E50"),
            ("相数", "label_phases", "#2C3E50"),
            ("MPPT数量", "label_mppt_nums_info", "#2C3E50"),
        ]
        for idx, (name, obj_name, color) in enumerate(device_items):
            row, col = idx // 3, idx % 3
            device_layout.addWidget(create_status_item(self.host, name, obj_name, color), row, col)
        layout.addWidget(device_group)

        # 软件版本信息
        version_group = QGroupBox("软件版本信息")
        version_layout = QGridLayout(version_group)
        version_items = [
            ("显示板ARM版本", "label_comm_board_version", "#6C5CE7"),
            ("控制板主DSP版本", "label_control_board_version", "#6C5CE7"),
            ("控制板辅DSP版本", "label_slave_control_board_version", "#6C5CE7"),
        ]
        for idx, (name, obj_name, color) in enumerate(version_items):
            row, col = idx // 3, idx % 3
            version_layout.addWidget(create_status_item(self.host, name, obj_name, color), row, col)
        layout.addWidget(version_group)

        # 告警历史记录
        history_group = QGroupBox("告警历史记录 (最近20条)")
        history_layout = QVBoxLayout(history_group)
        history_placeholder = QLabel("[告警历史列表 - 待添加]")
        history_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        history_placeholder.setStyleSheet("color: #909399; font-size: 12pt;")
        history_layout.addWidget(history_placeholder)
        layout.addWidget(history_group)

        layout.addStretch()
        return wrap_scroll_area(inner)
