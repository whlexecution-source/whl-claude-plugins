# Changelog

## whl-data 0.2.1 (2026-11-26)
- เปลี่ยนตัวอย่างในโค้ดและเอกสารเป็นชื่อกลาง (brand-a, <บัญชี>, <id>) ไม่มีชื่อลูกค้า

## whl-data 0.2.0 (2026-11-26)
- ใหม่ `whl-workspace`: โครงโฟลเดอร์งานมาตรฐาน `~/WHL/projects/<ลูกค้า>-<งาน>/{01_input,02_data,03_output,scripts}` + NOTES.md + CLAUDE.md กติกาองค์กร
  (คำสั่ง init / new / check / list - ไม่ลบไม่เขียนทับไฟล์เดิม, ปฏิเสธโฟลเดอร์ที่ซิงก์ cloud)
- `cloudviu-connector`: `--into <ลูกค้า>-<งาน>` เขียนผลลัพธ์ลง `02_data/` ของ project / ไม่ใส่ = `_scratch/`
- `whl-setup`: เพิ่มข้อ 0 ตรวจ workspace

## whl-data 0.1.0 (2026-11-26)
- `cloudviu-connector` 2.1.0: login ด้วยบัญชีของผู้ใช้ (`--account`, เก็บที่ `~/.cloudviu`), สแกนโปรเจกต์ที่เข้าได้,
  `records` (job-records API), `attendance` (attendance API, ตัดชื่อ/รูป/พิกัดเป็นค่าเริ่มต้น), `doctor`
- `gas-connect`: อ่าน/แก้ Apps Script ผ่าน Claude in Chrome
- `whl-setup`: checklist ตั้งค่าเครื่อง (Python, CloudViu, Google Drive, Google Sheets, Apps Script, GitHub)
