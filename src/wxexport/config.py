"""
配置加载器 - 从项目根目录的 config.json 读取路径配置
首次运行时自动检测微信数据目录（macOS 容器路径），检测失败则提示手动配置
"""
import glob
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT_ROOT / "config.json"

_DB_DIR_TEMPLATE = os.path.expanduser(
    "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/your_wxid/db_storage")

_DEFAULT = {
    "db_dir": _DB_DIR_TEMPLATE,
    "keys_file": "all_keys.json",
    "decrypted_dir": "decrypted",
}


def _choose_candidate(candidates):
    """在多个候选目录中选择一个。"""
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        if not sys.stdin.isatty():
            return candidates[0]
        print("[!] 检测到多个微信数据目录（请选择当前正在运行的微信账号）:")
        for i, c in enumerate(candidates, 1):
            print(f"    {i}. {c}")
        print("    0. 跳过，稍后手动配置")
        try:
            while True:
                choice = input("请选择 [0-{}]: ".format(len(candidates))).strip()
                if choice == "0":
                    return None
                if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                    return candidates[int(choice) - 1]
                print("    无效输入，请重新选择")
        except (EOFError, KeyboardInterrupt):
            print()
            return None
    return None


def auto_detect_db_dir():
    """自动检测 macOS 微信 db_storage 路径（沙盒容器内）。"""
    container = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files")
    if not os.path.isdir(container):
        return None
    candidates = [m for m in glob.glob(os.path.join(container, "*", "db_storage"))
                  if os.path.isdir(m)]
    return _choose_candidate(candidates)


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
        except json.JSONDecodeError:
            print(f"[!] {CONFIG_FILE} 格式损坏，将使用默认配置")
            cfg = {}
    # db_dir 缺失或仍为模板值时，尝试自动检测
    db_dir = cfg.get("db_dir", "")
    if not db_dir or db_dir == _DB_DIR_TEMPLATE or "your_wxid" in db_dir:
        detected = auto_detect_db_dir()
        if detected:
            print(f"[+] 自动检测到微信数据目录: {detected}")
            cfg = {**_DEFAULT, **cfg, "db_dir": detected}
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
            print(f"[+] 已保存到: {CONFIG_FILE}")
        else:
            if not os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "w") as f:
                    json.dump(_DEFAULT, f, indent=4, ensure_ascii=False)
            print("[!] 未能自动检测微信数据目录")
            print(f"    请手动编辑 {CONFIG_FILE} 中的 db_dir 字段")
            sys.exit(1)
    else:
        cfg = {**_DEFAULT, **cfg}

    # 将相对路径转为绝对路径
    for key in ("keys_file", "decrypted_dir"):
        if key in cfg and not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(PROJECT_ROOT, cfg[key])

    return cfg
