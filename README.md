# scys-daily-template · 生财有术每日看板（云端自动推送飞书）模板

> 每天 AI 自动帮你盯生财有术：1 条 AI 精华帖 + 4 条风向标（2 AI 变现 + 2 其他），
> 9 点推到你的飞书群。电脑关机也照推（GitHub Actions 云端运行）。
> **详细小白教程见配套飞书文档。**

## 使用三步（详见教程）
1. 用本模板创建你自己的**私有**仓库
2. `python3 authorize.py` 完成生财有术授权 → 加密成 `state/auth.json.enc` 提交
3. 配好 2 个 Secrets（`STATE_KEY`、`FEISHU_WEBHOOK`）→ 手动触发一次验证

## 文件说明
| 文件 | 作用 |
|---|---|
| `authorize.py` | 一次性授权脚本：浏览器同意后生成 `~/.scys-mcp-auth.json` |
| `daily.py` | 每日流水线：token 续期轮换 → 五路检索 → 规则选帖 → 14 天去重 → 推飞书 → 回写加密状态 |
| `.github/workflows/daily.yml` | 每天 UTC 01:00（北京 9:00）自动运行 + 手动触发入口 |
| `state/` | 加密的授权状态、去重历史、当日看板（由任务自动提交） |

## 改配置
- 推送时间：改 `daily.yml` 里的 `cron`
- 关键词/条数/去重天数：改 `daily.py` 顶部常量与 `fetch_candidates` 里的检索词
- 简介长度：`intro_of` 里的 40

## 安全
- refresh_token 以 AES-256-CBC 加密存于仓库 `state/`，密钥只在 GitHub Secrets
- 飞书 Webhook 同样只存 Secrets，请勿提交明文到仓库
