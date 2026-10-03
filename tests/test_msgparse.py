"""msgparse 的回归测试：结构化 detail 与可读渲染。fixture 为按真实结构构造的合成 XML。"""
import unittest
from datetime import datetime

from wxexport.msgparse import (CST, decode_local_type, parse_message,
                               parse_text, strip_sender_prefix, type_name)

RECORD_XML = """<msg><appmsg><title>群聊的聊天记录</title><des>甲: [图片]</des>
<type>19</type><recorditem><![CDATA[<recordinfo><isChatRoom>1</isChatRoom>
<datalist count="3">
<dataitem datatype="1"><sourcename>甲</sourcename><sourcetime>2026-1-14 19:20</sourcetime>
<srcMsgCreateTime>1768389638</srcMsgCreateTime><datadesc>你今天去不去打针</datadesc></dataitem>
<dataitem datatype="2"><datafmt>pic</datafmt><sourcename>甲</sourcename>
<sourcetime>2026-1-14 19:20</sourcetime><datasize>257598</datasize></dataitem>
<dataitem datatype="9"><sourcename>乙</sourcename><sourcetime>2026-1-14 19:21</sourcetime></dataitem>
</datalist></recordinfo>]]></recorditem></appmsg></msg>"""

QUOTE_TEXT_XML = """<msg><appmsg><title>应该是民办吧</title><type>57</type>
<refermsg><type>1</type><svrid>3035572160325032061</svrid>
<displayname>作弊式.</displayname><createtime>1757255408</createtime>
<content>听说你们学校是民办</content></refermsg></appmsg></msg>"""

QUOTE_MEDIA_XML = """<msg><appmsg><title>看看这个</title><type>57</type>
<refermsg><type>3</type><displayname>小张</displayname><createtime>1757255408</createtime>
<content><![CDATA[<msg><img aeskey="x"/></msg>]]></content>
</refermsg></appmsg></msg>"""

QUOTE_APPMSG_XML = """<msg><appmsg><type>57</type>
<refermsg><type>49</type><displayname>小李</displayname><createtime>1757255408</createtime><content>&lt;msg&gt;
&lt;appmsg&gt;&lt;title&gt;聊聊&lt;/title&gt;&lt;type&gt;19&lt;/type&gt;
&lt;recorditem&gt;&amp;lt;recordinfo&amp;gt;&amp;lt;datalist count="1"&amp;gt;
&amp;lt;dataitem datatype="1"&amp;gt;&amp;lt;sourcename&amp;gt;小李&amp;lt;/sourcename&amp;gt;
&amp;lt;datadesc&amp;gt;第一句&amp;lt;/datadesc&amp;gt;&amp;lt;/dataitem&amp;gt;
&amp;lt;/datalist&amp;gt;&amp;lt;/recordinfo&amp;gt;&lt;/recorditem&gt;
&lt;/appmsg&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>"""

QUOTE_GIFT_XML = ('<msg><appmsg><title>生日礼物咋样</title><type>57</type>'
                  '<refermsg><type>-2113929167</type><displayname>KD.</displayname>'
                  '<content>&lt;msg&gt;&lt;appmsg&gt;&lt;title&gt;微信礼物&lt;/title&gt;'
                  '&lt;des&gt;当前版本暂不支持查看礼物&lt;/des&gt;&lt;type&gt;124&lt;/type&gt;'
                  '&lt;/appmsg&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>')

TRANSFER_XML = """<msg><appmsg><title>微信转账</title><type>2000</type>
<wcpayinfo><paysubtype>1</paysubtype><feedesc><![CDATA[￥115.30]]></feedesc>
<pay_memo>请喝茶</pay_memo><transferid>12345</transferid></wcpayinfo>
</appmsg></msg>"""

REDPACKET_XML = """<msg><appmsg><type>2001</type><des>我给你发了一个红包，赶紧去拆!</des>
<wcpayinfo><sendertitle>恭喜发财</sendertitle><receivertitle>领取红包</receivertitle>
<scenetext>微信红包</scenetext><url>https://wxapp.tenpay.com/x</url></wcpayinfo>
</appmsg></msg>"""

FILE_XML = """<msg><appmsg><title>报告.pptx</title><type>6</type>
<appattach><totallen>2048000</totallen><fileext>pptx</fileext></appattach>
</appmsg></msg>"""

LOCATION_XML = """<msg><location x="30.182011" y="120.267632" label="某某路9号"
poiname="某餐厅" fromusername="wxid_a"/></msg>"""

VOIP_XML = """<voipmsg type="VoIPBubbleMsg"><VoIPBubbleMsg>
<msg><![CDATA[已在其它设备接听]]></msg><duration>75</duration>
<msg_type>101</msg_type></VoIPBubbleMsg></voipmsg>"""

