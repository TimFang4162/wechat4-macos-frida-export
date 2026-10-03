"""微信 4.x 消息类型解码与内容渲染。

local_type 为 64 位：低 32 位是基础消息类型，高 32 位对基础类型 49（应用消息）
是 appmsg 子类型，与消息 XML 内 <type> 的取值一致。渲染把 XML 归约为一行
可读文本；合并转发的聊天记录（appmsg 19/24/87）在 recorditem 内带有完整的
datalist 摘要，可离线逐条展开。
"""
import re
import xml.etree.ElementTree as ET

BASE_TYPES = {
    1: "文本", 3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "表情",
    48: "位置", 49: "应用消息", 50: "语音/视频通话", 66: "企业微信联系人",
    67: "企业微信客服", 10000: "系统提示", 10002: "撤回消息", 11000: "系统通知",
}

APP_TYPES = {
    1: "链接", 3: "音乐", 4: "链接", 5: "文章", 6: "文件", 8: "图片",
    17: "实时位置共享", 19: "聊天记录", 24: "笔记", 33: "小程序", 36: "小程序",
    40: "聊天记录", 51: "视频号", 53: "接龙", 57: "引用回复", 62: "拍一拍",
    63: "视频号直播", 68: "文章", 74: "文件", 76: "音乐", 87: "群公告",
    115: "微信礼物", 116: "微信礼物", 124: "微信礼物", 2000: "转账", 2001: "红包",
}

MEDIA_TYPES = {3, 34, 43, 47}
SYSTEM_TYPES = {51, 10000, 10002, 11000}

# 群聊消息的 content 以 "发送者用户名:" 开头、换行分隔；单聊没有该前缀。
# 用户名限定为 ASCII 词形（wxid/别名/@openim），避免误吃以 "xx:" 起头的正文。
_ROOM_PREFIX = re.compile(r"^[A-Za-z0-9_.+\-@]{1,128}:\r?\n")

_RECORD_TYPES = {"1": "文本", "2": "图片", "3": "视频", "4": "语音",
                 "5": "链接", "6": "文件"}
_RECORD_FORMATS = {"pic": "图片", "video": "视频", "voice": "语音"}

_REFER_MAX = 120

_INLINE_TAG = re.compile(r"</?(?:img|a|br|_wc_custom_link_)\b[^>]*/?>")
_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"\$[^$]{0,64}\$")
_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>")


def decode_local_type(local_type):
    """拆出 (基础类型, appmsg 子类型)。"""
    return local_type & 0xFFFFFFFF, local_type >> 32


def type_name(base, sub, raw_type=None):
    if base == 49:
        return APP_TYPES.get(sub, f"应用消息({sub})")
    return BASE_TYPES.get(base, f"未知({raw_type if raw_type is not None else base})")


def strip_sender_prefix(text):
    """去掉群聊 content 的 "发送者:" 首行前缀。"""
    if text:
        return _ROOM_PREFIX.sub("", text, count=1)
    return text


def render_message(base, sub, text, depth=0):
    """把消息内容渲染为可读文本；内容为空且无结构信息时返回空串。"""
    if base == 49:
        return _render_appmsg(sub, text or "", depth)
    if base in (1, 11000):
        return (text or "").strip()
    if base in (10000, 10002):
        return _render_system(text or "")
    if base in MEDIA_TYPES:
        return f"[{BASE_TYPES[base]}]"
    if base == 42:
        return _render_tagged(text, "名片", "nickname")
    if base == 48:
        return _render_location(text)
    if base == 50:
        return _render_voip(text)
    if base in (66, 67):
        return _render_tagged(text, BASE_TYPES[base], "nickname")
    # 未知类型：文本原样保留，XML 一律不透出（由调用方落占位符）
    t = (text or "").strip()
    return "" if t.startswith("<") else t


def _parse_xml(text):
    text = text.strip()
    if not text.startswith("<"):
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def _fallback_text(text):
    """XML 解析失败时用正则提取 title/des。"""
    m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", text, re.S)
    title = (m.group(1).strip() if m else "")
    if title:
        return title
    return ""


