# A3 Telnet / MXP 握手（定稿）

## Telnet 基础
- 用 Telnet IAC 状态机解析（见 C1 字节常量，同协议层复用）。

## MXP 能力探测
- 服务器可能做 MXP 探测：客户端收到 `\x1b[1z<SUPPORT>` 时回 `<SUPPORT>` 表示已启用 MXP。
- 若探测 SDRAW 有超时，稍等后回发送回车兜底，保证握手不卡死。

## MXP 定位
- MXP 是"服务器在文本流嵌入标记"的协议，可支持 `<IMG>`/超链接等（若服务器用）。
- QQ 图片走 GMCP.Message，与 MXP 无关（见 C-GMCP 与「输出窗口」）。

## 验收
- [ ] 正确处理 MXP 探测握手，不卡死
- [ ] 接收 ANSI 颜色的同时兼容 MXP 标记解析