# pcs_overview_tab.py
# -*- coding: utf-8 -*-
"""
PCS 总览 Tab：以 5 张卡片展示各台 PCS 的关键状态。
点击卡片可跳转到对应的单台 Tab（通过 jump_to_pcs 信号通知 MainWindow 切换）。
"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)


class PCSCardWidget(QFrame):
    """单台 PCS 状态卡片控件"""
    clicked = Signal(int)   # 点击时发出 pcs_id

    def __init__(self, pcs_id: int, pcs_name: str, parent=None):
        super().__init__(parent)
        self.pcs_id = pcs_id
        self.pcs_name = pcs_name
        self._is_connected = False
        self._setup_ui()

    # ---------- UI 构建 ----------

    def _setup_ui(self):
        self.setObjectName(f"card_pcs_{self.pcs_id}")
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumSize(200, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style(connected=False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # 顶部：名称 + 状态灯
        top = QHBoxLayout()
        self.lbl_name = QLabel(self.pcs_name)
        self.lbl_name.setStyleSheet("font-size: 13pt; font-weight: bold; color: #2F3E4C;")
        top.addWidget(self.lbl_name)
        top.addStretch()
        self.lbl_dot = QLabel("●")
        self.lbl_dot.setStyleSheet("font-size: 18pt; color: #94A3B8;")
        top.addWidget(self.lbl_dot)
        layout.addLayout(top)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #E4E7ED;")
        layout.addWidget(line)

        # 数据网格
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        def _row(r, key_text, value_init="--"):
            lbl_k = QLabel(key_text)
            lbl_k.setStyleSheet("color: #909399; font-size: 9pt;")
            lbl_v = QLabel(value_init)
            lbl_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lbl_v.setStyleSheet("color: #1E293B; font-weight: bold; font-size: 10pt;")
            grid.addWidget(lbl_k, r, 0)
            grid.addWidget(lbl_v, r, 1)
            return lbl_v

        self.lbl_conn_status = _row(0, "连接状态")
        self.lbl_run_state   = _row(1, "运行状态")
        self.lbl_soc         = _row(2, "SOC")
        self.lbl_bat_power  = _row(3, "电池功率")
        self.lbl_grid_power  = _row(4, "电网功率")
        layout.addLayout(grid)
        layout.addStretch()

        # 底部：跳转按钮
        self.btn_jump = QPushButton("查看详情 →")
        self.btn_jump.setFixedHeight(28)
        self.btn_jump.clicked.connect(lambda: self.clicked.emit(self.pcs_id))
        layout.addWidget(self.btn_jump)

    def _apply_style(self, connected: bool):
        border_color = "#409EFF" if connected else "#C0C4CC"
        bg_color      = "#F0F7FF" if connected else "#FFFFFF"
        self.setStyleSheet(f"""
            PCSCardWidget {{
                border: 2px solid {border_color};
                border-radius: 8px;
                background-color: {bg_color};
            }}
        """)

    # ---------- 事件 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.pcs_id)
        super().mousePressEvent(event)

    # ---------- 公开更新接口 ----------

    def update_connection(self, is_connected: bool, desc: str = ""):
        self._is_connected = is_connected
        self._apply_style(is_connected)
        if is_connected:
            self.lbl_dot.setStyleSheet("font-size: 18pt; color: #059669;")
            self.lbl_conn_status.setText("已连接")
            self.lbl_conn_status.setStyleSheet("color: #059669; font-weight: bold; font-size: 10pt;")
        else:
            self.lbl_dot.setStyleSheet("font-size: 18pt; color: #94A3B8;")
            self.lbl_conn_status.setText("未连接" if not desc else desc)
            self.lbl_conn_status.setStyleSheet("color: #D92D20; font-weight: bold; font-size: 10pt;")

    def update_run_state(self, state_str: str):
        self.lbl_run_state.setText(state_str or "--")
        if "故障" in (state_str or ""):
            color = "#D92D20"
        elif "正常" in (state_str or ""):
            color = "#059669"
        elif "自检" in (state_str or "") or "预警" in (state_str or ""):
            color = "#D97706"
        else:
            color = "#64748B"
        self.lbl_run_state.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 10pt;")

    def update_soc(self, soc):
        self.lbl_soc.setText(f"{soc} %" if soc is not None else "--")

    def update_bat_power(self, power):
        if power is None:
            self.lbl_bat_power.setText("--")
            self.lbl_bat_power.setStyleSheet("color: #64748B; font-size: 10pt;")
        else:
            p = float(power)
            sign  = "↑" if p >= 0 else "↓"
            color = "#D97706" if p >= 0 else "#409EFF"
            self.lbl_bat_power.setText(f"{sign} {abs(p):.0f} W")
            self.lbl_bat_power.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 10pt;")

    def update_grid_power(self, power):
        if power is None:
            self.lbl_grid_power.setText("--")
            self.lbl_grid_power.setStyleSheet("color: #64748B; font-size: 10pt;")
        else:
            p = float(power)
            sign  = "↑" if p >= 0 else "↓"
            color = "#D92D20" if p >= 0 else "#059669"
            self.lbl_grid_power.setText(f"{sign} {abs(p):.0f} W")
            self.lbl_grid_power.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 10pt;")


# ===================================================================
# PCSOverviewTab
# ===================================================================

class PCSOverviewTab(QWidget):
    """总览 Tab：5 张 PCS 卡片 + 连接控制栏 + 全局日志框。"""

    jump_to_pcs = Signal(int)
    connect_all   = Signal(list)  # [pcs_id, ...] 要连接的设备列表
    disconnect_all = Signal()     # 断开全部

    def __init__(self, pcs_configs: list, parent=None):
        super().__init__(parent)
        self._cards: dict[int, PCSCardWidget] = {}
        self._setup_ui(pcs_configs)

    # ---------- UI 构建 ----------

    def _setup_ui(self, pcs_configs):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        # ── 顶部标题栏 ──
        title_bar = QFrame()
        title_bar.setFixedHeight(42)
        title_bar.setStyleSheet("background-color: #2C3E50; border-radius: 6px;")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(16, 0, 16, 0)
        title_layout.setSpacing(10)

        lbl_title = QLabel("PCS 设备总览")
        lbl_title.setStyleSheet(
            "font-size: 14pt; font-weight: bold; color: white; background: transparent; letter-spacing: 2px;"
        )
        title_layout.addWidget(lbl_title)

        title_layout.addStretch()

        lbl_hint = QLabel("点击卡片查看单台详情")
        lbl_hint.setStyleSheet("color: #B0C4DE; font-size: 9pt; background: transparent;")
        title_layout.addWidget(lbl_hint)
        outer.addWidget(title_bar)

        # 卡片网格：每行最多 3 个
        cards_layout = QGridLayout()
        cards_layout.setSpacing(16)
        for i, cfg in enumerate(pcs_configs):
            pcs_id   = cfg.get("id", 0)
            pcs_name = cfg.get("name", f"PCS-{pcs_id}")
            card = PCSCardWidget(pcs_id, pcs_name)
            card.clicked.connect(self.jump_to_pcs)
            self._cards[pcs_id] = card
            row, col = divmod(i, 3)
            cards_layout.addWidget(card, row, col)
        outer.addLayout(cards_layout)
        outer.addStretch()

        # ── 连接控制栏 ──
        self._conn_bar = QFrame()
        self._conn_bar.setStyleSheet("""
            QFrame {
                background-color: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
            }
        """)
        conn_layout = QHBoxLayout(self._conn_bar)
        conn_layout.setContentsMargins(14, 10, 14, 10)
        conn_layout.setSpacing(12)

        lbl_conn = QLabel("设备连接")
        lbl_conn.setStyleSheet("font-size: 11pt; font-weight: bold; color: #2F3E4C;")
        conn_layout.addWidget(lbl_conn)

        # 勾选框：每台 PCS 一个
        self._conn_checks: dict[int, QCheckBox] = {}
        for cfg in pcs_configs:
            pcs_id   = cfg.get("id", 0)
            pcs_name = cfg.get("name", f"PCS-{pcs_id}")
            cb = QCheckBox(pcs_name)
            cb.setChecked(True)   # 默认全选
            self._conn_checks[pcs_id] = cb
            conn_layout.addWidget(cb)

        conn_layout.addStretch()

        # 一键全部连接 / 全部断开
        self.btn_connect_all = QPushButton("一键连接")
        self.btn_connect_all.setFixedHeight(30)
        self.btn_connect_all.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                border-radius: 4px;
                padding: 5px 18px;
                font-weight: bold;
                font-size: 10pt;
            }
            QPushButton:hover { background-color: #047857; }
            QPushButton:pressed { background-color: #065F46; }
        """)
        self.btn_connect_all.clicked.connect(self._on_connect_clicked)

        self.btn_disconnect_all = QPushButton("全部断开")
        self.btn_disconnect_all.setFixedHeight(30)
        self.btn_disconnect_all.setStyleSheet("""
            QPushButton {
                background-color: #DC2626;
                color: white;
                border-radius: 4px;
                padding: 5px 18px;
                font-weight: bold;
                font-size: 10pt;
            }
            QPushButton:hover { background-color: #B91C1C; }
            QPushButton:pressed { background-color: #991B1B; }
        """)
        self.btn_disconnect_all.clicked.connect(lambda: self.disconnect_all.emit())

        conn_layout.addWidget(self.btn_connect_all)
        conn_layout.addWidget(self.btn_disconnect_all)

        outer.addWidget(self._conn_bar)

        # ── 全局日志输出框（固定显示在底部）────
        lbl_log = QLabel("全局运行日志")
        lbl_log.setStyleSheet("font-size: 11pt; font-weight: bold; color: #2F3E4C; margin-top: 8px;")
        outer.addWidget(lbl_log)

        self._log_edit = QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumBlockCount(2000)
        self._log_edit.setMaximumHeight(200)
        self._log_edit.setMinimumHeight(120)
        self._log_edit.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1E1E2E;
                color: #D4D4D4;
                font-family: 'Consolas', 'Courier New';
                font-size: 9pt;
                border: 1px solid #3E3E50;
                border-radius: 4px;
                padding: 6px;
            }
        """)
        outer.addWidget(self._log_edit)

    # ---------- 公开接口 ----------

    def _on_connect_clicked(self):
        """一键连接 → 收集勾选的 pcs_id 列表，发信号给 MainWindow"""
        selected = [pid for pid, cb in self._conn_checks.items() if cb.isChecked()]
        if selected:
            self.connect_all.emit(selected)

    def append_log(self, level: str, message: str):
        """由 MainWindow 路由 UI 日志信号调用。"""
        # 用纯文本前缀标记级别，QPlainTextEdit 不支持 HTML 着色
        level_tag = f"[{level.upper()}]"
        self._log_edit.appendPlainText(f"{level_tag} {message}")
        # 自动滚动到底部
        self._log_edit.verticalScrollBar().setValue(
            self._log_edit.verticalScrollBar().maximum()
        )

    def on_pcs_status(self, pcs_id: int, is_connected: bool, desc: str):
        card = self._cards.get(pcs_id)
        if card:
            card.update_connection(is_connected, desc)
            if not is_connected:
                card.update_run_state("--")
                card.update_soc(None)
                card.update_bat_power(None)
                card.update_grid_power(None)

    def on_pcs_data(self, pcs_id: int, data_type: str, data: dict):
        card = self._cards.get(pcs_id)
        if card is None:
            return
        if data_type == "PCS device information parameters":
            card.update_run_state(str(data.get("run_state_str", "--")))
        elif data_type == "battery parameters":
            card.update_soc(data.get("soc_bat"))
            card.update_bat_power(data.get("p_bat"))
        elif data_type == "grid parameters":
            card.update_grid_power(data.get("p_grid"))
