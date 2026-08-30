# WuppoRelay 项目说明

## 核心目标

WuppoRelay 基于 **NoneBot2**，唯一核心方向是：

```text
Discord → QQ
```

将指定 Discord 频道的消息实时转发到指定 QQ 群。

当前 QQ 端：**QQ 官方机器人（私域，`nonebot-adapter-qq`）**。

## 启动（推荐：管理面板，无黑窗）

双击桌面或 `快捷方式/` 里的 **「WuppoRelay管理面板」** 即可（无命令行窗口，自动打开浏览器）：

1. 打开面板后，点「启动机器人」开始转发。
2. 在面板中勾选/取消 **QQ 接收群** 与 **Discord 转发频道**，修改立即生效，无需重启。
3. 「停止 / 重启机器人」控制运行状态；「运行日志」实时查看。

面板是本地网页（`http://127.0.0.1:8090`，仅本机可访问），
由 `panel/管理面板.pyw`（pythonw 无窗口运行）提供，机器人由它以后台进程方式启动。

其他启动方式（会弹出命令行窗口，仅作备用）：

```powershell
nb run
```

环境：Python 3.10+，项目目录 `C:\Users\Era\Wuppo`。

## 项目结构

```text
Wuppo/
├─ bot.py                   # 入口，注册适配器并加载插件
├─ pyproject.toml
├─ .env.prod                # 运行环境配置（含密钥，勿外泄）
├─ README.md
├─ panel/
│  └─ 管理面板.pyw          # 管理面板（无窗口，启停机器人 + 群聊/频道管理）
├─ plugins/                 # 插件源码
│  ├─ relay.py              # 转发事件处理：去重 + 解析 Discord 消息并调用 sender
│  ├─ media.py              # 文本/Emoji 转换 + QQ 消息段构建 + 媒体字节下载
│  ├─ sender.py             # 发送文字/媒体到启用群（富媒体失败降级为链接）
│  ├─ history.py            # data/discord_last.json 读写（转发去重）
│  ├─ discord_history.py    # 历史补发，当前暂停
│  ├─ config.py             # 频道、QQ群 openid 等默认配置 + 运行时设置读取（settings.json 缺失时自动初始化）
│  ├─ command.py            # QQ私聊 Discord 链接 + 群 openid 自动发现
│  ├─ fetch.py              # Discord 单条消息 API 抓取
│  └─ hello.py
├─ data/                    # 运行时数据
│  ├─ settings.json         # 面板管理的启停配置（群/频道，实时生效）
│  ├─ discord_last.json
│  ├─ discord_history.json
│  ├─ qq_group_openids.json # 机器人自动发现的新群 openid
│  └─ logs/                 # 运行日志（bot.log / panel.log）
├─ backup/                  # 历史版本备份（按日期归档）
│  ├─ 20260713/
│  ├─ 20260714/
│  ├─ 20260829/             # 切换 QQ 官方 Bot 前的 OneBot V11 版本
│  └─ 废弃文件/
├─ 测试内容/                # 测试用的音频/图片
└─ 快捷方式/                # 启动快捷方式（NapCat / NoneBot / 启动项）
```

## 当前技术栈

* NoneBot2
* `nonebot-adapter-discord`
* `nonebot-adapter-qq`
* QQ 官方机器人（私域，AppID / AppSecret）
* Discord Bot
* httpx

## 实时转发 `relay.py`

监听 `GuildMessageCreateEvent`，只处理 `data/settings.json`（或 `config.py` 兜底）中启用的频道，
并发送到启用的 QQ 群（openid 列表）。

目前支持：

* 普通文字
* `@everyone` 转换为 `[@全体成员]`，`@here` 转换为 `[@在线成员]`
* Discord 自定义静态/动态 Emoji → QQ 图片
* Discord 文本美化：粗体/斜体/删除线/行内代码/代码块清理，`[文字](链接)` → `文字（链接）`，
  `<@用户>` / `<@&角色>` / `<#频道>` 提及 ID → 可读文本（不查名字，避免额外 API 调用）
