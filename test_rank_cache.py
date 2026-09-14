# -*- coding: utf-8 -*-
"""test_rank_cache.py — 排名缓存的回归测试。

背景（用户反馈"看不到数据"）：原来 my_rank 只存在于进程内存，程序一重启
就变回 None，界面显示"同步中"；而首轮上报要等 5 秒（现在 2 秒），
叠加网络耗时，用户会以为功能坏了。

修复：成功后把结果落盘，get_state() 在内存为空时回落到磁盘缓存。
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utf8_output  # noqa: F401  # 中文输出兼容（CI 为英文 Windows）


def _isolate():
    """把 rank_client 的路径指到临时目录，避免污染真实会话。"""
    import rank_client as rc
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", "rankcache_t")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    rc._APP_DIR = d
    rc._SETTINGS_PATH = os.path.join(d, "settings.json")
    rc._RANK_PATH = os.path.join(d, "ranking.json")
    return rc, d


def _reset_mem(rc):
    rc._state.update({"board": None, "day": None, "my_rank": None, "my_tokens": None,
                      "last_time": None, "error": None, "token_invalid": False,
                      "error_streak": 0, "board_version": ""})


def test_cache_saved_and_loaded():
    """同步成功后应落盘；清空内存后 get_state 应能从磁盘恢复。"""
    rc, d = _isolate()
    cache = os.path.join(d, "rank_cache.json")
    if os.path.isfile(cache):
        os.remove(cache)

    # 模拟一次成功同步后的状态
    _reset_mem(rc)
    rc._state.update({
        "board": [{"rank": 1, "user_id": 3, "nickname": "YiS", "tokens": 12345}],
        "day": "2026-09-14", "my_rank": 1, "my_tokens": 12345,
        "last_time": "12:00:00", "board_version": "abc123",
    })
    rc._save_cache()
    assert os.path.isfile(cache), "缓存文件未生成"
    print("PASS 1 同步成功后会写磁盘缓存")

    # 模拟重启：内存清空
    _reset_mem(rc)
    assert rc._state.get("my_rank") is None
    st = rc.get_state()
    assert st.get("my_rank") == 1, "重启后应从缓存恢复名次，实际 %r" % st.get("my_rank")
    assert st.get("day") == "2026-09-14"
    assert st.get("from_cache") is True, "应标记数据来自缓存"
    print("PASS 2 重启后立即能读到名次（并标记 from_cache）")


def test_memory_takes_priority():
    """内存里有实时数据时，不应回落缓存。"""
    rc, d = _isolate()
    _reset_mem(rc)
    rc._state.update({"my_rank": 5, "day": "2026-09-14", "board": [{"rank": 5}],
                      "board_version": "live"})
    st = rc.get_state()
    assert st.get("my_rank") == 5
    assert not st.get("from_cache"), "实时数据不应标记为缓存"
    print("PASS 3 内存实时数据优先于缓存")


def test_empty_cache_returns_live_state():
    """没有缓存且内存为空时，应正常返回空状态（不报错）。"""
    rc, d = _isolate()
    cache = os.path.join(d, "rank_cache.json")
    if os.path.isfile(cache):
        os.remove(cache)
    _reset_mem(rc)
    st = rc.get_state()
    assert st.get("my_rank") is None
    assert not st.get("from_cache")
    print("PASS 4 无缓存时安全返回空状态")


def test_corrupt_cache_is_safe():
    """缓存文件损坏时不应抛异常，退化为无缓存。"""
    rc, d = _isolate()
    cache = os.path.join(d, "rank_cache.json")
    with io.open(cache, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON")
    _reset_mem(rc)
    try:
        st = rc.get_state()
    except Exception as exc:
        raise AssertionError("缓存损坏时不应抛异常: %s" % exc)
    assert st.get("my_rank") is None
    print("PASS 5 缓存损坏时安全降级")


def test_cache_excludes_credentials():
    """缓存文件里绝不能出现登录凭据。

    注意：my_tokens 是 token **数量**（用量统计），不是凭据，需与
    token/Bearer 等凭据字段区分开，所以这里按"键名"而不是子串判断。
    """
    rc, d = _isolate()
    _reset_mem(rc)
    rc._state.update({"my_rank": 1, "day": "2026-09-14",
                      "board": [{"rank": 1, "user_id": 3}], "board_version": "v"})
    rc._save_cache()
    data = json.loads(io.open(os.path.join(d, "rank_cache.json"), encoding="utf-8").read())

    # 顶层键名不得含凭据语义
    for key in data:
        assert key.lower() not in ("token", "access_token", "bearer", "password",
                                   "session", "secret"), "缓存键 %r 疑似凭据" % key
    assert "token" not in data, "缓存不应保存登录令牌"

    # 值里不得出现 64 位十六进制（登录令牌的特征）
    import re
    text = io.open(os.path.join(d, "rank_cache.json"), encoding="utf-8").read()
    hits = re.findall(r"[0-9a-f]{64}", text)
    assert not hits, "缓存中出现疑似令牌的 64 位十六进制串"
    print("PASS 6 缓存不含登录凭据（按键名 + 令牌特征双重校验）")


if __name__ == "__main__":
    test_cache_saved_and_loaded()
    test_memory_takes_priority()
    test_empty_cache_returns_live_state()
    test_corrupt_cache_is_safe()
    test_cache_excludes_credentials()
    print("\nALL RANK CACHE TESTS PASSED")
