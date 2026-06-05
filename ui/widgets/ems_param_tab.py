from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
    QGridLayout, QDoubleSpinBox, QComboBox, QTabWidget
)

from ui.widgets.common_widgets import add_combo, add_spin, wrap_scroll_area

class EMSParamTab(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._build_ui()

    def _build_ui(self):
        """构建EMS参数设置界面 - 使用Tab分类展示"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.param_tabs = QTabWidget()
        self.param_tabs.addTab(self._create_basic_setting_page(), "系统基础设置")
        self.param_tabs.addTab(self._create_system_work_mode1_page(), "系统工作模式(基础)")
        self.param_tabs.addTab(self._create_system_work_mode2_page(), "系统工作模式(调度)")
        self.param_tabs.addTab(self._create_advanced_setting_page(), "系统高级设置")
        self.param_tabs.addTab(self._create_battery_params_page(), "电池设置")
        self.param_tabs.addTab(self._create_grid_params_page(), "电网设置")
        self.param_tabs.addTab(self._create_protection_setting_page(), "保护设置")

        main_layout.addWidget(self.param_tabs)

    # 底部操作按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.host.btn_read_params = QPushButton("读取当前参数")
        self.host.btn_read_params.setMinimumHeight(35)
        self.host.btn_read_params.setMinimumWidth(120)
        self.host.btn_read_params.clicked.connect(self.host._on_read_current_params)
        btn_layout.addWidget(self.host.btn_read_params)
        
        btn_layout.addSpacing(10)
        
        self.host.btn_save_params = QPushButton("保存配置")
        self.host.btn_save_params.setMinimumHeight(35)
        self.host.btn_save_params.setMinimumWidth(120)
        self.host.btn_save_params.setStyleSheet("""
            QPushButton { background-color: #059669; color: white; }
            QPushButton { border: none; padding: 0 16px; }
            QPushButton:hover { background-color: #047857; }
        """)
        self.host.btn_save_params.clicked.connect(self.host._on_save_params)
        btn_layout.addWidget(self.host.btn_save_params)
        
        btn_layout.addSpacing(10)
        
        self.host.btn_reset_params = QPushButton("恢复出厂")
        self.host.btn_reset_params.setMinimumHeight(35)
        self.host.btn_reset_params.setMinimumWidth(120)
        self.host.btn_reset_params.clicked.connect(self.host._on_reset_params)
        btn_layout.addWidget(self.host.btn_reset_params)
        
        main_layout.addLayout(btn_layout)

    def _create_basic_setting_page(self):
        """基础设置参数页 (16001)"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── 1. 功率调节 ──────────────────────────────────────────────
        power_group = QGroupBox("功率调节")
        power_layout = QGridLayout(power_group)
        power_layout.setSpacing(5)
        power_layout.setColumnStretch(0, 1)
        power_layout.setColumnStretch(1, 1)
        power_layout.setColumnStretch(2, 1)
        power_layout.setColumnStretch(3, 1)

        add_spin(self.host, power_layout, 0, 0, "功率因数调节",  "spin_power_factor_regulation",     0, 2000, 3, "")
        add_spin(self.host, power_layout, 0, 1, "有功功率调节",  "spin_active_power_regulation",     0, 1200, 1, " %")
        add_spin(self.host, power_layout, 1, 0, "无功功率调节",  "spin_reactive_power_regulation",   0, 1200, 1, " %")
        add_spin(self.host, power_layout, 1, 1, "视在功率调节",  "spin_apparent_power_regulation",  0, 1200, 1, " %")
        layout.addWidget(power_group)

        # ── 2. 开关机 & 系统控制 ──────────────────────────────────────
        ctrl_group = QGroupBox("开关机 & 系统控制")
        ctrl_layout = QGridLayout(ctrl_group)
        ctrl_layout.setSpacing(5)
        ctrl_layout.setColumnStretch(0, 1)
        ctrl_layout.setColumnStretch(1, 1)
        ctrl_layout.setColumnStretch(2, 1)
        ctrl_layout.setColumnStretch(3, 1)

        add_combo(self.host, ctrl_layout, 0, 0, "开关机使能",      "combo_switch_on_off",          ["ON(开机)", "OFF(关机)"])
        add_combo(self.host, ctrl_layout, 0, 1, "恢复出厂设置",    "combo_factory_reset",          ["正常", "恢复"])
        add_combo(self.host, ctrl_layout, 1, 0, "PV阴影扫描功能",  "combo_pv_shadow_scanning",     ["关闭", "开启"])
        add_combo(self.host, ctrl_layout, 1, 1, "开环指令",        "combo_open_loop_instruction",  ["关闭", "开启"])
        add_combo(self.host, ctrl_layout, 2, 0, "手动清除永久故障", "combo_manual_removal_fault",   ["正常", "清除"])
        add_spin(self.host, ctrl_layout,  3, 0, "自检时间",         "spin_self_checking_time",      0, 600, 0, " s")
        layout.addWidget(ctrl_group)

        # ── 3. PV / MPPT 设置 ─────────────────────────────────────────
        pv_group = QGroupBox("PV / MPPT 设置")
        pv_layout = QGridLayout(pv_group)
        pv_layout.setSpacing(5)
        pv_layout.setColumnStretch(0, 1)
        pv_layout.setColumnStretch(1, 1)
        pv_layout.setColumnStretch(2, 1)
        pv_layout.setColumnStretch(3, 1)

        add_spin(self.host, pv_layout, 0, 0, "扫描周期",  "spin_scan_period",  0, 255, 0, " h")
        add_spin(self.host, pv_layout, 0, 1, "MPPT数量",  "spin_mppt_numbers", 0, 20,  0, "")
        layout.addWidget(pv_group)

        # ── 4. 安全功能 ──────────────────────────────────────────────
        safety_group = QGroupBox("安全功能")
        safety_layout = QGridLayout(safety_group)
        safety_layout.setSpacing(5)
        safety_layout.setColumnStretch(0, 1)
        safety_layout.setColumnStretch(1, 1)
        safety_layout.setColumnStretch(2, 1)
        safety_layout.setColumnStretch(3, 1)

        add_combo(self.host, safety_layout, 0, 0, "电表使能",  "combo_meter_enable", ["关闭", "开启"])
        add_combo(self.host, safety_layout, 0, 1, "RCD使能",  "combo_rcd_enable",   ["关闭", "开启"])
        add_combo(self.host, safety_layout, 1, 0, "RISO使能", "combo_riso_enable",  ["关闭", "开启"])
        layout.addWidget(safety_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_system_work_mode1_page(self):
        """系统工作模式参数1页 (16067) — 电池/工作模式/光伏卖电"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── 1. 电池 & 工作模式 ────────────────────────────────────────
        batt_mode_group = QGroupBox("电池 & 工作模式")
        batt_mode_layout = QGridLayout(batt_mode_group)
        batt_mode_layout.setSpacing(5)
        batt_mode_layout.setColumnStretch(0, 1)
        batt_mode_layout.setColumnStretch(1, 1)
        batt_mode_layout.setColumnStretch(2, 1)
        batt_mode_layout.setColumnStretch(3, 1)

        add_combo(self.host, batt_mode_layout, 0, 0, "电池类型",     "combo_battery_type",     ["Lead Battery", "Lithium", "Other"])
        add_combo(self.host, batt_mode_layout, 0, 1, "电池模式",     "combo_battery_mode",     ["Use Batt V", "Use Batt %", "No Batt"])
        add_combo(self.host, batt_mode_layout, 1, 0, "系统工作模式", "combo_system_work_mode", ["模式1", "模式2", "模式3", "模式4"])
        add_combo(self.host, batt_mode_layout, 1, 1, "能量模式",     "combo_energy_pattern",   ["模式1", "模式2", "模式3"])
        layout.addWidget(batt_mode_group)

        # ── 2. 光伏 & 卖电设置 ────────────────────────────────────────
        pv_sell_group = QGroupBox("光伏 & 卖电设置")
        pv_sell_layout = QGridLayout(pv_sell_group)
        pv_sell_layout.setSpacing(5)
        pv_sell_layout.setColumnStretch(0, 1)
        pv_sell_layout.setColumnStretch(1, 1)
        pv_sell_layout.setColumnStretch(2, 1)
        pv_sell_layout.setColumnStretch(3, 1)

        add_combo(self.host, pv_sell_layout, 0, 0, "光伏卖电使能",       "combo_solar_sell",            ["关闭", "开启"])
        add_spin(self.host, pv_sell_layout,  0, 1, "最大光伏功率",        "spin_max_solar_power",        0, 99999, 0, " W")
        add_spin(self.host, pv_sell_layout,  1, 0, "最大卖电功率",       "spin_max_sell_power",         0, 99999, 0, " W")
        add_spin(self.host, pv_sell_layout,  1, 1, "防逆流功率",          "spin_zero_export_power",     0, 99999, 0, " W")
        layout.addWidget(pv_sell_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_system_work_mode2_page(self):
        """系统工作模式参数2页 (16075) — 电网功率限制/分时电价/功率段/电压电量限制"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        # # ── 1. 电网输出功率限制 ──────────────────────────────────────
        # grid_limit_group = QGroupBox("电网输出功率限制")
        # grid_limit_layout = QGridLayout(grid_limit_group)
        # grid_limit_layout.setSpacing(5)
        # grid_limit_layout.setColumnStretch(0, 1)
        # grid_limit_layout.setColumnStretch(1, 1)
        # grid_limit_layout.setColumnStretch(2, 1)
        # grid_limit_layout.setColumnStretch(3, 1)

        # add_combo(self.host, grid_limit_layout, 0, 0, "功率限制使能", "combo_grid_output_limit_enable", ["关闭", "开启"])
        # add_spin(self.host, grid_limit_layout,  0, 1, "功率限制值",   "spin_grid_output_power_limit",  0, 99999, 0, " W")
        # layout.addWidget(grid_limit_group)

        # ── 2. 分时电价设置 ──────────────────────────────────────────
        tou_group = QGroupBox("分时电价设置")
        tou_layout = QGridLayout(tou_group)
        tou_layout.setSpacing(5)
        tou_layout.setColumnStretch(0, 1)
        tou_layout.setColumnStretch(1, 1)
        tou_layout.setColumnStretch(2, 1)
        tou_layout.setColumnStretch(3, 1)

        add_combo(self.host, tou_layout, 0, 0, "分时电价使能", "combo_time_of_use_enable", ["关闭", "开启"])
        layout.addWidget(tou_group)

        # ── 3. 功率段设置 (1-3) ────────────────────────────────────
        power_seg1_group = QGroupBox("功率段设置 (1-3)")
        power_seg1_layout = QGridLayout(power_seg1_group)
        power_seg1_layout.setSpacing(5)
        power_seg1_layout.setColumnStretch(0, 1)
        power_seg1_layout.setColumnStretch(1, 2)
        power_seg1_layout.setColumnStretch(2, 1)
        power_seg1_layout.setColumnStretch(3, 2)
        power_seg1_layout.setColumnStretch(4, 1)
        power_seg1_layout.setColumnStretch(5, 2)

        add_spin(self.host, power_seg1_layout, 0, 0, "功率段1", "spin_power1", 0, 15000, 0, " W")
        add_spin(self.host, power_seg1_layout, 0, 1, "功率段2", "spin_power2", 0, 15000, 0, " W")
        add_spin(self.host, power_seg1_layout, 0, 2, "功率段3", "spin_power3", 0, 15000, 0, " W")
        layout.addWidget(power_seg1_group)

        # ── 4. 功率段设置 (4-6) ────────────────────────────────────
        power_seg2_group = QGroupBox("功率段设置 (4-6)")
        power_seg2_layout = QGridLayout(power_seg2_group)
        power_seg2_layout.setSpacing(5)
        power_seg2_layout.setColumnStretch(0, 1)
        power_seg2_layout.setColumnStretch(1, 2)
        power_seg2_layout.setColumnStretch(2, 1)
        power_seg2_layout.setColumnStretch(3, 2)
        power_seg2_layout.setColumnStretch(4, 1)
        power_seg2_layout.setColumnStretch(5, 2)

        add_spin(self.host, power_seg2_layout, 0, 0, "功率段4", "spin_power4", 0, 15000, 0, " W")
        add_spin(self.host, power_seg2_layout, 0, 1, "功率段5", "spin_power5", 0, 15000, 0, " W")
        add_spin(self.host, power_seg2_layout, 0, 2, "功率段6", "spin_power6", 0, 15000, 0, " W")
        layout.addWidget(power_seg2_group)

        # ── 5. 电池电压限制 ─────────────────────────────────────────
        batt_v_group = QGroupBox("电池电压限制")
        batt_v_layout = QGridLayout(batt_v_group)
        batt_v_layout.setSpacing(5)
        batt_v_layout.setColumnStretch(0, 1)
        batt_v_layout.setColumnStretch(1, 2)
        batt_v_layout.setColumnStretch(2, 1)
        batt_v_layout.setColumnStretch(3, 2)
        batt_v_layout.setColumnStretch(4, 1)
        batt_v_layout.setColumnStretch(5, 2)

        add_spin(self.host, batt_v_layout, 0, 0, "电压限制1", "spin_batt_v1", 180, 800, 2, " V")
        add_spin(self.host, batt_v_layout, 0, 1, "电压限制2", "spin_batt_v2", 180, 800, 2, " V")
        add_spin(self.host, batt_v_layout, 0, 2, "电压限制3", "spin_batt_v3", 180, 800, 2, " V")
        add_spin(self.host, batt_v_layout, 1, 0, "电压限制4", "spin_batt_v4", 180, 800, 2, " V")
        add_spin(self.host, batt_v_layout, 1, 1, "电压限制5", "spin_batt_v5", 180, 800, 2, " V")
        add_spin(self.host, batt_v_layout, 1, 2, "电压限制6", "spin_batt_v6", 180, 800, 2, " V")
        layout.addWidget(batt_v_group)

        # ── 6. 电池电量限制 ─────────────────────────────────────────
        batt_pct_group = QGroupBox("电池电量限制")
        batt_pct_layout = QGridLayout(batt_pct_group)
        batt_pct_layout.setSpacing(5)
        batt_pct_layout.setColumnStretch(0, 1)
        batt_pct_layout.setColumnStretch(1, 2)
        batt_pct_layout.setColumnStretch(2, 1)
        batt_pct_layout.setColumnStretch(3, 2)
        batt_pct_layout.setColumnStretch(4, 1)
        batt_pct_layout.setColumnStretch(5, 2)

        add_spin(self.host, batt_pct_layout, 0, 0, "电量限制1", "spin_batt_percent1", 0, 100, 0, " %")
        add_spin(self.host, batt_pct_layout, 0, 1, "电量限制2", "spin_batt_percent2", 0, 100, 0, " %")
        add_spin(self.host, batt_pct_layout, 0, 2, "电量限制3", "spin_batt_percent3", 0, 100, 0, " %")
        add_spin(self.host, batt_pct_layout, 1, 0, "电量限制4", "spin_batt_percent4", 0, 100, 0, " %")
        add_spin(self.host, batt_pct_layout, 1, 1, "电量限制5", "spin_batt_percent5", 0, 100, 0, " %")
        add_spin(self.host, batt_pct_layout, 1, 2, "电量限制6", "spin_batt_percent6", 0, 100, 0, " %")
        layout.addWidget(batt_pct_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_advanced_setting_page(self):
        """高级设置参数页 (16164)"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── 并机设置 ─────────────────────────────────────────────────
        parallel_group = QGroupBox("并机设置")
        parallel_layout = QGridLayout(parallel_group)
        parallel_layout.setSpacing(5)
        parallel_layout.setColumnStretch(0, 1)
        parallel_layout.setColumnStretch(1, 1)
        parallel_layout.setColumnStretch(2, 1)
        parallel_layout.setColumnStretch(3, 1)

        add_combo(self.host, parallel_layout, 0, 0, "并机使能",       "combo_parallel_enable",         ["Disable", "Enable"])
        add_spin(self.host, parallel_layout,  0, 1, "并机序列号",     "spin_parallel_serial_number",   0, 63, 0, "")
        add_combo(self.host, parallel_layout, 1, 0, "主从设置",       "combo_master_slave",           ["Slave", "Master"])
        add_combo(self.host, parallel_layout, 1, 1, "相位选择",       "combo_phase_select",            ["A Phase", "B Phase", "C Phase", "无效"])
        layout.addWidget(parallel_group)

        # ── 三相并机设置 ─────────────────────────────────────────────
        three_phase_group = QGroupBox("三相并机设置")
        three_phase_layout = QGridLayout(three_phase_group)
        three_phase_layout.setSpacing(5)
        three_phase_layout.setColumnStretch(0, 1)
        three_phase_layout.setColumnStretch(1, 2)
        three_phase_layout.setColumnStretch(2, 1)
        three_phase_layout.setColumnStretch(3, 2)
        three_phase_layout.setColumnStretch(4, 1)
        three_phase_layout.setColumnStretch(5, 2)

        add_combo(self.host, three_phase_layout, 0, 0, "三相并机使能", "combo_three_phase_parallel_enable", ["Disable", "Enable"])
        add_combo(self.host, three_phase_layout, 1, 0, "A相位使能",   "combo_a_phase_enable",  ["Disable", "Enable"])
        add_combo(self.host, three_phase_layout, 1, 1, "B相位使能",   "combo_b_phase_enable",  ["Disable", "Enable"])
        add_combo(self.host, three_phase_layout, 1, 2, "C相位使能",   "combo_c_phase_enable",  ["Disable", "Enable"])
        layout.addWidget(three_phase_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_grid_params_page(self):
        """电网设置参数页 (26001)"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        mode_group = QGroupBox("并网基础")
        mode_layout = QGridLayout(mode_group)
        mode_layout.setSpacing(5)
        mode_layout.setColumnStretch(0, 1)
        mode_layout.setColumnStretch(1, 1)
        mode_layout.setColumnStretch(2, 1)
        mode_layout.setColumnStretch(3, 1)

        add_combo(
            self.host,
            mode_layout,
            0,
            0,
            "并网标准",
            "combo_grid_mode",
            [
                "General standard",
                "UL1741 & IE1547",
                "CPUC RULE21",
                "SRD-UL1741",
                "CEI 0-21",
                "Australia A",
                "Australia B",
                "Australia C",
                "EN50549_CZ-PPDS(>16A)",
                "NewZealand",
                "VDE4105",
                "OVE-Directive R25",
            ],
        )
        add_combo(
            self.host,
            mode_layout,
            0,
            1,
            "电网类型",
            "combo_grid_type",
            ["单相240/230/220V", "两相120/240V", "三相系统380/400V"],
        )
        add_combo(self.host, mode_layout, 1, 0, "电网频率", "combo_grid_frequency", ["50Hz", "60Hz"])
        add_spin(self.host, mode_layout, 1, 1, "恢复并网时间", "spin_restore_connection_time", 10, 300, 0, " s")
        layout.addWidget(mode_group)

        protect_group = QGroupBox("离网与保护")
        protect_layout = QGridLayout(protect_group)
        protect_layout.setSpacing(5)
        protect_layout.setColumnStretch(0, 1)
        protect_layout.setColumnStretch(1, 1)
        protect_layout.setColumnStretch(2, 1)
        protect_layout.setColumnStretch(3, 1)

        add_combo(self.host, protect_layout, 0, 0, "离网模式电压设置", "combo_off_grid_voltage", ["LN：220VAC LL：380VAC", "LN：230VAC LL：400VAC", "LN：240VAC LL：420VAC", "LN：120VAC LL：208VAC", "LN：133VAC LL：230VAC"])
        add_spin(self.host, protect_layout, 0, 1, "过频保护点", "spin_over_frequency_protection", 45, 65, 2, " Hz")
        add_spin(self.host, protect_layout, 1, 0, "欠频保护点", "spin_under_frequency_protection", 45, 65, 2, " Hz")
        add_spin(self.host, protect_layout, 1, 1, "过压保护点", "spin_over_voltage_protection", 176, 276, 1, " V")
        add_spin(self.host, protect_layout, 2, 0, "欠压保护点", "spin_under_voltage_protection", 176, 276, 1, " V")
        layout.addWidget(protect_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_battery_params_page(self):
        """电池设置参数页 (36001)"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        battery_group = QGroupBox("电池设置参数")
        battery_layout = QGridLayout(battery_group)
        battery_layout.setSpacing(5)
        battery_layout.setColumnStretch(0, 1)
        battery_layout.setColumnStretch(1, 1)
        battery_layout.setColumnStretch(2, 1)
        battery_layout.setColumnStretch(3, 1)

        add_combo(self.host, battery_layout, 0, 0, "电池类型", "combo_battery_type", ["Lead Battery", "Lithium battery", "Other"])
        add_combo(self.host, battery_layout, 0, 1, "电池模式", "combo_battery_mode", ["Use Batt V", "Use Batt %", "No Batt"])
        add_combo(self.host, battery_layout, 1, 0, "电池唤醒", "combo_activate_battery", ["关闭", "开启"])
        add_spin(self.host, battery_layout, 1, 1, "电池容量", "spin_battery_capacity", 0, 2000, suffix=" Ah")

        add_spin(self.host, battery_layout, 2, 0, "最大充电电流", "spin_max_a_charge", 0, 1000, 2, " A")
        add_spin(self.host, battery_layout, 2, 1, "最大放电电流", "spin_max_a_discharge", 0, 1000, 2, " A")
        add_combo(self.host, battery_layout, 3, 0, "发电机充电", "combo_gen_charge", ["关闭", "开启"])
        add_combo(self.host, battery_layout, 3, 1, "发电机信号", "combo_gen_signal", ["关闭", "开启"])

        add_spin(self.host, battery_layout, 4, 0, "发电机起始容量点", "spin_generator_charging_start_capacity_point", 0, 100, 2, " %")
        add_spin(self.host, battery_layout, 4, 1, "发电机到电池充电电流", "spin_generator_to_battery_charging_current", 0, 1000, 2, " A")
        add_spin(self.host, battery_layout, 5, 0, "最大运行时间", "spin_max_run_time", 0, 240, 1, " h")
        add_spin(self.host, battery_layout, 5, 1, "冷却时间", "spin_cooling_time", 0, 240, 1, " h")

        add_combo(self.host, battery_layout, 6, 0, "市电充电", "combo_grid_charge", ["关闭", "开启"])
        add_combo(self.host, battery_layout, 6, 1, "市电信号", "combo_grid_signal", ["关闭", "开启"])
        add_spin(self.host, battery_layout, 7, 0, "市电起始容量点", "spin_utility_charging_start_capacity_point", 0, 100, 2, " %")
        add_spin(self.host, battery_layout, 7, 1, "市电到电池充电电流", "spin_utility_to_battery_charging_current", 0, 1000, 2, " A")

        add_spin(self.host, battery_layout, 8, 0, "发电机起始电压点", "spin_generator_charging_start_voltage_point", 0, 1000, 2, " V")
        add_spin(self.host, battery_layout, 8, 1, "市电起始电压点", "spin_utility_charging_start_voltage_point", 0, 1000, 2, " V")
        add_combo(self.host, battery_layout, 9, 0, "锂电协议", "combo_lithium_protocol", ["26", "34"], [26, 34])

        add_spin(self.host, battery_layout, 10, 0, "关机百分比", "spin_shutdown_percent", 0, 100, 2, " %")
        add_spin(self.host, battery_layout, 10, 1, "重启百分比", "spin_restart_percent", 0, 100, 2, " %")
        add_spin(self.host, battery_layout, 11, 0, "低电百分比", "spin_low_batt_percent", 0, 100, 2, " %")
        add_spin(self.host, battery_layout, 11, 1, "关机电压", "spin_shutdown_voltage", 0, 1000, 2, " V")
        add_spin(self.host, battery_layout, 12, 0, "重启电压", "spin_restart_voltage", 0, 1000, 2, " V")
        add_spin(self.host, battery_layout, 12, 1, "低电电压", "spin_low_batt_voltage", 0, 1000, 2, " V")
        add_spin(self.host, battery_layout, 13, 0, "浮充电压", "spin_float_voltage", 0, 1000, 2, " V")
        add_spin(self.host, battery_layout, 13, 1, "吸收电压", "spin_absorption_voltage", 0, 1000, 2, " V")
        add_spin(self.host, battery_layout, 14, 0, "均衡电压", "spin_equalization_voltage", 0, 1000, 2, " V")
        add_spin(self.host, battery_layout, 14, 1, "均衡天数", "spin_equalization_days", 0, 90, 0, " d")
        add_spin(self.host, battery_layout, 15, 0, "均衡小时", "spin_equalization_hours", 0, 12, 0, " h")
        add_spin(self.host, battery_layout, 15, 1, "温度补偿", "spin_tempco", 0, 50, 0, " mV/℃")
        add_spin(self.host, battery_layout, 16, 0, "电池内阻值", "spin_battery_resistance_value", 0, 6000, 0, " mΩ")

        layout.addWidget(battery_group)

        layout.addStretch()
        return wrap_scroll_area(inner)

    def _create_protection_setting_page(self):
        """保护参数设置页 (46001) - 单/多级电压频率保护 + 10分钟过压保护"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── 1. 单/多级选择 ──────────────────────────────────────────────
        level_group = QGroupBox("保护级数选择")
        level_layout = QGridLayout(level_group)
        level_layout.setSpacing(5)
        level_layout.setColumnStretch(0, 1)
        level_layout.setColumnStretch(1, 1)
        level_layout.setColumnStretch(2, 1)
        level_layout.setColumnStretch(3, 1)

        add_combo(self.host, level_layout, 0, 0, "单级/多级选择",
                  "combo_single_multiple_level_selection",
                  ["单级", "二级", "三级", "四级", "五级"])
        layout.addWidget(level_group)

        # ── 2. 保护恢复点 ─────────────────────────────────────────────
        recovery_group = QGroupBox("保护恢复点")
        recovery_layout = QGridLayout(recovery_group)
        recovery_layout.setSpacing(5)
        recovery_layout.setColumnStretch(0, 1)
        recovery_layout.setColumnStretch(1, 1)
        recovery_layout.setColumnStretch(2, 1)
        recovery_layout.setColumnStretch(3, 1)

        add_spin(self.host, recovery_layout, 0, 0, "欠压保护恢复点",  "spin_uvp_recovery",  0, 300, 1, " V")
        add_spin(self.host, recovery_layout, 0, 1, "过压保护恢复点",  "spin_ovp_recovery",  0, 300, 1, " V")
        add_spin(self.host, recovery_layout, 1, 0, "欠频保护恢复点",  "spin_ufp_recovery",  40, 70, 2, " Hz")
        add_spin(self.host, recovery_layout, 1, 1, "过频保护恢复点",  "spin_ofp_recovery",  40, 70, 2, " Hz")
        layout.addWidget(recovery_group)

        # ── 3. 一级保护 ────────────────────────────────────────────────
        l1_group = QGroupBox("一级保护（单级时有效）")
        l1_layout = QGridLayout(l1_group)
        l1_layout.setSpacing(5)
        l1_layout.setColumnStretch(0, 1)
        l1_layout.setColumnStretch(1, 1)
        l1_layout.setColumnStretch(2, 1)
        l1_layout.setColumnStretch(3, 1)

        add_spin(self.host, l1_layout, 0, 0, "欠压一级保护值",   "spin_uvp_l1_value",  0, 300,  1, " V")
        add_spin(self.host, l1_layout, 0, 1, "过压一级保护值",   "spin_ovp_l1_value",  0, 300,  1, " V")
        add_spin(self.host, l1_layout, 1, 0, "欠频一级保护值",   "spin_ufp_l1_value",  40, 70,  2, " Hz")
        add_spin(self.host, l1_layout, 1, 1, "过频一级保护值",   "spin_ofp_l1_value",  40, 70,  2, " Hz")
        add_spin(self.host, l1_layout, 2, 0, "欠压一级保护时间", "spin_uvp_l1_time",   0, 3600, 2, " s")
        add_spin(self.host, l1_layout, 2, 1, "过压一级保护时间", "spin_ovp_l1_time",   0, 3600, 2, " s")
        add_spin(self.host, l1_layout, 3, 0, "欠频一级保护时间", "spin_ufp_l1_time",   0, 3600, 2, " s")
        add_spin(self.host, l1_layout, 3, 1, "过频一级保护时间", "spin_ofp_l1_time",   0, 3600, 2, " s")
        layout.addWidget(l1_group)

        # ── 4. 二级保护 ────────────────────────────────────────────────
        l2_group = QGroupBox("二级保护")
        l2_layout = QGridLayout(l2_group)
        l2_layout.setSpacing(5)
        l2_layout.setColumnStretch(0, 1)
        l2_layout.setColumnStretch(1, 1)
        l2_layout.setColumnStretch(2, 1)
        l2_layout.setColumnStretch(3, 1)

        add_spin(self.host, l2_layout, 0, 0, "欠压二级保护值",   "spin_uvp_l2_value",  0, 300,  1, " V")
        add_spin(self.host, l2_layout, 0, 1, "过压二级保护值",   "spin_ovp_l2_value",  0, 300,  1, " V")
        add_spin(self.host, l2_layout, 1, 0, "欠频二级保护值",   "spin_ufp_l2_value",  40, 70,  2, " Hz")
        add_spin(self.host, l2_layout, 1, 1, "过频二级保护值",   "spin_ofp_l2_value",  40, 70,  2, " Hz")
        add_spin(self.host, l2_layout, 2, 0, "欠压二级保护时间", "spin_uvp_l2_time",   0, 3600, 2, " s")
        add_spin(self.host, l2_layout, 2, 1, "过压二级保护时间", "spin_ovp_l2_time",   0, 3600, 2, " s")
        add_spin(self.host, l2_layout, 3, 0, "欠频二级保护时间", "spin_ufp_l2_time",   0, 3600, 2, " s")
        add_spin(self.host, l2_layout, 3, 1, "过频二级保护时间", "spin_ofp_l2_time",   0, 3600, 2, " s")
        layout.addWidget(l2_group)

        # ── 5. 三级保护 ────────────────────────────────────────────────
        l3_group = QGroupBox("三级保护")
        l3_layout = QGridLayout(l3_group)
        l3_layout.setSpacing(5)
        l3_layout.setColumnStretch(0, 1)
        l3_layout.setColumnStretch(1, 1)
        l3_layout.setColumnStretch(2, 1)
        l3_layout.setColumnStretch(3, 1)

        add_spin(self.host, l3_layout, 0, 0, "欠压三级保护值",   "spin_uvp_l3_value",  0, 300,  1, " V")
        add_spin(self.host, l3_layout, 0, 1, "过压三级保护值",   "spin_ovp_l3_value",  0, 300,  1, " V")
        add_spin(self.host, l3_layout, 1, 0, "欠频三级保护值",   "spin_ufp_l3_value",  40, 70,  2, " Hz")
        add_spin(self.host, l3_layout, 1, 1, "过频三级保护值",   "spin_ofp_l3_value",  40, 70,  2, " Hz")
        add_spin(self.host, l3_layout, 2, 0, "欠压三级保护时间", "spin_uvp_l3_time",   0, 3600, 2, " s")
        add_spin(self.host, l3_layout, 2, 1, "过压三级保护时间", "spin_ovp_l3_time",   0, 3600, 2, " s")
        add_spin(self.host, l3_layout, 3, 0, "欠频三级保护时间", "spin_ufp_l3_time",   0, 3600, 2, " s")
        add_spin(self.host, l3_layout, 3, 1, "过频三级保护时间", "spin_ofp_l3_time",   0, 3600, 2, " s")
        layout.addWidget(l3_group)

        # ── 6. 四级保护 ────────────────────────────────────────────────
        l4_group = QGroupBox("四级保护")
        l4_layout = QGridLayout(l4_group)
        l4_layout.setSpacing(5)
        l4_layout.setColumnStretch(0, 1)
        l4_layout.setColumnStretch(1, 1)
        l4_layout.setColumnStretch(2, 1)
        l4_layout.setColumnStretch(3, 1)

        add_spin(self.host, l4_layout, 0, 0, "欠压四级保护值",   "spin_uvp_l4_value",  0, 300,  1, " V")
        add_spin(self.host, l4_layout, 0, 1, "过压四级保护值",   "spin_ovp_l4_value",  0, 300,  1, " V")
        add_spin(self.host, l4_layout, 1, 0, "欠频四级保护值",   "spin_ufp_l4_value",  40, 70,  2, " Hz")
        add_spin(self.host, l4_layout, 1, 1, "过频四级保护值",   "spin_ofp_l4_value",  40, 70,  2, " Hz")
        add_spin(self.host, l4_layout, 2, 0, "欠压四级保护时间", "spin_uvp_l4_time",   0, 3600, 2, " s")
        add_spin(self.host, l4_layout, 2, 1, "过压四级保护时间", "spin_ovp_l4_time",   0, 3600, 2, " s")
        add_spin(self.host, l4_layout, 3, 0, "欠频四级保护时间", "spin_ufp_l4_time",   0, 3600, 2, " s")
        add_spin(self.host, l4_layout, 3, 1, "过频四级保护时间", "spin_ofp_l4_time",   0, 3600, 2, " s")
        layout.addWidget(l4_group)

        # ── 7. 五级保护 ────────────────────────────────────────────────
        l5_group = QGroupBox("五级保护")
        l5_layout = QGridLayout(l5_group)
        l5_layout.setSpacing(5)
        l5_layout.setColumnStretch(0, 1)
        l5_layout.setColumnStretch(1, 1)
        l5_layout.setColumnStretch(2, 1)
        l5_layout.setColumnStretch(3, 1)

        add_spin(self.host, l5_layout, 0, 0, "欠压五级保护值",   "spin_uvp_l5_value",  0, 300,  1, " V")
        add_spin(self.host, l5_layout, 0, 1, "过压五级保护值",   "spin_ovp_l5_value",  0, 300,  1, " V")
        add_spin(self.host, l5_layout, 1, 0, "欠频五级保护值",   "spin_ufp_l5_value",  40, 70,  2, " Hz")
        add_spin(self.host, l5_layout, 1, 1, "过频五级保护值",   "spin_ofp_l5_value",  40, 70,  2, " Hz")
        add_spin(self.host, l5_layout, 2, 0, "欠压五级保护时间", "spin_uvp_l5_time",   0, 3600, 2, " s")
        add_spin(self.host, l5_layout, 2, 1, "过压五级保护时间", "spin_ovp_l5_time",   0, 3600, 2, " s")
        add_spin(self.host, l5_layout, 3, 0, "欠频五级保护时间", "spin_ufp_l5_time",   0, 3600, 2, " s")
        add_spin(self.host, l5_layout, 3, 1, "过频五级保护时间", "spin_ofp_l5_time",   0, 3600, 2, " s")
        layout.addWidget(l5_group)

        # ── 8. 10分钟过压保护 ─────────────────────────────────────────
        ovp10_group = QGroupBox("10 分钟过压保护")
        ovp10_layout = QGridLayout(ovp10_group)
        ovp10_layout.setSpacing(5)
        ovp10_layout.setColumnStretch(0, 1)
        ovp10_layout.setColumnStretch(1, 1)
        ovp10_layout.setColumnStretch(2, 1)
        ovp10_layout.setColumnStretch(3, 1)

        add_combo(self.host, ovp10_layout, 0, 0, "10min过压保护使能",
                  "combo_ovp_10min_enable", ["Disable", "Enable"])
        add_spin(self.host, ovp10_layout, 0, 1, "10min过压保护值",
                 "spin_ovp_10min_value",    0, 300, 1, " V")
        add_spin(self.host, ovp10_layout, 1, 0, "10min过压保护恢复值",
                 "spin_ovp_10min_recovery", 0, 300, 1, " V")
        layout.addWidget(ovp10_group)

        layout.addStretch()
        return wrap_scroll_area(inner)
