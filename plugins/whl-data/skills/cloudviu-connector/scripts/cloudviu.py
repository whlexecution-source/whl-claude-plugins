#!/usr/bin/env python3
"""
cloudviu-connector : ดึงข้อมูลจาก CloudViu (อ่านอย่างเดียว) ใช้ร่วมกันทุก project

  python cloudviu.py --account <บัญชี> login                     # ผู้ใช้รันเอง: ใส่เบอร์ + รหัส แล้วสแกนโปรเจกต์
  python cloudviu.py --account <บัญชี> projects --active
  python cloudviu.py --account <บัญชี> --org <id> records --from 2026-09-01 --to 2026-09-30
  python cloudviu.py --account <บัญชี> --org <id> attendance --from 2026-09-01 --to 2026-09-30

บัญชีอยู่ที่ ~/.cloudviu/accounts/<บัญชี>.json (แบบเดิม: --project <ชื่อ> + .claude/skills/cloudviu-<ชื่อ>/project.json)
รหัสผ่านเก็บใน Windows Credential Manager แยกต่อบัญชี (cloudviu:<บัญชี>) - ไม่อยู่ในไฟล์ใดๆ
สคริปต์ใช้แค่ GET กับข้อมูล (POST มีแค่ login + เลือกโปรเจกต์) ไม่มีคำสั่งใดแก้ไขข้อมูลใน CloudViu
หมายเหตุ: เป็น API ภายในของ CloudViu ไม่ใช่ API ทางการ อาจเปลี่ยนได้ทุกเมื่อ
"""

import argparse
import collections
import csv
import datetime as dt
import gzip
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

for _stream in (sys.stdout, sys.stderr):   # Windows console เป็น cp1252 พิมพ์ไทยไม่ได้
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# ============================== CONFIG (ต่อ project) ==============================

BASE = "https://app.cloudviu.co"
# CloudViu ย้ายจาก Remix เป็น React Router v7 เมื่อ 10 พ.ย. 2026 (~21:00) - ?_data=<route> ใช้ไม่ได้แล้ว (ได้ HTML)
#   login    POST /login.data              -> 202 + turbo-stream {redirect:'/team-groups'} / รหัสผิด = 401
#   project  POST /current-organization    -> JSON {event:'organization-changed', organizationId}
#   loader   GET  <path>.data?<params>     -> turbo-stream {<route id>: {data: <ข้อมูลของ route นั้น>}, ...}
#   session หมด -> 202 + SingleFetchRedirect {redirect:'/login'}
#   export   GET  /job-record-download?... (resource route ไม่เปลี่ยน)
CONNECTOR_VERSION = "2.2.0"   # 2.2.0: profile + ตรวจความผิดปกติหลังดึง / 2.1.0: --account (~/.cloudviu), login+scan, records (job-records API), doctor / 2.0.0: React Router v7 (.data + turbo-stream) / 1.2.0: setup ก่อนรู้ organization_id

SKILLS_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
REPO_ROOT = os.path.abspath(os.path.join(SKILLS_DIR, "..", ".."))

WORKSPACE_MARKER = ".whl-workspace"   # ไฟล์บอกว่าโฟลเดอร์นี้คือ WHL workspace มาตรฐาน (สร้างโดย skill whl-workspace)


def _find_workspace(start=None):
    """เดินขึ้นจากโฟลเดอร์ที่รันคำสั่ง หาโฟลเดอร์ที่มี .whl-workspace"""
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, WORKSPACE_MARKER)):
            return d
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def _take_arg(name):
    """ดึง --name value ออกจาก sys.argv (ก่อน argparse) คืน value หรือ None"""
    for i, a in enumerate(sys.argv[1:], 1):
        if a == name and i + 1 < len(sys.argv):
            v = sys.argv[i + 1]
            del sys.argv[i:i + 2]
            return v
        if a.startswith(name + "="):
            del sys.argv[i]
            return a.split("=", 1)[1]
    return None


# ที่เก็บผลลัพธ์ (ลำดับ):
#   1) env CLOUDVIU_OUT_ROOT
#   2) อยู่ใน WHL workspace: --into <โฟลเดอร์ project> -> projects/<project>/02_data / ไม่ใส่ -> _scratch (ไฟล์ชั่วคราว)
#   3) <repo>/out (รันจาก <repo>/.claude/skills แบบเดิม)  4) <โฟลเดอร์ที่รันคำสั่ง>/out
WORKSPACE = _find_workspace()
INTO = _take_arg("--into")
if INTO and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", INTO):
    print("ERROR: --into ต้องเป็นชื่อโฟลเดอร์ใน projects/ (a-z 0-9 - _ ตัวเล็ก เช่น brand-a-osa)", file=sys.stderr)
    sys.exit(1)
if os.environ.get("CLOUDVIU_OUT_ROOT"):
    OUT_ROOT = os.environ["CLOUDVIU_OUT_ROOT"]
elif WORKSPACE:
    if INTO and not os.path.isdir(os.path.join(WORKSPACE, "projects", INTO)):
        print(f"ERROR: ไม่มีโฟลเดอร์ projects/{INTO} ใน workspace - สร้างด้วย skill whl-workspace ก่อน", file=sys.stderr)
        sys.exit(1)
    OUT_ROOT = os.path.join(WORKSPACE, "projects", INTO, "02_data") if INTO else os.path.join(WORKSPACE, "_scratch")
elif os.path.basename(os.path.dirname(SKILLS_DIR)) == ".claude":
    OUT_ROOT = os.path.join(REPO_ROOT, "out")
else:
    OUT_ROOT = os.path.join(os.getcwd(), "out")


# ข้อมูลของผู้ใช้ (บัญชี + ผลสแกน) อยู่นอกโฟลเดอร์ skill - อัปเดต/ลง skill ใหม่แล้วไม่หาย / ไม่ติดไปตอนแจก skill
USER_DIR = os.environ.get("CLOUDVIU_HOME") or os.path.join(os.path.expanduser("~"), ".cloudviu")
ACCOUNTS_DIR = os.path.join(USER_DIR, "accounts")
ACCOUNTS_FILE = os.path.join(USER_DIR, "accounts.json")
ACCOUNT_MODE = False   # True = ใช้ --account (ไม่ต้องมีโฟลเดอร์ skill ต่อ project)


def _registry():
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_project():
    """--account <ชื่อ> [--org <id>]  = บัญชีใน ~/.cloudviu (แบบใหม่ สำหรับผู้ใช้ทั่วไป)
       --project <ชื่อ> / --config <path> = config ใน .claude/skills/cloudviu-<project>/project.json (แบบเดิม)"""
    global ACCOUNT_MODE
    account = _take_arg("--account") or os.environ.get("CLOUDVIU_ACCOUNT")
    org = _take_arg("--org")
    cfg_path = _take_arg("--config")
    name = _take_arg("--project") or os.environ.get("CLOUDVIU_PROJECT")
    if account and not cfg_path and not name:
        ACCOUNT_MODE = True
        if not re.fullmatch(r"[a-z0-9_-]+", account):
            print("ERROR: ชื่อบัญชีใช้ได้แค่ a-z 0-9 - _ (ตัวเล็ก)", file=sys.stderr)
            sys.exit(1)
        cfg_path = os.path.join(ACCOUNTS_DIR, f"{account}.json")
        if not os.path.exists(cfg_path):
            if "login" not in sys.argv[1:]:
                print(f"ERROR: ยังไม่มีบัญชี '{account}' - ให้ผู้ใช้รัน `cloudviu.py --account {account} login` "
                      "เองในเทอร์มินัลก่อน", file=sys.stderr)
                sys.exit(1)
            os.makedirs(ACCOUNTS_DIR, exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"_note": "บัญชี CloudViu (ไม่มีรหัสผ่านในไฟล์นี้ - รหัสอยู่ใน Credential Manager)",
                           "project": account, "display_name": account,
                           "organization_id": 0, "project_id": 0, "jobs": []}, f, ensure_ascii=False, indent=2)
    elif not cfg_path:
        if not name:
            print("ERROR: ต้องระบุบัญชี เช่น `cloudviu.py --account <ชื่อ> projects` "
                  "(หรือแบบเดิม --project <ชื่อ>)", file=sys.stderr)
            sys.exit(1)
        cfg_path = os.path.join(SKILLS_DIR, f"cloudviu-{name}", "project.json")
    if not os.path.exists(cfg_path):
        print(f"ERROR: ไม่พบ config ของ project: {cfg_path}", file=sys.stderr)
        sys.exit(1)
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    if org:   # เลือกโปรเจกต์ต่อคำสั่ง - jobs เอาจากผลสแกน (ตัด job ชื่อ test)
        if not org.isdigit():
            print("ERROR: --org ต้องเป็นเลขโปรเจกต์ (ดูจากคำสั่ง projects)", file=sys.stderr)
            sys.exit(1)
        if int(org) != int(cfg.get("organization_id") or 0):
            prof = cfg.get("credentials_from") or cfg.get("project")
            hit = next((p for p in (_registry().get(prof) or {}).get("projects", []) if p.get("id") == int(org)), {})
            cfg = dict(cfg, organization_id=int(org), project_id=int(org), display_name=hit.get("name") or f"project {org}",
                       jobs=[j["id"] for j in hit.get("jobs") or [] if not re.search(r"test", j.get("name") or "", re.I)])
    # 0 = ยังไม่รู้เลข: ตั้งบัญชี (setup) ได้ แต่คำสั่งที่ดึงข้อมูลจะหยุดตอน login
    bad = [k for k in ("organization_id", "project_id") if not str(cfg.get(k, "")).isdigit()]
    if not all(str(j).isdigit() and int(j) > 0 for j in cfg.get("jobs") or []):
        bad.append("jobs")   # ว่างได้ (ยังไม่รู้ job) - คำสั่งที่ต้องใช้ export จะแจ้งเอง
    if not re.fullmatch(r"[a-z0-9_-]+", str(cfg.get("project") or "")):
        bad.append("project (ตัวอังกฤษเล็ก/ตัวเลข/ - _ เท่านั้น)")
    if bad:
        print(f"ERROR: config {cfg_path} ยังไม่ครบหรือไม่ถูกต้อง: {', '.join(bad)}", file=sys.stderr)
        sys.exit(1)
    return cfg, cfg_path


CFG, CFG_PATH = load_project()
PROJECT = CFG["project"]
DISPLAY_NAME = CFG.get("display_name") or PROJECT
ORGANIZATION_ID = int(CFG["organization_id"])   # เลือกโปรเจกต์หลัง login (ใช้เลข project ไม่ใช่ team group)
PROJECT_ID = int(CFG["project_id"])             # พารามิเตอร์ projectId ของ URL Export
DEFAULT_JOBS = [int(j) for j in CFG["jobs"]]
CAMPAIGN = (CFG.get("osa") or {}).get("campaign", "OSA / OOS")   # ชื่อกลุ่มคอลัมน์ OSA ในไฟล์ Excel
# รหัสคำตอบของคำถาม OSA -> (label, group) กำหนดใน config ของ project
STATUS = {int(k): tuple(v) for k, v in ((CFG.get("osa") or {}).get("status") or {}).items()}

MAX_RANGE_DAYS = 62      # กันดึงเยอะเกินในครั้งเดียว
CHUNK_DAYS = 7           # ขอไฟล์ทีละกี่วัน (ไฟล์แนบ master ร้าน/สินค้า ~2.5MB ทุกครั้ง)
PAUSE_SEC = 1.0          # เว้นระหว่าง request ไม่ยิงถี่ใส่ระบบของ vendor
KEYRING_SERVICE = "cloudviu"

# ผลลัพธ์ไปที่ <repo>/out/<project> (gitignore แล้ว) ไม่ผูกกับโฟลเดอร์ที่รันคำสั่ง
DEFAULT_OUT = os.path.join(OUT_ROOT, f"{PROJECT}_{ORGANIZATION_ID}" if ACCOUNT_MODE and ORGANIZATION_ID else PROJECT)

BASE_COLUMNS = {
    "Entry ID": "entryId", "Record ID": "recordId", "Entry Date": "entryDate",
    "Created At": "createdAt", "Updated At": "updatedAt", "User": "user",
    "Outlet Name": "outletName", "Outlet Code": "outletCode",
    "Outlet Store Code": "outletStoreCode", "Outlet Type": "outletType",
    "Outlet Latitude": "outletLat", "Outlet Longitude": "outletLng",
    "Checkin (1st)": "checkin1", "Checkout (1st)": "checkout1",
    "Checkin (2nd)": "checkin2", "Checkout (2nd)": "checkout2",
}

CSV_COLUMNS = [
    "entryDate", "createdAt", "updatedAt", "jobId", "entryId", "recordId", "user",
    "outletName", "outletCode", "outletStoreCode", "outletType", "outletLat", "outletLng",
    "checkin1", "checkout1", "checkin2", "checkout2",
    "province", "teamRegion", "chainAccount",
    "chain", "sku", "productName", "statusId", "statusLabel", "statusGroup", "shelfPhotos",
]


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ============================== CREDENTIALS ==============================

# บัญชีของแต่ละ project เก็บแยกกันใน Credential Manager: service "cloudviu:<project>"
#   user "mobile" = เบอร์ที่ใช้ login, user <เบอร์> = รหัสผ่าน
#
# 1 บัญชี CloudViu เข้าได้หลาย project - ถ้าเป็นกรณีนั้น ใส่ "credentials_from": "<project ที่ตั้งบัญชีไว้แล้ว>"
# ใน project.json เพื่อใช้บัญชีร่วมกัน ไม่ต้องตั้งรหัสซ้ำทุก project
# ต้องประกาศชัดเจนรายตัวเท่านั้น (ไม่ใช่หยิบอัตโนมัติ) และ setup/forget ยังทำงานกับบัญชีต้นทางเสมอ
PROFILE = (CFG.get("credentials_from") or PROJECT).strip()
PROFILE_SERVICE = f"cloudviu:{PROFILE}"
SHARED_ACCOUNT = PROFILE != PROJECT


def _keyring():
    try:
        import keyring  # pip install keyring
        return keyring
    except ImportError:
        return None


# env var ของ project: CLOUDVIU_<PROJECT>_MOBILE / CLOUDVIU_<PROJECT>_PASS
# env var ชื่อกลาง (CLOUDVIU_MOBILE/PASS) และ keyring แบบเก่า ใช้ได้เฉพาะ project ที่ config ตั้ง
# "legacy_credentials": true - กัน project ใหม่หยิบบัญชีของ project เดิมที่ตั้งไว้ทั้งเครื่องไปใช้
ENV_PREFIX = "CLOUDVIU_" + re.sub(r"\W", "_", PROFILE).upper() + "_"
LEGACY_CREDENTIALS = bool(CFG.get("legacy_credentials"))


def _env(name):
    v = (os.environ.get(ENV_PREFIX + name) or "").strip()
    if not v and LEGACY_CREDENTIALS:
        v = (os.environ.get("CLOUDVIU_" + name) or "").strip()
    return v


