# WuppoRelay 架构说明

> 面向项目维护者，帮助快速理解"每一块是干什么的、以后改东西应该去哪里"。
> 以当前实际代码为准。

---

## 1. 项目整体结构

```
Wuppo/
├── bot.py                    机器人入口（NoneBot 启动、适配器注册、日志轮转）
├── plugins/                  NoneBot 插件目录（所有转发逻辑都在这里）
│   ├── config.py             配置读取 + 内存缓存（settings.json 的消费者）
│   ├── relay.py              Discord → QQ 实时转发主逻辑
│   ├── command.py            QQ 私聊 relay 命令（手动转发）
│   ├── manage.py             QQ 私聊管理命令（status/restart/mode 等）
│   ├── registration.py       QQ 注册指令（register / register-group）+ 申请存储
│   ├── fetch.py              Discord 消息抓取 + 渲染（统一解析层）
│   ├── sender.py             QQ 群发送（重试、分片、降级）
│   ├── media.py              文本美化 + Emoji 解析 + 媒体下载
│   ├── history.py            去重记录读写（discord_last.json）
│   ├── dedup.py              去重判断逻辑（纯函数，不碰 I/O）
│   ├── filter.py             频道消息筛选（纯函数，relay/backfill 共用）
│   ├── backfill.py           启动后自动补发离线期间缺失的消息
│   ├── backfill_api.py       补发相关的 HTTP 接口（供面板调用）
│   ├── health_api.py         Bot 健康状态接口（供面板显示连接状态）
│   ├── identities.py         OpenID 身份资料库（昵称/群名/备注）
│   ├── stats.py              转发统计（成功/失败/当日条数）
│   └── json_io.py            JSON 原子读写工具
├── panel/
│   ├── 管理面板.pyw           管理面板主程序（FastAPI + 进程管理）
│   ├── autostart.pyw         开机自启入口
│   ├── restart_worker.pyw    面板重启 worker
│   └── web/                  面板前端（纯静态 HTML/JS/CSS）
│       ├── index.html
│       ├── app.js
│       └── style.css
├── scripts/                  独立诊断脚本（如 diagnose_channels.py）
├── data/                     运行时数据（settings、去重记录、统计等）
├── docs/                     文档（SETUP.md、CHANNELS.md）
├── .env.prod                 环境变量（Discord Token、QQ AppID 等，不进版本库）
├── .env.example              环境变量示例
└── pyproject.toml            项目依赖
```

---

## 2. 各模块职责总览

| 模块 | 职责 |
|------|------|
| **Discord 侧** | 接收 Discord 消息事件、通过 REST API 抓取消息内容、下载媒体 |
| **QQ 侧** | 向 QQ 群发送消息、接收 QQ 私聊命令、自动发现群/用户 OpenID |
| **Relay** | 把 Discord 消息转发到 QQ 群（唯一方向，不做 QQ → Discord） |
| **管理面板** | 本地 Web 界面，管理群/频道/权限的启用状态、启停机器人、查看日志 |
| **数据文件** | JSON 格式的配置、去重记录、统计、身份信息，是 bot 与面板之间的 IPC 通道 |

---

## 3. Discord → QQ 实时转发流程

```
Discord 消息事件
    │
    ▼
plugins/relay.py
    │ ① 检查频道是否启用（config.get_active_channels）
    │ ② 检查是否有启用的 QQ 群（config.get_active_groups）
    │ ③ 去重判断（dedup.select_target_groups）
    │    └── 读取 history.load_last_messages()
    │    └── 按群分别判断：该消息是否已经发过
    │
    ▼
plugins/fetch.py
    │ ④ normalize_event() 把事件对象归一化为 dict
    │ ⑤ build_parts() 渲染为 QQ 消息段
    │    └── 文字：@提及转换、Markdown 清理、Emoji 提取
    │    └── 媒体：并发下载图片/音频（media.fetch_bytes_many）
    │    └── 失败降级：下载失败 → 文字 + 原链接
    │
    ▼
plugins/sender.py
    │ ⑥ send_relay_message()
    │    └── 文字超长自动分片（3900 字符/片）
    │    └── 媒体上传失败 → 降级为链接
    │    └── 多群并行发送（asyncio.gather）
    │    └── 429 限流退避重试（最多 3 次）
    │    └── 返回 {群openid: 是否送达}
    │
    ▼
plugins/relay.py
    │ ⑦ 只给发送成功的群记录去重 ID（dedup.apply_success）
    │ ⑧ 保存到 history.save_last_messages()
    │ ⑨ 记录统计（stats.record）
    └── 失败的群不记录 → 下次补发时只重试失败群
```

