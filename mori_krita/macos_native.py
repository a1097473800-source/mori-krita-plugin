"""Small C ABI to macOS WebKit and Keychain; uses the Qt GUI thread."""
import ctypes
from functools import lru_cache
from pathlib import Path
import sys

CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_char_p)


@lru_cache(maxsize=1)
def native_library():
    if sys.platform != "darwin":
        raise OSError("macOS native services are unavailable")
    library = ctypes.CDLL(str(Path(__file__).with_name("macos") / "libMoriMac.dylib"))
    signatures = {
        "mori_mac_abi": ([], ctypes.c_int),
        "mori_web_create": ([ctypes.c_void_p, ctypes.c_char_p, CALLBACK], ctypes.c_void_p),
        "mori_web_visible": ([ctypes.c_void_p, ctypes.c_int], None),
        "mori_web_resize": ([ctypes.c_void_p], None),
        "mori_web_focus": ([ctypes.c_void_p], None),
        "mori_web_close": ([ctypes.c_void_p], None),
        "mori_keychain_get": ([ctypes.c_char_p], ctypes.c_void_p),
        "mori_keychain_set": ([ctypes.c_char_p, ctypes.c_char_p], ctypes.c_int),
        "mori_keychain_delete": ([ctypes.c_char_p], None),
        "mori_mac_free": ([ctypes.c_void_p], None),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    if library.mori_mac_abi() != 1:
        raise OSError("Mori macOS host version mismatch")
    return library


class MacWebHost:
    def __init__(self, parent_id, url, notify):
        self.library = native_library()
        self.handle = None
        self._closed = False

        def received(kind, message):
            if not self._closed:
                notify(kind.decode("utf-8", "replace"), message.decode("utf-8", "replace"))

        # Retain the ctypes callback until WebKit has detached all delegates.
        self.callback = CALLBACK(received)
        self.handle = self.library.mori_web_create(parent_id, url.encode("utf-8"), self.callback)
        if not self.handle:
            self._closed = True
            raise OSError("无法创建 Mac 画布，请确认使用完整的 Mac 插件包。")

    def set_visible(self, visible):
        if self.handle:
            self.library.mori_web_visible(self.handle, int(bool(visible)))

    def resize(self):
        if self.handle:
            self.library.mori_web_resize(self.handle)

    def focus(self):
        if self.handle:
            self.library.mori_web_focus(self.handle)

    def close(self):
        self._closed = True
        handle, self.handle = self.handle, None
        if handle:
            self.library.mori_web_close(handle)


def keychain_read(origin):
    library = native_library()
    pointer = library.mori_keychain_get(origin.encode("utf-8"))
    if not pointer:
        return None
    try:
        return ctypes.string_at(pointer)
    finally:
        library.mori_mac_free(pointer)


def keychain_write(origin, payload):
    if not isinstance(payload, bytes) or len(payload) > 65536 or b"\0" in payload:
        return False
    return bool(native_library().mori_keychain_set(origin.encode("utf-8"), payload))


def keychain_delete(origin):
    native_library().mori_keychain_delete(origin.encode("utf-8"))