def credentials():
    """ลำดับที่หา: env var ของ project -> Credential Manager ของ project (cloudviu:<project>) -> แบบเก่า (เฉพาะ legacy)"""
    kr = _keyring()
    mobile = _env("MOBILE")
    if not mobile and kr:
        mobile = (kr.get_password(PROFILE_SERVICE, "mobile") or "").strip()
    if not mobile:
        via = f" (project '{PROJECT}' ใช้บัญชีร่วมกับ '{PROFILE}' ตาม credentials_from)" if SHARED_ACCOUNT else ""
        die(f"ยังไม่ได้ตั้งบัญชี CloudViu ของ project '{PROFILE}'{via} - ให้ผู้ใช้รัน "
            f"`python cloudviu.py --project {PROFILE} setup` เองในเทอร์มินัล")
    pw = _env("PASS")
    if not pw and kr:
        pw = (kr.get_password(PROFILE_SERVICE, mobile)
              or (kr.get_password(KEYRING_SERVICE, mobile) if LEGACY_CREDENTIALS else None) or "").strip()
    if not pw:
        die(f"ไม่พบรหัสผ่าน - ให้ผู้ใช้รัน `python cloudviu.py --project {PROFILE} setup` เองในเทอร์มินัล")
    return mobile, pw


def _block_if_shared(verb):
    """กันไม่ให้ project ที่ยืมบัญชีมา ไปแก้/ลบบัญชีของ project ต้นทางโดยไม่ตั้งใจ"""
    if SHARED_ACCOUNT:
        die(f"project '{PROJECT}' ใช้บัญชีร่วมกับ '{PROFILE}' (credentials_from) จึง{verb}จากตรงนี้ไม่ได้\n"
            f"ถ้าตั้งใจจะแก้บัญชีนั้นจริง ให้รันกับ project ต้นทาง: --project {PROFILE}\n"
            f"ถ้าอยากให้ '{PROJECT}' มีบัญชีของตัวเอง ให้เอา credentials_from ออกจาก project.json ก่อน")


def cmd_forget(_args):
    """ลบบัญชีของ project นี้ออกจาก Credential Manager (เช่น คนดูแลเปลี่ยน)"""
    _block_if_shared("ลบบัญชี")
    kr = _keyring()
    if not kr:
        die("ไม่มี keyring ในเครื่อง - ไม่มีอะไรให้ลบ")
    mobile = kr.get_password(PROFILE_SERVICE, "mobile")
    for user in filter(None, ("mobile", mobile)):
        try:
            kr.delete_password(PROFILE_SERVICE, user)
        except Exception:
            pass
    print(f"ลบบัญชีของ project '{PROFILE}' ออกจาก Credential Manager แล้ว")


def cmd_set_password(args):
    """ผู้ใช้รันเองในเทอร์มินัล (`setup`): ถามเบอร์ + รหัส, ลอง login, แล้วเก็บทั้งคู่ใน Windows Credential Manager"""
    _block_if_shared("ตั้งบัญชี")
    kr = _keyring()
    if not kr:
        die("ต้องติดตั้ง keyring ก่อน: python -m pip install keyring")
    saved = (kr.get_password(PROFILE_SERVICE, "mobile") or _env("MOBILE") or "").strip()
    hint = f" [Enter = ใช้ {mask(saved)}]" if saved else ""
    mobile = input(f"เบอร์มือถือที่ใช้ login CloudViu ({PROFILE}){hint}: ").strip() or saved
    if not re.fullmatch(r"0\d{8,9}", mobile):
        die("เบอร์ไม่ถูกรูปแบบ - ใส่ตัวเลข 9-10 หลักขึ้นต้นด้วย 0 ไม่มีขีดหรือช่องว่าง")
    keyring = kr
    # รหัสขึ้นบนจอ (พิมพ์ครั้งเดียว ไม่ต้องยืนยัน) - ลอง login ก่อนบันทึกจึงรู้ทันทีถ้าพิมพ์ผิด
    print("!! รหัสจะขึ้นบนจอ - ดูว่าไม่มีใครเห็นจอ และพิมพ์ cls ล้างจอหลังเสร็จ")
    pw = input("รหัสผ่าน CloudViu: ").strip()
    if not pw:
        die("ไม่ได้ใส่รหัสผ่าน")

    # ลอง login ก่อนบันทึก จะได้รู้ทันทีว่ารหัสถูกไหม
    print("กำลังลอง login ...")
    login(mobile, pw, select_required=False)
    keyring.set_password(PROFILE_SERVICE, "mobile", mobile)
    keyring.set_password(PROFILE_SERVICE, mobile, pw)
    print(f"บันทึกแล้วใน Windows Credential Manager (ชื่อ {PROFILE_SERVICE}, เบอร์ {mask(mobile)}) - ใช้งานได้ทันที")
    env_mobile = _env("MOBILE")
    if env_mobile and env_mobile != mobile:
        print("!! เครื่องนี้ยังมี env var ของเบอร์อื่นสำหรับ project นี้ ซึ่งจะถูกใช้ก่อน - ลบ env var นั้นออก")
    print("พิมพ์ cls เพื่อล้างรหัสออกจากจอ")


def mask(s):
    return re.sub(r".(?=.{4})", "x", s)


# ============================== HTTP ==============================

class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [("User-Agent", f"cloudviu-connector/{CONNECTOR_VERSION}"), ("Accept-Encoding", "gzip")]

    def _open(self, req):
        try:
            resp = self.opener.open(req, timeout=120)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            return e.code, e.headers, body.encode()
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return resp.status, resp.headers, raw

    def get(self, url):
        return self._open(urllib.request.Request(url))

    def post(self, url, fields):
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        return self._open(req)


def extract_csrf(html):
    for pat in (r'name=["\']csrf["\'][^>]*value=["\']([^"\']+)["\']',
                r'value=["\']([^"\']+)["\'][^>]*name=["\']csrf["\']',
                r'["\']csrf["\']\s*:\s*["\']([0-9a-fA-F-]{36})["\']'):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def login(mobile=None, pw=None, select_required=True):
    """csrf -> POST login -> เลือกโปรเจกต์ (ขาดขั้นสุดท้ายจะได้ข้อมูลว่างหรือ HTTP 500)"""
    if not (mobile and pw):
        mobile, pw = credentials()
    s = Session()
    _, _, html = s.get(BASE + "/login")
    csrf = extract_csrf(html.decode("utf-8", "replace"))
    if not csrf:
        die("หา csrf ในหน้า login ไม่เจอ - หน้า login ของ CloudViu อาจเปลี่ยน")

    code, _, body = s.post(BASE + "/login.data", {"csrf": csrf, "mobile": mobile, "password": pw})
    if code == 401:
        die("login ไม่ผ่าน (HTTP 401) - เบอร์หรือรหัสผ่านไม่ถูกต้อง")
    try:
        redirect = str((decode_turbo(body) or {}).get("redirect") or "")
    except ValueError:
        redirect = ""
    if code >= 400 or not redirect or redirect.split("?")[0].rstrip("/") == "/login":
        die(f"login ไม่ผ่าน (HTTP {code}, redirect '{redirect}') - รูปแบบ login ของ CloudViu อาจเปลี่ยนอีก")
    print(f"login ผ่าน ({mask(mobile)})")

    if not ORGANIZATION_ID:
        if select_required:
            if ACCOUNT_MODE:
                die("ต้องเลือกโปรเจกต์: ใส่ --org <เลขโปรเจกต์> (ดูเลขจากคำสั่ง projects)")
            die(f"ยังไม่มี organization_id ของ {DISPLAY_NAME} ใน {CFG_PATH} - ไม่เลือกโปรเจกต์จะได้ข้อมูลว่าง "
                "(หาเลขจาก DevTools > Network > request current-organization)")
        if not ACCOUNT_MODE:   # แบบ --account เลือกโปรเจกต์ด้วย --org ตอนดึงข้อมูล
            print(f"!! ยังไม่มี organization_id - ข้ามการเลือกโปรเจกต์ (ใส่เลขใน {CFG_PATH} ก่อนดึงข้อมูล)")
        return s
    select_org(s, ORGANIZATION_ID)
    print(f"เลือกโปรเจกต์ {DISPLAY_NAME} (organizationId={ORGANIZATION_ID}) แล้ว")
    return s


def select_org(s, org_id):
    """POST /current-organization -> ต้องได้ JSON organizationId ตรงกับที่ขอ (ไม่งั้นข้อมูลจะเป็นของโปรเจกต์อื่น)"""
    code, _, body = s.post(BASE + "/current-organization", {"organizationId": str(org_id)})
    try:
        got = json.loads(body.decode("utf-8", "replace")).get("organizationId")
    except (json.JSONDecodeError, AttributeError):
        got = None
    if code >= 400 or str(got) != str(org_id):
        die(f"เลือกโปรเจกต์ {org_id} ไม่สำเร็จ (HTTP {code}) - เลขผิด บัญชีไม่มีสิทธิ์ หรือ session หลุด")


def decode_turbo(raw):
    """
    ถอด turbo-stream (รูปแบบข้อมูลของ React Router v7) -> dict/list ธรรมดา
    บรรทัดแรก = JSON array ของค่าทั้งหมด / object = {"_<index ชื่อ key>": <index ค่า>} / array = list ของ index
    ["D", ms] = วันที่ (คืน ISO string) / [tag, index] อื่น เช่น SingleFetchRedirect = ค่าข้างใน / เลขติดลบ = ค่าคงที่
    """
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        values = json.loads(text.split("\n", 1)[0])
    except json.JSONDecodeError as e:
        raise ValueError(f"ไม่ใช่ turbo-stream: {text[:80]!r}") from e
    # ค่าคงที่ของ turbo-stream (เรียงตามตัวอักษร): HOLE, NAN, NEGATIVE_INFINITY, NEGATIVE_ZERO, NULL, POSITIVE_INFINITY, UNDEFINED
    const = {-1: None, -2: float("nan"), -3: float("-inf"), -4: -0.0, -5: None, -6: float("inf"), -7: None}
    cache = {}

    def h(i):
        if i < 0:
            return const.get(i)
        if i in cache:
            return cache[i]
        v = values[i]
        if not isinstance(v, (list, dict)):
            cache[i] = v
            return v
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                if v[0] == "D":
                    cache[i] = dt.datetime.fromtimestamp(v[1] / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z")
                else:
                    cache[i] = h(v[1]) if len(v) == 2 else [h(x) for x in v[1:]]
                return cache[i]
            out = []
            cache[i] = out
            out.extend(h(x) for x in v)
            return out
        out = {}
        cache[i] = out
        for k, x in v.items():
            out[h(int(k[1:]))] = h(x)
        return out

    return h(0)


def download_export(s, job_id, start, end):
    qs = urllib.parse.urlencode({"jobIds": job_id, "projectId": PROJECT_ID,
                                 "startAt": start, "endAt": end, "isExcel": "true"})
    code, _, body = s.get(BASE + "/job-record-download?" + qs)
    if code != 200:
        die(f"ดาวน์โหลด Excel job {job_id} {start}..{end} ไม่สำเร็จ HTTP {code}: "
            f"{body[:200].decode('utf-8', 'replace')}")
    if body[:2] != b"PK":   # xlsx = zip ถ้าไม่ใช่แปลว่าได้หน้า HTML (session หลุด)
        die("ไฟล์ที่ได้ไม่ใช่ xlsx - session อาจหลุด หรือ URL export เปลี่ยน")
    return body


# ============================== XLSX ==============================

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def col_index(ref):
    m = re.match(r"([A-Z]+)(\d+)$", ref or "")
    if not m:
        return None, None
    c = 0
    for ch in m.group(1):
        c = c * 26 + ord(ch) - 64
    return c, int(m.group(2))


def read_job_sheet(xlsx_bytes):
    """คืน ({row: {col: text}}, job id จากชื่อ sheet หรือ None) ของ sheet "(ID-xx) Job ..." (ข้าม Product/Outlet-Master)"""
    grid, name = read_sheet(xlsx_bytes, lambda n: re.match(r"\(ID-\d+\)", n))
    m = re.match(r"\(ID-(\d+)\)", name)
    return grid, (int(m.group(1)) if m else None)


def read_sheet(xlsx_bytes, want):
    """คืน ({row: {col: text}}, ชื่อ sheet) ของ sheet แรกที่ want(ชื่อ) เป็นจริง (ไม่เจอใช้ sheet แรก)"""
    z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    names = set(z.namelist())

    strings = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si"):
            strings.append("".join(t.text or "" for t in si.iter(NS + "t")))

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = list(wb.iter(NS + "sheet"))
    if not sheets:
        die("ไฟล์ Excel ไม่มี sheet")
    pick = next((sh for sh in sheets if want(sh.get("name", ""))), sheets[0])
    rid = pick.get(RNS + "id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = next(r.get("Target") for r in rels if r.get("Id") == rid)
    path = "xl/" + re.sub(r"^/?(xl/)?", "", target)

    grid = {}
    for c in ET.fromstring(z.read(path)).iter(NS + "c"):
        col, row = col_index(c.get("r"))
        if col is None:
            continue
        t = c.get("t")
        if t == "inlineStr":
            val = "".join(x.text or "" for x in c.iter(NS + "t"))
        else:
            v = c.find(NS + "v")
            if v is None:
                continue
            val = strings[int(v.text)] if t == "s" else (v.text or "")
        grid.setdefault(row, {})[col] = val
    return grid, pick.get("name", "")


def parse_osa(grid, job_id, include_names):
    """แตก sheet เป็นแถว record x SKU เฉพาะคอลัมน์ของ campaign OSA / OOS"""
    header_row = next((r for r in sorted(grid) if r <= 10 and "Record ID" in grid[r].values()), None)
    if header_row is None:
        return [], 0
    headers = grid[header_row]
    top = {c: v for c, v in grid.get(1, {}).items() if str(v).strip()}
    starts = sorted(top)
    start = next((c for c in starts if str(top[c]).strip() == CAMPAIGN), None)
    if start is None:
        return [], 0
    nxt = [c for c in starts if c > start]
    end = nxt[0] - 1 if nxt else 10 ** 6
    base_col = {c: BASE_COLUMNS[str(h).strip()] for c, h in headers.items() if str(h).strip() in BASE_COLUMNS}

    data_rows = [r for r in sorted(grid) if r > header_row]
    out = []
    for r in data_rows:
        cells = grid[r]
        base = {f: cells.get(c, "") for c, f in base_col.items()}
        photos, answers = [], []
        for c in sorted(cells):
            if not (start <= c <= end):
                continue
            v = str(cells[c]).strip()
            if v.startswith("{"):
                answers.append((str(headers.get(c, "")).strip(), v))
            elif v.startswith("http"):
                photos.append(v)
        if not answers:
            continue   # แถวของฟอร์มอื่น
        for chain, text in answers:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  !! record {base.get('recordId')} อ่าน JSON ไม่ได้: {line[:80]}")
                    continue
                pm = re.match(r"\(([^)]+)\)\s*(.*)$", str(o.get("product", "")))
                sid = (o.get("counter") or {}).get("value")
                label, group = STATUS.get(sid, ("", ""))
                out.append(clean_row({**base,
                                      "jobId": job_id,
                                      "chain": chain,
                                      "sku": pm.group(1) if pm else o.get("product", ""),
                                      "productName": pm.group(2).strip() if pm else "",
                                      "statusId": sid,
                                      "statusLabel": label,
                                      "statusGroup": group,
                                      "shelfPhotos": " ".join(photos)}, include_names))
    return out, len(data_rows)


def thai_dt(s):
    """'01/10/2026 11:41:47' (เวลาไทย) -> '2026-10-01 11:41:47' (คงเป็นเวลาไทย เรียงลำดับได้)"""
    s = str(s or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        d, mo, y, h, mi, se = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{mi}:{se or '00'}"
    if re.match(r"^\d+(\.\d+)?$", s) and float(s) > 40000:   # serial date ของ Excel
        return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(s))).strftime("%Y-%m-%d %H:%M:%S")
    return s


def thai_date(s):
    """'01/10/2026' หรือมีเวลาต่อท้าย -> '2026-10-01' (ไม่งั้นเรียงข้ามเดือนผิด)"""
    s = str(s or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})(\s|$)", s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    iso = thai_dt(s)   # serial date / ISO มีเวลา
    return iso[:10] if re.match(r"\d{4}-\d{2}-\d{2}", iso) else s