---

## 4. Discord → QQ 启动补发流程

```
Discord Bot 连接成功（on_bot_connect 事件）
    │
    ▼
plugins/backfill.py → _backfill_missed()
    │ ① 检查补发总开关（config.get_backfill_enabled）
    │ ② 遍历每个启用频道
    │    └── 读取去重记录，算各群最落后的 last_id（dedup.compute_base_id）
    │    └── 无记录（首次启用）→ 只建基线，不补发，防刷屏
    │    └── 有记录 → REST 抓取 base_id 之后的消息（fetch.fetch_channel_messages_after）
    │    └── 按时间正序逐条转发，走 build_parts → send_relay_message
    │    └── 每条最多补发 get_backfill_limit() 条（默认 10）
    │    └── 逐条重读去重记录，实时转发已处理的群自动跳过
    └── 与实时转发共用去重记录，正常情况下不会重复发送
```

> 去重竞态说明：on_bot_connect 会同时放行实时接收与补发任务，连接瞬间
> 新建的消息存在极小竞态窗口——实时路径媒体下载未完成、去重记录尚未
> 落盘时，补发逐条重读记录也看不到它，可能被两条路径各发送一次。
> 该窗口目前未修复，仅影响启动瞬间新建的消息。
>
> 抓取失败语义：分页抓取任一页失败（请求异常/超时/非 200/429 重试耗尽）
> 时整轮放弃并返回空，绝不返回不完整列表推进去重游标，宁可下一轮重新
> 补发；单次收集超过上限（1000 条）且未确认翻到缺口底部时同样整轮放弃，
> 极大缺口需在面板「清除待补发」处置。补发翻页请求间隔 0.5s，429 按
> Retry-After 退避重试。

---

## 5. QQ → Bot 私聊命令流程

```
QQ 私聊消息
    │
    ├── 以 "relay " 开头 ──────────────► plugins/command.py
    │                                      │ ① 白名单校验（config.get_active_user_openids）
    │                                      │ ② 未授权 → 回复拒绝
    │                                      │ ③ Discord 链接 → fetch_message + build_parts
    │                                      │    普通文字 → 直接构造消息
    │                                      └── ④ send_relay_message → QQ 群
    │
    ├── 以 "register" 开头 ────────────► plugins/registration.py
    │                                      │ 用户注册（不需白名单）：
    │                                      │    register <QQ号> <昵称>
    │                                      │ 绑定当前 User OpenID 写入
    │                                      └── data/qq_registrations.json，等待面板审核
    │
    └── 其他命令 ──────────────────────► plugins/manage.py
                                           │ ① 白名单校验（同上）
                                           │ ② 未授权 → 回复拒绝
                                           │ ③ 命令分发：status / mode / groups /
                                           │    channels / users / backfill / restart
                                           │ ④ 通过 HTTP 调用面板 8090 API
                                           └── ⑤ 回复执行结果
```

QQ 群消息中的 `register-group <群号> <群名称>`（plugins/registration.py）：
发送者 `member_openid` 必须在 `qq_user_openids` 白名单内，申请绑定当前
Group OpenID 写入 `data/qq_registrations.json`，等待面板审核。
中文"注册"指令已删除，中文消息按未知命令正常回复，不触发注册。

