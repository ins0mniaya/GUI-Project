from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget


class EMSParamTab(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._build_ui()

    def _build_ui(self):
        layout_param = QVBoxLayout(self)
        h_layout_split_param = QHBoxLayout()

        self.host.group_param_config = QGroupBox("参数配置")
        layout_param_config = QVBoxLayout(self.host.group_param_config)

        h_param_row1 = QHBoxLayout()
        v_param_device_type = QVBoxLayout()
        lbl_param_device_type = QLabel("设备类型")
        lbl_param_device_type.setStyleSheet("color: #333333; font-size: 9pt;")
        self.host.spin_device_type = QSpinBox()
        self.host.spin_device_type.setRange(0, 65535)
        self.host.spin_device_type.setObjectName("spin_device_type")
        v_param_device_type.addWidget(lbl_param_device_type)
        v_param_device_type.addWidget(self.host.spin_device_type)
        h_param_row1.addLayout(v_param_device_type)

        v_param_voltage = QVBoxLayout()
        lbl_param_voltage = QLabel("额定电压(V)")
        lbl_param_voltage.setStyleSheet("color: #333333; font-size: 9pt;")
        self.host.spin_rated_voltage = QSpinBox()
        self.host.spin_rated_voltage.setRange(0, 65535)
        self.host.spin_rated_voltage.setObjectName("spin_rated_voltage")
        v_param_voltage.addWidget(lbl_param_voltage)
        v_param_voltage.addWidget(self.host.spin_rated_voltage)
        h_param_row1.addLayout(v_param_voltage)
        layout_param_config.addLayout(h_param_row1)

        h_param_row2 = QHBoxLayout()
        v_param_pf = QVBoxLayout()
        lbl_param_pf = QLabel("功率因数")
        lbl_param_pf.setStyleSheet("color: #333333; font-size: 9pt;")
        self.host.spin_power_factor = QSpinBox()
        self.host.spin_power_factor.setRange(0, 10000)
        self.host.spin_power_factor.setObjectName("spin_power_factor")
        v_param_pf.addWidget(lbl_param_pf)
        v_param_pf.addWidget(self.host.spin_power_factor)
        h_param_row2.addLayout(v_param_pf)

        v_param_rated_power = QVBoxLayout()
        lbl_param_rated_power = QLabel("额定功率(W)")
        lbl_param_rated_power.setStyleSheet("color: #333333; font-size: 9pt;")
        self.host.spin_rated_power = QSpinBox()
        self.host.spin_rated_power.setRange(0, 999999)
        self.host.spin_rated_power.setObjectName("spin_rated_power")
        v_param_rated_power.addWidget(lbl_param_rated_power)
        v_param_rated_power.addWidget(self.host.spin_rated_power)
        h_param_row2.addLayout(v_param_rated_power)
        layout_param_config.addLayout(h_param_row2)

        h_param_row3 = QHBoxLayout()
        v_param_ah = QVBoxLayout()
        lbl_param_ah = QLabel("调整AH")
        lbl_param_ah.setStyleSheet("color: #333333; font-size: 9pt;")
        self.host.spin_adjust_ah = QSpinBox()
        self.host.spin_adjust_ah.setRange(0, 65535)
        self.host.spin_adjust_ah.setObjectName("spin_adjust_ah")
        v_param_ah.addWidget(lbl_param_ah)
        v_param_ah.addWidget(self.host.spin_adjust_ah)
        h_param_row3.addLayout(v_param_ah)
        h_param_row3.addStretch()
        layout_param_config.addLayout(h_param_row3)

        h_param_btns = QHBoxLayout()
        self.host.btn_read_params = QPushButton("读取当前")
        self.host.btn_read_params.setMinimumHeight(35)
        self.host.btn_read_params.clicked.connect(self.host._on_read_current_params)
        h_param_btns.addWidget(self.host.btn_read_params)

        self.host.btn_save_params = QPushButton("保存配置")
        self.host.btn_save_params.setMinimumHeight(35)
        self.host.btn_save_params.clicked.connect(self.host._on_save_params)
        self.host.btn_save_params.setStyleSheet(self.host.btn_save_params.styleSheet() + "\nQPushButton { background-color: #059669; }\nQPushButton:hover { background-color: #047857; }")
        h_param_btns.addWidget(self.host.btn_save_params)

        self.host.btn_reset_params = QPushButton("重置")
        self.host.btn_reset_params.setMinimumHeight(35)
        self.host.btn_reset_params.clicked.connect(self.host._on_reset_params)
        h_param_btns.addWidget(self.host.btn_reset_params)

        layout_param_config.addLayout(h_param_btns)
        layout_param_config.addStretch()

        h_layout_split_param.addWidget(self.host.group_param_config, 3)

        v_layout_right_param = QVBoxLayout()
        v_layout_right_param.addWidget(QGroupBox("配置说明"), 1)
        v_layout_right_param.addWidget(QGroupBox("操作日志"), 1)
        h_layout_split_param.addLayout(v_layout_right_param, 2)
        layout_param.addLayout(h_layout_split_param)
