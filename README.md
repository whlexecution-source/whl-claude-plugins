# WHL Claude Code plugins

ปลั๊กอิน Claude Code ภายใน Whiteline Activation - ติดตั้งครั้งเดียว อัปเดตจาก repo นี้ได้ทุกคน

| plugin | มีอะไร |
|---|---|
| `whl-data` | `whl-setup` (checklist ตั้งค่าเครื่อง), `whl-workspace` (โครงโฟลเดอร์งานมาตรฐาน), `cloudviu-connector` (CloudViu job-records + attendance API), `gas-connect` (แก้ Apps Script ผ่าน Chrome) |

## ติดตั้ง (ผู้ใช้ทั่วไป)
1. ขอสิทธิ์อ่าน repo นี้จากทีม Data (repo เป็น private)
2. ให้เครื่องดึง repo ได้: ติดตั้ง GitHub CLI แล้วรันเองในเทอร์มินัล
   ```
   gh auth login
   gh auth setup-git
   ```
3. ใน Claude Code พิมพ์
   ```
   /plugin marketplace add whlexecution-source/whl-claude-plugins
   /plugin install whl-data@whl-marketplace
   ```
4. เริ่ม session ใหม่ แล้วพิมพ์ `/whl-data:whl-setup` (หรือบอก Claude ว่า "ตรวจการตั้งค่า WHL") - Claude จะตรวจทีละข้อและพาแก้

## Checklist ที่ทุกคนต้องผ่าน
- [ ] Workspace มาตรฐาน `~/WHL` (เปิด Claude Code ที่นี่เสมอ) - ดูโครงด้านล่าง
- [ ] Python 3.9+ และ `keyring`
- [ ] CloudViu: login ด้วยบัญชีของตัวเอง (`--account <ชื่อ> login`) แล้ว `doctor` ผ่าน
- [ ] Google Drive connector (บัญชี @whitelineactivation.com)
- [ ] Google Sheets connector (บัญชี @whitelineactivation.com)
- [ ] Claude in Chrome extension + script.google.com เปิดด้วยบัญชีงาน (สำหรับ Apps Script)
- [ ] (เฉพาะผู้พัฒนา) `gh auth status` ผ่าน

## โครงโฟลเดอร์งานมาตรฐาน (เหมือนกันทุกคน)
```
~/WHL/                          เปิด Claude Code ที่นี่
├─ CLAUDE.md                    กติกาสำหรับ Claude
├─ projects/<ลูกค้า>-<งาน>/       เช่น brand-a-osa
│   ├─ 01_input/   ไฟล์ที่ได้รับ
│   ├─ 02_data/    ข้อมูลดิบที่ดึงมา (CloudViu --into <ลูกค้า>-<งาน>)
│   ├─ 03_output/  ไฟล์ส่งงาน
│   ├─ scripts/    สคริปต์ / Apps Script
│   └─ NOTES.md    ข้อมูลอ้างอิงของงาน
├─ templates/
└─ _scratch/       ไฟล์ชั่วคราว
```
ชื่อไฟล์: `<ลูกค้า>_<เนื้อหา>_<YYYY-MM-DD>.xlsx` / ห้ามเก็บ workspace ใน OneDrive หรือ Drive ที่ซิงก์

## อัปเดต
```
/plugin marketplace update whl-marketplace
```
หรือเปิดอัปเดตอัตโนมัติ: `/plugin` > Marketplaces > whl-marketplace > Enable auto-update
plugin จะอัปเดตเมื่อเลข `version` ใน `plugins/whl-data/.claude-plugin/plugin.json` เปลี่ยน

## ทั้งทีมใน repo งานเดียวกัน (ไม่บังคับ)
ใส่ใน `.claude/settings.json` ของ repo งาน แล้ว Claude Code จะเสนอให้ติดตั้งเมื่อเปิดโฟลเดอร์นั้น
```json
{
  "extraKnownMarketplaces": {
    "whl-marketplace": { "source": { "source": "github", "repo": "whlexecution-source/whl-claude-plugins" } }
  },
  "enabledPlugins": { "whl-data@whl-marketplace": true }
}
```

## ความปลอดภัย
- repo นี้ **ห้ามมี** รหัสผ่าน token cookie ข้อมูลลูกค้า หรือไฟล์ผลลัพธ์ (CSV/Excel) - ดู `.gitignore`
- รหัส CloudViu อยู่ใน Credential Manager ของแต่ละเครื่อง / ข้อมูลบัญชีอยู่ที่ `~/.cloudviu/` ไม่อยู่ใน repo
- ทุกการแก้ต้องผ่าน Pull Request และให้คนที่มีพื้นฐานเทคนิครีวิวก่อน merge
- ควรให้ทีม IT ตรวจสอบก่อนใช้งานจริง