QQ 私聊的 `backfill on/off` 走字段级接口 `POST /api/settings/backfill-toggle`：
只提交 `backfill_enabled` 一个字段，由面板读取当前配置后仅修改该字段再
走统一保存流程（结构校验 + `.bak` + 原子替换；值未变化时不写盘），
避免回传整份配置覆盖面板刚保存的其他字段。

---

## 6. 权限 / 自动发现 / 身份库的关系

```
机器人收到 QQ 群消息 / 被拉进群 / 收到私聊
    │
    ├──► 自动发现（plugins/command.py）
    │      把 group_openid 写入 data/qq_group_openids.json
    │      把 user_openid  写入 data/qq_user_openids.json
    │      ⚠️ 只记录，不授权，也不进同步名单。需先注册再经面板审核
    │
    ├──► 注册申请（plugins/registration.py）
    │      用户私聊：register <QQ号> <昵称> → 绑定当前 User OpenID
    │      群聊（白名单用户）：register-group <群号> <群名称> → 绑定当前 Group OpenID
    │      写入 data/qq_registrations.json（同一 openid 只有一条记录）
    │      面板「注册审核」：√ 通过 → 按同步流程加入 settings（默认禁用），
    │                        并把注册信息带入条目（用户：昵称→name、QQ号→qq_id；
    │                        群：群名→name、群号→qq_id）
    │                        × 拒绝 → 申请消失（重新注册会再次出现）
    │
    ├──► 身份记录（plugins/identities.py）
    │      把昵称/群名、最后活动时间写入 data/qq_identities.json
    │      ⚠️ 纯展示用，绝不参与权限判断
    │
    └──► 权限判断（plugins/config.py）
           唯一权限来源：data/settings.json 里的
           qq_group_openids[*].enabled = true
           qq_user_openids[*].enabled = true
           ⚠️ 自动发现/注册审核通过的新群/新用户默认 enabled=false
              必须在管理面板手动勾选才放行
```

**四个关键原则：**

- `qq_user_openids` 和 `qq_group_openids`（settings.json 中的列表）是**唯一的权限来源**。
- 自动发现**不会自动授权**，也**不直接进同步名单**；只有主动注册后才会出现在面板审核列表里。
- 注册 ≠ 授权，审核通过 ≠ 自动启用：审核通过只是把 openid 加入 settings（`enabled: false`），必须由管理员手动勾选。
- `qq_identities.json` 是**身份资料库**（昵称、群名、备注），只用于面板展示，不参与任何放行判断。


---

## 7. 管理面板三张核心卡片

### 7.1 QQ 接收群

| 项目 | 说明 |
|------|------|
| 数据来源 | `settings.json → qq_group_openids` |
| 展示辅助 | `qq_identities.json → groups`（群名） |
| 每行字段 | openid、名称（群名，注册审核通过时带入）、账号（群号）、启用/禁用、测试标记 |
| 同步按钮 | "注册审核"：读取 `data/qq_registrations.json`（群注册申请），√ 通过 → 按同步流程加入 settings（默认禁用，群名/群号自动带入条目），× 拒绝 → 移除申请；管理员勾选后生效 |
| 影响范围 | 实时转发目标群、启动补发目标群 |

### 7.2 Discord 转发频道

| 项目 | 说明 |
|------|------|
| 数据来源 | `settings.json → discord_channels` |
| 展示辅助 | Discord API 真实频道名（面板点"刷新频道记录"时拉取，缓存于 bot 进程） |
| 每行字段 | 频道 ID、显示名、启用/禁用、测试标记 |
| 同步按钮 | "刷新频道记录"→ 执行 `scripts/diagnose_channels.py`，更新 `data/channels_audit.json`，展示哪些频道 bot 有读权限 |
| 影响范围 | 哪些 Discord 频道的消息会被转发 |

### 7.3 私聊权限

