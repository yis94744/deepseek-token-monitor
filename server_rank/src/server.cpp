// ============================================================================
// server.cpp — 组织 Token 排名后端 主程序（纯 C++）
//
// 技术栈：cpp-httplib(HTTP) + nlohmann/json(JSON) + MySQL Connector/C(C API 封装)
// 三方头文件依赖会由 build 脚本自动下载到 vendor/（见 README）。
//
// 隐私原则：本服务只接收并保存「token 数 + 组织归属」用于排名，不采集任何
// 对话内容 / API Key / 请求明细等关键信息。
// ============================================================================
#include <httplib.h>
#include <nlohmann/json.hpp>

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>

#include "db.h"
#include "util.h"

using json = nlohmann::json;
using httplib::Server;
using httplib::Request;
using httplib::Response;

namespace {

rank::Database g_db;
rank::DbConfig g_cfg;
int g_session_max_age_days = 30;

// ---------------- 响应辅助 ----------------
json ok(json body = json::object()) {
    body["ok"] = true;
    return body;
}
json fail(const std::string& msg, const std::string& code = "") {
    json j;
    j["ok"] = false;
    j["msg"] = msg;
    if (!code.empty()) j["code"] = code;
    return j;
}

// 统一 JSON 响应
void send_json(Response& res, const json& j) {
    res.set_content(j.dump(), "application/json; charset=utf-8");
}

// 读取并校验请求体为 object
json parse_body(const Request& req, Response& res) {
    try {
        json j = json::parse(req.body.empty() ? "{}" : req.body);
        if (!j.is_object()) throw std::runtime_error("not object");
        return j;
    } catch (...) {
        send_json(res, fail("请求体不是合法 JSON 对象"));
        return json();   // 空
    }
}

// 若 body 为空对象(解析失败)则返回 true（表示出错已回响应）
bool body_failed(const json& j) { return !j.is_object() || j.empty(); }

// 密码校验：仅约束长度与非控制字符，不限制符号集
bool password_ok(const std::string& p) {
    if (p.size() < 6 || p.size() > 64) return false;
    for (unsigned char c : p) {
        if (c < 0x20 || c == 0x7f) return false;  // 拒绝控制字符
    }
    return true;
}

// ---------------- 鉴权 ----------------
// 从 Authorization: Bearer <token> 取登录令牌；成功返回用户 id，失败返回 0
long long auth_user(const Request& req) {
    std::string auth = req.get_header_value("Authorization");
    const std::string prefix = "Bearer ";
    if (auth.rfind(prefix, 0) != 0) return 0;
    std::string token = auth.substr(prefix.size());
    if (token.size() != 64) return 0;
    // token 只可能是 hex(64)，安全，无需转义
    auto rows = g_db.query("SELECT user_id FROM sessions WHERE token='" + token + "'");
    if (rows.empty()) return 0;
    return rows[0].getInt(0);
}

} // namespace

