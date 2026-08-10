from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from xkxclient.core.config import ConfigManager

# 全局主题（wiki E8-主题.md）。每套是一组颜色令牌，build_qss 统一生成样式表。
# 令牌：
#   base       窗口 / 顶栏背景
#   panel      工具条 / 标签 / Dock / 菜单栏背景
#   raised     菜单、弹出层背景
#   input      QLineEdit / 列表框/输入背景
#   text       主文字
#   text_dim   次要文字 / 状态
#   border     边框
#   border2    悬浮 / 焦点边框
#   accent     强调色（焦点边框、按钮悬浮、选区）
#   btn        普通按钮背景
#   btn_hover  按钮悬浮
#   btn_down   按钮按下
#   btn_text   按钮文字
#   btn_border 按钮边框
#   tip_t      提示文字

DEFAULT_THEME = "night"

PALETTES: dict[str, dict[str, str]] = {
    "night": {
        "name": "暗夜",
        "base": "#232526",
        "panel": "#2c2f31",
        "raised": "#26282a",
        "field": "#1a1c1e",
        "text": "#e0e0e0",
        "text_dim": "#9aa0a3",
        "border": "#45494b",
        "border2": "#6a6f72",
        "accent": "#5a7ad1",
        "btn": "#35383a",
        "btn_hover": "#44474a",
        "btn_down": "#2a2d2f",
        "btn_text": "#e8e8e8",
        "btn_border": "#4a4e50",
    },
    "nightblue": {
        "name": "暗夜蓝",
        "base": "#1e2430",
        "panel": "#262e3d",
        "raised": "#232a38",
        "field": "#151a24",
        "text": "#dbe3f0",
        "text_dim": "#8a94a8",
        "border": "#39414f",
        "border2": "#4f5a6b",
        "accent": "#4a8dff",
        "focus": "#4a8dff",
        "btn": "#2e3746",
        "btn_hover": "#3a4556",
        "btn_down": "#242c39",
        "btn_text": "#e6ecf7",
        "btn_border": "#3f4a5c",
    },
    "graphite": {
        "name": "石墨灰",
        "base": "#2a2a2a",
        "panel": "#333333",
        "raised": "#2e2e2e",
        "field": "#222222",
        "text": "#e8e8e8",
        "text_dim": "#a0a0a0",
        "border": "#4d4d4d",
        "border2": "#6f6f6f",
        "accent": "#c9a15c",
        "focus": "#c9a15c",
        "btn": "#3a3a3a",
        "btn_hover": "#484848",
        "btn_down": "#2f2f2f",
        "btn_text": "#ececec",
        "btn_border": "#525252",
    },
    "emerald": {
        "name": "墨绿",
        "base": "#1f2825",
        "panel": "#29352f",
        "raised": "#242e29",
        "field": "#18201c",
        "text": "#d6e3d9",
        "text_dim": "#8fa295",
        "border": "#3d4a42",
        "border2": "#617568",
        "accent": "#6fbf9c",
        "focus": "#6fbf9c",
        "btn": "#314038",
        "btn_hover": "#3d4f45",
        "btn_down": "#29372f",
        "btn_text": "#e6f0e8",
        "btn_border": "#46574b",
    },
    "warm": {
        "name": "暖棕",
        "base": "#2b2621",
        "panel": "#36302a",
        "raised": "#302a24",
        "field": "#221d18",
        "text": "#e5dcd0",
        "text_dim": "#a79b8b",
        "border": "#4c443b",
        "border2": "#6e6353",
        "accent": "#e0a458",
        "focus": "#e0a458",
        "btn": "#413a32",
        "btn_hover": "#504740",
        "btn_down": "#352f28",
        "btn_text": "#efe6d8",
        "btn_border": "#554c42"
    },
    "solarized_light": {
        "name": "浅色纸感",
        "base": "#eee8d5",
        "panel": "#f5f0e1",
        "raised": "#faf6ea",
        "field": "#ffffff",
        "text": "#42535f",
        "text_dim": "#7a8a96",
        "border": "#cfc7b4",
        "border2": "#a89f88",
        "accent": "#268bd2",
        "focus": "#268bd2",
        "btn": "#e8e2d0",
        "btn_hover": "#dcd5bf",
        "btn_down": "#d0c8b0",
        "btn_text": "#2b3a44",
        "btn_border": "#bcb396",
    },
    "light": {
        "name": "浅色",
        "base": "#f2f2f4",
        "panel": "#ffffff",
        "raised": "#fafafc",
        "field": "#ffffff",
        "text": "#26282c",
        "text_dim": "#74777e",
        "border": "#d5d7dc",
        "border2": "#a9adb5",
        "accent": "#3b74e0",
        "focus": "#3b74e0",
        "btn": "#e8eaee",
        "btn_hover": "#dfe2e8",
        "btn_down": "#d2d5dd",
        "btn_text": "#1f2226",
        "btn_border": "#c3c6cc",
    },
}


