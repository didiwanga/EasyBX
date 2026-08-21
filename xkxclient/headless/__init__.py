"""无头守护进程（headless）：在服务器后台运行多个账号的自动化引擎，本地 HTTP 远程控制。

复用桌面的 AccountSession（连接/登录/触发器/宏/定时器/别名）与全部自动化引擎，
只不创建任何 GUI。事件经 EventBus 落入 Qt 事件循环，控制请求经 QtBridge 排入
Qt 线程执行，保证线程安全。
"""