# 水豚噜噜 · 优化与稳定性任务清单

> 生成：2026-09-14 · **全部完成：2026-09-14** · 提交 `b631cce`（已推送）

## 完成情况总览（全部为实测结果）

| 项 | 状态 | 实测结果 |
|---|---|---|
| 服务器宕机恢复 | ✅ | 宕机 5 天后恢复；根因是 uvicorn 0.52 硬编码 ProactorEventLoop |
| 服务器自愈 | ✅ | 实测杀掉进程后 9 秒自动拉起（watchdog.log 有记录） |
| 服务器定期清理 | ✅ | 每天 04:30 自动跑，保留 60 天 + 超期按月归档 |
| 服务器健康告警 | ✅ | 每 5 分钟，含「day 落后」业务判据（可识别半死状态） |
| 客户端日志轮转 | ✅ | data 目录 **145.46 MB → 6.45 MB** |
| 客户端健康自检 | ✅ | 底部状态栏着色 + 逐通道明细弹窗 |
| 客户端单实例 | ✅ | 实测第三个实例自动退出 |
| 客户端崩溃兜底 | ✅ | crash.log 含版本/堆栈/操作轨迹 |
| 客户端凭据外置 | ✅ | 密码从未进过 git 历史（已核验） |
| 测试与 CI | ✅ | **13 个套件全跑**（原来只跑 2 个），全部通过 |
| 冗余清理 | ✅ | 项目 **1352.2 MB → 87.5 MB** |
| 数据库备份 | ✅ | 每天 03:30 mysqldump，压缩 + 保留 7 天（磁盘不足自动跳过） |
| 磁盘告警 | ✅ | 剩余 <2GB 或 <15% 时告警 |
| 用户活跃度视图 | ✅ | 管理后台新增，一眼区分全员掉线 vs 个别人掉线 |
| 额外发现并修复 | ✅ | 排名上报用 0 覆盖服务端累计值，导致当天用量丢失 |

### ✅ 两项阻塞已解除（2026-09-14 完成）

| 项 | 结果 |
|---|---|
| **CI 启用** | 已获得 workflow scope，CI 正式生效；**三个 job 全绿**（Py3.10、Py3.13、打包冒烟） |
| **HTTPS 生效** | 443 已放行，外网 TLSv1.3 严格校验通过；客户端已切到 `https://`，实测 0.44s 上报成功 |

**CI 启用后立刻抓到两个真实缺陷**（这正是加 CI 的价值）：
1. 测试脚本在**英文 Windows（cp1252）**下中文 print 抛 `UnicodeEncodeError`，9 个套件全挂
   —— 本地中文 Windows 永远不暴露。已新增 `utf8_output.py` 统一修复。
2. `tools/scan_secrets.py` 同类问题。已一并修复。
   已用 `PYTHONIOENCODING=cp1252` 完整模拟验证：compileall + 14 套件 + 敏感扫描全通过。

### 服务器三个计划任务（均 SYSTEM 身份）

| 任务 | 频率 | 作用 |
|---|---|---|
| `\CloudRank` | 开机 + 每 5 分钟 | 服务自愈（探活失败→清理残留→重启→复验） |
| `\CloudRankCleanup` | 每天 04:30 | 数据清理（保留 60 天、归档、OPTIMIZE、日志清理） |
| `\CloudRankHealth` | 开机 + 每 5 分钟 | 健康检查（HTTP + 业务判据 + 告警） |

### 数据清理策略（cleanup.py）

- `daily_usage`：保留最近 **60 天**（`CLOUDRANK_RETENTION_DAYS` 可调）
- 超期数据：按月归档到 `archive/daily_usage_<YYYY-MM>.jsonl` 后再删除
- 归档保留 **24 个月**，单文件超 20 MB 会提示压缩
- 清理后执行 `OPTIMIZE TABLE` 回收空间
- 日志：轮转副本超过 **14 天**自动删除
- 支持 `--dry-run` 预演，所有动作写入 `cleanup.log`

---

## 〇、本次实测发现的关键事实（先看这个）

