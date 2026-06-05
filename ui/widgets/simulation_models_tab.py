# -*- coding: utf-8 -*-
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from .pv_model_tab import PVModelTab
from .wind_model_tab import WindModelTab

try:
    from .flywheel_model_tab import FlywheelModelTab
except ImportError:
    FlywheelModelTab = None

try:
    from .load_model_tab import LoadModelTab
except ImportError:
    LoadModelTab = None


class PlaceholderModelTab(QWidget):
    def __init__(self, title, message):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #1F2937;")
        layout.addWidget(title_label)

        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        message_label.setStyleSheet(
            "QLabel { background: #FFFFFF; border: 1px solid #D1D5DB; "
            "border-radius: 4px; padding: 16px; color: #4B5563; }"
        )
        layout.addWidget(message_label)
        layout.addStretch(1)


class SimulationModelsTab(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._build_ui()
        application = QCoreApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.shutdown_processes)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.tabs = QTabWidget()
        pv_tab = PVModelTab(self.host)
        wind_tab = WindModelTab(self.host)
        self.tabs.addTab(pv_tab, "光伏发电模型")
        self.tabs.addTab(wind_tab, "风力发电模型")
        if FlywheelModelTab is not None:
            self.tabs.addTab(FlywheelModelTab(self.host), "飞轮储能模型")
        else:
            self.tabs.addTab(
                PlaceholderModelTab(
                    "飞轮储能模型",
                    "界面入口已接好。将 `ui/widgets/flywheel_model_tab.py` 和 `packages/flywheel/` "
                    "添加到当前工程后，这里会自动显示飞轮模型界面。",
                ),
                "飞轮储能模型",
            )
        if LoadModelTab is not None:
            self.tabs.addTab(LoadModelTab(self.host), "用电负荷模型")
        else:
            self.tabs.addTab(
                PlaceholderModelTab(
                    "用电负荷模型",
                    "界面入口已接好。将 `ui/widgets/load_model_tab.py` 和 `packages/load_forecast/` "
                    "添加到当前工程后，这里会自动显示用电负荷模型界面。",
                ),
                "用电负荷模型",
            )
        layout.addWidget(self.tabs)

    def shutdown_processes(self):
        """Stop every model child before the outer application window exits."""
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            worker = getattr(tab, "worker", None)
            if worker is not None:
                worker.shutdown()
