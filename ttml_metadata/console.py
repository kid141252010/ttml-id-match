from __future__ import annotations

import os
import sys
from typing import Any

_COLOR_CODES = {
    "dry-run": "94",      # light blue
    "updated": "92",      # light green
    "normalized": "96",   # light cyan
    "unchanged": "90",    # dark gray
    "error": "91",        # light red
    "skip": "93",         # light yellow
    "info": "36",         # cyan
    "header": "1;35",     # bold magenta
    "prompt": "1;33",     # bold yellow
    "highlight": "1;32",  # bold green
    "warning": "33",      # yellow
    "reset": "0"
}

def _supports_color() -> bool:
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        if "ANSICON" in os.environ or "WT_SESSION" in os.environ or os.environ.get("TERM_PROGRAM") == "vscode":
            return True
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            hStdOut = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(hStdOut, ctypes.byref(mode)):
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                if kernel32.SetConsoleMode(hStdOut, mode.value | 0x0004):
                    return True
        except Exception:
            pass
        return False
    return True

def _color_text(text: str, color_key: str) -> str:
    if not _supports_color():
        return text
    code = _COLOR_CODES.get(color_key, "0")
    return f"\033[{code}m{text}\033[0m"

def _safe_print(*values: Any, file: Any = None, **kwargs: Any) -> None:
    stream = file or sys.stdout
    try:
        print(*values, file=stream, **kwargs)
    except UnicodeEncodeError:
        text = kwargs.get("sep", " ").join(str(value) for value in values)
        end = kwargs.get("end", "\n")
        encoded = text.encode(getattr(stream, "encoding", None) or "utf-8", "backslashreplace").decode(
            getattr(stream, "encoding", None) or "utf-8"
        )
        stream.write(encoded + end)
