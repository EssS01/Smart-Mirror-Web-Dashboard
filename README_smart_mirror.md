# AI-Based Smart Mirror Web Dashboard

A web-based dashboard for an AI-powered Smart Mirror system that combines live camera monitoring, face recognition, sensor readings, liver health screening, calendar notes, weather, and patient/session saving in one local interface.

The dashboard is designed to run on a Raspberry Pi and can also be opened from another device on the same local network, such as a phone or laptop.

## Project Purpose

The purpose of this project is to provide a smart mirror interface that can support basic non-invasive health monitoring and user identification. The system collects readings from connected sensors, displays them in a clean dashboard, saves patient/session records, and supports liver-related screening using manually entered or PDF-extracted lab values.

This project is intended for educational and graduation-project purposes. It is not a medical device and should not replace professional medical diagnosis.

## Made By

- Eslam Samir
- Nouran Maged

## Main Features

- Web dashboard built with Flask, HTML, CSS, and JavaScript
- Live camera feed through the browser
- Face detection using YOLO
- Face recognition using DeepFace/ArcFace embeddings
- Patient registration and FaceDB management
- Temperature reading and status classification
- Heart-rate and SpO2 reading and status classification
- Skin/color sensing using a color sensor
- Manual liver lab value entry
- Optional PDF lab report extraction
- Rule-based abnormal-range liver screening
- AI liver model prediction using saved `.pkl` model files
- SQLite database for users, lab results, sessions, and calendar notes
- Calendar month view with note saving
- Current date, time, greeting, and weather widgets
- Light/dark mode toggle
- Local-network access from phone/laptop

## Hardware Used

| Component | Model / Sensor Name | Purpose |
|---|---|---|
| Main controller | Raspberry Pi 4B | Runs the dashboard, backend, camera, and sensor services |
| Camera | Raspberry Pi Camera Module v2 / USB camera | Live mirror camera feed and face recognition |
| Temperature sensor | MLX90614 infrared temperature sensor | Measures non-contact object/body temperature |
| Heart-rate sensor | MAX30102 pulse oximeter sensor | Measures heart rate and SpO2 |
| Color sensor | TCS3200 color sensor | Reads reflected RGB/color frequency values |
| Display | HDMI display / monitor behind two-way mirror | Shows the smart mirror dashboard |
| Power/control | Power supply, wiring, and optional fan cooling | Powers and cools the mirror system |

## How It Works

1. The Raspberry Pi runs the Flask backend in `app.py`.
2. The web browser opens the dashboard from `templates/index.html`.
3. JavaScript in `static/dashboard.js` continuously polls backend API routes for live updates.
4. The camera stream is displayed as an MJPEG feed.
5. YOLO detects faces from the camera image.
6. DeepFace/ArcFace compares the detected face with saved users in `FaceDB/`.
7. The MLX90614, MAX30102, and TCS3200 sensors provide health and color readings.
8. The dashboard classifies readings as normal, processing, warning, or unavailable depending on the measured values.
9. Liver lab values can be typed manually or extracted from uploaded PDF reports.
10. The system compares liver values against reference ranges and can run the saved AI liver prediction model.
11. Results are saved locally in the SQLite database file `smart_mirror.db`.

## Project Structure

```text
smart_mirror_web_dashboard/
├── app.py                         # Flask backend and AI/hardware services
├── heartrate_monitor.py           # MAX30102 heart-rate helper module
├── requirements.txt               # Python dependencies
├── run_server.sh                  # Linux/Raspberry Pi run script
├── run_server.bat                 # Windows run script
├── database_schema.sql            # SQLite schema reference
├── view_database.py               # Utility for viewing saved records
├── best.pt                        # YOLO face detection model
├── models/                        # Liver AI model files
│   ├── liver_prediction_model.pkl
│   ├── liver_model_columns.pkl
│   ├── liver_label_encoders.pkl
│   └── liver_training_medians.pkl
├── templates/
│   └── index.html                 # Web dashboard page
├── static/
│   ├── style.css                  # Dashboard styling
│   └── dashboard.js               # Frontend logic and polling
├── FaceDB/                        # Local face database; keep private
├── logs/                          # Local runtime logs; keep private
└── uploads/                       # Uploaded lab PDFs; keep private
```

## Installation on Raspberry Pi

Open a terminal inside the project folder and run:

```bash
python3 -m venv vision_web
source vision_web/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Some Raspberry Pi packages may be easier to install using `apt`:

```bash
sudo apt update
sudo apt install -y python3-picamera2 tesseract-ocr poppler-utils libatlas-base-dev
```

Run the dashboard:

```bash
python3 app.py
```

Open it on the Raspberry Pi:

```text
http://127.0.0.1:5000
```

Open it from another device on the same Wi-Fi:

```text
http://RASPBERRY_PI_IP:5000
```

To find the Raspberry Pi IP address:

```bash
hostname -I
```

## Uploading This Folder to GitHub

First, create an empty repository on GitHub. Then open Git Bash, PowerShell, or Terminal inside the `smart_mirror_web_dashboard` folder and run:

```bash
git init
git branch -M main
git add .
git commit -m "Initial commit - smart mirror web dashboard"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git
git push -u origin main
```

Replace `YOUR_USERNAME` and `YOUR_REPOSITORY_NAME` with your actual GitHub username and repository name.

If the repository already exists locally and already has a remote, use:

```bash
git remote -v
git add .
git commit -m "Add smart mirror web dashboard"
git push
```

## Important Public Repository Notes

Do not upload private or personal data to a public GitHub repository. Keep these files/folders private:

- `FaceDB/` user face images and embeddings
- `FaceDB.rar`
- `smart_mirror.db`
- `logs/`
- `uploads/`
- Any API keys, passwords, or private configuration files

The included `.gitignore` is set up to avoid uploading local databases, logs, uploaded PDFs, cache files, and face data.

## Notes

- The dashboard can still open on Windows without Raspberry Pi sensors, but hardware cards may show offline/unavailable.
- The AI model files are included for project demonstration. If they become too large, use Git LFS or provide a download link instead.
- For best performance on Raspberry Pi, keep the camera resolution reasonable and avoid running unnecessary browser tabs.
