# main_window.py
# -*- coding: utf-8 -*-
"""
主窗口

MainWindow 只负责：
  - 路由 data_received(pcs_id, ...) → 总览卡片更新
  - 路由 status_changed(pcs_id, ...) → 总览卡片 + Tab 标题

各台 PCS 的详细数据刷新、对时、参数读写 → 由 PCSSingleTab 自治完成
"""

import logging
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QTabWidget,
    QVBoxLayout, QWidget
)
from ui.widgets import (
    PCSOverviewTab, PCSSingleTab, PCSBatchTab, SimulationModelsTab,
    SceneApplicationsTab, AboutTab
)
from config import MULTI_PCS_CONFIG
from data_storage import DataStorageEngine

logger = logging.getLogger(__name__)


class MainWindow(QWidget):
    def __init__(self, device_managers: list, ui_signaller):
        """
        device_managers : list[DeviceManager]，长度 == len(MULTI_PCS_CONFIG)
        """
        super().__init__()
        self.device_managers = device_managers
        self.ui_signaller = ui_signaller

        # ── 数据存储引擎（全局单例，由批量操作 Tab 的 UI 控制）──
        self.storage_engine = DataStorageEngine()

        # pcs_id → Tab 索引映射（总览=0，PCS-1=1...N，批量=N+1，仿真=N+2，场景=N+3）
        self._pcs_tab_map: dict[int, int] = {}

        self._setup_ui()
        self._connect_signals()

        if self.ui_signaller is not None:
            self.ui_signaller.log_signal.connect(self._on_ui_log_received)

    # ================================================================
    # UI 构建
    # ================================================================

    def _setup_ui(self):
        if not self.objectName():
            self.setObjectName("main_widget")
        self.resize(1400, 960)
        self.setWindowTitle("EMS 上位机系统 V2.0 — 多台 PCS 联调")
        self.setStyleSheet("""
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
            QTabWidget::pane { border: 2px solid #C0C4CC; background: white; }
            QTabBar::tab {
                background: #E9EDF2; padding: 8px 18px; border: 1px solid #C0C4CC;
                border-bottom: none; margin-right: 2px;
                border-top-left-radius: 4px; border-top-right-radius: 4px;
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
            QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
            QScrollBar::handle:vertical {
                background: #C0C4CC; border-radius: 4px; min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #909399; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 主 Tab ──
        self.tabs_main = QTabWidget()

        # Tab 0：总览
        self.tab_overview = PCSOverviewTab(MULTI_PCS_CONFIG)
        self.tabs_main.addTab(self.tab_overview, "PCS 总览")
        self.tab_overview.jump_to_pcs.connect(self._on_jump_to_pcs)
        self.tab_overview.connect_all.connect(self._on_connect_pcs_list)
        self.tab_overview.disconnect_all.connect(self._on_disconnect_all)

        # Tab 1~5：各台 PCS 单机页
        self._single_tabs: list[PCSSingleTab] = []
        for i, (dm, cfg) in enumerate(zip(self.device_managers, MULTI_PCS_CONFIG)):
            pcs_id   = cfg["id"]
            pcs_name = cfg["name"]
            tab = PCSSingleTab(dm, pcs_id, pcs_name)
            self._single_tabs.append(tab)
            tab_idx = 1 + i       # 总览占 index 0
            self._pcs_tab_map[pcs_id] = tab_idx
            self.tabs_main.addTab(tab, pcs_name)
            self.tabs_main.setTabVisible(tab_idx, False)   # 隐藏，通过总览页跳转

        # Tab 6：批量操作（注入存储引擎）
        self.tab_batch = PCSBatchTab(self.device_managers, MULTI_PCS_CONFIG)
        self.tab_batch._storage_engine = self.storage_engine   # 注入引擎实例
        self.tabs_main.addTab(self.tab_batch, "批量操作")

        self.tab_sim_models = SimulationModelsTab(self)
        self.tabs_main.addTab(self.tab_sim_models, "仿真模型")

        self.tab_scenes = SceneApplicationsTab(self, self.device_managers)
        self.tabs_main.addTab(self.tab_scenes, "场景应用")

        self.tab_about = AboutTab()
        self.tabs_main.addTab(self.tab_about, "关于我们")

        outer.addWidget(self.tabs_main)

        # ── 底部全局状态栏 ──
        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet("background-color: #F0F2F5; border-top: 1px solid #E4E7ED;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 0, 12, 0)
        bar_layout.setSpacing(8)

        lbl_lab = QLabel("合肥工业大学  交通能源协同控制实验室")
        lbl_lab.setStyleSheet("color: #909399; font-size: 9pt;")
        bar_layout.addWidget(lbl_lab)
        bar_layout.addStretch()

        outer.addWidget(bar)

    # ================================================================
    # 信号连接
    # ================================================================

    def _connect_signals(self):
        # 汇总 5 台 DM 的状态信号 → 更新总览卡片 + Tab 标题
        for dm in self.device_managers:
            dm.data_received.connect(self._on_pcs_data)
            dm.status_changed.connect(self._on_pcs_status)

    # ================================================================
    # 路由槽函数
    # ================================================================

    def _on_pcs_data(self, pcs_id: int, data_type: str, data: dict):
        """data_received(pcs_id, data_type, data) → 总览卡片更新 + 场景路由 + 数据存储"""
        self.tab_overview.on_pcs_data(pcs_id, data_type, data)
        self.tab_scenes.on_pcs_data(pcs_id, data_type, data)
        # ── 路由到存储引擎（由批量操作 Tab 的 UI 控制是否实际写入）──
        try:
            self.storage_engine.on_data_received(pcs_id, data_type, data)
        except Exception:
            pass  # 存储异常不影响主流程

    def _on_pcs_status(self, pcs_id: int, is_connected: bool, desc: str):
        """status_changed(pcs_id, ...) → 总览卡片 + Tab 标题颜色"""
        self.tab_overview.on_pcs_status(pcs_id, is_connected, desc)
        tab_idx = self._pcs_tab_map.get(pcs_id)
        if tab_idx is not None:
            cfg = next((c for c in MULTI_PCS_CONFIG if c["id"] == pcs_id), {})
            name = cfg.get("name", f"PCS-{pcs_id}")
            suffix = " ✓" if is_connected else ""
            self.tabs_main.setTabText(tab_idx, name + suffix)

    def _on_jump_to_pcs(self, pcs_id: int):
        """总览卡片点击 → 跳转到对应单机 Tab"""
        tab_idx = self._pcs_tab_map.get(pcs_id)
        if tab_idx is not None:
            self.tabs_main.setCurrentIndex(tab_idx)

    def _on_connect_pcs_list(self, pcs_ids: list):
        """总览页一键连接 → 按勾选列表连接指定 PCS"""
        for dm in self.device_managers:
            # device_manager 里存了 self.pcs_id
            if hasattr(dm, 'pcs_id') and dm.pcs_id in pcs_ids:
                try:
                    dm.connect_device()
                    logger.info(f"总览一键连接: PCS-{dm.pcs_id} 已发起连接")
                except Exception as e:
                    logger.error(f"总览一键连接失败: PCS-{getattr(dm, 'pcs_id', '?')} - {e}")

    def _on_disconnect_all(self):
        """总览页全部断开 → 断开所有 PCS"""
        for dm in self.device_managers:
            try:
                dm.disconnect_device()
            except Exception as e:
                logger.error(f"断开失败: PCS-{getattr(dm, 'pcs_id', '?')} - {e}")
        logger.info("总览全部断开完成")

    # ================================================================
    # UI 日志
    # ================================================================

    def _on_ui_log_received(self, level: str, message: str):
        """UI 日志信号回调 → 路由到总览页日志框"""
        if hasattr(self, "tab_overview") and hasattr(self.tab_overview, "append_log"):
            self.tab_overview.append_log(level, message)

    # ================================================================
    # 预测 Tab 占位槽函数（PredictionTab 需要 host 提供）
    # ================================================================

    def _on_start_pv_predict(self): pass
    def _on_stop_pv_predict(self): pass
    def _on_start_load_predict(self): pass
    def _on_stop_load_predict(self): pass
    def _on_clear_pv_pred_log(self): pass
    def _on_clear_load_pred_log(self): pass
