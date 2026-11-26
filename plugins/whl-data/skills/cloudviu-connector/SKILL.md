---
name: cloudviu-connector
description: เชื่อม CloudViu แบบอ่านอย่างเดียว - login บัญชีของผู้ใช้, ดูโปรเจกต์ที่เข้าได้, ดึงคำตอบฟอร์ม (job-records API) และการเข้า-ออกงาน (attendance API) เป็น CSV ใช้เมื่อผู้ใช้ขอข้อมูล/รายงานจาก CloudViu, เพิ่มบัญชี CloudViu, หรือถามว่าบัญชีเข้าโปรเจกต์ไหนได้
---

# CloudViu connector

ดึงข้อมูลจาก CloudViu แบบอ่านอย่างเดียว แหล่งข้อมูลหลักคือ **job-records API** (คำตอบฟอร์ม) และ **attendance API** (เข้า-ออกงาน)
สคริปต์: `${CLAUDE_SKILL_DIR}/scripts/cloudviu.py` (เรียกด้วย `python` + path เต็มเสมอ)

**ห้ามเรียก CloudViu เองด้วย curl / WebFetch / โค้ดที่เขียนสด - ใช้สคริปต์นี้เท่านั้น**

## กฎ
1. **ห้ามรัน `login` / `setup` / `forget` เอง และห้ามอ่านแท็บเทอร์มินัลที่ผู้ใช้ใช้ใส่รหัส** - ผู้ใช้พิมพ์เอง
   ห้ามขอหรือรับรหัสผ่านในแชท ถ้าผู้ใช้เผลอวางมา ให้เตือนให้เปลี่ยนรหัสทันที
2. 1 คน 1 บัญชีของตัวเอง - ไม่ใช้รหัสร่วมกัน (รหัสอยู่ใน Credential Manager ของเครื่องผู้ใช้ `cloudviu:<บัญชี>`)
3. ชื่อคน รูป พิกัด GPS เป็นข้อมูลส่วนบุคคล - ค่าเริ่มต้นตัดทิ้ง `--include-names` / `--include-photos` เมื่อผู้ใช้ขอและงานจำเป็นเท่านั้น
4. อ่านอย่างเดียว / เริ่มจากช่วงวันที่สั้น (1-2 วัน) / ห้ามอัปโหลดไฟล์ผลลัพธ์ไปบริการภายนอก ห้าม commit ไฟล์ผลลัพธ์
5. ข้อมูลผู้ใช้ (บัญชี ผลสแกน) อยู่ที่ `~/.cloudviu/` ไม่อยู่ในโฟลเดอร์ skill - ห้ามคัดลอกไปแจกต่อ

## ติดตั้ง (ครั้งเดียวต่อเครื่อง)
- มากับ plugin `whl-data` - ตรวจความพร้อมทั้งเครื่องด้วย skill `whl-setup` (Python, keyring, CloudViu, Google, Apps Script)
- ต้องมี Python 3.9+ และ keyring: `python -m pip install keyring`
- ที่เก็บผลลัพธ์ (ตามมาตรฐาน skill `whl-workspace`):
  - ใน WHL workspace + `--into <ลูกค้า>-<งาน>` -> `projects/<ลูกค้า>-<งาน>/02_data/<บัญชี>_<org>/` **(ใช้แบบนี้เป็นหลัก)**
  - ใน workspace แต่ไม่ใส่ `--into` -> `_scratch/<บัญชี>_<org>/` (ชั่วคราว)
  - นอก workspace -> `<โฟลเดอร์ที่รันคำสั่ง>/out/` / บังคับที่อื่นด้วย env `CLOUDVIU_OUT_ROOT`

## เริ่มใช้ครั้งแรก (หรือเพิ่มบัญชีใหม่) - Claude ทำตามนี้
1. ถามชื่อบัญชี 1 คำถาม (อังกฤษตัวเล็ก เช่น `somchai`, `ad-hoc`)
2. เปิดคำสั่งในแท็บเทอร์มินัลให้ (run_in_terminal) - **ผู้ใช้พิมพ์เบอร์ + รหัสเอง** ห้ามอ่านแท็บนั้น:
   ```
   python ${CLAUDE_SKILL_DIR}/scripts/cloudviu.py --account <บัญชี> login
   ```
   login ลองเข้าก่อนบันทึก -> เก็บรหัสใน Credential Manager -> สแกนทุกโปรเจกต์ + job ที่บัญชีเข้าได้ -> `~/.cloudviu/accounts.json`
3. เมื่อผู้ใช้บอกว่าเสร็จ: `--account <บัญชี> projects --active` แสดงรายการ แล้วถามว่าจะดูโปรเจกต์ไหน
4. ตรวจความพร้อม: `--account <บัญชี> --org <id> doctor` (Python, keyring, login, job-records, attendance)

