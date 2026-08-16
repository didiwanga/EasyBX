"""自动更新：启动时检测服务器新版本 → 下载 → 提示 → 单exe更新器替换。

设计（单 exe 复用更新器）：
- 客户端每次启动异步请求 EasyBXb_version.json（阿里云 pytools.cloud）
- 清单格式：{"version": "1.2.1", "url": "http://pytools.cloud/EasyBXb.exe", "md5": "..."}
- 服务器版本更新时弹窗询问，确认后下载新版 exe 到 %TEMP%\\EasyBXb_update\\
- 下载完成（校验 MD5）后再次提示「即将关闭客户端」，确认后用子进程启动
  自身 exe 的 ``--update`` 静默模式，主程序退出
- ``--update`` 模式（main.py 入口，无 GUI）：等待旧进程释放目标文件 → 备份并
  替换 → 启动新版 → 清理临时文件 → 退出。全部用标准库，静默运行。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QObject, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QMessageBox, QProgressDialog

from xkxclient.version import VERSION, UPDATE_DOWNLOAD_URL, UPDATE_MANIFEST_URL, is_newer

UPDATE_DIR_NAME = "EasyBXb_update"
NEW_EXE_NAME = "EasyBXb_new.exe"
_WAIT_TIMEOUT = 60.0  # 更新器等待旧进程释放目标文件的最长时间（秒）
_STEP_WAIT = 0.4      # 轮询间隔（秒）


# ---------------------------------------------------------------------------
# 纯逻辑（无 Qt 依赖，供 main.py 更新器模式与测试复用）
# ---------------------------------------------------------------------------

def load_manifest(text: str) -> dict:
    """解析版本清单文本，返回 dict；不合法返回 {}。容忍 UTF-8 BOM 与前后空白。"""
    try:
        data = json.loads(text.lstrip("\ufeff \t\r\n"))
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    if not str(data.get("version", "")).strip():
        return {}
    return data


def _file_unlocked(path: Path) -> bool:
    """目标 exe 是否可被独占打开（旧进程已完全释放文件锁）。"""
    try:
        with open(path, "ab"):
            return True
    except OSError:
        return False


def _wait_target_free(target: Path, timeout: float = _WAIT_TIMEOUT,
                      step: float = _STEP_WAIT) -> bool:
    """等待旧客户端进程完全退出（目标 exe 可写）。返回是否成功。"""
    if not target.exists():
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _file_unlocked(target):
            return True
        time.sleep(step)
    return False


_LOG_PATH = Path(tempfile.gettempdir()) / UPDATE_DIR_NAME / "updater.log"


def _log(message: str) -> None:
    """追加一行更新器日志，便于诊断更新失败。"""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _terminate_target_owners(target: Path) -> None:
    """强制结束占用目标 exe 的旧客户端进程（taskkill /F /IM <basename>.exe）。

    更新器进程自身位于临时目录（EasyBXb_new.exe），进程名与目标不同，
    不会被误杀。多开场景下会一并结束所有旧实例（更新本就要求全部退出）。
    """
    exe_name = target.name
    try:
        subprocess.run(["taskkill", "/F", "/IM", exe_name],
                       capture_output=True, timeout=15)
    except OSError:
        pass
    except subprocess.SubprocessError:
        pass


def run_updater(new_exe: str, target: str, wait_timeout: float = _WAIT_TIMEOUT) -> int:
    """更新器核心：等待旧进程退出 → 备份并替换 → 启动新版 → 清理。返回退出码。

    - new_exe:  已下载到临时目录的新版 exe（即当前更新器进程自身）
    - target:   目标安装路径（要被替换的旧客户端 exe）
    全程静默，不依赖 Qt/GUI。
    """
    new_path = Path(new_exe)
    target_path = Path(target)
    bak_path = target_path.with_suffix(target_path.suffix + ".bak")

    _log(f"updater start new={new_path.name} target={target_path}")
    if not new_path.exists():
        _log("abort: new exe missing")
        return 3

    # 复制前直接强制结束占用目标 exe 的旧客户端进程（复制期间文件必须可写）
    _log("taskkill old process before copy")
    _terminate_target_owners(target_path)
    if not _wait_target_free(target_path, timeout=wait_timeout):
        _log("abort: target still locked after taskkill")
        return 4

    # 备份旧 exe 后替换；替换失败则回滚备份
    try:
        if target_path.exists():
            if bak_path.exists():
                bak_path.unlink()
            shutil.copy2(target_path, bak_path)
        shutil.copy2(new_path, target_path)
    except OSError:
        try:
            if bak_path.exists():
                shutil.copy2(bak_path, target_path)
        except OSError:
            pass
        return 5

    # 启动新版客户端（分离进程，主更新器随即退出）
    try:
        subprocess.Popen([str(target_path)], close_fds=True)
    except OSError:
        pass

    # 清理：删除备份与更新临时目录（删除失败不影响已替换结果）
    try:
        if bak_path.exists():
            bak_path.unlink()
    except OSError:
        pass
    try:
        _cleanup_update_dir(new_path)
    except OSError:
        pass
    return 0


def _cleanup_update_dir(inside: Path) -> None:
    """删除更新临时目录（new_exe 所在目录），残留交给系统临时清理兜底。

    仅当目录名恰为 EasyBXb_update（本次下载产生的目录）时才删除，
    避免误删用户磁盘上同名目录。
    """
    root = inside.parent
    if root.name == UPDATE_DIR_NAME:
        shutil.rmtree(root, ignore_errors=True)


def _cleanup_stale_update_dir() -> None:
    """启动时清理上次更新遗留的临时目录。

    更新器进程自身是 new_exe，运行期间该 exe 文件被 Windows 锁定而无法删除；
    待其退出后文件解锁，客户端下次启动时删除即可。
    """
    root = Path(tempfile.gettempdir()) / UPDATE_DIR_NAME
    if root.name == UPDATE_DIR_NAME:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Qt 交互部分：启动时检测 + 下载 + 提示
# ---------------------------------------------------------------------------

def _format_changelog(data: dict) -> str:
    """把清单 changelog 格式化为 HTML（最新在前，逐条展示）。"""
    rows: list[str] = []
    for entry in data.get("changelog") or []:
        ver = str(entry.get("v") or entry.get("version") or "")
        date = str(entry.get("date") or "")
        items = entry.get("items") or []
        head = f"<b>v{ver}</b>"
        if date:
            head += f" <span style='color:gray'>（{date}）</span>"
        rows.append(f"<p style='margin:6px 0 2px 0'>{head}</p>")
        for it in items:
            rows.append(f"<p style='margin:0 0 1px 12px'>· {it}</p>")
    return "".join(rows)


class UpdateManager(QObject):
    """启动后异步检查新版本；发现新版则引导下载、确认、启动更新器并退出主程序。"""

    def __init__(self, app, parent=None) -> None:
        super().__init__(parent)
        self.app = app
        self.nam = QNetworkAccessManager(self)
        self.nam.finished.connect(self._on_reply)
        self._download_reply: QNetworkReply | None = None
        self._progress: QProgressDialog | None = None
        self._manifest: dict = {}
        self._update_dir: Path | None = None
        self._new_path: Path | None = None
        self._manual = False
        # 下载使用独立 NAM：manifest 处理器绑定在本 NAM 的 finished 上，
        # 若共用会把 exe 下载完成信号也当 manifest 处理，readAll 读走数据导致写空文件。
        self._dl_nam = QNetworkAccessManager(self)
        # 强制直连（NoProxy）：下载走阿里云国内服务器，避免用户全局代理
        # 劫持/中断 44MB 大文件（manifest 小文件能走国内路由，exe 却被代理挂起）。
        no_proxy = QNetworkProxy(QNetworkProxy.ProxyType.NoProxy)
        self.nam.setProxy(no_proxy)
        self._dl_nam.setProxy(no_proxy)

    # ---- 入口 ----
    def start(self, manual: bool = False) -> None:
        """检查服务器新版本。manual=True 为菜单手动检查：无新版/失败都给出提示。"""
        self._manual = manual
        _cleanup_stale_update_dir()
        _log(f"start: checking manifest manual={manual}")
        req = QNetworkRequest(QUrl(UPDATE_MANIFEST_URL))
        req.setTransferTimeout(10_000)
        self.nam.get(req)

    # ---- 检测 ----
    def _on_reply(self, reply: QNetworkReply) -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                _log(f"manifest error: {reply.error()}")
                if self._manual:
                    QMessageBox.warning(None, "检查更新", "无法连接更新服务器，请稍后重试。")
                return
            raw = bytes(reply.readAll())
            _log(f"manifest received: {len(raw)} bytes")
            data = load_manifest(raw.decode("utf-8", errors="replace"))
            if not data:
                _log("manifest parse failed")
                if self._manual:
                    QMessageBox.warning(None, "检查更新", "更新清单解析失败，请稍后重试。")
                return
            if not is_newer(data.get("version", ""), VERSION):
                _log(f"no newer version: server={data.get('version')} local={VERSION}")
                if self._manual:
                    QMessageBox.information(None, "检查更新", f"当前已是最新版本 v{VERSION}。")
                return
            self._manifest = data
            _log(f"new version found: {data.get('version')}")
            self._prompt_update(data)
        finally:
            reply.deleteLater()

    def _prompt_update(self, data: dict) -> None:
        new_ver = str(data.get("version", ""))
        from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                                     QScrollArea, QVBoxLayout)
        dlg = QDialog()
        dlg.setWindowTitle("发现新版本")
        dlg.setMinimumSize(480, 360)
        lay = QVBoxLayout(dlg)
        top = QLabel(f"<b>EasyBXb 有新版本可用：v{VERSION} → v{new_ver}</b>")
        top.setWordWrap(True)
        lay.addWidget(top)

        changelog = _format_changelog(data)
        if changelog:
            note = QLabel(f"本次更新内容（共 {len(data.get('changelog') or [])} 个版本，最新在最上方）：")
            lay.addWidget(note)
            body = QLabel(changelog)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setWordWrap(True)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            inner = QWidget()
            ilay = QVBoxLayout(inner)
            ilay.addWidget(body)
            ilay.addStretch(1)
            scroll.setWidget(inner)
            lay.addWidget(scroll, 1)

        box = QDialogButtonBox()
        upd = box.addButton("立即更新", QDialogButtonBox.ButtonRole.AcceptRole)
        later = box.addButton("稍后", QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        dlg.exec()
        if box.clickedButton() is upd:
            _log("user chose: update now")
            self._start_download(data)
        else:
            _log("user chose: later")

    # ---- 下载 ----
    def _start_download(self, data: dict) -> None:
        url = str(data.get("url") or UPDATE_DOWNLOAD_URL)
        _log(f"start download: {url}")
        self._update_dir = Path(tempfile.gettempdir()) / UPDATE_DIR_NAME
        self._update_dir.mkdir(parents=True, exist_ok=True)
        self._new_path = self._update_dir / NEW_EXE_NAME
        try:
            if self._new_path.exists():
                self._new_path.unlink()
        except OSError:
            pass

        self._progress = QProgressDialog("正在下载新版本…", "取消", 0, 100)
        self._progress.setWindowTitle("更新 EasyBXb")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(300)
        self._progress.setAutoClose(True)
        self._progress.setAutoReset(True)
        self._progress.canceled.connect(self._cancel_download)

        req = QNetworkRequest(QUrl(url))
        req.setTransferTimeout(120_000)
        reply = self._dl_nam.get(req)
        reply.downloadProgress.connect(self._on_progress)
        self._download_reply = reply
        reply.finished.connect(self._on_download_done)

    def _cancel_download(self) -> None:
        _log("download cancelled by user")
        if self._download_reply is not None:
            self._download_reply.abort()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0 and self._progress is not None:
            self._progress.setMaximum(total)
            self._progress.setValue(done)
        _log(f"progress: {done}/{total}")

    def _on_download_done(self) -> None:
        reply = self._download_reply
        self._download_reply = None
        if self._progress is not None:
            self._progress.close()
            self._progress = None
        if reply is None:
            _log("download done: reply is None")
            return
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                _log(f"download error: {reply.error()}")
                QMessageBox.warning(None, "更新失败", "下载新版本失败，请稍后重试。")
                return
            data = bytes(reply.readAll())
            _log(f"download done: {len(data)} bytes")
            if not self._new_path or not self._write_downloaded(data):
                return
            _log(f"written to {self._new_path}")
            if not self._verify_md5(self._manifest.get("md5", "")):
                _log("md5 mismatch, update cancelled")
                QMessageBox.warning(None, "更新失败", "新版本校验未通过，已取消更新。")
                return
            _log("md5 ok, prompting apply")
            self._prompt_apply()
        finally:
            reply.deleteLater()

    def _write_downloaded(self, data: bytes) -> bool:
        try:
            self._new_path.write_bytes(data)
            return True
        except OSError as exc:
            _log(f"write failed: {exc!r}")
            QMessageBox.warning(None, "更新失败", "写入临时文件失败，请稍后重试。")
            return False

    @staticmethod
    def _verify_md5(expected: str) -> bool:
        if not expected:
            return True  # 清单未提供 MD5：跳过校验
        actual = hashlib.md5(Path(
            Path(tempfile.gettempdir()) / UPDATE_DIR_NAME / NEW_EXE_NAME
        ).read_bytes()).hexdigest().lower()
        return actual == str(expected).strip().lower()

    # ---- 应用更新 ----
    def _prompt_apply(self) -> None:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("准备更新")
        box.setText("新版本下载完成，即将关闭客户端进行更新。")
        box.setInformativeText("更新过程中请勿关闭本窗口。更新完成后客户端会自动重新启动。")
        ok = box.addButton("开始更新", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(ok)
        box.exec()
        if box.clickedButton() is not ok:
            _log("user cancelled apply")
            return
        self._launch_and_quit()

    def _launch_and_quit(self) -> None:
        if not self._new_path:
            return
        target = Path(sys.executable).resolve()
        args = [str(self._new_path), "--update", str(self._new_path), str(target)]
        _log(f"launch updater: {args!r}")
        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(args, close_fds=True, creationflags=flags)
        except OSError:
            QMessageBox.warning(None, "更新失败", "无法启动更新程序，请稍后重试。")
            return
        # 主程序退出，让更新器接管替换
        self.app.quit()


def frozen() -> bool:
    """是否打包运行（sys.frozen 为 PyInstaller 标记）。"""
    return bool(getattr(sys, "frozen", False))