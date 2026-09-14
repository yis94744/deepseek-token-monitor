# 水豚噜噜 · 组织 Token 排名后端（server_rank）

纯 C++ 实现的 HTTP 后端，用于把使用 DeepSeek 的 **token 消耗量**在“组织”内做排名，并提供**每日排名**与**每周总结排名**。

> 隐私原则：本服务**只接收并保存「token 数 + 组织归属」**用于排名，**不采集**任何对话内容 / API Key / 账号余额 / 请求明细等关键信息。

---

## 功能

- 账号注册、登录（登录后返回令牌，之后凭令牌访问）
- 创建组织（创建者即组织主）
- 加入组织；**若组织不存在，返回 `ORG_NOT_FOUND`**，由客户端询问是否创建
- 客户端**每 1 分钟上报**本账号累计 token 增量 → 服务器按当天汇总
- **今日**组织内排名
- **每周总结**（本周 / 指定周）排名，token 按日、按周分别累计

## 数据模型（MySQL，库名 `token_rank`）

| 表 | 说明 |
|---|---|
| `users` | 用户（含盐哈希密码，非明文） |
| `organizations` | 组织（owner_id = 创建者） |
| `org_members` | 成员关系（一人可加多个组织） |
| `token_daily` | 每日 token 汇总，`day` 一天一行（**token 每日重置更新**） |
| `weekly_rank` | 每周汇总（`week_start` = 该周周一），用于每周总结 |
| `sessions` | 登录令牌 |

## API 一览（JSON over HTTP，端口默认 8899）

鉴权：请求头 `Authorization: Bearer <登录返回的token>`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/health` | 服务健康（根路径 `/`） |
| POST | `/api/register` | `{username, password}` 注册 |
| POST | `/api/login` | `{username, password}` → `{token, user_id, username, orgs[]}` |
| GET  | `/api/me` | 当前用户 + 所属组织 |
| POST | `/api/org/create` | `{name}` 创建组织 |
| POST | `/api/org/join` | `{name}` 加入；不存在返回 `ORG_NOT_FOUND` |
| POST | `/api/report_token` | `{org_id, tokens_delta}` 上报增量（每分钟） |
| GET  | `/api/rank/today?org_id=x` | 今日组织内排名 |
| GET  | `/api/rank/week?org_id=x&week=YYYY-MM-DD` | 每周总结（缺省 week=本周） |

### 上报语义
客户端每次把 `自上次上报以来新增的 token 数`（增量，≥0）POST 给 `/api/report_token`。服务器把该增量累加到「当天」与「当周」的用户记录上，因此：
- **每天自然刷新**：日期切换后，新的一天从 0 重新累计（token_daily 按 `day` 分行）。
- **每周总结**：weekly_rank 按周一 `week_start` 分行，跨周自动分到新周。

## 目录结构

```
server_rank/
├── src/
│   ├── server.cpp   # 主程序：HTTP 路由 + 全部业务接口
│   ├── db.h/.cpp    # MySQL 访问封装（MySQL Connector/C 的 C API）
│   └── util.h/.cpp  # SHA-256、随机令牌、时间/周计算、名称校验
├── sql/
│   └── schema.sql   # 建库建表脚本
├── vendor/          # 自动下载的第三方头文件（httplib.h、nlohmann/json.hpp）
├── config.example.json
├── deploy.bat        # ★ 双击即可：自动装工具链→下依赖→建库→生成配置→编译→启动（ASCII 安全）
├── 一键部署.bat      #  同 deploy.bat 的中文名副本（双击也行）
├── auto_setup.ps1    # 一键部署的具体实现（被上面的 .bat 调用）
├── fetch_deps.ps1    # 仅下载第三方头文件（含 jsDelivr 国内镜像，GitHub 被墙也能下）
├── download-mingw.bat/ps1  # 在能上网的电脑上双击，自动下载 MinGW 并生成 tools\mingw.zip
├── build.bat         # 仅编译
└── start.bat         # 仅启动（已编译好时用）
```

## 部署到腾讯云 Windows 的步骤

### 方式 A：全自动（推荐）—— 双击一个脚本搞定
把整个 `server_rank` 文件夹拷到服务器后，**双击 `deploy.bat`（或 `一键部署.bat`）**，脚本会自动：
1. 定位 **MinGW-w64 (g++)**：先查系统已有 → winget（若装了）→ **离线包 `tools\mingw.zip`**（推荐，不联网）；不会默认联网下大文件（避免卡死），确需联网请加 `-DownloadMinGW`
2. 定位 **MySQL Connector/C**：先找系统已装的，其次用**离线包 `tools\mysql-connector.zip`**（推荐）；都没有才联网下载（限短超时）
3. 下载 **cpp-httplib / nlohmann-json** 到头文件目录 vendor\
4. 若本机有 mysql.exe，自动**建库并导入 schema.sql**（会问你一次数据库 root 密码，可跳过）
5. 生成 **config.json**（会问你数据库地址/账号/密码、服务端口）
6. 自动**编译**成 token_rank_server.exe
7. 自动**启动**服务器

> 前提：服务器上已有 MySQL 服务并启动；需要网络。
>
> **服务器下大文件困难时的推荐流程（两步）：**
> 1. 在**能上网的电脑**上双击 `download-mingw.bat`，会自动从 GitHub 下载 winlibs MinGW 并生成 `tools\mingw.zip`（无需手动找文件）。
> 2. 把整个 `server_rank` 文件夹（含生成的 `tools\mingw.zip`）拷到服务器，双击 `deploy.bat`——MinGW 直接用离线包，MySQL Connector 如连不上也可放 `tools\mysql-connector.zip`。

> ⚠️ 编码说明：所有 `.bat` 均为**纯 ASCII**（不含中文），中文只出现在带 UTF-8 BOM 的 `.ps1` 里，避免中文 Windows 下 cmd 的代码页把 `.bat` 解析乱（常见报错如 `'xxx' 不是内部或外部命令`）。请勿把中文写进 `.bat` 文件。

### 方式 B：分步手动（不放心自动时）
1. 导入建表脚本：
   ```bat
   mysql -uroot -p < sql\schema.sql
   ```
2. 装 MinGW-w64，把 `bin` 加入 PATH；`g++ --version` 可显示即成功。
3. 装 MySQL Connector/C，设置环境变量（或用下方目录探测）：
   ```bat
   set MYSQL_INCLUDE=C:\你的\mysql\include
   set MYSQL_LIB=C:\你的\mysql\lib
   ```
4. 复制 config.example.json 为 config.json 并填数据库密码、端口。
5. 双击 **start.bat**（若提示缺依赖会自动下载头文件后编译）。

### 4. 安全组/防火墙
- 在腾讯云控制台「安全组」放行所选端口（默认 8899）入方向。
- 建议服务器防火墙也放行该端口。

### 5. 客户端配置服务器地址
把客户端里填的服务器地址指向 `http://服务器公网IP:8899`。为便于部署，可用域名 + HTTPS 反向代理（本仓库为精简版，直接用 IP 即可）。

