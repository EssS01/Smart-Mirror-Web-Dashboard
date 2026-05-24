AI Smart Mirror Web Dashboard
=============================

This folder is the Flask/local-network version of your PyQt smart mirror dashboard.
It keeps the same backend logic: MLX90614 temperature, MAX30102 heart rate/SpO2,
TCS3200 color sensing, sensor fusion, weather, YOLO + DeepFace face recognition,
FaceDB register/update/delete, manual lab entry, PDF lab extraction, rule-based
liver screening, and the liver AI .pkl model prediction.

Included files
--------------
app.py                              Flask backend and hardware/AI services
templates/index.html                Web dashboard UI
static/style.css                    Dashboard styling
static/dashboard.js                 Browser polling and controls
best.pt                             YOLO face model copied from your upload
heartrate_monitor.py                MAX30102 heart-rate module copied from your upload
models/liver_prediction_model.pkl   Liver AI model
models/liver_model_columns.pkl      Liver model columns
models/liver_label_encoders.pkl     Liver label encoders
models/liver_training_medians.pkl   Liver training medians
FaceDB/                             Put your saved users here
logs/                               Recognition logs
uploads/                            Uploaded lab PDFs

Important
---------
Your FaceDB folder was not included in the uploaded files I received here, so this
package contains an empty FaceDB folder. To keep your registered users, copy your
old FaceDB folder into this web dashboard folder and replace the empty one.

Raspberry Pi setup
------------------
1) Copy this full folder to the Raspberry Pi.
2) Open a terminal inside the folder.
3) Create and activate a virtual environment, for example:

   python3 -m venv vision_web
   source vision_web/bin/activate

4) Install Python packages:

   pip install --upgrade pip
   pip install -r requirements.txt

5) Some Raspberry Pi camera/GPIO packages are usually better installed with apt,
   not pip. If imports fail, install these too:

   sudo apt update
   sudo apt install -y python3-picamera2 tesseract-ocr poppler-utils libatlas-base-dev

6) Run:

   python3 app.py

7) Open it on the Pi:

   http://127.0.0.1:5000

8) Open it from another phone/laptop on the same Wi-Fi/local network:

   http://RASPBERRY_PI_IP:5000

   Example:
   http://192.168.1.25:5000

Finding the Pi IP
-----------------
Run this on the Pi:

   hostname -I

Notes
-----
- The backend binds to 0.0.0.0, so other devices on the same network can access it.
- The camera feed is served as MJPEG at /video_feed.
- The face crop preview is served as MJPEG at /face_crop_feed.
- Sensor readings are polled by the browser from /api/status about every 600 ms.
- If you run this on a Windows laptop without Pi sensors, the page still opens and
  the hardware cards will show Offline/Unavailable until you run it on the Pi.
- If the liver .pkl model fails to load, install the same scikit-learn/joblib versions
  used when training the model, or retrain/export again using the Pi environment.

NEW DATABASE / SAVE-FLOW UPDATE
===============================
This version adds a local SQLite database file named smart_mirror.db.
It is created automatically the first time you run app.py.

New saved data includes:
- user name
- date of birth and calculated age
- gender
- symptom tags selected from the dashboard dropdown (for example headache, nausea, fatigue)
- FaceDB path
- number of face pictures saved/taken
- camera recognition status and distance/confidence
- temperature reading and range
- heart-rate reading and range
- SpO2 reading and range
- TCS3200 color values
- sensor-fusion result
- manual/PDF liver readings when available
- optional AI liver prediction
- optional notes
- timestamps for camera, temperature, heart, and final save

Saving order:
1) Start Checkup
2) Capture Camera
3) Capture Temperature
4) Capture Heart
5) Save to Database

PDF upload is optional. You can save the database record without uploading a PDF and without entering liver lab values.

FaceDB note:
Your uploaded FaceDB.rar is included in this package as FaceDB.rar. Extract it on the Raspberry Pi so the real folder becomes:
  smart_mirror_web_dashboard/FaceDB/<person_name>/images
  smart_mirror_web_dashboard/FaceDB/<person_name>/embedding.npy


UPDATE IN THIS VERSION
----------------------
- The Summary tab now includes Calendar, Current Date & Time, and Current Weather widgets.
- A global Light/Dark mode toggle was added in the top-right header. It works from any tab and remembers the selected theme in the browser.

SYMPTOMS UPDATE IN THIS VERSION
-------------------------------
- Added a Symptoms multi-select dropdown in the Patient & Save Flow card.
- You can select multiple symptoms such as Headache, Nausea, Fatigue, Fever, Abdominal pain, Yellow skin/eyes, Dark urine, and more.
- Selected symptoms appear as tags on the dashboard.
- SQLite now stores symptom_tags_json in both users and measurement_sessions.
- The user row remembers the latest selected symptoms, while each saved checkup stores its own symptoms snapshot.
- Saving to the DB still works with or without PDF/lab values.

2026-05-22 UI update:
- Added a new Home tab designed as a smart-mirror screen.
- Home tab uses a fully black background with only time/date, calendar status, and weather widgets.
- Removed calendar, time, and weather widgets from the Sensors / Camera / Labs tab.
- Summary tab still keeps the dashboard summary cards and database workflow.
- Light/dark mode still works on the normal dashboard tabs; Home stays black to match the mirror-style display.

2026-05-22 HOME CALENDAR UPDATE:
- The Home tab calendar is now a full month-view calendar.
- Today is highlighted clearly.
- Days with public/calendar events show an event dot.
- Days with saved notes show a note dot.
- The backend generates Egypt/public calendar events locally when possible, including fixed public holidays and Hijri events such as Ramadan, Eid al-Fitr, Arafat Day, and Eid al-Adha.
- Optional packages `holidays` and `hijridate` improve public/Hijri event generation. If they are not installed, the app still opens with built-in fixed events.
- A note can be added to any specific day from the Home tab. Notes are stored in the SQLite table `calendar_notes`.
- A greeting message now changes automatically by time of day: Good morning, Good afternoon, Good evening, or Good night.

2026-05-22 HOME CALENDAR FIX:
- Home screen was compacted so the smart-mirror Home page fits in one viewport without vertical scrolling on normal laptop/Raspberry Pi displays.
- Islamic calendar events are now generated even if `hijridate` is not installed, using a built-in tabular Hijri fallback.
- Ramadan begins, Laylat al-Qadr, Eid al-Fitr, Arafat Day, Eid al-Adha, Islamic New Year, and Prophet's Birthday are marked on the month calendar.
- Islamic dates can shift by official moon sighting, so treat these as dashboard reminders.

UPDATE - 12-HOUR TIME FORMAT
----------------------------
The Home and Summary tabs now display time in 12-hour AM/PM format.
Home uses a short format such as 6:05 PM, while Summary uses a detailed format such as 6:05:30 PM.

- Home page font color changed to pure white for better smart-mirror readability.

REMOTE CONTROL / PHONE + PI DISPLAY UPDATE
==========================================
This version supports two browser roles:

1) Raspberry Pi display screen:
   http://127.0.0.1:5000/display

2) Phone/tablet controller on the same Wi-Fi:
   http://RASPBERRY_PI_IP:5000/controller

Use the phone controller normally. The Raspberry Pi display follows the controller:
- selected tab/screen
- light/dark mode
- selected user/profile fields
- symptoms
- notes/lab form text
- Home calendar month/day selection
- workflow action messages

This is not HDMI/screen mirroring. It is local-network dashboard synchronization.
The Raspberry Pi remains the backend/server, so camera and sensor readings still come from the Pi.
