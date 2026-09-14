// ============================================================================
// util.h — 通用工具：SHA-256、随机令牌、时间/周计算、JSON 便捷函数
// ============================================================================
#pragma once

#include <string>
#include <ctime>

namespace rank {

// ---------- SHA-256 ----------
// 返回 64 位小写十六进制摘要
std::string sha256_hex(const std::string& data);

// ---------- 随机数 / 令牌 ----------
// 返回长度为 n 字节的随机 hex（n 字节 -> 2n 个 hex 字符）
std::string random_hex(int bytes);

// ---------- 密码哈希 ----------
// 加盐哈希：sha256(salt + password)。盐为 16 字节随机 hex。
std::string make_salt();
std::string hash_password(const std::string& salt, const std::string& password);

// ---------- 时间 / 日期（本地时区，服务器与客户端约定同一时区，建议统一设为中国标准时间） ----------
// 今天的日期 YYYY-MM-DD
std::string today_str();
// 当前时间 YYYY-MM-DD HH:MM:SS
std::string now_str();
// 给定 YYYY-MM-DD，返回所在周的周一 YYYY-MM-DD
std::string week_start_of(const std::string& date);
// 当前一周（周一 ~ 周日）区间：返回 [week_monday, week_sunday]
std::string current_week_monday();
std::string current_week_sunday();

// ---------- JSON ----------
// 对客户端输入做基本校验：用户名/组织名仅允许 [A-Za-z0-9_-\u4e00-\u9fff]
// （字母数字下划线连字符 + 中文），其余一律拒绝，避免注入与异常字符。
bool is_safe_name(const std::string& s, size_t max_len);

// 解析日期字符串为 tm（用于周计算），失败返回 false
bool parse_date(const std::string& s, std::tm& out);

} // namespace rank
