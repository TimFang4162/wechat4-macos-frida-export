#!/usr/bin/env python3
"""SIP 调试副本使用的公共路径与身份配置。"""

import pathlib

BASE = pathlib.Path(__file__).resolve().parent
RUNTIME = BASE / ".runtime"
DEFAULT_SOURCE = pathlib.Path("/Applications/WeChat.app")
DEFAULT_TARGET = RUNTIME / "WeChat-SIP.app"

# 公共仓库级调试身份。不能使用开发者个人 Team ID 或本机用户名。
DEBUG_BUNDLE_ID = "io.github.timfang4162.wechat4-macos-frida-export.sip"
DEBUG_BUNDLE_NAME = "WeChat SIP Export"
DEBUG_CONTAINER = pathlib.Path.home() / "Library" / "Containers" / DEBUG_BUNDLE_ID

OFFICIAL_XWECHAT_FILES = (
    pathlib.Path.home() / "Library" / "Containers" / "com.tencent.xinWeChat"
    / "Data" / "Documents" / "xwechat_files"
)
DEBUG_XWECHAT_FILES = (
    DEBUG_CONTAINER / "Data" / "Documents" / "xwechat_files"
)
