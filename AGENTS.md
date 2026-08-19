# AGENTS.md

## Shell/脚本约定

PowerShell 对代码中的中文、特殊字符、转义解析经常出错（多次出现嵌套引号/here-string/中文乱码问题）。

- 能用 Python 脚本完成的事情，尽可能用 Python 做（例如写一个临时 .py 脚本，或用 `python -c` 处理编码/转义/文本处理）。
- 对服务器部署、文件生成、JSON 处理、中文文本操作，一律优先 Python。
- 只有确实需要 PowerShell 的情况下才用它（例如调用 Windows 原生命令、进程管理、管道/环境变量需要 PowerShell 语义时）。