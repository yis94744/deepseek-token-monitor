// ============================================================================
// db.h — MySQL 数据库访问封装（基于 MySQL Connector/C 的 C API，经 C++ 封装）
//
// 选型说明：在 Windows + MinGW(g++) 下，用 MySQL 官方 Connector/C 提供的
// mysql.h(C API) + libmysql.dll 链接是最稳定、最少坑的方式（Connector/C++
// 底层同样依赖 Connector/C 的 libmysql.dll）。因此这里用 C API 包一层 C++，
// 既满足“纯 C++ 代码后端”，又能在一键脚本里可靠地 g++ 链接。
//
// 依赖：
//   * MySQL Connector/C (libmysql.dll / mysql.h) —— 随“mysql-connector-c”发布
//   * 只需 mysql 相关头文件与链接库，运行时同目录放 libmysql.dll 即可
// ============================================================================
#pragma once

#include <mysql.h>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace rank {

// 单行结果：列名 -> 值（值可能为 NULL，用空串表达；数字已转字符串）
struct Row {
    std::vector<std::string> cols;                 // 与 query 传入的列顺序一致
    const std::string& at(size_t i) const { return cols.at(i); }
    std::string get(size_t i) const { return i < cols.size() ? cols[i] : ""; }
    long long getInt(size_t i) const {
        return cols.size() > i && !cols[i].empty() ? std::stoll(cols[i]) : 0LL;
    }
    double getDouble(size_t i) const {
        return cols.size() > i && !cols[i].empty() ? std::stod(cols[i]) : 0.0;
    }
};

// MySQL 连接参数
struct DbConfig {
    std::string host = "127.0.0.1";
    unsigned int port = 3306;
    std::string user;
    std::string password;
    std::string database;
    std::string charset = "utf8mb4";
};

// 数据库访问器：持有单一连接（后端为单进程低并发，连接复用一个即可；
// 若需更高并发可在调用方加锁或改用连接池）。
class Database {
public:
    Database();
    ~Database();

    // 初始化并建立连接；失败抛 std::runtime_error（含 mysql 错误信息）
    void connect(const DbConfig& cfg);

    // 执行无结果语句（INSERT/UPDATE/CREATE…），返回影响行数
    unsigned long long execute(const std::string& sql);

    // 执行查询，返回行列表（列顺序由 SQL 决定）
    std::vector<Row> query(const std::string& sql);

    // 返回上一个 INSERT 的自增 id；无自增 id 时返回 0
    unsigned long long lastInsertId();

    // 释放连接
    void close();

private:
    MYSQL* conn_ = nullptr;
};

// 转义字符串，防止 SQL 注入（对需要拼进 SQL 的字符串调用）
std::string escape(const std::string& s);

} // namespace rank