| # | 事实 | 实测证据 |
|---|---|---|
| 1 | **云排名服务器已宕机 5 天，客户端毫无感知** | 服务端 `C:\cloud_rank\server.log` 最后写入 **2026-09-09 12:47:28**；之后是 `OSError [WinError 64] 指定的网络名不再可用` 的 accept 失败循环，监听套接字已死；python 进程 PID 5204 仍僵活着 |
| 2 | 服务器本机自检也连不上 | 服务器上 `Invoke-WebRequest http://127.0.0.1/` → "无法连接到远程服务器"；`Get-NetTCPConnection -LocalPort 80` 返回空；netstat 无 80 端口 LISTENING |
| 3 | **MySQL 里最后一条数据停在 09-09** | `users=2`，`daily_usage=6`，最新 day = **2026-09-09**（今天 09-14） |
| 4 | **开机自启计划任务里的端口还是 8000，实际服务跑在 80** | 计划任务 `\CloudRank` 命令为 `uvicorn ... --port 8000`；但实际进程命令行是 `--port 80 --app-dir C:\cloud_rank` |
| 5 | **计划任务上次运行结果 = 1（失败），且没有"失败后重启"设置** | `schtasks /query /tn \CloudRank /v`：上次运行 2026/9/7 18:34:12，上次结果 **1**，作为 SYSTEM 运行，触发器仅"系统启动时" |
| 6 | 客户端后台日志无任何轮转，已膨胀到 **145 MB** | `%APPDATA%\DeepSeekTokenMonitor\data\`：`cc_sync.log` **74.83 MB**（2 秒一条，约 52 万行）、`dsh_sync.log` 17.73 MB、`kun_sync.log` 17.26 MB（已删模块留下的孤儿）、`yq_sync.log` 16.32 MB。全仓库无任何 rotate/truncate 代码 |
| 7 | 排名上报链路的失效是**静默**的 | `rank_client.py` 失败退避 30→60→120→240→300s 封顶，UI 只显示"同步中/同步失败"，`updater.log` 里却是"已是最新版本"——两条通道的健康度完全脱钩 |
| 8 | 服务器凭据为**明文**且已暴露面很大 | 仓库根目录 `deploy_tools.py` 内含服务器 IP + Administrator + 明文密码；该文件虽被 `.gitignore` 忽略但仍是本机明文。同一台机 **22 / 3389 / 3306 / 5985 / 445 全部对公网 LISTENING** |
| 9 | 客户端多开无互斥保护 | 全代码无 Mutex/单实例检测；两个实例抢 8787 端口，第二个静默 "代理 启动失败"，用户不知道自己在用一个不统计的实例 |
| 10 | 工程化缺口 | 仓库**无 `.github`（无 CI）**、**无 pip 依赖清单**（`config.example.json`/`cloud_rank/requirements.txt` 之外无 `requirements.txt`）、8 个 `test_*.py` 但只有 2 个进了自动跑（`.tmp/run_tests_wrapper.py`）、无 lint/format 配置 |
| 11 | 文档与现实脱节 | `CONTEXT_SUMMARY.md` 写"v1.13.2 / 高峰 09:00-14:00 / 已删除 dsh_sync.py"，实际是 **v1.13.28**、高峰 `[[9,12],[14,18]]`、`dsh_sync.py` 在线 |
| 12 | 服务器 C 盘余量紧张 | C: 已用 24.1 GB / 剩余 **15.8 GB**；另有历史计划任务残留 `CloudRankDiag` |

---

## 一、软件优化任务清单（桌面端 + 仓库工程）

### P0 — 影响可用性 / 会继续恶化，建议立刻做

- [ ] **客户端日志轮转与总量封顶**
  - 给 `cc_switch_sync.py` / `dsh_sync.py` / `codebuddy_sync.py` / `workbuddy_sync.py` 的 `_log()` 抽成公共模块（如 `logutil.py`），统一实现：
    1. 单文件超过 **5 MB** → 改名 `.1` 并重开（保留 1 份历史）；
    2. 只有"状态变化"才写日志（本轮新增 = 0 且信息未变时跳过），把 2 秒一条降到"有变化才写"；
    3. 启动时清理已删模块的孤儿日志（`kun_sync.log`、`yq_sync.log` 等）。
  - 验收：连续运行 1 小时后 `data/` 下任一 `.log` < 5 MB；`cc_sync.log` 不再每 2 秒增长。
  - 预计收益：单机释放 ~140 MB，并消除"用户开一年后磁盘被日志写满"的隐患。

- [ ] **分级健康自检 + 主动告警（把"静默失败"变成"看得见的红字"）**
  - 统一一个 `health` 快照：数据源同步、本地代理端口、余额接口、排名上报各带 `last_ok_at` / `consecutive_failures`。
  - 状态栏（`lbl_*_status` 一带）按"最后成功时间距今"着色：< 5 min 绿 / < 1 h 黄 / **> 1 h 红并给出人话原因**。
  - 排名通道额外判据：**服务器返回的 `day` 落后本地日期 ≥ 1 天** ⇒ 直接判定"服务器异常"，不再显示"同步中"。
  - 验收：手动停掉云服务，客户端 1 小时内应显示红色"排名服务器异常（最后成功 HH:MM）"。

- [ ] **单实例互斥**
  - 启动时用命名 Mutex（`CreateMutexW` / `msvcrt.locking` 锁文件）检测；已存在实例则唤起窗口并退出，避免双开抢 8787。
  - 验收：双击两次只留一个进程；第二个实例不再出现"代理启动失败"。

- [ ] **全局异常兜底 + 崩溃留痕**
  - 补 `sys.excepthook` / `threading.excepthook` / `Tk.report_callback_exception`，统一写 `data/crash.log`（含版本号、堆栈、最近 20 行操作）。
  - 验收：人为抛异常后 `crash.log` 有完整堆栈，程序不整体退出。

- [ ] **服务器凭据外置（安全）**
  - 删除本机 `deploy_tools.py` 中的明文密码，改为读环境变量 `RANK_SSH_PWD`；确认该文件从未进过 git 历史。
  - 服务器侧：关闭公网 3389/5985/445（改走跳板或仅放行自己的出口 IP），MySQL 3306 收回到 `127.0.0.1`。

### P1 — 体验与正确性

- [ ] **服务器地址可配置 + 故障可切换**
  - `rank_client._DEFAULT_SERVER` 硬编码在代码里 ⇒ 服务器迁移必须发版。改为：内置默认值 + `settings.json` 覆盖 + **设置页可填**；连接失败时给出"服务器地址"入口。
- [ ] **`/api/report` 不再回传全量榜单**
  - 现在每次上报（30s/台）都返回 200 人完整榜单，是流量放大器。改为返回 `{my_rank, my_tokens, board_version, board}`，并用 **ETag / `since_version`** 让客户端"没变就不重传"。
- [ ] **tick 热路径减负**
  - `_tick()` 每 1.5 秒无条件执行 `today_stats() + new_requests_since()`（两次全连接 + 全表聚合）。23710 行数据下尚可，但随历史增长线性变差。
  - 改：`max_request_id()` 轮询走内存哨兵；`today_stats()` 结果缓存 1s；为 `requests(date, model)` 补覆盖索引。
- [ ] **存储层连接复用**
  - `storage._conn()` 每次操作新建连接（一操作一连接 + 全局锁串行）。改为 **thread-local 长连接**（WAL 已开，读多写少），配合 `PRAGMA synchronous=NORMAL`。
  - 验收：`_tick` 一轮内 SQLite 打开次数从 ~6 降到 0（复用）。
- [ ] **测试与 CI 补全**
  - 新增根目录 `requirements.txt`（`pillow` / `pystray` / `pyinstaller`；打包用）+ `requirements-dev.txt`。
  - 建 `.github/workflows/ci.yml`：`windows-latest` 上跑 **全部 8 个 test_*.py**（现在只跑 2 个）+ `python -m compileall`。
  - 把 `.tmp/run_tests_wrapper.py` 里的沙箱兼容层提升为正式 `tests/conftest.py` 式写法，去掉对 `.tmp` 的依赖。
- [ ] **发布前敏感信息扫描**
  - CI 加一步扫描（账号/token/绝对路径/邮箱），防止 `config.json`、`ranking.json`、`deploy_tools.py` 之类的文件被误提交。

### P2 — 打磨

- [ ] **文档对齐**：`CONTEXT_SUMMARY.md` 更新到 v1.13.28（版本号、高峰时段 `09:00-12:00 / 14:00-18:00`、dsh_sync 已恢复）；`README.txt` 里的单价表还停留在 2026-08-13，已过期 4 轮调价。
- [ ] **代理层健壮性**：`proxy_server` 用 `HTTP/1.0` 每请求关连接（实现简单但放弃了 keep-alive），长会话客户端会有额外握手开销；可评估升级到 1.1 + `Content-Length` 转发。
- [ ] **界面**：页签从 10 个继续增长后，可考虑二级分组（统计 / 排行 / 设置）或"自定义显示页签"。
- [ ] **`updater` 与排名通道解耦提示**：`updater.log` 显示"已是最新版本"不代表整体健康，UI 不应让用户产生"一切正常"的错觉（与 P0 第 2 条联动）。

---

## 二、服务器稳定性任务清单（106.52.172.73 · CloudRank）

### P0 — 先让它别死，死了有人管

- [ ] **修复开机自启计划任务的端口不一致（当前最致命）**
  - `\CloudRank` 里仍是 `--port 8000`，实际服务跑在 80 ⇒ 服务器一旦重启，服务要么起不来、要么起在没人访问的 8000。
  - 动作：`schtasks /change` 或重新注册，命令统一为
    `python -m uvicorn app:app --host 0.0.0.0 --port 80 --app-dir C:\cloud_rank`。
- [ ] **立刻恢复服务并验证端到端**
  - 杀掉僵死 PID（监听已失效），用 `start_server.py` 拉起；从**外网**验证 `GET http://106.52.172.73/api/board` 返回 401（而不是 502/超时），再验证客户端能上报。
  - 注意：这台机上当前**没有任何进程监听 80**，所以恢复后必须确认 80 真的被占用（`Get-NetTCPConnection -LocalPort 80`）。
