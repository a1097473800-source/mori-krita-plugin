"""A focused native sign-in surface, sharing Mori's dark visual language."""
from urllib.parse import urlsplit

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                            QLineEdit, QPushButton, QCheckBox)

from .realtime_panel import MORI_STYLE, icon_button, set_dark_surface


class MoriLoginDialog(QDialog):
    def __init__(self, parent=None, server_url="https://morinet.chat"):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setObjectName("MoriLoginDialog")
        self.setWindowTitle("登录 Mori")
        self.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.setFixedWidth(420)
        set_dark_surface(self)
        self.setStyleSheet(MORI_STYLE + """
            QDialog#MoriLoginDialog { background:#222225; border:1px solid #48414f; }
            QLabel#LoginBrand { color:#cbbaff; font-size:15px; font-weight:600; letter-spacing:3px; }
            QLabel#LoginTitle { color:#f4f1fa; font-size:24px; font-weight:600; }
            QLineEdit { background:#2a2a2f; color:#f3eff8; border:1px solid #44414d;
                        border-radius:7px; padding:11px 12px; font-size:13px; min-height:19px; }
            QLineEdit:focus { border-color:#b8a8ff; }
            QLineEdit:disabled { color:#88818f; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(10)
        header = QHBoxLayout()
        brand = QLabel("MORI")
        brand.setObjectName("LoginBrand")
        header.addWidget(brand)
        header.addStretch()
        close = icon_button("close", "关闭登录窗口")
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)
        layout.addSpacing(12)
        title = QLabel("登录 Mori")
        title.setObjectName("LoginTitle")
        layout.addWidget(title)
        description = QLabel("使用现有账号，共用积分与创作记录。")
        description.setProperty("role", "muted")
        layout.addWidget(description)
        layout.addSpacing(14)
        self.email = QLineEdit()
        self.email.setAccessibleName("Mori 账号邮箱")
        self.email.setPlaceholderText("输入账号邮箱")
        self.email.setClearButtonEnabled(True)
        self.email.setMaxLength(254)
        label = QLabel("邮箱")
        label.setBuddy(self.email)
        layout.addWidget(label)
        layout.addWidget(self.email)
        layout.addSpacing(4)
        self.password = QLineEdit()
        self.password.setAccessibleName("Mori 账号密码")
        self.password.setPlaceholderText("输入密码")
        self.password.setEchoMode(QLineEdit.Password)
        label = QLabel("密码")
        label.setBuddy(self.password)
        layout.addWidget(label)
        layout.addWidget(self.password)
        self.show_password = QCheckBox("显示密码")
        self.show_password.toggled.connect(lambda checked: self.password.setEchoMode(
            QLineEdit.Normal if checked else QLineEdit.Password))
        layout.addWidget(self.show_password)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setMinimumHeight(32)
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)
        self.submit = QPushButton("登录")
        self.submit.setDefault(True)
        self.submit.setProperty("role", "primary")
        self.submit.setMinimumHeight(42)
        layout.addWidget(self.submit)
        destination = QLabel("连接到 " + (urlsplit(server_url).hostname or "Mori"))
        destination.setAlignment(Qt.AlignCenter)
        destination.setProperty("role", "muted")
        layout.addWidget(destination)
        self.email.setFocus()

    def set_busy(self, busy):
        for widget in (self.email, self.password, self.submit, self.show_password):
            widget.setEnabled(not busy)
        self.submit.setText("正在连接…" if busy else "登录")

    def set_status(self, message, error=False):
        self.status.setText(str(message))
        self.status.setStyleSheet("color:#f0a6ae;" if error else "color:#a4a1ae;")
