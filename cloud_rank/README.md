# CloudRank 后端部署文档（腾讯云轻量 / Windows 或 Linux）

API：注册/登录/上报/榜单；网页：`/`（登录后 30s 自动刷新榜单）。

## 1. 安装 MySQL
Windows Server：安装 MySQL 8（https://dev.mysql.com/downloads/installer/ 选 Server only），
记录 root 密码；安装后用 root 执行：

```sql
CREATE DATABASE cloud_rank CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'rank'@'localhost' IDENTIFIED BY '改成强密码';
CREATE USER 'rank'@'127.0.0.1' IDENTIFIED BY '改成强密码';
GRANT ALL ON cloud_rank.* TO 'rank'@'localhost';
GRANT ALL ON cloud_rank.* TO 'rank'@'127.0.0.1';
```

## 2. 安装 Python 依赖
```bash
pip install -r requirements.txt
```

## 3. 建表
```bash
set CLOUDRANK_DB=mysql+pymysql://rank:改成强密码@127.0.0.1:3306/cloud_rank?charset=utf8mb4
python init_db.py
```

## 4. 启动服务（建议注册为系统服务/计划任务）
```bash
set CLOUDRANK_DB=mysql+pymysql://rank:改成强密码@127.0.0.1:3306/cloud_rank?charset=utf8mb4
set CLOUDRANK_PORT=8000
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

## 5. 防火墙/安全组
- 放行 TCP 8000（或经 nginx 反代 80/443）
- MySQL 3306 保持仅本机可访问，勿对外开放

## 6. 验证
- `curl http://IP:8000/api/board`（未带 token 应 401）
- 浏览器打开 `http://IP:8000/` 注册 → 登录 → 看榜
