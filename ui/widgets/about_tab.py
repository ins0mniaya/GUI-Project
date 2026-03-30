from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QWidget


class AboutTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        layout_about = QVBoxLayout(self)
        h_layout_split_about = QHBoxLayout()
        h_layout_split_about.addWidget(QGroupBox("系统版本信息"), 3)
        v_layout_right_about = QVBoxLayout()
        v_layout_right_about.addWidget(QGroupBox("实验室介绍"), 1)
        v_layout_right_about.addWidget(QGroupBox("操作日志"), 1)
        h_layout_split_about.addLayout(v_layout_right_about, 2)
        layout_about.addLayout(h_layout_split_about)
