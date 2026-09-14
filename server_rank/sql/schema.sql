-- ============================================================
-- 水豚噜噜 · 组织 Token 排名后端 —— 数据库建表脚本
-- 目标数据库：MySQL 8.x（UTF-8mb4，支持中文组织名/用户名）
-- 用法（服务器 MySQL 命令行）：
--   mysql -uroot -p < sql/schema.sql
-- 或图形化工具(Navicat/Workbench)导入本文件。
-- ============================================================
CREATE DATABASE IF NOT EXISTS token_rank
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE token_rank;

-- ---------- 用户表 ----------
CREATE TABLE IF NOT EXISTS users (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username    VARCHAR(64)     NOT NULL,
  password    VARBINARY(128)  NOT NULL,          -- 加盐哈希后的密码(非明文)
  salt        VARBINARY(32)   NOT NULL,          -- 每用户随机盐
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 组织表 ----------
CREATE TABLE IF NOT EXISTS organizations (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name        VARCHAR(64)     NOT NULL,
  owner_id    BIGINT UNSIGNED NOT NULL,          -- 创建者(组织主)用户 id
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_org_name (name),
  KEY idx_owner (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 组织成员表（一个用户可加入多个组织） ----------
CREATE TABLE IF NOT EXISTS org_members (
  org_id    BIGINT UNSIGNED NOT NULL,
  user_id   BIGINT UNSIGNED NOT NULL,
  joined_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (org_id, user_id),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 每日 token 汇总（token 每日重置更新：一天一行） ----------
-- server 只保存 用户归属组织 在某一天的累计 token 数，用于排名。
CREATE TABLE IF NOT EXISTS token_daily (
  org_id     BIGINT UNSIGNED NOT NULL,
  user_id    BIGINT UNSIGNED NOT NULL,
  day        DATE            NOT NULL,           -- 所属日期(本地时区)
  tokens     BIGINT UNSIGNED NOT NULL DEFAULT 0, -- 当日累计 token
  updated_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (org_id, user_id, day),
  KEY idx_user_day (user_id, day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 每周汇总快照（每周总结排名） ----------
CREATE TABLE IF NOT EXISTS weekly_rank (
  org_id     BIGINT UNSIGNED NOT NULL,
  user_id    BIGINT UNSIGNED NOT NULL,
  week_start DATE            NOT NULL,           -- 该周的周一
  tokens     BIGINT UNSIGNED NOT NULL DEFAULT 0, -- 该周累计 token
  updated_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (org_id, user_id, week_start),
  KEY idx_week (org_id, week_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------- 会话/登录令牌（服务端登录态） ----------
CREATE TABLE IF NOT EXISTS sessions (
  token      CHAR(64) NOT NULL,                  -- 服务端下发的登录令牌
  user_id    BIGINT UNSIGNED NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (token),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