- [ ] **计划任务必须具备自愈能力**
  - 现在触发器只有"系统启动时"，且"上次结果=1"。改为：
    1. 触发器增加 **每 5 分钟重复**；
    2. 设置 **失败后重启：间隔 1 分钟，重试 3 次**；
    3. 加 `-MultipleInstances IgnoreNew`（避免重复拉起撞端口，本机已经出现过 `Errno 10048 bind 8000`）；
    4. `Stop-Process` 前置清理：启动脚本先杀掉旧 uvicorn，再拉起。
  - 更稳的做法：用 **NSSM / WinSW 注册为 Windows 服务**（自带崩溃重启 + 日志轮转），比计划任务可靠。
- [ ] **外部健康检查 + 告警（现在宕机 5 天没人知道）**
  - 最小可用方案：另一台机器/本机客户端定时 `GET /api/board`，连续 3 次失败就推送（企业微信机器人 / 邮件 / Bark 任一）。
  - 顺带把"服务器返回的 day 落后当天"也纳入判据（能抓出"进程活着但业务停止"这种半死状态）。
- [ ] **应用层加固 `accept` 崩溃**
  - `WinError 64 / OSError(22)` 出现在 uvicorn 的 `IocpProactor.accept`，说明 proactor 事件循环在长跑后 accept 失败且任务异常无人回收。
  - 动作：① 升级 `uvicorn`/`starlette`（当前 uvicorn 0.52.4、starlette 1.6.0）；② Windows 上显式改用 `--loop asyncio`（selector 系）；③ 给 `app` 挂 `lifespan` 健康日志，每 N 分钟打印心跳，便于区分"僵活"与"真死"。
