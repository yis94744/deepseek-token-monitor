# 🦫 水豚噜噜 · DeepSeek 用量监控

一个运行在 Windows 上的本地工具，实时统计 DeepSeek API 的 token 用量与费用。

## 功能

- **实时统计**：实时记录 token 消耗、输入/输出，精确到每一次请求
- **本地代理**：监听 `127.0.0.1:8787`，在本地转发 DeepSeek API 请求并统计
- **Codex 对话记录**：直接读取 CC Switch 的用量数据库，Codex 对话也能自动记录
- **Kun 用量同步**：自动读取 Kun 客户端的本地会话用量事件（`~/.kun/data/threads/`），Kun 对话自动统计，无需改任何地址
- **DeepSeek Harness 同步**：自动读取 Harness 的会话用量投影缓存（`~/.dsh/storages/session_projcache.json`），Harness 对话自动统计
- **每日更新**：每天 00:00 自动结算当日用量，生成每日总结
- **周/月总结**：自动生成最近 7 天周总结与整月总结
- **动态提示**：每次 token 消耗增加时，悬浮窗弹出 `+N` 动画效果
- **实时计费**：按 DeepSeek 官方现价实时计算费用
- **分类统计**：按模型分别统计，支持 `deepseek-v4-flash` / `deepseek-v4-pro`

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

**方式四：其他客户端走本地代理（可选）**

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
- 四个数据源（本地代理 / CC Switch / Kun / Harness）可在设置页「数据源开关」中分别开关，修改即时生效

## 源码运行 / 打包

支持 Windows + Python 3.8+，克隆仓库后运行：

```
python token_monitor.py          # 运行主程序
pip install pyinstaller          # 打包 exe
pyinstaller DeepSeekTokenMonitor.spec
```

Windows 安装包使用 Inno Setup 打包：`ISCC.exe installer.iss`。

## 素材说明

水豚噜噜 UI 素材来自搜狐文章 https://www.sohu.com/a/967459512_121118784 中的图片，版权归原作者所有，仅供个人学习使用。