def _render_tagged(text, label, attr):
    root = _parse_xml(text or "")
    if root is not None:
        name = root.get(attr) or root.findtext(attr) or ""
        name = name.strip()
        if name:
            return f"[{label}] {name}"
    return f"[{label}]"


def _render_system(text):
    """渲染 10000/10002 系统消息：撤回、群成员模板通知、带标记的纯文本。"""
    t = _XML_DECL_RE.sub("", text).strip()
    if not t.startswith("<"):
        return _INLINE_TAG.sub("", t).strip()
    try:
        root = ET.fromstring(f"<root>{t}</root>")
    except ET.ParseError:
        return _INLINE_TAG.sub("", _TAG_RE.sub("", t)).strip()

    revoke = root.find(".//revokemsg/content")
    if revoke is not None and (revoke.text or "").strip():
        return revoke.text.strip()

    if root.find(".//mmchatroomtopmsg") is not None:
        return ""

    tmpl = root.find(".//content_template")
    if tmpl is not None:
        return _render_content_template(tmpl)

    delm = root.find(".//delchatroommember")
    if delm is not None:
        plain = delm.findtext("plain") or delm.findtext("text") or ""
        return plain.strip()

    content = root.find(".//content")
    if content is not None:
        parts = " ".join("".join(content.itertext()).split())
        if parts:
            return _INLINE_TAG.sub("", parts).strip()
    parts = " ".join("".join(root.itertext()).split())
    return _INLINE_TAG.sub("", parts).strip()


def _render_content_template(tmpl):
    """展开 sysmsgtemplate：把模板里的 $占位$ 替换为成员昵称。"""
    out = tmpl.findtext("template") or ""
    fallback = (tmpl.findtext("plain") or "").strip()
    for link in tmpl.findall(".//link_list/link"):
        name = link.get("name") or ""
        if not name:
            continue
        nicknames = [
            (m.findtext("nickname") or m.findtext("username") or "").strip()
            for m in link.findall(".//member")]
        nicknames = [n for n in nicknames if n]
        sep = link.findtext("separator") or ""
        out = out.replace(f"${name}$", sep.join(nicknames))
    out = _PLACEHOLDER_RE.sub("", out).strip()
    return out or fallback


def _render_location(text):
    root = _parse_xml(text or "")
    poi = label = ""
    if root is not None:
        loc = root.find("location")
        if loc is not None:
            poi = (loc.get("poiname") or "").strip()
            label = (loc.get("label") or "").strip()
    place = poi or label
    return f"[位置] {place}" if place else "[位置]"


def _render_voip(text):
    root = _parse_xml(text or "")
    detail = ""
    duration = 0
    if root is not None:
        bubble = root.find(".//VoIPBubbleMsg")
        if bubble is not None:
            detail = (bubble.findtext("msg") or "").strip()
            try:
                duration = int(float(bubble.findtext("duration") or 0))
            except ValueError:
                duration = 0
    line = f"[语音/视频通话] {detail}".rstrip()
    if duration > 0:
        line += f"（{duration // 60}分{duration % 60}秒）"
    return line


def _human_len(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n}B"


def _render_recordinfo(record_item):
    """展开 recorditem CDATA 里的 recordinfo/datalist 摘要。"""
    info = _parse_xml(record_item)
    if info is None or info.tag != "recordinfo":
        return None
    datalist = info.find("datalist")
    lines = []
    if datalist is not None:
        for item in datalist.findall("dataitem"):
            who = (item.findtext("sourcename") or "").strip()
            desc = (item.findtext("datadesc") or "").strip()
            if not desc:
                fmt = (item.findtext("datafmt") or "").strip()
                dt = item.get("datatype", "")
                label = _RECORD_FORMATS.get(
                    fmt, _RECORD_TYPES.get(dt, f"记录({dt or '?'})"))
                desc = f"[{label}]"
            lines.append(f"  {who}: {desc}" if who else f"  {desc}")
    return lines


