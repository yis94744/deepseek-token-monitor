#include "util.h"
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <vector>
#include <random>
#include <chrono>

namespace rank {

// ============================ SHA-256 ============================
namespace {

inline uint32_t rotr(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }

const uint32_t K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};

void sha256_impl(const unsigned char* in, size_t len, unsigned char out[32]) {
    uint32_t h[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                     0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    // 填充：末尾加 0x80，再加 0~63 个 0x00，最后 8 字节为大端 64 位位长度，
    // 使总长 (含长度8字节) 为 64 的倍数。
    uint64_t bitlen = (uint64_t)len * 8;
    size_t with_pad_bit = len + 1;                 // 加了 0x80 后的长度
    // 需要使 (with_pad_bit + zeros) % 64 == 56  （留下 8 字节放长度）
    size_t zeros = (with_pad_bit % 64 <= 56)
                   ? (56 - with_pad_bit % 64)
                   : (64 - with_pad_bit % 64) + 56;

    std::vector<unsigned char> msg;
    msg.reserve(with_pad_bit + zeros + 8);
    msg.insert(msg.end(), in, in + len);
    msg.push_back(0x80);
    for (size_t i = 0; i < zeros; ++i) msg.push_back(0);
    for (int i = 7; i >= 0; --i) msg.push_back((unsigned char)(bitlen >> (i * 8)));

    for (size_t off = 0; off < msg.size(); off += 64) {
        uint32_t w[64];
        for (int i = 0; i < 16; ++i) {
            w[i] = ((uint32_t)msg[off + i*4] << 24) | ((uint32_t)msg[off + i*4+1] << 16) |
                   ((uint32_t)msg[off + i*4+2] << 8) | ((uint32_t)msg[off + i*4+3]);
        }
        for (int i = 16; i < 64; ++i) {
            uint32_t s0 = rotr(w[i-15], 7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
            uint32_t s1 = rotr(w[i-2], 17) ^ rotr(w[i-2], 19) ^ (w[i-2] >> 10);
            w[i] = w[i-16] + s0 + w[i-7] + s1;
        }
        uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],hh=h[7];
        for (int i = 0; i < 64; ++i) {
            uint32_t S1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25);
            uint32_t ch = (e & f) ^ ((~e) & g);
            uint32_t t1 = hh + S1 + ch + K[i] + w[i];
            uint32_t S0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22);
            uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = S0 + maj;
            hh=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
        }
        h[0]+=a; h[1]+=b; h[2]+=c; h[3]+=d; h[4]+=e; h[5]+=f; h[6]+=g; h[7]+=hh;
    }
    for (int i = 0; i < 8; ++i) {
        out[i*4]   = (unsigned char)(h[i] >> 24);
        out[i*4+1] = (unsigned char)(h[i] >> 16);
        out[i*4+2] = (unsigned char)(h[i] >> 8);
        out[i*4+3] = (unsigned char)(h[i]);
    }
}
} // namespace

std::string sha256_hex(const std::string& data) {
    unsigned char d[32];
    sha256_impl((const unsigned char*)data.data(), data.size(), d);
    static const char* h = "0123456789abcdef";
    std::string out;
    out.reserve(64);
    for (int i = 0; i < 32; ++i) { out += h[d[i] >> 4]; out += h[d[i] & 0xf]; }
    return out;
}

std::string random_hex(int bytes) {
    static std::mt19937_64 rng{std::random_device{}()};
    static const char* h = "0123456789abcdef";
    std::string out;
    out.reserve(bytes * 2);
    for (int i = 0; i < bytes; ++i) {
        unsigned char b = (unsigned char)(rng() & 0xff);
        out += h[b >> 4]; out += h[b & 0xf];
    }
    return out;
}

std::string make_salt() { return random_hex(16); }

std::string hash_password(const std::string& salt, const std::string& password) {
    return sha256_hex(salt + password);
}

// ============================ 时间/日期 ============================
static std::tm local_tm(time_t t = 0) {
    time_t now = t ? t : time(nullptr);
    std::tm tm;
#if defined(_WIN32)
    localtime_s(&tm, &now);
#else
    localtime_r(&now, &tm);
#endif
    return tm;
}

static std::string tm_to_date(const std::tm& tm) {
    char buf[16];
    snprintf(buf, sizeof(buf), "%04d-%02d-%02d", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday);
    return std::string(buf);
}

std::string today_str() { return tm_to_date(local_tm()); }

std::string now_str() {
    std::tm tm = local_tm();
    char buf[32];
    snprintf(buf, sizeof(buf), "%04d-%02d-%02d %02d:%02d:%02d",
             tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
             tm.tm_hour, tm.tm_min, tm.tm_sec);
    return std::string(buf);
}

bool parse_date(const std::string& s, std::tm& out) {
    if (s.size() != 10 || s[4] != '-' || s[7] != '-') return false;
    int y, m, d;
    if (sscanf(s.c_str(), "%d-%d-%d", &y, &m, &d) != 3) return false;
    if (m < 1 || m > 12 || d < 1 || d > 31 || y < 1970) return false;
    std::memset(&out, 0, sizeof(out));
    out.tm_year = y - 1900;
    out.tm_mon = m - 1;
    out.tm_mday = d;
    return true;
}

std::string week_start_of(const std::string& date) {
    std::tm tm;
    if (!parse_date(date, tm)) return date;
    // mktime 使 mday 规范化，并借 tm_wday 得知星期几
    time_t t = mktime(&tm);
    std::tm c = local_tm(t);
    int wd = c.tm_wday;               // 0=周日 … 6=周六
    int back = (wd == 0) ? 6 : wd - 1; // 回退到周一
    time_t monday = t - (time_t)back * 86400;
    std::tm mt = local_tm(monday);
    // 防止夏令时导致偏移，用 mday 规范化后再格式化
    mt.tm_isdst = -1;
    time_t m2 = mktime(&mt);
    return tm_to_date(local_tm(m2));
}

std::string current_week_monday() { return week_start_of(today_str()); }

std::string current_week_sunday() {
    std::tm tm;
    if (!parse_date(current_week_monday(), tm)) return today_str();
    tm.tm_mday += 6;
    tm.tm_isdst = -1;
    time_t t = mktime(&tm);
    return tm_to_date(local_tm(t));
}

// ============================ 名称校验 ============================
bool is_safe_name(const std::string& s, size_t max_len) {
    if (s.empty() || s.size() > max_len) return false;
    // 逐个 UTF-8 字符判断。ASCII 仅允许 [A-Za-z0-9_-]；多字节首字节必须
    // 是合法 UTF-8 引导字节（>=0xC0），并正确跳过其续字节。
    for (size_t i = 0; i < s.size();) {
        unsigned char c = (unsigned char)s[i];
        if (c < 0x80) {                         // ASCII
            if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                  (c >= '0' && c <= '9') || c == '_' || c == '-')) {
                return false;
            }
            ++i;
        } else {                                 // 多字节 UTF-8
            int extra;
            if (c >= 0xF0 && c <= 0xF4) extra = 3;      // 4 字节
            else if (c >= 0xE0) extra = 2;              // 3 字节
            else if (c >= 0xC0) extra = 1;              // 2 字节
            else return false;                          // 非法首字节(续字节起始)
            if (i + extra >= s.size()) return false;    // 续字节不足
            // 校验续字节形如 10xxxxxx
            for (int k = 1; k <= extra; ++k) {
                if (((unsigned char)s[i + k] & 0xC0) != 0x80) return false;
            }
            i += (size_t)extra + 1;
        }
    }
    return true;
}

} // namespace rank
