import json
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class JsonlLogger:
    def __init__(self, file_path: str, component: str) -> None:
        self.file_path = file_path
        self.component = component
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        self._lock = threading.Lock()
        self._hostname = socket.gethostname()
        self._pid = os.getpid()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def log(self, event: str, data: Optional[Dict[str, Any]] = None, level: str = "INFO", **extra: Any) -> None:
        record = {
            "ts": self._now_iso(),
            "level": level,
            "component": self.component,
            "event": event,
            "data": data or {},
            "pid": self._pid,
            "host": self._hostname,
            **extra,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()


def get_logger(component: str, log_dir: str = "./logs", filename: Optional[str] = None) -> JsonlLogger:
    if filename is None:
        safe_name = component.replace("/", "-")
        filename = f"{safe_name}.jsonl"
    return JsonlLogger(os.path.join(log_dir, filename), component)