* Embed 标题、URL、图片
* 图片附件：png/jpg/jpeg/gif/webp
* 音频附件：wav/mp3/ogg/flac/m4a
* 其他文件：发送文件名和 URL
* 使用 `data/discord_last.json` 记录消息 ID，避免重复处理（按 QQ 群分别记录，
  某群发送失败不会影响其他群的去重，重连补发时只补失败群）

媒体处理机制（重要）：

* **本地下载后直传**：图片/Emoji/音频由机器人**先通过代理下载字节**，
  再用 `file_image` / `file_audio` 把字节上传给 QQ（≤10MB 走 base64，>10MB 走分片上传）。
  这样不依赖 QQ 服务器访问 Discord CDN（此前直接给 URL 会报
  `40093007 富媒体文件下载失败` / `850027 上传超时`）。
* **下载大小上限**：单文件默认 30MB（`MEDIA_MAX_BYTES` 可调），超过上限的媒体
  直接放弃下载，降级为链接，避免超大文件拖垮内存/超时。
* 下载失败或 QQ 富媒体上传失败时，自动降级为**文字 + 原链接**发送，内容不丢失。
* 音频可能受 QQ 格式/审核限制，若失败仍会降级为链接。
* **多群并发发送**：同时转发到多个 QQ 群（`asyncio.gather`），
  某个群被限流(429)不会拖慢其他群。

## 历史补发

`plugins/discord_history.py` 的**自动历史补发目前暂停**。

原因：旧实现会补发不需要的老消息，而且历史 API 的附件处理不如实时事件完整。

**禁止自行恢复自动历史扫描。**未来如需重新实现，应重新设计触发方式和附件处理。

补充：机器人每次连接时会对比各启用频道的最新消息 ID 与 `discord_last.json`，
若发现缺口只输出**警告日志**（`频道[...]离线期间可能丢失N条消息...未自动补发`），
不发送任何消息——这只是提示，不属于历史补发，不要在此基础上加回自动转发。

## QQ 私聊功能

`command.py` 还有独立的 QQ 私聊 Discord 消息链接功能，例如：

```text
https://discord.com/channels/服务器ID/频道ID/消息ID
```

或直接发 `relay 任意文字` 手动转发。

该功能需要保留，不要因项目核心方向是 Discord → QQ 而误删。

**私聊转发白名单**（`data/settings.json` 的 `allowed_users`）：空列表 = 不限制；
非空时只有列表内的 QQ 用户 openid 能使用私聊转发，其余用户会收到「没有权限」提示。
白名单只限制私聊 `relay` 命令，不影响群里自动发现的群 openid 记录。

## 当前状态

| 功能                                | 状态      |
| --------------------------------- | ------- |
| Discord → QQ 实时转发                 | ✅       |
| 文字 / 图片 / Emoji / Embed / 音频 / 文件 | ✅       |
| 消息 ID 防重复（按群分别记录）            | ✅       |
| 多群并发发送 + 下载大小上限              | ✅       |
| Discord 文本美化（Markdown/提及）       | ✅       |
| 私聊转发白名单                         | ✅       |
| QQ 私聊 Discord 链接功能                | ✅ 保留    |
| 管理面板（启停/群聊/频道/日志）           | ✅       |
| 面板：日志级别切换 / 清空日志 / 转发统计    | ✅       |
| 离线消息丢失提示（仅提示，不补发）          | ✅       |
| 自动历史补发                            | ⏸ 暂停    |
| QQ → Discord                      | ❌ 不做    |
| QQ 官方 Bot                         | ✅ 已切换    |

## 切换到 QQ 官方机器人后的配置

当前已配置好的实际值：

* `AppID`：`1905519536`（`.env.prod` 的 `QQ_BOTS` 中 `id` 与 `token` 均为它）
* 群 openid：`C206FC38640F9A3CCE072C9797FAED43`（`plugins/config.py` 的 `QQ_GROUP_OPENIDS`）
* 注意：`AppSecret` 属于密钥，保存在 `.env.prod`，不要外泄/提交。

后续新增群的流程：