def clean_row(r, include_names):
    r["entryDate"] = thai_date(r.get("entryDate"))
    for k in ("createdAt", "updatedAt", "checkin1", "checkout1", "checkin2", "checkout2"):
        r[k] = thai_dt(r.get(k))
    for k in ("outletCode", "outletStoreCode"):
        if r.get(k) == "{}":
            r[k] = ""
    if not include_names:
        r["user"] = ""
    return r


# ============================== COMMANDS ==============================

def chunks(start, end, days):
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while s <= e:
        stop = min(s + dt.timedelta(days=days - 1), e)
        yield s.isoformat(), stop.isoformat()
        s = stop + dt.timedelta(days=1)


def cmd_check(_args):
    login()
    print("OK - พร้อมใช้งาน")


# คอลัมน์ของ sheet Outlet-Master ที่ต่อเข้า CSV (ไม่ใช่ข้อมูลส่วนบุคคล - ข้ามชื่อ/เบอร์ผู้ติดต่อ)
OUTLET_FIELDS = {"Province": "province", "Region": "teamRegion", "Chain Account": "chainAccount"}


def outlet_lookup(xlsx_bytes):
    """{ชื่อร้าน: {province, teamRegion, chainAccount}} จาก Outlet-Master
    CSV ไม่มี outlet id จึงจับคู่ด้วยชื่อร้าน (Name TH) - ชื่อซ้ำที่ค่าไม่ตรงกันจะเว้นว่าง"""
    grid, name = read_sheet(xlsx_bytes, lambda n: "outlet" in n.lower())
    if "outlet" not in name.lower() or not grid:
        return {}
    hdr = {str(v).strip(): c for c, v in grid[min(grid)].items()}
    if "Name TH" not in hdr:
        return {}
    seen = {}
    for r in sorted(grid)[1:]:
        key = str(grid[r].get(hdr["Name TH"], "")).strip()
        vals = {f: str(grid[r].get(hdr[h], "")).strip() for h, f in OUTLET_FIELDS.items() if h in hdr}
        seen.setdefault(key, []).append(vals)
    out = {}
    for key, lst in seen.items():
        out[key] = {f: (lst[0][f] if all(v.get(f) == lst[0].get(f) for v in lst) else "") for f in lst[0]}
    return out


def enrich(rows, lookup):
    for r in rows:
        r.update(lookup.get(str(r.get("outletName", "")).strip(), {}))
    return rows


def cmd_osa(args):
    rows = []
    if args.xlsx:
        with open(args.xlsx, "rb") as f:
            body = f.read()
        grid, sheet_job = read_job_sheet(body)
        # job ของไฟล์ดูจากชื่อ sheet "(ID-41) ..." - ถ้าไม่มีค่อยใช้ --jobs
        job = sheet_job or args.jobs[0]
        if sheet_job and args.jobs != DEFAULT_JOBS and sheet_job not in args.jobs:
            print(f"  !! --jobs {args.jobs} ไม่ตรงกับชื่อ sheet (ID-{sheet_job}) - ใช้ {sheet_job}")
        if not sheet_job:
            print(f"  !! ชื่อ sheet ไม่มี (ID-xx) - ใส่ jobId = {job} ตาม --jobs")
        got, total = parse_osa(grid, job, args.include_names)
        enrich(got, outlet_lookup(body))
        print(f"ไฟล์ {os.path.basename(args.xlsx)} (job {job}) : {total} records ในไฟล์, OSA {len({r['recordId'] for r in got})} records")
        rows.extend(got)
        label = os.path.splitext(os.path.basename(args.xlsx))[0]
    else:
        if not (args.start and args.end):
            die("ต้องระบุ --from และ --to (YYYY-MM-DD) หรือใช้ --xlsx")
        span = (dt.date.fromisoformat(args.end) - dt.date.fromisoformat(args.start)).days + 1
        if span < 1:
            die("--from ต้องไม่เกิน --to")
        if span > MAX_RANGE_DAYS:
            die(f"ช่วงวันที่ {span} วัน เกินเพดาน {MAX_RANGE_DAYS} วัน - แบ่งดึงเป็นหลายครั้ง")
        s = login()
        for job in args.jobs:
            for a, b in chunks(args.start, args.end, CHUNK_DAYS):
                body = download_export(s, job, a, b)
                grid, _ = read_job_sheet(body)
                got, total = parse_osa(grid, job, args.include_names)
                enrich(got, outlet_lookup(body))
                print(f"  job {job} {a}..{b} : {total} records ในไฟล์, OSA {len({r['recordId'] for r in got})} records")
                # ~180 records/วัน (ต.ค. 2026) ก้อน 7 วันเกิน 1000 เป็นปกติ - เตือนเฉพาะเลขกลมที่ดูเหมือนเพดาน
                if total >= 100 and total % 100 == 0:
                    print(f"  !! {total} records พอดี - อาจชนเพดานของ export ลองช่วงสั้นลง")
                rows.extend(got)
                time.sleep(PAUSE_SEC)
        label = f"{PROJECT}_osa_{args.start}_{args.end}"

    # dedupe record x SKU (ก้อนวันที่ติดกันอาจซ้ำ)
    uniq = {}
    for r in rows:
        uniq[(r["recordId"], r["sku"])] = r
    rows = list(uniq.values())

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, label + ("_names" if args.include_names else "") + ".csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig ให้ Excel อ่านไทยได้
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    summarize(rows)
    print(f"\nเขียนไฟล์: {path}")
    if args.include_names:
        print("!! ไฟล์นี้มีชื่อพนักงาน - เก็บในที่ปลอดภัย อย่าส่งต่อโดยไม่ได้รับอนุมัติ")


AREA_HEADER = re.compile(r"region|part|province|state|zone|area|district|ภาค|จังหวัด|เขต|อำเภอ|type|channel|chain|account|format", re.I)
PII_HEADER = re.compile(r"contact|name|phone|mobile|tel|email|ผู้ติดต่อ|ชื่อ|เบอร์|โทร|อีเมล|address|ที่อยู่", re.I)


def cmd_outlets(args):
    """ดูโครงสร้าง sheet Outlet-Master: ชื่อคอลัมน์ + จำนวนค่า แสดงค่าจริงเฉพาะคอลัมน์พื้นที่/ประเภทร้าน (ไม่ใช่ข้อมูลส่วนบุคคล)"""
    if args.xlsx:
        with open(args.xlsx, "rb") as f:
            body = f.read()
    else:
        if not (args.start and args.end):
            die("ต้องระบุ --from และ --to (YYYY-MM-DD) หรือใช้ --xlsx")
        body = download_export(login(), args.jobs[0], args.start, args.end)
    grid, name = read_sheet(body, lambda n: "outlet" in n.lower())
    if "outlet" not in name.lower():
        die(f"ไม่พบ sheet Outlet-Master (เจอ {name})")
    header_row = min(grid)
    headers = {c: str(v).strip() for c, v in grid[header_row].items()}
    data = [grid[r] for r in sorted(grid) if r > header_row]
    print(f"sheet [{name}] {len(data)} ร้าน, {len(headers)} คอลัมน์\n")
    for c in sorted(headers):
        h = headers[c]
        vals = [str(row.get(c, "")).strip() for row in data]
        filled = [v for v in vals if v]
        line = f"  {h:30.30} มีค่า {len(filled):5}/{len(data)}  ต่างกัน {len(set(filled)):5}"
        if AREA_HEADER.search(h) and not PII_HEADER.search(h.replace("Outlet Name", "")) and len(set(filled)) <= 80:
            top = sorted(((filled.count(v), v) for v in set(filled)), reverse=True)[:15]
            line += "  ค่า: " + ", ".join(f"{v} ({n})" for n, v in top)
        print(line)


def summarize(rows):
    """สรุปแบบรวม (ไม่มีชื่อพนักงาน) ให้ Claude/ผู้ใช้เห็นภาพทันที"""
    recs = {r["recordId"] for r in rows}
    print(f"\nสรุป: {len(recs)} records, {len(rows)} แถว (record x SKU)")
    if not rows:
        return
    groups = {}
    for r in rows:
        groups[r["statusGroup"] or "?"] = groups.get(r["statusGroup"] or "?", 0) + 1
    print("  ตามสถานะ: " + ", ".join(f"{k} {v}" for k, v in sorted(groups.items())))
    noprov = len({r["outletName"] for r in rows if not r.get("province")})
    if noprov:
        print(f"  !! หา province ไม่เจอ {noprov} ร้าน (ชื่อร้านไม่ตรง Outlet-Master หรือชื่อซ้ำ)")
    unknown = sorted({str(r["statusId"]) for r in rows if not r["statusGroup"]})
    if unknown:
        print(f"  !! รหัสสถานะที่ไม่รู้จัก: {', '.join(unknown)} - ฟอร์มอาจถูกแก้ ให้อัปเดต osa.status ใน project.json")
    missing = len({r["recordId"] for r in rows if not r["createdAt"]})
    if missing:
        print(f"  !! ไม่มี createdAt {missing} records")
    dates = sorted({r["entryDate"] for r in rows if r["entryDate"]})
    if dates:
        print(f"  entryDate {dates[0]} ถึง {dates[-1]}")


# ============================== ATTENDANCE ==============================
#
# GET /attendances.data?page=N&startAt=&endAt=  -> route routes/_app.attendances._index
# ยังไม่เคยเห็น response จริง - โค้ดส่วนนี้จึงไม่ fix ชื่อฟิลด์:
#   - หา array ของ record เอง (array ของ object ที่ยาวที่สุดใน response)
#   - แบ่งหน้า: วนจนได้หน้าว่าง / ครบ total / หน้าสั้นกว่าหน้าแรก
#   - แตก object ซ้อนเป็นคอลัมน์ "a.b" แล้วตัดฟิลด์ข้อมูลส่วนบุคคลตามชื่อทิ้ง
# เมื่อได้ตัวอย่างจริงแล้ว ควรเปลี่ยนเป็นรายการคอลัมน์ที่ระบุชัด (allowlist)

ROUTE_ATTENDANCE = "routes/_app.attendances._index"
MAX_PAGES = 500

# ฟิลด์ที่ตัดทิ้งเป็นค่าเริ่มต้น (เทียบกับชื่อคีย์ส่วนสุดท้าย ไม่สนตัวพิมพ์)
PII_NAME_KEYS = re.compile(r"^(first_?name|last_?name|display_?name|full_?name|nick_?name|"
                           r"username|mobile|phone(_?number)?|tel|email|id_?card|citizen_?id|"
                           r"address|birth_?date|dob)$", re.I)
PHOTO_KEYS = re.compile(r"(image|photo|selfie|picture|avatar|face|uri|url)s?$", re.I)
PAGINATION_KEYS = ("total", "totalCount", "count", "totalPages", "pageCount", "lastPage", "hasMore", "limit", "perPage")


def fetch_attendance_page(s, page, start, end):
    return fetch_json(s, "/attendances", ROUTE_ATTENDANCE, {"page": page, "startAt": start, "endAt": end})


def find_record_list(obj):
    """
    หา array ของ record คืน (path, list)
    Remix loader มักแนบรายการ dropdown มาด้วย (เช่น outletOptions หลักพันรายการ) ซึ่งยาวกว่า
    record ในหน้านั้น - จึงข้าม path ที่มีคำว่า option และให้ path ที่มี "attend" มาก่อน
    """
    cands = []

    def walk(o, path):
        if isinstance(o, list):
            if o and all(isinstance(x, dict) for x in o):
                cands.append((path or "(root)", o))
            return
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k)

    walk(obj, "")
    cands = [c for c in cands if "option" not in c[0].lower()]
    if not cands:
        return None, []
    attend = [c for c in cands if "attend" in c[0].lower()]
    return max(attend or cands, key=lambda c: len(c[1]))


def get_path(obj, path):
    if path == "(root)":
        return obj
    for k in path.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj if isinstance(obj, list) else []


def find_pagination(obj, depth=0, out=None):
    out = {} if out is None else out
    if isinstance(obj, dict) and depth < 3:
        for k, v in obj.items():
            if k in PAGINATION_KEYS and isinstance(v, (int, bool)):
                out[k] = v
            elif isinstance(v, dict):
                find_pagination(v, depth + 1, out)
    return out


def flatten_obj(obj, prefix="", depth=0):
    out = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and depth < 2:
            out.update(flatten_obj(v, key, depth + 1))
        elif isinstance(v, (dict, list)):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = v
    return out


def is_pii_key(key, include_names, include_photos):
    last = key.split(".")[-1]
    if not include_names and PII_NAME_KEYS.match(last):
        return True
    if not include_names and last.lower() == "name" and re.search(r"(^|\.)(user|staff|employee|member|actor)\.", key, re.I):
        return True
    if not include_names and re.search(r"(supervisor|manager|leader|user|staff|employee)_?name$", last, re.I):
        return True   # ชื่อหัวหน้า/พนักงานในฟิลด์รวม เช่น supervisorName
    if not include_photos and PHOTO_KEYS.search(last):
        return True
    if not include_photos and re.search(r"(location|coordinates|latitude|longitude|lat|lng)$", key, re.I)             and re.search(r"(checked|check)", key, re.I):
        return True   # พิกัด GPS ตอนเช็คอิน/เอาท์ = ตำแหน่งของคน (ข้อมูลส่วนบุคคล)
    return False


def describe(obj, path="", depth=0, lines=None):
    """โครงสร้างแบบไม่มีค่าจริง - ปลอดภัยที่จะแสดงในแชท"""
    lines = [] if lines is None else lines
    label = path or "(root)"
    if isinstance(obj, dict):
        lines.append(f"{'  ' * depth}{label}: object")
        for k, v in obj.items():
            describe(v, f"{path}.{k}" if path else k, depth + 1, lines)
    elif isinstance(obj, list):
        lines.append(f"{'  ' * depth}{label}: array({len(obj)})")
        if obj and depth < 4:
            describe(obj[0], label + "[0]", depth + 1, lines)
    else:
        lines.append(f"{'  ' * depth}{label}: {type(obj).__name__}")
    return lines