| 项目 | 说明 |
|------|------|
| 数据来源 | `settings.json → qq_user_openids` |
| 展示辅助 | `qq_identities.json → users`（昵称、管理员备注） |
| 每行字段 | openid、名称（昵称，注册审核通过时带入）、账号（QQ号）、启用/禁用 |
| 同步按钮 | "注册审核"：读取 `data/qq_registrations.json`（用户注册申请），√ 通过 → 按同步流程加入 settings（默认禁用，昵称/QQ号自动带入条目），× 拒绝 → 移除申请 |
| 影响范围 | 哪些 QQ 用户可以使用 relay 命令和管理命令（status/restart/mode 等） |

---

## 8. 重要数据文件

| 文件 | 写入方 | 读取方 | 内容 |
|------|--------|--------|------|
| `data/settings.json` | 面板（主要；保存时先轮转 `settings.json.bak` 备份）；bot（仅文件完全不存在时首次默认初始化） | bot（高频，带缓存；损坏/结构无效时内存降级，不覆盖原文件）、面板 | 启用的群/频道/用户列表、补发开关、模式预设 |
| `data/settings.json.bak` | 面板（每次保存前轮转写入） | 面板 / 人工（配置损坏或误写时恢复） | 上一版 settings.json 的完整备份（仅保留最近一代） |
| `data/discord_last.json` | bot | bot | 每个频道每个群最后成功转发的消息 ID（去重用） |
| `data/qq_group_openids.json` | bot（自动发现）；面板（审核通过时补回缺失 openid） | 面板（同步预览、审核补回校验） | 机器人发现的所有群 openid（原始记录，非权限，不进同步名单） |
| `data/qq_user_openids.json` | bot（自动发现）；面板（审核通过时补回缺失 openid） | 面板（同步预览、审核补回校验） | 机器人发现的所有用户 openid（原始记录，非权限，不进同步名单） |
| `data/qq_registrations.json` | bot（注册指令）；面板（审核通过/拒绝时移除申请并写回） | 面板（注册审核列表） | 待审核的注册申请：用户（QQ号/昵称）、群（群号/群名/操作人），非权限 |
| `data/qq_identities.json` | bot（自动记录） | 面板（展示） | 用户昵称 / 群名 / 最后活动时间 / 管理员备注 |
| `data/stats.json` | bot | 面板 | 转发成功/失败累计 + 当日条数 |
| `data/channels_audit.json` | bot（诊断脚本） | 面板 | 频道权限扫描快照（可读/可见频道列表） |
| `data/runtime_log_level.json` | 面板 | bot | 运行时日志级别（面板写入后 bot 3 秒内自动生效） |
| `data/bot.pid` | 面板 | 面板 | 机器人进程 PID（进程管理用） |
| `data/restart_pending.json` | bot（manage.py） | bot（manage.py） | 跨进程重启确认标记 |

---

## 9. 重要代码文件

| 文件 | 职责 | 修改频率 |
|------|------|----------|
| `bot.py` | 入口：适配器注册、日志轮转、运行时日志级别监听 | 低 |
| `plugins/config.py` | settings.json 消费者：带 mtime 缓存的配置读取；文件缺失时初始化默认值，损坏/结构无效时内存降级（不写盘） | 低 |
| `plugins/relay.py` | 实时转发主逻辑：频道过滤 → 去重 → 渲染 → 发送 → 记录 | 中 |
| `plugins/command.py` | QQ 私聊 relay 命令 + OpenID 自动发现 + 身份记录 | 中 |
| `plugins/registration.py` | QQ 注册指令（注册/注册群）+ 注册申请存储 | 中 |
| `plugins/manage.py` | QQ 私聊管理命令（通过 HTTP 调用面板 API） | 中 |
| `plugins/fetch.py` | Discord 消息抓取 + 归一化 + 渲染为 QQ 消息段（唯一渲染逻辑） | 中 |
| `plugins/sender.py` | QQ 群发送：分片、重试、限流退避、降级 | 低 |
| `plugins/media.py` | 文本美化（Markdown → 纯文本）+ Emoji 提取 + 媒体下载 | 低 |
| `plugins/dedup.py` | 去重纯函数：该不该发、发给谁、成功后怎么记 | 低 |
| `plugins/filter.py` | 频道消息筛选纯函数（用户名/关键词，relay 与 backfill 共用） | 低 |
| `plugins/history.py` | discord_last.json 读写（带 mtime 缓存） | 低 |
| `plugins/backfill.py` | 启动后自动补发离线缺失消息 | 中 |
| `plugins/backfill_api.py` | 补发 HTTP 接口（面板调用）+ 频道权限审计 | 中 |
| `plugins/identities.py` | OpenID 身份资料库（昵称/群名/备注） | 低 |
| `plugins/stats.py` | 转发统计 | 低 |
| `plugins/json_io.py` | JSON 原子读写工具 | 低 |
| `plugins/health_api.py` | Bot 健康状态接口 | 低 |
| `panel/管理面板.pyw` | 面板主程序：FastAPI + 配置读写 + 进程管理 + 看门狗 | 高 |
| `panel/web/app.js` | 面板前端逻辑 | 高 |

