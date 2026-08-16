"""宏分享对话框：上传本地宏到服务器 + 浏览/下载共享宏。

工具栏/菜单「宏分享…」打开。本地宏来自当前账号 automation.json 的 macros 列表
（含共享作用域），上传时附带 name/author/desc/type/graph|steps。下载的宏写入
当前账号 automation.json（同名覆盖），并重载引擎。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, QListWidgetItem,
    QLineEdit, QTextEdit, QPushButton, QLabel, QMessageBox, QWidget,
)

from xkxclient.core import macroshare


class MacroShareDialog(QDialog):
    """宏分享：左=本地宏，右=服务器共享宏。"""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("宏分享")
        self.resize(760, 480)
        self._remote: list[dict] = []
        self._build()

    # ---- 界面 ----
    def _build(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：本地宏
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("我的宏（上传到服务器）："))
        self.local_list = QListWidget()
        ll.addWidget(self.local_list, 1)
        self.desc_ed = QLineEdit()
        self.desc_ed.setPlaceholderText("一句话说明（可选）")
        ll.addWidget(self.desc_ed)
        self.author_lb = QLabel("作者署名：未登录（分享需先登录客户端账号）")
        ll.addWidget(self.author_lb)
        up_btn = QPushButton("上传选中宏 ⬆")
        up_btn.clicked.connect(self._upload)
        ll.addWidget(up_btn)

        # 右：服务器宏
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("服务器共享宏（点击查看说明，选中后下载）："))
        self.remote_list = QListWidget()
        self.remote_list.currentItemChanged.connect(self._on_remote_selected)
        rl.addWidget(self.remote_list, 1)
        self.remote_detail = QTextEdit()
        self.remote_detail.setReadOnly(True)
        self.remote_detail.setMaximumHeight(120)
        rl.addWidget(self.remote_detail)
        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("刷新列表")
        refresh_btn.clicked.connect(self._refresh)
        dl_btn = QPushButton("下载选中宏 ⬇")
        dl_btn.clicked.connect(self._download)
        del_btn = QPushButton("删除自己的宏")
        del_btn.clicked.connect(self._delete)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(dl_btn, 1)
        btn_row.addWidget(del_btn)
        rl.addLayout(btn_row)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.addWidget(splitter, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

        self._refresh_auth()
        self._load_local()
        self._refresh()

    # ---- 本地宏 ----
    def _load_local(self) -> None:
        self.local_list.clear()
        cfg = self.session.app.config
        macros = cfg.automation(self.session.account_id)["macros"]
        for m in macros:
            name = m.get("name", "")
            is_node = bool(m.get("graph"))
            label = f"[节点图] {name}" if is_node else f"[步骤] {name}"
            if m.get("shared"):
                label += " (共享)"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, m)
            self.local_list.addItem(it)

    def _upload(self) -> None:
        it = self.local_list.currentItem()
        if it is None:
            QMessageBox.information(self, "宏分享", "请先选中要上传的本地宏。")
            return
        author = self._username()
        if not author:
            QMessageBox.information(self, "宏分享", "分享宏需先登录客户端账号（账号菜单→客户端用户）。")
            return
        m = dict(it.data(Qt.ItemDataRole.UserRole))
        m.pop("shared", None)
        m.pop("enabled", None)
        m["author"] = author
        m["owner"] = author
        m["desc"] = self.desc_ed.text().strip()
        m["type"] = "node" if m.get("graph") else "macro"
        try:
            name = macroshare.upload_macro(m)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "上传失败", str(exc))
            return
        QMessageBox.information(self, "宏分享", f"宏「{name}」已上传。")
        self._refresh()

    # ---- 服务器宏 ----
    def _refresh(self) -> None:
        self.remote_list.clear()
        self._remote = []
        self.remote_detail.clear()
        try:
            self._remote = macroshare.list_remote_macros()
        except Exception as exc:  # noqa: BLE001
            self.remote_list.addItem("（无法连接服务器）")
            QMessageBox.warning(self, "刷新失败", str(exc))
            return
        if not self._remote:
            self.remote_list.addItem("（暂无共享宏）")
            return
        me = self._username()
        for r in self._remote:
            name = r.get("name", "")
            kind = "节点图" if r.get("node") else "步骤"
            author = r.get("owner") or r.get("author") or "匿名"
            label = f"{name}  [{kind}]  {author}"
            if me and r.get("owner") == me:
                label += "  (我)"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, r.get("name"))
            self.remote_list.addItem(it)

    def _on_remote_selected(self, cur, _prev) -> None:
        if cur is None:
            return
        name = cur.data(Qt.ItemDataRole.UserRole)
        for r in self._remote:
            if r.get("name") == name:
                self.remote_detail.setPlainText(
                    f"名称：{r.get('name', '')}\n"
                    f"作者：{r.get('owner') or r.get('author') or '匿名'}\n"
                    f"类型：{'节点图' if r.get('node') else '步骤宏'}\n"
                    f"下载次数：{r.get('downloads', 0)}\n"
                    f"上传时间：{r.get('time', '')}\n"
                    f"说明：{r.get('desc') or ''}")
                return

    def _username(self) -> str:
        cfg = self.session.app.config
        return str(cfg.get("client_user.username") or "")

    def _refresh_auth(self) -> None:
        name = self._username()
        if name:
            self.author_lb.setText(f"作者署名：{name}（分享后以该账号署名）")
        else:
            self.author_lb.setText("作者署名：未登录（分享需先登录客户端账号）")

    def _delete(self) -> None:
        it = self.remote_list.currentItem()
        if it is None:
            QMessageBox.information(self, "宏分享", "请先选中要删除的服务器宏。")
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        token = str(self.session.app.config.get("client_user.token") or "")
        if not token:
            QMessageBox.information(self, "宏分享", "删除宏需先登录客户端账号。")
            return
        ret = QMessageBox.question(self, "删除宏", f"确定删除共享宏「{name}」？",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            macroshare.delete_macro(name, token)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        QMessageBox.information(self, "宏分享", f"宏「{name}」已删除。")
        self._refresh()

    def _download(self) -> None:
        it = self.remote_list.currentItem()
        if it is None:
            QMessageBox.information(self, "宏分享", "请先选中要下载的服务器宏。")
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        try:
            macro = macroshare.fetch_macro(name)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "下载失败", str(exc))
            return
        # 覆盖同名宏（保留 enabled=True），写入当前账号
        cfg = self.session.app.config
        acc = self.session.account_id
        data = {d["name"]: dict(d) for d in cfg.automation(acc)["macros"]
                if not d.get("shared")}
        macro.setdefault("name", name)
        macro["enabled"] = True
        macro["shared"] = False
        data[macro["name"]] = macro
        cfg.save_automation(acc, "macros", list(data.values()))
        self.session.reload_automation()
        bus = getattr(self.session.app, "bus", None)
        if bus is not None:
            bus.publish("automation.saved", account=acc, kind="macros")
            bus.publish("ui.message", account=acc, message=f"已下载宏「{macro['name']}」")
        QMessageBox.information(self, "宏分享", f"宏「{macro['name']}」已下载并保存。")
        self._load_local()