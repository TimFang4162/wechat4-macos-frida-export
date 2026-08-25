# wechat-frida-export

macOS 微信聊天记录一键解密导出工具。支持 **微信 4.1.10+（含 4.1.13）**——即进程内存
不再缓存 `x'<hexkey>'` 密钥串、传统内存扫描方案全部失效的新版本。

```
微信进程 ──Frida hook──▶ 32字节raw key ──HMAC验证──▶ all_keys.json
(CommonCrypto)            (CCCryptorCreate等)        (逐库映射)
        ──▶ 明文 SQLite ──▶ TXT / CSV / JSON（全部会话批量导出）
```

## 与旧方案的区别（为什么需要这个工具）

微信 4.1.10 起（macOS）：

- **内存扫描 `x'<key><salt>'` 失效** —— 密钥字符串不再常驻内存
- **`sqlite3_key` lldb 断点失效** —— 符号被完全剥离
- **Xcode lldb 直接 attach 会崩溃** —— 解析微信巨型 Mach-O 符号表时递归爆栈
  （`ObjectFileMachO::ParseSymtab → ParseTrieEntries`）

本工具改用 **Frida hook CommonCrypto**：微信的 WCDB 以 CommonCrypto 为 AES 后端，
派生后的 per-DB raw key 必然经过 `CCCrypt` / `CCCryptorCreate` /
`CCCryptorCreateWithMode`；`CCKeyDerivationPBKDF`（mac key 派生，rounds=2）也会
带出 raw key。重启微信让全部数据库重新打开，即可一次性抓齐所有库的密钥。

## 前置条件

- macOS（Apple Silicon / Intel），**SIP 已关闭**（`csrutil status` 为 disabled）。
  SIP 开启时无法读取其他进程内存（除非重签微信，但新版 macOS 无法原地重签）
- 微信 4.x 桌面版已登录
- Python 3.9+，Xcode Command Line Tools
- 一次管理员密码（抓密钥时弹系统授权框）

## 快速开始

```bash
./run.sh
```

一条命令完成全部流程：

1. 创建 venv 并安装依赖（frida / pycryptodome / zstandard）
2. 自动检测微信数据目录，生成 `config.json`
3. 弹出管理员授权框 → Frida 挂到微信进程挂钩子 → **自动重启微信**触发全部
   数据库重新打开 → 抓齐密钥（静默 45 秒无新密钥自动停止）
4. 逐库 HMAC 验证映射密钥 → `all_keys.json`
5. 逐页解密 SQLCipher 4 数据库 → `decrypted/`
6. 批量导出全部会话 → `exported_all/`

> 微信重启说明：需要触发数据库重新打开才能抓齐密钥；登录状态自动保留，
> 若弹出登录界面点一下即可。

### 只重新导出（密钥未变时）

```bash
./run.sh --no-hunt
```

### 分步执行

```bash
.venv/bin/python hunt_keys.py --restart   # 抓密钥（自动弹授权框）
.venv/bin/python map_keys.py              # 验证并映射密钥
.venv/bin/python decrypt_db.py            # 解密全部数据库
.venv/bin/python export_all.py            # 批量导出全部会话

# 单会话工具（模糊搜索昵称/备注）
.venv/bin/python export_chat.py --list
.venv/bin/python export_chat.py --name "张三" --output ~/Downloads/张三
```

## 输出

```
exported_all/
├── index.csv            # 总索引：显示名/用户名/消息数/时间范围/目录
└── chats/<会话名>/
    ├── chat.txt         # 可直接阅读（[时间] 发送者: 内容）
    ├── chat.csv         # Excel/Numbers 可打开
    └── chat.json        # 结构化数据
```

导出特性：

- **zstd 压缩消息解码** —— 微信 4.x 约一半消息以 `WCDB_CT=4`（zstd）压缩存储，
  本工具全部解压为可读文本，不丢消息
- **群聊发言人显示昵称**（通过联系人库解析 wxid）
- **账号主人自动识别**（跨会话出现频率最高的发送者，标注为「我」）

## 文件说明

| 文件 | 说明 |
|------|------|
| `hunt_keys.py` | Frida 密钥抓取器，自动经 osascript 提权，支持重启微信、静默自动停止 |
| `keyhunt_frida.js` | 注入脚本：hook CCCrypt / CCCryptorCreate / CCKeyDerivationPBKDF |
| `map_keys.py` | 候选密钥逐库 HMAC-SHA512 验证，生成 `all_keys.json` |
| `decrypt_db.py` | SQLCipher 4 逐页解密（AES-256-CBC, HMAC-SHA512, reserve=80） |
| `export_all.py` | 全部会话批量导出（TXT/CSV/JSON + zstd 解码 + 群昵称解析） |
| `export_chat.py` | 单会话导出工具（沿用上游） |
| `config.py` | 配置加载，macOS/Windows/Linux 数据目录自动检测 |
| `run.sh` | 一键全流程 |

## 常见问题

**Q: 密钥会变吗？什么时候要重抓？**
已有数据库的密钥不变；微信更新、换设备登录、聊天记录从手机迁移（可能重建
某些库，实测 `message_resource.db` 被换过密钥）后建议重跑 `./run.sh`。

**Q: 从手机迁移聊天记录到 Mac 后怎么办？**
手机微信 → 设置 → 通用 → 聊天记录迁移与备份 → 迁移到电脑微信（聊天选全部、
时间选全部时间）。迁完重跑 `./run.sh` 即可。

**Q: `attach failed` / 抓不到任何密钥？**
确认 (1) SIP 已关闭 `csrutil status`；(2) 微信正在运行；(3) 用的是本目录
`.venv` 里的 frida；(4) 管理员密码已输入。仍失败时看 `hunt.log`。

**Q: map_keys 提示某些库未覆盖？**
那些库本次会话未被微信打开。在微信里打开对应功能（通讯录/收藏/朋友圈等）
或直接用 `--restart` 重跑；无关紧要的库（如 `migrate/unspportmsg.db`）可忽略。

**Q: 导出的媒体消息是什么样？**
图片/语音/视频等显示为 `[图片]` `[语音]` 占位符；媒体文件本体在微信数据目录
的 `Message/` 下，后续版本可能支持关联导出。

**Q: 数据安全？**
`all_keys.json`、`hunted_keys.txt`、`decrypted/`、`exported_all/` 均已在
`.gitignore` 中，不会被误提交。密钥与聊天记录等同明文，注意保管。

## 致谢

- [ydotdog/wechat-export-macos](https://github.com/ydotdog/wechat-export-macos) —
  解密器与导出工具基础（本仓库 decrypt_db / export_chat / config 由其派生，WTFPL）
- [Evanyuan-builder/wechat-4.1.10-macos-key](https://github.com/Evanyuan-builder/wechat-4.1.10-macos-key) —
  CCCrypt hook 思路与寄存器布局（其 issue 记录了 4.1.10 密钥机制变化）
- [Thearas/wechat-db-decrypt-macos](https://github.com/Thearas/wechat-db-decrypt-macos)、
  [TANGandXUE/wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool) —
  4.1+ 密钥机制变化的分析

实测环境：微信 4.1.13 / macOS 27 (Apple Silicon) / SIP off —— 23 库 22 解密成功，
358 会话 70 万条消息完整导出。

## License

WTFPL - Do What The Fuck You Want To Public License.
