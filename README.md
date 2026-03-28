# PTZ Web Control Project (Axis + Hikvision, API only)

โปรเจคนี้เป็นเว็บควบคุมกล้อง PTZ ที่ใช้ vendor API โดยตรง
- Axis ใช้ VAPIX
- Hikvision ใช้ ISAPI

ฟังก์ชันหลัก
- ปุ่มลูกศรด้านขวา กดค้างเพื่อแพน/ทิลต์
- Mouse wheel เพื่อซูมเข้า/ออก
- คลิกบนภาพเพื่อให้ตำแหน่งที่คลิกถูกเลื่อนเข้าใกล้กึ่งกลางภาพ
- ทดสอบการเชื่อมต่อกล้อง
- เพิ่ม/ลบ/แก้ไขกล้องผ่านหน้าเว็บ
- ดึง snapshot ผ่าน backend เพื่อเลี่ยงปัญหา CORS และ auth ฝั่ง browser

## วิธีติดตั้ง

```bash
python -m venv .venv
source .venv/bin/activate   # Windows ใช้ .venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```

เปิดใช้งานที่

```bash
http://127.0.0.1:5000
```

## หมายเหตุสำคัญ

### 1) การคลิกบนภาพเพื่อจัดกลาง
ฟังก์ชันนี้ใช้วิธีคำนวณจากพิกัดคลิกบนภาพ แล้วสั่ง continuous move ตามระยะจากกึ่งกลาง พร้อม auto-stop ตามเวลา

จุดเด่น
- ไม่ต้องใช้ ONVIF
- ทำงานได้ทั้ง Axis และ Hikvision ผ่าน abstraction เดียวกัน

ข้อจำกัด
- ยังไม่ใช่ pixel-perfect auto-centering
- ความแม่นขึ้นกับมุมภาพ, optical zoom, FOV และสปีด PTZ ของแต่ละรุ่น
- หากต้องการแม่นมากขึ้น ควรเพิ่ม calibration ต่อรุ่น หรือดึง field-of-view/position feedback มาใช้

### 2) Snapshot / Live view
หน้าเว็บนี้ใช้ snapshot refresh ทุก ~700 ms เพื่อให้ setup ง่ายที่สุด

ถ้าต้องการภาพลื่นกว่า:
- Axis บางรุ่นใช้ MJPEG ได้
- หรือให้ backend แปลง RTSP เป็น HLS / WebRTC ผ่าน MediaMTX หรือ go2rtc

### 3) Axis API ที่ใช้
- snapshot: `/axis-cgi/jpg/image.cgi?camera=1`
- PTZ move: `/axis-cgi/com/ptz.cgi?camera=1&continuouspantiltmove=x,y`
- zoom: `/axis-cgi/com/ptz.cgi?camera=1&continuouszoommove=z`

### 4) Hikvision API ที่ใช้
ค่าเริ่มต้นในโปรเจคนี้ใช้ endpoint ยอดนิยมของ ISAPI:
- snapshot: `/ISAPI/Streaming/channels/101/picture` เมื่อ channel=1
- PTZ: `/ISAPI/PTZCtrl/channels/1/continuous`

บาง firmware หรือบางรุ่นอาจต่างกันเล็กน้อย จึงเผื่อ `snapshot_path` ไว้ให้แก้เองจากหน้าเว็บได้

## ปรับแต่งเพิ่มได้ทันที
- เพิ่ม preset
- เพิ่ม patrol
- เปลี่ยน snapshot เป็น MJPEG/HLS/WebRTC
- เพิ่ม login / role-based access
- เก็บรหัสผ่านแบบเข้ารหัส
- ทำ calibration สำหรับ click-to-center แยกต่อกล้อง
# cctv-ptz-api