- [ ] **日志轮转**
  - `C:\cloud_rank\server.log` 已 736 KB 且混合了 schtasks 输出；uvicorn 无轮转时长期会把盘写满（C 盘只剩 15.8 GB）。
  - 动作：改由 `logging.handlers.RotatingFileHandler`（10 MB × 5）承载，或服务化后用 NSSM 的 stdout 轮转。

### P1 — 可用性、安全与容量

- [ ] **HTTPS + 域名**
  - 现在是**明文 HTTP`，登录令牌与邮箱走公网**。加一层 Nginx/Caddy 反代 + Let's Encrypt 证书，客户端默认地址同步切 `https://`。
  - 迁移时用 `rank_client.make_client()` 里已有的"旧地址自动升级"逻辑扩展成通用迁移。
- [ ] **会话令牌策略收紧**
  - `cloud_rank` 令牌 **365 天**且每次登录刷新；泄露即长期有效。改：令牌加 `last_used`，30/90 天滑动过期 + 可在管理后台"强制下线"。
- [ ] **请求限流与防刷（README 里自己列的待办）**
  - `/api/report` 限制单账号最小上报间隔（如 10s）与单 IP 速率；注册接口加简单人机校验/验证码，防止刷号占榜。
- [ ] **数据库与备份**
  - MySQL 与 Web 服务同机；加每日 `mysqldump` 到独立目录并保留 7 天（`C:\dailykit` 下已有类似备份习惯，可直接复用）。
  - `/api/admin/users` 的 `LIKE '%%q%%'` 在用户量上来后全表扫；补 `email` 索引或改前缀匹配。
