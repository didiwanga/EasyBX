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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
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
        self.enc_cb.addItems(["gbk", "utf-8"])
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


class AccountEditDialog(QDialog):
    """账号增改：账号(登录ID/下拉名)、用户名、密码、登录后命令、自动登录。"""

    def __init__(self, account_id: str = "", data: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("账号信息")
        self.id_ed = QLineEdit(account_id)
        self.id_ed.setPlaceholderText("客户端显示名（登录ID）")
        self.user_ed = QLineEdit((data or {}).get("username", ""))
        self.user_ed.setPlaceholderText("游戏内用户名")
        self.pass_ed = QLineEdit()
        self.pass_ed.setEchoMode(QLineEdit.EchoMode.Password)
        pwd = decrypt_password(str((data or {}).get("password", "") or ""))
        if pwd:
            self.pass_ed.setText(pwd)
        init_cmds = (data or {}).get("init_cmds") or []
        self.init_ed = QLineEdit(
            ";".join(init_cmds) if isinstance(init_cmds, list) else str(init_cmds))
        self.auto_cb = QCheckBox("自动登录")
        self.auto_cb.setChecked(bool((data or {}).get("autologin", True)))

        show_btn = QPushButton("显示")
        show_btn.setCheckable(True)
        show_btn.setFixedWidth(52)
        show_btn.toggled.connect(lambda on: self.pass_ed.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))

        form = QFormLayout()
        form.addRow("账号", self.id_ed)
        form.addRow("用户名", self.user_ed)
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.pass_ed, 1)
        pw_row.addWidget(show_btn)
        form.addRow("密码", pw_row)
        form.addRow("登录后命令", self.init_ed)
        form.addRow(self.auto_cb)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(box)

    def result(self) -> tuple[str, dict] | None:
        account_id = self.id_ed.text().strip()
        username = self.user_ed.text().strip()
        if not account_id or not username:
            return None
        data = {
            "username": username,
            "password": encrypt_password(self.pass_ed.text().strip()),
            "init_cmds": [c for c in self.init_ed.text().split(";") if c.strip()],
            "autologin": self.auto_cb.isChecked(),
        }
        return account_id, data


class AccountManagerDialog(QDialog):
    """账号管理：已存账号增/删/改（写入 accounts.json）。"""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("账号管理")
        self.setMinimumWidth(320)
        self.list = QListWidget()
        add_btn = QPushButton("添加")
        edit_btn = QPushButton("编辑")
        del_btn = QPushButton("删除")
        add_btn.clicked.connect(self._add)
        edit_btn.clicked.connect(self._edit)
        del_btn.clicked.connect(self._delete)
        self.list.itemDoubleClicked.connect(lambda _: self._edit())

        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addLayout(btn_row)
        lay.addWidget(close_btn)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for aid in self.config.accounts().keys():
            QListWidgetItem(aid, self.list)

    def _selected(self) -> str | None:
        it = self.list.currentItem()
        return it.text() if it else None

    def _add(self) -> None:
        dlg = AccountEditDialog(parent=self)
        if dlg.exec() and dlg.result():
            aid, data = dlg.result()
            self.config.save_account(aid, data)
            self._reload()
            self.list.setCurrentRow(max(0, self.list.count() - 1))

    def _edit(self) -> None:
        aid = self._selected()
        if not aid:
            return
        dlg = AccountEditDialog(aid, self.config.accounts().get(aid), self)
        if dlg.exec() and dlg.result():
            new_aid, data = dlg.result()
            if new_aid != aid:
                self.config.remove_account(aid)
            self.config.save_account(new_aid, data)
            self._reload()

    def _delete(self) -> None:
        aid = self._selected()
        if not aid:
            return
        ret = QMessageBox.question(self, "删除账号", f"确定删除账号「{aid}」？",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self.config.remove_account(aid)
            self._reload()


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
        self.enc_cb.addItems(["gbk", "utf-8"])
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
        self._autofill_last()

    # ---- 上次登录自动回填 ----
    def _autofill_last(self) -> None:
        """打开登录窗即回填上一次成功连接使用的账号密码。"""
        last = self.config.get("login.last") or {}
        last_id = (last.get("account_id") or "").strip()
        if not last_id:
            return
        if last_id in self.config.accounts():
            self.account_combo.setCurrentText(last_id)   # 触发 _fill_creds 回填全部
        else:
            self.account_combo.setCurrentText(last_id)
            self.user_edit.setText(str(last.get("username", "")))
            pwd = decrypt_password(str(last.get("password", "") or ""))
            if pwd:
                self.pass_edit.setText(pwd)

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

    def _resolve_account_id(self, combo_id: str, username: str) -> str:
        """账号与用户名本应一致：账号框残留旧值而用户名被改写时，账号跟随用户名。

        场景：回填上次账号后用户改了用户名/密码但没清账号框，若仍用旧账号 key
        保存会覆盖旧账号的 username，造成账号库错乱。仅当账号库里该账号名确实
        对应当前用户名时才保留原账号名，否则以新用户名为准。
        """
        if not combo_id or combo_id == username:
            return combo_id or username
        stored = self.config.accounts().get(combo_id)
        if stored and str(stored.get("username", "")).strip() == username:
            return combo_id
        return username

    def _on_connect(self) -> None:
        srv = self._selected_server()
        if srv is None:
            return
        username = self.user_edit.text().strip()
        if not username:
            return
        account_id = self._resolve_account_id(self.account_combo.currentText().strip(), username)
        password = self.pass_edit.text().strip()
        init_cmds = [c for c in self.init_edit.text().split(";") if c.strip()]
        if self.remember_cb.isChecked():
            self.config.save_account(account_id, {
                "username": username,
                "password": encrypt_password(password),
                "init_cmds": init_cmds,
                "autologin": self.auto_cb.isChecked(),
            })
        self.config.set("login.last", {
            "account_id": account_id,
            "username": username,
            "password": encrypt_password(password),
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