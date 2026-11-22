"""冒烟脚本：真实拉起 uvicorn 并请求 /healthz。

用于任务 1.3 的验收（curl localhost:8000/healthz 等价验证），
不依赖外部 shell 的手工操作，也不会留下后台进程。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8123


def main() -> int:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        last_error = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/healthz", timeout=3
                ) as resp:
                    body = json.loads(resp.read().decode())
                    print(json.dumps({"http_status": resp.status, "body": body}, ensure_ascii=False))
                    return 0 if body.get("code") == 0 else 1
            except Exception as exc:  # noqa: BLE001 - 冒烟脚本，启动期内任意失败都重试
                last_error = str(exc)
                time.sleep(1)
        print(f"FAILED: {last_error}")
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