CARD_XML = """<msg bigheadimgurl="http://x/0" nickname="某单位" alias="ops"
province="浙江" city="金华"/>"""

FINDER_XML = """<msg><appmsg><title></title><type>51</type>
<finderFeed><objectId>14995654624611080561</objectId>
<nickname>某博主</nickname><desc><![CDATA[视频描述文本]]></desc>
</finderFeed></appmsg></msg>"""

ARTICLE_XML = """<msg><appmsg><title>入学时间轴</title><des>请查收</des><type>5</type>
<url>https://mp.weixin.qq.com/s?__biz=abc</url></appmsg></msg>"""

IMG_XML = ('<msg><img aeskey="secret" encryver="1" cdnthumbaeskey="tk" '
           'cdnthumburl="http://cdn/x" length="1024" md5="m1" '
           'cdnthumbwidth="100" cdnthumbheight="50"/></msg>')

VOICE_XML = '<msg><voicemsg voicelength="75000" voiceformat="4" aeskey="k"/></msg>'

VIDEO_XML = '<msg><videomsg playlength="63" length="2048" md5="v1" aeskey="k"/></msg>'

EMOJI_XML = ('<msg><emoji fromusername="wxid_a" md5="e1" len="2048" '
             'cdnurl="http://cdn/e"/></msg>')

REVOKE_XML = ('<?xml version="1.0"?><sysmsg type="revokemsg"><revokemsg>'
              '<content>"甲" 撤回了一条消息</content><revoketime>0</revoketime>'
              '</revokemsg></sysmsg>')

TEMPLATE_XML = ('<sysmsg type="sysmsgtemplate"><sysmsgtemplate>'
                '<content_template type="tmpl_type_profile">'
                '<plain><![CDATA[]]></plain>'
                '<template><![CDATA["$username$"邀请"$names$"加入了群聊]]></template>'
                '<link_list>'
                '<link name="username" type="link_profile"><memberlist><member>'
                '<username>wxid_a</username><nickname>蓝色星球</nickname></member>'
                '</memberlist></link>'
                '<link name="names" type="link_profile"><memberlist>'
                '<member><username>wxid_b</username><nickname>弦</nickname></member>'
                '</memberlist><separator><![CDATA[、]]></separator></link>'
                '</link_list></content_template></sysmsgtemplate></sysmsg>')

TOPMSG_XML = ('<sysmsg type="mmchatroomtopmsg"><mmchatroomtopmsg>'
              '<chatroomname>123@chatroom</chatroomname><op>1</op>'
              '</mmchatroomtopmsg></sysmsg>')


class DecodeTest(unittest.TestCase):
    def test_local_type_roundtrip(self):
        self.assertEqual(decode_local_type((19 << 32) | 49), (49, 19))
        self.assertEqual(decode_local_type(1), (1, 0))

    def test_type_name(self):
        self.assertEqual(type_name(1, 0), "文本")
        self.assertEqual(type_name(49, 19), "聊天记录")
        self.assertEqual(type_name(49, 2000), "转账")
        self.assertIn("应用消息", type_name(49, 9999))
        self.assertIn("未知", type_name(777, 0, 777))

    def test_room_prefix(self):
        self.assertEqual(strip_sender_prefix("wxid_abc:\n你好"), "你好")
        self.assertIsNone(strip_sender_prefix(None))


class BasicTest(unittest.TestCase):
    def test_text(self):
        m = parse_message(1, "hello", 0)
        self.assertEqual((m["base"], m["sub"]), (1, 0))
        self.assertEqual(m["content"], "hello")
        self.assertIsNone(m["detail"])

    def test_empty_text_placeholder(self):
        self.assertEqual(parse_message(1, "", 0)["content"], "[文本]")
        self.assertEqual(parse_message(1, None, 0)["content"], "[文本]")

    def test_chatroom_prefix_applied_only_for_rooms(self):
        self.assertEqual(
            parse_message(1, "wxid_abc:\n备注:\n第二行", 0, is_chatroom=True)["content"],
            "备注:\n第二行")
        self.assertEqual(
            parse_message(1, "备注:\n第二行", 0)["content"], "备注:\n第二行")

    def test_xml_never_leaks(self):
        m = parse_message(49, "<msg><appmsg>", 0)
        self.assertFalse("<" in m["content"])


