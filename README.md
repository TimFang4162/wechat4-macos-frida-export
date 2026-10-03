# wechat4-macos-frida-export

macOS 平台微信（WeChat 4.x）本地聊天记录解密与导出工具。针对微信 4.1.10 起
的密钥存储机制变更，采用 Frida 运行时拦截方式获取各数据库的 SQLCipher 密钥，
随后完成解密与批量导出。

## 适用范围

- 操作系统：macOS（Apple Silicon 与 Intel）
- 微信版本：4.x。重点针对 4.1.10 及以上版本。自该版本起，既有方法失效：
  进程内存中不再出现 `x'<key><salt>'` 形式的密钥字符串，内存扫描无结果；
  二进制中 SQLCipher 符号被完全剥离，`sqlite3_key` 断点无法解析
- 支持 SIP 保持开启。工具不再对官方微信执行外部 `task_for_pid` 附加，而是在
  项目 `.runtime/` 下构建内嵌 Frida Gadget 的独立调试副本；
  `/Applications/WeChat.app` 全程只读。调试副本使用独立 Bundle ID
  `io.github.timfang4162.wechat4-macos-frida-export.sip`，避免与官方微信的
  TCC 身份冲突

验证环境：微信 4.1.13，macOS 27（Apple Silicon），SIP 开启。

## 工作原理

微信 4.x 使用 WCDB（SQLCipher 4）加密本地数据库。加密参数：AES-256-CBC、
HMAC-SHA512、reserve=80、页大小 4096。每个数据库持有独立的 32 字节 raw key。

4.1.10 起，raw key 不再以字符串形式驻留可扫描内存，仅在加解密调用时经过
系统加密库。本工具的处理流程：

1. `wxexport.sip.prepare` 复制官方微信，向双架构 Mach-O 加入
   `LC_LOAD_DYLIB`，嵌入 Frida Gadget 并按嵌套代码对象恢复运行所需
   entitlement 后执行严格重签名校验
2. `wxexport.keys.hunt` 在调试副本的独立容器内建立正式
   `xwechat_files` 的 APFS 写时复制快照。这使调试副本打开与正式库
   相同的密文数据，同时避免 macOS App Data 对跨容器符号链接的拒绝。
   快照文件在首次修改前共享数据块，不会立即额外占用整个数据目录的空间
3. Gadget 在 `127.0.0.1:27042` 等待控制器；控制器装载 Hook 后微信
   才继续初始化。Hook 拦截 CommonCrypto 的 `CCCrypt`、`CCCryptorCreate`、
   `CCCryptorCreateWithMode` 与 `CCKeyDerivationPBKDF`，将 32 字节 raw key 写入
   `hunted_keys.txt`
4. 以候选密钥对各数据库首页做 HMAC-SHA512 校验，建立密钥与数据库的映射，
   生成 `all_keys.json`
5. 按页解密数据库，输出明文 SQLite 至 `decrypted/`
6. 批量读取全部会话，导出为 TXT / CSV / JSON

不使用 lldb 的原因：Xcode 自带的 lldb 在解析微信主程序 Mach-O 符号表时，
因导出 trie 递归过深而崩溃（`ObjectFileMachO::ParseSymtab →
ParseTrieEntries`，栈溢出），进程附加阶段即失败。

## 环境要求