---

## 说明与已知注意事项

- 本项目为精简起步版：接口、组织/成员、每日+每周排名均可用。
- **鉴权令牌**目前存于 MySQL `sessions`，登录态过期时间在 `config.json` 里 `session_max_age_days` 配置。
- 若把服务暴露公网，建议在服务器上用 Nginx/Caddy 反代加 **HTTPS**，并限制速率，避免被刷。
- 每周“总结”：本周排行榜即 `weekly_rank` 当前周数据；跨周自动切新周，历史周可用 `week=` 参数回看。

## 已知待办 / 下一步建议
- [ ] 客户端接入登录注册、组织创建/加入、每分钟上报、排名查看界面
- [ ] HTTPS 与更健壮的会话管理
- [ ] 请求限流与防刷

---

## 附：服务器编译 · 快速清单与常见问题

本后端为**纯 C++**，需在腾讯云 **Windows** 上编译（开发机通常无 g++/MySQL）。请按下表逐项照做：

### 服务器端准备清单
| 步骤 | 说明 |
|---|---|
| 1. 拷文件夹 | 把整个 `server_rank/` 拷到服务器（如 `D:\token_rank`） |
| 2. 装 MySQL | 服务器已有 MySQL；先导入 `sql\schema.sql` 建库建表 |
| 3. 装 MinGW | 下载 MinGW-w64 并加入 PATH；`g++ --version` 能显示即成功 |
| 4. 装 MySQL Connector/C | 提供 `mysql.h`(include) 与 `libmysql.a/.dll`(lib) |
| 5. 下依赖 | `powershell -ExecutionPolicy Bypass -File fetch_deps.ps1`（需网络） |
| 6. 编译 | 设好 `MYSQL_INCLUDE`/`MYSQL_LIB` 后运行 `build.bat` |
| 7. 配置 | 复制 `config.json`，填数据库密码与端口 |
| 8. 启动 | 双击 `start.bat` |
| 9. 放行 | 腾讯云安全组 + 服务器防火墙放行端口（默认 8899） |

### 编译/启动常见报错
| 现象 | 解决 |
|---|---|
| `g++ 不是内部或外部命令` | MinGW 未加入 PATH，或未装 MinGW-w64 |
| `找不到 mysql.h` | 装 MySQL Connector/C，并把 include 目录设到 `MYSQL_INCLUDE` |
| 链接报 `undefined reference to mysql_*` 或找不到 `-lmysql` | `MYSQL_LIB` 指向含 `libmysql.a`/`mysql.lib` 的目录；必要时把 `libmysql.dll` 复制到 exe 同目录 |
| 运行报 `数据库连接失败` | 检查 `config.json` 的 host/账号/密码/库名；确认 MySQL 已启动、`sql/schema.sql` 已导入 |
| 客户端连不上 | 确认端口已放行；用浏览器访问 `http://IP:8899/` 应返回 `{"ok":true,...}` |
| 端口被占用 | 改 `config.json` 的 `server.port` |

> 首次在服务器编译，如有零星编译告警可忽略；若有编译错误，把报错贴给我，我来改对应代码。
