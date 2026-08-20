from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from xkxclient.ui.mapcanvas import LocalMapWidget


class MapDock(QWidget):
    """地图面板（地图系统.md）：本地拓扑矢量画布 + 寻路(walk) + 当前房间。"""

    def __init__(self, session=None, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.cache = getattr(session, "map_cache", None) if session else None
        self.map_view = LocalMapWidget(self.cache)
        self._plan_route: list[str] | None = None

        self.cur_label = QLabel("当前位置: -")
        self.target = QLineEdit()
        self.target.setPlaceholderText("目标房间名…")
        self.plan_btn = QPushButton("寻路")
        self.go_btn = QPushButton("行走")
        self.go_btn.setEnabled(False)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.refresh_btn = QPushButton("刷新位置")
        self.plan_btn.clicked.connect(self._on_plan)
        self.go_btn.clicked.connect(self._on_go)
        self.stop_btn.clicked.connect(self._on_stop)
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.target.returnPressed.connect(self._on_plan)
        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("步速"))
        self.step_slider = QSlider(Qt.Orientation.Horizontal)
        self.step_slider.setRange(8, 30)            # 0.8s ~ 3.0s
        self.step_slider.setValue(15)               # 1.5s 默认
        self.step_slider.valueChanged.connect(self._on_step)
        self.step_label = QLabel("1.5s")
        step_row.addWidget(self.step_slider, 1)
        step_row.addWidget(self.step_label)

        top = QHBoxLayout()
        top.addWidget(self.cur_label, 1)
        top.addWidget(self.target, 1)
        top.addWidget(self.plan_btn)
        top.addWidget(self.go_btn)
        top.addWidget(self.stop_btn)
        top.addWidget(self.refresh_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.path_label)
        lay.addWidget(self.map_view, 1)
        lay.addLayout(step_row)

    def bind(self, session) -> None:
        self.session = session
        self.cache = getattr(session, "map_cache", None)
        self.map_view._cache = self.cache
        if self.cache is not None:
            self.map_view.reload()

    def _on_step(self, v: int) -> None:
        sec = v / 10.0
        self.step_label.setText(f"{sec:.1f}s")
        if self.session is not None and self.session.navigator is not None:
            self.session.navigator.config_step_ms(int(sec * 1000))

    def _sync_current(self) -> None:
        """寻路前用 session 已确认的最新房间名校准本地当前位置。

        GMCP.Move 的房间短名可能是父/子房间（如百年堂短名=庄家胡同），
        look 解析才是权威房间名；两者乱序时本地缓存可能落后，寻路起点会错。
        """
        if self.session is None or self.cache is None:
            return
        rn = str(getattr(self.session, "room_name", "") or "").strip()
        if rn and rn != str(self.cache.current or "").strip():
            self.cache.set_current_name(rn)

    def _on_plan(self) -> None:
        """寻路：只列出路径，是否行走由用户点「行走」决定。"""
        if self.session is None:
            return
        self._sync_current()
        target = self.target.text().strip()
        if not target:
            return
        route: list[str] | None = None
        if self.cache is not None:
            cands = self.cache.find_targets(target)
            if len(cands) == 1:
                route = cands[0][2]
            elif len(cands) > 1:
                route = self._choose_target(target, cands)
        if route is None:
            route = self._find_route(target)  # 本地无路/被取消 -> 服务端兜底
        self._plan_route = route
        self.go_btn.setEnabled(False)
        if route is None:
            self.cur_label.setText(f"无路可走: {target}")
            self.path_label.setText("")
            return
        if not route:
            self.path_label.setText(f"已在目标房间: {target}")
        else:
            start_name = self.cache.current or "当前位置"
            self.path_label.setText(
                f"{start_name} → {target}（{len(route)} 步）: " + " → ".join(route))
        self.cur_label.setText(f"已规划 → {target}（{len(route)} 步）")
        self.go_btn.setEnabled(True)

    def _on_go(self) -> None:
        """开始行走已规划的路径。"""
        if self.session is None or not self._plan_route:
            return
        nav = self.session.navigator
        if nav is None:
            return
        self.go_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        nav.start(self._plan_route)

    def _on_refresh(self) -> None:
        """刷新当前位置：发 look 让采集器回写当前位置，并重绘地图。"""
        if self.session is not None and getattr(self.session, "logged_in", False):
            self.session.send("look")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(600, self._refresh_cur)

    def _refresh_cur(self) -> None:
        name = self.cache.current if self.cache is not None else ""
        self.cur_label.setText(f"当前位置: {name or '-'}")
        if self.cache is not None:
            self.map_view.reload()

    def _choose_target(self, name: str, cands) -> list[str] | None:
        items = []
        for i, (_nid, dist, path, reachable) in enumerate(cands, 1):
            if not reachable:
                tag = "数据不可达"
            elif dist == 0:
                tag = "当前所在"
            else:
                tag = f"距离 {int(dist)} 步"
            if path:
                tag += f" · 首步 {path[0]}"
            items.append(f"#{i} {tag} · {name}")
        chosen, ok = QInputDialog.getItem(
            self, "选择目的地",
            f"同名房间 {len(cands)} 个（可达优先，按距离由近到远）：",
            items, 0, False)
        if not ok or chosen is None:
            return None
        return cands[items.index(chosen)][2]

    def _find_route(self, target: str) -> list[str] | None:
        route = self.cache.route(target) if self.cache is not None else None
        if route is not None:
            return route
        # 服务端补充路由
        from xkxclient.core.config import ConfigManager
        base = ConfigManager.instance().get("map.api", "")
        if base and self.cache is not None:
            try:
                import urllib.request
                # 地图 API 公网直连，不走系统代理（与 fullme 一致，避免 DNS/代理干扰）
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}))
                rid_from = self.cache.current or ""
                url = f"{base}/route?from={rid_from}&to={target}"
                with opener.open(url, timeout=3) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                route = data.get("route") if isinstance(data, dict) else None
                if route:
                    return [d for d in route if d]
            except Exception:
                return None
        return None

    def _on_stop(self) -> None:
        if self.session is not None and self.session.navigator is not None:
            self.session.navigator.stop()
        self.stop_btn.setEnabled(False)
        self.go_btn.setEnabled(bool(self._plan_route))

    def on_nav_state(self, payload: dict) -> None:
        ev = payload.get("event", "")
        account = payload.get("account")
        if account is not None and self.session is not None and account != self.session.account_id:
            return
        if ev == "nav.start":
            self.go_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        elif ev == "nav.arrived":
            self.cur_label.setText("已到达")
            self.stop_btn.setEnabled(False)
            self.go_btn.setEnabled(bool(self._plan_route))
            if self.cache is not None:
                self.map_view.reload()
        elif ev in ("nav.stuck", "nav.stopped"):
            self.cur_label.setText(f"停止: {payload.get('reason', '')}")
            self.stop_btn.setEnabled(False)
            self.go_btn.setEnabled(bool(self._plan_route))
        elif ev == "nav.step":
            if self.cache is not None:
                self.map_view.reload()