int main(int argc, char** argv) {
    // ---------- 读取配置 ----------
    std::string cfg_path = "config.json";
    if (argc > 1) cfg_path = argv[1];

    std::ifstream ifs(cfg_path);
    if (!ifs) {
        std::cerr << "[错误] 找不到配置文件 " << cfg_path
                  << "。请复制 config.example.json 为 config.json 并填写数据库账号密码。\n";
        return 2;
    }
    json cfg;
    try { cfg = json::parse(ifs); }
    catch (...) {
        std::cerr << "[错误] 配置文件 " << cfg_path << " 解析失败(JSON 格式错误)\n";
        return 2;
    }

    const json& srv = cfg.value("server", json::object());
    std::string host = srv.value("host", std::string("0.0.0.0"));
    int port = srv.value("port", 8899);
    g_session_max_age_days = srv.value("session_max_age_days", 30);

    const json& my = cfg.value("mysql", json::object());
    g_cfg.host = my.value("host", std::string("127.0.0.1"));
    g_cfg.port = my.value("port", 3306);
    g_cfg.user = my.value("user", std::string("root"));
    g_cfg.password = my.value("password", std::string());
    g_cfg.database = my.value("database", std::string("token_rank"));
    g_cfg.charset = my.value("charset", std::string("utf8mb4"));

    // ---------- 连接数据库 ----------
    try {
        g_db.connect(g_cfg);
        std::cout << "[ok] 数据库连接成功: " << g_cfg.database << "\n";
    } catch (const std::exception& e) {
        std::cerr << "[错误] " << e.what() << "\n";
        std::cerr << "      请确认 MySQL 已启动、库已按 sql/schema.sql 建好、config.json 账号正确。\n";
        return 3;
    }

    Server svr;

    // ---------------- 健康检查 ----------------
    svr.Get("/", [](const Request&, Response& res) {
        send_json(res, ok(json{{"service", "token_rank_server"},
                               {"version", "0.1.0"}}));
    });

    // ---------------- 注册 ----------------
    svr.Post("/api/register", [](const Request& req, Response& res) {
        json body = parse_body(req, res);
        if (body_failed(body)) return;
        std::string username = body.value("username", "");
        std::string password = body.value("password", "");
        if (!rank::is_safe_name(username, 32) || !password_ok(password)) {
            send_json(res, fail("用户名仅允许字母数字下划线连字符与中文；密码需 6-64 位"));
            return;
        }
        // 查重
        auto dup = g_db.query("SELECT id FROM users WHERE username='" +
                              rank::escape(username) + "'");
        if (!dup.empty()) {
            send_json(res, fail("用户名已存在"));
            return;
        }
        std::string salt = rank::make_salt();
        std::string phash = rank::hash_password(salt, password);
        g_db.execute("INSERT INTO users(username, password, salt) VALUES('" +
                     rank::escape(username) + "','" + phash + "','" + salt + "')");
        long long uid = g_db.lastInsertId();
        send_json(res, ok(json{{"user_id", uid}, {"username", username}}));
    });

    // ---------------- 登录 ----------------
    svr.Post("/api/login", [](const Request& req, Response& res) {
        json body = parse_body(req, res);
        if (body_failed(body)) return;
        std::string username = body.value("username", "");
        std::string password = body.value("password", "");
        if (!rank::is_safe_name(username, 32) || !password_ok(password)) {
            send_json(res, fail("用户名或密码不合法"));
            return;
        }
        auto rows = g_db.query("SELECT id, salt, password FROM users WHERE username='" +
                               rank::escape(username) + "'");
        if (rows.empty()) { send_json(res, fail("用户名或密码错误")); return; }
        long long uid = rows[0].getInt(0);
        std::string salt = rows[0].get(1);
        std::string stored = rows[0].get(2);
        std::string phash = rank::hash_password(salt, password);
        if (phash != stored) { send_json(res, fail("用户名或密码错误")); return; }

        // 签发登录令牌（64 hex）
        std::string token = rank::random_hex(32);
        // 清理该用户旧会话 + 过期会话
        g_db.execute("DELETE FROM sessions WHERE user_id=" + std::to_string(uid) +
                     " OR created_at < DATE_SUB(NOW(), INTERVAL " +
                     std::to_string(g_session_max_age_days) + " DAY)");
        g_db.execute("INSERT INTO sessions(token, user_id) VALUES('" + token + "'," +
                     std::to_string(uid) + ")");
        // 返回用户所在组织列表
        json orgs = json::array();
        auto om = g_db.query(
            "SELECT o.id, o.name, IF(o.owner_id=" + std::to_string(uid) + ",1,0) AS is_owner "
            "FROM org_members m JOIN organizations o ON o.id=m.org_id "
            "WHERE m.user_id=" + std::to_string(uid) + " ORDER BY o.id");
        for (auto& r : om) {
            orgs.push_back(json{{"id", r.getInt(0)}, {"name", r.get(1)},
                                {"is_owner", r.getInt(2) == 1}});
        }
        send_json(res, ok(json{{"token", token}, {"user_id", uid},
                               {"username", username}, {"orgs", orgs}}));
    });

    // ---------------- 当前用户信息（含所在组织） ----------------
    svr.Get("/api/me", [](const Request& req, Response& res) {
        long long uid = auth_user(req);
        if (uid == 0) { send_json(res, fail("未登录或登录已过期", "AUTH")); return; }
        auto u = g_db.query("SELECT username FROM users WHERE id=" + std::to_string(uid));
        json orgs = json::array();
        auto om = g_db.query(
            "SELECT o.id, o.name, IF(o.owner_id=" + std::to_string(uid) + ",1,0) AS is_owner "
            "FROM org_members m JOIN organizations o ON o.id=m.org_id "
            "WHERE m.user_id=" + std::to_string(uid) + " ORDER BY o.id");
        for (auto& r : om) {
            orgs.push_back(json{{"id", r.getInt(0)}, {"name", r.get(1)},
                                {"is_owner", r.getInt(2) == 1}});
        }
        send_json(res, ok(json{{"user_id", uid},
                               {"username", u.empty() ? "" : u[0].get(0)},
                               {"orgs", orgs}}));
    });

    // ---------------- 创建组织 ----------------
    svr.Post("/api/org/create", [](const Request& req, Response& res) {
        long long uid = auth_user(req);
        if (uid == 0) { send_json(res, fail("未登录", "AUTH")); return; }
        json body = parse_body(req, res);
        if (body_failed(body)) return;
        std::string name = body.value("name", "");
        if (!rank::is_safe_name(name, 32)) {
            send_json(res, fail("组织名格式不合法（长度受限）")); return;
        }
        auto dup = g_db.query("SELECT id FROM organizations WHERE name='" + rank::escape(name) + "'");
        if (!dup.empty()) { send_json(res, fail("组织已存在，请直接加入", "ORG_EXISTS")); return; }
        g_db.execute("INSERT INTO organizations(name, owner_id) VALUES('" +
                     rank::escape(name) + "'," + std::to_string(uid) + ")");
        long long oid = g_db.lastInsertId();
        g_db.execute("INSERT INTO org_members(org_id, user_id) VALUES(" +
                     std::to_string(oid) + "," + std::to_string(uid) + ")");
        send_json(res, ok(json{{"org_id", oid}, {"name", name}, {"is_owner", true}}));
    });

    // ---------------- 加入组织 ----------------
    // 若组织不存在，返回 code=ORG_NOT_FOUND，客户端据此询问“是否创建”。
    svr.Post("/api/org/join", [](const Request& req, Response& res) {
        long long uid = auth_user(req);
        if (uid == 0) { send_json(res, fail("未登录", "AUTH")); return; }
        json body = parse_body(req, res);
        if (body_failed(body)) return;
        std::string name = body.value("name", "");
        if (!rank::is_safe_name(name, 32)) {
            send_json(res, fail("组织名格式不合法（长度受限）")); return;
        }
        auto org = g_db.query("SELECT id, owner_id FROM organizations WHERE name='" +
                              rank::escape(name) + "'");
        if (org.empty()) {
            send_json(res, fail("没有这个组织，是否创建？", "ORG_NOT_FOUND")); return;
        }
        long long oid = org[0].getInt(0);
        // 已是成员则幂等返回
        auto m = g_db.query("SELECT 1 FROM org_members WHERE org_id=" + std::to_string(oid) +
                            " AND user_id=" + std::to_string(uid));
        if (m.empty()) {
            g_db.execute("INSERT INTO org_members(org_id, user_id) VALUES(" +
                         std::to_string(oid) + "," + std::to_string(uid) + ")");
        }
        send_json(res, ok(json{{"org_id", oid}, {"name", name},
                               {"is_owner", org[0].getInt(1) == uid}}));
    });

    // ---------------- 上报 token（增量） ----------------
    // body: {org_id: <int>, tokens_delta: <int>}  —— tokens_delta>=0 为距上次上报的增量
    // 服务器只接收 token 数，用于当日/当周汇总排名。
    svr.Post("/api/report_token", [](const Request& req, Response& res) {
        long long uid = auth_user(req);
        if (uid == 0) { send_json(res, fail("未登录", "AUTH")); return; }
        json body = parse_body(req, res);
        if (body_failed(body)) return;
        if (!body.contains("org_id") || !body.contains("tokens_delta")) {
            send_json(res, fail("缺少 org_id 或 tokens_delta")); return;
        }
        long long org_id = body["org_id"].is_number_integer() ? body["org_id"].get<long long>() : -1;
        long long delta = body["tokens_delta"].is_number_integer() ? body["tokens_delta"].get<long long>() : 0;
        if (org_id <= 0 || delta < 0) { send_json(res, fail("参数不合法")); return; }
        // 必须属于该组织
        auto m = g_db.query("SELECT 1 FROM org_members WHERE org_id=" + std::to_string(org_id) +
                            " AND user_id=" + std::to_string(uid));
        if (m.empty()) { send_json(res, fail("你不属于该组织", "NOT_MEMBER")); return; }

        std::string today = rank::today_str();
        std::string monday = rank::current_week_monday();
        // 当日累计（存在则累加）
        auto ex = g_db.query("SELECT tokens FROM token_daily WHERE org_id=" +
                             std::to_string(org_id) + " AND user_id=" + std::to_string(uid) +
                             " AND day='" + today + "'");
        long long new_daily = delta;
        if (!ex.empty()) new_daily = ex[0].getInt(0) + delta;
        g_db.execute("INSERT INTO token_daily(org_id,user_id,day,tokens) VALUES(" +
                     std::to_string(org_id) + "," + std::to_string(uid) + ",'" + today + "'," +
                     std::to_string(new_daily) + ") ON DUPLICATE KEY UPDATE tokens=" +
                     std::to_string(new_daily) + ", updated_at=NOW()");
        // 当周累计（同样累加）
        auto wex = g_db.query("SELECT tokens FROM weekly_rank WHERE org_id=" +
                              std::to_string(org_id) + " AND user_id=" + std::to_string(uid) +
                              " AND week_start='" + monday + "'");
        long long new_week = delta;
        if (!wex.empty()) new_week = wex[0].getInt(0) + delta;
        g_db.execute("INSERT INTO weekly_rank(org_id,user_id,week_start,tokens) VALUES(" +
                     std::to_string(org_id) + "," + std::to_string(uid) + ",'" + monday + "'," +
                     std::to_string(new_week) + ") ON DUPLICATE KEY UPDATE tokens=" +
                     std::to_string(new_week) + ", updated_at=NOW()");

        send_json(res, ok(json{{"org_id", org_id}, {"day", today},
                               {"week_start", monday}, {"today_tokens", new_daily},
                               {"week_tokens", new_week}}));
    });

    // ---------------- 今日组织内排名 ----------------
    // GET /api/rank/today?org_id=xx
    svr.Get("/api/rank/today", [](const Request& req, Response& res) {
        long long uid = auth_user(req);
        if (uid == 0) { send_json(res, fail("未登录", "AUTH")); return; }
        long long org_id = 0;
        if (req.has_param("org_id")) org_id = std::atoll(req.get_param_value("org_id").c_str());
        if (org_id <= 0) { send_json(res, fail("缺少 org_id")); return; }
        auto m = g_db.query("SELECT 1 FROM org_members WHERE org_id=" + std::to_string(org_id) +
                            " AND user_id=" + std::to_string(uid));
        if (m.empty()) { send_json(res, fail("你不属于该组织", "NOT_MEMBER")); return; }

        std::string today = rank::today_str();
        auto rows = g_db.query(
            "SELECT u.username, COALESCE(t.tokens,0) AS tokens "
            "FROM org_members om "
            "JOIN users u ON u.id=om.user_id "
            "LEFT JOIN token_daily t ON t.org_id=om.org_id AND t.user_id=om.user_id AND t.day='" + today + "' "
            "WHERE om.org_id=" + std::to_string(org_id) + " "
            "ORDER BY tokens DESC, om.joined_at ASC");
        json list = json::array();
        int rank_no = 0;
        for (auto& r : rows) {
            ++rank_no;
            list.push_back(json{{"rank", rank_no}, {"username", r.get(0)},
                                {"tokens", r.getInt(1)}});
        }
        // 计算“我”的排名与 token
        long long my_tokens;
        auto mine = g_db.query(
            "SELECT COALESCE(SUM(tokens),0) FROM token_daily WHERE org_id=" + std::to_string(org_id) +
            " AND user_id=" + std::to_string(uid) + " AND day='" + today + "'");
        my_tokens = mine.empty() ? 0 : mine[0].getInt(0);
        // 排名 = 今日 token 严格大于我的人数 + 1
        int my_rank = 0;
        auto cnt = g_db.query(
            "SELECT COUNT(*) FROM token_daily WHERE org_id=" + std::to_string(org_id) +
            " AND user_id<>" + std::to_string(uid) + " AND day='" + today + "' AND tokens>" +
            std::to_string(my_tokens));
        my_rank = cnt.empty() ? 1 : (int)(cnt[0].getInt(0) + 1);

        send_json(res, ok(json{{"org_id", org_id}, {"day", today}, {"list", list},
                               {"my_rank", my_rank}, {"my_tokens", my_tokens}}));
    });

    // ---------------- 每周总结排名 ----------------
    // GET /api/rank/week?org_id=xx            → 本周
    // GET /api/rank/week?org_id=xx&week=YYYY-MM-DD（该周任意一天）→ 该周
    svr.Get("/api/rank/week", [](const Request& req, Response& res) {
        long long uid = auth_user(req);
        if (uid == 0) { send_json(res, fail("未登录", "AUTH")); return; }
        long long org_id = 0;
        if (req.has_param("org_id")) org_id = std::atoll(req.get_param_value("org_id").c_str());
        if (org_id <= 0) { send_json(res, fail("缺少 org_id")); return; }
        auto m = g_db.query("SELECT 1 FROM org_members WHERE org_id=" + std::to_string(org_id) +
                            " AND user_id=" + std::to_string(uid));
        if (m.empty()) { send_json(res, fail("你不属于该组织", "NOT_MEMBER")); return; }

        std::string monday = rank::current_week_monday();
        if (req.has_param("week")) {
            std::string d = req.get_param_value("week");
            monday = rank::week_start_of(d);
        }
        auto rows = g_db.query(
            "SELECT u.username, COALESCE(w.tokens,0) AS tokens "
            "FROM org_members om "
            "JOIN users u ON u.id=om.user_id "
            "LEFT JOIN weekly_rank w ON w.org_id=om.org_id AND w.user_id=om.user_id "
            "  AND w.week_start='" + monday + "' "
            "WHERE om.org_id=" + std::to_string(org_id) + " "
            "ORDER BY tokens DESC, om.joined_at ASC");
        json list = json::array();
        int rank_no = 0;
        for (auto& r : rows) {
            ++rank_no;
            list.push_back(json{{"rank", rank_no}, {"username", r.get(0)},
                                {"tokens", r.getInt(1)}});
        }
        auto mine = g_db.query(
            "SELECT COALESCE(SUM(tokens),0) FROM weekly_rank WHERE org_id=" + std::to_string(org_id) +
            " AND user_id=" + std::to_string(uid) + " AND week_start='" + monday + "'");
        long long my_tokens = mine.empty() ? 0 : mine[0].getInt(0);
        auto cnt = g_db.query(
            "SELECT COUNT(*) FROM weekly_rank WHERE org_id=" + std::to_string(org_id) +
            " AND user_id<>" + std::to_string(uid) + " AND week_start='" + monday +
            "' AND tokens>" + std::to_string(my_tokens));
        int my_rank = cnt.empty() ? 1 : (int)(cnt[0].getInt(0) + 1);
        send_json(res, ok(json{{"org_id", org_id}, {"week_start", monday},
                               {"list", list}, {"my_rank", my_rank},
                               {"my_tokens", my_tokens}}));
    });

    // ---------------- 日志与启动 ----------------
    svr.set_error_handler([](const Request&, Response& res) {
        if (res.status == 404) send_json(res, fail("接口不存在", "NOT_FOUND"));
    });

    std::cout << "[ok] token_rank_server 启动于 " << host << ":" << port << "\n";
    if (!svr.listen(host, port)) {
        std::cerr << "[错误] 监听失败（端口被占用或地址无效）\n";
        return 4;
    }
    return 0;
}
