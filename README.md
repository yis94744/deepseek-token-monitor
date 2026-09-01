# 🦫 水豚噜噜 · DeepSeek 用量监控

一个运行在 Windows 上的本地工具，实时统计 DeepSeek API 的 token 用量与费用。

## 功能

- **实时统计**：实时记录 token 消耗、输入/输出，精确到每一次请求
- **本地代理**：监听 `127.0.0.1:8787`，在本地转发 DeepSeek API 请求并统计
- **Codex 对话记录**：直接读取 CC Switch 的用量数据库，Codex 对话也能自动记录
- **YQ Harness 同步**：自动读取 YQ Harness 的会话用量投影缓存（`%APPDATA%\YQ\yq-home\storages\session_projcache.json`，支持 `YQ_HOME` 环境变量），YQ 对话自动统计
- **CodeBuddy 用量同步**：自动读取 CodeBuddy（腾讯云代码助手）日志中的 Agent 回合用量（`%APPDATA%\CodeBuddy CN\logs\`），CodeBuddy 对话自动统计，无需改任何地址
- **WorkBuddy 用量同步**：自动读取 WorkBuddy CLI 会话文件中的调用用量（`~/.workbuddy/projects/*/*.jsonl`），WorkBuddy 对话自动统计
- **每日更新**：每天 00:00 自动结算当日用量，生成每日总结
- **周/月总结**：自动生成最近 7 天周总结与整月总结
- **动态提示**：每次 token 消耗增加时，悬浮窗弹出 `+N` 动画效果
- **实时计费**：按 DeepSeek 官网峰谷价计算费用（2026-08-17 起生效：每日 09:00-14:00 高峰时段价格翻倍；之前的调用按旧平峰价结算），单价可在配置中调整
- **分类统计**：按模型分别统计，支持 `deepseek-v4-flash` / `deepseek-v4-pro`
- **每日统计**：每日费用明细页——近 30 天费用柱状图 + 全部历史逐日明细表（日期/请求数/输入/输出/命中/Token/费用）
- **余额统计**：账户余额每日快照（刷新成功自动记录）——近 30 天余额走势折线图 + 每日余额明细表（充值/扣款分列：充值=当日到账，扣款=当日实际消耗，符号正确）
- **时段统计**：高峰/非高峰分时统计——不同时段按各自计费规则（高峰 09:00-14:00 价格翻倍），近 30 天堆叠柱状图 + 每日高峰/非高峰明细表（请求/Token/费用）
- **按 API Key 统计**：代理转发时按 `Authorization` 头自动识别 API Key（只存 SHA-256 指纹，不存明文），近 30 天各 Key 费用堆叠图 + 本月/今日各 Key 明细表——只覆盖经本地代理的流量，官方无免登录按 Key 用量接口
- **桌宠形态**：设置页可在「悬浮窗 / 桌宠」两种桌面挂件形态间切换。桌宠为透明底水豚，可拖动、按压回弹；**点击桌宠弹出信息面板**，展示①当前余额 ②今日总消耗 ③今日当前 Key 消耗金额+Token 数量 ④当前计价时段（高峰显示「梁文峰」、低谷显示「梁文谷」）
- **自动更新**：启动及每 6 小时检查 GitHub Releases 最新版本——发现新版本弹窗提示，点击「更新」自动下载（带进度条）、自动退出安装、安装完成自动启动新版覆盖旧版；状态栏常驻显示更新状态（设置页可手动「检查更新」）

## 下载安装

- 前往 [Releases](https://github.com/yis94744/deepseek-token-monitor/releases) 下载安装程序
- 首次启动会引导填写 API Key，可跳过（跳过仅影响余额显示，用量照常统计），之后可在设置页修改

## 使用方法

**方式一：Codex / CC Switch（推荐，无需改任何地址）**

1. 启动软件即可，首次启动的 API Key 引导可以跳过
2. 软件会只读 CC Switch 的本地数据库 `~/.cc-switch/cc-switch.db`，每 2 秒自动同步
3. Codex 里的对话用量会自动进入统计，不经过任何转发，不影响响应速度

**方式二：YQ Harness（自动，无需任何配置）**

1. 启动软件即可，YQ Harness 会把会话用量投影写到 `%APPDATA%\YQ\yq-home\storages\session_projcache.json`（桌面端通过 `YQ_HOME` 环境变量指定数据目录，软件自动识别）
2. 软件每 5 秒只读同步，按会话差分导入 token 用量并计费
3. 模型归属：手动映射 > 本地 API 解析 > YQ `settings.yaml` 默认模型（`agent-default-model`），解析不到时按 `unknown_model_fallback` 计费

**方式三：CodeBuddy（自动，无需任何配置）**

1. 启动软件即可，CodeBuddy 每次 Agent 回合结束会把用量写入 `%APPDATA%\CodeBuddy CN\logs\` 下的扩展日志
2. 软件每 10 秒只读同步，把每次回合的输入/输出 token 导入并计费
3. 模型名从日志自动识别（如 DeepSeek-V4 Pro / Deepseek-V4-Flash），识别不到时按 `unknown_model_fallback` 计费；也可在 `codebuddy.model` 中手动指定

**方式四：WorkBuddy（自动，无需任何配置）**

1. 启动软件即可，WorkBuddy CLI 会把每次模型调用写入 `~/.workbuddy/projects/<项目目录>/<会话ID>.jsonl`
2. 软件每 10 秒只读同步，把每次调用的 token 用量导入并计费
3. 模型名取自调用记录的 `providerData.model`

**方式五：其他客户端走本地代理（可选）**

1. 将客户端 DeepSeek 的 `base_url` 从 `https://api.deepseek.com` 改为 `http://127.0.0.1:8787`
2. 流式请求需开启 `"stream_options": {"include_usage": true}` 才能返回精确的 token 用量
3. 请求由本地代理转发到 DeepSeek 官方接口并自动计费

## 配置说明

将 `config.example.json` 复制为 `config.json`，放在 `%APPDATA%\DeepSeekTokenMonitor\` 目录（软件首次启动也会自动创建）。

- `api_key`：DeepSeek API Key，用于查询余额
- `proxy_port`：本地代理监听端口
- `models`：各模型输入/输出 token 单价与计费规则
- `cc_switch`：CC Switch 同步设置（默认开启，读取 `~/.cc-switch/cc-switch.db`）
- `yq`：YQ Harness 同步设置（默认开启，读取 `%APPDATA%\YQ\yq-home\storages\session_projcache.json`，可用 `projcache_path` 覆盖默认路径；优先识别 `YQ_HOME` 环境变量）
  - `api_base`：YQ 本地地址（默认 `http://127.0.0.1:3080`，与 Harness 默认端口一致；YQ 桌面端端口随机时可不填，自动退回 settings.yaml 默认模型）
  - `model_refresh_seconds`：模型缓存刷新间隔（默认 300 秒）
  - `models`：可选手动指定 `{会话ID: 模型名}`，优先级高于自动解析
- `codebuddy`：CodeBuddy 用量同步设置（默认开启，读取 `%APPDATA%\CodeBuddy CN\logs\`，可用 `logs_dir` 覆盖日志目录）
  - `sync_interval_seconds`：同步间隔（默认 10 秒）
  - `model`：可选手动指定模型名（如 `deepseek-v4-pro`），不填则从日志自动识别
- `workbuddy`：WorkBuddy 用量同步设置（默认开启，读取 `~/.workbuddy/projects/`，可用 `projects_dir` 覆盖目录）
  - `sync_interval_seconds`：同步间隔（默认 10 秒）
- `update_check`：自动更新检测设置（默认开启）
  - `enabled`：是否启用自动更新检测
  - `interval_hours`：检查间隔小时数（默认 6）
- 五个数据源（本地代理 / CC Switch / YQ Harness / CodeBuddy / WorkBuddy）可在设置页「数据源开关」中分别开关，修改即时生效

## 源码运行 / 打包

支持 Windows + Python 3.8+，克隆仓库后运行：

```
python token_monitor.py          # 运行主程序
pip install pyinstaller pillow pystray   # 打包 exe（pillow 渲染悬浮球；pystray 系统托盘）
pyinstaller DeepSeekTokenMonitor.spec
```

Windows 安装包使用 Inno Setup 打包：`ISCC.exe installer.iss`。

## 素材说明

水豚噜噜 UI 素材来自搜狐文章 https://www.sohu.com/a/967459512_121118784 中的图片，版权归原作者所有，仅供个人学习使用。