class MediaTest(unittest.TestCase):
    def test_image_detail(self):
        m = parse_message(3, IMG_XML, 0)
        self.assertEqual(m["content"], "[图片]")
        self.assertEqual(m["detail"],
                         {"md5": "m1", "size": 1024, "width": 100, "height": 50})
        self.assertNotIn("aeskey", m["detail"])

    def test_voice_duration(self):
        m = parse_message(34, VOICE_XML, 0)
        self.assertEqual(m["content"], "[语音 1分15秒]")
        self.assertEqual(m["detail"], {"duration_ms": 75000})

    def test_video_duration(self):
        m = parse_message(43, VIDEO_XML, 0)
        self.assertEqual(m["content"], "[视频 1分03秒]")
        self.assertEqual(m["detail"], {"duration_sec": 63, "size": 2048, "md5": "v1"})

    def test_emoji_md5(self):
        m = parse_message(47, EMOJI_XML, 0)
        self.assertEqual(m["content"], "[表情]")
        self.assertEqual(m["detail"], {"md5": "e1", "size": 2048})

    def test_chatroom_media_prefix_stripped_before_parse(self):
        m = parse_message(3, f"wxid_a:\n{IMG_XML}", 0, is_chatroom=True)
        self.assertEqual(m["detail"]["md5"], "m1")

    def test_single_chat_media_hash_prefix(self):
        raw = ("wxid_a:0:1:f790e342a02e0f99d34b316547f9aeab:"
               '<msg><emoji md5="f790e342a02e0f99d34b316547f9aeab" len="2048"/></msg>')
        m = parse_message(47, raw, 0)
        self.assertEqual(m["content"], "[表情]")
        self.assertEqual(m["detail"]["size"], 2048)


class RecordTest(unittest.TestCase):
    def test_record_detail_with_times(self):
        m = parse_text(19 << 32 | 49, RECORD_XML)
        d = m["detail"]
        self.assertEqual(d["title"], "群聊的聊天记录")
        self.assertEqual(d["count"], 3)
        self.assertTrue(d["is_chatroom"])
        self.assertEqual(d["items"][0],
                         {"sender": "甲", "time": "2026-1-14 19:20",
                          "timestamp": 1768389638, "datatype": 1,
                          "text": "你今天去不去打针"})
        self.assertEqual(d["items"][1]["size"], 257598)

    def test_record_render_with_times(self):
        m = parse_text(19 << 32 | 49, RECORD_XML)
        self.assertTrue(m["content"].startswith("[聊天记录] 群聊的聊天记录（3条）"))
        self.assertIn("  1. 2026-1-14 19:20 甲: 你今天去不去打针", m["content"])
        self.assertIn("  2. 2026-1-14 19:20 甲: [图片]", m["content"])
        self.assertIn("  3. 2026-1-14 19:21 乙: [记录(9)]", m["content"])


class QuoteTest(unittest.TestCase):
    def test_quote_text(self):
        m = parse_text(57 << 32 | 49, QUOTE_TEXT_XML)
        expected = datetime.fromtimestamp(1757255408, tz=CST).strftime("%m-%d %H:%M")
        self.assertEqual(m["content"],
                         f"应该是民办吧（引用 作弊式. [{expected}]: 听说你们学校是民办）")
        ref = m["detail"]["refer"]
        self.assertEqual(ref["time"], 1757255408)
        self.assertEqual(ref["type"], 1)
        self.assertEqual(ref["svrid"], 3035572160325032061)
        self.assertEqual(ref["summary"], "听说你们学校是民办")
        self.assertNotIn("content", ref)

    def test_quote_media(self):
        m = parse_text(57 << 32 | 49, QUOTE_MEDIA_XML)
        self.assertTrue(m["content"].startswith("看看这个（引用 小张 ["))
        self.assertIn(": [图片]）", m["content"])

    def test_quote_nested_appmsg_single_line(self):
        m = parse_text(57 << 32 | 49, QUOTE_APPMSG_XML)
        self.assertEqual(m["content"],
                         "引用 小李 [09-07 22:30]: [聊天记录] 聊聊（1条） / 1. 小李: 第一句")
        self.assertEqual(m["detail"]["refer"]["subtype"], 19)

    def test_quote_garbage_ref_type_sniffs_content(self):
        m = parse_text(57 << 32 | 49, QUOTE_GIFT_XML)
        self.assertEqual(m["content"],
                         "生日礼物咋样（引用 KD.: [微信礼物] 当前版本暂不支持查看礼物）")


class PayTest(unittest.TestCase):
    def test_transfer_with_memo(self):
        m = parse_text(2000 << 32 | 49, TRANSFER_XML)
        self.assertEqual(m["content"], "[转账] ￥115.30（留言：请喝茶）")
        d = m["detail"]
        self.assertEqual(d["amount"], "￥115.30")
        self.assertEqual(d["memo"], "请喝茶")
        self.assertEqual(d["pay_subtype"], 1)
        self.assertNotIn("transferid", d)

    def test_redpacket(self):
        m = parse_text(2001 << 32 | 49, REDPACKET_XML)
        self.assertEqual(m["content"], "[红包] 我给你发了一个红包，赶紧去拆!")
        d = m["detail"]
        self.assertEqual(d["sender_title"], "恭喜发财")
        self.assertEqual(d["scene_text"], "微信红包")
        self.assertNotIn("url", d)


