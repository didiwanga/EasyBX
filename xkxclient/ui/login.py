from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from xkxclient.core.config import ConfigManager
from xkxclient.core.crypto import decrypt_password, encrypt_password


class ServerDialog(QDialog):
    """A1 服务器增/改/删。"""

    def __init__(self, server: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("服务器")
        self.name_ed = QLineEdit()
        self.host_ed = QLineEdit()
        self.port_ed = QLineEdit()
        self.enc_cb = QComboBox()
        self.enc_cb.addItems(["gbk", "utf-8", "big5"])
        if server:
            self.name_ed.setText(server.get("name", ""))
            self.host_ed.setText(server.get("host", ""))
            self.port_ed.setText(str(server.get("port", 8080)))
            self.enc_cb.setCurrentText(server.get("encoding", "gbk"))
        form = QFormLayout()
        form.addRow("名称", self.name_ed)
        form.addRow("主机", self.host_ed)
        form.addRow("端口", self.port_ed)
        form.addRow("编码", self.enc_cb)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(box)

    def result(self) -> dict | None:
        name = self.name_ed.text().strip()
        host = self.host_ed.text().strip()
        if not name or not host:
            return None
        try:
            port = int(self.port_ed.text().strip() or 8080)
        except ValueError:
            port = 8080
        return {"name": name, "host": host, "port": port, "encoding": self.enc_cb.currentText()}


class LoginWindow(QDialog):
    """登录窗（D3）：服务器选择/管理 + 账号 + 编码 + 记住账号 + 自动登录。"""

    def __init__(self, app=None, main_window=None, parent=None) -> None:
        super().__init__(parent)
        self.app = app
        self.main_window = main_window
        self.config = app.config if app else ConfigManager.instance()
        self.setWindowTitle("EasyBXb - 登录")
        self.setMinimumWidth(420)

        # 服务器
        self.server_combo = QComboBox()
        self._servers_changed()
        self.add_srv = QPushButton("添加")
        self.edit_srv = QPushButton("编辑")
        self.del_srv = QPushButton("删除")
        self.add_srv.clicked.connect(self._add_server)
        self.edit_srv.clicked.connect(self._edit_server)
        self.del_srv.clicked.connect(self._del_server)
        srv_row = QHBoxLayout()
        srv_row.addWidget(self.server_combo, 1)
        srv_row.addWidget(self.add_srv)
        srv_row.addWidget(self.edit_srv)
        srv_row.addWidget(self.del_srv)

        self.enc_cb = QComboBox()
        self.enc_cb.addItems(["gbk", "utf-8", "big5"])
        self.server_combo.currentIndexChanged.connect(self._on_server_changed)
        self._on_server_changed()

        # 账号
        self.account_combo = QComboBox()
        self.account_combo.setEditable(True)
        self.account_combo.addItem("")
        for aid in self.config.accounts().keys():
            self.account_combo.addItem(aid)
        self.account_combo.currentTextChanged.connect(self._fill_creds)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("用户名")
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_cb = QCheckBox("记住到账号库")
        self.remember_cb.setChecked(True)
        self.init_edit = QLineEdit()
        self.init_edit.setPlaceholderText("登录后命令（; 分隔，可空）")
        self.auto_cb = QCheckBox("自动登录")

        form = QFormLayout()
        form.addRow("服务器", srv_row)
        form.addRow("编码", self.enc_cb)
        form.addRow("账号", self.account_combo)
        form.addRow("用户名", self.user_edit)

        self.show_btn = QPushButton("显示")
        self.show_btn.setCheckable(True)
        self.show_btn.setFixedWidth(52)
        self.show_btn.toggled.connect(self._toggle_pw_visible)
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.pass_edit, 1)
        pw_row.addWidget(self.show_btn)
        form.addRow("密码", pw_row)
        form.addRow(self.remember_cb)
        form.addRow("登录后命令", self.init_edit)

        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._on_connect)
        self.register_btn = QPushButton("注册")
        self.register_btn.clicked.connect(self._on_register)
        self.pass_edit.returnPressed.connect(self._on_connect)
        self.user_edit.returnPressed.connect(self._on_connect)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(self.register_btn)
        row.addWidget(self.auto_cb)
        row.addStretch(1)
        row.addWidget(self.connect_btn)
        lay.addLayout(row)

    # ---- 服务器 ----
    def _servers_changed(self) -> None:
        self.server_combo.clear()
        for s in self.config.servers:
            self.server_combo.addItem(f"{s['name']} ({s['host']}:{s['port']})")

    def _on_server_changed(self) -> None:
        idx = self.server_combo.currentIndex()
        if 0 <= idx < len(self.config.servers):
            self.enc_cb.setCurrentText(self.config.servers[idx].get("encoding", "gbk"))

    def _selected_server(self) -> dict | None:
        idx = self.server_combo.currentIndex()
        if 0 <= idx < len(self.config.servers):
            return self.config.servers[idx]
        return None

    def _add_server(self) -> None:
        dlg = ServerDialog(parent=self)
        if dlg.exec() and dlg.result():
            self.config.save_server(dlg.result())
            self._servers_changed()

    def _edit_server(self) -> None:
        idx = self.server_combo.currentIndex()
        if not (0 <= idx < len(self.config.servers)):
            return
        dlg = ServerDialog(self.config.servers[idx], self)
        if dlg.exec() and dlg.result():
            self.config.save_server(dlg.result())
            self._servers_changed()

    def _del_server(self) -> None:
        idx = self.server_combo.currentIndex()
        if 0 <= idx < len(self.config.servers):
            srv = self.config.servers[idx]
            cfg = self.config
            cfg.set("servers", [s for s in cfg.servers if s["name"] != srv["name"]])
            self._servers_changed()

    # ---- 账号 ----
    def _toggle_pw_visible(self, on: bool) -> None:
        self.pass_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        )
        self.show_btn.setText("隐藏" if on else "显示")

    def _fill_creds(self, account_id: str) -> None:
        data = self.config.accounts().get(account_id.strip())
        if data:
            self.user_edit.setText(str(data.get("username", "")))
            self.pass_edit.setText(decrypt_password(str(data.get("password", ""))))
            init_cmds = data.get("init_cmds") or []
            self.init_edit.setText(";".join(init_cmds) if isinstance(init_cmds, list) else str(init_cmds))
            self.auto_cb.setChecked(bool(data.get("autologin", True)))

    def _on_connect(self) -> None:
        srv = self._selected_server()
        if srv is None:
            return
        account_id = self.account_combo.currentText().strip() or self.user_edit.text().strip()
        if not account_id or not self.user_edit.text().strip():
            return
        username = self.user_edit.text().strip()
        password = self.pass_edit.text().strip()
        init_cmds = [c for c in self.init_edit.text().split(";") if c.strip()]
        if self.remember_cb.isChecked():
            self.config.save_account(account_id, {
                "username": username,
                "password": encrypt_password(password),
                "init_cmds": init_cmds,
                "autologin": self.auto_cb.isChecked(),
            })

        session = self.app.session(account_id)
        session.connect_to(srv["host"], srv["port"], encoding=self.enc_cb.currentText(),
                           username=username, password=password, init_cmds=init_cmds, autologin=True)
        self.main_window.add_account_tab(account_id, session)
        self.main_window.show()
        self.close()

    def _on_register(self) -> None:
        """注册：连接服务器，被提示「请输入new」时自动发送 new，之后完全交给用户。"""
        srv = self._selected_server()
        if srv is None:
            return
        uid = self.user_edit.text().strip() or "new"
        session = self.app.session(uid)
        session.connect_to(srv["host"], int(srv["port"]), encoding=self.enc_cb.currentText(),
                           username=None, password=None, init_cmds=[], autologin=True, register=True)
        self.main_window.add_account_tab(uid, session)
        self.main_window.show()
        self.close()