- [ ] **腾讯云安全组与系统防火墙收敛**
  - 当前对外 LISTENING：`22 / 445 / 3389 / 3306 / 5985 / 47001`。至少：3306 与 445 不对公网；3389 限定来源 IP；5985(WinRM) 与 47001 关闭。
- [ ] **磁盘与配额告警**
  - C 盘剩余 15.8 GB，加"剩余 < 15% 告警"；清理 `C:\dailykit\node.zip`、`node22.zip`（合计 ~63 MB）与残留计划任务 `CloudRankDiag`。
- [ ] **监控面板补"最后上报时间"**
  - 管理后台的"近 30 分钟活跃"已经用 `updated_at` 判断，很好；再加一张"每个用户的最后上报时间距今天数"列表，能一眼看出"全平台都停了"还是"个别用户掉线"。

### P2 — 工程化

- [ ] **服务端纳入版本管理**：`server_rank/` 目前是未跟踪状态，`cloud_rank` 也不在仓库（只有源码副本）。至少把**线上那份 `C:\cloud_rank\app.py` 与仓库保持哈希一致**（本次核对：两边 SHA256 一致，`FC7B3D21...AF5C`，说明是同一版；应固化为发布流程的一步）。
- [ ] **服务器端也加依赖清单校验**：线上 `fastapi 0.141.1 / starlette 1.6.0 / uvicorn 0.52.4 / SQLAlchemy 2.0.52 / PyMySQL 1.2.0` 与 `cloud_rank/requirements.txt` 的宽松约束对不上，应固定版本便于回滚。
- [ ] **`server_rank`（C++ 组织版）决策**：它与 `cloud_rank`（现网全平台版）是**两套并存的后端**，请求体字段不兼容（`tokens_delta` vs 当日累计 `tokens`）。要么明确下线 `server_rank`，要么写清两者关系，避免以后误部署。
- [ ] **服务器时间与时区**：已核实为 `China Standard Time (+08:00)`，与客户端"北京时间自然日"口径一致 ✅（此项无需改动，仅记录以免以后误判）。

---

## 三、建议的执行顺序（一次会话可完成的最小闭环）

1. **恢复云服务** + 修正计划任务端口（P0 服务器 1–2）→ 立刻让榜恢复。
2. **加健康告警**（P0 服务器 4）→ 下次再宕机 5 分钟内知道。
3. **日志轮转**（P0 软件 1）→ 顺手清掉 140 MB。
4. **单实例 + 崩溃兜底 + 健康状态着色**（P0 软件 2–4）→ 客户端不再"静默装死"。
5. 其余按 P1 / P2 排期发版。

---

## 附：本次核验方式（可复现）

- 仓库：读源码 + `git log/status` + 跑 `.tmp/run_tests_wrapper.py`（2 套件全 PASS）。
- 本机：`%APPDATA%\DeepSeekTokenMonitor\` 文件清单与大小、`usage.db` 的 23710 条记录。
- 服务器：SSH(22) 只读诊断 5 轮（进程/端口/服务/计划任务/事件日志/磁盘/MySQL 行数/文件哈希），**未做任何写操作**。
- 外网：直连（bypass 系统 Clash 代理）观测到 TCP 可连、HTTP 无响应 → 与"监听缺失"吻合。

> 待确认项：客户端默认地址是 `http://106.52.172.73`（80 端口明文）。切换 HTTPS 前需先确定域名与证书方案。