class TypedDetailTest(unittest.TestCase):
    def test_file(self):
        m = parse_text(6 << 32 | 49, FILE_XML)
        self.assertEqual(m["content"], "[文件] 报告.pptx（2.0MB）")
        self.assertEqual(m["detail"], {"title": "报告.pptx",
                                       "size_bytes": 2048000, "ext": "pptx"})

    def test_location(self):
        m = parse_message(48, LOCATION_XML, 0)
        self.assertEqual(m["content"], "[位置] 某餐厅")
        self.assertEqual(m["detail"], {"poiname": "某餐厅", "label": "某某路9号",
                                       "lat": "30.182011", "lon": "120.267632"})

    def test_card(self):
        m = parse_message(42, CARD_XML, 0)
        self.assertEqual(m["content"], "[名片] 某单位")
        self.assertEqual(m["detail"], {"nickname": "某单位", "alias": "ops",
                                       "province": "浙江", "city": "金华"})

    def test_voip(self):
        m = parse_message(50, VOIP_XML, 0)
        self.assertEqual(m["content"], "[语音/视频通话] 已在其它设备接听（1分15秒）")
        self.assertEqual(m["detail"], {"detail": "已在其它设备接听",
                                       "duration_sec": 75, "voip_type": 101})

    def test_finder(self):
        m = parse_text(51 << 32 | 49, FINDER_XML)
        self.assertEqual(m["content"], "[视频号] 某博主: 视频描述文本")
        self.assertEqual(m["detail"]["object_id"], "14995654624611080561")

    def test_article_keeps_url(self):
        m = parse_text(5 << 32 | 49, ARTICLE_XML)
        self.assertEqual(m["content"], "[文章] 入学时间轴")
        self.assertEqual(m["detail"]["url"], "https://mp.weixin.qq.com/s?__biz=abc")

    def test_pat_bare_text(self):
        m = parse_text(62 << 32 | 49, '<msg><appmsg><type>62</type>'
                                      '<title>"甲" 拍了拍 "乙"</title></appmsg></msg>')
        self.assertEqual((m["content"], m["detail"]["text"]),
                         ('"甲" 拍了拍 "乙"', '"甲" 拍了拍 "乙"'))


class SystemTest(unittest.TestCase):
    def test_revoke(self):
        m = parse_message(10000, REVOKE_XML, 0)
        self.assertEqual((m["detail"]["kind"], m["content"]),
                         ("revoke", '"甲" 撤回了一条消息'))

    def test_template(self):
        m = parse_message(10000, TEMPLATE_XML, 0)
        self.assertEqual(m["detail"]["kind"], "template")
        self.assertEqual(m["content"], '"蓝色星球"邀请"弦"加入了群聊')

    def test_room_top_placeholder(self):
        m = parse_message(10000, TOPMSG_XML, 0)
        self.assertEqual((m["detail"]["kind"], m["content"]),
                         ("room_top", "[系统提示]"))

    def test_hongbao_markup(self):
        raw = ('<img src="SystemMessages_HongbaoIcon.png"/>  张三领取了你的'
               '<_wc_custom_link_ color="#FD9931" href="weixin://x">红包'
               '</_wc_custom_link_>')
        m = parse_message(10000, raw, 0)
        self.assertEqual((m["detail"]["kind"], m["content"]),
                         ("plain", "张三领取了你的红包"))

    def test_paymsg_cdata_markup(self):
        raw = ('<?xml version="1.0"?>\n<sysmsg type="paymsg"><content>'
               '<![CDATA[收款方24小时内未接收你的<_wc_custom_link_ '
               'href="weixin://wxpay/x&transferid=1">转账</_wc_custom_link_>，'
               '已过期]]></content></sysmsg>')
        m = parse_message(10000, raw, 0)
        self.assertEqual(m["content"], "收款方24小时内未接收你的转账，已过期")


class FallbackTest(unittest.TestCase):
    def test_malformed_xml_rescues_title(self):
        m = parse_text(5 << 32 | 49, "<msg><appmsg><title>标 题</title>")
        self.assertEqual(m["content"], "[文章] 标 题")
        self.assertEqual(m["detail"], {"title": "标 题"})

    def test_unknown_subtype_generic(self):
        m = parse_text(9999 << 32 | 49,
                       '<msg><appmsg><type>9999</type><title>t</title></appmsg></msg>')
        self.assertEqual((m["content"], m["detail"]), ("[应用消息] t", {"title": "t"}))


if __name__ == "__main__":
    unittest.main()
