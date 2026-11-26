"""TNG Gemba Online demo - standalone FastAPI/SQLite application.
NO connection to any TNG production service.
"""
import csv
import io
import json
import os
import re
import secrets
import hashlib
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('DATA_DIR', str(BASE / 'data')))
UPLOAD_DIR = Path(os.environ.get('UPLOAD_DIR', str(BASE / 'uploads')))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'gemba.sqlite3'
VN = timezone(timedelta(hours=7))
app = FastAPI(title='TNG Gemba Online DEMO', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=str(BASE / 'app' / 'static')), name='static')
app.mount('/uploads', StaticFiles(directory=str(UPLOAD_DIR)), name='uploads')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def to_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def db_conn():
    c = sqlite3.connect(str(DB_PATH), timeout=20, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=20000')
    return c


@contextmanager
def db():
    c = db_conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000)
    return f'{salt}:{dk.hex()}'


def check_password(password, saved):
    try:
        salt, value = saved.split(':')
        return secrets.compare_digest(password_hash(password, salt), saved)
    except (ValueError, AttributeError):
        return False


def init():
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, branch TEXT NOT NULL DEFAULT 'Bông',
          department TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
          email TEXT UNIQUE NOT NULL, role TEXT NOT NULL DEFAULT 'manager',
          active INTEGER NOT NULL DEFAULT 1, password TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gembas (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          creator_id INTEGER NOT NULL REFERENCES users(id), branch TEXT NOT NULL,
          area TEXT NOT NULL DEFAULT '', problem TEXT NOT NULL, solution TEXT NOT NULL DEFAULT '',
          deadline TEXT NOT NULL, photo TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
          review_round INTEGER NOT NULL DEFAULT 1, passed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS assignments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          gemba_id INTEGER NOT NULL REFERENCES gembas(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id), done INTEGER NOT NULL DEFAULT 0,
          done_at TEXT, note TEXT NOT NULL DEFAULT '', photo TEXT NOT NULL DEFAULT '',
          UNIQUE(gemba_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS pig (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          gemba_id INTEGER NOT NULL REFERENCES gembas(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id),
          round INTEGER NOT NULL, reason TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
          UNIQUE(gemba_id,user_id,round,reason)
        );
        CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          gemba_id INTEGER NOT NULL REFERENCES gembas(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        ''')
        # Owner must configure an admin password; never ship a public fixed admin credential.
        if c.execute('SELECT count(*) AS n FROM users').fetchone()['n'] == 0:
            email = os.environ.get('ADMIN_EMAIL', 'admin@demo.local').strip().lower()
            pwd = os.environ.get('ADMIN_PASSWORD')
            if not pwd:
                pwd = secrets.token_urlsafe(15)
                print('\n' + '=' * 60 + '\nGEMBA DEMO ADMIN ACCOUNT\n'
                      f'Email: {email}\nOne-time generated password: {pwd}\n'
                      'Save this password. Set ADMIN_PASSWORD on hosting before deployment.\n' + '=' * 60, flush=True)
            c.execute('INSERT INTO users(code,name,branch,department,title,email,role,password,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                      ('ADMIN001', 'Quản trị DEMO', 'Bông', 'Ban quản lý', 'Quản trị', email, 'admin', password_hash(pwd), now_iso()))

init()


@app.middleware('http')
async def protect(request: Request, call_next):
    resp = await call_next(request)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    if request.url.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp


def clean_user(row):
    if row is None:
        return None
    d = dict(row)
    d.pop('password', None)
    d['active'] = bool(d['active'])
    return d


def get_user(request, c):
    token = request.cookies.get('gemba_session', '')
    if not token:
        raise HTTPException(status_code=401, detail='Hãy đăng nhập')
    h = hashlib.sha256(token.encode()).hexdigest()
    u = c.execute('SELECT u.* FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1', (h, now_iso())).fetchone()
    if not u:
        raise HTTPException(status_code=401, detail='Phiên đăng nhập đã hết hạn')
    return u


def is_admin(u):
    return u['role'] == 'admin'


def admin_only(u):
    if not is_admin(u):
        raise HTTPException(403, 'Chỉ quản trị viên được thực hiện')


def event(c, gid, uid, action, note=''):
    c.execute('INSERT INTO history(gemba_id,user_id,action,note,created_at) VALUES(?,?,?,?,?)', (gid, uid, action, note, now_iso()))


def pig_insert(c, gid, uid, rnd, reason, detail):
    c.execute('INSERT OR IGNORE INTO pig(gemba_id,user_id,round,reason,detail,created_at) VALUES(?,?,?,?,?,?)', (gid, uid, rnd, reason, detail, now_iso()))


def sweep(c):
    rows = c.execute('''SELECT g.id AS gid,g.review_round,g.deadline,a.user_id,a.done,a.done_at
      FROM assignments a JOIN gembas g ON g.id=a.gemba_id WHERE g.status != 'passed' ''').fetchall()
    current = datetime.now(timezone.utc)
    for a in rows:
        deadline = to_dt(a['deadline'])
        if deadline and ((a['done'] and to_dt(a['done_at']) and to_dt(a['done_at']) > deadline) or (not a['done'] and current > deadline)):
            pig_insert(c, a['gid'], a['user_id'], a['review_round'], 'overdue', 'Xử lý sau hạn' if a['done'] else 'Quá hạn chưa xử lý xong')


class Login(BaseModel):
    email: str
    password: str


@app.get('/')
def index():
    return FileResponse(BASE / 'app' / 'static' / 'index.html')


@app.post('/api/login')
def login(payload: Login, response: Response):
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE lower(email)=? AND active=1', (payload.email.strip().lower(),)).fetchone()
        if not u or not check_password(payload.password, u['password']):
            raise HTTPException(401, 'Email hoặc mật khẩu không chính xác')
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), u['id'], (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()))
        # Secure cookies on HTTPS, controlled in deployment; localhost supports http.
        response.set_cookie('gemba_session', token, httponly=True, secure=os.environ.get('COOKIE_SECURE','0')=='1', samesite='strict', max_age=604800, path='/')
        return {'user': clean_user(u)}


@app.post('/api/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get('gemba_session', '')
    with db() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie('gemba_session', path='/')
    return {'ok': True}


@app.get('/api/me')
def me(request: Request):
    with db() as c:
        return {'user': clean_user(get_user(request, c))}


@app.get('/api/users')
def users(request: Request):
    with db() as c:
        get_user(request, c)
        return [clean_user(r) for r in c.execute('SELECT * FROM users ORDER BY active DESC, name').fetchall()]


class UserPayload(BaseModel):
    code: str = Field(min_length=2)
    name: str = Field(min_length=2)
    email: str = Field(min_length=5)
    branch: str = 'Bông'
    department: str = ''
    title: str = ''
    role: str = 'manager'
    active: bool = True
    password: Optional[str] = None


def validate_email(e):
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', e.strip().lower()):
        raise HTTPException(400, 'Email không hợp lệ')


@app.post('/api/users')
def create_user(p: UserPayload, request: Request):
    with db() as c:
        admin_only(get_user(request, c))
        validate_email(p.email)
        if p.role not in ('admin','manager','handler'):
            raise HTTPException(400, 'Quyền không hợp lệ')
        pwd = p.password or secrets.token_urlsafe(9)
        if len(pwd) < 8:
            raise HTTPException(400,'Mật khẩu tối thiểu 8 ký tự')
        try:
            cur = c.execute('''INSERT INTO users(code,name,email,branch,department,title,role,active,password,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (p.code.strip().upper(),p.name.strip(),p.email.strip().lower(),p.branch.strip(),p.department.strip(),p.title.strip(),p.role,int(p.active),password_hash(pwd),now_iso()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Mã nhân viên hoặc email đã có')
        return {'id':cur.lastrowid,'temporary_password':pwd}


class UserUpdate(BaseModel):
    name: Optional[str] = None
    branch: Optional[str] = None
    department: Optional[str] = None
    title: Optional[str] = None
    role: Optional[str] = None
    active: Optional[bool] = None
    password: Optional[str] = None


@app.patch('/api/users/{uid}')
def patch_user(uid: int, p: UserUpdate, request: Request):
    with db() as c:
        u = get_user(request, c)
        admin_only(u)
        target = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if target is None: raise HTTPException(404,'Không thấy người dùng')
        values = p.model_dump(exclude_none=True)
        if 'role' in values and values['role'] not in ('admin','manager','handler'): raise HTTPException(400,'Quyền không hợp lệ')
        if 'password' in values:
            if len(values['password']) < 8: raise HTTPException(400,'Mật khẩu tối thiểu 8 ký tự')
            values['password'] = password_hash(values['password'])
        if 'active' in values: values['active'] = int(values['active'])
        if uid == u['id'] and (values.get('active') == 0 or values.get('role','admin') != 'admin'):
            raise HTTPException(400,'Không thể tự khoá hoặc hạ quyền chính mình')
        if values:
            c.execute('UPDATE users SET '+','.join(k+'=?' for k in values)+' WHERE id=?', (*values.values(),uid))
        return {'ok':True}


@app.post('/api/users/import')
async def import_users(request: Request, file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.csv'):
        raise HTTPException(400, 'Hãy xuất Excel thành CSV UTF-8 và tải lên')
    content = await file.read(350000)
    text = content.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    required = {'code','name','email'}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise HTTPException(400, 'CSV phải có các cột code,name,email,branch,department,title,role')
    results=[]
    with db() as c:
        admin_only(get_user(request,c))
        for idx, row in enumerate(reader, start=2):
            code = (row.get('code') or '').strip().upper()
            name = (row.get('name') or '').strip()
            email = (row.get('email') or '').strip().lower()
            if not code or not name or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
                results.append({'row':idx,'error':'Thiếu mã/tên hoặc email không hợp lệ'});continue
            password = secrets.token_urlsafe(9)
            role = (row.get('role') or 'manager').strip().lower()
            if role not in ('manager','handler','admin'): role='manager'
            try:
                c.execute('''INSERT INTO users(code,name,email,branch,department,title,role,password,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                  (code,name,email,row.get('branch') or 'Bông',row.get('department') or '',row.get('title') or '',role,password_hash(password),now_iso()))
                results.append({'row':idx,'name':name,'email':email,'password':password})
            except sqlite3.IntegrityError:
                results.append({'row':idx,'error':'Mã hoặc email đã tồn tại'})
    return {'results':results}


class GembaCreate(BaseModel):
    branch: str = Field(min_length=1)
    area: str = ''
    problem: str = Field(min_length=3)
    solution: str = ''
    deadline: str
    assignees: list[int] = Field(min_length=1)
    photo: str = ''


@app.post('/api/gembas')
def create_gemba(p: GembaCreate, request: Request):
    deadline=to_dt(p.deadline)
    if not deadline: raise HTTPException(400,'Hạn xử lý không đúng định dạng')
    ids=list(dict.fromkeys(p.assignees))
    with db() as c:
        u=get_user(request,c)
        qs=','.join('?'*len(ids))
        found=c.execute('SELECT count(*) AS n FROM users WHERE active=1 AND id IN ('+qs+')', ids).fetchone()['n']
        if found!=len(ids):raise HTTPException(400,'Có người được giao chưa hoạt động hoặc không tồn tại')
        cur=c.execute('''INSERT INTO gembas(creator_id,branch,area,problem,solution,deadline,photo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',
          (u['id'],p.branch.strip(),p.area.strip(),p.problem.strip(),p.solution.strip(),deadline.isoformat(),p.photo,now_iso(),now_iso()))
        gid=cur.lastrowid
        for uid in ids:
            c.execute('INSERT INTO assignments(gemba_id,user_id) VALUES(?,?)',(gid,uid))
        event(c,gid,u['id'],'created','Tạo Gemba')
        return {'id':gid}


def get_gemba(c, gid):
    row=c.execute('''SELECT g.*,u.name AS creator_name FROM gembas g JOIN users u ON u.id=g.creator_id WHERE g.id=?''',(gid,)).fetchone()
    if not row: raise HTTPException(404,'Không tìm thấy Gemba')
    out=dict(row)
    out['code']='GB-'+str(out['id']).zfill(5)
    out['assignments']=[dict(x) for x in c.execute('''SELECT a.*,u.name AS name,u.code AS code FROM assignments a JOIN users u ON u.id=a.user_id WHERE a.gemba_id=? ORDER BY a.id''',(gid,)).fetchall()]
    out['history']=[dict(x) for x in c.execute('''SELECT h.*,u.name AS actor FROM history h JOIN users u ON u.id=h.user_id WHERE h.gemba_id=? ORDER BY h.id DESC''',(gid,)).fetchall()]
    out['pig_count']=c.execute('SELECT count(*) AS n FROM pig WHERE gemba_id=?',(gid,)).fetchone()['n']
    return out


@app.get('/api/gembas')
def list_gembas(request: Request):
    with db() as c:
        get_user(request,c);sweep(c)
        ids=[r['id'] for r in c.execute('SELECT id FROM gembas ORDER BY id DESC LIMIT 1000').fetchall()]
        return [get_gemba(c,i) for i in ids]


@app.get('/api/gembas/{gid}')
def detail(gid: int, request: Request):
    with db() as c:
        get_user(request,c);sweep(c)
        return get_gemba(c,gid)


class DonePayload(BaseModel):
    done: bool = True
    note: str = ''
    photo: str = ''


@app.post('/api/assignments/{aid}/done')
def mark_done(aid: int,p: DonePayload, request: Request):
    with db() as c:
        u=get_user(request,c)
        a=c.execute('SELECT * FROM assignments WHERE id=?',(aid,)).fetchone()
        if not a:raise HTTPException(404,'Không tìm thấy phân công')
        g=c.execute('SELECT * FROM gembas WHERE id=?',(a['gemba_id'],)).fetchone()
        if g['status']=='passed':raise HTTPException(400,'Gemba đã hoàn thành')
        if a['user_id']!=u['id'] and not is_admin(u):raise HTTPException(403,'Chỉ người được giao mới được đánh dấu')
        timestamp=now_iso() if p.done else None
        c.execute('UPDATE assignments SET done=?,done_at=?,note=?,photo=? WHERE id=?', (int(p.done),timestamp,p.note.strip(),p.photo,aid))
        if p.done and to_dt(timestamp)>to_dt(g['deadline']):
            pig_insert(c,g['id'],a['user_id'],g['review_round'],'overdue','Xử lý xong sau thời hạn')
        pending=c.execute('SELECT count(*) AS n FROM assignments WHERE gemba_id=? AND done=0',(g['id'],)).fetchone()['n']
        next_status='awaiting_review' if pending==0 else ('rework' if g['review_round']>1 else 'open')
        c.execute('UPDATE gembas SET status=?,updated_at=? WHERE id=?',(next_status,now_iso(),g['id']))
        event(c,g['id'],u['id'],'done' if p.done else 'reopened',p.note)
        sweep(c)
        return get_gemba(c,g['id'])


class ReviewPayload(BaseModel):
    passed: bool
    note: str = ''


@app.post('/api/gembas/{gid}/review')
def review(gid:int,p:ReviewPayload,request:Request):
    with db() as c:
        u=get_user(request,c)
        g=c.execute('SELECT * FROM gembas WHERE id=?',(gid,)).fetchone()
        if not g:raise HTTPException(404,'Không tìm thấy Gemba')
        if g['creator_id']!=u['id']:raise HTTPException(403,'Chỉ người tạo được đánh giá')
        if g['status']!='awaiting_review':raise HTTPException(400,'Chưa xử lý xong tất cả các phân công')
        sweep(c)
        if p.passed:
            c.execute('UPDATE gembas SET status=?,passed_at=?,updated_at=? WHERE id=?',('passed',now_iso(),now_iso(),gid))
            event(c,gid,u['id'],'passed',p.note or 'Đạt')
        else:
            # Do not accuse a user after deadline unless they have an overdue event.
            if datetime.now(timezone.utc)<=to_dt(g['deadline']):
                for a in c.execute('SELECT user_id FROM assignments WHERE gemba_id=?',(gid,)):
                    pig_insert(c,gid,a['user_id'],g['review_round'],'failed',p.note or 'Đánh giá không đạt trong thời hạn')
            c.execute('UPDATE assignments SET done=0,done_at=NULL WHERE gemba_id=?',(gid,))
            c.execute('UPDATE gembas SET status=?,review_round=review_round+1,updated_at=? WHERE id=?',('rework',now_iso(),gid))
            event(c,gid,u['id'],'failed',p.note or 'Không đạt: yêu cầu làm lại')
        return get_gemba(c,gid)


class SolutionPayload(BaseModel):
    solution: str


@app.patch('/api/gembas/{gid}/solution')
def update_solution(gid:int,p:SolutionPayload,request:Request):
    with db() as c:
        u=get_user(request,c)
        g=c.execute('SELECT * FROM gembas WHERE id=?',(gid,)).fetchone()
        if not g:raise HTTPException(404,'Không có Gemba')
        if not (is_admin(u) or g['creator_id']==u['id'] or c.execute('SELECT 1 FROM assignments WHERE gemba_id=? AND user_id=?',(gid,u['id'])).fetchone()):
            raise HTTPException(403,'Không được sửa giải pháp')
        c.execute('UPDATE gembas SET solution=?,updated_at=? WHERE id=?',(p.solution.strip(),now_iso(),gid))
        event(c,gid,u['id'],'solution','Cập nhật giải pháp')
        return {'ok':True}


@app.get('/api/pig')
def pig(request:Request):
    with db() as c:
        get_user(request,c);sweep(c)
        return [dict(r) for r in c.execute('''SELECT p.*,u.name AS name,g.problem AS problem,g.branch AS branch FROM pig p JOIN users u ON u.id=p.user_id JOIN gembas g ON g.id=p.gemba_id ORDER BY p.id DESC''').fetchall()]


@app.get('/api/report')
def report(request:Request):
    with db() as c:
        get_user(request,c);sweep(c)
        rows=c.execute('''SELECT g.branch, count(*) AS total,
          sum(CASE WHEN status='passed' THEN 1 ELSE 0 END) AS passed,
          sum(CASE WHEN status!='passed' THEN 1 ELSE 0 END) AS open,
          sum(CASE WHEN status!='passed' AND deadline<? THEN 1 ELSE 0 END) AS overdue
          FROM gembas g GROUP BY g.branch ORDER BY g.branch''',(now_iso(),)).fetchall()
        return {'branches':[dict(r) for r in rows],
                'total':c.execute('SELECT count(*) AS n FROM gembas').fetchone()['n'],
                'pig':c.execute('SELECT count(*) AS n FROM pig').fetchone()['n']}


@app.post('/api/photo')
async def photo(request:Request,file:UploadFile=File(...)):
    with db() as c:get_user(request,c)
    content=await file.read(5_000_001)
    if len(content)>5_000_000:raise HTTPException(400,'Ảnh tối đa 5MB')
    if file.content_type not in ('image/jpeg','image/png','image/webp'):raise HTTPException(400,'Chỉ hỗ trợ JPG, PNG, WebP')
    ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp'}[file.content_type]
    filename=secrets.token_hex(16)+'.'+ext
    (UPLOAD_DIR/filename).write_bytes(content)
    return {'url':'/uploads/'+filename}


class ResetPayload(BaseModel):
    confirmation: str


@app.post('/api/reset-demo')
def reset_demo(p:ResetPayload,request:Request):
    with db() as c:
        admin_only(get_user(request,c))
        if p.confirmation!='XOA DU LIEU DEMO':raise HTTPException(400,'Nhập đúng cụm xác nhận')
        for table in ('pig','history','assignments','gembas'):
            c.execute('DELETE FROM '+table)
        return {'ok':True}


@app.get('/api/health')
def health():
    return {'status':'ok','demo':True}
