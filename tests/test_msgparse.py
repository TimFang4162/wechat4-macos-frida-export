"""msgparse 渲染器的回归测试。fixture 为按真实结构构造的合成 XML。"""
import unittest

from wxexport.msgparse import (decode_local_type, render_message,
                               strip_sender_prefix, type_name)

RECORD_XML = """<msg><appmsg><title>群聊的聊天记录</title><des>甲: [图片]</des>
<type>19</type><recorditem><![CDATA[<recordinfo><isChatRoom>1</isChatRoom>
<datalist count="3">
<dataitem datatype="1"><sourcename>甲</sourcename><datadesc>你今天去不去打针</datadesc></dataitem>
<dataitem datatype="2"><datafmt>pic</datafmt><sourcename>甲</sourcename></dataitem>
<dataitem datatype="9"><sourcename>乙</sourcename></dataitem>
</datalist></recordinfo>]]></recorditem></appmsg></msg>"""

QUOTE_TEXT_XML = """<msg><appmsg><title>应该是民办吧</title><type>57</type>
<refermsg><type>1</type><displayname>作弊式.</displayname>
<content>听说你们学校是民办</content></refermsg></appmsg></msg>"""

QUOTE_MEDIA_XML = """<msg><appmsg><title>看看这个</title><type>57</type>
<refermsg><type>3</type><displayname>小张</displayname>
<content><![CDATA[<msg><img aeskey="x" encryver="1"/></msg>]]></content>
</refermsg></appmsg></msg>"""

QUOTE_APPMSG_XML = """<msg><appmsg><type>57</type>
<refermsg><type>49</type><displayname>小李</displayname><content>&lt;msg&gt;
&lt;appmsg&gt;&lt;title&gt;聊聊&lt;/title&gt;&lt;type&gt;19&lt;/type&gt;
&lt;recorditem&gt;&amp;lt;recordinfo&amp;gt;&amp;lt;datalist count="1"&amp;gt;
&amp;lt;dataitem datatype="1"&amp;gt;&amp;lt;sourcename&amp;gt;小李&amp;lt;/sourcename&amp;gt;
&amp;lt;datadesc&amp;gt;第一句&amp;lt;/datadesc&amp;gt;&amp;lt;/dataitem&amp;gt;
&amp;lt;/datalist&amp;gt;&amp;lt;/recordinfo&amp;gt;&lt;/recorditem&gt;
&lt;/appmsg&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>"""

TRANSFER_XML = """<msg><appmsg><title>微信转账</title><type>2000</type>
<wcpayinfo><paysubtype>1</paysubtype><feedesc><![CDATA[￥115.30]]></feedesc>
</wcpayinfo></appmsg></msg>"""

FILE_XML = """<msg><appmsg><title>报告.pptx</title><type>6</type>
<appattach><totallen>2048000</totallen><fileext>pptx</fileext></appattach>
</appmsg></msg>"""

LOCATION_XML = """<msg><location x="30.1" y="120.2" label="某某路9号"
poiname="某餐厅" fromusername="wxid_a"/></msg>"""

VOIP_XML = """<voipmsg type="VoIPBubbleMsg"><VoIPBubbleMsg>
<msg><![CDATA[已在其它设备接听]]></msg><duration>75</duration></VoIPBubbleMsg></voipmsg>"""

CARD_XML = """<msg bigheadimgurl="http://x/0" nickname="某单位" alias="ops"/>"""

FINDER_XML = """<msg><appmsg><title></title><type>51</type>
<finderFeed><nickname>某博主</nickname><desc><![CDATA[视频描述文本]]></desc>
</finderFeed></appmsg></msg>"""


class DecodeTest(unittest.TestCase):
    def test_local_type_roundtrip(self):
        base, sub = decode_local_type((19 << 32) | 49)
        self.assertEqual((base, sub), (49, 19))
        self.assertEqual(decode_local_type(1), (1, 0))

    def test_type_name(self):
        self.assertEqual(type_name(1, 0), "文本")
        self.assertEqual(type_name(49, 19), "聊天记录")
        self.assertEqual(type_name(49, 2000), "转账")
        self.assertIn("应用消息", type_name(49, 9999))
        self.assertIn("未知", type_name(777, 0, 777))


class PrefixTest(unittest.TestCase):
    def test_room_prefix(self):
        self.assertEqual(strip_sender_prefix("wxid_abc:\n你好"),
                         "你好")
        self.assertEqual(strip_sender_prefix("wxid_abc:\n<img aeskey=\"x\"/>"),
                         '<img aeskey="x"/>')

    def test_single_chat_untouched(self):
        self.assertEqual(strip_sender_prefix("备注:\n第二行"), "备注:\n第二行")
        self.assertEqual(strip_sender_prefix("wxid_abc:\n备注:\n第二行"),
                         "备注:\n第二行")
        self.assertIsNone(strip_sender_prefix(None))


