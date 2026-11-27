#!/usr/bin/env python3
"""WHL workspace มาตรฐาน - สร้าง/ตรวจโครงโฟลเดอร์ให้ทุกคนเหมือนกัน (ไม่ลบ ไม่เขียนทับไฟล์ที่มีอยู่)

  python workspace.py init [--root <path>]            สร้าง workspace (ค่าเริ่มต้น ~/WHL)
  python workspace.py new <client>-<project> [--root]  สร้างโฟลเดอร์ project ตามมาตรฐาน
  python workspace.py check [--root]                   ตรวจว่าโครงครบ บอกสิ่งที่ขาด
  python workspace.py list [--root]                    รายชื่อ project
  python workspace.py add <ไฟล์> <client>-<project>    คัดลอกไฟล์ที่ได้รับเข้า 01_input (ไม่ย้าย ไม่เขียนทับ)
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(os.path.dirname(HERE), "templates")
MARKER = ".whl-workspace"
VERSION = 1
TOP_DIRS = ["projects", "templates", "_scratch"]
PROJECT_DIRS = {
    "01_input": "ไฟล์ที่ได้รับ (brief, master จากลูกค้า, ไฟล์ที่ทีมส่งมา) - เก็บตามที่ได้รับ ไม่แก้",
    "02_data": "ข้อมูลดิบที่ดึงมา (CloudViu, BigQuery, Export) - สคริปต์เขียนให้ ห้ามแก้มือ",
    "03_output": "ไฟล์ส่งงาน (รายงาน Excel/PDF/สไลด์) - ตั้งชื่อตามมาตรฐาน",
    "scripts": "สคริปต์ / โค้ด Apps Script ของ project นี้",
}
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*-[a-z0-9][a-z0-9-]*")


def default_root():
    return os.environ.get("WHL_WORKSPACE") or os.path.join(os.path.expanduser("~"), "WHL")


def find_root(start=None):
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, MARKER)):
            return d
        if os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def resolve_root(arg):
    return os.path.abspath(arg) if arg else (find_root() or default_root())


def copy_if_missing(src, dst):
    if os.path.exists(dst):
        return False
    shutil.copyfile(src, dst)
    return True


def cmd_init(a):
    root = resolve_root(a.root)
    low = root.lower()
    if any(k in low for k in ("onedrive", "google drive", "googledrive", "dropbox", "my drive")):
        print(f"!! {root} อยู่ในโฟลเดอร์ที่ซิงก์ขึ้น cloud - ข้อมูลดิบมีข้อมูลลูกค้า/พนักงาน ควรใช้โฟลเดอร์ในเครื่อง เช่น ~/WHL")
        if not a.force:
            sys.exit(1)
    os.makedirs(root, exist_ok=True)
    made = []
    for d in TOP_DIRS:
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            os.makedirs(p)
            made.append(d + "/")
    if copy_if_missing(os.path.join(TEMPLATES, "CLAUDE.md"), os.path.join(root, "CLAUDE.md")):
        made.append("CLAUDE.md")
    if copy_if_missing(os.path.join(TEMPLATES, "WORKSPACE_README.md"), os.path.join(root, "README.md")):
        made.append("README.md")
    mk = os.path.join(root, MARKER)
    if not os.path.exists(mk):
        with open(mk, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "created": dt.date.today().isoformat()}, f)
        made.append(MARKER)
    print(f"workspace: {root}")
    print("สร้างใหม่: " + (", ".join(made) if made else "- (มีครบอยู่แล้ว ไม่ได้แก้อะไร)"))
    print(f"\nเปิด Claude Code ที่โฟลเดอร์นี้เสมอ: {root}")


def cmd_new(a):
    root = resolve_root(a.root)
    if not os.path.isfile(os.path.join(root, MARKER)):
        sys.exit(f"ERROR: {root} ยังไม่ใช่ workspace - รัน init ก่อน")
    name = a.name.strip().lower()
    if not NAME_RE.fullmatch(name):
        sys.exit("ERROR: ชื่อต้องเป็น <ลูกค้า>-<งาน> ตัวอังกฤษเล็ก ตัวเลข และ - เช่น brand-a-osa, brand-b-sales, brand-c-sampling")
    p = os.path.join(root, "projects", name)
    existed = os.path.isdir(p)
    for d in PROJECT_DIRS:
        os.makedirs(os.path.join(p, d), exist_ok=True)
    notes = os.path.join(p, "NOTES.md")
    if not os.path.exists(notes):
        with open(os.path.join(TEMPLATES, "PROJECT_NOTES.md"), encoding="utf-8") as f:
            txt = f.read().replace("{{name}}", name).replace("{{date}}", dt.date.today().isoformat())
        with open(notes, "w", encoding="utf-8") as f:
            f.write(txt)
    print(("มีอยู่แล้ว - เติมโฟลเดอร์ที่ขาด: " if existed else "สร้างแล้ว: ") + p)
    for d, why in PROJECT_DIRS.items():
        print(f"  {d:<10} {why}")
    print(f"  NOTES.md   ข้อมูลอ้างอิงของ project (กรอกให้ครบ)")
    print(f"\nดึงข้อมูล CloudViu ลง 02_data: ใส่ --into {name} ในคำสั่ง cloudviu.py")


def cmd_check(a):
    root = resolve_root(a.root)
    issues = []
    if not os.path.isfile(os.path.join(root, MARKER)):
        issues.append(f"ไม่มี {MARKER} (ยังไม่ได้ init)")
    for d in TOP_DIRS:
        if not os.path.isdir(os.path.join(root, d)):
            issues.append(f"ไม่มีโฟลเดอร์ {d}/")
    if not os.path.isfile(os.path.join(root, "CLAUDE.md")):
        issues.append("ไม่มี CLAUDE.md")
    pdir = os.path.join(root, "projects")
    for n in sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []:
        pp = os.path.join(pdir, n)
        if not os.path.isdir(pp):
            issues.append(f"projects/{n} ไม่ใช่โฟลเดอร์ (ไฟล์ควรอยู่ใน project)")
            continue
        if not NAME_RE.fullmatch(n):
            issues.append(f"projects/{n}: ชื่อไม่ตามมาตรฐาน <ลูกค้า>-<งาน>")
        miss = [d for d in PROJECT_DIRS if not os.path.isdir(os.path.join(pp, d))]
        if miss:
            issues.append(f"projects/{n}: ขาด {', '.join(miss)} (รัน new {n} เพื่อเติม)")
        if not os.path.isfile(os.path.join(pp, "NOTES.md")):
            issues.append(f"projects/{n}: ไม่มี NOTES.md")
    loose = [f for f in os.listdir(root) if os.path.isfile(os.path.join(root, f))
             and f not in (MARKER, "CLAUDE.md", "README.md")] if os.path.isdir(root) else []
    if loose:
        issues.append(f"มีไฟล์วางที่ root {len(loose)} ไฟล์ - ย้ายเข้า projects/<ชื่อ>/ หรือ _scratch/")
    print(f"workspace: {root}")
    print("OK - โครงครบตามมาตรฐาน" if not issues else "ต้องแก้:\n  - " + "\n  - ".join(issues))
    sys.exit(0 if not issues else 2)


def cmd_list(a):
    root = resolve_root(a.root)
    pdir = os.path.join(root, "projects")
    names = sorted(n for n in os.listdir(pdir) if os.path.isdir(os.path.join(pdir, n))) if os.path.isdir(pdir) else []
    print(f"workspace: {root} ({len(names)} project)")
    for n in names:
        print("  " + n)


def cmd_add(a):
    root = resolve_root(a.root)
    src = os.path.abspath(a.file)
    if not os.path.isfile(src):
        sys.exit(f"ERROR: ไม่พบไฟล์ {src}")
    pdir = os.path.join(root, "projects", a.project)
    if not os.path.isdir(pdir):
        sys.exit(f"ERROR: ไม่มี project {a.project} - สร้างด้วย `new {a.project}` ก่อน")
    client = a.project.split("-")[0]
    stem, ext = os.path.splitext(os.path.basename(src))
    stem = re.sub(r'[\\/:*?"<>|]+', "", stem).strip()
    stem = re.sub(r"\s+", "-", a.name or stem).strip("-_") or "file"
    today = dt.date.today().isoformat()
    has_date = re.search(r"\d{4}-\d{2}-\d{2}", stem)
    base = stem if stem.lower().startswith(client + "_") else f"{client}_{stem}"
    base = base if has_date else f"{base}_{today}"
    dst = os.path.join(pdir, "01_input", base + ext.lower())
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(pdir, "01_input", f"{base}_v{n}{ext.lower()}")
        n += 1
    shutil.copy2(src, dst)
    notes = os.path.join(pdir, "NOTES.md")
    line = f"- {today}: {os.path.basename(dst)} (ต้นฉบับ: {os.path.basename(src)})"
    if os.path.exists(notes):
        with open(notes, encoding="utf-8") as f:
            txt = f.read()
        if "## ไฟล์ที่ได้รับ" not in txt:
            txt = txt.rstrip() + "\n\n## ไฟล์ที่ได้รับ\n"
        with open(notes, "w", encoding="utf-8") as f:
            f.write(txt.rstrip() + "\n" + line + "\n")
    print(f"คัดลอกแล้ว: {dst}")
    print(f"ต้นฉบับยังอยู่ที่เดิม: {src}")


def main():
    p = argparse.ArgumentParser(description="WHL workspace มาตรฐาน")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--root")
    i.add_argument("--force", action="store_true", help="ยอมให้อยู่ในโฟลเดอร์ที่ซิงก์ cloud")
    i.set_defaults(fn=cmd_init)
    n = sub.add_parser("new")
    n.add_argument("name")
    n.add_argument("--root")
    n.set_defaults(fn=cmd_new)
    ad = sub.add_parser("add")
    ad.add_argument("file")
    ad.add_argument("project")
    ad.add_argument("--name", help="ชื่อเนื้อหาไฟล์ (ไม่ใส่ = ใช้ชื่อเดิม)")
    ad.add_argument("--root")
    ad.set_defaults(fn=cmd_add)
    for name, fn in (("check", cmd_check), ("list", cmd_list)):
        s = sub.add_parser(name)
        s.add_argument("--root")
        s.set_defaults(fn=fn)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
