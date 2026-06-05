from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QVBoxLayout, QWidget, QLabel,
    QTextEdit, QFrame, QGridLayout
)
from .common_widgets import wrap_scroll_area

# 图标主题名称
_ICON_BATTERY = "battery-full"
_ICON_SCHOOL = "school"
_ICON_STAR = "star"
_ICON_SIZE = 28


def _make_icon_pixmap(name: str, size: int = _ICON_SIZE) -> QPixmap | None:
    """获取系统主题图标 pixmap，找不到返回 None。"""
    icon = QIcon.fromTheme(name)
    return icon.pixmap(size, size) if not icon.isNull() else None


def _icon_title(icon_name: str, fallback_char: str, text: str,
               font_size: str = "10pt") -> QWidget:
    """创建 图标+文字 的标题行。找不到主题图标时 fallback 到 Unicode 字符。"""
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(6)

    pm = _make_icon_pixmap(icon_name)
    if pm:
        icon_lbl = QLabel()
        icon_lbl.setPixmap(pm)
        layout.addWidget(icon_lbl)
    else:
        char_lbl = QLabel(fallback_char)
        char_lbl.setStyleSheet(f"font-size: {font_size};")
        layout.addWidget(char_lbl)

    text_lbl = QLabel(text)
    text_lbl.setStyleSheet(f"font-size: {font_size}; font-weight: bold;")
    layout.addWidget(text_lbl)
    layout.addStretch()
    return widget


class AboutTab(QWidget):
    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        """构建关于我们界面"""
        # 内容 widget
        inner = QWidget()
        main_layout = QVBoxLayout(inner)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # 系统信息卡片
        system_card = QFrame()
        system_card.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border: 1px solid #E4E7ED;
                border-radius: 8px;
                padding: 15px;
            }
        """)
        system_layout = QVBoxLayout(system_card)

        # 标题行：图标 + 文字
        title_row = _icon_title(_ICON_BATTERY, "🔋", "EMS 能量管理系统",
                                font_size="24pt")
        title_row.setStyleSheet(
            "font: 24pt 'Microsoft YaHei UI'; font-weight: bold; color: #1E293B;"
        )
        title_row.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 覆盖内部子 label 的样式
        for child in title_row.findChildren(QLabel):
            child.setStyleSheet(
                "font: 24pt 'Microsoft YaHei UI'; font-weight: bold; color: #1E293B;"
            )
        system_layout.addWidget(title_row)

        # 副标题
        subtitle = QLabel("Energy Management System")
        subtitle.setStyleSheet("font: 12pt 'Microsoft YaHei UI'; color: #64748B;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        system_layout.addWidget(subtitle)

        system_layout.addSpacing(20)

        # 版本信息网格
        info_grid = QGridLayout()
        info_grid.setSpacing(15)

        info_items = [
            ("软件版本", "V2.0.0"),
            ("发布日期", "2026-04-23"),
            ("通信协议", "RS485 自定义协议"),
            ("支持设备", "PCS / BMS / CMS"),
            ("运行平台", "Windows 10/11"),
            ("开发框架", "PySide6 + Python 3.x"),
        ]

        for idx, (label, value) in enumerate(info_items):
            row, col = idx // 3, (idx % 3) * 2
            lbl_name = QLabel(f"{label}:")
            lbl_name.setStyleSheet("color: #606266; font-size: 10pt;")
            lbl_val = QLabel(value)
            lbl_val.setStyleSheet("color: #1E293B; font-size: 10pt; font-weight: bold;")
            info_grid.addWidget(lbl_name, row, col)
            info_grid.addWidget(lbl_val, row, col + 1)

        system_layout.addLayout(info_grid)
        main_layout.addWidget(system_card)

        # 中间区域：实验室介绍 + 功能特性
        middle_layout = QHBoxLayout()

        # 实验室介绍
        lab_group = QGroupBox("")
        lab_layout = QVBoxLayout(lab_group)
        lab_layout.setContentsMargins(0, 0, 0, 0)
        lab_layout.insertWidget(0, _icon_title(_ICON_SCHOOL, "🏫", "实验室介绍",
                                              font_size="11pt"))
        lab_text = QTextEdit()
        lab_text.setReadOnly(True)
        lab_text.setHtml("""
        <h3>合肥工业大学 · 交通能源协同控制实验室</h3>
        <p>本实验室致力于交通能源系统优化控制、智能电网与储能技术研究。</p>
        <p><b>主要研究方向：</b></p>
        <ul>
            <li>储能系统能量管理与优化控制</li>
            <li>光储充一体化系统设计与控制</li>
            <li>电动汽车与电网互动(V2G)技术</li>
            <li>新能源发电预测与功率控制</li>
        </ul>
        """)
        lab_text.setMaximumHeight(200)
        lab_layout.addWidget(lab_text)
        middle_layout.addWidget(lab_group, 1)

        # 功能特性
        feature_group = QGroupBox("")
        feature_layout = QVBoxLayout(feature_group)
        feature_layout.setContentsMargins(0, 0, 0, 0)
        feature_layout.insertWidget(
            0, _icon_title(_ICON_STAR, "✨", "功能特性", font_size="11pt")
        )
        feature_text = QTextEdit()
        feature_text.setReadOnly(True)
        feature_text.setHtml("""
        <h3>系统功能</h3>
        <ul>
            <li><b>实时监控：</b>PCS/BMS/电网/光伏/温度/告警全方位监测</li>
            <li><b>参数配置：</b>支持基础/电网/电池/保护参数远程设置</li>
            <li><b>智能预测：</b>光伏功率与负荷预测，支持多种AI模型</li>
            <li><b>数据记录：</b>历史数据存储与查询，告警日志管理</li>
            <li><b>故障诊断：</b>故障码解析与告警提示</li>
        </ul>
        """)
        feature_text.setMaximumHeight(200)
        feature_layout.addWidget(feature_text)
        middle_layout.addWidget(feature_group, 1)

        main_layout.addLayout(middle_layout)

        # 底部：版权信息
        footer = QLabel("© 2026 合肥工业大学 交通能源协同控制实验室 | All Rights Reserved")
        footer.setStyleSheet("color: #909399; font-size: 9pt;")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(footer)

        main_layout.addStretch()

        # 用滚动区包裹整体
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(wrap_scroll_area(inner))
