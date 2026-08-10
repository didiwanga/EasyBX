from __future__ import annotations

from xkxclient.core.config import json_read, json_write


class HistoryStore:
    """B6 命令历史（本地文件持久化，每账号列表，去重保留最新）。"""

    def __init__(self, account_id: str, root=None) -> None:
        import os
        from pathlib import Path
        self.account = account_id
        base = root or os.environ.get("APPDATA", str(Path.home()))
        self.root = Path(base) / "XkxClient"
        self.path = self.root / "history.json"
        self._data: dict[str, list[str]] = json_read(self.path)
        self.limit = 200
        self._cursor = len(self.peek())

    def _list(self) -> list[str]:
        lst = self._data.get(self.account)
        if lst is None:
            self._data[self.account] = []
            lst = self._data[self.account]
        return lst

    def peek(self) -> list[str]:
        return list(self._list())

    def record(self, text: str) -> None:
        lst = self._list()
        if text in lst:
            lst.remove(text)
        lst.append(text)
        del lst[:-self.limit]
        self._cursor = len(lst)
        json_write(self.path, self._data)

    def back(self) -> str:
        lst = self._list()
        if not lst:
            return ""
        if self._cursor is None:
            self._cursor = len(lst)
        self._cursor = max(0, self._cursor - 1)
        return lst[self._cursor]

    def forward(self) -> str:
        lst = self._list()
        if not lst:
            return ""
        if self._cursor is None:
            return ""
        self._cursor = min(len(lst), self._cursor + 1)
        if self._cursor >= len(lst):
            return ""
        return lst[self._cursor]

    def reset_cursor(self) -> None:
        self._cursor = len(self._list())