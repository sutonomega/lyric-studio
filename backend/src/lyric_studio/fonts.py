"""Japanese font families available to the Windows preview and subtitle renderer."""

import ctypes
from ctypes import wintypes
from functools import lru_cache
import sys


class LogFont(ctypes.Structure):
    _fields_ = [
        ("height", wintypes.LONG), ("width", wintypes.LONG), ("escapement", wintypes.LONG),
        ("orientation", wintypes.LONG), ("weight", wintypes.LONG),
        ("italic", wintypes.BYTE), ("underline", wintypes.BYTE), ("strikeout", wintypes.BYTE),
        ("charset", wintypes.BYTE), ("out_precision", wintypes.BYTE), ("clip_precision", wintypes.BYTE),
        ("quality", wintypes.BYTE), ("pitch_and_family", wintypes.BYTE),
        ("family", wintypes.WCHAR * 32),
    ]


@lru_cache(maxsize=1)
def japanese_fonts():
    if sys.platform != "win32":
        return ()
    gdi = ctypes.WinDLL("gdi32")
    user = ctypes.WinDLL("user32")
    callback = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(LogFont), ctypes.c_void_p,
                                wintypes.DWORD, wintypes.LPARAM)
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi.EnumFontFamiliesExW.argtypes = [
        wintypes.HDC, ctypes.POINTER(LogFont), callback, wintypes.LPARAM, wintypes.DWORD,
    ]
    names = set()

    @callback
    def collect(font, _metrics, _kind, _param):
        name = font.contents.family
        if name and not name.startswith("@") and "," not in name:
            names.add(name)
        return 1

    dc = user.GetDC(None)
    if dc:
        try:
            query = LogFont()
            query.charset = 128  # SHIFTJIS_CHARSET
            gdi.EnumFontFamiliesExW(dc, ctypes.byref(query), collect, 0, 0)
        finally:
            user.ReleaseDC(None, dc)
    return tuple(sorted(names, key=str.casefold))
