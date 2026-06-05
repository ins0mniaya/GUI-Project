# pcs_batch_tab.py
# -*- coding: utf-8 -*-
"""
批量操作 Tab（PCSBatchTab）

功能：
  1. 批量连接 / 断开：勾选多台 PCS 后一键操作
  2. 批量下发基础参数：统一设置最大充/放电功率、开关机，逐台写入并显示进度
  3. 数据存储配置：勾选数据类型、采样间隔、保留天数，启停 CSV 记录
  4. 实时显示各台 PCS 的连接状态

设计约束：
  - 只做"批量触发"，实际写入由各台 DeviceManager 的写队列串行执行
  - 没有独立数据监测，只关注操作反馈
"""

import logging
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget
)

logger = logging.getLogger(__name__)

# 存储引擎延迟导入（避免循环依赖）
def _get_storage_engine():
    from data_storage import DataStorageEngine
    return DataStorageEngine


class PCSBatchTab(QWidget):
    """批量操作页面"""

    def __init__(self, device_managers: list, pcs_configs: list, parent=None):
        """
        device_managers : list[DeviceManager]，顺序与 pcs_configs 一致
        pcs_configs     : list of {"id": int, "name": str, ...}
        """
        super().__init__(parent)
        self._dms    = device_managers          # DeviceManager 列表
        self._cfgs   = pcs_configs              # PCS 配置列表
        self._n      = len(device_managers)

        self._checkboxes: list[QCheckBox]  = []
        self._status_labels: list[QLabel]  = []
        self._progress_bars: list[QProgressBar] = []

        # ── 存储引擎（延迟创建，由外部注入或首次使用时自动初始化）──
        self._storage_engine = None

        self._build_ui()
        self._connect_signals()

    # ================================================================
    # UI 构建
    # ================================================================

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(16)

        # ── 标题 ──
        title = QLabel("批量操作")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #2F3E4C;")
        outer.addWidget(title)

        # ── 设备选择区 ──
        grp_select = QGroupBox("选择操作目标")
        grp_select_layout = QVBoxLayout(grp_select)

        # 全选/取消全选
        top_row = QHBoxLayout()
        self.btn_select_all   = QPushButton("全选")
        self.btn_deselect_all = QPushButton("取消全选")
        self.btn_select_all.setFixedWidth(80)
        self.btn_deselect_all.setFixedWidth(90)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_deselect_all.clicked.connect(self._deselect_all)
        top_row.addWidget(self.btn_select_all)
        top_row.addWidget(self.btn_deselect_all)
        top_row.addStretch()
        grp_select_layout.addLayout(top_row)

        # 逐台勾选行
        for i, cfg in enumerate(self._cfgs):
            row = QHBoxLayout()
            cb = QCheckBox(cfg.get("name", f"PCS-{cfg.get('id',i+1)}"))
            cb.setChecked(True)
            cb.setFixedWidth(90)
            self._checkboxes.append(cb)
            row.addWidget(cb)

            lbl_status = QLabel("未连接")
            lbl_status.setFixedWidth(70)
            lbl_status.setStyleSheet("color: #D92D20; font-weight: bold;")
            self._status_labels.append(lbl_status)
            row.addWidget(lbl_status)

            pb = QProgressBar()
            pb.setRange(0, 100)
            pb.setValue(0)
            pb.setFixedHeight(14)
            pb.setTextVisible(False)
            pb.setVisible(False)
            self._progress_bars.append(pb)
            row.addWidget(pb)
            row.addStretch()
            grp_select_layout.addLayout(row)

        outer.addWidget(grp_select)

        # ── 批量连接/断开 ──
        grp_conn = QGroupBox("连接控制")
        conn_layout = QHBoxLayout(grp_conn)
        self.btn_batch_connect    = QPushButton("批量连接")
        self.btn_batch_disconnect = QPushButton("批量断开")
        self.btn_batch_connect.setMinimumWidth(110)
        self.btn_batch_disconnect.setMinimumWidth(110)
        self.btn_batch_connect.clicked.connect(self._batch_connect)
        self.btn_batch_disconnect.clicked.connect(self._batch_disconnect)
        conn_layout.addWidget(self.btn_batch_connect)
        conn_layout.addWidget(self.btn_batch_disconnect)
        conn_layout.addStretch()
        outer.addWidget(grp_conn)

        # ── 批量参数下发 ──
        grp_param = QGroupBox("批量下发基础参数")
        param_form = QFormLayout(grp_param)
        param_form.setLabelAlignment(Qt.AlignRight)

        self.spin_batch_max_charge   = QSpinBox()
        self.spin_batch_max_charge.setRange(0, 100000)
        self.spin_batch_max_charge.setSuffix("  W")
        self.spin_batch_max_charge.setValue(5000)
        param_form.addRow("最大充电功率:", self.spin_batch_max_charge)

        self.spin_batch_max_discharge = QSpinBox()
        self.spin_batch_max_discharge.setRange(0, 100000)
        self.spin_batch_max_discharge.setSuffix("  W")
        self.spin_batch_max_discharge.setValue(5000)
        param_form.addRow("最大放电功率:", self.spin_batch_max_discharge)

        self.spin_batch_active_power = QDoubleSpinBox()
        self.spin_batch_active_power.setRange(0, 120.0)
        self.spin_batch_active_power.setDecimals(1)
        self.spin_batch_active_power.setSuffix("  %")
        self.spin_batch_active_power.setValue(100.0)
        param_form.addRow("有功功率调节:", self.spin_batch_active_power)

        btn_row = QHBoxLayout()
        self.btn_batch_send = QPushButton("批量下发参数")
        self.btn_batch_send.setMinimumWidth(130)
        self.btn_batch_send.setStyleSheet("""
            QPushButton { background-color: #059669; color: white; }
            QPushButton:hover { background-color: #047857; }
        """)
        self.btn_batch_send.clicked.connect(self._batch_send_params)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_batch_send)
        param_form.addRow("", btn_row)
        outer.addWidget(grp_param)

        # ── 数据存储配置 ──
        self._build_storage_ui(outer)

        # ── 操作日志 ──
        grp_log = QGroupBox("操作日志")
        log_layout = QVBoxLayout(grp_log)
        self.lbl_log = QLabel("就绪")
        self.lbl_log.setStyleSheet("color: #606266; font-size: 9pt;")
        self.lbl_log.setWordWrap(True)
        log_layout.addWidget(self.lbl_log)
        outer.addWidget(grp_log)

        outer.addStretch()

    def _build_storage_ui(self, parent_layout: QVBoxLayout):
        """构建数据存储配置区域。"""
        grp = QGroupBox("数据存储配置")
        layout = QVBoxLayout(grp)
        layout.setSpacing(10)

        # ── 第一行：总开关 + 启停按钮 ──
        row1 = QHBoxLayout()

        self.cb_storage_enable = QCheckBox("启用数据记录")
        self.cb_storage_enable.setChecked(False)
        self.cb_storage_enable.stateChanged.connect(self._on_storage_toggle_changed)
        row1.addWidget(self.cb_storage_enable)

        row1.addStretch()

        self.btn_storage_start = QPushButton("开始记录")
        self.btn_storage_start.setFixedHeight(28)
        self.btn_storage_start.setMinimumWidth(100)
        self.btn_storage_start.setStyleSheet("""
            QPushButton { background-color: #059669; color: white; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #047857; }
            QPushButton:disabled { background-color: #9CA3AF; color: #6B7280; }
        """)
        self.btn_storage_start.setEnabled(False)  # 需要先勾选数据类型
        self.btn_storage_start.clicked.connect(self._on_storage_start_clicked)

        self.btn_storage_stop = QPushButton("停止记录")
        self.btn_storage_stop.setFixedHeight(28)
        self.btn_storage_stop.setMinimumWidth(100)
        self.btn_storage_stop.setVisible(False)
        self.btn_storage_stop.setStyleSheet("""
            QPushButton { background-color: #DC2626; color: white; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #B91C1C; }
        """)
        self.btn_storage_stop.clicked.connect(self._on_storage_stop_clicked)

        row1.addWidget(self.btn_storage_start)
        row1.addWidget(self.btn_storage_stop)
        layout.addLayout(row1)

        # ── 第二行：数据类型选择（横向排列）──
        row2_type = QHBoxLayout()
        row2_type.addWidget(QLabel("记录内容:"))

        self._storage_type_checks: dict[str, QCheckBox] = {}
        type_items = [
            ("battery",      "电池参数"),
            ("bms_group1",   "BMS组参数"),
            ("cell_voltage", "单体电压"),
            ("pv",           "光伏参数"),
            ("grid",         "电网参数"),
            ("load",         "负载参数"),
        ]
        for key, label in type_items:
            cb = QCheckBox(label)
            cb.setChecked(key in ("battery", "bms_group1", "cell_voltage"))
            cb.stateChanged.connect(self._on_storage_types_changed)
            self._storage_type_checks[key] = cb
            row2_type.addWidget(cb)

        row2_type.addStretch()
        layout.addLayout(row2_type)

        # ── 第三行：采样间隔 + 保留天数 ──
        row3_params = QHBoxLayout()
        row3_params.addWidget(QLabel("采样间隔:"))

        self.spin_sample_interval = QSpinBox()
        self.spin_sample_interval.setRange(1, 300)
        self.spin_sample_interval.setValue(10)
        self.spin_sample_interval.setSuffix(" 次轮询/次")
        self.spin_sample_interval.setToolTip("每 N 次轮询存一条记录\n10次≈3秒，30次≈9秒")
        row3_params.addWidget(self.spin_sample_interval)

        row3_params.addWidget(QLabel("  保留天数:"))
        self.spin_retention_days = QSpinBox()
        self.spin_retention_days.setRange(1, 365)
        self.spin_retention_days.setValue(15)
        self.spin_retention_days.setSuffix(" 天")
        self.spin_retention_days.setToolTip("超过此天数的 CSV 文件将自动删除")
        row3_params.addWidget(self.spin_retention_days)

        row3_params.addStretch()

        # 立即清理按钮
        self.btn_cleanup_now = QPushButton("立即清理旧文件")
        self.btn_cleanup_now.setFixedHeight(24)
        self.btn_cleanup_now.setStyleSheet("""
            QPushButton { background-color: #6366F1; color: white; border-radius: 3px; padding: 2px 12px; font-size: 9pt; }
            QPushButton:hover { background-color: #4F46E5; }
        """)
        self.btn_cleanup_now.clicked.connect(self._on_cleanup_clicked)
        row3_params.addWidget(self.btn_cleanup_now)

        layout.addLayout(row3_params)

        # ── 状态栏 ──
        self.lbl_storage_status = QLabel("状态：未启动 | 文件数：- | 总大小：- MB")
        self.lbl_storage_status.setStyleSheet(
            "color: #606266; font-size: 9pt; padding: 4px 8px;"
            "background-color: #F3F4F6; border-radius: 3px;"
        )
        layout.addWidget(self.lbl_storage_status)

        parent_layout.addWidget(grp)

    # ================================================================
    # 存储相关方法
    # ================================================================

    @property
    def storage_engine(self):
        """获取或创建存储引擎实例。"""
        if self._storage_engine is None:
            EngineClass = _get_storage_engine()
            self._storage_engine = EngineClass()
        return self._storage_engine

    def _on_storage_toggle_changed(self, state):
        """启用/禁用复选框变化时更新 UI 状态。"""
        has_types = any(cb.isChecked() for cb in self._storage_type_checks.values())
        self.btn_storage_start.setEnabled(state == Qt.Checked.value and has_types)

    def _on_storage_types_changed(self):
        """数据类型勾选变化时更新启停按钮可用性。"""
        has_types = any(cb.isChecked() for cb in self._storage_type_checks.values())
        enable = self.cb_storage_enable.isChecked() and has_types
        self.btn_storage_start.setEnabled(enable)

    def _get_selected_types(self) -> list[str]:
        return [k for k, cb in self._storage_type_checks.items() if cb.isChecked()]

    def _on_storage_start_clicked(self):
        """开始记录按钮点击。"""
        types = self._get_selected_types()
        if not types:
            QMessageBox.warning(self, "未选择", "请至少勾选一种要记录的数据类型")
            return

        engine = self.storage_engine
        engine.set_config(
            enabled=True,
            sample_interval=self.spin_sample_interval.value(),
            retention_days=self.spin_retention_days.value(),
        )
        engine.set_data_types(types)

        self.btn_storage_start.setVisible(False)
        self.btn_storage_stop.setVisible(True)
        self.cb_storage_enable.setEnabled(False)
        self._update_storage_status()
        logger.info("[BatchTab] 数据存储已启动: types=%s", types)
        self._log(f"数据存储已启动 — 记录类型: {', '.join(types)}")

    def _on_storage_stop_clicked(self):
        """停止记录按钮点击。"""
        if self._storage_engine:
            self._storage_engine.set_config(enabled=False)

        self.btn_storage_start.setVisible(True)
        self.btn_storage_stop.setVisible(False)
        self.cb_storage_enable.setEnabled(True)
        self._update_storage_status()
        self._log("数据存储已停止")

    def _on_cleanup_clicked(self):
        """立即清理过期文件。"""
        if self._storage_engine:
            before = len(self._storage_engine._get_all_csv_files())
            self._storage_engine.cleanup_now()
            after = len(self._storage_engine._get_all_csv_files())
            removed = before - after
            QMessageBox.information(
                self, "清理完成",
                f"已删除 {removed} 个过期文件\n剩余 {after} 个文件"
            )
            self._update_storage_status()

    def _update_storage_status(self):
        """刷新存储状态标签。"""
        if self._storage_engine:
            info = self._storage_engine.get_status()
            status_text = (
                f"{'运行中' if info['enabled'] else '已停止'} "
                f"| 文件数: {info['total_files']} "
                f"| 总大小: {info['total_size_mb']} MB"
            )
            color = "#059669" if info["enabled"] else "#606266"
            self.lbl_storage_status.setText(f"状态：{status_text}")
            self.lbl_storage_status.setStyleSheet(
                f"color: {color}; font-size: 9pt; padding: 4px 8px;"
                "background-color: #F3F4F6; border-radius: 3px;"
            )

    # ================================================================
    # 信号连接
    # ================================================================

    def _connect_signals(self):
        for i, dm in enumerate(self._dms):
            # 用默认参数捕获当前 i，避免闭包陷阱
            dm.status_changed.connect(
                lambda pcs_id, connected, desc, idx=i: self._on_status_changed(idx, connected, desc)
            )

    # ================================================================
    # 槽函数
    # ================================================================

    def _on_status_changed(self, idx: int, is_connected: bool, desc: str):
        lbl = self._status_labels[idx]
        if is_connected:
            lbl.setText("已连接")
            lbl.setStyleSheet("color: #059669; font-weight: bold;")
        else:
            lbl.setText("未连接")
            lbl.setStyleSheet("color: #D92D20; font-weight: bold;")

    # ── 选择控制 ──

    def _select_all(self):
        for cb in self._checkboxes:
            cb.setChecked(True)

    def _deselect_all(self):
        for cb in self._checkboxes:
            cb.setChecked(False)

    def _selected_indices(self) -> list[int]:
        return [i for i, cb in enumerate(self._checkboxes) if cb.isChecked()]

    # ── 批量连接/断开 ──

    def _batch_connect(self):
        indices = self._selected_indices()
        if not indices:
            QMessageBox.warning(self, "未选择", "请先勾选至少一台 PCS")
            return
        names = [self._cfgs[i].get("name", f"PCS-{i+1}") for i in indices]
        self._log(f"批量连接: {', '.join(names)}")
        for i in indices:
            dm = self._dms[i]
            if not dm.is_connected:
                try:
                    dm.connect_device()
                except Exception as e:
                    logger.error("批量连接 %s 异常: %s", self._cfgs[i].get("name"), e)

    def _batch_disconnect(self):
        indices = self._selected_indices()
        if not indices:
            QMessageBox.warning(self, "未选择", "请先勾选至少一台 PCS")
            return
        names = [self._cfgs[i].get("name", f"PCS-{i+1}") for i in indices]
        self._log(f"批量断开: {', '.join(names)}")
        for i in indices:
            dm = self._dms[i]
            if dm.is_connected:
                try:
                    dm.disconnect_device()
                except Exception as e:
                    logger.error("批量断开 %s 异常: %s", self._cfgs[i].get("name"), e)

    # ── 批量下发参数 ──

    def _batch_send_params(self):
        indices = self._selected_indices()
        if not indices:
            QMessageBox.warning(self, "未选择", "请先勾选至少一台 PCS")
            return

        # 检查全部已连接
        not_connected = [
            self._cfgs[i].get("name", f"PCS-{i+1}")
            for i in indices if not self._dms[i].is_connected
        ]
        if not_connected:
            QMessageBox.warning(
                self, "未连接",
                f"以下设备未连接，无法下发参数：\n{', '.join(not_connected)}"
            )
            return

        payload = {
            "max_charge_power":        self.spin_batch_max_charge.value(),
            "max_discharge_power":     self.spin_batch_max_discharge.value(),
            "active_power_regulation": self.spin_batch_active_power.value(),
            # 其余字段保留默认值（写入时驱动层会填充协议默认）
        }

        success_names = []
        fail_names    = []
        for i in indices:
            dm   = self._dms[i]
            name = self._cfgs[i].get("name", f"PCS-{i+1}")
            pb   = self._progress_bars[i]
            pb.setVisible(True)
            pb.setValue(50)
            try:
                ok = dm.enqueue_write_parameters("basic setting parameters", payload)
                if ok:
                    success_names.append(name)
                    pb.setValue(100)
                else:
                    fail_names.append(name)
                    pb.setValue(0)
            except Exception as e:
                fail_names.append(f"{name}(异常:{e})")
                pb.setValue(0)

        # 3 秒后隐藏进度条
        QTimer.singleShot(3000, lambda: [pb.setVisible(False) for pb in self._progress_bars])

        msg = ""
        if success_names:
            msg += f"下发成功：{', '.join(success_names)}\n"
        if fail_names:
            msg += f"下发失败：{', '.join(fail_names)}"
        self._log(msg.strip() or "操作完成")

        if fail_names:
            QMessageBox.warning(self, "部分失败", msg)
        else:
            QMessageBox.information(self, "下发成功", msg)

    # ── 日志 ──

    def _log(self, text: str):
        logger.info("[BatchTab] %s", text)
        self.lbl_log.setText(text)
