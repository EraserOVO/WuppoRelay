# WuppoRelay 安装指南（Windows）

WuppoRelay 是一个 **Discord → QQ 单向消息转发器**：把指定 Discord 频道的消息实时转发到指定 QQ 群。

---

## 1. 前置条件

- Windows 10 / 11
- Python 3.10 或更高版本（安装时勾选 **Add to PATH**，可到 [python.org](https://www.python.org/downloads/) 下载）
- 能访问 Discord（国内网络需要代理，见第 6 节）

---

## 2. 一键安装

1. 把整个项目放到一个目录（例如 `C:\Wuppo`），或 `git clone` 到本地。
2. **双击 `install.bat`**，脚本会自动：
   - 创建虚拟环境 `.venv`
   - 安装项目依赖（`pip install -e .`）
   - 若不存在 `.env.prod`，从 `.env.example` 复制生成
3. 用记事本打开 **`.env.prod`**，按第 3、4、5 节填入你自己的配置。
4. **双击 `启动面板.bat`** 打开管理面板（浏览器访问 `http://127.0.0.1:8090`）。
5. 在面板中：勾选你的 Discord 频道和 QQ 群 → 点击「启动机器人」。

> 安装后每次使用只需双击 `启动面板.bat`（机器人由面板托管，崩溃会自动重启）。

---

## 3. `.env.prod` 配置详解

| 配置项 | 含义 | 说明 |
|---|---|---|
| `DISCORD_BOTS` | Discord Bot 的 token | 见第 4 节 |
| `QQ_BOTS` | QQ 官方机器人的 AppID / AppSecret | 见第 5 节；`id` 与 `token` 都填 AppID |
| `HTTP_PROXY` | 本地代理地址 | 国内访问 Discord 必需，见第 6 节 |
| `LOG_LEVEL` | 日志级别 | 默认 `INFO`；排查问题时可改 `DEBUG` |
| `API_TIMEOUT` | 媒体下载超时（秒） | 默认 120 |
| `MEDIA_CONCURRENCY` | 同时下载的媒体数 | 默认 4 |
| `MEDIA_MAX_BYTES` | 单文件大小上限（字节） | 默认 30MB，超限降级为链接 |

> `.env.prod` 包含密钥，**绝对不要提交到 git 或外传**。

---

## 4. 创建 Discord Bot

1. 打开 [Discord Developer Portal](https://discord.com/developers/applications)，点 **New Application**。
2. 左侧 **Bot** 页 → **Reset Token** → 复制 token，填到 `.env.prod` 的 `DISCORD_BOTS`。
3. 在同一页面打开 **Message Content Intent**（特权网关意图，否则收不到消息正文）。
4. 邀请机器人进服务器：**OAuth2 → URL Generator**，勾选 `bot` 权限，再勾选需要的权限（`Send Messages`、`Read Messages/View Channels`、`Read Message History` 等），用生成的链接邀请。
5. 获取频道 ID：
   - 打开 Discord 用户设置 → **高级 → 开发者模式**
   - 右键目标频道 → **复制频道 ID**，粘贴到管理面板「Discord 转发频道」里

---

## 5. 创建 QQ 官方机器人

1. 登录 [QQ 开放平台](https://q.qq.com)，创建应用/机器人，获得 **AppID** 和 **AppSecret**。
2. 在 `.env.prod` 的 `QQ_BOTS` 中：`id` 与 `token` 都填 AppID，`secret` 填 AppSecret。
3. 把机器人拉进目标 QQ 群，在群里 @机器人 发一条消息。
4. 控制台会出现 `[QQ group_openid] ...`，同时写入 `data/qq_group_openids.json`。
5. 打开管理面板 →「QQ 接收群」→ 点「同步自动发现的群」，勾选启用该群。

> 说明：
> - QQ 官方机器人使用 **group_openid**（不是群号），每个机器人的 openid 不同，无法推算，只能靠入群后自动发现。
> - 公域机器人需要群主开启「主动消息」权限，且可能有时段/频率限制；想稳定使用建议申请转私域。
> - 机器人无法给未配置 openid 的群发消息。

---

## 6. 代理（国内访问 Discord 必需）

Discord API 和 CDN 在国内无法直连，需要 HTTP 代理：

1. 运行你的代理软件（Clash / v2rayN 等），确认本地监听端口（Clash 默认 `7890`）。
2. 在 `.env.prod` 填写，例如：

   ```
   HTTP_PROXY=http://127.0.0.1:7890
   ```

3. 代理不对时，表现为：机器人连不上 Discord、图片/音频下载失败。

---

## 7. 常见问题

| 现象 | 排查 |
|---|---|
| 面板提示找不到 `.venv` | 还没运行 `install.bat` |
| 机器人启动失败 | 查看面板「运行日志」；确认 `.env.prod` 的 token / AppID / AppSecret 填对 |
| 收不到转发消息 | 群主是否开启「主动消息」权限；公域机器人有时段/频率限制；频道 ID / 群 openid 是否已启用 |
| 图片 / 音频发不出来 | 检查代理；文件超过 `MEDIA_MAX_BYTES` 会降级为文字 + 链接 |
| 想重置配置 | 删除 `data/settings.json` 后重启机器人，会生成空配置重新开始 |

---

## 8. 安全提醒

- `.env.prod` 含密钥，**绝不提交、不分享**。
- `data/` 目录是运行时数据（配置、去重记录、日志），误删后重新配置即可，不影响程序本身。
- 本项目只做 Discord → QQ 单向转发，不做 QQ → Discord。
