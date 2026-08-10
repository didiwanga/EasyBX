# EasyBXb

PyQt6 桌面 MUD 客户端（北大侠客行 xkx）。架构与功能以 `wiki/` 下设计文档为准（`设计索引.md` 为索引）。

## 目录结构

```
main.py               入口（E8-启动流程）
xkxclient/
  app.py              核心装配 XkxApp（总线 + 配置 + 账号会话）
  core/               基础件：EventBus / ConfigManager / 资源
  net/                连接 / 编码 / Telnet握手 / GMCP
  automation/         触发器 / 别名 / 定时器 / 宏
  parse/              look 解析层
  ui/                 登录窗 / 主窗口 / 输出 / 状态栏 / 标签页 / 地图
lua/                  Lua 脚本目录
materials/            图标与地图等资源（app.ico / worldmap.png）
wiki/                 设计文档
```

## 运行

```bash
py -m venv .venv                 # 首次
.venv\Scripts\python.exe -m pip install -r requirements.txt   # 首次
.venv\Scripts\python.exe main.py
```

Windows 也可直接双击 `run.bat`。

## 配置存储

`%APPDATA%\XkxClient\`：全局 `config.json`/`layout.json` + `accounts\<id>\`（详见 wiki `E8-配置管理.md`）。