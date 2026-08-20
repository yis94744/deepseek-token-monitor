# 🦫 水豚噜噜 · DeepSeek 用量监控

一个运行在 Windows 上的本地工具，实时统计 DeepSeek API 的 token 用量与费用。

## 功能

- **实时统计**：实时记录 token 消耗、输入/输出，精确到每一次请求
- **本地代理**：监听 `127.0.0.1:8787`，在本地转发 DeepSeek API 请求并统计
- **Codex 对话记录**：直接读取 CC Switch 的用量数据库，Codex 对话也能自动记录
- **Kun 用量同步**：自动读取 Kun 客户端的本地会话用量事件（`~/.kun/data/threads/`），Kun 对话自动统计，无需改任何地址
- **DeepSeek Harness 同步**：自动读取 Harness 的会话用量投影缓存（`~/.dsh/storages/session_projcache.json`），Harness 对话自动统计
- **YQ Harness 同步**：自动读取 YQ Harness 的会话用量投影缓存（`%APPDATA%\YQ\yq-home\storages\session_projcache.json`，支持 `YQ_HOME` 环境变量），YQ 对话自动统计
- **每日更新**：每天 00:00 自动结算当日用量，生成每日总结
- **周/月总结**：自动生成最近 7 天周总结与整月总结
- **动态提示**：每次 token 消耗增加时，悬浮窗弹出 `+N` 动画效果
- **实时计费**：按 DeepSeek 官网峰谷价计算费用（2026-08-17 起生效：每日 09:00-14:00 高峰时段价格翻倍；之前的调用按旧平峰价结算），单价可在配置中调整
- **分类统计**：按模型分别统计，支持 `deepseek-v4-flash` / `deepseek-v4-pro`
- **每日统计**：每日费用明细页——近 30 天费用柱状图 + 全部历史逐日明细表（日期/请求数/输入/输出/命中/Token/费用）
- **自动更新检测**：启动及每 6 小时检查 GitHub Releases 最新版本，发现新版本弹窗提示并可一键跳转下载（设置页可手动「检查更新」）

## 下载安装

- 前往 [Releases](https://github.com/yis94744/deepseek-token-monitor/releases) 下载安装程序
- 首次启动会引导填写 API Key，可跳过（跳过仅影响余额显示，用量照常统计），之后可在设置页修改

## 使用方法

**方式一：Codex / CC Switch（推荐，无需改任何地址）**

1. 启动软件即可，首次启动的 API Key 引导可以跳过
2. 软件会只读 CC Switch 的本地数据库 `~/.cc-switch/cc-switch.db`，每 2 秒自动同步
3. Codex 里的对话用量会自动进入统计，不经过任何转发，不影响响应速度

**方式二：Kun（自动，无需任何配置）**

1. 启动软件即可，Kun 客户端会把每次模型调用的用量事件写到 `~/.kun/data/threads/<线程ID>/events.jsonl`
2. 软件每 3 秒只读同步，自动还原单轮 token 用量并计费
3. 任何人的电脑上安装后都会自动定位到当前登录用户的 Kun 数据目录

**方式三：DeepSeek Harness（自动，无需任何配置）**

1. 启动软件即可，Harness 会把会话用量投影写到 `~/.dsh/storages/session_projcache.json`
2. 软件每 5 秒只读同步，按会话差分导入 token 用量并计费

**方式四：YQ Harness（自动，无需任何配置）**

1. 启动软件即可，YQ Harness 会把会话用量投影写到 `%APPDATA%\YQ\yq-home\storages\session_projcache.json`（桌面端通过 `YQ_HOME` 环境变量指定数据目录，软件自动识别）
2. 软件每 5 秒只读同步，按会话差分导入 token 用量并计费
3. 模型归属：手动映射 > 本地 API 解析 > YQ `settings.yaml` 默认模型（`agent-default-model`），解析不到时按 `unknown_model_fallback` 计费

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
- `kun`：Kun 同步设置（默认开启，读取 `~/.kun/data/threads/`，可用 `threads_dir` 覆盖默认目录）
- `dsh`：DeepSeek Harness 同步设置（默认开启，读取 `~/.dsh/storages/session_projcache.json`，可用 `projcache_path` 覆盖默认路径）
  - `api_base`：Harness 本地地址（默认 `http://127.0.0.1:3080`），用于解析每个会话使用的模型（pro / flash），保证计费正确
  - `model_refresh_seconds`：模型缓存刷新间隔（默认 300 秒）
  - `models`：可选手动指定 `{会话ID: 模型名}`，优先级高于自动解析
- `yq`：YQ Harness 同步设置（默认开启，读取 `%APPDATA%\YQ\yq-home\storages\session_projcache.json`，可用 `projcache_path` 覆盖默认路径；优先识别 `YQ_HOME` 环境变量）
  - `api_base`：YQ 本地地址（默认 `http://127.0.0.1:3080`，与 Harness 默认端口一致；YQ 桌面端端口随机时可不填，自动退回 settings.yaml 默认模型）
  - `model_refresh_seconds`：模型缓存刷新间隔（默认 300 秒）
  - `models`：可选手动指定 `{会话ID: 模型名}`，优先级高于自动解析
- `update_check`：自动更新检测设置（默认开启）
  - `enabled`：是否启用自动更新检测
  - `interval_hours`：检查间隔小时数（默认 6）
- 五个数据源（本地代理 / CC Switch / Kun / DeepSeek Harness / YQ Harness）可在设置页「数据源开关」中分别开关，修改即时生效

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