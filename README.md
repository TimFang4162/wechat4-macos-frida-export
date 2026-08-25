# wechat4-macos-frida-export

macOS 平台微信（WeChat 4.x）本地聊天记录解密与导出工具。针对微信 4.1.10 起
的密钥存储机制变更，采用 Frida 运行时拦截方式获取各数据库的 SQLCipher 密钥，
随后完成解密与批量导出。

## 适用范围

- 操作系统：macOS（Apple Silicon 与 Intel）
- 微信版本：4.x。重点针对 4.1.10 及以上版本。自该版本起，既有方法失效：
  进程内存中不再出现 `x'<key><salt>'` 形式的密钥字符串，内存扫描无结果；
  二进制中 SQLCipher 符号被完全剥离，`sqlite3_key` 断点无法解析
- 系统完整性保护（SIP）须处于关闭状态。SIP 开启时外部进程无法读取微信
  进程内存；新版 macOS 的 App Management 亦不允许对 `/Applications` 下的
  微信原地重签名

验证环境：微信 4.1.13，macOS 27（Apple Silicon），SIP 关闭。23 个数据库中
22 个解密成功，358 个会话、约 70 万条消息完成导出。以上数据仅代表单一
环境的测试结果。

## 工作原理

微信 4.x 使用 WCDB（SQLCipher 4）加密本地数据库。加密参数：AES-256-CBC、
HMAC-SHA512、reserve=80、页大小 4096。每个数据库持有独立的 32 字节 raw key。

4.1.10 起，raw key 不再以字符串形式驻留可扫描内存，仅在加解密调用时经过
系统加密库。本工具的处理流程：

1. 以 Frida 附加微信进程，拦截 CommonCrypto 的 `CCCrypt`、`CCCryptorCreate`、
   `CCCryptorCreateWithMode` 与 `CCKeyDerivationPBKDF`。参数中的 32 字节密钥
   即数据库 raw key（`CCKeyDerivationPBKDF` 的 password 参数为同一密钥，
   用于派生 HMAC 校验密钥，rounds=2，SHA-512）
2. 重启微信，使全部数据库重新打开；密钥在派生与使用时刻被记录至
   `hunted_keys.txt`
3. 以候选密钥对各数据库首页做 HMAC-SHA512 校验，建立密钥与数据库的映射，
   生成 `all_keys.json`
4. 按页解密数据库，输出明文 SQLite 至 `decrypted/`
5. 批量读取全部会话，导出为 TXT / CSV / JSON

不使用 lldb 的原因：Xcode 自带的 lldb 在解析微信主程序 Mach-O 符号表时，
因导出 trie 递归过深而崩溃（`ObjectFileMachO::ParseSymtab →
ParseTrieEntries`，栈溢出），进程附加阶段即失败。

## 环境要求

- macOS，SIP 关闭（`csrutil status` 返回 disabled）
- 微信 4.x 桌面版，处于已登录状态
- Python 3.9+，Xcode Command Line Tools
- 一次管理员密码输入（密钥抓取阶段通过系统授权框提权）

## 使用方法

```bash
./run.sh
```

`run.sh` 依次执行：

1. 创建 `.venv` 并安装依赖（frida-tools、pycryptodome、zstandard）
2. 自动检测微信数据目录，生成 `config.json`
3. 运行 `hunt_keys.py`：弹出管理员授权框，Frida 附加微信进程并挂钩；
   随后重启微信以触发全部数据库重新打开；连续 45 秒未出现新密钥时自动
   停止并脱离。微信登录状态在重启后保留；若出现登录界面，手动确认一次
4. 运行 `map_keys.py`：验证并映射密钥
5. 运行 `decrypt_db.py`：解密全部数据库
6. 运行 `export_all.py`：批量导出全部会话

密钥未变化时（例如仅获取迁移后的新消息），可跳过抓取步骤：

```bash
./run.sh --no-hunt
```

### 分步执行

