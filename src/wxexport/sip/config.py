"""SIP 调试副本使用的公共路径与身份配置。"""

from pathlib import Path

from wxexport.config import PROJECT_ROOT

RUNTIME = PROJECT_ROOT / ".runtime"
DEFAULT_SOURCE = Path("/Applications/WeChat.app")
DEFAULT_TARGET = RUNTIME / "WeChat-SIP.app"

# 公共仓库级调试身份。不能使用开发者个人 Team ID 或本机用户名。
DEBUG_BUNDLE_ID = "io.github.timfang4162.wechat4-macos-frida-export.sip"
DEBUG_BUNDLE_NAME = "WeChat SIP Export"
DEBUG_CONTAINER = Path.home() / "Library" / "Containers" / DEBUG_BUNDLE_ID

OFFICIAL_XWECHAT_FILES = (
    Path.home() / "Library" / "Containers" / "com.tencent.xinWeChat"
    / "Data" / "Documents" / "xwechat_files"
)
DEBUG_XWECHAT_FILES = (
    DEBUG_CONTAINER / "Data" / "Documents" / "xwechat_files"
)
