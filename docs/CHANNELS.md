# WuppoRelay Bot 频道权限记录

> 由 scripts/diagnose_channels.py 生成，更新于 2026-09-04 22:00
> 权限变化后重新运行 `python scripts/diagnose_channels.py` 即可更新本文件。

## 权限模型

- **View Channel（查看频道）**：能列出并看到频道名（对应下表"可见频道"）
- **Read Message History（读取消息历史）**：能通过 API 读取消息内容，
  relay 链接转发与历史补发需要此权限（对应下表"实际可读取内容"）

## 实际可读取内容的频道（28 个）

| 服务器 | 频道 | ID |
|---|---|---|
| snekflat enthusiasts | #popocity | 222730600488894464 |
| snekflat enthusiasts | #news | 222730696786051073 |
| snekflat enthusiasts | #botcommands | 339642446218526720 |
| snekflat enthusiasts | #speedwumming | 340566914550202368 |
| snekflat enthusiasts | #wuppo-help | 343294494675828736 |
| snekflat enthusiasts | #wuppo | 354459313118642176 |
| snekflat enthusiasts | #voicechat | 361411882080534528 |
| snekflat enthusiasts | #wuppo-spoilers | 362561154721251331 |
| snekflat enthusiasts | #filmstrips | 387858187921129472 |
| snekflat enthusiasts | #sinkhole | 404406283966349312 |
| snekflat enthusiasts | #wuppo-bff | 733375711720767488 |
| snekflat enthusiasts | #other-games | 751181022564974703 |
| snekflat enthusiasts | #wuppo-wiki-discussion | 823698268776038441 |
| snekflat enthusiasts | #wondersplenk-modding | 865580013913767946 |
| snekflat enthusiasts | #server-feedback | 1025629241217405008 |
| snekflat enthusiasts | #terry-enthusiasts | 1025635002144260116 |
| snekflat enthusiasts | #terry-fan-art | 1129336477579497482 |
| snekflat enthusiasts | #terry-questions | 1129336588632068207 |
| snekflat enthusiasts | #terry-spoilers | 1202539296255508541 |
| snekflat enthusiasts | #i-broke-terry | 1204090294379749377 |
| snekflat enthusiasts | #terry-clips | 1245391476209488025 |
| snekflat enthusiasts | #terry-speedrun | 1246093825768689726 |
| snekflat enthusiasts | #patch-notes | 1246134049123336252 |
| snekflat enthusiasts | #mld-faq | 1428024896461471836 |
| snekflat enthusiasts | #mld-updates | 1428025029290889318 |
| snekflat enthusiasts | #mld-general | 1428025061243228170 |
| 橡皮测试服务器 | #测试频道 | 1525802021582540822 |
| 橡皮测试服务器 | #私密测试频道 | 1543932628418433114 |

## 可见频道（55 个，含不可读）

### snekflat enthusiasts（222730600488894464）

| 频道 | ID | 可读内容 |
|---|---|---|
| #popocity | 222730600488894464 | ✅ |
| #news | 222730696786051073 | ✅ |
| #offtopicunused | 222973763745087488 | ❌ 403 |
| #mod-chat | 339633866337812481 | ❌ 403 |
| #botcommands | 339642446218526720 | ✅ |
| #speedwumming | 340566914550202368 | ✅ |
| #wuppo-help | 343294494675828736 | ✅ |
| #wuppo | 354459313118642176 | ✅ |
| #future-projects | 354624404199571458 | ❌ 403 |
| #wums | 361411046348554241 | ❌ 403 |
| #fnakkers | 361411059086524416 | ❌ 403 |
| #splenkhakkers | 361411076820041729 | ❌ 403 |
| #blussers | 361411094427992064 | ❌ 403 |
| #voicechat | 361411882080534528 | ✅ |
| #wuppo-spoilers | 362561154721251331 | ✅ |
| #filmstrips | 387858187921129472 | ✅ |
| #sinkhole | 404406283966349312 | ✅ |
| #blieks | 405097089920663552 | ❌ 403 |
| #bobos | 410171816590573568 | ❌ 403 |
| #teas-cult | 448625638119833610 | ❌ 403 |
| #knefts | 557874574109310987 | ❌ 403 |
| #bliones | 557874728459698177 | ❌ 403 |
| #special_testing_elite | 557920819188334593 | ❌ 403 |
| #wumhouse-brawl | 562263233264746496 | ❌ 403 |
| #wuppo-bff | 733375711720767488 | ✅ |
| #wuppobffminigame | 749712074338205786 | ❌ 403 |
| #other-games | 751181022564974703 | ✅ |
| #wuppo-wiki-discussion | 823698268776038441 | ✅ |
| #wondersplenk-modding | 865580013913767946 | ✅ |
| #server-feedback | 1025629241217405008 | ✅ |
| #terry-enthusiasts | 1025635002144260116 | ✅ |
| #terries | 1083435528139046954 | ❌ 403 |
| #logs | 1083579361233490041 | ❌ 403 |
| #terry-fan-art | 1129336477579497482 | ✅ |
| #terry-questions | 1129336588632068207 | ✅ |
| #playtest-info | 1176845356496080957 | ❌ 403 |
| #playtest-general | 1176845375253008384 | ❌ 403 |
| #playtest-questions | 1176845405451989042 | ❌ 403 |
| #playtest-bugs | 1176845431771254835 | ❌ 403 |
| #playtest-suggestions | 1177758027349245982 | ❌ 403 |
| #terry-spoilers | 1202539296255508541 | ✅ |
| #i-broke-terry | 1204090294379749377 | ✅ |
| #terry-clips | 1245391476209488025 | ✅ |
| #terry-speedrun | 1246093825768689726 | ✅ |
| #patch-notes | 1246134049123336252 | ✅ |
| #wuppo-translation-help | 1362128778595401849 | ❌ 403 |
| #mld-faq | 1428024896461471836 | ✅ |
| #mld-updates | 1428025029290889318 | ✅ |
| #mld-general | 1428025061243228170 | ✅ |
| #rules | 1433476493257998490 | ❌ 403 |
| #modmail-setup | 1472623232224985214 | ❌ 403 |
| #modmail-log | 1472623377658155265 | ❌ 403 |
| #coffeecoffin | 1545390861737525278 | ❌ 403 |

### 橡皮测试服务器（1525802021142397061）

| 频道 | ID | 可读内容 |
|---|---|---|
| #测试频道 | 1525802021582540822 | ✅ |
| #私密测试频道 | 1543932628418433114 | ✅ |
