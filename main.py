# main.py
import sys
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import Qt

from logger import setup_global_logger, UILogSignaller
from driver import DeviceManager
from ui.main_window import MainWindow
from ui.login_window import LoginWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    login_window = LoginWindow()
    if login_window.exec() != QDialog.Accepted:
        return 0

    ui_signaller = UILogSignaller()
    setup_global_logger(ui_signaller)
    device_manager = DeviceManager()
    main_window = MainWindow(device_manager, ui_signaller)

    device_manager.status_changed.connect(
        main_window.update_connection_ui,
        Qt.QueuedConnection,
    )

    main_window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
