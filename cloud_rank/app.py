# -*- coding: utf-8 -*-
"""水豚噜噜 · DeepSeek 用量监控 —— 云端排名后端（FastAPI + MySQL）

功能：
  - 邮箱+密码注册 / 登录（token 长期有效，客户端本地保存）
  - 客户端每 30s 上报【当日累计 token】（多数据源总量），服务端按 (用户, 日期) 存当日最新值
  - 上报响应直接携带全平台当日榜单（HTTP 30s 轮询等效"半分钟广播"，客户端本地渲染）
  - 跨天自动开新榜（daily_usage 按 date 分区），无需定时任务
  - GET / 简易榜单网页

隐私：只接收 token 数字，不接收任何对话内容 / API Key / 余额等。
"""
import hashlib
import os
import re
import secrets

from datetime import date, datetime, timezone, timedelta

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import (create_engine, Column, Integer, BigInteger, String,
                        Date, DateTime, UniqueConstraint)
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------- 配置 ----------------
DB_URL = os.environ.get(
    "CLOUDRANK_DB",
    "mysql+pymysql://root:root@127.0.0.1:3306/cloud_rank?charset=utf8")
TOKEN_TTL_DAYS = 365  # token 有效期（登录态本地保存，足够长）
BC = os.environ.get("CLOUDRANK_BC", "Asia/Shanghai")  # 排名"天"的时区
TZ = timezone(timedelta(hours=8))  # 服务按北京时间划分自然日

engine = create_engine(DB_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


# ---------------- 模型 ----------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(190), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)   # pbkdf2_sha256$iter$salt$hash
    nickname = Column(String(60), nullable=False, default="")
    token = Column(String(64), nullable=False, default="")
    token_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)


class Admin(Base):
    """管理员（独立于普通用户体系，可多管理员）。"""
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(190), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    token = Column(String(64), nullable=False, default="")
    token_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    day = Column(Date, nullable=False)          # 自然日（北京时间）
    tokens = Column(BigInteger, nullable=False, default=0)  # 当日累计（最新上报值）
    updated_at = Column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_user_day"),)


# ---------------- 密码/token ----------------
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000)
    return "pbkdf2$%s$%s" % (salt, dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, hexv = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000)
        return secrets.compare_digest(dk.hex(), hexv)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_hex(32)


def today_bj() -> date:
    return datetime.now(TZ).date()


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def mask_email(email: str) -> str:
    """邮箱脱敏：保留 @ 前首尾各 2 字符，中间用 *** 替换（过短则全打码）。

    例：alice123@test.com -> al***23@test.com；ab@x.com -> ***@x.com
    供对外榜单等场景使用，保护用户隐私。
    """
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.partition("@")
    if len(local) <= 4:
        return "***@" + domain
    return local[:2] + "***" + local[-2:] + "@" + domain


# ---------------- 请求体 ----------------
class RegisterIn(BaseModel):
    email: str = Field(max_length=190)
    password: str = Field(min_length=6, max_length=72)
    nickname: str = Field(default="", max_length=60)


class LoginIn(BaseModel):
    email: str = Field(max_length=190)
    password: str = Field(max_length=72)


class ReportIn(BaseModel):
    tokens: int = Field(ge=0, le=10**15)  # 当日累计 token（多数据源总量）


# ---------------- FastAPI ----------------
app = FastAPI(title="CloudRank", docs_url=None, redoc_url=None)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def auth_user(authorization: str = Header(default=""), db=Depends(get_db)):
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "未登录")
    token = authorization[7:].strip()
    u = db.query(User).filter(User.token == token).first()
    if not u:
        raise HTTPException(401, "登录已失效，请重新登录")
    if u.token_expires and u.token_expires < now_utc():
        raise HTTPException(401, "登录已过期，请重新登录")
    return u


def _public_user(u: User):
    return {"user_id": u.id, "email": u.email,
            "nickname": u.nickname or u.email.split("@")[0]}