- macOS，SIP 可保持开启（已在 `csrutil status` 为 enabled 时验证）
- 微信 4.x 桌面版，处于已登录状态
- [uv](https://docs.astral.sh/uv/)（依赖与虚拟环境由其托管），Xcode Command Line Tools
- macOS 26/27：为运行本项目的终端/Codex授予“完全磁盘访问权限”，
  以便控制端读取官方微信容器并建立本地快照。TCC 与 SIP 是两套机制
- 调试副本使用独立签名身份，首次启动需单独扫码登录一次；后续运行复用该副本
  的登录状态

## 使用方法

```bash
./run.sh
```

脚本会自动读取 `csrutil status`。SIP 开启时选择内嵌 Frida Gadget 链路，
不调用 `task_for_pid`、不请求 root；SIP 关闭时仍复用同一 Gadget 链路，避免维护
两套密钥抓取实现。除完全磁盘访问权限和首次独立登录外，无需手动选择运行模式。

直接导出到桌面指定目录：

```bash
./run.sh --output ~/Desktop/WeChat-Export
```

`run.sh` 依次执行：

1. 自动识别 SIP 状态并选择无需 root 的内嵌 Gadget 链路
2. 用 `uv sync` 创建 `.venv` 并安装锁定版本的依赖
3. 自动检测微信数据目录，生成 `config.json`
4. 运行 `wxexport.sip.prepare`，生成 `.runtime/WeChat-SIP.app`。只有微信版本或
   主程序摘要变化时才重建
5. 运行 `wxexport.keys.hunt`：正常退出官方微信，首次运行时建立 APFS 数据快照，
   启动 Gadget 调试副本并在初始化前装载 Hook；如果独立登录态失效，
   在弹出的调试副本中扫码一次。连续 45 秒未出现新密钥时自动停止
6. 运行 `wxexport.keys.map`：验证并映射密钥
7. 运行 `wxexport.decrypt`：解密全部数据库
8. 运行 `wxexport.export.all`：批量导出全部会话

密钥未变化时，可跳过抓取步骤：

```bash
./run.sh --no-hunt
```

微信升级、账号切换、聊天记录迁移或密钥轮换后，刷新调试容器内的 APFS 快照：

```bash
./run.sh --refresh-snapshot --output ~/Desktop/WeChat-Export-New
```

`--refresh-snapshot` 会先完成新快照，再原子替换并清理旧快照，不能与
`--no-hunt` 或 `--no-restart` 同时使用。运行 `./run.sh --help` 可查看全部参数。

### 分步执行

```bash
uv sync                                        # 安装依赖（自动创建 .venv）
uv run python -m wxexport.sip.prepare          # 构建/更新 Gadget 调试副本
uv run python -m wxexport.keys.hunt --restart  # 抓取密钥
uv run python -m wxexport.keys.map             # 验证并映射
uv run python -m wxexport.decrypt              # 解密
uv run python -m wxexport.export.all           # 批量导出

uv run python -m wxexport.export.chats --list  # 列出会话（按消息数排序）
uv run python -m wxexport.export.chats --name "联系人昵称或备注" --out ./output
```

## 输出

```
exported_all/
├── index.csv            # 会话索引：显示名、用户名、消息数、时间范围、目录
└── chats/<会话名>/
    ├── chat.txt         # 纯文本，格式为 [时间] 发送者: 内容
    ├── chat.csv         # 表格，UTF-8 BOM，Excel / Numbers 可直接打开
    └── chat.json        # 结构化数据
```

导出内容说明：

- 微信 4.x 部分消息以 `WCDB_CT_message_content=4`（zstd）压缩存储，
  导出时全部解压为文本，不产生占位符
- 群聊发言人解析为联系人昵称；无法解析时保留 wxid；群消息内容中的
  "发送者:" 前缀自动去除
- 发送者为本账号的消息标注为「我」。账号通过跨会话发送频率统计自动识别
- 图片、语音、视频等媒体消息以 `[图片]`、`[语音]` 等占位符表示；
  媒体文件本体不在导出范围内
- 应用消息（type 49）按子类型渲染为可读文本：合并转发的聊天记录展开为
  `[聊天记录] 标题（N条）` 并逐条列出摘要（本机数据即可完成，无需联网）；
  引用回复显示为 `回复内容（引用 昵称: 被引内容）`；转账/红包显示金额与
  留言；文件显示大小；视频号、小程序、公众号文章、接龙、群公告、位置、
  名片、通话、拍一拍等各取关键字段
- 系统通知渲染为纯文本：撤回提醒、群成员变动（模板占位符展开）、
  红包/转账领取提醒等，不透出内部 XML 标记
- JSON 输出中每条消息含 `type`（原始 64 位类型）与 `subtype`（appmsg 子类型）
- appmsg 子类型 40 的新版聊天记录本地仅存储摘要，导出为标题加摘要片段

## 文件说明

```
├── run.sh                     # 全流程入口
├── pyproject.toml / uv.lock   # 依赖与 Python 版本（uv 托管）
└── src/wxexport/
    ├── config.py              # 配置加载、项目根路径、微信数据目录自动检测
    ├── check_permissions.py   # 导出前检查 TCC/完全磁盘访问权限
    ├── msgparse.py            # 消息类型解码与内容渲染（64 位 local_type、appmsg 各子类型）
    ├── decrypt.py             # SQLCipher 4 逐页解密器；合并 -wal 未落盘帧，--db-dir 指定快照源
    ├── sip/
    │   ├── prepare.py         # SIP 模式构建器：下载 Gadget、复制官方 App、注入、重签名
    │   ├── macho_inject.py    # 无依赖的 Mach-O LC_LOAD_DYLIB 注入器
    │   └── config.py          # 调试副本路径与身份常量
    ├── keys/
    │   ├── hunt.py            # 无 root 的 Gadget 控制器；APFS 数据快照、loopback 连接、静默超时
    │   ├── hook.js            # Gadget 脚本，拦截 CommonCrypto 各入口上报候选密钥
    │   ├── map.py             # 候选密钥逐库 HMAC-SHA512 验证，生成 all_keys.json
    │   └── utils.py           # all_keys.json 读写辅助
    └── export/
        ├── all.py             # 全部会话批量导出（TXT/CSV/JSON + index.csv）
        └── chats.py           # 指定会话导出与会话列表（--username/--name/--list）
```

测试：`uv run python -m unittest discover -s tests`（消息渲染回归用例）。

## 已知限制

- `migrate/unspportmsg.db` 未获得密钥，不解密；不影响消息导出
- `biz_message_0.db`（公众号消息）不在导出范围内
- 媒体文件（图片、语音、视频）本体不导出
- 密钥在微信更新、账号切换、聊天记录迁移后可能变化，届时需重新运行 `./run.sh`
- 调试副本为 ad-hoc 签名，不应替代官方微信长期日常使用；官方 App 不会被修改
- APFS 快照是抓取密钥时的数据视图；微信升级、账号迁移或密钥变化后需使用
  `--refresh-snapshot` 更新
- 仅在本机数据上验证过；其他环境如出现问题，参照下节排查

## 故障排查

**Gadget 在微信初始化阶段断开**
在“系统设置 → 隐私与安全性 → 完全磁盘访问权限”中添加
当前运行项目的 Terminal/Codex。重新授权后需先退出残留微信进程再运行。
详细过程见 `hunt.log`。

**微信提示“储存位置不可用”**
旧版调试容器中可能保留了跨容器符号链接。退出调试副本并重新运行
`./run.sh`；新流程会撤销该链接并自动建立 APFS 写时复制快照。

**无法连接 `127.0.0.1:27042`**
确认没有其他 Frida 服务占用端口，并运行
`uv run python -m wxexport.sip.prepare --force` 重建签名树。

**`wxexport.keys.map` 报告部分数据库未覆盖**
这些数据库在抓取窗口内未被微信打开。使用 `--restart` 参数重跑
`wxexport.keys.hunt`，或在微信中打开对应功能（通讯录、收藏、朋友圈）后重跑。

**聊天记录从手机迁移后**
手机微信执行 设置 → 通用 → 聊天记录迁移与备份 → 迁移到电脑微信，
聊天范围与时间范围均选择全部；完成后重新运行 `./run.sh`。迁移是否完整
可通过对比导出索引 `index.csv` 中各会话的最早消息时间判断。

## 致谢

- [ydotdog/wechat-export-macos](https://github.com/ydotdog/wechat-export-macos) —
  解密器与导出工具基础（`wxexport.decrypt`、`wxexport.config` 由其派生）
- [Evanyuan-builder/wechat-4.1.10-macos-key](https://github.com/Evanyuan-builder/wechat-4.1.10-macos-key) —
  CommonCrypto 拦截思路与参数寄存器布局；其 issue 记录了 4.1.10 密钥机制变化
- [Thearas/wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos)、
  [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) —
  4.1+ 密钥机制变化的分析

## 免责声明

- 本项目仅面向导出使用者本人微信账号聊天记录的场景（个人备份、迁移与
  数据留存），不用于获取他人数据
- 使用者应确保行为符合所在司法辖区的法律法规，并自行承担使用本项目产生
  的一切后果
- 本项目以「按原样」（AS IS）提供，不附带任何形式的明示或默示担保，
  作者不对任何直接或间接损失承担责任
- 本项目与腾讯公司无关，未获腾讯公司授权或认可。运行重签名调试副本、
  拦截微信进程等操作可能违反《微信软件使用许可协议》或相关服务条款，理论上
  存在账号被限制的风险，使用者应自行评估
- 严禁将本项目用于未经授权访问他人账户、窃取他人数据、商业取证或其他
  非法用途

## License

WTFPL - Do What The Fuck You Want To Public License.