def cmd_attendance(args):
    span = (dt.date.fromisoformat(args.end) - dt.date.fromisoformat(args.start)).days + 1
    if span < 1:
        die("--from ต้องไม่เกิน --to")
    if span > MAX_RANGE_DAYS:
        die(f"ช่วงวันที่ {span} วัน เกินเพดาน {MAX_RANGE_DAYS} วัน - แบ่งดึงเป็นหลายครั้ง")
    s = login()

    first = fetch_attendance_page(s, 1, args.start, args.end)
    path, recs = find_record_list(first)
    if args.describe:
        print("โครงสร้าง response หน้า 1 (ไม่มีค่าจริง):")
        print("\n".join(describe(first)))
        print(f"\narray ของ record: {path} ({len(recs)} รายการ)")
        print(f"ฟิลด์แบ่งหน้าที่เจอ: {find_pagination(first) or '-'}")
        return
    if not path:
        die("หน้า 1 ไม่มี array ของ record - ช่วงนี้อาจไม่มีข้อมูล หรือโครงสร้างเปลี่ยน (ลอง --describe)")

    pag = find_pagination(first)
    total = pag.get("total") or pag.get("totalCount")
    pages = pag.get("totalPages") or pag.get("pageCount") or pag.get("lastPage")
    page_size = len(recs)
    print(f"หน้า 1 : {len(recs)} รายการ (array = {path}, แบ่งหน้า = {pag or 'ไม่บอก'})")

    rows, seen, page = [], set(), 1
    while True:
        new = 0
        for r in recs:
            key = r.get("id", json.dumps(r, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
            new += 1
        if not recs or new == 0:
            break                                   # หน้าว่าง / หน้าซ้ำ (server ไม่สนเลขหน้า)
        if pages and page >= pages:
            break
        if total and len(rows) >= total:
            break
        if not pages and not total and len(recs) < page_size:
            break                                   # หน้าสั้นกว่าหน้าแรก = หน้าสุดท้าย
        if page >= MAX_PAGES:
            print(f"  !! หยุดที่ {MAX_PAGES} หน้า - ข้อมูลอาจไม่ครบ ลดช่วงวันที่")
            break
        page += 1
        time.sleep(PAUSE_SEC)
        recs = get_path(fetch_attendance_page(s, page, args.start, args.end), path)
        print(f"หน้า {page} : {len(recs)} รายการ")
    if total and len(rows) < total:
        print(f"  !! ได้ {len(rows)} จาก total {total} - ข้อมูลอาจไม่ครบ")

    flat = [flatten_obj(r) for r in rows]
    cols, dropped = [], set()
    for r in flat:
        for k in r:
            if k in cols or k in dropped:
                continue
            (dropped.add(k) if is_pii_key(k, args.include_names, args.include_photos) else cols.append(k))

    os.makedirs(args.out, exist_ok=True)
    suffix = ("_names" if args.include_names else "") + ("_photos" if args.include_photos else "")
    path_out = os.path.join(args.out, f"{PROJECT}_attendance_{args.start}_{args.end}{suffix}.csv")
    with open(path_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    print(f"\nสรุป: {len(rows)} รายการ, {len(cols)} คอลัมน์")
    print("คอลัมน์: " + ", ".join(cols))
    if dropped:
        print("ตัดทิ้ง (ข้อมูลส่วนบุคคล/รูป): " + ", ".join(sorted(dropped)))
    print(f"\nเขียนไฟล์: {path_out}")
    if not getattr(args, "no_check", False):
        check_attendance(flat, dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end))
    if args.include_names or args.include_photos:
        print("!! ไฟล์นี้มีข้อมูลส่วนบุคคล - เก็บในที่ปลอดภัย อย่าส่งต่อโดยไม่ได้รับอนุมัติ")


# ============================== JOB RECORDS (แหล่งหลักของคำตอบฟอร์ม) ==============================
#
# GET /job-records.data?startAt&endAt[&jobId][&campaignName] -> route routes/_app.job-records
#   {data: {records, itemMap, products}, selectJobOptions, campaignOptions, params}
# - ข้อมูลปัจจุบันของคำตอบ (SKU ที่ถูกลบออกจากคำตอบจะไม่อยู่) - ต่างจาก Export/BigQuery ที่มีแถวค้าง
# - startAt/endAt กรองตามเวลาส่ง ไม่ใช่ record.date (วันของงาน) -> ดึงเผื่อท้าย --pad วัน แล้วกรอง record.date เอง
# - ไม่มีการแบ่งหน้า -> ขอทีละวัน / jobId ใช้ได้ (jobIds ไม่มีผล) / ไม่ใส่ campaignName = ทุก campaign
# - record มีชื่อพนักงาน (PII) -> ค่าเริ่มต้นเก็บแค่ userId

ROUTE_RECORDS = "routes/_app.job-records"
RECORD_COLUMNS = ["date", "recordId", "entryId", "jobName", "campaignName", "formId", "formName",
                  "outletId", "outletName", "userId", "staff", "questionId", "question", "type", "analyticKey",
                  "sku", "product", "property", "value"]


def _flat(v, prefix=""):
    """dict ซ้อน -> [(property, ค่า)] เช่น {"value": {"price": 1, "quantity": 2}} -> value.price, value.quantity"""
    if isinstance(v, dict):
        out = []
        for k, x in v.items():
            out += _flat(x, f"{prefix}.{k}" if prefix else str(k))
        return out
    if isinstance(v, list):
        return [(prefix, ", ".join(str(x) for x in v if not isinstance(x, (dict, list))))]
    return [(prefix, v)]


def record_rows(rec, itemmap, products, include_names, include_photos):
    """1 record -> แถวยาว 1 แถวต่อ (คำถาม x SKU x property)"""
    base = {
        "date": str(rec.get("date") or "")[:10], "recordId": rec.get("id"), "entryId": rec.get("entryId"),
        "jobName": rec.get("jobName"), "campaignName": rec.get("campaignName"), "formId": rec.get("formId"),
        "formName": rec.get("formName"), "outletId": rec.get("outletId"), "outletName": rec.get("outletName"),
        "userId": rec.get("userId"),
        "staff": (rec.get("displayName") or f"{rec.get('firstName') or ''} {rec.get('lastName') or ''}".strip())
                 if include_names else "",
    }
    rows = []
    for a in rec.get("data") or []:
        if "value" not in a:
            continue
        q = (itemmap.get(f"{rec.get('formId')}/{a.get('id')}") or {}).get("data") or {}
        qt, v = q.get("type") or "", a["value"]
        head = dict(base, questionId=a.get("id"), question=q.get("label"), type=qt, analyticKey=q.get("analyticKey"))
        choices = ((q.get("option") or {}).get("choices")) or {}
        if qt.startswith("product") and isinstance(v, dict):
            for sku, props in v.items():
                for prop, x in _flat(props) if isinstance(props, dict) else [("value", props)]:
                    rows.append(dict(head, sku=sku, product=products.get(sku, ""), property=prop or "value", value=x))
        elif qt == "image" or (isinstance(v, dict) and "url" in v):
            imgs = v if isinstance(v, list) else [v]
            urls = [i.get("url") for i in imgs if isinstance(i, dict) and i.get("url")]
            rows.append(dict(head, property="url" if include_photos else "photos",
                             value="\n".join(urls) if include_photos else len(urls)))
        elif isinstance(v, list) and choices:
            rows.append(dict(head, property="choice",
                             value=", ".join(str(choices.get(str(x), x)) for x in v)))
        else:
            for prop, x in _flat(v):
                if include_photos or not re.search(r"location|latitude|longitude|url", prop, re.I):
                    rows.append(dict(head, property=prop or "value", value=x))
    return rows


STATS_DIR = os.path.join(USER_DIR, "stats")
PROFILES_DIR = os.path.join(USER_DIR, "profiles")


def fetch_records(s, start, end, jobs=None, campaign=None, pad=3, quiet=False):
    """job-records ทีละวัน start..end+pad (API กรองตามเวลาส่ง) แล้วเก็บเฉพาะ record.date ใน start..end"""
    last = min(end + dt.timedelta(days=pad), dt.date.today())
    recs, itemmap, products, days = {}, {}, {}, 0
    day = start
    while day <= last:
        for job in jobs or [None]:
            params = {"startAt": day.isoformat(), "endAt": day.isoformat()}
            if job:
                params["jobId"] = job
            if campaign:
                params["campaignName"] = campaign
            d = fetch_json(s, "/job-records", ROUTE_RECORDS, params)
            blk = d.get("data") or {}
            if not isinstance(blk.get("records"), list):
                die("ไม่พบ data.records ใน job-records - รูปแบบ API อาจเปลี่ยน (รัน doctor)")
            itemmap.update(blk.get("itemMap") or {})
            products.update({p.get("sku"): p.get("name") for p in blk.get("products") or [] if p.get("sku")})
            for r in blk["records"]:
                if start.isoformat() <= str(r.get("date") or "")[:10] <= end.isoformat():
                    recs[r.get("id")] = r
            time.sleep(PAUSE_SEC / 2)
        days += 1
        day += dt.timedelta(days=1)
    if not quiet:
        print(f"ขอข้อมูล {days} วัน ({start}..{last}) ได้ {len(recs)} records ที่ date อยู่ใน {start}..{end}")
    return recs, itemmap, products


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _json_load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _json_save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _days(start, end):
    return [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _short(items, n=6):
    items = list(items)
    return ", ".join(items[:n]) + (" ..." if len(items) > n else "")


def _report_check(title, warns):
    if not warns:
        print(f"\nตรวจ{title}: ✅ ไม่พบความผิดปกติ")
        return
    print(f"\nตรวจ{title}: ⚠️ {len(warns)} เรื่อง (ตรวจก่อนใช้ตัวเลข)")
    for w in warns[:8]:
        print("  - " + w)


def check_records(recs, rows, start, end, key):
    """ตรวจหลังดึง records - คำนวณจากข้อมูลที่ดึงมาแล้ว ไม่เรียก API เพิ่ม"""
    warns, today = [], dt.date.today().isoformat()
    per_day = collections.Counter(str(r.get("date") or "")[:10] for r in recs.values())
    days = [d for d in _days(start, end) if d < today]
    zero = [d for d in days if not per_day[d]]
    if zero and len(zero) < len(days):
        warns.append(f"ไม่มี record เลย {len(zero)} วัน: {_short(zero)} "
                     "(วันหยุด / ไม่มีงาน / ข้อมูลหาย - เทียบกับ attendance)")
    nz = sorted(per_day[d] for d in days if per_day[d])
    if nz:
        med = nz[len(nz) // 2]
        low = [d for d in days if 0 < per_day[d] < med * 0.5]
        if low:
            warns.append(f"record น้อยกว่าครึ่งของปกติ (มัธยฐาน {med}/วัน): {_short(low)}")
    stats_path = os.path.join(STATS_DIR, f"{key}.json")
    prev = _json_load(stats_path, {})
    avg = (sum(nz) / len(nz)) if nz else 0
    if prev.get("avg_per_day") and avg and avg < prev["avg_per_day"] * 0.5:
        warns.append(f"เฉลี่ย {avg:.0f} record/วัน ต่ำกว่ารอบก่อน ({prev['avg_per_day']:.0f}) เกินครึ่ง")
    prof = _json_load(os.path.join(PROFILES_DIR, f"{key.split('__')[0]}.json"), {})
    known = set(prof.get("skus") or [])
    skus = {r["sku"] for r in rows if r.get("sku")}
    if known and skus - known:
        new = sorted(skus - known)
        warns.append(f"SKU ใหม่ที่ไม่อยู่ใน profile {len(new)} ตัว: {_short(new)} (รัน profile ใหม่)")
    bad_num = [r for r in rows if r.get("type") == "number" and r.get("value") not in ("", None)
               and _num(r["value"]) is None]
    if bad_num:
        warns.append(f"ช่องตัวเลขมีข้อความ {len(bad_num)} แถว (เช่น '{str(bad_num[0]['value'])[:20]}')")
    qty = [(_num(r.get("value")), r) for r in rows
           if r.get("property") in ("quantity", "value") and str(r.get("type", "")).startswith("product")]
    vals = sorted(v for v, _ in qty if v is not None and v > 0)
    if len(vals) >= 20:
        cap = max(50, vals[int(len(vals) * 0.99)] * 3)
        big = [(v, r) for v, r in qty if v is not None and v > cap]
        if big:
            warns.append(f"จำนวนสูงผิดปกติ (> {cap:.0f}) {len(big)} แถว เช่น {big[0][1].get('sku')} = "
                         f"{big[0][0]:.0f} ({big[0][1].get('date')})")
    neg = [r for r in rows if r.get("property") == "price" and (_num(r.get("value")) or 0) < 0]
    if neg:
        warns.append(f"ราคาติดลบ {len(neg)} แถว")
    raw_choice = [r for r in rows if r.get("property") == "choice"
                  and re.fullmatch(r"[\d, ]+", str(r.get("value") or ""))]
    if raw_choice:
        warns.append(f"ตัวเลือกที่แปลเป็นข้อความไม่ได้ {len(raw_choice)} แถว (ฟอร์มอาจถูกแก้ - รัน profile ใหม่)")
    _report_check("ข้อมูล", warns)
    if nz and end.isoformat() < today:
        _json_save(stats_path, {"avg_per_day": avg, "range": [start.isoformat(), end.isoformat()],
                                "updated": dt.datetime.now().isoformat(timespec="seconds")})


def check_attendance(flat, start, end):
    """ตรวจหลังดึง attendance - ไม่เรียก API เพิ่ม"""
    warns, today = [], dt.date.today().isoformat()

    def day_of(r):
        t = r.get("stCheckedInTime") or r.get("createdDate") or ""
        try:
            return (dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")) + dt.timedelta(hours=7)).date().isoformat()
        except ValueError:
            return str(t)[:10]
    per_day = collections.Counter(day_of(r) for r in flat)
    days = [d for d in _days(start, end) if d < today]
    zero = [d for d in days if not per_day[d]]
    if zero and len(zero) < len(days):
        warns.append(f"ไม่มีคนเข้างานเลย {len(zero)} วัน: {_short(zero)}")
    no_out = [r for r in flat if r.get("stCheckedInTime") and not r.get("stCheckedOutTime") and day_of(r) < today]
    if no_out:
        warns.append(f"เช็คอินแต่ไม่เช็คเอาท์ {len(no_out)} รายการ")
    far = [r for r in flat if (_num(r.get("stDiffDistance")) or 0) > 300]
    if far:
        warns.append(f"เช็คอินไกลร้านเกิน 300 ม. {len(far)} รายการ")
    _report_check("การเข้างาน", warns)


def cmd_records(args):
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if end < start:
        die("--from ต้องไม่เกิน --to")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        die(f"ช่วงวันที่เกิน {MAX_RANGE_DAYS} วัน - แบ่งดึงเป็นหลายครั้ง")
    s = login()
    recs, itemmap, products = fetch_records(s, start, end, args.jobs, args.campaign, args.pad)

    rows = []
    for r in sorted(recs.values(), key=lambda r: (str(r.get("date")), r.get("id") or 0)):
        rows += record_rows(r, itemmap, products, args.include_names, args.include_photos)
    os.makedirs(args.out, exist_ok=True)
    tag = f"_{args.campaign}" if args.campaign else ""
    tag += f"_job{'-'.join(map(str, args.jobs))}" if args.jobs else ""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", tag)
    path = os.path.join(args.out, f"{PROJECT}_{ORGANIZATION_ID}_records{safe}_{start}_{end}"
                                  f"{'_names' if args.include_names else ''}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=RECORD_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    by_form = collections.Counter((r.get("campaignName"), r.get("formName")) for r in recs.values())
    for (c, fm), n in by_form.most_common():
        print(f"  {n:>5} records  {c} / {fm}")
    print(f"\n{len(rows)} แถว (record x คำถาม x SKU x property) -> {path}")
    if not args.no_check:
        key = f"{PROFILE}_{ORGANIZATION_ID}" + (f"__{safe.strip('_')}" if safe else "")
        check_records(recs, rows, start, end, key)
    if args.include_names or args.include_photos:
        print("!! ไฟล์นี้มีข้อมูลส่วนบุคคล/ลิงก์รูป - เก็บในที่ปลอดภัย อย่าส่งต่อโดยไม่ได้รับอนุมัติ")


def _price_meaning(samples):
    """[(sku, qty, price)] -> ('ราคาต่อชิ้น' / 'ยอดรวมบรรทัด' / 'ไม่แน่ใจ ...', จำนวนแถวที่ใช้ตัดสิน)"""
    unit = collections.defaultdict(list)
    for sku, q, p in samples:
        if q == 1:
            unit[sku].append(p)
    vote = collections.Counter()
    for sku, q, p in samples:
        if q < 2 or not unit.get(sku):
            continue
        u = sorted(unit[sku])[len(unit[sku]) // 2]
        if u and abs(p / q - u) / u < 0.15:
            vote["ยอดรวมบรรทัด"] += 1
        elif u and abs(p - u) / u < 0.15:
            vote["ราคาต่อชิ้น"] += 1
    if not vote:
        return "ไม่แน่ใจ (ไม่มีแถวที่ขาย >1 ชิ้นให้เทียบ)", 0
    top, n = vote.most_common(1)[0]
    share = n / sum(vote.values())
    return (top if share >= 0.8 else f"ไม่แน่ใจ ({dict(vote)})"), sum(vote.values())


def cmd_profile(args):
    """วิเคราะห์ว่าโปรเจกต์เก็บข้อมูลอะไร (job / ฟอร์ม / คำถาม / ตัวเลือก / ความหมายราคา) จาก N วันล่าสุด
    -> ~/.cloudviu/profiles/<บัญชี>_<org>.json + .md (และ PROFILE_<org>.md ใน project ถ้าใส่ --into)"""
    end = dt.date.fromisoformat(args.to) if args.to else dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)
    s = login()
    recs, itemmap, products = fetch_records(s, start, end, None, None, pad=2)
    key = f"{PROFILE}_{ORGANIZATION_ID}"
    reg_jobs = next((p.get("jobs") for p in (_registry().get(PROFILE) or {}).get("projects", [])
                     if p.get("id") == ORGANIZATION_ID), None) or job_list(s)
    att = fetch_attendance_page(s, 1, start.isoformat(), end.isoformat())
    att_total = (find_pagination(att) or {}).get("total") or len(find_record_list(att)[1] or [])

    used_jobs = collections.Counter(r.get("jobName") for r in recs.values())
    jobs = []
    for j in reg_jobs:
        n = used_jobs.get(j["name"], 0)
        kind = ("เก็บข้อมูล" if n else "test" if re.search(r"test", j["name"] or "", re.I)
                else "เช็คอินอย่างเดียว / ไม่ได้ใช้ช่วงนี้")
        jobs.append({"id": j["id"], "name": j["name"], "records": n, "kind": kind})

    answered = collections.Counter()
    offtake = collections.defaultdict(list)
    skus = set()
    for r in recs.values():
        for a in r.get("data") or []:
            if "value" not in a:
                continue
            k = f"{r.get('formId')}/{a.get('id')}"
            answered[k] += 1
            v = a["value"]
            q = (itemmap.get(k) or {}).get("data") or {}
            if str(q.get("type", "")).startswith("product") and isinstance(v, dict):
                skus.update(v.keys())
                if q.get("type") == "product-offtake":
                    for sku, pr in v.items():
                        if isinstance(pr, dict) and _num(pr.get("quantity")) and _num(pr.get("price")):
                            offtake[k].append((sku, _num(pr["quantity"]), _num(pr["price"])))
    forms = collections.OrderedDict()
    for r in sorted(recs.values(), key=lambda r: (str(r.get("campaignName")), str(r.get("formName")))):
        f = forms.setdefault(r.get("formId"), {"formId": r.get("formId"), "form": r.get("formName"),
                                               "campaign": r.get("campaignName"), "job": r.get("jobName"),
                                               "records": 0, "questions": []})
        f["records"] += 1
    asks = []
    for fid, f in forms.items():
        for k, it in itemmap.items():
            if not k.startswith(f"{fid}/"):
                continue
            q = it.get("data") or {}
            ch = ((q.get("option") or {}).get("choices")) or {}
            row = {"id": k.split("/", 1)[1], "label": q.get("label"), "type": q.get("type"),
                   "analyticKey": q.get("analyticKey"), "answered": answered.get(k, 0),
                   "choices": {str(c): str(t) for c, t in ch.items()} if isinstance(ch, dict) else {},
                   "skus": len(it.get("productSkus") or [])}
            if q.get("type") == "product-offtake":
                meaning, _n = (_price_meaning(offtake.get(k, [])) if answered.get(k)
                               else ("ยังไม่มีข้อมูลช่วงนี้", 0))
                row["price_meaning"] = meaning
                if meaning.startswith("ไม่แน่ใจ"):
                    asks.append(f"ฟอร์ม '{f['form']}' คำถาม '{q.get('label')}': price เป็นราคาต่อชิ้นหรือยอดรวมบรรทัด?")
            f["questions"].append(row)
    prof = {"account": PROFILE, "org": ORGANIZATION_ID, "project": DISPLAY_NAME,
            "window": [start.isoformat(), end.isoformat()], "created": dt.datetime.now().isoformat(timespec="seconds"),
            "records": len(recs), "attendance": att_total, "jobs": jobs, "forms": list(forms.values()),
            "skus": sorted(skus), "questions_to_confirm": asks}

    old = _json_load(os.path.join(PROFILES_DIR, f"{key}.json"), {})
    changes = []
    if old:
        def qmap(p):
            return {(f["formId"], q["id"]): q for f in p.get("forms", []) for q in f.get("questions", [])}
        oq, nq = qmap(old), qmap(prof)
        changes += [f"คำถามใหม่: {nq[k]['label']}" for k in nq.keys() - oq.keys()]
        changes += [f"ตัวเลือกเปลี่ยน: {nq[k]['label']}" for k in nq.keys() & oq.keys()
                    if nq[k].get("choices") != oq[k].get("choices")]
        changes += [f"คำถามหาย / ไม่มีคนตอบช่วงนี้: {oq[k]['label']}" for k in oq.keys() - nq.keys()]
    prof["changes_since_last"] = changes
    _json_save(os.path.join(PROFILES_DIR, f"{key}.json"), prof)

    lines = [f"# Profile: {DISPLAY_NAME} (org {ORGANIZATION_ID})", "",
             f"สร้าง {prof['created'][:10]} จากข้อมูล {start} ถึง {end}: {len(recs)} records / เข้างาน {att_total} รายการ",
             "", "## Job", "| job | ชื่อ | records | ประเภท |", "|---|---|---|---|"]
    lines += [f"| {j['id']} | {j['name']} | {j['records']} | {j['kind']} |" for j in jobs]
    lines += ["", "## ฟอร์มและคำถาม"]
    for f in prof["forms"]:
        lines += ["", f"### {f['campaign']} / {f['form']} (form {f['formId']}, {f['records']} records, job {f['job']})",
                  "| คำถาม | ชนิด | ตอบ | หมายเหตุ |", "|---|---|---|---|"]
        for q in f["questions"]:
            notes = []
            if q.get("price_meaning"):
                notes.append("price = " + q["price_meaning"])
            if q["choices"]:
                notes.append("ตัวเลือก: " + " / ".join(f"{c}={t}" for c, t in list(q["choices"].items())[:8]))
            if q["skus"]:
                notes.append(f"{q['skus']} SKU")
            lines.append(f"| {q['label']} | {q['type']} | {q['answered']} | {'; '.join(notes)} |")
    if asks:
        lines += ["", "## ❓ ต้องยืนยันกับทีม"] + [f"- {a}" for a in asks]
    if changes:
        lines += ["", "## เปลี่ยนจาก profile รอบก่อน"] + [f"- {c}" for c in changes]
    md = "\n".join(lines) + "\n"
    paths = [os.path.join(PROFILES_DIR, f"{key}.md")]
    if INTO and WORKSPACE:
        paths.append(os.path.join(WORKSPACE, "projects", INTO, f"PROFILE_{ORGANIZATION_ID}.md"))
    for pth in paths:
        os.makedirs(os.path.dirname(pth), exist_ok=True)
        with open(pth, "w", encoding="utf-8") as fh:
            fh.write(md)

    data_jobs = [f"{j['id']} {j['name']}" for j in jobs if j["records"]]
    print(f"\n{DISPLAY_NAME}: {len(recs)} records ({len(forms)} ฟอร์ม) / เข้างาน {att_total} รายการ ใน {start}..{end}")
    print("job เก็บข้อมูล: " + (", ".join(data_jobs) or "- (ไม่มี = โปรเจกต์เช็คอินอย่างเดียว)"))
    for a in asks:
        print("❓ " + a)
    for c in changes:
        print("Δ " + c)
    print("profile: " + " | ".join(paths))


# ============================== CAMPAIGNS (อ่านอย่างเดียว) ==============================
#
# GET /campaigns.data             -> route routes/_app.campaigns._index   รายการ campaign ของโปรเจกต์
# GET /campaigns/<id>/edit.data   -> route routes/_app.campaigns.$id      meta ของ campaign (ฟอร์ม/คำถาม)
# ใช้แค่ GET ของหน้า edit (loader) ไม่ส่ง action ใดๆ ไม่แก้ campaign

ROUTE_CAMPAIGNS = "routes/_app.campaigns._index"
ROUTE_CAMPAIGN = "routes/_app.campaigns.$id"


def fetch_json(s, path, route, params=None):
    """
    ข้อมูล loader ของ route หนึ่ง (รูปเดียวกับที่ ?_data=<route> เคยคืน)
    React Router v7: GET <path>.data -> turbo-stream ของทุก route ที่ match -> เลือกเฉพาะ route ที่ขอ
    """
    qs = urllib.parse.urlencode(params or {})
    code, _, body = s.get(f"{BASE}{path.rstrip('/')}.data" + (f"?{qs}" if qs else ""))
    try:
        d = decode_turbo(body)
    except ValueError:
        die(f"GET {path} (HTTP {code}) อ่านไม่ได้ - รูปแบบข้อมูลของ CloudViu อาจเปลี่ยนอีก")
    if isinstance(d, dict) and d.get("redirect"):
        die(f"GET {path} ถูกส่งไป {d['redirect']} - session หลุด หรือไม่มีสิทธิ์หน้านี้")
    if code != 200:
        die(f"GET {path} ไม่สำเร็จ HTTP {code}")
    node = d.get(route) if isinstance(d, dict) else None
    if not isinstance(node, dict) or "data" not in node:
        have = ", ".join(k for k in (d or {}) if isinstance(k, str) and k.startswith(("root", "routes/")))
        die(f"ไม่พบ route {route} ใน {path}.data (มี: {have or '-'}) - route id อาจเปลี่ยน ลอง --route")
    return node["data"]


def strip_pii(o, path=""):
    """ตัดคีย์ข้อมูลส่วนบุคคล/รูปทิ้งแบบ recursive (เกณฑ์เดียวกับ attendance)"""
    if isinstance(o, dict):
        return {k: strip_pii(v, f"{path}.{k}" if path else k) for k, v in o.items()
                if not is_pii_key(f"{path}.{k}" if path else k, False, False)}
    if isinstance(o, list):
        return [strip_pii(x, path) for x in o]
    return o


def cmd_campaigns(args):
    s = login()
    data = fetch_json(s, "/campaigns", ROUTE_CAMPAIGNS)
    if args.describe:
        print("โครงสร้าง response (ไม่มีค่าจริง):")
        print("\n".join(describe(data)))
        return
    items = list((data.get("data") or {}).get("items") or [])
    meta = (data.get("data") or {}).get("meta") or {}
    path = "data.items"
    if not items:
        die("ไม่พบรายการ campaign ใน response (ลอง --describe)")
    pages = int(meta.get("totalPages") or 1)
    for page in range(2, min(pages, MAX_PAGES) + 1):   # หน้าละ 20
        time.sleep(PAUSE_SEC)
        items += ((fetch_json(s, "/campaigns", ROUTE_CAMPAIGNS, {"page": page}).get("data") or {}).get("items") or [])
    if meta.get("totalItems") and len(items) != meta["totalItems"]:
        print(f"  !! ได้ {len(items)} จาก totalItems {meta['totalItems']}")
    items = strip_pii(items)
    cols = [k for k, v in items[0].items() if not isinstance(v, (dict, list))]
    print(f"{len(items)} campaigns (array = {path})\n")
    if not args.forms:
        print(" | ".join(cols))
        for it in items:
            print(" | ".join(str(it.get(k, ""))[:40] for k in cols))
        return

    # --forms: เปิด meta ทีละ campaign สรุปฟอร์ม/ชนิดคำถาม (เว้นระยะทุก request)
    out = []
    for it in items:
        time.sleep(PAUSE_SEC)
        d = (strip_pii(fetch_json(s, f"/campaigns/{it['id']}/edit", ROUTE_CAMPAIGN)).get("data") or {})
        forms = []
        for f in d.get("forms") or []:
            qs = f.get("items") or []
            forms.append({
                "formId": f.get("id"), "name": f.get("name"), "status": f.get("status"),
                "questions": len(qs),
                "enabled": sum(1 for q in qs if not q.get("disabled")),
                "types": dict(sorted(collections.Counter(q.get("type") for q in qs).items(), key=str)),
                "analyticKeys": sorted({q.get("analyticKey") for q in qs if q.get("analyticKey")}),
                "labels": [q.get("label") for q in qs][:40],
                "products": sum(len(q.get("products") or []) for q in qs),
                "updatedAt": f.get("updatedAt"),
            })
        out.append({"id": it["id"], "name": it.get("name"), "updatedAt": it.get("updatedAt"), "forms": forms})
        print(f"{it['id']:>5} {str(it.get('name'))[:44]:44} " + "; ".join(
            f"form {f['formId']} [{f['status']}] {f['enabled']}/{f['questions']} ข้อ {f['types']}" for f in forms))
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "campaign_forms.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"\nเขียนไฟล์ (ตัดข้อมูลส่วนบุคคลแล้ว): {path}")


FORMS_FILE = os.path.join(DEFAULT_OUT, "campaign_forms.json")


def form_segments(grid, header_row):
    """แบ่งคอลัมน์ของ sheet job เป็นช่วงของแต่ละฟอร์ม -> [(c0, c1, campaignId, campaignName, formId, formName)]
    แถว 1-2 ของไฟล์ export ใส่ชื่อ campaign/ฟอร์มไม่ครบ (ฟอร์มที่ไม่มีชื่อถูกนับรวมกับกลุ่มก่อนหน้า)
    จึงจับคู่ลำดับหัวคอลัมน์กับรายการคำถามของแต่ละฟอร์มจาก campaign_forms.json (สร้างด้วย `campaigns --forms`)"""
    if not os.path.exists(FORMS_FILE):
        die(f"ไม่พบ {FORMS_FILE} - รัน `cloudviu.py campaigns --forms` ก่อน")
    with open(FORMS_FILE, encoding="utf-8") as f:
        camps = json.load(f)
    # คอลัมน์ในไฟล์ export เรียงตามเลข campaign จากน้อยไปมาก แล้วตามลำดับฟอร์มใน campaign
    # (ยืนยันกับหลายโปรเจกต์) - เรียงรายการฟอร์มแบบเดียวกันเพื่อใช้แยกฟอร์มที่คำถามเหมือนกัน
    forms = [(c["id"], c["name"], f["formId"], f["name"], [str(l).strip() for l in f["labels"]])
             for c in sorted(camps, key=lambda c: int(c["id"])) for f in c["forms"] if f["labels"]]
    hdr = grid.get(header_row, {})
    # คอลัมน์พื้นฐาน (Entry ID, ร้าน, เวลา) อยู่ใต้ "Project: ..." - เริ่มนับจากกลุ่ม campaign แรก
    first = min((c for c, v in grid.get(1, {}).items() if str(v).strip() and not str(v).startswith("Project:")), default=1)
    cols = sorted(c for c in hdr if c >= first)
    labels = [str(hdr[c]).strip() for c in cols]
    segs, used, i, last_cid = [], set(), 0, 0
    while i < len(cols):
        cand = [f for f in forms if labels[i:i + len(f[4])] == f[4]]
        if not cand:
            segs.append((cols[i], cols[i], None, "(ไม่ตรงฟอร์มใด)", None, labels[i]))
            i += 1
            continue
        n = max(len(f[4]) for f in cand)
        # ฟอร์มคำถามเหมือนกัน (เช่นธีมโปรโมชั่นรายแบรนด์) เรียงตามลำดับใน campaign - เลือกตัวที่ยังไม่ถูกใช้ก่อน
        same = [f for f in cand if len(f[4]) == n]
        # ฟอร์มคำถามเหมือนกัน (เช่นธีมโปรรายแบรนด์ หรือฟอร์มที่คัดลอกไปไว้อีก campaign):
        # เลือกตัวแรกที่ยังไม่ถูกใช้และอยู่ไม่ก่อน campaign ของกลุ่มก่อนหน้า
        f = (next((f for f in same if f[2] not in used and int(f[0]) >= last_cid), None)
             or next((f for f in same if f[2] not in used), same[0]))
        used.add(f[2])
        last_cid = int(f[0])
        segs.append((cols[i], cols[i + n - 1], f[0], f[1], f[2], f[3]))
        i += n
    return segs


def campaign_usage(grid):
    """{(campaignId, campaign, formId, ฟอร์ม): จำนวน record ที่กรอกช่องของฟอร์มนั้น}"""
    header_row = next((r for r in sorted(grid) if r <= 10 and "Record ID" in grid[r].values()), None)
    if header_row is None:
        return {}, 0
    data_rows = [grid[r] for r in sorted(grid) if r > header_row]
    used = {}
    for c0, c1, cid, cname, fid, fname in form_segments(grid, header_row):
        n = sum(1 for r in data_rows if any(c0 <= c <= c1 and str(v).strip() for c, v in r.items()))
        key = (cid, cname, fid, fname)
        used[key] = used.get(key, 0) + n
    return used, len(data_rows)


def campaign_block(grid, campaign):
    """(header_row, {col: หัวคอลัมน์}, [แถวข้อมูล]) ของกลุ่มคอลัมน์ campaign ตามชื่อในแถว 1"""
    header_row = next((r for r in sorted(grid) if r <= 10 and "Record ID" in grid[r].values()), None)
    top = {c: str(v).strip() for c, v in grid.get(1, {}).items() if str(v).strip()}
    starts = sorted(top)
    c0 = next((c for c in starts if top[c] == campaign), None)
    if header_row is None or c0 is None:
        return header_row, {}, []
    nxt = [c for c in starts if c > c0]
    c1 = nxt[0] - 1 if nxt else 10 ** 6
    headers = {c: str(v).strip() for c, v in grid[header_row].items() if c0 <= c <= c1}
    return header_row, headers, [grid[r] for r in sorted(grid) if r > header_row]


def cmd_peek(args):
    """ดูหัวคอลัมน์และรูปแบบค่าของ campaign หนึ่งในไฟล์ export (ลิงก์รูปแสดงแค่จำนวน)"""
    s = login()
    grid, _ = read_job_sheet(download_export(s, args.jobs[0], args.start, args.end))
    if args.layout:
        # แถว 1 = campaign, แถว 2 = ฟอร์ม (merge: ค่าอยู่ที่คอลัมน์แรกของกลุ่ม) - พิมพ์ทุกจุดที่เริ่มกลุ่มใหม่
        header_row = next((r for r in sorted(grid) if r <= 10 and "Record ID" in grid[r].values()), 3)
        data_rows = [grid[r] for r in sorted(grid) if r > header_row]
        cols = sorted({c for r in (1, 2, header_row) for c in grid.get(r, {})})
        starts = [c for c in cols if str(grid.get(1, {}).get(c, "")).strip() or str(grid.get(2, {}).get(c, "")).strip()]
        for i, c in enumerate(starts):
            end = starts[i + 1] - 1 if i + 1 < len(starts) else max(cols)
            filled = sum(1 for r in data_rows if any(c <= k <= end and str(v).strip() for k, v in r.items()))
            print(f"col {c:4}-{end:4} | campaign: {str(grid.get(1, {}).get(c, '')).strip()[:40]:40} | "
                  f"form: {str(grid.get(2, {}).get(c, '')).strip()[:40]:40} | {end - c + 1:3} คอลัมน์ | {filled:5} records")
        # หัวคอลัมน์ + จำนวนแถวที่มีค่า (ไม่มีค่าที่กรอก) ไว้จับคู่คอลัมน์กับฟอร์มทีหลัง
        cols_info = [{"col": c, "campaign": str(grid.get(1, {}).get(c, "")).strip(), "form": str(grid.get(2, {}).get(c, "")).strip(),
                      "header": str(grid.get(header_row, {}).get(c, "")).strip(),
                      "filled": sum(1 for r in data_rows if str(r.get(c, "")).strip())} for c in cols]
        os.makedirs(DEFAULT_OUT, exist_ok=True)
        path = os.path.join(DEFAULT_OUT, f"export_headers_job{args.jobs[0]}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cols_info, f, ensure_ascii=False, indent=0)
        print(f"เขียนไฟล์หัวคอลัมน์ (ไม่มีข้อมูลที่กรอก): {path}")
        return
    if args.form:
        header_row = next((r for r in sorted(grid) if r <= 10 and "Record ID" in grid[r].values()), 3)
        rows = [grid[r] for r in sorted(grid) if r > header_row]
        headers = {}
        for c0, c1, *_rest, fid, _fname in form_segments(grid, header_row):
            if fid in args.form:
                headers.update({c: f"form {fid} | {str(grid[header_row].get(c, '')).strip()}" for c in range(c0, c1 + 1)})
    else:
        _, headers, rows = campaign_block(grid, args.campaign)
    if not headers:
        die(f"ไม่พบ campaign/ฟอร์มที่ขอในไฟล์ job {args.jobs[0]}")
    for c, h in sorted(headers.items()):
        vals = [str(r.get(c, "")).strip() for r in rows if str(r.get(c, "")).strip()]
        urls = sum(1 for v in vals if v.startswith("http"))
        other = collections.Counter(v[:80] for v in vals if not v.startswith("http"))
        print(f"[{h}] มีค่า {len(vals)} แถว, เป็นลิงก์ {urls}")
        if PII_HEADER.search(h.split("|")[-1]):
            print("     (ข้ามค่า - คอลัมน์ชื่อคน)")
            continue
        for v, n in other.most_common(12):
            print(f"     {n:5}  {v!r}")


FORM_BASE = ["entryDate", "createdAt", "updatedAt", "jobId", "entryId", "recordId", "user",
             "outletName", "outletType", "province", "teamRegion", "chainAccount",
             "checkin1", "checkout1", "campaignId", "formId", "formName"]


def parse_forms(grid, job_id, campaign_id, include_names, include_photos):
    """แถว record x ฟอร์ม ของ campaign หนึ่ง คอลัมน์ = หัวคำถาม (ชื่อคนตัดทิ้งเว้นแต่ include_names)
    คืน (rows, ชื่อคอลัมน์คำถามตามลำดับ, คอลัมน์ที่ตัดทิ้ง)"""
    header_row = next((r for r in sorted(grid) if r <= 10 and "Record ID" in grid[r].values()), None)
    if header_row is None:
        return [], [], set()
    hdr = grid[header_row]
    base_col = {c: BASE_COLUMNS[str(h).strip()] for c, h in hdr.items() if str(h).strip() in BASE_COLUMNS}
    segs = [sg for sg in form_segments(grid, header_row) if sg[2] == campaign_id]
    out, qcols, dropped = [], [], set()
    for r in sorted(grid):
        if r <= header_row:
            continue
        cells = grid[r]
        base = {f: cells.get(c, "") for c, f in base_col.items()}
        for c0, c1, cid, _cname, fid, fname in segs:
            vals = {}
            for c in range(c0, c1 + 1):
                label = str(hdr.get(c, "")).strip()
                v = str(cells.get(c, "")).strip()
                if not include_names and PII_HEADER.search(label):
                    dropped.add(label)
                    continue
                if not include_photos and v.startswith("http"):
                    vals[label] = str(len(v.split()))   # เก็บแค่จำนวนรูป
                    if label not in qcols:
                        qcols.append(label)
                    continue
                vals[label] = v
                if label not in qcols:
                    qcols.append(label)
            # แถวที่มีแต่ช่องชื่อคน (ไม่มีคำตอบจริง) ไม่นับ
            if not any(v for v in vals.values()):
                continue
            row = clean_row({**base, "jobId": job_id, "campaignId": cid, "formId": fid, "formName": fname.strip()},
                            include_names)
            row.update(vals)
            out.append(row)
    return out, qcols, dropped


ENTRY_COLUMNS = ["entryDate", "createdAt", "updatedAt", "jobId", "entryId", "recordId", "user",
                 "outletName", "outletType", "province", "teamRegion", "chainAccount",
                 "checkin1", "checkout1", "checkin2", "checkout2", "campaignId", "campaignName", "formId", "formName"]


def cmd_entries(args):
    """1 แถว = 1 record (ทุกฟอร์มทุก campaign) - ไว้ดูปริมาณงาน/เวลา ไม่มีคำตอบ"""
    span = (dt.date.fromisoformat(args.end) - dt.date.fromisoformat(args.start)).days + 1
    if not 1 <= span <= MAX_RANGE_DAYS:
        die(f"ช่วงวันที่ต้องอยู่ระหว่าง 1-{MAX_RANGE_DAYS} วัน")
    s = login()
    rows = {}
    for job in args.jobs:
        for a, b in chunks(args.start, args.end, CHUNK_DAYS):
            body = download_export(s, job, a, b)
            grid, _ = read_job_sheet(body)
            header_row = next((r for r in sorted(grid) if r <= 10 and "Record ID" in grid[r].values()), None)
            if header_row is None:
                continue
            hdr = grid[header_row]
            base_col = {c: BASE_COLUMNS[str(h).strip()] for c, h in hdr.items() if str(h).strip() in BASE_COLUMNS}
            segs = form_segments(grid, header_row)
            got = []
            for r in sorted(grid):
                if r <= header_row:
                    continue
                cells = grid[r]
                base = {f: cells.get(c, "") for c, f in base_col.items()}
                seg = next(((cid, cn, fid, fn) for c0, c1, cid, cn, fid, fn in segs
                            if any(c0 <= c <= c1 and str(v).strip() for c, v in cells.items())), (None, "", None, ""))
                got.append(clean_row({**base, "jobId": job, "campaignId": seg[0], "campaignName": seg[1],
                                      "formId": seg[2], "formName": str(seg[3]).strip()}, args.include_names))
            enrich(got, outlet_lookup(body))
            for g in got:
                rows[g["recordId"]] = g
            print(f"  job {job} {a}..{b} : {len(got)} records")
            time.sleep(PAUSE_SEC)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"{PROJECT}_entries_{args.start}_{args.end}{'_names' if args.include_names else ''}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=ENTRY_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows.values())
    print(f"\nสรุป: {len(rows)} records, {len({r['entryId'] for r in rows.values()})} entries")
    print(f"เขียนไฟล์: {path}")
    if args.include_names:
        print("!! ไฟล์นี้มีชื่อพนักงาน - เก็บในที่ปลอดภัย อย่าส่งต่อโดยไม่ได้รับอนุมัติ")


def cmd_forms(args):
    if not (args.start and args.end):
        die("ต้องระบุ --from และ --to (YYYY-MM-DD)")
    span = (dt.date.fromisoformat(args.end) - dt.date.fromisoformat(args.start)).days + 1
    if not 1 <= span <= MAX_RANGE_DAYS:
        die(f"ช่วงวันที่ต้องอยู่ระหว่าง 1-{MAX_RANGE_DAYS} วัน")
    s = login()
    rows, qcols, dropped = [], [], set()
    for job in args.jobs:
        for a, b in chunks(args.start, args.end, CHUNK_DAYS):
            body = download_export(s, job, a, b)
            grid, _ = read_job_sheet(body)
            got, q, d = parse_forms(grid, job, args.campaign, args.include_names, args.include_photos)
            enrich(got, outlet_lookup(body))
            print(f"  job {job} {a}..{b} : {len(got)} แถว (record x ฟอร์ม)")
            rows.extend(got)
            qcols += [x for x in q if x not in qcols]
            dropped |= d
            time.sleep(PAUSE_SEC)
    uniq = {(r["recordId"], r["formId"]): r for r in rows}
    rows = list(uniq.values())
    # ฟอร์มที่ปิดใช้แล้วยังมีคอลัมน์ในไฟล์ export - ไม่เอาคอลัมน์ที่ว่างทุกแถว
    qcols = [q for q in qcols if any(r.get(q) for r in rows)]

    os.makedirs(args.out, exist_ok=True)
    suffix = ("_names" if args.include_names else "") + ("_photos" if args.include_photos else "")
    path = os.path.join(args.out, f"{PROJECT}_campaign{args.campaign}_{args.start}_{args.end}{suffix}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FORM_BASE + qcols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"\nสรุป: {len({r['recordId'] for r in rows})} records, {len(rows)} แถว (record x ฟอร์ม)")
    for (fid, fname), n in collections.Counter((r["formId"], r["formName"]) for r in rows).most_common():
        print(f"  form {fid} {fname}: {n}")
    if dropped:
        print("ตัดทิ้ง (ชื่อคน): " + ", ".join(sorted(dropped)))
    if not args.include_photos:
        print("คอลัมน์รูปเก็บเป็นจำนวนรูป (ใช้ --include-photos ถ้าต้องการลิงก์)")
    print(f"\nเขียนไฟล์: {path}")
    if args.include_names or args.include_photos:
        print("!! ไฟล์นี้มีข้อมูลส่วนบุคคล/ลิงก์รูป - เก็บในที่ปลอดภัย อย่าส่งต่อโดยไม่ได้รับอนุมัติ")


def cmd_usage(args):
    span = (dt.date.fromisoformat(args.end) - dt.date.fromisoformat(args.start)).days + 1
    if not 1 <= span <= MAX_RANGE_DAYS:
        die(f"ช่วงวันที่ต้องอยู่ระหว่าง 1-{MAX_RANGE_DAYS} วัน")
    s = login()
    for job in args.jobs:
        total, used = 0, collections.Counter()
        for a, b in chunks(args.start, args.end, CHUNK_DAYS):
            grid, _ = read_job_sheet(download_export(s, job, a, b))
            u, n = campaign_usage(grid)
            used.update(u)
            total += n
            time.sleep(PAUSE_SEC)
        print(f"\njob {job} {args.start}..{args.end}: {total} records (นับต่อฟอร์ม)")
        for (cid, cname, fid, fname), n in sorted(used.items(), key=lambda kv: (-kv[1], str(kv[0][0]))):
            if n or args.all:
                print(f"  {n:6}  campaign {cid or '-':>5} {str(cname)[:36]:36} | form {fid or '-':>5} {str(fname)[:40]}")


# ============================== EXPLORE (หน้าอื่นของเว็บ - อ่านอย่างเดียว) ==============================
# ใช้กับหน้าที่ผู้ใช้ส่ง Request URL มาจาก DevTools เช่น
#   /premium-orders-distribute.data?page=1  (route id ดูจากคีย์ใน response เช่น routes/_app.premium-orders-distribute._index)
# GET loader ของหน้าอย่างเดียว ไม่ส่ง action / POST ใดๆ

def _explore_items(data):
    d = data.get("data") if isinstance(data.get("data"), dict) else {}
    if isinstance(d.get("items"), list):
        return "data.items", d["items"], d.get("meta") or {}
    path, items = find_record_list(data)
    return path, items, find_pagination(data)


def cmd_explore(args):
    path = "/" + args.path.strip().strip("/")
    if not re.fullmatch(r"/[a-z0-9\-/]+", path):
        die("path ต้องเป็นตัวอังกฤษเล็ก ตัวเลข - และ / เท่านั้น เช่น /premium-orders-distribute")
    route = args.route or f"routes/_app.{path.strip('/').replace('/', '.')}._index"
    params = dict(p.split("=", 1) for p in args.param or [])
    s = login()
    first = fetch_json(s, path, route, params)
    if args.describe or not (args.rows or args.save):
        print(f"โครงสร้าง {path} (ไม่มีค่าจริง):")
        print("\n".join(describe(first)))
    apath, items, meta = _explore_items(first)
    print(f"\narray หลัก: {apath} ({len(items)} รายการในหน้าแรก) | แบ่งหน้า: {meta or 'ไม่บอก'}")
    if not (args.rows or args.save) or not items:
        return
    pages = int(meta.get("totalPages") or meta.get("pageCount") or meta.get("lastPage") or 0)
    if args.save:
        page = 1
        while page < MAX_PAGES:
            if pages and page >= pages:
                break
            page += 1
            time.sleep(PAUSE_SEC)
            more = _explore_items(fetch_json(s, path, route, {**params, "page": page}))[1]
            if not more or (not pages and more == items[-len(more):]):
                break
            items += more
        if meta.get("totalItems") and len(items) != meta["totalItems"]:
            print(f"  !! ได้ {len(items)} จาก totalItems {meta['totalItems']}")
    items = strip_pii(items)
    flat = [flatten_obj(it) for it in items if isinstance(it, dict)]
    cols = []
    for r in flat:
        cols += [k for k in r if k not in cols and not is_pii_key(k, False, False)]
    if args.rows:
        show = [c for c in cols if not isinstance(flat[0].get(c), str) or len(str(flat[0].get(c))) < 60][:12]
        print(" | ".join(show))
        for r in flat[:args.rows]:
            print(" | ".join(str(r.get(c, ""))[:30] for c in show))
    if args.save:
        os.makedirs(args.out, exist_ok=True)
        name = path.strip("/").replace("/", "_")
        out = os.path.join(args.out, f"{PROJECT}_{name}.csv")
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(flat)
        print(f"\n{len(flat)} รายการ, {len(cols)} คอลัมน์ -> {out} (ตัดข้อมูลส่วนบุคคล/ลิงก์รูปแล้ว)")


ROUTE_JOBS = "routes/_app.jobs._index"


def cmd_jobs(args):
    """รายการ job ของ project (อ่านอย่างเดียว) - ไว้หาเลข job ใส่ config โดยไม่ต้องดูจากปุ่ม Export"""
    s = login()
    data = fetch_json(s, "/jobs", ROUTE_JOBS)
    if args.describe:
        print("โครงสร้าง response (ไม่มีค่าจริง):")
        print("\n".join(describe(data)))
        return
    items = list(((data.get("data") or {}).get("items")) or [])
    path = "data.items"
    if not items:
        path, items = find_record_list(data)
    if not items:
        die("ไม่พบรายการ job ใน response (ลอง --describe)")
    pages = int(((data.get("data") or {}).get("meta") or {}).get("totalPages") or 1)
    for page in range(2, min(pages, MAX_PAGES) + 1):
        time.sleep(PAUSE_SEC)
        items += ((fetch_json(s, "/jobs", ROUTE_JOBS, {"page": page}).get("data") or {}).get("items") or [])
    items = strip_pii(items)
    cols = [k for k, v in items[0].items() if not isinstance(v, (dict, list))][:8]
    print(f"{len(items)} jobs (array = {path}) | config ปัจจุบัน: jobs = {DEFAULT_JOBS}\n")
    print(" | ".join(cols))
    for it in items:
        print(" | ".join(str(it.get(k, ""))[:40] for k in cols))


ROUTE_APP = "routes/_app"


def cmd_projects(args):
    """โปรเจกต์ทั้งหมดที่บัญชีของ project นี้เข้าได้ (อ่านอย่างเดียว) - จาก organizations ใน loader หลักของแอป
    ไว้หาเลข organization_id ของโปรเจกต์ใหม่ (เช่น โปรเจกต์รายเดือน) โดยไม่ต้องเปิด DevTools
    ไม่บังคับเลือกโปรเจกต์ - ใช้กับบัญชีใหม่ที่ config ยังเป็น organization_id 0 ได้"""
    s = login(select_required=False)
    data = fetch_json(s, "/team-groups", ROUTE_APP)
    orgs = data.get("organizations")
    if not isinstance(orgs, list):
        die("ไม่พบ organizations ใน response - route อาจเปลี่ยน")
    if args.active:
        today = dt.date.today().isoformat()
        orgs = [o for o in orgs if (o.get("endAt") or "9999") >= today]
    print(f"บัญชีนี้ (role {data.get('role')}) เข้าได้ {len(orgs)} โปรเจกต์"
          f"{' ที่ยังไม่หมดอายุ' if args.active else ''} | config นี้ใช้ organization_id = {ORGANIZATION_ID}\n")
    print(f"{'id':>5}  {'ชื่อ':<44} {'เริ่ม':<10}   {'สิ้นสุด':<10}")
    for o in sorted(orgs, key=lambda o: o.get("id") or 0):
        mark = " <- config นี้" if o.get("id") == ORGANIZATION_ID else ""
        print(f"{o.get('id'):>5}  {str(o.get('name'))[:44]:<44} {o.get('startAt') or '-':<10} -> {o.get('endAt') or '-':<10}{mark}")


def existing_configs():
    """{organization_id: [ชื่อ project ที่มี config อยู่แล้ว]} จากทุก cloudviu-*/project.json"""
    out = {}
    for d in sorted(os.listdir(SKILLS_DIR)):
        p = os.path.join(SKILLS_DIR, d, "project.json")
        if not d.startswith("cloudviu-") or not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                c = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if str(c.get("organization_id", "")).isdigit() and int(c["organization_id"]):
            out.setdefault(int(c["organization_id"]), []).append(c.get("project") or d[len("cloudviu-"):])
    return out


def job_list(s):
    """[{id, name}] ของโปรเจกต์ที่เลือกอยู่ (ไม่เก็บข้อมูลส่วนบุคคล)"""
    block = lambda d: d.get("jobs") or d.get("data") or {}   # loader ตอบ {projectId, jobs: {items, meta}}
    first = fetch_json(s, "/jobs", ROUTE_JOBS)
    items = list(block(first).get("items") or [])
    pages = int((block(first).get("meta") or {}).get("totalPages") or 1)
    for page in range(2, min(pages, 20) + 1):
        time.sleep(PAUSE_SEC)
        items += block(fetch_json(s, "/jobs", ROUTE_JOBS, {"page": page})).get("items") or []
    return [{"id": it.get("id"), "name": it.get("name") or "", "status": it.get("status"),
             "selfDeployment": it.get("selfDeployment")} for it in items if it.get("id")]


def cmd_scan(args):
    """บัญชีใหม่: login -> หาโปรเจกต์ทั้งหมดที่บัญชีนี้เข้าได้ (+ job ของแต่ละโปรเจกต์) -> บันทึกลง accounts.json
    ไม่เก็บรหัส/เบอร์เต็ม (เบอร์เก็บแบบมาสก์) / อ่านอย่างเดียว / --set <id> = ใส่เลขโปรเจกต์ลง config ของบัญชีนี้"""
    mobile, _ = credentials()
    s = login(select_required=False)
    data = fetch_json(s, "/team-groups", ROUTE_APP)
    orgs = data.get("organizations")
    if not isinstance(orgs, list):
        die("ไม่พบ organizations ใน response - route อาจเปลี่ยน")
    today = dt.date.today().isoformat()
    have = existing_configs()
    projects = []
    for o in sorted(orgs, key=lambda o: o.get("id") or 0):
        pid = o.get("id")
        active = (o.get("endAt") or "9999") >= today
        row = {"id": pid, "name": o.get("name"), "startAt": o.get("startAt"), "endAt": o.get("endAt"),
               "active": active, "configs": have.get(pid, [])}
        if not args.no_jobs and (active or args.all_jobs):
            time.sleep(PAUSE_SEC)
            select_org(s, pid)
            row["jobs"] = job_list(s)
        projects.append(row)

    reg = _registry()
    os.makedirs(USER_DIR, exist_ok=True)
    reg.setdefault("_note", "บัญชี CloudViu -> โปรเจกต์ที่เข้าได้ (สร้างโดยคำสั่ง scan) - ไม่มีรหัสผ่าน เบอร์เก็บแบบมาสก์")
    reg[PROFILE] = {"mobile": mask(mobile), "role": data.get("role"),
                    "scanned_at": dt.datetime.now().isoformat(timespec="seconds"), "projects": projects}
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)

    n_act = sum(p["active"] for p in projects)
    print(f"\nบัญชี '{PROFILE}' ({mask(mobile)}, role {data.get('role')}) เข้าได้ {len(projects)} โปรเจกต์ "
          f"(ยังไม่หมดอายุ {n_act})\n")
    print(f"{'id':>5}  {'ชื่อ':<40} {'สิ้นสุด':<10}  {'jobs':<28} config ที่มีแล้ว")
    for p in projects:
        jobs = ", ".join(str(j["id"]) for j in p.get("jobs") or []) or ("-" if p["active"] else "(หมดอายุ)")
        print(f"{p['id']:>5}  {str(p['name'])[:40]:<40} {p['endAt'] or '-':<10}  {jobs[:28]:<28} "
              f"{', '.join(p['configs']) or '-'}")
    print(f"\nบันทึกแล้ว: {ACCOUNTS_FILE}")

    if args.set:
        hit = next((p for p in projects if p["id"] == args.set), None)
        if not hit:
            die(f"บัญชีนี้เข้าโปรเจกต์ {args.set} ไม่ได้")
        _block_if_shared("แก้ config")
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["organization_id"] = cfg["project_id"] = hit["id"]
        if not cfg.get("display_name") or cfg.get("display_name") == cfg.get("project"):
            cfg["display_name"] = hit["name"]
        if hit.get("jobs") and not cfg.get("jobs"):
            cfg["jobs"] = [j["id"] for j in hit["jobs"] if not re.search(r"test", j["name"] or "", re.I)]
        cfg["id_source"] = f"คำสั่ง scan ({dt.date.today().isoformat()}) - {hit['name']}"
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"ใส่โปรเจกต์ {hit['id']} ({hit['name']}) jobs {cfg.get('jobs')} ลง {CFG_PATH} แล้ว")
    elif not ORGANIZATION_ID:
        print("ยังไม่ได้เลือกโปรเจกต์ - ใส่ --org <id> ตอนดึงข้อมูล (หรือ `scan --set <id>` ตั้งเป็นค่าเริ่มต้น)")


def cmd_login(args):
    """(ผู้ใช้รันเองในเทอร์มินัล) ใส่เบอร์ + รหัส -> ลอง login -> เก็บใน Credential Manager -> สแกนโปรเจกต์ที่เข้าได้"""
    cmd_set_password(args)
    print("\nกำลังสแกนโปรเจกต์ที่บัญชีนี้เข้าได้ ...")
    args.set, args.no_jobs, args.all_jobs = None, False, False
    cmd_scan(args)
    print("\nเสร็จแล้ว - กลับไปที่ Claude แล้วบอกว่า login เสร็จ")


def cmd_doctor(_args):
    """ตรวจความพร้อม: Python, keyring, บัญชี, login, route ที่ใช้ (ไม่แสดงรหัส/ข้อมูลจริง)"""
    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            msg = fn()
            print(f"  OK    {name}{' - ' + msg if msg else ''}")
            return True
        except SystemExit:
            ok = False
            print(f"  FAIL  {name} (ดูข้อความ ERROR ด้านบน)")
        except Exception as e:   # noqa: BLE001 - แสดงทุกปัญหาให้ผู้ใช้เห็น
            ok = False
            print(f"  FAIL  {name} - {type(e).__name__}: {str(e)[:150]}")
        return False

    print(f"cloudviu-connector {CONNECTOR_VERSION} | บัญชี {PROFILE} | ข้อมูลผู้ใช้ {USER_DIR}")
    step("Python >= 3.9", lambda: sys.version.split()[0] if sys.version_info >= (3, 9) else (_ for _ in ()).throw(
        RuntimeError(f"ต้อง 3.9+ (มี {sys.version.split()[0]})")))
    step("keyring", lambda: "ติดตั้งแล้ว" if _keyring() else (_ for _ in ()).throw(
        RuntimeError("python -m pip install keyring")))
    if not step("มีบัญชีใน Credential Manager", lambda: mask(credentials()[0])):
        return
    box = {}
    if not step("login", lambda: box.setdefault("s", login(select_required=False)) and ""):
        return
    s = box["s"]
    step("รายการโปรเจกต์ (routes/_app)",
         lambda: f"{len(fetch_json(s, '/team-groups', ROUTE_APP).get('organizations') or [])} โปรเจกต์")
    if ORGANIZATION_ID:
        y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        step(f"job-records (โปรเจกต์ {ORGANIZATION_ID}, {y})", lambda: f"{len((fetch_json(s, '/job-records', ROUTE_RECORDS, {'startAt': y, 'endAt': y}).get('data') or {})['records'])} records")
        step(f"attendance (โปรเจกต์ {ORGANIZATION_ID}, {y})",
             lambda: f"{len(find_record_list(fetch_attendance_page(s, 1, y, y))[1])} รายการในหน้า 1")
    else:
        print("  -     ข้าม job-records / attendance (ใส่ --org <id> เพื่อทดสอบโปรเจกต์)")
    print("\nพร้อมใช้งาน" if ok else "\nมีขั้นที่ไม่ผ่าน - แก้ตามข้อความด้านบน")


def cmd_campaign(args):
    s = login()
    data = strip_pii(fetch_json(s, f"/campaigns/{args.id}/edit", ROUTE_CAMPAIGN))
    print(f"โครงสร้าง campaign {args.id} (ไม่มีค่าจริง):")
    print("\n".join(describe(data)))
    if args.save:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, f"campaign_{args.id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"\nเขียนไฟล์ (ตัดข้อมูลส่วนบุคคลแล้ว): {path}")


def main():
    p = argparse.ArgumentParser(description=f"cloudviu-connector {CONNECTOR_VERSION} - project {PROJECT} ({DISPLAY_NAME})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="ทดสอบ login + เลือกโปรเจกต์ (ไม่ดึงข้อมูล)").set_defaults(fn=cmd_check)
    for name in ("setup", "set-password"):   # set-password = ชื่อเดิม ยังใช้ได้
        sp = sub.add_parser(name, help="(ผู้ใช้รันเอง) ใส่เบอร์ + รหัส CloudViu ลอง login แล้วเก็บใน Credential Manager")
        sp.add_argument("--show", action="store_true", help=argparse.SUPPRESS)   # เดิม: ให้รหัสขึ้นจอ - ตอนนี้ขึ้นจอเสมอ
        sp.set_defaults(fn=cmd_set_password)
    sub.add_parser("forget", help="(ผู้ใช้รันเอง) ลบบัญชีของ project นี้ออกจาก Credential Manager").set_defaults(fn=cmd_forget)

    o = sub.add_parser("osa", help="ดึง OSA/OOS เป็น CSV (1 แถว = record x SKU)")
    o.add_argument("--from", dest="start", help="YYYY-MM-DD (Entry Date)")
    o.add_argument("--to", dest="end", help="YYYY-MM-DD (Entry Date)")
    o.add_argument("--jobs", type=int, nargs="+", default=DEFAULT_JOBS, help=f"job id (ค่าเริ่มต้นจาก config: {DEFAULT_JOBS})")
    o.add_argument("--xlsx", help="แปลงไฟล์ Excel ที่ export เองแทนการดาวน์โหลด")
    o.add_argument("--include-names", action="store_true", help="เก็บชื่อพนักงาน (ข้อมูลส่วนบุคคล - ใช้เมื่อจำเป็นเท่านั้น)")
    o.add_argument("--out", default=DEFAULT_OUT, help=f"โฟลเดอร์ผลลัพธ์ (ค่าเริ่มต้น <repo>/out/{PROJECT})")
    o.set_defaults(fn=cmd_osa)

    ol = sub.add_parser("outlets", help="ดูคอลัมน์ของ sheet Outlet-Master (ไม่แสดงชื่อ/เบอร์ผู้ติดต่อ)")
    ol.add_argument("--from", dest="start", help="YYYY-MM-DD")
    ol.add_argument("--to", dest="end", help="YYYY-MM-DD")
    ol.add_argument("--jobs", type=int, nargs="+", default=DEFAULT_JOBS)
    ol.add_argument("--xlsx", help="ใช้ไฟล์ Excel ที่ export เองแทนการดาวน์โหลด")
    ol.set_defaults(fn=cmd_outlets)

    cs = sub.add_parser("campaigns", help="รายการ campaign ในโปรเจกต์ (อ่านอย่างเดียว)")
    cs.add_argument("--describe", action="store_true", help="แสดงแค่โครงสร้าง response")
    cs.add_argument("--forms", action="store_true", help="เปิด meta ทุก campaign สรุปฟอร์ม/คำถาม (1 request ต่อ campaign)")
    cs.add_argument("--out", default=DEFAULT_OUT)
    cs.set_defaults(fn=cmd_campaigns)

    en = sub.add_parser("entries", help="1 แถว = 1 record ทุกฟอร์ม (ปริมาณงาน/เวลาเข้าออกร้าน) ไม่มีคำตอบ")
    en.add_argument("--from", dest="start", required=True)
    en.add_argument("--to", dest="end", required=True)
    en.add_argument("--jobs", type=int, nargs="+", default=DEFAULT_JOBS)
    en.add_argument("--include-names", action="store_true", help="เก็บชื่อพนักงาน (ใช้เมื่อผู้ใช้ขอดูรายคน)")
    en.add_argument("--out", default=DEFAULT_OUT)
    en.set_defaults(fn=cmd_entries)

    fm = sub.add_parser("forms", help="ดึงคำตอบของ campaign ใดก็ได้เป็น CSV (1 แถว = record x ฟอร์ม)")
    fm.add_argument("--campaign", type=int, required=True, help="campaign id (ดูจาก `campaigns`)")
    fm.add_argument("--from", dest="start", help="YYYY-MM-DD (Entry Date)")
    fm.add_argument("--to", dest="end", help="YYYY-MM-DD (Entry Date)")
    fm.add_argument("--jobs", type=int, nargs="+", default=DEFAULT_JOBS)
    fm.add_argument("--include-names", action="store_true", help="เก็บชื่อพนักงาน/ชื่อ Mer-Sup (ใช้เมื่อจำเป็นเท่านั้น)")
    fm.add_argument("--include-photos", action="store_true", help="เก็บลิงก์รูป (ค่าเริ่มต้นเก็บแค่จำนวนรูป)")
    fm.add_argument("--out", default=DEFAULT_OUT)
    fm.set_defaults(fn=cmd_forms)

    us = sub.add_parser("usage", help="นับ record ต่อ campaign ในไฟล์ export ของแต่ละ job (ดูว่า campaign ไหนใช้จริง)")
    us.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    us.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    us.add_argument("--jobs", type=int, nargs="+", default=DEFAULT_JOBS)
    us.add_argument("--all", action="store_true", help="แสดงฟอร์มที่ไม่มี record ด้วย")
    us.set_defaults(fn=cmd_usage)

    pk = sub.add_parser("peek", help="ดูหัวคอลัมน์/รูปแบบค่าของ campaign ในไฟล์ export (ลิงก์รูปนับอย่างเดียว)")
    pk.add_argument("campaign", nargs="?", default="", help="ชื่อ campaign ตามแถวบนสุดของไฟล์ export")
    pk.add_argument("--layout", action="store_true", help="แสดงกลุ่มคอลัมน์ campaign/ฟอร์ม ทั้งไฟล์")
    pk.add_argument("--form", type=int, nargs="+", help="ดูเฉพาะคอลัมน์ของ form id เหล่านี้ (จับคู่ด้วยรายการคำถาม)")
    pk.add_argument("--from", dest="start", required=True)
    pk.add_argument("--to", dest="end", required=True)
    pk.add_argument("--jobs", type=int, nargs="+", default=DEFAULT_JOBS)
    pk.set_defaults(fn=cmd_peek)

    ex = sub.add_parser("explore", help="ดูข้อมูลหน้าอื่นของเว็บ (GET loader อย่างเดียว) จาก path ที่เห็นใน DevTools")
    ex.add_argument("path", help="เช่น /premium-orders-distribute")
    ex.add_argument("--route", help="route id ใน response .data (ค่าเริ่มต้น routes/_app.<path>._index)")
    ex.add_argument("--param", action="append", help="พารามิเตอร์ k=v เช่น startAt=2026-10-01 (ใส่ได้หลายครั้ง)")
    ex.add_argument("--describe", action="store_true", help="แสดงโครงสร้าง response (ไม่มีค่าจริง)")
    ex.add_argument("--rows", type=int, default=0, help="แสดง N แถวแรก (ตัดข้อมูลส่วนบุคคลแล้ว)")
    ex.add_argument("--save", action="store_true", help="ดึงทุกหน้าแล้วบันทึก CSV ลง out/<project>")
    ex.add_argument("--out", default=DEFAULT_OUT)
    ex.set_defaults(fn=cmd_explore)

    jb = sub.add_parser("jobs", help="รายการ job ของ project (ไว้หาเลข job ใส่ config) - อ่านอย่างเดียว")
    jb.add_argument("--describe", action="store_true", help="แสดงแค่โครงสร้าง response")
    jb.set_defaults(fn=cmd_jobs)

    pj = sub.add_parser("projects", help="โปรเจกต์ทั้งหมดที่บัญชีนี้เข้าได้ (หาเลข organization_id) - อ่านอย่างเดียว")
    pj.add_argument("--active", action="store_true", help="เฉพาะโปรเจกต์ที่ยังไม่หมดอายุ (endAt >= วันนี้)")
    pj.set_defaults(fn=cmd_projects)

    sc = sub.add_parser("scan", help="บัญชีใหม่: หาโปรเจกต์ + job ทั้งหมดที่เข้าได้ แล้วบันทึกลง accounts.json - อ่านอย่างเดียว")
    sc.add_argument("--set", type=int, help="ใส่เลขโปรเจกต์นี้ (+ jobs ที่ไม่ใช่ test) ลง project.json ของบัญชีนี้")
    sc.add_argument("--no-jobs", action="store_true", help="ไม่ดึงรายการ job (เร็วกว่า)")
    sc.add_argument("--all-jobs", action="store_true", help="ดึง job ของโปรเจกต์ที่หมดอายุแล้วด้วย")
    sc.set_defaults(fn=cmd_scan)

    lg = sub.add_parser("login", help="(ผู้ใช้รันเอง) ใส่เบอร์ + รหัส แล้วสแกนโปรเจกต์ที่เข้าได้ - ใช้ครั้งแรก/เปลี่ยนรหัส")
    lg.set_defaults(fn=cmd_login)
    sub.add_parser("doctor", help="ตรวจความพร้อม (Python, keyring, login, API) - ใส่ --org เพื่อทดสอบโปรเจกต์").set_defaults(fn=cmd_doctor)

    rc = sub.add_parser("records", help="คำตอบฟอร์มจาก job-records API (แหล่งหลัก) เป็น CSV แบบยาว")
    rc.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD (วันของงาน = record.date)")
    rc.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    rc.add_argument("--campaign", help="ชื่อ campaign ตามที่เห็นใน CloudViu (ไม่ใส่ = ทุก campaign)")
    rc.add_argument("--jobs", type=int, nargs="+", default=None, help="เฉพาะ job (ไม่ใส่ = ทุก job)")
    rc.add_argument("--pad", type=int, default=3, help="ดึงเผื่อหลังวันสุดท้ายกี่วัน (คนส่งรายงานย้อนหลัง) ค่าเริ่มต้น 3")
    rc.add_argument("--include-names", action="store_true", help="เก็บชื่อพนักงาน (ใช้เมื่อจำเป็นเท่านั้น)")
    rc.add_argument("--include-photos", action="store_true", help="เก็บลิงก์รูป/พิกัด (ค่าเริ่มต้นเก็บแค่จำนวนรูป)")
    rc.add_argument("--no-check", action="store_true", help="ไม่ต้องตรวจความผิดปกติหลังดึง")
    rc.add_argument("--out", default=DEFAULT_OUT)
    rc.set_defaults(fn=cmd_records)

    pf = sub.add_parser("profile", help="วิเคราะห์โปรเจกต์: job / ฟอร์ม / คำถาม / ตัวเลือก / ความหมายราคา (ครั้งแรก + เมื่อฟอร์มเปลี่ยน)")
    pf.add_argument("--days", type=int, default=7, help="ดูย้อนหลังกี่วัน (ค่าเริ่มต้น 7)")
    pf.add_argument("--to", help="วันสุดท้าย YYYY-MM-DD (ค่าเริ่มต้น เมื่อวาน)")
    pf.set_defaults(fn=cmd_profile)

    c1 = sub.add_parser("campaign", help="meta ของ campaign หนึ่ง (ฟอร์ม/คำถาม) - อ่านอย่างเดียว")
    c1.add_argument("id", type=int)
    c1.add_argument("--save", action="store_true", help="บันทึก JSON (ตัดข้อมูลส่วนบุคคลแล้ว) ลง out/<project>")
    c1.add_argument("--out", default=DEFAULT_OUT)
    c1.set_defaults(fn=cmd_campaign)

    a = sub.add_parser("attendance", help="ดึงข้อมูลเข้างาน (attendance) เป็น CSV")
    a.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    a.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    a.add_argument("--describe", action="store_true", help="แสดงแค่โครงสร้าง response หน้า 1 (ไม่มีค่าจริง) ไม่เขียนไฟล์")
    a.add_argument("--include-names", action="store_true", help="เก็บชื่อ/เบอร์/อีเมลพนักงาน (ใช้เมื่อจำเป็นเท่านั้น)")
    a.add_argument("--include-photos", action="store_true", help="เก็บลิงก์รูป (มักเป็นรูปเซลฟี่ใบหน้า - ใช้เมื่อจำเป็นเท่านั้น)")
    a.add_argument("--out", default=DEFAULT_OUT, help=f"โฟลเดอร์ผลลัพธ์ (ค่าเริ่มต้น <repo>/out/{PROJECT})")
    a.add_argument("--no-check", action="store_true", help="ไม่ต้องตรวจความผิดปกติหลังดึง")
    a.set_defaults(fn=cmd_attendance)

    args = p.parse_args()
    if hasattr(args, "jobs") and not args.jobs and not getattr(args, "xlsx", None) and args.fn not in (cmd_records, cmd_profile):
        die(f"ยังไม่มีเลข job ของ project '{PROJECT}' - ใส่ \"jobs\" ใน {CFG_PATH} หรือส่ง --jobs <เลข> "
            "(ดูเลขจาก URL ปุ่ม Export: job-record-download?jobIds=...)")
    args.fn(args)


if __name__ == "__main__":
    main()
