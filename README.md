# WuppoRelay

**Discord → QQ 单向消息转发器**（基于 NoneBot2）：把指定 Discord 频道的消息实时转发到指定 QQ 群。

> 本项目只做 **Discord → QQ**，不做 QQ → Discord，不做双向同步。

## 功能

- **实时转发**：Discord 频道消息 → QQ 群
- **消息类型**：文字、图片、Discord 自定义 Emoji、Embed（标题/链接/图片）、音频、文件
- **文本美化**：清理 Markdown 标记，@提及（用户/角色/频道/@everyone）转为可读文本
- **防重复**：按 QQ 群分别记录消息 ID，某群发送失败不影响其他群，重连只补失败的群
- **健壮发送**：多群并发、媒体并发下载 + 大小上限、失败自动降级为文字 + 原链接
- **启动补发**：机器人连接后自动补齐离线期间未转发的缺口消息（每频道每批条数可配置，防止刷屏）
- **QQ 私聊 relay 命令**：私聊机器人发 Discord 消息链接或任意文字，手动转发到启用群
- **OpenID 身份识别**：自动记录私聊用户/群的昵称、群名与最后活动时间，在「QQ 接收群」「私聊权限」的 OpenID 旁内联展示（仅记录，不自动放行）
- **管理面板**：本地网页启停机器人、勾选频道/群、查看日志、切换日志级别、离线补发管理、开机自启

## 快速开始（Windows）

> 目前仅支持 Windows（安装脚本与管理面板依赖 Windows 特性）。

需要 Python 3.10+。

1. **安装**：双击 `install.bat`
   - 创建虚拟环境 `.venv` 并安装依赖（`pip install -e .`）
   - 若不存在 `.env.prod`，自动从 `.env.example` 复制生成配置模板
2. **配置**：用记事本编辑 `.env.prod`，填入你自己的 Discord / QQ 机器人配置
3. **启动**：双击 `启动面板.bat`，浏览器自动打开管理面板 `http://127.0.0.1:8090`
4. 在面板中勾选你的 Discord 频道和 QQ 群，点击「启动机器人」

完整的申请与配置流程（Discord Bot、QQ 开放平台机器人、group_openid、频道 ID、代理）见 **[docs/SETUP.md](docs/SETUP.md)**。

## 项目结构

```text
Wuppo/
├─ bot.py               # 入口：注册适配器、日志轮转、加载插件
├─ pyproject.toml       # 依赖与项目元信息（pip install -e .）
├─ .env.example         # 配置模板（install.bat 复制为 .env.prod）
├─ install.bat          # Windows 一键安装
├─ 启动面板.bat          # 启动管理面板
├─ docs/SETUP.md        # 全新安装与配置指南
├─ plugins/             # 转发核心
│  ├─ relay.py          # 实时转发：去重 + 渲染 Discord 消息
│  ├─ fetch.py          # Discord 消息统一抓取 / 归一化 / 渲染
│  ├─ media.py          # 文本/Emoji 转换 + 媒体字节下载
│  ├─ sender.py         # 发送文字/媒体到启用群（分片、重试、降级）
│  ├─ backfill.py       # 启动补发：连接后补发离线缺口消息
│  ├─ backfill_api.py   # 补发 HTTP 接口（供面板查询/操作）
│  ├─ history.py        # 去重记录读写
│  ├─ config.py         # 运行时配置读取（settings.json 缺失时自动生成空配置）
│  ├─ command.py        # QQ 私聊 relay 命令 + 群 openid 自动发现
│  ├─ identities.py     # QQ OpenID 身份资料（昵称/群名/最后活动，独立于白名单）
│  ├─ stats.py          # 转发统计
│  ├─ hello.py          # hello / 你好 测试命令
│  └─ json_io.py        # JSON 原子读写
├─ panel/
│  ├─ 管理面板.pyw      # 管理面板（127.0.0.1:8090）
│  ├─ autostart.pyw     # 开机自启中转
│  └─ wuppo_panel.ico   # 面板图标
└─ data/                # 运行时数据：配置、去重记录、日志（不入库）
```

## 技术栈

- [NoneBot2](https://nonebot.dev/) + `nonebot-adapter-discord` + `nonebot-adapter-qq`
- QQ 官方机器人（AppID / AppSecret）
- Discord Bot
- httpx / FastAPI / uvicorn

## 注意事项

- 国内网络访问 Discord 需要代理，在 `.env.prod` 的 `HTTP_PROXY` 配置（详见 SETUP.md）
- QQ 官方机器人使用 group_openid 标识群，机器人入群后自动发现，在面板「同步自动发现的群」即可
- 公域机器人需要群主开启「主动消息」权限，可能有时段/频率限制；稳定使用建议申请转私域
- `.env.prod` 包含密钥，请勿提交到仓库或外传
- 本项目只做 Discord → QQ 单向转发

## 文档

- [安装与配置指南](docs/SETUP.md)