def _single_line(text):
    return re.sub(r"\s*\n+\s*", " / ", text.strip())


def _render_refer(app, depth):
    """渲染 57 引用回复中 <refermsg> 的被引内容。"""
    refer = app.find("refermsg")
    if refer is None:
        return None
    who = (refer.findtext("displayname")
           or refer.findtext("chatusr") or "").strip()
    raw = refer.findtext("content") or ""
    # refermsg 的 <type> 可能是回绕的垃圾值，以内容里是否含 appmsg 为准
    try:
        declared = int((refer.findtext("type") or "0").strip() or 0)
    except ValueError:
        declared = 0
    ref_base, _ = decode_local_type(declared)
    root = _parse_xml(strip_sender_prefix(raw)) if raw.lstrip().startswith("<") else None
    if ref_base == 49 or (root is not None and root.find("appmsg") is not None):
        inner = root.find("appmsg") if root is not None else None
        if inner is not None:
            try:
                inner_sub = int((inner.findtext("type") or "0").strip() or 0)
            except ValueError:
                inner_sub = 0
            ref = _render_appmsg(inner_sub, raw, depth + 1)
        else:
            ref = ""
    else:
        ref = render_message(ref_base, 0, strip_sender_prefix(raw))
    ref = _single_line(ref)[:_REFER_MAX]
    if not ref:
        return None
    return f"（引用 {who}: {ref}）" if who else f"（引用 {ref}）"


def _render_appmsg(sub, text, depth):
    name = APP_TYPES.get(sub, "应用消息")
    root = _parse_xml(text)
    app = root.find("appmsg") if root is not None else None
    if app is None:
        return _fallback_text(text)
    title = (app.findtext("title") or "").strip()
    des = (app.findtext("des") or "").strip()

    if sub in (19, 24, 87):
        head = f"[{name}] {title or des}".strip()
        lines = None
        record_item = app.findtext("recorditem")
        if record_item:
            lines = _render_recordinfo(record_item)
        if lines is None:
            return head or f"[{name}]"
        count = ""
        datalist = None
        info = _parse_xml(record_item)
        if info is not None:
            datalist = info.find("datalist")
        if datalist is not None and datalist.get("count"):
            count = f"（{datalist.get('count')}条）"
        return "\n".join([head + count] + lines)

    if sub == 40:
        body = f"{title}\n  {des}".strip() if des else title
        return f"[{name}] {body}" if body else f"[{name}]"

    if sub == 57:
        head = title or des
        if depth > 0:
            return head
        tail = _render_refer(app, depth) or ""
        if head and tail:
            return f"{head}{tail}"
        if tail:
            return tail[1:-1]
        return head

    if sub == 62:
        return title or des

    if sub == 2000:
        pay = app.find("wcpayinfo")
        feedesc = (pay.findtext("feedesc") or "").strip() if pay is not None else ""
        head = feedesc or des or title
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub == 2001:
        head = des or title
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (33, 36, 5, 68, 17, 53):
        head = title or des
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (6, 74):
        head = title or des
        attach = app.find("appattach")
        size = ""
        if attach is not None:
            try:
                total = int(attach.findtext("totallen") or 0)
            except ValueError:
                total = 0
            if total > 0:
                size = f"（{_human_len(total)}）"
        return f"[{name}] {head}{size}".rstrip() if head or size else f"[{name}]"

    if sub in (3, 76):
        head = f"{title} - {des}".strip(" -") if des else title
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub == 51:
        head = title
        if not head:
            feed = app.find(".//finderFeed")
            if feed is not None:
                nick = (feed.findtext("nickname") or "").strip()
                desc = _single_line(feed.findtext("desc") or "")[:80]
                head = f"{nick}: {desc}".strip(": ")
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub == 63:
        head = title or des
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (115, 116, 124):
        head = des or title
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub == 8:
        return f"[{name}]"

    head = title or des
    return f"[{name}] {head}".rstrip() if head else f"[{name}]"
