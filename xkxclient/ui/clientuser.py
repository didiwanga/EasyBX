"""客户端用户窗口（账号菜单 →「客户端用户」）。

功能：
- 注册 / 登录 / 登出客户端账号（非强制）
- 登录后云同步：上传 / 下载「客户端设置」（config.json，不含账号密码）
- 登录后云同步：上传 / 下载「自动化设置」（宏/触发器/定时器/别名，含共享作用域）
- 下载自动化前询问是否备份本地到 backup/（覆盖式），可还原上次备份

登录态（token）持久化在 config 的 client_user 命名空间，不涉及游戏账号密码。
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QLabel, QMessageBox, QGroupBox, QWidget, QGridLayout,
)

from xkxclient.core import client_user
from xkxclient.core.config import json_read, json_write


class ClientUserDialog(QDialog):
    """客户端用户：注册/登录 + 云同步设置与自动化。"""

    def __init__(self, app=None, parent=None) -> None:
        super().__init__(parent)
        self.app = app
        self.config = app.config if app else None
        self.setWindowTitle("客户端用户")
        self.setMinimumWidth(460)
        self._build()
        self._refresh()

    # ---- 界面 ----
    def _build(self) -> None:
        root = QVBoxLayout(self)

        # 登录区
        lg = QGroupBox("客户端账号")
        form = QFormLayout(lg)
        self.user_ed = QLineEdit()
        self.user_ed.setPlaceholderText("注册/登录使用的客户端账号名")
        self.pass_ed = QLineEdit()
        self.pass_ed.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("账号", self.user_ed)
        form.addRow("密码", self.pass_ed)
        self.login_btn = QPushButton("登录")
        self.reg_btn = QPushButton("注册")
        self.logout_btn = QPushButton("登出")
        self.login_btn.clicked.connect(self._login)
        self.reg_btn.clicked.connect(self._register)
        self.logout_btn.clicked.connect(self._logout)
        row = QHBoxLayout()
        row.addWidget(self.login_btn)
        row.addWidget(self.reg_btn)
        row.addWidget(self.logout_btn)
        row.addStretch(1)
        form.addRow(row)
        self.status_lb = QLabel("未登录")
        form.addRow(self.status_lb)
        root.addWidget(lg)

        # 同步区
        sg = QGroupBox("云同步")
        sg.setEnabled(False)
        gl = QGridLayout(sg)

        self.up_set_btn = QPushButton("上传客户端设置 ⬆")
        self.dl_set_btn = QPushButton("下载客户端设置 ⬇")
        self.up_auto_btn = QPushButton("上传自动化设置 ⬆")
        self.dl_auto_btn = QPushButton("下载自动化设置 ⬇")
        self.restore_btn = QPushButton("还原上次自动化备份")
        self.up_set_btn.clicked.connect(self._upload_settings)
        self.dl_set_btn.clicked.connect(self._download_settings)
        self.up_auto_btn.clicked.connect(self._upload_automation)
        self.dl_auto_btn.clicked.connect(self._download_automation)
        self.restore_btn.clicked.connect(self._restore_backup)
        gl.addWidget(self.up_set_btn, 0, 0)
        gl.addWidget(self.dl_set_btn, 0, 1)
        gl.addWidget(self.up_auto_btn, 1, 0)
        gl.addWidget(self.dl_auto_btn, 1, 1)
        gl.addWidget(self.restore_btn, 2, 0, 1, 2)
        root.addWidget(sg)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn)

        self.sg = sg
        self.sg_up_buttons = (self.up_set_btn, self.dl_set_btn,
                              self.up_auto_btn, self.dl_auto_btn, self.restore_btn)

    # ---- 登录态 ----
    def _token(self) -> str:
        if self.config is None:
            return ""
        return str(self.config.get("client_user.token") or "")

    def _username(self) -> str:
        if self.config is None:
            return ""
        return str(self.config.get("client_user.username") or "")

    def _set_auth(self, username: str, token: str) -> None:
        if self.config is not None:
            self.config.set("client_user", {"username": username, "token": token})

    def _refresh(self) -> None:
        name = self._username()
        if name:
            self.status_lb.setText(f"已登录：{name}")
            self.user_ed.setText(name)
            self.pass_ed.clear()
            self.user_ed.setEnabled(False)
            self.login_btn.setEnabled(False)
            self.reg_btn.setEnabled(False)
            self.logout_btn.setEnabled(True)
            self.sg.setEnabled(True)
        else:
            self.status_lb.setText("未登录（非强制，可跳过）")
            self.user_ed.setEnabled(True)
            self.login_btn.setEnabled(True)
            self.reg_btn.setEnabled(True)
            self.logout_btn.setEnabled(False)
            self.sg.setEnabled(False)

    def _login(self) -> None:
        name = self.user_ed.text().strip()
        pwd = self.pass_ed.text()
        if not name or not pwd:
            QMessageBox.information(self, "客户端用户", "请输入账号和密码。")
            return
        try:
            token = client_user.login(name, pwd)
        except client_user.ClientUserError as exc:
            QMessageBox.warning(self, "登录失败", str(exc))
            return
        self._set_auth(name, token)
        QMessageBox.information(self, "客户端用户", f"登录成功：{name}")
        self._refresh()

    def _register(self) -> None:
        name = self.user_ed.text().strip()
        pwd = self.pass_ed.text()
        if not name or not pwd:
            QMessageBox.information(self, "客户端用户", "请输入账号和密码（密码至少 4 位）。")
            return
        if len(pwd) < 4:
            QMessageBox.warning(self, "客户端用户", "密码至少 4 位。")
            return
        try:
            token = client_user.register(name, pwd)
        except client_user.ClientUserError as exc:
            QMessageBox.warning(self, "注册失败", str(exc))
            return
        self._set_auth(name, token)
        QMessageBox.information(self, "客户端用户", f"注册成功：{name}")
        self._refresh()

    def _logout(self) -> None:
        if self.config is not None:
            self.config.set("client_user", {"username": "", "token": ""})
        self._refresh()

    # ---- 同步：客户端设置 ----
    def _upload_settings(self) -> None:
        ret = QMessageBox.question(
            self, "上传客户端设置",
            "将把本机所有客户端设置上传到服务器，覆盖服务器上该账号已有的设置。\n\n"
            "注意：游戏账号和密码只保存在本地，绝不会上传到服务器。\n\n确定上传？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        settings = client_user.pack_settings(self.config)
        try:
            client_user.upload_settings(self._token(), settings)
        except client_user.ClientUserError as exc:
            QMessageBox.warning(self, "上传失败", str(exc))
            return
        QMessageBox.information(self, "上传客户端设置", "客户端设置已上传。")

    def _download_settings(self) -> None:
        ret = QMessageBox.question(
            self, "下载客户端设置",
            "将用服务器上的客户端设置覆盖本机所有设置。\n\n"
            "注意：游戏账号和密码只保存在本地，绝不会上传到服务器。\n\n确定下载？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            settings = client_user.download_settings(self._token())
            client_user.unpack_settings(self.config, settings)
        except client_user.ClientUserError as exc:
            QMessageBox.warning(self, "下载失败", str(exc))
            return
        QMessageBox.information(self, "下载客户端设置", "客户端设置已下载并覆盖本机设置。")

    # ---- 同步：自动化 ----
    def _upload_automation(self) -> None:
        ret = QMessageBox.question(
            self, "上传自动化设置",
            "将把本机全部自动化设置（宏/触发器/定时器/别名，含共享作用域）上传到服务器，"
            "覆盖服务器上该账号已有的自动化设置。\n\n"
            "游戏账号和密码不会上传。确定上传？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        automation = client_user.pack_automation(self.config)
        try:
            client_user.upload_automation(self._token(), automation)
        except client_user.ClientUserError as exc:
            QMessageBox.warning(self, "上传失败", str(exc))
            return
        QMessageBox.information(self, "上传自动化设置", "自动化设置已上传。")

    def _download_automation(self) -> None:
        ret = QMessageBox.question(
            self, "下载自动化设置",
            "将用服务器上的自动化设置覆盖本机全部自动化设置（宏/触发器/定时器/别名）。\n\n"
            "下载前是否先备份当前本地自动化设置？\n"
            "（备份覆盖 backup 目录；可随时「还原上次自动化备份」）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
        if ret == QMessageBox.StandardButton.Cancel:
            return
        try:
            automation = client_user.download_automation(self._token())
            if ret == QMessageBox.StandardButton.Yes:
                self._backup_automation()
            client_user.unpack_automation(self.config, automation)
        except client_user.ClientUserError as exc:
            QMessageBox.warning(self, "下载失败", str(exc))
            return
        # 通知各账号会话重载自动化
        bus = getattr(self.app, "bus", None)
        if bus is not None:
            for acc in list((self.config.accounts() or {}).keys()):
                bus.publish("automation.saved", account=acc, kind="all")
        QMessageBox.information(self, "下载自动化设置", "自动化设置已下载并覆盖本机设置。")

    # ---- 备份/还原 ----
    def _backup_dir(self):
        return self.config.root / "backup"

    def _backup_automation(self) -> None:
        """备份本地自动化到 backup/（覆盖式）。"""
        if self.config is None:
            return
        bdir = self._backup_dir()
        bdir.mkdir(parents=True, exist_ok=True)
        shared = json_read(self.config.root / "automation_shared.json")
        json_write(bdir / "automation_shared.json", shared)
        accs_dir = self.config.root / "accounts"
        bak_accs = bdir / "backup_accounts"
        bak_accs.mkdir(exist_ok=True)
        for child in accs_dir.iterdir() if accs_dir.is_dir() else []:
            if child.is_dir():
                src = child / "automation.json"
                if src.exists():
                    json_write(bak_accs / f"{child.name}.json", json_read(src))

    def _restore_backup(self) -> None:
        if self.config is None:
            return
        bdir = self._backup_dir()
        if not (bdir / "automation_shared.json").exists():
            QMessageBox.information(self, "还原备份", "尚无可用备份。")
            return
        ret = QMessageBox.question(
            self, "还原上次备份",
            "将用 backup 目录中的上次备份覆盖当前本地全部自动化设置。确定还原？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        shared = json_read(bdir / "automation_shared.json")
        json_write(self.config.root / "automation_shared.json", shared)
        bak_accs = bdir / "backup_accounts"
        if bak_accs.is_dir():
            for f in bak_accs.iterdir():
                if f.suffix == ".json":
                    aid = f.stem
                    json_write(self.config.account_file(aid) / "automation.json", json_read(f))
        bus = getattr(self.app, "bus", None)
        if bus is not None:
            for acc in list((self.config.accounts() or {}).keys()):
                bus.publish("automation.saved", account=acc, kind="all")
        QMessageBox.information(self, "还原备份", "已还原上次自动化备份。")