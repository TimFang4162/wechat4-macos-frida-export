#!/usr/bin/env python3
"""Frida 密钥抓取器 — 从运行中的微信进程抓取各数据库的 SQLCipher raw key。

原理：微信 4.1.10+ 不再把密钥以 x'<hex>' 形式缓存在内存，派生后的 32 字节
raw key 只在 CommonCrypto 加解密/派生瞬间出现。本脚本用 Frida hook
CCCrypt / CCCryptorCreate / CCCryptorCreateWithMode / CCKeyDerivationPBKDF，
把路过的 32 字节密钥全部记入 hunted_keys.txt。

需要 root（task_for_pid）。以普通用户运行时会经 osascript 弹出管理员
授权框并自动以 root 重新执行自身。

用法：
    python3 hunt_keys.py                 # 抓取，静默 45 秒无新密钥后自动停止
    python3 hunt_keys.py --restart       # 先挂钩，再重启微信触发全部库重新打开
    python3 hunt_keys.py --timeout 600   # 最长抓取 600 秒
    touch hunt.stop                      # 手动提前停止
"""
import argparse
import os
import shlex
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_JS = os.path.join(BASE, "keyhunt_frida.js")
OUT = os.path.join(BASE, "hunted_keys.txt")
LOG_PATH = os.path.join(BASE, "hunt.log")
STOP = os.path.join(BASE, "hunt.stop")


def elevate_and_exec(args):
    """非 root 时经 osascript 管理员授权框以 root 重跑自身。"""
    cmd = " ".join(shlex.quote(p) for p in
                   [sys.executable, os.path.abspath(__file__)] + args)
    as_escaped = cmd.replace("\\", "\\\\").replace('"', '\\"')
    script = (f'do shell script "{as_escaped}" '
              f'with administrator privileges '
              f'with prompt "微信数据导出：抓取数据库密钥（需要读取微信进程内存）"')
    ret = subprocess.run(["osascript", "-e", script]).returncode
    sys.exit(ret)


def find_wechat_pid():
    out = subprocess.run(["pgrep", "-x", "WeChat"],
                         capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def restart_wechat():
    """重启微信以触发全部数据库重新打开（密钥派生集中发生在此刻）。"""
    print("[*] 重启微信以触发数据库重新打开 ...")
    subprocess.run(["killall", "-TERM", "WeChat"], capture_output=True)
    for _ in range(20):
        if find_wechat_pid() is None:
            break
        time.sleep(1)
    if find_wechat_pid() is not None:
        subprocess.run(["killall", "-9", "WeChat"], capture_output=True)
        time.sleep(2)
    time.sleep(2)
    # root 下通过 SUDO_USER 回到用户会话执行 open
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        subprocess.run(["sudo", "-u", sudo_user, "open", "-a", "WeChat"],
                       capture_output=True)
    else:
        subprocess.run(["open", "-a", "WeChat"], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Frida 微信数据库密钥抓取器")
    parser.add_argument("--restart", action="store_true",
                        help="挂钩后重启微信，触发全部数据库重新打开")
    parser.add_argument("--timeout", type=int, default=420,
                        help="最长抓取秒数（默认 420）")
    parser.add_argument("--quiet", type=int, default=45,
                        help="连续 N 秒无新密钥即自动停止（默认 45；0=禁用）")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[!] 需要 root 权限，弹出管理员授权框 ...")
        elevate_and_exec(sys.argv[1:])
        return  # 只在提权失败时到达

    import frida  # 延迟导入：普通用户路径无需安装 frida

    if os.path.exists(STOP):
        os.remove(STOP)
    log = open(LOG_PATH, "a", buffering=1)
    keys = set()
    src = open(SCRIPT_JS).read()
    state = {"armed": False, "last_new": time.time()}

    def on_message(message, data):
        if message["type"] == "send":
            payload = message["payload"]
            if not isinstance(payload, str):
                return
            if payload.startswith("KEY32 "):
                hexkey = payload.split(" ")[-1]
                if hexkey not in keys:
                    keys.add(hexkey)
                    state["last_new"] = time.time()
                    with open(OUT, "a") as f:
                        f.write(hexkey + "\n")
                    log.write(payload + "\n")
                    print(f"[+] 密钥 #{len(keys)}: {hexkey[:16]}...")
            elif payload.startswith(("ARMED", "MISS", "PBKDF")):
                log.write(payload + "\n")
                if payload.startswith("ARMED"):
                    state["armed"] = True
                    print(f"[*] {payload}")
        elif message["type"] == "error":
            log.write("error: %s\n" % message.get("stack", message))

    deadline = time.time() + args.timeout
    attached_pid = None
    session = None
    restarted = False
    print(f"[*] 抓取中（超时 {args.timeout}s，静默 {args.quiet}s 自动停止），"
          f"touch hunt.stop 可手动停止")

    while time.time() < deadline and not os.path.exists(STOP):
        pid = find_wechat_pid()
        if pid != attached_pid:
            if session is not None:
                log.write("detaching from %s\n" % attached_pid)
                try:
                    session.detach()
                except Exception:
                    pass
                session = None
                attached_pid = None
            if pid is None:
                time.sleep(1)
                continue
            try:
                log.write("attaching frida to pid %d\n" % pid)
                print(f"[*] 附加微信进程 pid={pid}")
                session = frida.attach(pid)
                script = session.create_script(src)
                script.on("message", on_message)
                script.load()
                attached_pid = pid
            except Exception as e:
                log.write("attach failed: %r\n" % e)
                session = None
                time.sleep(1)
                continue
        elif not pid_alive(attached_pid):
            session = None
            attached_pid = None
            time.sleep(1)
            continue

        if (args.restart and state["armed"] and not restarted
                and attached_pid is not None):
            restart_wechat()
            restarted = True
            state["last_new"] = time.time()

        if (args.quiet > 0 and keys
                and time.time() - state["last_new"] >= args.quiet):
            print(f"[*] 已 {args.quiet}s 无新密钥，自动停止")
            break
        time.sleep(1)

    if session is not None:
        try:
            session.detach()
        except Exception as e:
            log.write("detach error: %r\n" % e)
    for p in (OUT, LOG_PATH):
        try:
            os.chmod(p, 0o644)
        except OSError:
            pass
    print(f"[+] 完成，共捕获 {len(keys)} 个唯一密钥 → {OUT}")


if __name__ == "__main__":
    main()