---

## 10. 常见需求应该去哪里改

| 需求 | 去哪改 |
|------|--------|
| **修改 QQ 群接收权限** | `panel/管理面板.pyw`（API + 页面数据）+ `panel/web/app.js`（UI） |
| **修改 Discord 频道列表** | 同上；频道权限扫描逻辑在 `scripts/diagnose_channels.py` |
| **修改转发逻辑**（消息格式、渲染、过滤规则） | `plugins/relay.py`（实时）+ `plugins/fetch.py`（渲染）+ `plugins/sender.py`（发送） |
| **修改补发逻辑** | `plugins/backfill.py`（自动补发）+ `plugins/backfill_api.py`（手动触发接口） |
| **修改 OpenID 自动发现** | `plugins/command.py`（`save_group_openid` / `save_user_openid`） |
| **修改注册/审核机制** | `plugins/registration.py`（指令 + 存储）+ `panel/管理面板.pyw`（`/api/registrations*`）+ `panel/web/app.js`（审核弹窗） |
| **修改身份资料库** | `plugins/identities.py` |
| **修改面板 UI** | `panel/web/index.html` + `panel/web/app.js` + `panel/web/style.css` |
| **修改面板 API** | `panel/管理面板.pyw`（FastAPI 路由） |
| **修改 QQ 私聊命令** | `plugins/manage.py`（管理命令）+ `plugins/command.py`（relay 命令） |
| **修改消息文本格式**（Markdown 清理、Emoji 处理） | `plugins/media.py`（`cleanup_markdown` / `parse_discord_emoji` 等） |
| **修改去重规则** | `plugins/dedup.py`（纯函数） |
| **修改频道筛选规则**（按用户名/关键词过滤） | `plugins/filter.py`（纯函数）+ `plugins/config.py`（`get_channel_filter`） |
| **修改发送重试/限流策略** | `plugins/sender.py`（`RETRY_DELAY` / `MAX_SEND_ATTEMPTS` / `RATE_LIMIT_BACKOFFS`） |
| **修改日志级别/轮转** | `bot.py`（`_setup_log_sink`）；运行时级别由面板写入 `data/runtime_log_level.json` |
| **添加新的 settings.json 字段** | `plugins/config.py`（`_default_settings` + getter 函数）+ `panel/管理面板.pyw`（`get_default_settings` + 读取/保存逻辑） |

---

## 11. 高风险区域（不要随便改）

### 11.1 `plugins/dedup.py` — 去重核心

**为什么危险：** 纯函数逻辑，但直接决定"消息会不会重复发送"。改错任何一个比较条件（`int(message_id) > int(last_id)`），会导致消息重复或丢失。`relay.py` 和 `backfill.py` 都依赖它，改动影响面覆盖全部转发路径。

### 11.2 `plugins/config.py` — 配置缓存

**为什么危险：** 带 mtime/size 的内存缓存是 bot 进程的性能关键路径（每条消息都调）。改缓存逻辑可能导致：读到旧配置（用户改了面板但不生效）、频繁读盘（性能下降）、或读到损坏数据（转发异常）。当前 `_ensure_settings_file` 仅在文件完全不存在时初始化默认值；损坏/结构无效时内存降级、不覆盖原文件——若改回「无效即用默认值覆盖写盘」则会清空用户配置。

