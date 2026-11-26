---
name: whl-setup
description: Checklist ตั้งค่าเครื่องสำหรับพนักงาน Whiteline - ตรวจว่า Claude Code เชื่อม CloudViu, Google Sheets, Google Drive และ Apps Script (ผ่าน Chrome) ได้ครบหรือยัง แล้วพาแก้ทีละข้อ ใช้เมื่อผู้ใช้พิมพ์ /whl-setup, เครื่องใหม่/พนักงานใหม่, เพิ่งติดตั้ง plugin whl-data, ถามว่า "ตั้งค่าครบยัง", "เชื่อม cloudviu / google ยังไง", หรือเครื่องมือข้อมูลใช้ไม่ได้
argument-hint: "[check | fix]"
---

# WHL setup checklist

เป้าหมาย: ทุกคนในทีมใช้ Claude Code ดึงข้อมูล CloudViu และทำงานกับ Google Sheets / Drive / Apps Script ได้ด้วยบัญชีของตัวเอง
ผู้ใช้ส่วนใหญ่ไม่มีพื้นฐาน IT - อธิบายเป็นภาษาไทยง่ายๆ ทำทีละข้อ และบอกเสมอว่าข้อไหนผู้ใช้ต้องทำเอง

## กฎ
- **ห้ามขอ / รับ / พิมพ์รหัสผ่านแทนผู้ใช้** (CloudViu, Google, GitHub) - ผู้ใช้พิมพ์เองในหน้าต่างของระบบนั้น
- **ห้ามกด Allow / ยอมรับสิทธิ์ OAuth แทนผู้ใช้** - ให้ผู้ใช้อ่านหน้าขอสิทธิ์และกดเอง
- ใช้บัญชีงาน (`@whitelineactivation.com`) เท่านั้น ไม่ใช้ Gmail ส่วนตัว
- เชื่อมเฉพาะ connector ที่องค์กรอนุมัติ ถ้าไม่แน่ใจให้ถามทีม IT ก่อน
- ตรวจแบบอ่านอย่างเดียว ไม่สร้าง/แก้ไฟล์ของผู้ใช้ใน Drive

## ขั้นตอน (ทำตามลำดับ แล้วสรุปเป็นตาราง)

### 0. Workspace มาตรฐาน
- รัน `python ${CLAUDE_PLUGIN_ROOT}/skills/whl-workspace/scripts/workspace.py check`
- ยังไม่มี: ขออนุญาตแล้ว `init` (สร้าง `~/WHL`) แล้วบอกผู้ใช้ให้เปิด Claude Code ที่โฟลเดอร์นั้นในครั้งต่อไป
- มีแต่ไม่ได้เปิด Claude Code ในนั้น: บอกให้เปิดที่ workspace (ไฟล์จะได้ลงที่ถูก)

### 1. เครื่อง (Python + keyring)
- รัน `python --version` (ต้อง 3.9 ขึ้นไป) และ `python -c "import keyring"`
- ไม่มี Python: ให้ผู้ใช้ติดตั้งจาก python.org (ติ๊ก "Add python.exe to PATH") หรือขอทีม IT ติดตั้งให้
- ไม่มี keyring: ขออนุญาตแล้วรัน `python -m pip install keyring`

### 2. CloudViu
- ดูว่ามีบัญชีแล้วหรือยัง: อ่าน `~/.cloudviu/accounts.json` (มีชื่อบัญชี + รายการโปรเจกต์ ไม่มีรหัส)
- ยังไม่มี: ทำตามหัวข้อ "เริ่มใช้ครั้งแรก" ของ skill `cloudviu-connector` (ถามชื่อบัญชี -> เปิดคำสั่ง `login` ในแท็บเทอร์มินัลให้ผู้ใช้พิมพ์รหัสเอง)
- มีแล้ว: เลือกโปรเจกต์ที่ยังไม่หมดอายุ 1 ตัวจาก accounts.json แล้วรัน
  `python ${CLAUDE_PLUGIN_ROOT}/skills/cloudviu-connector/scripts/cloudviu.py --account <บัญชี> --org <id> doctor`
  ผ่านทุกข้อ = ✅