def build_qss(p: dict[str, str]) -> str:
    g = p.get
    base = g("base", "#232526")
    panel = g("panel", "#2c2f31")
    raised = g("raised", "#26282a")
    field = g("field", "#1a1c1e")
    text = g("text", "#e0e0e0")
    dim = g("text_dim", "#9aa0a3")
    border = g("border", "#45494b")
    border2 = g("border2", "#6a6f72")
    accent = g("accent", "#5a7ad1")
    focus = g("focus", accent)
    btn = g("btn", "#35383a")
    btn_hover = g("btn_hover", "#44474a")
    btn_down = g("btn_down", "#2a2d2f")
    btn_text = g("btn_text", "#e8e8e8")
    btn_border = g("btn_border", "#4a4e50")
    return f"""
QMainWindow {{ background: {base}; color: {text}; }}
QPlainTextEdit {{ background: {field}; color: {text}; border: none; }}
QToolBar {{ background: {panel}; border: none; padding: 2px; spacing: 3px; }}
QToolBar::separator {{ background: {border2}; width: 1px; margin: 4px 2px; }}
QToolButton {{
    background: {btn}; color: {btn_text}; border: 1px solid {btn_border};
    border-radius: 5px; padding: 4px 10px; }}
QToolButton:hover {{ background: {btn_hover}; border-color: {border2}; }}
QToolButton:pressed {{ background: {btn_down}; }}
QPushButton {{
    background: {btn}; color: {btn_text}; border: 1px solid {btn_border};
    border-radius: 5px; padding: 4px 10px; }}
QPushButton:hover {{ background: {btn_hover}; border-color: {border2}; }}
QPushButton:pressed {{ background: {btn_down}; }}
QPushButton:disabled {{ color: {dim}; background: {panel}; border-color: {border}; }}
QPushButton:focus {{ outline: none; }}
QPushButton[dirBtn="true"] {{ background: {btn_hover}; border: 1px solid {accent}; color: {btn_text}; }}
QPushButton[dirBtn="true"]:disabled {{ background: {panel}; border: 1px solid {border}; color: {dim}; }}
QLineEdit {{
    background: {field}; color: {text}; border: 1px solid {border};
    border-radius: 5px; padding: 4px 8px; }}
QLineEdit:focus {{ border-color: {focus}; }}
QToolTip {{ background: {raised}; color: {text}; border: 1px solid {border2}; }}
QDockWidget {{ color: {dim}; }}
QDockWidget::title {{ background: {panel}; padding: 4px; color: {text}; }}
QTabWidget::pane {{ background: {base}; border: 1px solid {border}; }}
QTabWidget QTabBar {{ background: {panel}; }}
QSplitter {{ background: {base}; }}
QStatusBar {{ background: {panel}; color: {dim}; }}
QMenu {{ background: {raised}; color: {text}; border: 1px solid {border}; }}
QMenu::item {{ padding: 4px 18px; }}
QMenu::item:selected {{ background: {btn_hover}; }}
QMenuBar {{ background: {base}; color: {text}; }}
QMenuBar::item:selected {{ background: {btn_hover}; }}
QTabBar::tab {{ background: {panel}; color: {dim}; padding: 5px 12px;
    border: 1px solid {border}; border-bottom: none; border-top-left-radius: 5px;
    border-top-right-radius: 5px; }}
QTabBar::tab:selected {{ background: {field}; color: {text}; }}
QGroupBox {{ border: 1px solid {border}; border-radius: 5px; margin-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {text}; }}
QComboBox {{ background: {field}; color: {text}; border: 1px solid {border};
    border-radius: 5px; padding: 3px 8px; }}
QComboBox QAbstractItemView {{ background: {raised}; color: {text};
    selection-background-color: {btn_hover}; }}
QSpinBox {{ background: {field}; color: {text}; border: 1px solid {border};
    border-radius: 5px; padding: 2px 6px; }}
QScrollBar:vertical {{ background: {base}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {border2}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: {base}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {border2}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ background: none; border: none; height: 0; width: 0; }}
QCheckBox {{ color: {text}; spacing: 6px; }}
QRadioButton {{ color: {text}; spacing: 6px; }}
QLabel {{ color: {text}; }}
QListWidget {{ background: {field}; color: {text}; border: 1px solid {border};
    border-radius: 5px; }}
QListWidget::item {{ padding: 3px 6px; }}
QListWidget::item:selected {{ background: {btn_hover}; }}
QTreeWidget {{ background: {field}; color: {text}; border: 1px solid {border}; }}
QTreeWidget::item:selected {{ background: {btn_hover}; }}
QTableWidget {{ background: {field}; color: {text}; border: 1px solid {border}; }}
QTableWidget::item:selected {{ background: {btn_hover}; }}
QHeaderView::section {{ background: {panel}; color: {text}; border: 1px solid {border}; }}
QTextEdit {{ background: {field}; color: {text}; border: 1px solid {border}; }}
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:hover {{ background: {accent}; }}
QDialog {{ background: {base}; color: {text}; }}
QMessageBox {{ background: {base}; color: {text}; }}
QProgressBar {{ background: {field}; border: 1px solid {border}; border-radius: 3px; }}
QProgressBar::chunk {{ background: {accent}; }}
"""