@app.post("/api/register")
def register(body: RegisterIn, db=Depends(get_db)):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "邮箱格式不正确")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "该邮箱已注册")
    u = User(email=email, password_hash=hash_password(body.password),
             nickname=body.nickname.strip(), token=new_token(),
             token_expires=now_utc() + timedelta(days=TOKEN_TTL_DAYS),
             created_at=now_utc())
    db.add(u)
    db.commit()
    return {"ok": True, "token": u.token, "user": _public_user(u)}


@app.post("/api/login")
def login(body: LoginIn, db=Depends(get_db)):
    email = body.email.strip().lower()
    u = db.query(User).filter(User.email == email).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
    u.token = new_token()
    u.token_expires = now_utc() + timedelta(days=TOKEN_TTL_DAYS)
    db.commit()
    return {"ok": True, "token": u.token, "user": _public_user(u)}


@app.post("/api/report")
def report(body: ReportIn, user: User = Depends(auth_user), db=Depends(get_db)):
    """上报当日累计 token；返回 {ok, my_today, board} 即一次"广播"。

    board: 当日全平台榜单 [{rank, email, nickname, tokens}]（最多 200 名）。
    """
    day = today_bj()
    now = now_utc()
    row = db.query(DailyUsage).filter(DailyUsage.user_id == user.id,
                                      DailyUsage.day == day).first()
    if row:
        row.tokens = body.tokens
        row.updated_at = now
    else:
        db.add(DailyUsage(user_id=user.id, day=day, tokens=body.tokens,
                          updated_at=now))
    db.commit()
    board = build_board(db, day, limit=200)
    return {"ok": True, "day": day.isoformat(), "board": board}


@app.get("/api/board")
def board(user: User = Depends(auth_user), db=Depends(get_db)):
    day = today_bj()
    return {"ok": True, "day": day.isoformat(), "board": build_board(db, day, 200)}


def build_board(db, day, limit=200):
    """当日榜单：按 tokens 倒序；tokens=0 不上榜。"""
    rows = (db.query(DailyUsage)
            .filter(DailyUsage.day == day, DailyUsage.tokens > 0)
            .order_by(DailyUsage.tokens.desc(), DailyUsage.updated_at.asc())
            .limit(limit).all())
    ids = [r.user_id for r in rows]
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} \
        if ids else {}
    out = []
    for r in rows:
        u = users.get(r.user_id)
        if u:
            masked = mask_email(u.email)
            nick = u.nickname or masked.split("@")[0]
        else:
            masked, nick = "?", ""
        out.append({"rank": len(out) + 1,
                    "user_id": r.user_id,
                    "email": masked,
                    "nickname": nick,
                    "tokens": r.tokens})
    return out


@app.get("/api/me")
def me(user: User = Depends(auth_user)):
    return {"ok": True, "user": _public_user(user)}


