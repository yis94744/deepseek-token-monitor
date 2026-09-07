# -*- coding: utf-8 -*-
"""建表：python init_db.py

MySQL 部署前需先建库与账号（README.md 有 SQL）；本机 SQLite 自测无需任何准备。
"""
import os

import sqlalchemy as sa

import app as backend

DB_URL = os.environ.get(
    "CLOUDRANK_DB",
    "mysql+pymysql://rank:rankpass@127.0.0.1:3306/cloud_rank?charset=utf8mb4")


def main():
    eng = sa.create_engine(DB_URL)
    backend.Base.metadata.create_all(eng)
    print("tables ready:", list(backend.Base.metadata.tables))


if __name__ == "__main__":
    main()