### 11.3 `plugins/sender.py` — 发送 + 重试

**为什么危险：** 429 限流退避、分片发送、媒体降级三条路径交织。改错重试逻辑可能导致：限流时雪崩式重试（被封号）、长消息截断丢失内容、或媒体全部降级为链接（体验变差）。返回的 `ok_map` 直接影响去重记录，改错会导致重复发送。

### 11.4 `panel/管理面板.pyw` — settings.json 写入

**为什么危险：** 面板是 settings.json 的唯一正常写入方。`save_settings` 的原子替换（唯一 tmp + `os.replace`，见 `plugins/json_io.py`）保证配置不会写坏，保存前还会把上一版轮转备份为 `settings.json.bak`，误写/损坏可从 `.bak` 恢复。如果改成非原子写入，面板崩溃时可能写坏配置文件，导致 bot 启动后读不到配置。`load_settings` 里的迁移逻辑（`test_group_openid` → `is_test`）是一次性的，重复执行无害但不能删（否则旧配置用户升级后会丢失测试标记）。

### 11.5 `plugins/backfill.py` — 补发与去重的交叉

**为什么危险：** 补发和实时转发共用 `discord_last.json`，且补发是异步后台任务。如果改补发的去重逻辑（比如不逐条重读记录），可能与实时转发竞争，导致同一条消息被发两次。`_backfill_running` 互斥锁保证不会并发补发，去掉会导致刷屏。

---

## 12. 已完成的模块化

### `plugins/dedup.py` — 去重逻辑抽离

- 原本去重判断散落在 `relay.py` 和 `backfill.py` 中，现已抽为纯函数模块。
- 不碰 I/O、不碰 fetch、不碰 send，只回答"该不该发 / 发给谁 / 成功后怎么记"。
- 所有持久化由调用方通过 `history.py` 完成。
- 好处：可以独立测试去重逻辑，新增转发路径时复用同一套去重。

### `panel/web/` — 面板前端独立

- 面板前端从 `管理面板.pyw` 中分离为独立的 `index.html` + `app.js` + `style.css`。
- 通过 FastAPI `StaticFiles` 挂载，面板重启后刷新浏览器即可看到最新前端。
- 好处：改 UI 不需要改 Python 代码，也不需要重启面板进程。

---

## 13. 明确记录

| 条目 | 说明 |
|------|------|
| `qq_user_openids`（settings.json） | **私聊权限的唯一来源**。`enabled: true` 的用户才能使用 relay 和管理命令 |
| `qq_group_openids`（settings.json） | **转发目标群的唯一来源**。`enabled: true` 的群才会收到转发消息 |
| 自动发现不自动授权 | bot 发现新群/新用户后写入 `data/qq_group_openids.json` / `data/qq_user_openids.json`，但只记录不进同步名单，也不加入 settings |
| 注册审核 | 主动注册（`register` / `register-group` 指令）后才出现在面板"注册审核"弹窗；通过 = 按现有同步流程加入 settings（默认 `enabled: false`）并把昵称/群名写入条目 `name`、QQ号/群号写入 `qq_id`（可选字段，旧条目/手动添加项没有时显示为空；`remark` 原样保留），拒绝 = 申请消失；同一 openid 同时只有一条注册记录（重新注册覆盖） |
| `qq_registrations.json` | 注册申请存储，与白名单数据独立；**不参与任何权限判断**，审核通过 ≠ 自动启用 |
| `qq_identities.json` | 纯身份资料库（昵称、群名、管理员备注），只用于面板展示，**绝不参与任何权限判断** |
| 不存在主动反查群名 | QQ 官方 API 不提供群名/昵称查询接口。群名只在机器人收到群消息时从事件中提取并记录，无法通过 openid 主动查询 |
| 只做 Discord → QQ | 项目不做 QQ → Discord，不做双向同步 |
