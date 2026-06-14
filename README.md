# wechat-remind

本项目是一个本地运行的单用户微信备忘提醒机器人。

它只做一条很窄的路径：

- 单个微信机器人账号
- 只绑定第一个私聊联系人作为主人
- 只处理私聊文本提醒
- 本地 SQLite 保存登录态、上下文、提醒任务和工具调用日志
- 使用 OpenAI Responses API，默认模型 `gpt-5.5`

微信 iLink HTTP 协议参考了公开的 `@tencent-weixin/openclaw-weixin` 实现。它不是稳定公开 API，后续可能因为微信侧协议变化而失效。

## Ubuntu 快速安装

先安装系统依赖：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git ca-certificates tzdata
```

拉取项目并安装 Python 环境：

```bash
git clone https://github.com/KamiPasi/wechat-remind.git
cd wechat-remind
bash scripts/install_ubuntu.sh
```

编辑 `.env`，至少填这三项：

```env
OPENAI_API_KEY=sk-your-key
OPENAI_BASE_URL=https://vip.zen-ai.top/
OPENAI_MODEL=gpt-5.5
```

然后运行：

```bash
source .venv/bin/activate
python -m wechat_remind doctor --skip-api
python -m wechat_remind login
python -m wechat_remind run
```

`login` 会在终端打印二维码。扫码登录后，第一次给机器人发私聊消息的微信用户会被绑定为主人。

长期运行可以用 `tmux`：

```bash
tmux new -s wechat-remind
source .venv/bin/activate
python -m wechat_remind run
```

按 `Ctrl+B` 再按 `D` 可以让它留在后台运行。恢复窗口：

```bash
tmux attach -t wechat-remind
```

## Windows 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env
```

然后编辑 `.env`，再运行：

```powershell
python -m wechat_remind doctor --skip-api
python -m wechat_remind login
python -m wechat_remind run
```

## 配置说明

程序会自动从当前目录或父目录加载 `.env`。`.env` 里的值会覆盖同名系统环境变量，避免旧的 OpenAI Key 误用。

常用配置：

```env
OPENAI_API_KEY=sk-your-key
OPENAI_BASE_URL=https://vip.zen-ai.top/
OPENAI_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=low
OPENAI_TIMEOUT_SECONDS=60

BOT_TIMEZONE=Asia/Shanghai
BOT_SYSTEM_PROMPT_PATH=prompts/custom.md
WECHAT_REMIND_DB=data/wechat_remind.sqlite3
WECHAT_BOT_AGENT=WechatRemind/0.1.0
```

`BOT_SYSTEM_PROMPT_PATH` 和 `WECHAT_REMIND_DB` 可以写相对路径。相对路径会按 `.env` 所在目录解析。

如果要显式指定配置文件：

```bash
export WECHAT_REMIND_ENV_FILE=/path/to/wechat-remind/.env
```

## 常用命令

```bash
python -m wechat_remind list
python -m wechat_remind cancel 1
python -m wechat_remind tool-logs --limit 20
python -m wechat_remind doctor --skip-api
```

`tool-logs` 会显示最近的模型决策和本地工具执行记录，包括来源、动作、参数、回复、执行结果和错误。

来源含义：

- `model`：Responses API 返回的 function call 或普通回复
- `local_parser`：本地规则直接识别的常见提醒指令
- `tool_execute`：本地实际执行了创建、查询或取消提醒

## 多轮对话上下文

机器人会保存最近 20 条用户/助手文本，用于支持多轮确认。例如你先说“提醒我开会”，机器人追问时间，你再说“10 分钟后”。

如果 1 小时内没有新的聊天指令，下一次新消息到达时会自动清空旧上下文，避免很久以前的对话影响新的提醒解析。

## 自定义提示词

编辑 `prompts/custom.md` 可以追加你自己的行为规则。建议只写偏好和表达风格，不要删除内置提醒解析要求。

模型收到的消息里会包含当前本地时间和时区，用于解析“10 分钟后”“明天上午 9 点”这类相对时间。

## 数据和隐私

本地数据默认保存在 `data/wechat_remind.sqlite3`。`.env`、`data/`、虚拟环境和缓存目录都在 `.gitignore` 里，不会提交到 GitHub。

公开仓库只包含源码、测试、脚本、README 和 `.env.example`。不要把真实 API Key 写进 `.env.example` 或 README。