## ใช้งานประจำ (ทุกคำสั่งใส่ `--account <บัญชี> --org <เลขโปรเจกต์>` และ `--into <ลูกค้า>-<งาน>` เมื่อดึงข้อมูลเข้างาน)
```
python ${CLAUDE_SKILL_DIR}/scripts/cloudviu.py --account <บัญชี> projects --active
python ${CLAUDE_SKILL_DIR}/scripts/cloudviu.py --account <บัญชี> --org <id> --into <ลูกค้า>-<งาน> records --from 2026-11-24 --to 2026-11-24
python ${CLAUDE_SKILL_DIR}/scripts/cloudviu.py --account <บัญชี> --org <id> --into <ลูกค้า>-<งาน> attendance --from 2026-11-24 --to 2026-11-24
```
- **`records`** = คำตอบฟอร์มจาก job-records API -> CSV แบบยาว 1 แถว = record x คำถาม x SKU x property
  (`date` = วันของงาน, `question`, `type`, `sku`, `product`, `property` เช่น quantity/price/value/choice, `value`)
  - `--campaign "<ชื่อ>"` เฉพาะ campaign / `--jobs <id>` เฉพาะ job / ไม่ใส่ = ทุกอย่างในโปรเจกต์
  - API กรองตามเวลาส่ง ไม่ใช่วันของงาน - สคริปต์ดึงเผื่อ `--pad 3` วันแล้วกรอง `date` เอง (คนส่งย้อนหลังเกิน 3 วันให้เพิ่ม --pad)
  - เป็นข้อมูลปัจจุบันของคำตอบ: SKU ที่ถูกลบออกจากคำตอบจะไม่อยู่ (Export / BigQuery ยังมีแถวค้าง - ผู้ใช้ยืนยันว่า API ถูก)
  - ราคาใน product-offtake บางโปรเจกต์เป็นราคาต่อชิ้น บางโปรเจกต์เป็นยอดรวมบรรทัด - ถามผู้ใช้ก่อนคูณ
- **`attendance`** = เข้า-ออกงานจาก attendance API (ตัดชื่อ รูป พิกัด ชื่อหัวหน้าทิ้งเป็นค่าเริ่มต้น เก็บ userId)
- job ชื่อ "บันทึกการทำงาน" / "เช็คเข้างาน" มักเป็น job เช็คอินอย่างเดียว ข้อมูลฟอร์มอยู่ใน job อื่นของโปรเจกต์
- CloudViu แยกโปรเจกต์รายเดือนบ่อย - ถ้าผู้ใช้หาโปรเจกต์ใหม่ไม่เจอ ให้สแกนใหม่: `--account <บัญชี> scan`

## คำสั่งทั้งหมด
| คำสั่ง | ทำอะไร |
|---|---|
| `login` | (ผู้ใช้รันเอง) ใส่เบอร์ + รหัส แล้วสแกนโปรเจกต์ที่เข้าได้ |
| `doctor` | ตรวจ Python / keyring / login / job-records / attendance |
| `projects [--active]` | โปรเจกต์ที่บัญชีเข้าได้ (id ใช้กับ --org) |
| `scan` | สแกนโปรเจกต์ + job ใหม่ (เมื่อมีโปรเจกต์รายเดือนใหม่) |
| `records --from --to [--campaign/--jobs/--pad]` | คำตอบฟอร์ม (job-records API) |
| `attendance --from --to` | เข้า-ออกงาน (attendance API) |
| `jobs` / `campaigns [--forms]` | รายการ job / campaign / ฟอร์มของโปรเจกต์ |
| `forget` | (ผู้ใช้รันเอง) ลบบัญชีออกจากเครื่อง |

## ข้อควรรู้
- เป็น API ภายในของ CloudViu ไม่ใช่ API ทางการ อาจเปลี่ยนได้ - ถ้าทุกคำสั่ง error พร้อมกัน รัน `doctor` แล้วแจ้งทีม Data (ผู้ดูแล plugin)
- ระบบล้มเงียบ: ไม่เลือกโปรเจกต์ = ข้อมูลว่าง - สคริปต์เช็คให้แล้ว แต่ถ้าผลเป็น 0 ทั้งหมดให้สงสัยไว้ก่อน
- ชื่อสินค้า/ตัวเลือกคำตอบถูกแก้ภายหลังได้ - จับคู่ด้วยรหัส SKU / choice id ไม่ใช้ชื่อ
- ข้อมูลลูกค้าและพนักงาน: ห้ามส่งไฟล์ผลลัพธ์ไปบริการภายนอกที่องค์กรไม่ได้อนุมัติ ตัดข้อมูลที่ไม่จำเป็นก่อนแชร์