# ---------------- 简易网页 ----------------
_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>水豚噜噜 · Token 排行</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:"Microsoft YaHei",sans-serif;background:#faf3e7;margin:0;padding:24px;color:#4a2f1d}
 h1{font-size:20px}.wrap{max-width:640px;margin:0 auto}
 input{padding:6px 8px;margin:2px;border:1px solid #bf9674;border-radius:4px;width:220px}
 button{padding:6px 14px;margin:2px;border:0;border-radius:4px;background:#f1ac38;color:#572c19;font-weight:bold;cursor:pointer}
 table{width:100%;border-collapse:collapse;margin-top:12px;background:#fffdf8}
 th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #ead9c3}
 th{background:#bf9674;color:#fff}.me{background:#fdf0d8;font-weight:bold}
 .t{font-variant-numeric:tabular-nums}.err{color:#d9534f;font-size:13px}
 #auth,#main{display:none}
</style></head><body><div class="wrap">
<h1>🦫 水豚噜噜 · Token 排行</h1>
<div id="auth">
 <p>邮箱+密码登录（无账号将自动注册）</p>
 <input id="email" placeholder="邮箱"><br>
 <input id="pass" type="password" placeholder="密码（至少6位）"><br>
 <input id="nick" placeholder="昵称（可留空）"><br>
 <button onclick="login()">登录 / 注册</button>
 <span class="err" id="err"></span>
</div>
<div id="main">
 <p><b id="who"></b> · 今日：<b id="meinfo"></b>
   <button onclick="logout()">退出</button>
   <button onclick="refreshBoard()">刷新</button></p>
 <table><thead><tr><th>#</th><th>昵称</th><th>邮箱</th><th style="text-align:right">今日 Token</th></tr></thead>
 <tbody id="rows"></tbody></table>
 <p class="t" id="hint" style="color:#8a6a4d;font-size:12px"></p>
</div>
</div>
<script>
let token=localStorage.getItem('cr_token')||'', me=JSON.parse(localStorage.getItem('cr_me')||'null');
function show(which){document.getElementById('auth').style.display=which==='auth'?'':'none';
 document.getElementById('main').style.display=which==='main'?'':'none';}
async function api(path,opt){const r=await fetch(path,opt);const j=await r.json();
 if(!r.ok) throw new Error(j.detail||'请求失败');return j;}
async function login(){const err=document.getElementById('err');err.textContent='';
 const email=document.getElementById('email').value.trim();
 const pass=document.getElementById('pass').value;
 const nick=document.getElementById('nick').value.trim();
 if(!email||pass.length<6){err.textContent='请填写邮箱与至少6位密码';return;}
 try{let j;try{j=await api('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({email,password:pass})});}catch(e){j=await api('/api/register',
   {method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email,password:pass,nickname:nick})});}
   token=j.token;me=j.user;localStorage.setItem('cr_token',token);
   localStorage.setItem('cr_me',JSON.stringify(me));enter();}
 catch(e){err.textContent=String(e.message||e);}}
function logout(){token='';me=null;localStorage.removeItem('cr_token');
 localStorage.removeItem('cr_me');document.getElementById('email').value='';show('auth');}
async function refreshBoard(){const hint=document.getElementById('hint');hint.textContent='更新中…';
 try{const j=await api('/api/board',{headers:{Authorization:'Bearer '+token}});render(j.board);}
 catch(e){hint.textContent=String(e.message||e);}}
function render(board){const tb=document.getElementById('rows');tb.innerHTML='';
 if(!board.length){tb.innerHTML='<tr><td colspan=4 style="color:#8a6a4d">今日暂无数据</td></tr>';}
 board.forEach(b=>{const tr=document.createElement('tr');
 if(me&&b.user_id===me.user_id)tr.className='me';
 const toks=(b.tokens/1e6).toFixed(2);
 tr.innerHTML='<td>'+(b.rank)+'</td><td>'+b.nickname+'</td><td>'+b.email
   +'</td><td style="text-align:right">'+b.tokens.toLocaleString()+'</td>';
 tb.appendChild(tr);});
 document.getElementById('hint').textContent='共 '+(board.length||0)+' 人上榜 · 半分钟自动同步';
}
async function enter(){show('main');document.getElementById('who').textContent=
 '你好，'+(me.nickname||me.email);try{
  // 每 30s 用 token 拉一次榜单（等效半分钟广播）
  const tick=async()=>{try{const j=await api('/api/board',{headers:{Authorization:'Bearer '+token}});
   render(j.board);document.getElementById('meinfo').textContent=
    (j.board.find(b=>me&&b.user_id===me.user_id)||{}).tokens?('第 '+(
    (j.board.find(b=>me&&b.user_id===me.user_id)||{}).rank)+' 名'):
    '未上榜';}catch(e){}};
  await tick();setInterval(tick,30000);
 }catch(e){document.getElementById('hint').textContent=String(e.message||e);}}
if(token&&me){enter();}else{show('auth');}
</script></body></html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_HTML)


# ---------------- 管理员面板 ----------------
def admin_auth(authorization: str = Header(default=""), db=Depends(get_db)):
    """管理员鉴权：Bearer admin token。"""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "未登录")
    token = authorization[7:].strip()
    a = db.query(Admin).filter(Admin.token == token).first()
    if not a:
        raise HTTPException(401, "登录已失效")
    if a.token_expires and a.token_expires < now_utc():
        raise HTTPException(401, "登录已过期")
    return a


class AdminLoginIn(BaseModel):
    email: str = Field(max_length=190)
    password: str = Field(max_length=72)


@app.post("/api/admin/login")
def admin_login(body: AdminLoginIn, db=Depends(get_db)):
    email = body.email.strip().lower()
    a = db.query(Admin).filter(Admin.email == email).first()
    if not a or not verify_password(body.password, a.password_hash):
        raise HTTPException(401, "邮箱或密码错误")
    a.token = new_token()
    a.token_expires = now_utc() + timedelta(days=30)
    db.commit()
    return {"ok": True, "token": a.token, "email": a.email}


@app.post("/api/admin/setup")
def admin_setup(body: AdminLoginIn, db=Depends(get_db)):
    """首次安装：创建管理员账号（仅当 admin_users 为空时允许）。"""
    if db.query(Admin).count() > 0:
        raise HTTPException(400, "管理员已存在")
    if not EMAIL_RE.match(body.email.strip().lower()):
        raise HTTPException(400, "邮箱格式不正确")
    a = Admin(email=body.email.strip().lower(),
              password_hash=hash_password(body.password),
              token=new_token(),
              token_expires=now_utc() + timedelta(days=30),
              created_at=now_utc())
    db.add(a)
    db.commit()
    return {"ok": True, "token": a.token}


@app.get("/api/admin/overview")
def admin_overview(_: Admin = Depends(admin_auth), db=Depends(get_db)):
    day = today_bj()
    total_users = db.query(User).count()
    today_rows = db.query(DailyUsage).filter(DailyUsage.day == day).all()
    online = sum(1 for r in today_rows
                 if r.updated_at and (now_utc() - r.updated_at).total_seconds() < 1800)
    today_total = sum(r.tokens for r in today_rows)
    return {"ok": True, "day": day.isoformat(),
            "total_users": total_users, "today_users": len(today_rows),
            "today_total": today_total, "online_users": online}


@app.get("/api/admin/users")
def admin_users(q: str = "", _: Admin = Depends(admin_auth), db=Depends(get_db)):
    day = today_bj()
    usages = {u.user_id: u for u in db.query(DailyUsage)
              .filter(DailyUsage.day == day).all()}
    query = db.query(User).order_by(User.created_at.desc())
    if q:
        like = "%%%s%%" % q
        query = query.filter((User.email.like(like)) | (User.nickname.like(like)))
    out = []
    for u in query.limit(500).all():
        usage = usages.get(u.id)
        out.append({"user_id": u.id,
                    "email": u.email,
                    "nickname": u.nickname,
                    "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else "",
                    "today_tokens": (usage.tokens if usage else 0),
                    "updated_at": usage.updated_at.strftime("%Y-%m-%d %H:%M")
                                   if usage and usage.updated_at else ""})
    return {"ok": True, "day": day.isoformat(), "users": out}


@app.get("/api/admin/daily")
def admin_daily(days: int = 30, _: Admin = Depends(admin_auth), db=Depends(get_db)):
    """近 N 天每日平台总 token（每天取所有用户最新值之和）。"""
    rows = (db.query(DailyUsage)
            .filter(DailyUsage.day >= date.today() - timedelta(days=days - 1))
            .all())
    tot = {}
    for r in rows:
        tot[r.day] = tot.get(r.day, 0) + r.tokens
    out = []
    for i in range(days):
        d = date.today() - timedelta(days=days - 1 - i)
        out.append({"day": d.isoformat(), "tokens": tot.get(d, 0)})
    return {"ok": True, "daily": out}


@app.get("/api/admin/board")
def admin_board(_: Admin = Depends(admin_auth), db=Depends(get_db)):
    """今日全平台榜单（管理员视角，邮箱脱敏）。"""
    day = today_bj()
    return {"ok": True, "day": day.isoformat(), "board": build_board(db, day, 200)}


_ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>水豚噜噜 · 云排名管理后台</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:"Microsoft YaHei",sans-serif;background:#faf3e7;margin:0;color:#4a2f1d}
 .wrap{max-width:980px;margin:0 auto;padding:20px}
 h1{font-size:20px;margin:6px 0}
 .cards{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
 .card{flex:1;min-width:170px;background:#fffdf8;border:1px solid #ead9c3;border-radius:8px;padding:14px}
 .card .v{font-size:26px;font-weight:bold;color:#d77522;font-variant-numeric:tabular-nums}
 .card .t{color:#8a6a4d;font-size:12px}
 input,button{padding:7px 10px;border:1px solid #bf9674;border-radius:4px;font-size:13px}
 button{background:#f1ac38;border:0;color:#572c19;font-weight:bold;cursor:pointer}
 .err{color:#d9534f;font-size:13px}
 table{width:100%;border-collapse:collapse;background:#fffdf8;font-size:13px}
 th,td{padding:7px 9px;text-align:left;border-bottom:1px solid #ead9c3;white-space:nowrap}
 th{background:#bf9674;color:#fff}
 table td:last-child,table th:last-child{text-align:right}
 canvas{background:#fffdf8;border:1px solid #ead9c3;border-radius:8px;margin-top:10px}
 .sec{margin-top:22px}
 .tab{display:inline-block;padding:6px 14px;margin-right:6px;background:#ead9c3;border-radius:5px 5px 0 0;cursor:pointer;font-size:13px}
 .tab.on{background:#bf9674;color:#fff}
 .panel{display:none}
 .panel.on{display:block}
 #auth,#main{display:none}
</style></head><body><div class="wrap">
<h1>🦫 水豚噜噜 · 云排名管理后台</h1>
<div id="auth">
 <p style="color:#8a6a4d">管理员登录（独立账号）</p>
 <input id="aemail" placeholder="管理员邮箱"><br><br>
 <input id="apass" type="password" placeholder="密码"><br><br>
 <button onclick="al()">登录</button> <span class="err" id="aerr"></span>
</div>
<div id="main">
 <p><b>数据总览</b> · <span id="day" style="color:#8a6a4d"></span>
   <span style="float:right"><button onclick="loadAll()">刷新</button>
   <button onclick="logout()">退出</button></span></p>
 <div class="cards">
   <div class="card"><div class="v" id="c_users">-</div><div class="t">总注册用户</div></div>
   <div class="card"><div class="v" id="c_today">-</div><div class="t">今日上报用户</div></div>
   <div class="card"><div class="v" id="c_online">-</div><div class="t">近30分钟活跃</div></div>
   <div class="card"><div class="v" id="c_total">-</div><div class="t">今日全部 Token</div></div>
 </div>
 <div class="sec">
  <span class="tab on" data-p="board">今日榜单</span>
  <span class="tab" data-p="users">用户列表</span>
  <span class="tab" data-p="trend">近30天趋势</span>
 </div>
 <div class="panel on" id="p-board">
  <table><thead><tr><th>#</th><th>昵称</th><th>邮箱</th><th style="text-align:right">今日 Token</th></tr></thead>
  <tbody id="board"></tbody></table>
 </div>
 <div class="panel" id="p-users">
  <input id="q" placeholder="搜索邮箱/昵称" style="width:220px" oninput="loadUsers()">
  <table><thead><tr><th>ID</th><th>邮箱</th><th>昵称</th><th>今日Token</th><th>最近上报</th><th>注册</th></tr></thead>
  <tbody id="users"></tbody></table>
 </div>
 <div class="panel" id="p-trend">
  <canvas id="cv" width="940" height="220"></canvas>
 </div>
</div>
</div>
<script>
let tok=localStorage.getItem('adm_token')||'';
function api(path,opt){return fetch(path,opt).then(async r=>{const j=await r.json();
 if(!r.ok)throw new Error(j.detail||'失败');return j;});}
function al(){const e=document.getElementById('aemail').value.trim(),
  p=document.getElementById('apass').value;const err=document.getElementById('aerr');err.textContent='';
 if(!e||p.length<6){err.textContent='请填写邮箱与密码';return;}
 api('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({email:e,password:p})}).then(j=>{tok=j.token;
  localStorage.setItem('adm_token',tok);enter();}).catch(e=>err.textContent=String(e.message||e));}
function logout(){tok='';localStorage.removeItem('adm_token');
 document.getElementById('auth').style.display='';document.getElementById('main').style.display='none';}
function fmt(n){return (n||0).toLocaleString();}
async function loadOverview(){const j=await api('/api/admin/overview',
  {headers:{Authorization:'Bearer '+tok}});
 document.getElementById('day').textContent=j.day;
 document.getElementById('c_users').textContent=fmt(j.total_users);
 document.getElementById('c_today').textContent=fmt(j.today_users);
 document.getElementById('c_online').textContent=fmt(j.online_users);
 document.getElementById('c_total').textContent=fmt(j.today_total);}
async function loadBoard(){const j=await api('/api/admin/board',
  {headers:{Authorization:'Bearer '+tok}});
 const tb=document.getElementById('board');tb.innerHTML=(j.board.length?j.board.map(b=>
  '<tr><td>'+b.rank+'</td><td>'+b.nickname+'</td><td>'+b.email+'</td><td>'+fmt(b.tokens)+'</td></tr>'
 ).join(''):'<tr><td colspan=4 style="color:#8a6a4d">今日暂无数据</td></tr>');}
async function loadUsers(){const q=document.getElementById('q').value.trim();
 const j=await api('/api/admin/users?q='+encodeURIComponent(q),
  {headers:{Authorization:'Bearer '+tok}});
 const tb=document.getElementById('users');tb.innerHTML=(j.users.length?j.users.map(u=>
  '<tr><td>'+u.user_id+'</td><td>'+u.email+'</td><td>'+u.nickname+'</td><td>'+fmt(u.today_tokens)
  +'</td><td>'+u.updated_at+'</td><td>'+u.created_at+'</td></tr>').join(''):
  '<tr><td colspan=6 style="color:#8a6a4d">暂无用户</td></tr>');}
async function loadTrend(){const j=await api('/api/admin/daily',
  {headers:{Authorization:'Bearer '+tok}});
 const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
 const d=j.daily||[];ctx.clearRect(0,0,cv.width,cv.height);
 ctx.fillStyle='#8a6a4d';ctx.font='12px sans-serif';
 const pad=46,base=cv.height-26,top=14,w=(cv.width-pad-20)/(d.length||1);
 const max=Math.max(1,...d.map(x=>x.tokens));
 d.forEach((x,i)=>{const h=(x.tokens/max)*(base-top);
  ctx.fillStyle=x.tokens?'#f1ac38':'#ead9c3';
  ctx.fillRect(pad+i*w,base-h,Math.max(w*0.6,3),h);
  if(d.length<=15||i%3===0){ctx.fillStyle='#8a6a4d';
   ctx.fillText(x.day.slice(5),pad+i*w,base+14);}});
 ctx.fillText('','','');}
async function enter(){document.getElementById('auth').style.display='none';
 document.getElementById('main').style.display='block';
 try{await loadAll();}catch(e){logout();
  document.getElementById('aerr').textContent=String(e.message||e);}}
async function loadAll(){try{await loadOverview();await loadBoard();await loadUsers();
 await loadTrend();}catch(e){document.getElementById('aerr').textContent=String(e.message||e);}}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',e=>{
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
 t.classList.add('on');document.getElementById('p-'+t.dataset.p).classList.add('on');}));
if(tok){enter();}else{document.getElementById('auth').style.display='block';}
</script></body></html>
"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return HTMLResponse(_ADMIN_HTML)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("CLOUDRANK_PORT", 8000)))
