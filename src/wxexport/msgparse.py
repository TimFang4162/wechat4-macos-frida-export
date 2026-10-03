"""微信 4.x 消息类型解码与结构化解析。

local_type 为 64 位：低 32 位是基础消息类型，高 32 位对基础类型 49（应用消息）
是 appmsg 子类型，与消息 XML 内 <type> 的取值一致。

parse_message() 把一条消息解析为 {base, sub, type_name, content, detail}：
detail 是按类型结构化的内容字段（无损投影，供 JSON/CSV 使用）；content 是从
detail 派生的可读文本（TXT/CSV 使用）。两者同源，不会漂移。

解析只保留内容性标量；CDN 取件凭证（aeskey/cdnurl/模板 id 等）不是内容，
一律不收——媒体本体导出属于另一个特性，届时应从解密库按需解析。
"""
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import zstandard

CST = timezone(timedelta(hours=8))

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

_MEDIA_CHILD = {3: "img", 34: "voicemsg", 43: "videomsg", 47: "emoji"}

# 群聊消息的 content 以 "发送者用户名:" 开头、换行分隔；单聊没有该前缀。
# 用户名限定为 ASCII 词形（wxid/别名/@openim），避免误吃以 "xx:" 起头的正文。
_ROOM_PREFIX = re.compile(r"^[A-Za-z0-9_.+\-@]{1,128}:\r?\n")
# 单聊媒体消息的另一种前缀："发送者:目录:序号:md5:"
_MEDIA_HASH_PREFIX = re.compile(r"^[A-Za-z0-9_.+\-@]{1,128}:\d+:\d+:[0-9a-f]{32}:(?=<)")

_INLINE_TAG = re.compile(r"</?(?:img|a|br|_wc_custom_link_)\b[^>]*/?>")
_TAG_RE = re.compile(r"<[^>]+>")
_PLACEHOLDER_RE = re.compile(r"\$[^$]{0,64}\$")
_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>")

_RECORD_TYPES = {"1": "文本", "2": "图片", "3": "视频", "4": "语音",
                 "5": "链接", "6": "文件"}
_RECORD_FORMATS = {"pic": "图片", "video": "视频", "voice": "语音"}

_zdec = zstandard.ZstdDecompressor()


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


