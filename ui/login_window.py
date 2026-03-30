# login_window.py
from PySide6.QtWidgets import QDialog, QVBoxLayout, QGridLayout, QLineEdit, QPushButton, QLabel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

class LoginWindow(QDialog):
    """基础登录窗口：仅保留账号/密码校验与成功进入。"""

    login_success = Signal()
    VALID_USERS = {
        "admin": "123456",
        "user": "password",
        "test": "test123",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EMS 上位机系统 - 登录")
        self.setFixedSize(600, 420)
        self.setWindowModality(Qt.ApplicationModal)

        # 样式与主界面保持一致
        self.setStyleSheet("""
            QWidget {
                font-family: 'Microsoft YaHei UI';
                font-size: 10pt;
                background-color: #F8F9FB;
            }

            QLabel {
                color: #2F3E4C;
            }

            QLineEdit {
                padding: 6px 10px;
                border: 1px solid #C0C4CC;
                border-radius: 2px;
                background-color: #FFFFFF;
                color: #333333;
                min-height: 34px;
            }

            QLineEdit:focus {
                border: 1px solid #409EFF;
            }

            QPushButton {
                background-color: #4A5D73;
                color: white;
                border-radius: 2px;
                padding: 8px 14px;
                border: 1px solid #3E4E60;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #5D738F;
            }

            QPushButton:pressed {
                background-color: #344252;
            }

            QPushButton:disabled {
                background-color: #C0C4CC;
                color: #606266;
            }
        """)

        self.setup_ui()

        self.user_edit.returnPressed.connect(self.login)
        self.pwd_edit.returnPressed.connect(self.login)
        self.login_btn.clicked.connect(self.login)
        self.user_edit.setFocus()
    
    def setup_ui(self):
        """构建基础登录界面。"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(50, 34, 50, 30)
        main_layout.setSpacing(10)

        title_label = QLabel("上位机系统")
        title_font = QFont("Microsoft YaHei UI", 18, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #2F3E4C; margin-bottom: 5px;")
        main_layout.addWidget(title_label)

        subtitle_label = QLabel("EMS实时监控平台")
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: #909399; font-size: 11pt;")
        main_layout.addWidget(subtitle_label)

        main_layout.addSpacing(10)

        form_layout = QGridLayout()
        form_layout.setSpacing(10)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setColumnStretch(0, 0)
        form_layout.setColumnStretch(1, 1)

        user_label = QLabel("账号：")
        user_label.setFixedWidth(50)
        user_label.setStyleSheet("font-weight: bold; color: #2F3E4C;")
        user_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("请输入账号")

        form_layout.addWidget(user_label, 0, 0)
        form_layout.addWidget(self.user_edit, 0, 1)

        pwd_label = QLabel("密码：")
        pwd_label.setFixedWidth(50)
        pwd_label.setStyleSheet("font-weight: bold; color: #2F3E4C;")
        pwd_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.pwd_edit = QLineEdit()
        self.pwd_edit.setPlaceholderText("请输入密码")
        self.pwd_edit.setEchoMode(QLineEdit.Password)

        form_layout.addWidget(pwd_label, 1, 0)
        form_layout.addWidget(self.pwd_edit, 1, 1)

        main_layout.addLayout(form_layout)

        main_layout.addSpacing(10)

        self.login_btn = QPushButton("登 录")
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.setMinimumHeight(36)
        main_layout.addWidget(self.login_btn)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #F56C6C; font-size: 9pt; min-height: 18px;")
        main_layout.addWidget(self.status_label)

        main_layout.addStretch()

        footer = QLabel("合肥工业大学  |  交通能源协同控制实验室")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color: #909399; font-size: 9pt;")
        main_layout.addWidget(footer)
    
    def login(self):
        """执行最基础的账号密码登录流程。"""
        username = self.user_edit.text().strip()
        password = self.pwd_edit.text().strip()

        if not username or not password:
            self.status_label.setText("请输入用户名和密码")
            self.status_label.setStyleSheet("color: #F56C6C; font-size: 9pt;")
            return

        self.login_btn.setEnabled(False)
        self.login_btn.setText("登录中...")
        self.status_label.setText("")

        if self._validate_credentials(username, password):
            self.status_label.setText("登录成功")
            self.status_label.setStyleSheet("color: #67C23A; font-size: 9pt;")
            self._on_login_success()
            return

        self.status_label.setText("用户名或密码错误")
        self.status_label.setStyleSheet("color: #F56C6C; font-size: 9pt;")
        self.pwd_edit.clear()
        self.pwd_edit.setFocus()
        self.login_btn.setEnabled(True)
        self.login_btn.setText("登 录")
    
    def _validate_credentials(self, username, password):
        """本地凭据校验。"""
        return self.VALID_USERS.get(username) == password

    def _on_login_success(self):
        """登录成功后发信号并关闭登录框。"""
        self.login_btn.setEnabled(True)
        self.login_btn.setText("登 录")
        self.login_success.emit()
        self.accept()


    