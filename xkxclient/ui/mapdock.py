from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
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

        self.cur_label = QLabel("当前位置: -")
        self.target = QLineEdit()
        self.target.setPlaceholderText("目标房间名…")
        self.walk_btn = QPushButton("寻路并走")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.walk_btn.clicked.connect(self._on_walk)
        self.stop_btn.clicked.connect(self._on_stop)
        self.target.returnPressed.connect(self._on_walk)

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
        top.addWidget(self.walk_btn)
        top.addWidget(self.stop_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
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

    def _on_walk(self) -> None:
        if self.session is None:
            return
        target = self.target.text().strip()
        if not target:
            return
        route = self._find_route(target)
        if not route:
            self.cur_label.setText(f"无路可走: {target}")
            return
        self.cur_label.setText(f"→ {target} ({len(route)} 步)")
        self.walk_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        nav = self.session.navigator
        if nav is not None:
            nav.start(route)

    def _find_route(self, target: str) -> list[str] | None:
        route = self.cache.route(target) if self.cache is not None else None
        if route is not None:
            return route
        # 服务端补充路由
        from xkxclient.core.config import ConfigManager
        base = ConfigManager.instance().get("map.api", "")
        if base and self.cache is not None:
            from xkxclient.core import gmcp
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
        self.walk_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_nav_state(self, payload: dict) -> None:
        ev = payload.get("event", "")
        account = payload.get("account")
        if account is not None and self.session is not None and account != self.session.account_id:
            return
        if ev == "nav.start":
            self.walk_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        elif ev == "nav.arrived":
            self.cur_label.setText("已到达")
            self.walk_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            if self.cache is not None:
                self.map_view.reload()
        elif ev in ("nav.stuck", "nav.stopped"):
            self.cur_label.setText(f"停止: {payload.get('reason', '')}")
            self.walk_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        elif ev == "nav.step":
            if self.cache is not None:
                self.map_view.reload()