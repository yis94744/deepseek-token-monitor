# -*- coding: utf-8 -*-
"""CloudRank 后端 API 自测（默认 SQLite，无需 MySQL 即可跑）。

用法:  cd cloud_rank
      $env:CLOUDRANK_DB="sqlite:///./test_rank.db"; python test_api.py
"""
import os
import sys
import time

import sqlalchemy as sa

import app as backend

DB = os.environ.get("CLOUDRANK_DB", "sqlite:///./test_rank.db")
os.environ["CLOUDRANK_DB"] = DB


def main():
    eng = sa.create_engine(DB)
    backend.Base.metadata.create_all(eng)
    try:
        from fastapi.testclient import TestClient
        c = TestClient(backend.app)

        # 1 注册/重复/登录/错误密码
        r = c.post("/api/register", json={"email": "alice@test.com",
                                          "password": "pass123456", "nickname": "爱丽丝"})
        assert r.status_code == 200, r.text
        t1 = r.json()["token"]
        r = c.post("/api/register", json={"email": "bob@test.com", "password": "pass123456"})
        t2 = r.json()["token"]
        assert c.post("/api/register", json={"email": "alice@test.com",
                                             "password": "x123456"}).status_code == 409
        r = c.post("/api/login", json={"email": "alice@test.com", "password": "pass123456"})
        t1 = r.json()["token"]  # 登录轮换 token
        assert c.post("/api/login", json={"email": "alice@test.com",
                                          "password": "wrong1"}).status_code == 401
        print("PASS 1 注册/重复/登录/错误密码")

        # 2 上报/榜单排序/未登录拦截
        h1, h2 = {"Authorization": "Bearer " + t1}, {"Authorization": "Bearer " + t2}
        r = c.post("/api/report", json={"tokens": 1000000}, headers=h1)
        assert r.status_code == 200, r.text
        c.post("/api/report", json={"tokens": 500000}, headers=h2)
        r = c.post("/api/report", json={"tokens": 1200000}, headers=h1)
        board = r.json()["board"]
        assert board[0]["nickname"] == "爱丽丝" and board[0]["tokens"] == 1200000, board
        assert board[1]["tokens"] == 500000
        assert c.post("/api/report", json={"tokens": 1}).status_code == 401
        print("PASS 2 上报/榜单排序/未登录拦截")

        # 3 me
        r = c.get("/api/me", headers=h1)
        assert r.json()["user"]["email"] == "alice@test.com"
        print("PASS 3 /api/me")

        # 4 网页
        r = c.get("/")
        assert r.status_code == 200 and "Token" in r.text
        print("PASS 4 网页可访问")

        # 5 跨天自动开新榜
        r = c.post("/api/report", json={"tokens": 1000000}, headers=h1)
        assert r.json()["board"][0]["tokens"] == 1000000
        with eng.begin() as conn:
            conn.execute(sa.text("UPDATE daily_usage SET day=date(day,'-1 day')"))
        r = c.get("/api/board", headers=h1)
        assert r.json()["board"] == [], r.json()
        r = c.post("/api/report", json={"tokens": 500}, headers=h1)
        assert r.json()["board"][0]["tokens"] == 500
        print("PASS 5 跨天自动开新榜")
        print("\nALL BACKEND TESTS PASSED")
    finally:
        try:
            eng.dispose()
        except Exception:
            pass
        time.sleep(0.3)
        for _ in range(3):
            try:
                if os.path.exists("test_rank.db"):
                    os.remove("test_rank.db")
                break
            except Exception:
                time.sleep(0.5)


if __name__ == "__main__":
    main()
