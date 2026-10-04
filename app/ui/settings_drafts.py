"""设置编辑器的内存草稿；不读写数据库或文件。"""


class SettingsDrafts:
    def __init__(self):
        self._saved: dict[str, str] = {}
        self._pending: dict[str, str] = {}

    def load(self, key: str, saved: str) -> str:
        self._saved[key] = saved
        if self._pending.get(key) == saved:
            self._pending.pop(key, None)
        return self._pending.get(key, saved)

    def edit(self, key: str, text: str) -> None:
        if text == self._saved.get(key, ""):
            self._pending.pop(key, None)
        else:
            self._pending[key] = text

    def accept(self, key: str, saved: str) -> None:
        self._saved[key] = saved
        self._pending.pop(key, None)

    def dirty(self, key: str) -> bool:
        return key in self._pending