class RenderTest(unittest.TestCase):
    def test_system_paymsg_cdata_markup(self):
        raw = ('<?xml version="1.0"?>\n<sysmsg type="paymsg"><content>'
               '<![CDATA[收款方24小时内未接收你的<_wc_custom_link_ '
               'href="weixin://wxpay/x&transferid=1">转账</_wc_custom_link_>，'
               '已过期]]></content></sysmsg>')
        self.assertEqual(render_message(10000, 0, raw),
                         "收款方24小时内未接收你的转账，已过期")

    def test_system_revoke(self):
        xml = ('<?xml version="1.0"?><sysmsg type="revokemsg"><revokemsg>'
               '<content>"甲" 撤回了一条消息</content><revoketime>0</revoketime>'
               '</revokemsg></sysmsg>')
        self.assertEqual(render_message(10000, 0, xml), '"甲" 撤回了一条消息')

    def test_system_template_notice(self):
        xml = ('<sysmsg type="sysmsgtemplate"><sysmsgtemplate>'
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
        self.assertEqual(render_message(10000, 0, xml),
                         '"蓝色星球"邀请"弦"加入了群聊')

    def test_system_hongbao_markup(self):
        raw = ('<img src="SystemMessages_HongbaoIcon.png"/>  张三领取了你的'
               '<_wc_custom_link_ color="#FD9931" href="weixin://x">红包'
               '</_wc_custom_link_>')
        self.assertEqual(render_message(10000, 0, raw), "张三领取了你的红包")

    def test_quote_garbage_ref_type_sniffs_content(self):
        xml = ('<msg><appmsg><title>生日礼物咋样</title><type>57</type>'
               '<refermsg><type>-2113929167</type><displayname>KD.</displayname>'
               '<content>&lt;msg&gt;&lt;appmsg&gt;&lt;title&gt;微信礼物&lt;/title&gt;'
               '&lt;des&gt;当前版本暂不支持查看礼物&lt;/des&gt;&lt;type&gt;124&lt;/type&gt;'
               '&lt;/appmsg&gt;&lt;/msg&gt;</content></refermsg></appmsg></msg>')
        out = render_message(49, 57, xml)
        self.assertEqual(out, "生日礼物咋样（引用 KD.: [微信礼物] 当前版本暂不支持查看礼物）")

    def test_text_and_media(self):
        self.assertEqual(render_message(1, 0, "hello"), "hello")
        self.assertEqual(render_message(1, 0, None), "")
        self.assertEqual(render_message(3, 0, "<msg><img/></msg>"), "[图片]")
        self.assertEqual(render_message(34, 0, ""), "[语音]")

    def test_merged_record(self):
        out = render_message(49, 19, RECORD_XML)
        self.assertTrue(out.startswith("[聊天记录] 群聊的聊天记录（3条）"))
        self.assertIn("  甲: 你今天去不去打针", out)
        self.assertIn("  甲: [图片]", out)
        self.assertIn("  乙: [记录(9)]", out)

    def test_quote_text(self):
        out = render_message(49, 57, QUOTE_TEXT_XML)
        self.assertEqual(out, "应该是民办吧（引用 作弊式.: 听说你们学校是民办）")

    def test_quote_location(self):
        xml = ('<msg><appmsg><title>在吗</title><type>57</type>'
               '<refermsg><type>48</type><displayname>小张</displayname>'
               '<content>&lt;msg&gt;&lt;location x="1" y="2" '
               'poiname="某餐厅" label="某路"/&gt;&lt;/msg&gt;</content>'
               '</refermsg></appmsg></msg>')
        self.assertEqual(render_message(49, 57, xml),
                         "在吗（引用 小张: [位置] 某餐厅）")

    def test_quote_media(self):
        out = render_message(49, 57, QUOTE_MEDIA_XML)
        self.assertEqual(out, "看看这个（引用 小张: [图片]）")

    def test_quote_nested_appmsg(self):
        out = render_message(49, 57, QUOTE_APPMSG_XML)
        self.assertEqual(out, "引用 小李: [聊天记录] 聊聊（1条） / 小李: 第一句")

    def test_pay(self):
        out = render_message(49, 2000, TRANSFER_XML)
        self.assertEqual(out, "[转账] ￥115.30")
        self.assertEqual(render_message(
            49, 2001, '<msg><appmsg><type>2001</type>'
                      '<des>我给你发了一个红包</des></appmsg></msg>'),
            "[红包] 我给你发了一个红包")

    def test_file_with_size(self):
        self.assertEqual(render_message(49, 6, FILE_XML), "[文件] 报告.pptx（2.0MB）")

    def test_location_card_voip_finder(self):
        self.assertEqual(render_message(48, 0, LOCATION_XML), "[位置] 某餐厅")
        self.assertEqual(render_message(42, 0, CARD_XML), "[名片] 某单位")
        self.assertEqual(render_message(50, 0, VOIP_XML),
                         "[语音/视频通话] 已在其它设备接听（1分15秒）")
        self.assertEqual(render_message(49, 51, FINDER_XML),
                         "[视频号] 某博主: 视频描述文本")

    def test_pat_returns_bare_title(self):
        xml = '<msg><appmsg><type>62</type>' \
              '<title>"甲" 拍了拍 "乙"</title></appmsg></msg>'
        self.assertEqual(render_message(49, 62, xml), '"甲" 拍了拍 "乙"')

    def test_malformed_xml_falls_back_to_title(self):
        self.assertEqual(render_message(49, 5, "<msg><appmsg><title>标 题</title>"),
                         "标 题")

    def test_unknown_subtype_generic(self):
        xml = '<msg><appmsg><type>9999</type><title>t</title></appmsg></msg>'
        self.assertEqual(render_message(49, 9999, xml), "[应用消息] t")
        self.assertEqual(render_message(49, 9999, "<msg><appmsg></appmsg></msg>"),
                         "[应用消息]")


if __name__ == "__main__":
    unittest.main()