1. 把机器人拉进新群，并在群里 @ 机器人发一条消息。
2. 控制台输出 `[QQ group_openid] ...`（同时写入 `data/qq_group_openids.json`）。
3. 打开管理面板 → QQ 接收群 → 点「同步自动发现的群」，再勾选启用该群。
   （也可直接在面板中手动添加 openid；无需改 `plugins/config.py`，无需重启。）

其他说明：

* 当前为**公域机器人**：主动推送依赖群主开启「主动消息」权限，且可能有时段/频率限制；若稳定使用建议在 QQ 开放平台转**私域**。
* QQ 官方机器人**不能把消息发到未配置 openid 的群**，群标识是 openid 而非群号，不可直接推算。
* 音频（Discord 附件 → QQ 语音）走官方富媒体上传，可能受格式/审核限制，若失败会以文件链接形式发送。

## 管理面板

入口：桌面或 `快捷方式/` 里的 **「WuppoRelay管理面板」**（双击，无黑窗，自动打开浏览器）。

* 端口 `8090`，仅绑定 `127.0.0.1`，其他设备无法访问。
* 机器人由面板以后台进程启动（`CREATE_NO_WINDOW`，无命令行窗口），日志写入 `data/logs/bot.log`。
* 启停/重启：面板「启动机器人 / 停止机器人 / 重启机器人」。
* 机器人状态卡：显示 PID 与**转发统计**（今日/累计成功与失败条数，写入 `data/stats.json`）。
* 运行日志卡：可切换**日志级别**（DEBUG/INFO/WARNING/ERROR，写入 `data/runtime_log_level.json`，
  机器人后台任务 3 秒内自动生效，无需重启）与**清空日志**。
* 私聊转发白名单卡：每行一个 QQ 用户 openid，留空 = 不限制；
  非空时只有白名单内的用户能私聊机器人执行 `relay` 命令。
* 「QQ 开放平台管理页」按钮：在「QQ 接收群」卡片里，一键新标签页打开
  `https://q.qq.com/qqbot/dashboard/manage/1905519536`。
* 收发群聊管理：修改 `data/settings.json`（通过面板勾选），`relay.py`/`command.py` 每条消息实时读取，**立即生效**。
  * QQ 接收群：`qq_group_openids`（openid + enabled + 备注）
  * Discord 转发频道：`discord_channels`（id + 名称 + enabled）
* 面板自身日志：`data/logs/panel.log`（启动、启停、自启操作均带时间戳记录）。
* 若面板已运行，再次双击快捷方式只会打开浏览器。
* 开机自启（面板「机器人状态」里勾选）：「模式」卡片下方的勾选框开启后，
  在 Windows 启动文件夹写入 `WuppoAutostart.vbs`，重启后**后台启动面板 + 自动拉起机器人**，
  不打开浏览器/面板窗口；面板本身仍在 `8090` 运行，随时双击「WuppoRelay管理面板」即可打开管理页。
  关闭勾选即删除自启项。

注意：

* 机器人日志里 Discord 的 `GuildCreateCompatEvent` 信息量大（`LOG_LEVEL=DEBUG`），长时间运行日志会很大，可定期清理 `data/logs/bot.log`。
* 若用旧方式（`nb run` 或 `start_wuppo.bat`）手动启动机器人，面板仍会显示「运行中」，但该实例不受面板完全控制；此时请先手动停止，再由面板接管。

## OpenCode 修改原则

1. **不要增加 QQ → Discord 或双向同步。**
2. **不要自行恢复历史补发。**
3. 优先最小范围修改，不要无理由重构其他插件。
4. 不要破坏现有文字、图片、Emoji、Embed、音频、文件转发。
5. 修改前先阅读相关文件，尤其是 `relay.py`、`config.py`、启动文件、`command.py`、`discord_history.py`。
6. 如果修改文件，应提供**完整文件内容**，不要只给零散代码片段。
7. 需求存在冲突时先说明，不要自行改变项目方向。

**WuppoRelay 本质上是 Discord → QQ 的消息转发器，不是双向聊天桥。**
