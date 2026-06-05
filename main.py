import sys
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import Qt

from logger import setup_global_logger, UILogSignaller
from drivers import DeviceManager
from config import MULTI_PCS_CONFIG
from ui.main_window import MainWindow
from ui.login_window import LoginWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    """登录界面暂时注释，方便调试"""
    # login_window = LoginWindow()
    # if login_window.exec() != QDialog.Accepted:
    #     return 0

    ui_signaller = UILogSignaller()
    setup_global_logger(ui_signaller)

    # 五台 PCS，每台独立串口，循环实例化各自的 DeviceManager
    device_managers = [
        DeviceManager(pcs_config=cfg)
        for cfg in MULTI_PCS_CONFIG
    ]

    main_window = MainWindow(device_managers, ui_signaller)

    main_window.show()
    # print(f"窗口尺寸: {main_window.width()}x{main_window.height()}")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