def decode_content(raw, ct):
    """zstd 解压并按 UTF-8 解码 message_content；失败返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        if ct == 4:
            try:
                raw = _zdec.stream_reader(io.BytesIO(raw)).read()
            except zstandard.ZstdError:
                return None
        return raw.decode("utf-8", errors="replace")
    return raw


def parse_message(type_id, raw, ct, is_chatroom=False):
    """解析一条消息，返回 {base, sub, type_name, content, detail}。

    content 保证非空（无可读内容时为类型占位符）。
    """
    text = decode_content(raw, ct)
    return parse_text(type_id, text, is_chatroom)


def parse_text(type_id, text, is_chatroom=False, depth=0):
    base, sub = decode_local_type(type_id)
    tname = type_name(base, sub, type_id)
    if is_chatroom:
        text = strip_sender_prefix(text)
    if base in MEDIA_TYPES:
        text = _MEDIA_HASH_PREFIX.sub("", text or "")
    detail = _parse_detail(base, sub, text or "", depth)
    if detail is not None:
        content = _render_detail(base, sub, detail, depth)
    else:
        content = _render_plain(text)
    return {"base": base, "sub": sub, "type_name": tname,
            "content": content or f"[{tname}]", "detail": detail}


# ---------------------------------------------------------------- 解析

def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_xml(text):
    text = text.strip()
    if not text.startswith("<"):
        return None
    try:
        return ET.fromstring(_XML_DECL_RE.sub("", text))
    except ET.ParseError:
        return None


def _parse_detail(base, sub, text, depth):
    if base == 49:
        return _parse_appmsg(sub, text, depth)
    if base in (10000, 10002):
        return _parse_system(text)
    if base in (42, 66, 67):
        return _parse_tagged(text)
    if base == 48:
        return _parse_location(text)
    if base == 50:
        return _parse_voip(text)
    if base in MEDIA_TYPES:
        return _parse_media(base, text)
    return None


def _parse_appmsg(sub, text, depth):
    root = _parse_xml(text)
    app = root.find("appmsg") if root is not None else None
    if app is None:
        # XML 破损时尽力抢救 title
        m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", text, re.S)
        if m and m.group(1).strip():
            return {"title": m.group(1).strip()}
        return None
    title = (app.findtext("title") or "").strip()
    des = (app.findtext("des") or "").strip()

    if sub in (19, 24, 87):
        return _parse_recordinfo(title, des, app)
    if sub == 40:
        d = {"title": title, "summary": des}
        full = _int(app.findtext("xmlfulllen"))
        if full:
            d["full_len"] = full
        return d
    if sub == 57:
        return _parse_quote(title, des, app, depth)
    if sub in (2000, 2001):
        return _parse_payment(sub, title, des, app)
    if sub in (6, 74):
        d = {"title": title}
        attach = app.find("appattach")
        if attach is not None:
            size = _int(attach.findtext("totallen"))
            if size:
                d["size_bytes"] = size
            ext = (attach.findtext("fileext") or "").strip()
            if ext:
                d["ext"] = ext
        return d or None
    if sub in (1, 4, 5, 68, 33, 36):
        d = {}
        if title:
            d["title"] = title
        if des:
            d["desc"] = des
        url = (app.findtext("url") or "").strip()
        if url:
            d["url"] = url
        return d or None
    if sub in (3, 76):
        d = {"title": title} if title else {}
        if des:
            d["artist"] = des
        return d or None
    if sub == 51:
        d = {"title": title} if title else {}
        feed = app.find(".//finderFeed")
        if feed is not None:
            for tag, key in (("nickname", "nickname"), ("desc", "desc"),
                             ("objectId", "object_id")):
                v = (feed.findtext(tag) or "").strip()
                if v:
                    d[key] = v
        return d or None
    if sub == 63:
        d = {"title": title} if title else {}
        if des:
            d["desc"] = des
        return d or None
    if sub in (115, 116, 124):
        d = {}
        if title:
            d["title"] = title
        if des:
            d["desc"] = des
        return d or None
    if sub in (17, 53):
        return {"text": title or des} if (title or des) else None
    if sub == 62:
        return {"text": title or des} if (title or des) else None
    if sub == 8:
        d = {}
        attach = app.find("appattach")
        if attach is not None:
            size = _int(attach.findtext("totallen"))
            if size:
                d["size_bytes"] = size
            md5 = (attach.findtext("emoticonmd5") or "").strip()
            if md5:
                d["md5"] = md5
        return d or None
    d = {}
    if title:
        d["title"] = title
    if des:
        d["desc"] = des
    return d or None


def _parse_recordinfo(title, des, app):
    record_item = app.findtext("recorditem")
    info = _parse_xml(record_item) if record_item else None
    if info is None or info.tag != "recordinfo":
        return {"title": title or des} if (title or des) else None
    datalist = info.find("datalist")
    items = []
    if datalist is not None:
        for item in datalist.findall("dataitem"):
            it = {"sender": (item.findtext("sourcename") or "").strip() or None,
                  "time": (item.findtext("sourcetime") or "").strip() or None,
                  "timestamp": _int(item.findtext("srcMsgCreateTime")),
                  "datatype": _int(item.get("datatype"))}
            desc = (item.findtext("datadesc") or "").strip()
            if desc:
                it["text"] = desc
            else:
                fmt = (item.findtext("datafmt") or "").strip()
                if fmt:
                    it["datafmt"] = fmt
            size = _int(item.findtext("datasize"))
            if size:
                it["size"] = size
            items.append({k: v for k, v in it.items() if v is not None})
    d = {"title": title or (info.findtext("info") or "").strip() or des,
         "is_chatroom": (info.findtext("isChatRoom") or "").strip() == "1",
         "items": items}
    count = _int(datalist.get("count")) if datalist is not None else None
    d["count"] = count or len(items)
    return d


def _single_line(text):
    return re.sub(r"\s*\n+\s*", " / ", text.strip())


def _parse_quote(title, des, app, depth):
    refer = app.find("refermsg")
    if refer is None:
        return {"text": title or des} if (title or des) else None
    raw = refer.findtext("content") or ""
    try:
        declared = int((refer.findtext("type") or "").strip() or 0)
    except ValueError:
        declared = 0
    ref_base, _ = decode_local_type(declared)
    root = _parse_xml(strip_sender_prefix(raw)) if raw.lstrip().startswith("<") else None
    inner_sub = None
    if ref_base == 49 or (root is not None and root.find("appmsg") is not None):
        inner = root.find("appmsg") if root is not None else None
        if inner is not None:
            inner_sub = _int(inner.findtext("type")) or 0
            summary = parse_text((inner_sub << 32) | 49, raw, depth=depth + 1)["content"]
        else:
            summary = ""
    elif ref_base in MEDIA_TYPES:
        summary = f"[{BASE_TYPES[ref_base]}]"
    else:
        detail = _parse_detail(ref_base, 0, strip_sender_prefix(raw), depth + 1)
        if detail is not None:
            summary = _render_detail(ref_base, 0, detail, depth + 1)
        else:
            plain = strip_sender_prefix(raw).strip()
            summary = "" if plain.startswith("<") else plain
    ref = {"summary": summary, "type": ref_base}
    svrid = _int(refer.findtext("svrid"))
    if svrid:
        ref["svrid"] = svrid
    who = (refer.findtext("displayname")
           or refer.findtext("chatusr") or "").strip()
    if who:
        ref["sender"] = who
    chatusr = (refer.findtext("chatusr") or "").strip()
    if chatusr:
        ref["sender_wxid"] = chatusr
    created = _int(refer.findtext("createtime"))
    if created:
        ref["time"] = created
    if inner_sub:
        ref["subtype"] = inner_sub
    # 不保留被引原文 XML：文本引用的 summary 即其无损文本；嵌套 49 的内容
    # 已由 summary 完整渲染；媒体被引的原文只是 CDN 凭证，不是内容。
    ref["summary"] = _single_line(ref["summary"])
    return {"text": title or "", "refer": ref}


def _parse_payment(sub, title, des, app):
    w = app.find("wcpayinfo")
    d = {}
    if w is not None:
        if sub == 2000:
            for tag, key in (("feedesc", "amount"), ("pay_memo", "memo")):
                v = (w.findtext(tag) or "").strip()
                if v:
                    d[key] = v
            st = _int(w.findtext("paysubtype"))
            if st:
                d["pay_subtype"] = st
            for tag, key in (("begintransfertime", "begin_time"),
                             ("invalidtime", "expire_at"),
                             ("receiver_username", "receiver"),
                             ("payer_username", "payer")):
                if tag in ("begintransfertime", "invalidtime"):
                    v = _int(w.findtext(tag))
                else:
                    v = (w.findtext(tag) or "").strip()
                if v:
                    d[key] = v
        else:
            for tag, key in (("sendertitle", "sender_title"),
                             ("receivertitle", "receiver_title"),
                             ("senderdes", "sender_des"),
                             ("receiverdes", "receiver_des"),
                             ("scenetext", "scene_text")):
                v = (w.findtext(tag) or "").strip()
                if v:
                    d[key] = v
            inv = _int(w.findtext("invalidtime"))
            if inv:
                d["expire_at"] = inv
    if des:
        d["des"] = des
    if title and title not in ("微信转账", "微信红包"):
        d["title"] = title
    return d or None


def _parse_tagged(text):
    root = _parse_xml(text or "")
    if root is None:
        return None
    d = {}
    for key in ("nickname", "alias", "province", "city", "sign"):
        v = (root.get(key) or root.findtext(key) or "").strip()
        if v:
            d[key] = v
    return d or None


def _parse_location(text):
    root = _parse_xml(text or "")
    if root is None:
        return None
    loc = root.find("location")
    if loc is None:
        return None
    d = {}
    for attr, key in (("poiname", "poiname"), ("label", "label"),
                      ("x", "lat"), ("y", "lon")):
        v = (loc.get(attr) or "").strip()
        if v:
            d[key] = v
    return d or None


def _parse_voip(text):
    root = _parse_xml(text or "")
    if root is None:
        return None
    bubble = root.find(".//VoIPBubbleMsg")
    if bubble is None:
        return None
    d = {}
    msg = (bubble.findtext("msg") or "").strip()
    if msg:
        d["detail"] = msg
    dur = _int(bubble.findtext("duration"))
    if dur:
        d["duration_sec"] = dur
    vtype = _int(bubble.findtext("msg_type"))
    if vtype:
        d["voip_type"] = vtype
    return d or None


def _parse_media(base, text):
    root = _parse_xml(text or "")
    if root is None:
        return None
    el = root.find(_MEDIA_CHILD[base])
    if el is None:
        el = root
    d = {}

    def attr(*names):
        for n in names:
            v = el.get(n)
            if v:
                return v
        return None

    if base == 3:
        d = {"md5": attr("md5"), "size": _int(attr("length")),
             "width": _int(attr("cdnthumbwidth")), "height": _int(attr("cdnthumbheight"))}
    elif base == 34:
        d = {"duration_ms": _int(attr("voicelength")), "md5": attr("voicemd5")}
    elif base == 43:
        d = {"duration_sec": _int(attr("playlength")), "size": _int(attr("length")),
             "md5": attr("md5")}
    elif base == 47:
        d = {"md5": attr("md5"), "size": _int(attr("len"))}
    return {k: v for k, v in d.items() if v is not None} or None


def _parse_system(text):
    t = _XML_DECL_RE.sub("", text).strip()
    if not t.startswith("<"):
        return {"kind": "plain", "text": _INLINE_TAG.sub("", t).strip()}
    try:
        root = ET.fromstring(f"<root>{t}</root>")
    except ET.ParseError:
        return {"kind": "plain",
                "text": _INLINE_TAG.sub("", _TAG_RE.sub("", t)).strip()}

    revoke = root.find(".//revokemsg/content")
    if revoke is not None and (revoke.text or "").strip():
        return {"kind": "revoke", "text": revoke.text.strip()}

    if root.find(".//mmchatroomtopmsg") is not None:
        return {"kind": "room_top", "text": ""}

    tmpl = root.find(".//content_template")
    if tmpl is not None:
        return {"kind": "template", "text": _expand_template(tmpl)}

    delm = root.find(".//delchatroommember")
    if delm is not None:
        plain = delm.findtext("plain") or delm.findtext("text") or ""
        return {"kind": "plain", "text": plain.strip()}

    content = root.find(".//content")
    if content is not None:
        parts = " ".join("".join(content.itertext()).split())
        if parts:
            return {"kind": "plain", "text": _INLINE_TAG.sub("", parts).strip()}
    parts = " ".join("".join(root.itertext()).split())
    return {"kind": "plain", "text": _INLINE_TAG.sub("", parts).strip()}


def _expand_template(tmpl):
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


# ---------------------------------------------------------------- 渲染

def _fmt_md_hm(epoch):
    return datetime.fromtimestamp(epoch, tz=CST).strftime("%m-%d %H:%M")


def _fmt_dur(seconds):
    seconds = int(seconds)
    if seconds >= 60:
        return f"{seconds // 60}分{seconds % 60:02d}秒"
    return f"{seconds}秒"


def _render_plain(text):
    t = (text or "").strip()
    return "" if t.startswith("<") else t


def _item_text(item):
    if "text" in item:
        return item["text"]
    label = (_RECORD_FORMATS.get(item.get("datafmt", ""))
             or _RECORD_TYPES.get(str(item.get("datatype", "")))
             or f"记录({item.get('datatype') or '?'})")
    return f"[{label}]"


def _render_detail(base, sub, d, depth=0):
    if base == 49:
        return _render_appmsg(sub, d, depth)
    if base in (10000, 10002):
        return d.get("text") or ""
    if base == 42:
        return f"[名片] {d['nickname']}" if "nickname" in d else "[名片]"
    if base in (66, 67):
        nick = d.get("nickname")
        return f"[{BASE_TYPES[base]}] {nick}" if nick else f"[{BASE_TYPES[base]}]"
    if base == 48:
        place = d.get("poiname") or d.get("label")
        return f"[位置] {place}" if place else "[位置]"
    if base == 50:
        line = f"[语音/视频通话] {d.get('detail', '')}".rstrip()
        if d.get("duration_sec"):
            line += f"（{_fmt_dur(d['duration_sec'])}）"
        return line
    if base == 3:
        return "[图片]"
    if base == 34:
        ms = d.get("duration_ms")
        return f"[语音 {_fmt_dur(ms / 1000)}]" if ms else "[语音]"
    if base == 43:
        sec = d.get("duration_sec")
        return f"[视频 {_fmt_dur(sec)}]" if sec else "[视频]"
    if base == 47:
        return "[表情]"
    return d.get("text") or ""


def _render_appmsg(sub, d, depth):
    name = APP_TYPES.get(sub, "应用消息")

    if sub in (19, 24, 87):
        head = f"[{name}] {d.get('title', '')}".strip()
        count = d.get("count")
        if count:
            head += f"（{count}条）"
        lines = [head]
        for i, item in enumerate(d.get("items") or [], 1):
            left = f"  {i}."
            if item.get("time"):
                left += f" {item['time']}"
            if item.get("sender"):
                left += f" {item['sender']}:"
            lines.append(f"{left} {_item_text(item)}")
        return "\n".join(lines)

    if sub == 40:
        body = d.get("title", "")
        if d.get("summary"):
            body = f"{body}\n  {d['summary']}" if body else d["summary"]
        return f"[{name}] {body}".rstrip() if body else f"[{name}]"

    if sub == 57:
        return _render_quote(d, depth)

    if sub == 2000:
        head = d.get("amount") or d.get("des") or d.get("title") or ""
        line = f"[{name}] {head}".rstrip()
        if d.get("memo"):
            line += f"（留言：{d['memo']}）"
        return line if head else f"[{name}]"

    if sub == 2001:
        head = d.get("des") or d.get("sender_title") or d.get("title") or ""
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (6, 74):
        head = d.get("title", "")
        size = d.get("size_bytes")
        if size:
            head = f"{head}（{_human_len(size)}）" if head else f"（{_human_len(size)}）"
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (1, 4, 5, 68, 33, 36):
        head = d.get("title") or d.get("desc") or ""
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (3, 76):
        head = f"{d.get('title', '')} - {d['artist']}".strip(" -") if d.get("artist") \
            else d.get("title", "")
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub == 51:
        head = d.get("title")
        if not head:
            nick, desc = d.get("nickname"), d.get("desc")
            head = f"{nick}: {desc}".strip(": ") if (nick or desc) else ""
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (115, 116, 124):
        head = d.get("desc") or d.get("title") or ""
        return f"[{name}] {head}".rstrip() if head else f"[{name}]"

    if sub in (17, 53, 62):
        return d.get("text") or f"[{name}]"

    head = d.get("title") or d.get("desc") or ""
    return f"[{name}] {head}".rstrip() if head else f"[{name}]"


def _render_quote(d, depth):
    text = d.get("text") or ""
    ref = d.get("refer")
    if not ref:
        return text
    if depth > 0:
        return text
    body = ref.get("sender") or ""
    if ref.get("time"):
        body = f"{body} [{_fmt_md_hm(ref['time'])}]" if body \
            else _fmt_md_hm(ref["time"])
    if ref.get("summary"):
        body = f"{body}: {ref['summary']}" if body else ref["summary"]
    if not body:
        return text
    return f"{text}（引用 {body}）" if text else f"引用 {body}"


def _human_len(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n}B"