```bash
.venv/bin/python hunt_keys.py --restart   # 抓取密钥
.venv/bin/python map_keys.py              # 验证并映射
.venv/bin/python decrypt_db.py            # 解密
.venv/bin/python export_all.py            # 批量导出

.venv/bin/python export_chat.py --list    # 列出会话（按消息数排序）
.venv/bin/python export_chat.py --name "联系人昵称或备注" --output ./output
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

- 微信 4.x 约半数消息以 `WCDB_CT_message_content=4`（zstd）压缩存储，
  导出时全部解压为文本，不产生占位符
- 群聊发言人解析为联系人昵称；无法解析时保留 wxid
- 发送者为本账号的消息标注为「我」。账号通过跨会话发送频率统计自动识别
- 图片、语音、视频等媒体消息以 `[图片]`、`[语音]` 等占位符表示；
  媒体文件本体不在导出范围内
- 链接、小程序等应用消息保留原始 XML

## 文件说明

| 文件 | 说明 |
|------|------|
| `hunt_keys.py` | 密钥抓取器。非 root 运行时经 osascript 提权重启自身；支持重启微信、进程退出后自动重新附加、静默超时自动停止 |
| `keyhunt_frida.js` | Frida 注入脚本，拦截 CommonCrypto 各入口并上报密钥与 KDF 参数 |
| `map_keys.py` | 候选密钥逐库 HMAC-SHA512 验证，生成 `all_keys.json` |
| `decrypt_db.py` | SQLCipher 4 逐页解密器 |
| `export_all.py` | 全部会话批量导出 |
| `export_chat.py` | 单会话导出（派生自上游项目） |
| `config.py` | 配置加载与数据目录自动检测（macOS / Windows / Linux） |
| `run.sh` | 全流程入口 |

## 已知限制

- `migrate/unspportmsg.db` 未获得密钥，不解密；不影响消息导出
- 媒体文件（图片、语音、视频）本体不导出
- 密钥在微信更新、账号切换、聊天记录迁移（曾观察到 `message_resource.db`
  被重建并更换密钥）后可能变化，届时需重新运行 `./run.sh`
- 仅在本机数据上验证过；其他环境如出现问题，参照下节排查

## 故障排查

**`attach failed` 或未捕获任何密钥**
确认：SIP 处于关闭状态；微信正在运行；管理员授权框未被取消。详细过程见
`hunt.log`。

**`map_keys.py` 报告部分数据库未覆盖**
这些数据库在抓取窗口内未被微信打开。使用 `--restart` 参数重跑
`hunt_keys.py`，或在微信中打开对应功能（通讯录、收藏、朋友圈）后重跑。

**聊天记录从手机迁移后**
手机微信执行 设置 → 通用 → 聊天记录迁移与备份 → 迁移到电脑微信，
聊天范围与时间范围均选择全部；完成后重新运行 `./run.sh`。迁移是否完整
可通过对比导出索引 `index.csv` 中各会话的最早消息时间判断。

## 数据安全

`config.json`、`all_keys.json`、`hunted_keys.txt`、`hunt.log`、`decrypted/`、
`exported_all/` 均已列入 `.gitignore`，不会被提交。密钥文件与聊天记录
等同明文，请妥善保管项目目录。

## 致谢

- [ydotdog/wechat-export-macos](https://github.com/ydotdog/wechat-export-macos) —
  解密器与导出工具基础（`decrypt_db.py`、`export_chat.py`、`config.py` 由其派生）
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
- 本项目与腾讯公司无关，未获腾讯公司授权或认可。拦截运行中的微信进程、
  关闭 SIP 等操作可能违反《微信软件使用许可协议》或相关服务条款，理论上
  存在账号被限制的风险，使用者应自行评估
- 严禁将本项目用于未经授权访问他人账户、窃取他人数据、商业取证或其他
  非法用途

## License

WTFPL - Do What The Fuck You Want To Public License.
