from __future__ import annotations

import sys
from typing import Any

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