def _build_palette(p: dict[str, str]) -> "QPalette":
    """把颜色令牌同时写进 QPalette，保证未被 QSS 覆盖的控件也随主题变色。"""
    from PyQt6.QtGui import QColor, QPalette

    pal = QPalette()
    text = QColor(p.get("text", "#e0e0e0"))
    dim = QColor(p.get("text_dim", "#9aa0a3"))
    base = QColor(p.get("base", "#232526"))
    panel = QColor(p.get("panel", "#2c2f31"))
    field = QColor(p.get("field", "#1a1c1e"))
    raised = QColor(p.get("raised", "#26282a"))
    hover = QColor(p.get("btn_hover", "#44474a"))
    btn = QColor(p.get("btn", "#35383a"))

    pal.setColor(QPalette.ColorRole.Window, base)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, field)
    pal.setColor(QPalette.ColorRole.AlternateBase, panel)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.PlaceholderText, dim)
    pal.setColor(QPalette.ColorRole.Button, btn)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.Highlight, hover)
    pal.setColor(QPalette.ColorRole.HighlightedText, base)
    pal.setColor(QPalette.ColorRole.ToolTipBase, raised)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Link, QColor(p.get("accent", "#5a7ad1")))
    for state in (QPalette.ColorGroup.Disabled,):
        pal.setColor(state, QPalette.ColorRole.WindowText, dim)
        pal.setColor(state, QPalette.ColorRole.Text, dim)
        pal.setColor(state, QPalette.ColorRole.ButtonText, dim)
    return pal


def apply(theme_name: str | None = None) -> str:
    """把主题应用到整个 QApplication（QSS + QPalette），返回实际生效的主题名。"""
    if theme_name is None:
        theme_name = ConfigManager.instance().get("theme", DEFAULT_THEME)
    if theme_name not in PALETTES:
        theme_name = DEFAULT_THEME
    app = QApplication.instance()
    if app is not None:
        app.setPalette(_build_palette(PALETTES[theme_name]))
        app.setStyleSheet(build_qss(PALETTES[theme_name]))
    return theme_name