### 3. Google Drive
- ดูว่ามีเครื่องมือของ Google Drive connector (เช่น `search_files`, `read_file_content`) หรือไม่ - ถ้าเป็น deferred tool ให้โหลดด้วย ToolSearch ก่อน
- มี: ทดสอบค้นไฟล์ 1 รายการ (`search_files` ด้วยคำค้นสั้นๆ จำกัด 1 ผลลัพธ์) แล้วรายงานแค่ว่าเจอไฟล์ได้ ไม่ต้องบอกชื่อไฟล์
- ไม่มี / error เรื่องสิทธิ์: ให้ผู้ใช้เปิดแอป Claude > Settings > Connectors > เปิด Google Drive แล้ว sign in ด้วยบัญชีงาน
  (Claude Code ในแอปเดสก์ท็อปใช้ connector ชุดเดียวกับบัญชี claude.ai) จากนั้นเริ่ม session ใหม่แล้วตรวจอีกรอบ

### 4. Google Sheets
- ดูว่ามีเครื่องมือของ Google Sheets connector (เช่น `get_spreadsheet`, `get_values`) หรือไม่
- มี: ถ้าผู้ใช้มีลิงก์ชีตที่ใช้งานประจำ ให้อ่านแค่ชื่อแท็บ (`get_spreadsheet` + fields `sheets.properties.title`) ถ้าไม่มีลิงก์ ถือว่าผ่านเมื่อเครื่องมือโหลดได้
- ไม่มี: เปิด connector Google Sheets แบบเดียวกับข้อ 3

### 5. Apps Script (GAS) ผ่าน Chrome
- ต้องมี Claude in Chrome extension: ลองเรียก `tabs_context_mcp` (โหลดเครื่องมือด้วย ToolSearch ก่อน)
- ไม่เชื่อม: ให้ผู้ใช้ติดตั้ง extension "Claude in Chrome" จาก Chrome Web Store แล้ว sign in บัญชี Claude เดียวกัน
- เชื่อมแล้ว: เปิด `https://script.google.com/u/0/home` แล้วตรวจว่าบัญชีที่ login อยู่เป็น `@whitelineactivation.com`
  (ถ้าเป็น Gmail ส่วนตัว ให้ผู้ใช้สลับบัญชีเอง - trigger ของ GAS จะรันด้วยสิทธิ์ของคนที่ติดตั้ง)
- การแก้โค้ด GAS ใช้ skill `gas-connect`

### 6. GitHub (เฉพาะคนที่จะแก้/เพิ่ม skill ใน plugin)
- `gh auth status` - ยังไม่ login: ให้ผู้ใช้รัน `gh auth login` เอง แล้ว `gh auth setup-git`
- ผู้ใช้ทั่วไปข้ามข้อนี้ได้ (การอัปเดต plugin ใช้แค่สิทธิ์อ่าน repo)

## สรุปผล
แสดงตารางเดียว แล้วบอกข้อที่ต้องทำต่อ (ข้อที่ผู้ใช้ต้องทำเองให้เขียนชัดๆ):

| # | รายการ | สถานะ | ต้องทำต่อ |
|---|---|---|---|
| 0 | Workspace มาตรฐาน (`~/WHL`) | ✅ / ❌ | ... |
| 1 | Python + keyring | ✅ / ❌ | ... |
| 2 | CloudViu (บัญชี, doctor) | ✅ / ❌ | ... |
| 3 | Google Drive | ✅ / ❌ | ... |
| 4 | Google Sheets | ✅ / ❌ | ... |
| 5 | Apps Script ผ่าน Chrome (บัญชีงาน) | ✅ / ❌ | ... |
| 6 | GitHub (เฉพาะผู้พัฒนา) | ✅ / ➖ ข้าม | ... |

ปิดท้ายด้วย: การอัปเดต plugin ทำได้ด้วย `/plugin marketplace update whl-marketplace` และงานที่แตะรหัสผ่าน สิทธิ์ หรือการเชื่อมระบบภายนอก ควรให้ทีม IT ตรวจสอบก่อนใช้งานจริง
