#include "db.h"

namespace rank {

Database::Database() {}
Database::~Database() { close(); }

void Database::connect(const DbConfig& cfg) {
    if (conn_) { mysql_close(conn_); conn_ = nullptr; }
    conn_ = mysql_init(nullptr);
    if (!conn_) {
        throw std::runtime_error("mysql_init 失败（内存不足）");
    }
    // 设置字符集（影响存储与读取中文）
    mysql_options(conn_, MYSQL_SET_CHARSET_NAME, cfg.charset.c_str());
    if (!mysql_real_connect(conn_, cfg.host.c_str(), cfg.user.c_str(),
                            cfg.password.c_str(), cfg.database.c_str(),
                            cfg.port, nullptr, 0)) {
        std::string err = std::string("数据库连接失败: ") +
                          (mysql_error(conn_) ? mysql_error(conn_) : "未知错误");
        mysql_close(conn_);
        conn_ = nullptr;
        throw std::runtime_error(err);
    }
    // 让连接始终使用 utf8mb4（覆盖部分情况下 charset 未生效的问题）
    mysql_set_character_set(conn_, cfg.charset.c_str());
}

unsigned long long Database::execute(const std::string& sql) {
    if (!conn_) throw std::runtime_error("数据库未连接");
    if (mysql_query(conn_, sql.c_str()) != 0) {
        throw std::runtime_error(std::string("SQL 执行失败: ") + mysql_error(conn_));
    }
    return static_cast<unsigned long long>(mysql_affected_rows(conn_));
}

std::vector<Row> Database::query(const std::string& sql) {
    if (!conn_) throw std::runtime_error("数据库未连接");
    if (mysql_query(conn_, sql.c_str()) != 0) {
        throw std::runtime_error(std::string("SQL 查询失败: ") + mysql_error(conn_));
    }
    MYSQL_RES* res = mysql_store_result(conn_);
    if (!res) {
        // 无结果集（例如只是走了 execute 的语句），返回空
        if (mysql_field_count(conn_) == 0) return {};
        throw std::runtime_error(std::string("取结果失败: ") + mysql_error(conn_));
    }
    std::vector<Row> out;
    MYSQL_ROW row;
    while ((row = mysql_fetch_row(res)) != nullptr) {
        unsigned long* lengths = mysql_fetch_lengths(res);
        Row r;
        unsigned int ncol = mysql_num_fields(res);
        for (unsigned int i = 0; i < ncol; ++i) {
            if (row[i] == nullptr) {
                r.cols.emplace_back("");
            } else {
                r.cols.emplace_back(std::string(row[i], lengths[i]));
            }
        }
        out.push_back(std::move(r));
    }
    mysql_free_result(res);
    return out;
}

unsigned long long Database::lastInsertId() {
    if (!conn_) return 0;
    return static_cast<unsigned long long>(mysql_insert_id(conn_));
}

void Database::close() {
    if (conn_) { mysql_close(conn_); conn_ = nullptr; }
}

std::string escape(const std::string& s) {
    // 需要连接才能转义；这里提供一个不需要连接的简易转义。
    // 注：为安全起见，业务代码优先用占位符参数化（本项目 SQL 均为服务端拼装，
    //     统一调用本函数转义用户输入）。以下按 MySQL 字符串字面量规则转义。
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '\'': out += "\\'"; break;
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\0': out += "\\0"; break;
            case 0x1a: out += "\\Z"; break;   // Ctrl-Z
            default:   out += c; break;
        }
    }
    return out;
}

} // namespace rank
