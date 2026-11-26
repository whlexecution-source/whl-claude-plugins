# เพิ่ม / แก้ skill ใน plugin

ตัวอย่าง: เพิ่มระบบ AI deployment (skill `cloudviu-deploy`) ให้ทุกคนใช้ได้

## ขั้นตอน
1. สร้าง branch จาก `main`: `git switch -c feat/cloudviu-deploy`
2. เพิ่มโฟลเดอร์ `plugins/whl-data/skills/<ชื่อ-skill>/SKILL.md` (+ `scripts/` ถ้ามี)
   - frontmatter ต้องมี `description` ที่บอกชัดว่า **ใช้เมื่อไร** (Claude ใช้ข้อความนี้ตัดสินใจเรียก skill)
   - อ้างไฟล์ของ skill เองด้วย `${CLAUDE_SKILL_DIR}` / ไฟล์ของ plugin ด้วย `${CLAUDE_PLUGIN_ROOT}` - ห้าม path ของเครื่องตัวเอง
   - ใช้ connector CloudViu กลาง (`cloudviu-connector/scripts/cloudviu.py`) ไม่เขียน login ใหม่
3. ถ้าเป็นงานที่**เขียนข้อมูล**ลงระบบจริง (เช่น deploy งานใน CloudViu, แก้ชีตจริง):
   - ค่าเริ่มต้นต้องเป็น dry-run แสดงสิ่งที่จะเปลี่ยนก่อน ต้องยืนยัน (`--yes`) ถึงจะเขียน
   - อ่านกลับมาตรวจหลังเขียน และเก็บ log ว่าใครทำอะไรเมื่อไร
   - ทดสอบกับโปรเจกต์ / ชีตทดสอบก่อนเสมอ
4. เพิ่มเลข `version` ใน `plugins/whl-data/.claude-plugin/plugin.json` (เช่น 0.1.0 -> 0.2.0) - ไม่เพิ่ม = ผู้ใช้ไม่ได้อัปเดต
5. ตรวจรูปแบบ: `claude plugin validate . --strict`
6. ทดสอบในเครื่อง: `/plugin marketplace add ./` (path ของ repo) แล้ว `/plugin install whl-data@whl-marketplace`
7. เปิด Pull Request ตาม template - ต้องมีคนที่มีพื้นฐานเทคนิครีวิวก่อน merge

## ห้าม commit
- รหัสผ่าน, token, cookie, API key (ใช้ Credential Manager / env var / secret manager)
- ข้อมูลลูกค้า ชื่อ/เบอร์พนักงาน ไฟล์ผลลัพธ์ (CSV, Excel) ลิงก์รูปเซลฟี่
- config ที่ผูกกับลูกค้ารายใดรายหนึ่ง (เลขโปรเจกต์ลูกค้า, ตาราง mapping) - เก็บไว้ใน repo งานของทีมนั้นแทน

## เลขเวอร์ชัน
- แก้เล็ก/แก้บั๊ก: 0.1.0 -> 0.1.1
- เพิ่ม skill/คำสั่งใหม่: 0.1.x -> 0.2.0
- เปลี่ยนวิธีใช้ที่ผู้ใช้ต้องทำอะไรใหม่: เขียนใน CHANGELOG.md ให้ชัด
