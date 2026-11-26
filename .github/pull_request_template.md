## เปลี่ยนอะไร


## Checklist
- [ ] ไม่มีรหัสผ่าน / token / cookie / ชื่อลูกค้า / เลขโปรเจกต์ลูกค้า / ไฟล์ผลลัพธ์ใน PR นี้ (repo เป็น public)
- [ ] เพิ่ม `version` ใน `plugins/whl-data/.claude-plugin/plugin.json` แล้ว
- [ ] `claude plugin validate . --strict` ผ่าน
- [ ] ทดสอบติดตั้งจาก local marketplace แล้ว
- [ ] ถ้าเขียนข้อมูลลงระบบจริง: มี dry-run, ยืนยันก่อนเขียน, ทดสอบกับโปรเจกต์/ชีตทดสอบแล้ว
- [ ] อัปเดต CHANGELOG.md
