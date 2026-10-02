#!/usr/bin/env python3
"""检查导出链路读取微信 App Data 容器所需的 TCC 权限。"""

import os
import pathlib
import subprocess
import sys

CONTAINER = pathlib.Path.home() / "Library/Containers/com.tencent.xinWeChat/Data"


def main():
    if sys.platform != "darwin":
        return 0
    try:
        next(os.scandir(CONTAINER), None)
    except PermissionError:
        print("[!] macOS 的 App Data 保护阻止当前进程读取微信容器。", file=sys.stderr)
        print("    请在 系统设置 → 隐私与安全性 → 完全磁盘访问权限 中添加：",
              file=sys.stderr)
        print("      当前运行本项目的 Terminal 或 Codex", file=sys.stderr)
        print("    授权后完全退出并重新打开对应终端/Codex，再运行 ./run.sh。",
              file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"[!] 未发现微信容器: {CONTAINER}", file=sys.stderr)
        return 1
    print(f"[+] 微信容器读取权限正常: {CONTAINER}")
    status = subprocess.run(
        ["csrutil", "status"], capture_output=True, text=True)
    if status.returncode == 0 and "enabled" in status.stdout.lower():
        print("[+] SIP 状态: enabled")
    elif status.stdout.strip():
        print(f"[*] {status.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
