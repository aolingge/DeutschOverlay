"""One settings window per Windows login session."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtNetwork import QLocalServer, QLocalSocket


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    def __init__(self, name: str, on_second_launch: Callable[[], None]) -> None:
        self.name = name
        self.on_second_launch = on_second_launch
        self.server = QLocalServer()
        self._mutex = None
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        mutex = _kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
        if not mutex:
            return False
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(mutex)
            return False
        self._mutex = mutex
        if not self.server.listen(self.name):
            self.close()
            return False
        return True

    def notify_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        connected = socket.waitForConnected(1000)
        if connected:
            socket.disconnectFromServer()
        socket.close()
        return connected

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
            self.on_second_launch()

    def close(self) -> None:
        self.server.close()
        if self._mutex is not None:
            _kernel32.CloseHandle(self._mutex)
            self._mutex = None
