from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

HALT_STATUS_CODES = {403, 412, 418, 429}


class PlatformHalted(RuntimeError):
    def __init__(self, platform: str, reason: str) -> None:
        super().__init__(reason)
        self.platform = platform
        self.reason = reason


@dataclass
class FetchResult:
    url: str
    status_code: int
    content: bytes
    text: str
    content_type: str


class PoliteClient:
    """公开 GET。本机 Python TLS 对番茄握手超时，改走系统 curl，不升级对抗。"""

    def __init__(self, platform: str, delay_seconds: float = 0.6, timeout: float = 40.0) -> None:
        self.platform = platform
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self._last_request_at = 0.0
        self._curl = shutil.which("curl")
        if not self._curl:
            raise RuntimeError("需要系统 curl 才能礼貌请求公开页")

    def close(self) -> None:
        return

    def get(self, url: str, referer: str | None = None, headers: dict[str, str] | None = None) -> FetchResult:
        self._wait()
        with tempfile.TemporaryDirectory() as temp_dir:
            body_path = Path(temp_dir) / "body"
            command = [
                self._curl,
                "-sS",
                "-L",
                "--max-redirs",
                "3",
                "--max-time",
                str(max(int(self.timeout), 1)),
                "-A",
                DEFAULT_USER_AGENT,
                "-o",
                str(body_path),
                "-w",
                "%{http_code}",
                "-D",
                str(Path(temp_dir) / "headers"),
            ]
            if referer:
                command.extend(["-e", referer])
            for key, value in (headers or {}).items():
                command.extend(["-H", f"{key}: {value}"])
            command.append(url)
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or f"curl 失败 {url}")
            status_code = int(completed.stdout.strip() or "0")
            content = body_path.read_bytes() if body_path.exists() else b""
            header_text = (Path(temp_dir) / "headers").read_text(errors="replace") if (Path(temp_dir) / "headers").exists() else ""
        content_type = "application/octet-stream"
        for line in header_text.splitlines():
            if line.lower().startswith("content-type:"):
                content_type = line.split(":", 1)[1].strip()
        if status_code in HALT_STATUS_CODES:
            raise PlatformHalted(self.platform, f"HTTP {status_code} {url}")
        if status_code >= 400:
            raise RuntimeError(f"HTTP {status_code} {url}")
        return FetchResult(
            url=url,
            status_code=status_code,
            content=content,
            text=content.decode("utf-8", errors="replace"),
            content_type=content_type,
        )

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay_seconds - elapsed
        if remaining > 0 and self._last_request_at > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()
