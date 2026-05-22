import os
import re
import csv
import json
import math
import time
import shutil
import base64
import threading
import urllib.parse
import urllib.request
import sqlite3
import calendar as py_calendar
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

# =========================
# CONFIG
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FACE_DB_PATH = os.path.join(BASE_DIR, "FaceDB")
MODEL_DIR = os.path.join(BASE_DIR, "models")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_CSV = os.path.join(LOGS_DIR, "recognition_log.csv")
DB_PATH = os.path.join(BASE_DIR, "smart_mirror.db")

LIVER_MODEL_PATH = os.path.join(MODEL_DIR, "liver_prediction_model.pkl")
LIVER_COLUMNS_PATH = os.path.join(MODEL_DIR, "liver_model_columns.pkl")
LIVER_ENCODERS_PATH = os.path.join(MODEL_DIR, "liver_label_encoders.pkl")
LIVER_MEDIANS_PATH = os.path.join(MODEL_DIR, "liver_training_medians.pkl")

WEATHER_LOCATION = "Cairo, Egypt"
SENSOR_REFRESH_MS = 450
WEATHER_REFRESH_MS = 15 * 60 * 1000
CAMERA_REFRESH_MS = 33
CAMERA_SIZE = (480, 270)

HR_NORMAL_MIN = 60.0
HR_NORMAL_MAX = 100.0
TEMP_NORMAL_MIN = 36.1
TEMP_NORMAL_MAX = 37.2
MLX_CAL_OFFSET = 2.0
MLX_SAMPLES = 5
MLX_SAMPLE_DELAY = 0.05
MLX_MIN_VALID_RAW = 26.0
MLX_MIN_DELTA_OVER_AMBIENT = 1.0
SPO2_NORMAL_MIN = 95.0
SPO2_LOW_MIN = 90.0

STATUS_FAIL = "fail"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

LAB_FIELDS = [
    ("age", "Age"),
    ("gender", "Gender"),
    ("alkaline_phosphatase", "Alkaline Phosphatase"),
    ("aspartate_aminotransferase", "Aspartate Aminotransferase (AST/SGOT)"),
    ("alanine_aminotransferase", "Alanine Aminotransferase (ALT/SGPT)"),
    ("total_bilirubin", "Total Bilirubin"),
    ("direct_bilirubin", "Direct Bilirubin"),
    ("albumin", "Albumin"),
    ("total_proteins", "Total Proteins"),
    ("albumin_globulin_ratio", "Albumin/Globulin Ratio"),
]

LAB_REFERENCE_RANGES = {
    "alkaline_phosphatase": {"label": "Alkaline Phosphatase", "low": 40.0, "high": 129.0},
    "aspartate_aminotransferase": {"label": "AST", "low": 8.0, "high": 48.0},
    "alanine_aminotransferase": {"label": "ALT", "low": 7.0, "high": 55.0},
    "total_bilirubin": {"label": "Total Bilirubin", "low": 0.1, "high": 1.2},
    "direct_bilirubin": {"label": "Direct Bilirubin", "low": 0.0, "high": 0.3},
    "albumin": {"label": "Albumin", "low": 3.5, "high": 5.0},
    "total_proteins": {"label": "Total Proteins", "low": 6.3, "high": 7.9},
    "albumin_globulin_ratio": {"label": "A/G Ratio", "low": 1.0, "high": 2.5},
}

# Symptoms shown in the web dashboard multi-select. Stored as JSON tags in SQLite.
SYMPTOM_OPTIONS = [
    "Headache",
    "Nausea",
    "Fatigue",
    "Fever",
    "Dizziness",
    "Weakness",
    "Vomiting",
    "Loss of appetite",
    "Abdominal pain",
    "Yellow skin/eyes",
    "Dark urine",
    "Pale stool",
    "Itching",
    "Shortness of breath",
    "Chest pain",
    "Sweating",
    "Chills",
    "Other",
]

MODEL_FEATURE_TO_LAB_KEY = {
    "Age of the patient": "age",
    "Gender of the patient": "gender",
    "Total Bilirubin": "total_bilirubin",
    "Direct Bilirubin": "direct_bilirubin",
    "Alkphos Alkaline Phosphotase": "alkaline_phosphatase",
    "Sgpt Alamine Aminotransferase": "alanine_aminotransferase",
    "Sgot Aspartate Aminotransferase": "aspartate_aminotransferase",
    "Total Protiens": "total_proteins",
    "ALB Albumin": "albumin",
    "A/G Ratio Albumin and Globulin Ratio": "albumin_globulin_ratio",
}

PDF_PARAMETER_ALIASES = {
    "total_bilirubin": [
        r"Total\s+Bilirubin", r"Bilirubin\s+Total", r"T\.?\s*Bilirubin", r"Bilirubin\s*\(\s*Total\s*\)",
    ],
    "direct_bilirubin": [
        r"Direct\s+Bilirubin", r"Bilirubin\s+Direct", r"D\.?\s*Bilirubin", r"Bilirubin\s*\(\s*Direct\s*\)",
    ],
    "alkaline_phosphatase": [
        r"Alkaline\s+Phosphatase", r"Alkaline\s+Phosphotase", r"Alk\.?\s*Phos", r"ALP", r"ALKP", r"Alkphos",
    ],
    "alanine_aminotransferase": [
        r"SGPT", r"ALT", r"Alanine\s+Aminotransferase", r"Alamine\s+Aminotransferase",
    ],
    "aspartate_aminotransferase": [
        r"SGOT", r"AST", r"Aspartate\s+Aminotransferase",
    ],
    "total_proteins": [
        r"Total\s+Protein", r"Total\s+Proteins", r"Total\s+Protiens", r"T\.?\s*Protein",
    ],
    "albumin": [
        r"Albumin", r"ALB",
    ],
    "albumin_globulin_ratio": [
        r"A\s*/\s*G\s+Ratio", r"A-G\s+Ratio", r"AG\s+Ratio", r"Albumin\s*/\s*Globulin\s+Ratio",
    ],
}

for path in (FACE_DB_PATH, MODEL_DIR, UPLOAD_DIR, LOGS_DIR):
    os.makedirs(path, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

# =========================
# SQLITE DATABASE
# =========================
_db_init_lock = threading.Lock()
_capture_lock = threading.Lock()
_capture_sessions = {}


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database():
    """Create/upgrade the local SQLite database used by the web dashboard."""
    with _db_init_lock:
        with db_connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    dob TEXT,
                    age INTEGER,
                    gender TEXT,
                    symptom_tags_json TEXT,
                    face_db_path TEXT,
                    pics_saved INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lab_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE,
                    source TEXT DEFAULT 'manual',
                    pdf_filename TEXT,
                    values_json TEXT,
                    analysis_json TEXT,
                    ai_prediction_json TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS measurement_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    session_started_at TEXT,
                    camera_captured_at TEXT,
                    temp_captured_at TEXT,
                    heart_captured_at TEXT,
                    saved_at TEXT NOT NULL,
                    recognized_identity TEXT,
                    camera_status TEXT,
                    camera_distance TEXT,
                    pics_saved INTEGER DEFAULT 0,
                    temperature_raw REAL,
                    temperature_text TEXT,
                    temperature_range TEXT,
                    temperature_state TEXT,
                    heart_bpm REAL,
                    heart_bpm_text TEXT,
                    heart_range TEXT,
                    heart_state TEXT,
                    spo2 REAL,
                    spo2_text TEXT,
                    spo2_range TEXT,
                    spo2_state TEXT,
                    color_base TEXT,
                    color_dominant TEXT,
                    color_red REAL,
                    color_green REAL,
                    color_blue REAL,
                    fusion_title TEXT,
                    fusion_state TEXT,
                    fusion_score_text TEXT,
                    fusion_detail TEXT,
                    lab_available INTEGER DEFAULT 0,
                    symptom_tags_json TEXT,
                    pdf_filename TEXT,
                    lab_values_json TEXT,
                    lab_analysis_json TEXT,
                    ai_prediction_json TEXT,
                    notes TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS calendar_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_date TEXT NOT NULL UNIQUE,
                    note_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Lightweight migrations for users who already ran an older package.
            _ensure_column(conn, "users", "symptom_tags_json", "TEXT")
            _ensure_column(conn, "measurement_sessions", "symptom_tags_json", "TEXT")
            conn.commit()


def _ensure_column(conn, table, column, coltype):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_dob(dob_text):
    dob_text = str(dob_text or "").strip()
    if not dob_text:
        return None
    try:
        return date.fromisoformat(dob_text)
    except Exception:
        return None


def age_from_dob(dob_text):
    dob = parse_dob(dob_text)
    if dob is None:
        return None
    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age if age >= 0 else None


def count_person_images(name):
    name = safe_person_name(name)
    if not name:
        return 0
    img_dir = os.path.join(FACE_DB_PATH, name, "images")
    if not os.path.isdir(img_dir):
        return 0
    try:
        return sum(1 for fname in os.listdir(img_dir) if fname.lower().endswith(IMAGE_EXTS))
    except Exception:
        return 0


def face_db_relative_path(name):
    try:
        return os.path.relpath(person_dir_for(name), BASE_DIR)
    except Exception:
        return ""


def ensure_db_user(name, dob=None, gender=None, symptoms=None):
    """Create or update a user row. FaceDB remains the storage for image/embedding files."""
    init_database()
    name = safe_person_name(name)
    if not name:
        raise ValueError("Enter or select a user name first")

    dob = str(dob or "").strip() or None
    gender = str(gender or "").strip() or None
    symptom_tags_json = symptoms_to_json(symptoms)
    computed_age = age_from_dob(dob) if dob else None
    pics_saved = count_person_images(name)
    timestamp = now_iso()
    face_path = face_db_relative_path(name)

    with db_connect() as conn:
        existing = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO users (name, dob, age, gender, symptom_tags_json, face_db_path, pics_saved, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, dob, computed_age, gender, symptom_tags_json if symptom_tags_json is not None else json.dumps([]), face_path, pics_saved, timestamp, timestamp),
            )
        else:
            update_dob = dob if dob is not None else existing["dob"]
            update_age = age_from_dob(update_dob) if update_dob else existing["age"]
            update_gender = gender if gender is not None else existing["gender"]
            update_symptoms = symptom_tags_json if symptom_tags_json is not None else existing["symptom_tags_json"]
            changed = (
                existing["dob"] != update_dob or
                existing["age"] != update_age or
                existing["gender"] != update_gender or
                existing["symptom_tags_json"] != update_symptoms or
                existing["face_db_path"] != face_path or
                int(existing["pics_saved"] or 0) != int(pics_saved or 0)
            )
            if changed:
                conn.execute(
                    """
                    UPDATE users
                    SET dob = ?, age = ?, gender = ?, symptom_tags_json = ?, face_db_path = ?, pics_saved = ?, updated_at = ?
                    WHERE name = ?
                    """,
                    (update_dob, update_age, update_gender, update_symptoms, face_path, pics_saved, timestamp, name),
                )
        conn.commit()
        return attach_symptom_tags(dict(conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()))


def delete_db_user(name):
    init_database()
    name = safe_person_name(name)
    with db_connect() as conn:
        conn.execute("DELETE FROM users WHERE name = ?", (name,))
        conn.commit()


def sync_facedb_users_to_database():
    init_database()
    if not os.path.isdir(FACE_DB_PATH):
        return
    for person in os.listdir(FACE_DB_PATH):
        if os.path.isdir(os.path.join(FACE_DB_PATH, person)):
            try:
                ensure_db_user(person)
            except Exception:
                pass


def row_to_dict(row):
    return dict(row) if row is not None else None


def json_loads_maybe(value, default=None):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def normalize_symptoms(symptoms):
    """Return a clean, unique list of symptom tag labels."""
    if symptoms is None:
        return None
    if isinstance(symptoms, str):
        symptoms = [item.strip() for item in symptoms.split(",")]
    if not isinstance(symptoms, (list, tuple, set)):
        return []

    allowed = {item.lower(): item for item in SYMPTOM_OPTIONS}
    cleaned = []
    seen = set()
    for item in symptoms:
        text = str(item or "").strip()
        if not text:
            continue
        canonical = allowed.get(text.lower(), text[:60])
        key = canonical.lower()
        if key not in seen:
            cleaned.append(canonical)
            seen.add(key)
    return cleaned


def symptoms_to_json(symptoms):
    cleaned = normalize_symptoms(symptoms)
    if cleaned is None:
        return None
    return json.dumps(cleaned)


def attach_symptom_tags(record):
    if not record:
        return record
    value = record.get("symptom_tags_json")
    record["symptom_tags"] = json_loads_maybe(value, [])
    return record


def clean_lab_values(values, dob=None, gender=None):
    """Normalize manual/PDF lab values and inject age from DoB when available."""
    values = values or {}
    clean = default_lab_values()
    computed_age = age_from_dob(dob)
    for key, _label in LAB_FIELDS:
        value = values.get(key)
        if value == "":
            value = None
        if key == "gender":
            clean[key] = (gender or value) if (gender or value) else None
        elif key == "age":
            if computed_age is not None:
                clean[key] = int(computed_age)
            else:
                clean[key] = None if safe_float(value) is None else int(float(value))
        else:
            clean[key] = None if safe_float(value) is None else float(value)
    return clean


def has_any_lab_value(values):
    values = values or {}
    # DoB-derived age and gender are demographics, not optional PDF/manual liver lab readings.
    return any(
        values.get(key) not in (None, "")
        for key, _ in LAB_FIELDS
        if key not in ("age", "gender")
    )


def upsert_lab_result(name, values, source="manual", pdf_filename=None, analysis=None, ai_prediction=None, dob=None, gender=None):
    init_database()
    user = ensure_db_user(name, dob=dob, gender=gender)
    if analysis is None:
        analysis = analyze_saved_lab_results(values)
    timestamp = now_iso()
    with db_connect() as conn:
        existing = conn.execute("SELECT id FROM lab_results WHERE user_id = ?", (user["id"],)).fetchone()
        payload = (
            user["id"], source, pdf_filename, json.dumps(values),
            json.dumps(analysis), json.dumps(ai_prediction) if ai_prediction is not None else None,
            timestamp,
        )
        if existing is None:
            conn.execute(
                """
                INSERT INTO lab_results (user_id, source, pdf_filename, values_json, analysis_json, ai_prediction_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        else:
            conn.execute(
                """
                UPDATE lab_results
                SET source = ?, pdf_filename = COALESCE(?, pdf_filename), values_json = ?,
                    analysis_json = ?, ai_prediction_json = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (source, pdf_filename, json.dumps(values), json.dumps(analysis), json.dumps(ai_prediction) if ai_prediction is not None else None, timestamp, user["id"]),
            )
        conn.commit()
    return analysis


def current_capture(name):
    name = safe_person_name(name)
    with _capture_lock:
        session = _capture_sessions.setdefault(name, {"name": name, "started_at": now_iso(), "steps": {}})
        return json.loads(json.dumps(session))


def update_capture_step(name, step, payload):
    name = safe_person_name(name)
    with _capture_lock:
        session = _capture_sessions.setdefault(name, {"name": name, "started_at": now_iso(), "steps": {}})
        session["steps"][step] = payload
        session["updated_at"] = now_iso()
        return json.loads(json.dumps(session))


def reset_capture(name):
    name = safe_person_name(name)
    with _capture_lock:
        _capture_sessions.pop(name, None)


def extract_bpm_value(heart):
    return safe_float(str((heart or {}).get("bpm_text", "")).replace(" BPM", "").strip())


def extract_spo2_value(heart):
    return safe_float(str((heart or {}).get("spo2_text", "")).replace("%", "").strip())


def database_summary(selected_name=None):
    init_database()
    sync_facedb_users_to_database()
    selected_name = safe_person_name(selected_name)
    with db_connect() as conn:
        totals = {
            "users": conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
            "sessions": conn.execute("SELECT COUNT(*) AS c FROM measurement_sessions").fetchone()["c"],
            "with_lab_results": conn.execute("SELECT COUNT(*) AS c FROM lab_results").fetchone()["c"],
        }
        users = [attach_symptom_tags(dict(r)) for r in conn.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE").fetchall()]
        selected_user = None
        if selected_name:
            selected_user = attach_symptom_tags(row_to_dict(conn.execute("SELECT * FROM users WHERE name = ?", (selected_name,)).fetchone()))
        latest = row_to_dict(conn.execute(
            """
            SELECT ms.*, u.name, u.dob, u.age, u.gender, u.symptom_tags_json AS user_symptom_tags_json
            FROM measurement_sessions ms
            JOIN users u ON u.id = ms.user_id
            ORDER BY ms.id DESC LIMIT 1
            """
        ).fetchone())
        if selected_name:
            latest_for_selected = row_to_dict(conn.execute(
                """
                SELECT ms.*, u.name, u.dob, u.age, u.gender, u.symptom_tags_json AS user_symptom_tags_json
                FROM measurement_sessions ms
                JOIN users u ON u.id = ms.user_id
                WHERE u.name = ?
                ORDER BY ms.id DESC LIMIT 1
                """, (selected_name,)
            ).fetchone())
        else:
            latest_for_selected = None
        lab_row = None
        if selected_name:
            lab_row = row_to_dict(conn.execute(
                """
                SELECT lr.* FROM lab_results lr
                JOIN users u ON u.id = lr.user_id
                WHERE u.name = ?
                """, (selected_name,)
            ).fetchone())
        if latest:
            for key in ("lab_values_json", "lab_analysis_json", "ai_prediction_json"):
                latest[key.replace("_json", "")] = json_loads_maybe(latest.get(key), None)
            latest["symptom_tags"] = json_loads_maybe(latest.get("symptom_tags_json"), [])
            latest["user_symptom_tags"] = json_loads_maybe(latest.get("user_symptom_tags_json"), [])
        if latest_for_selected:
            for key in ("lab_values_json", "lab_analysis_json", "ai_prediction_json"):
                latest_for_selected[key.replace("_json", "")] = json_loads_maybe(latest_for_selected.get(key), None)
            latest_for_selected["symptom_tags"] = json_loads_maybe(latest_for_selected.get("symptom_tags_json"), [])
            latest_for_selected["user_symptom_tags"] = json_loads_maybe(latest_for_selected.get("user_symptom_tags_json"), [])
        if lab_row:
            lab_row["values"] = json_loads_maybe(lab_row.get("values_json"), {})
            lab_row["analysis"] = json_loads_maybe(lab_row.get("analysis_json"), {})
            lab_row["ai_prediction"] = json_loads_maybe(lab_row.get("ai_prediction_json"), {})
        return {
            "ok": True,
            "db_path": DB_PATH,
            "totals": totals,
            "users": users,
            "selected_user": selected_user,
            "latest_session": latest,
            "latest_selected_session": latest_for_selected,
            "selected_lab": lab_row,
            "active_capture": current_capture(selected_name) if selected_name else None,
            "schema": {
                "users": ["name", "dob", "age", "gender", "symptom tags", "face_db_path", "pics_saved"],
                "measurement_sessions": ["camera step", "temperature", "heart rate", "SpO2", "color", "fusion", "symptom tags", "manual/PDF labs", "AI prediction", "notes"],
            },
        }


def save_measurement_session(name, dob=None, gender=None, symptoms=None, lab_values=None, notes=""):
    init_database()
    name = safe_person_name(name)
    if not name:
        raise ValueError("Enter or select a user name first")
    user = ensure_db_user(name, dob=dob, gender=gender, symptoms=symptoms)
    session = current_capture(name)
    steps = session.get("steps", {})
    missing = [label for key, label in (("camera", "camera"), ("temperature", "temperature"), ("heart", "heart")) if key not in steps]
    symptom_tags = normalize_symptoms(symptoms) or []
    if missing:
        raise ValueError("Follow the save flow first: " + " → ".join(["camera", "temperature", "heart"]) + f". Missing: {', '.join(missing)}")

    clean_labs = clean_lab_values(lab_values or {}, dob=dob, gender=gender)
    lab_available = 1 if has_any_lab_value(clean_labs) else 0
    if lab_available:
        saved_labs = save_lab_results(name, clean_labs)
        lab_analysis = analyze_saved_lab_results(saved_labs)
        try:
            ai_prediction = predict_liver_ai_from_values(saved_labs)
        except Exception as e:
            ai_prediction = {"title": "AI Prediction Unavailable", "state": STATUS_PROCESSING, "detail_text": str(e)}
        upsert_lab_result(name, saved_labs, source="manual", analysis=lab_analysis, ai_prediction=ai_prediction, dob=dob, gender=gender)
    else:
        saved_labs = clean_labs
        lab_analysis = analyze_saved_lab_results(saved_labs)
        ai_prediction = None

    temp = steps.get("temperature", {})
    heart = steps.get("heart", {})
    camera = steps.get("camera", {})
    live_sensors = sensor_hub.get()
    color = live_sensors.get("color", {})
    fusion = live_sensors.get("fusion", {})
    timestamp = now_iso()

    with db_connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO measurement_sessions (
                user_id, session_started_at, camera_captured_at, temp_captured_at, heart_captured_at, saved_at,
                recognized_identity, camera_status, camera_distance, pics_saved,
                temperature_raw, temperature_text, temperature_range, temperature_state,
                heart_bpm, heart_bpm_text, heart_range, heart_state,
                spo2, spo2_text, spo2_range, spo2_state,
                color_base, color_dominant, color_red, color_green, color_blue,
                fusion_title, fusion_state, fusion_score_text, fusion_detail,
                lab_available, symptom_tags_json, pdf_filename, lab_values_json, lab_analysis_json, ai_prediction_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], session.get("started_at"), camera.get("captured_at"), temp.get("captured_at"), heart.get("captured_at"), timestamp,
                camera.get("identity_text"), camera.get("status_text"), camera.get("distance_text"), count_person_images(name),
                safe_float(temp.get("raw")), temp.get("text"), temp.get("range_text"), temp.get("range_state"),
                extract_bpm_value(heart), heart.get("bpm_text"), heart.get("range_text"), heart.get("range_state"),
                extract_spo2_value(heart), heart.get("spo2_text"), heart.get("spo2_range_text"), heart.get("spo2_range_state"),
                color.get("base_color"), color.get("dominant"), safe_float(color.get("red")), safe_float(color.get("green")), safe_float(color.get("blue")),
                fusion.get("title"), fusion.get("state"), fusion.get("score_text"), fusion.get("detail_text"),
                lab_available, json.dumps(symptom_tags), None, json.dumps(saved_labs), json.dumps(lab_analysis), json.dumps(ai_prediction) if ai_prediction is not None else None, notes,
            ),
        )
        conn.commit()
        session_id = cur.lastrowid
    reset_capture(name)
    return {"ok": True, "message": f"Saved complete checkup for {name}", "session_id": session_id, "summary": database_summary(name)}

# 1x1 JPEG fallback for MJPEG streams when OpenCV/camera is unavailable.
TINY_JPEG = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    b"////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    b"////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EFBABAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)

# =========================
# GENERAL HELPERS
# =========================
def safe_float(value):
    try:
        if value is None:
            return None
        value = float(value)
        if math.isnan(value):
            return None
        return value
    except Exception:
        return None


def safe_person_name(name):
    name = str(name or "").strip()
    name = name.replace("/", "_").replace("\\", "_").replace("..", "_")
    return name[:80]


def person_dir_for(name):
    name = safe_person_name(name)
    if not name:
        raise ValueError("Missing user name")
    return os.path.join(FACE_DB_PATH, name)


def classify_heart_rate(bpm_text):
    bpm_value = safe_float(str(bpm_text).replace(" BPM", "").strip())
    if bpm_value is None:
        return "Current Range: ---", "unknown"
    if bpm_value < HR_NORMAL_MIN:
        return "Current Range: Low", "low"
    if bpm_value <= HR_NORMAL_MAX:
        return "Current Range: Normal", "normal"
    return "Current Range: High", "high"


def classify_temperature(temp_value):
    value = safe_float(temp_value)
    if value is None:
        return "Current Range: ---", "unknown"
    if value < TEMP_NORMAL_MIN:
        return "Current Range: Low", "low"
    if value <= TEMP_NORMAL_MAX:
        return "Current Range: Normal", "normal"
    return "Current Range: High", "high"


def classify_spo2(spo2_text):
    value = safe_float(str(spo2_text).replace("%", "").strip())
    if value is None:
        return "SpO2 Range: ---", "unknown"
    if value < SPO2_LOW_MIN:
        return "SpO2 Range: Critical", "critical"
    if value < SPO2_NORMAL_MIN:
        return "SpO2 Range: Low", "low"
    return "SpO2 Range: Normal", "normal"


def compute_sensor_fusion(temp_data, heart_data, color_data):
    score = 0
    findings = []
    missing = []

    temp_value = safe_float(temp_data.get("raw"))
    bpm_value = safe_float(str(heart_data.get("bpm_text", "")).replace(" BPM", "").strip())
    spo2_value = safe_float(str(heart_data.get("spo2_text", "")).replace("%", "").strip())
    base_color = str(color_data.get("base_color", "") or "")
    dominant_color = str(color_data.get("dominant", "") or "")

    if temp_data.get("ok") and temp_value is not None:
        if temp_value < 35.5 or temp_value > 38.0:
            score += 2
            findings.append("Temperature is far from the normal range")
        elif temp_value < TEMP_NORMAL_MIN or temp_value > TEMP_NORMAL_MAX:
            score += 1
            findings.append("Temperature is outside the normal range")
    else:
        missing.append("temperature")

    heart_status = str(heart_data.get("status", "")).lower()
    if "offline" in heart_status:
        missing.append("heart sensor")
    elif "place finger" in heart_status or "stabilizing" in heart_status:
        missing.append("stable heart data")
    else:
        if bpm_value is not None:
            if bpm_value < 50 or bpm_value > 120:
                score += 2
                findings.append("Heart rate is far from the normal range")
            elif bpm_value < HR_NORMAL_MIN or bpm_value > HR_NORMAL_MAX:
                score += 1
                findings.append("Heart rate is outside the normal range")
        else:
            missing.append("heart rate")

        if spo2_value is not None:
            if spo2_value < 90:
                score += 2
                findings.append("SpO2 is critically low")
            elif spo2_value < 95:
                score += 1
                findings.append("SpO2 is below the normal range")
        else:
            missing.append("SpO2")

    if color_data.get("ok"):
        if "Yellow" in dominant_color or base_color == "Yellow":
            score += 1
            findings.append("Yellow color tendency detected by the color sensor")
    else:
        missing.append("color sensor")

    if score == 0 and findings:
        title = "Stable"
        state = STATUS_READY
    elif score == 0:
        title = "Collecting Data"
        state = STATUS_PROCESSING
    elif score == 1:
        title = "Attention"
        state = STATUS_PROCESSING
    elif score == 2:
        title = "Warning"
        state = STATUS_FAIL
    else:
        title = "High Alert"
        state = STATUS_FAIL

    if findings:
        detail = " | ".join(findings)
    elif missing:
        detail = "Waiting for: " + ", ".join(missing)
    else:
        detail = "All current sensor values are within the expected screening ranges"

    return {
        "title": title,
        "state": state,
        "score_text": f"Fusion Score: {score}",
        "detail_text": detail,
        "note_text": "Screening summary only - not a medical diagnosis",
    }


def clean_feature_name(name):
    return str(name).replace("\xa0", " ").strip()


def gender_to_model_value(value):
    text = str(value or "").strip().lower()
    if text in ("male", "m", "1", "1.0"):
        return 1.0
    if text in ("female", "f", "0", "0.0"):
        return 0.0
    return None


def clean_pdf_text(text):
    text = str(text or "").replace("\xa0", " ")
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_number_after_aliases(text, aliases):
    text = clean_pdf_text(text)
    for alias in aliases:
        pattern = rf"(?:{alias})\s*(?:result|value)?\s*[:=\-]?\s*([0-9]+(?:\.[0-9]+)?)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return safe_float(match.group(1))
    return None


def extract_liver_values_from_text(pdf_text):
    return {key: extract_number_after_aliases(pdf_text, aliases) for key, aliases in PDF_PARAMETER_ALIASES.items()}


def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        for page in doc:
            text += page.get_text() + "\n"
        if len(text.strip()) > 100:
            return text, "direct PDF text extraction"
    except Exception:
        pass

    try:
        from pdf2image import convert_from_path
        import pytesseract
        pages = convert_from_path(pdf_path, dpi=300)
        ocr_text = ""
        for i, page in enumerate(pages):
            ocr_text += f"\n--- Page {i + 1} ---\n"
            ocr_text += pytesseract.image_to_string(page, lang="eng")
        return ocr_text, "OCR extraction"
    except Exception as e:
        raise RuntimeError(
            "Could not read the PDF. Install dependencies first: "
            "pip install pymupdf pdf2image pytesseract pillow joblib and install tesseract-ocr poppler-utils. "
            f"Details: {e}"
        )


def default_lab_values():
    return {key: None for key, _ in LAB_FIELDS}


def load_lab_results(person_name):
    person_dir = person_dir_for(person_name)
    lab_path = os.path.join(person_dir, "lab_results.json")
    values = default_lab_values()
    if os.path.isfile(lab_path):
        try:
            with open(lab_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            values.update(saved)
        except Exception:
            pass
    return values


def save_lab_results(person_name, values):
    person_dir = person_dir_for(person_name)
    os.makedirs(person_dir, exist_ok=True)
    clean_values = default_lab_values()
    clean_values.update(values or {})
    lab_path = os.path.join(person_dir, "lab_results.json")
    with open(lab_path, "w", encoding="utf-8") as f:
        json.dump(clean_values, f, indent=2)
    return clean_values


def analyze_saved_lab_results(values):
    available = 0
    checked = 0
    abnormal = []
    abnormal_details = []
    high_flags = set()
    low_flags = set()

    def _num(name):
        return safe_float(values.get(name))

    for key, spec in LAB_REFERENCE_RANGES.items():
        val = _num(key)
        if val is None:
            continue
        available += 1
        checked += 1
        low = spec["low"]
        high = spec["high"]
        label = spec["label"]
        if val < low:
            low_flags.add(key)
            abnormal.append(f"{label} is below range")
            abnormal_details.append(f"{label}: {val:g} is LOW compared with reference range {low:g}-{high:g}")
        elif val > high:
            high_flags.add(key)
            abnormal.append(f"{label} is above range")
            abnormal_details.append(f"{label}: {val:g} is HIGH compared with reference range {low:g}-{high:g}")

    total_optional_numeric = len(LAB_REFERENCE_RANGES) + 1
    if safe_float(values.get("age")) is not None:
        available += 1
    coverage_text = f"Coverage: {available}/{total_optional_numeric} lab fields entered"

    alt_high = "alanine_aminotransferase" in high_flags
    ast_high = "aspartate_aminotransferase" in high_flags
    alp_high = "alkaline_phosphatase" in high_flags
    total_bil_high = "total_bilirubin" in high_flags
    direct_bil_high = "direct_bilirubin" in high_flags
    albumin_low = "albumin" in low_flags
    proteins_low = "total_proteins" in low_flags
    ag_low = "albumin_globulin_ratio" in low_flags

    transaminase_flag = alt_high or ast_high
    cholestatic_flag = alp_high or total_bil_high or direct_bil_high
    synthetic_flag = albumin_low or proteins_low or ag_low

    pattern = "Insufficient Data"
    possible_concern = "Not enough laboratory values are available to suggest a pattern."
    state = STATUS_PROCESSING

    if checked == 0:
        pattern = "No Lab Data"
        possible_concern = "No usable lab values were entered yet."
        state = STATUS_PROCESSING
    elif not abnormal:
        pattern = "No Flagged Abnormality"
        possible_concern = "The entered lab values are within the current reference ranges."
        state = STATUS_READY
    else:
        if transaminase_flag and cholestatic_flag:
            pattern = "Possible Mixed Liver Pattern"
            possible_concern = (
                "Likely pattern: mixed liver injury pattern. Elevated liver enzymes together with "
                "cholestatic markers may be associated with hepatitis, fatty liver with cholestasis, "
                "drug-induced liver stress, bile duct/gallbladder obstruction, or other liver conditions."
            )
        elif transaminase_flag:
            pattern = "Possible Hepatocellular Pattern"
            possible_concern = (
                "Likely pattern: hepatocellular liver irritation/injury. Elevated ALT and/or AST may be "
                "associated with hepatitis, fatty liver disease, medication-related liver stress, muscle injury, "
                "or other inflammatory liver conditions."
            )
        elif cholestatic_flag:
            pattern = "Possible Cholestatic Pattern"
            possible_concern = (
                "Likely pattern: cholestatic/bile-flow concern. Elevated alkaline phosphatase and/or bilirubin "
                "may be associated with bile duct obstruction, gallbladder disease, cholestasis, or other "
                "liver/biliary conditions."
            )
        elif synthetic_flag:
            pattern = "Possible Liver Function Concern"
            possible_concern = (
                "Likely concern: reduced protein synthesis or nutritional/inflammatory effect. Low albumin, "
                "low total proteins, or abnormal A/G ratio may require medical review."
            )
        else:
            pattern = "Abnormal Lab Pattern"
            possible_concern = (
                "Some entered values are outside the expected reference ranges. A doctor should review the "
                "full report and clinical symptoms."
            )

    abnormal_count = len(abnormal)
    severe_flags = []
    alt_value = _num("alanine_aminotransferase")
    ast_value = _num("aspartate_aminotransferase")
    alp_value = _num("alkaline_phosphatase")
    total_bil_value = _num("total_bilirubin")
    direct_bil_value = _num("direct_bilirubin")
    albumin_value = _num("albumin")

    if alt_value is not None and alt_value > LAB_REFERENCE_RANGES["alanine_aminotransferase"]["high"] * 3:
        severe_flags.append("ALT is more than 3x the upper reference limit")
    if ast_value is not None and ast_value > LAB_REFERENCE_RANGES["aspartate_aminotransferase"]["high"] * 3:
        severe_flags.append("AST is more than 3x the upper reference limit")
    if alp_value is not None and alp_value > LAB_REFERENCE_RANGES["alkaline_phosphatase"]["high"] * 2:
        severe_flags.append("Alkaline phosphatase is more than 2x the upper reference limit")
    if total_bil_value is not None and total_bil_value > LAB_REFERENCE_RANGES["total_bilirubin"]["high"] * 2:
        severe_flags.append("Total bilirubin is more than 2x the upper reference limit")
    if direct_bil_value is not None and direct_bil_value > LAB_REFERENCE_RANGES["direct_bilirubin"]["high"] * 2:
        severe_flags.append("Direct bilirubin is more than 2x the upper reference limit")
    if albumin_value is not None and albumin_value < LAB_REFERENCE_RANGES["albumin"]["low"]:
        severe_flags.append("Albumin is below the lower reference limit")

    if checked == 0:
        warning_level = "No data"
        advice = "No lab values were entered. Please enter or upload a liver function test report."
    elif abnormal_count == 0:
        warning_level = "Low"
        advice = (
            "No abnormal entered values were flagged by the current reference ranges. "
            "This does not replace medical interpretation."
        )
    elif abnormal_count == 1 and not severe_flags:
        warning_level = "Mild warning"
        state = STATUS_PROCESSING
        advice = (
            "One abnormal reading was detected. Review the value and consult a doctor if symptoms exist, "
            "if the value is repeated, or if the abnormality persists."
        )
    elif abnormal_count <= 3 and len(severe_flags) <= 1:
        warning_level = "Moderate warning"
        state = STATUS_FAIL
        advice = "Multiple abnormal readings were detected. Please consult a doctor for proper clinical interpretation."
    else:
        warning_level = "High warning"
        state = STATUS_FAIL
        advice = (
            "Several liver-related readings are outside the reference ranges or one or more readings are markedly "
            "abnormal. Medical consultation is strongly recommended."
        )

    detail_parts = []
    if abnormal_details:
        detail_parts.append("Abnormal readings:")
        detail_parts.extend([f"• {item}" for item in abnormal_details])
    if severe_flags:
        if detail_parts:
            detail_parts.append("")
        detail_parts.append("Priority warnings:")
        detail_parts.extend([f"• {item}" for item in severe_flags])

    detail_text = "\n".join(detail_parts) if detail_parts else "No abnormal entered values were flagged."
    advice_text = f"{warning_level}: {possible_concern} {advice}"
    note_text = "Screening support only - not a diagnosis. Please consult a qualified physician for medical diagnosis."

    return {
        "title": pattern,
        "state": state,
        "coverage_text": coverage_text,
        "detail_text": detail_text,
        "advice_text": advice_text,
        "note_text": note_text,
    }


def load_liver_ai_assets():
    missing = [p for p in [LIVER_MODEL_PATH, LIVER_COLUMNS_PATH, LIVER_MEDIANS_PATH, LIVER_ENCODERS_PATH] if not os.path.isfile(p)]
    if missing:
        raise FileNotFoundError("Missing liver model file(s): " + ", ".join(os.path.basename(p) for p in missing))
    import joblib
    model = joblib.load(LIVER_MODEL_PATH)
    model_columns = joblib.load(LIVER_COLUMNS_PATH)
    training_medians = joblib.load(LIVER_MEDIANS_PATH)
    label_encoders = joblib.load(LIVER_ENCODERS_PATH)
    return model, model_columns, training_medians, label_encoders


def build_ai_input_from_lab_values(values, model, model_columns, training_medians):
    exact_columns = list(getattr(model, "feature_names_in_", model_columns))
    medians_clean = training_medians.copy()
    try:
        medians_clean.index = [clean_feature_name(i) for i in medians_clean.index]
    except Exception:
        pass

    input_data = {}
    missing = []
    for exact_col in exact_columns:
        clean_col = clean_feature_name(exact_col)
        lab_key = MODEL_FEATURE_TO_LAB_KEY.get(clean_col)
        value = values.get(lab_key) if lab_key else None
        if lab_key == "gender":
            value = gender_to_model_value(value)
        else:
            value = safe_float(value)
        if value is None:
            missing.append(clean_col)
            try:
                value = medians_clean[clean_col]
            except Exception:
                value = 0.0
        input_data[exact_col] = value

    import pandas as pd
    input_df = pd.DataFrame([input_data])
    input_df = input_df[exact_columns]
    return input_df, missing


def predict_liver_ai_from_values(values):
    model, model_columns, training_medians, _label_encoders = load_liver_ai_assets()
    input_df, missing = build_ai_input_from_lab_values(values, model, model_columns, training_medians)

    prediction = model.predict(input_df)[0]
    probability = model.predict_proba(input_df)[0]
    confidence = max(probability) * 100.0
    missing_ratio = len(missing) / max(1, len(input_df.columns))

    if int(prediction) == 1:
        title = "Possible Liver Abnormality"
        state = STATUS_FAIL
        result_text = "AI model detected a possible liver-related abnormality."
    else:
        title = "No Strong AI Abnormality"
        state = STATUS_READY
        result_text = "AI model did not detect a strong liver-related abnormality."

    if missing_ratio >= 0.4:
        reliability = "Low"
        state = STATUS_PROCESSING
        advice = "Too many parameters were missing. Please consult your doctor and complete the missing tests."
    elif missing:
        reliability = "Moderate"
        advice = "Some parameters were missing and were replaced with training median values. Please consult your doctor for interpretation."
    else:
        reliability = "High"
        advice = "All required AI parameters were available. Please consult your doctor for clinical interpretation."

    rule_analysis = analyze_saved_lab_results(values)
    ai_detail = f"AI result: {result_text} Confidence: {confidence:.2f}% | Reliability: {reliability}"
    if missing:
        ai_detail += "\nMissing AI parameters: " + ", ".join(missing[:4])
        if len(missing) > 4:
            ai_detail += f" +{len(missing) - 4} more"

    detail = rule_analysis.get("detail_text", "")
    detail = (detail + "\n\n" + ai_detail) if detail else ai_detail

    combined_title = rule_analysis.get("title", title)
    if combined_title in ("No Lab Data", "No Flagged Abnormality"):
        combined_title = title

    if rule_analysis.get("state") == STATUS_FAIL or state == STATUS_FAIL:
        combined_state = STATUS_FAIL
    elif rule_analysis.get("state") == STATUS_PROCESSING or state == STATUS_PROCESSING:
        combined_state = STATUS_PROCESSING
    else:
        combined_state = STATUS_READY

    combined_advice = rule_analysis.get("advice_text", "") + "\nAI note: " + advice
    return {
        "title": combined_title,
        "state": combined_state,
        "coverage_text": f"AI Confidence: {confidence:.2f}% | Reliability: {reliability}",
        "detail_text": detail,
        "advice_text": combined_advice,
        "note_text": "AI screening support only - not a medical diagnosis",
        "prediction": int(prediction),
        "probability": [float(x) for x in probability],
        "confidence": confidence,
        "missing_parameters": missing,
        "input_columns": [clean_feature_name(c) for c in input_df.columns],
    }

# =========================
# SENSORS
# =========================
class MLXSensor:
    def __init__(self):
        self.ok = False
        self.last_error = ""
        self.mlx = None
        try:
            import board
            import busio
            import adafruit_mlx90614
            i2c = busio.I2C(board.SCL, board.SDA)
            self.mlx = adafruit_mlx90614.MLX90614(i2c)
            self.ok = True
        except Exception as e:
            self.last_error = str(e)
            self.ok = False

    def read(self):
        if not self.ok or self.mlx is None:
            return {"ok": False, "text": "Offline", "raw": None, "error": self.last_error}
        try:
            raw_vals = []
            ambient_vals = []
            for _ in range(MLX_SAMPLES):
                raw_vals.append(float(self.mlx.object_temperature))
                ambient_vals.append(float(self.mlx.ambient_temperature))
                time.sleep(MLX_SAMPLE_DELAY)
            raw_avg = sum(raw_vals) / len(raw_vals)
            ambient_avg = sum(ambient_vals) / len(ambient_vals)
            if raw_avg < MLX_MIN_VALID_RAW and (raw_avg - ambient_avg) < MLX_MIN_DELTA_OVER_AMBIENT:
                return {
                    "ok": False,
                    "text": "Target too far",
                    "raw": None,
                    "error": f"raw={raw_avg:.2f}C ambient={ambient_avg:.2f}C delta={(raw_avg - ambient_avg):.2f}C",
                }
            temp = round(raw_avg + MLX_CAL_OFFSET, 2)
            return {"ok": True, "text": f"{temp:.2f} °C", "raw": temp, "error": f"raw={raw_avg:.2f}C ambient={ambient_avg:.2f}C"}
        except Exception as e:
            self.ok = False
            self.last_error = str(e)
            return {"ok": False, "text": "Read error", "raw": None, "error": self.last_error}


class TCS3200Sensor:
    def __init__(self):
        self.ok = False
        self.last_error = ""
        self.samples_per_color = 7
        self.channel_duration = 0.06
        self.black_total_threshold = 8
        self.dark_total_threshold = 18
        self.bright_total_threshold = 42
        self.gray_balance_threshold = 0.08
        try:
            from gpiozero import DigitalInputDevice, OutputDevice
            self.OUT = DigitalInputDevice(23)
            self.S0 = OutputDevice(16)
            self.S1 = OutputDevice(20)
            self.S2 = OutputDevice(5)
            self.S3 = OutputDevice(6)
            self.S0.on()
            self.S1.off()
            self.ok = True
        except Exception as e:
            self.last_error = str(e)
            self.ok = False

    def read_channel(self, s2, s3, duration=None):
        if duration is None:
            duration = self.channel_duration
        self.S2.value = s2
        self.S3.value = s3
        time.sleep(0.01)
        count = 0
        start = time.time()
        while time.time() - start < duration:
            if self.OUT.value:
                count += 1
                while self.OUT.value:
                    pass
        return count

    def _average_channel(self, s2, s3, samples=None):
        if samples is None:
            samples = self.samples_per_color
        values = [self.read_channel(s2, s3) for _ in range(samples)]
        return sum(values) / len(values) if values else 0.0

    def _shade_prefix(self, total):
        if total < self.dark_total_threshold:
            return "Dark"
        if total > self.bright_total_threshold:
            return "Light"
        return ""

    def classify_color(self, red, green, blue):
        total = red + green + blue
        if total <= 0:
            return "Unknown", "Unknown", (0.0, 0.0, 0.0)
        nr = red / total
        ng = green / total
        nb = blue / total
        normalized = (nr, ng, nb)
        mx = max(nr, ng, nb)
        mn = min(nr, ng, nb)
        if total < self.black_total_threshold:
            return "Black", "Black", normalized
        if (mx - mn) < self.gray_balance_threshold:
            if total < self.dark_total_threshold:
                return "Gray", "Dark Gray", normalized
            if total > self.bright_total_threshold:
                return "White", "White", normalized
            return "Gray", "Gray", normalized
        if nr > 0.38 and ng > 0.34 and nb < 0.24:
            prefix = self._shade_prefix(total)
            return "Yellow", f"{prefix} Yellow".strip(), normalized
        if nr > 0.34 and nb > 0.34 and ng < 0.24:
            prefix = self._shade_prefix(total)
            return "Magenta", f"{prefix} Magenta".strip(), normalized
        if ng > 0.34 and nb > 0.34 and nr < 0.24:
            prefix = self._shade_prefix(total)
            return "Cyan", f"{prefix} Cyan".strip(), normalized
        values = {"Red": nr, "Green": ng, "Blue": nb}
        base = max(values, key=values.get)
        prefix = self._shade_prefix(total)
        return base, f"{prefix} {base}".strip(), normalized

    def read(self):
        if not self.ok:
            return {
                "ok": False, "dominant": "Offline", "shade": "Offline", "base_color": "Offline",
                "red": None, "green": None, "blue": None,
                "norm_red": None, "norm_green": None, "norm_blue": None,
                "error": self.last_error,
            }
        try:
            red = self._average_channel(0, 0)
            blue = self._average_channel(0, 1)
            green = self._average_channel(1, 1)
            base_color, shade, normalized = self.classify_color(red, green, blue)
            nr, ng, nb = normalized
            return {
                "ok": True, "dominant": shade, "shade": shade, "base_color": base_color,
                "red": round(red, 2), "green": round(green, 2), "blue": round(blue, 2),
                "norm_red": round(nr, 3), "norm_green": round(ng, 3), "norm_blue": round(nb, 3), "error": "",
            }
        except Exception as e:
            self.ok = False
            self.last_error = str(e)
            return {
                "ok": False, "dominant": "Read error", "shade": "Read error", "base_color": "Read error",
                "red": None, "green": None, "blue": None,
                "norm_red": None, "norm_green": None, "norm_blue": None,
                "error": self.last_error,
            }


class HeartSensor:
    def __init__(self):
        self.hrm = None
        self.started = False
        self.start_error = ""
        try:
            from heartrate_monitor import HeartRateMonitor
            self.hrm = HeartRateMonitor(print_result=False, print_interval=0.5, window_size=200, finger_lost_timeout=0.35, debug=False)
            self.hrm.start()
            self.started = True
        except Exception as e:
            self.start_error = str(e)
            self.started = False

    def read(self):
        if not self.started or self.hrm is None:
            return {"ok": False, "status": "Heart sensor offline", "bpm_text": "---", "spo2_text": "---", "error": self.start_error}
        try:
            sensor_found = getattr(self.hrm, "sensor_found", False)
            finger_detected = getattr(self.hrm, "finger_detected", False)
            bpm = safe_float(getattr(self.hrm, "bpm", None))
            spo2 = safe_float(getattr(self.hrm, "spo2", None))
            if not sensor_found:
                return {"ok": False, "status": "Heart sensor offline", "bpm_text": "---", "spo2_text": "---", "error": "MAX30102 not detected"}
            if not finger_detected:
                return {"ok": True, "status": "Place finger on sensor", "bpm_text": "---", "spo2_text": "---" if spo2 is None else f"{spo2:.1f} %", "error": ""}
            if bpm is None:
                return {"ok": True, "status": "Finger detected - stabilizing BPM...", "bpm_text": "---", "spo2_text": "---" if spo2 is None else f"{spo2:.1f} %", "error": ""}
            return {"ok": True, "status": "Reading stable", "bpm_text": f"{bpm:.1f} BPM", "spo2_text": "---" if spo2 is None else f"{spo2:.1f} %", "error": ""}
        except Exception as e:
            return {"ok": False, "status": "Heart read error", "bpm_text": "---", "spo2_text": "---", "error": str(e)}

    def stop(self):
        if self.hrm is not None:
            try:
                self.hrm.stop()
            except Exception:
                pass


class SensorHub:
    def __init__(self):
        self.running = False
        self.lock = threading.Lock()
        self.thread = None
        self.last = {
            "temperature": {"ok": False, "text": "Offline", "raw": None, "error": "not started"},
            "heart": {"ok": False, "status": "Heart sensor offline", "bpm_text": "---", "spo2_text": "---", "error": "not started"},
            "color": {"ok": False, "dominant": "Offline", "base_color": "Offline", "red": None, "green": None, "blue": None, "error": "not started"},
            "fusion": compute_sensor_fusion({}, {}, {}),
        }
        self.mlx = None
        self.tcs = None
        self.heart = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        self.mlx = MLXSensor()
        self.tcs = TCS3200Sensor()
        self.heart = HeartSensor()
        while self.running:
            payload = {
                "temperature": self.mlx.read(),
                "color": self.tcs.read(),
                "heart": self.heart.read(),
            }
            payload["temperature"]["range_text"], payload["temperature"]["range_state"] = classify_temperature(payload["temperature"].get("raw"))
            payload["heart"]["range_text"], payload["heart"]["range_state"] = classify_heart_rate(payload["heart"].get("bpm_text"))
            payload["heart"]["spo2_range_text"], payload["heart"]["spo2_range_state"] = classify_spo2(payload["heart"].get("spo2_text"))
            payload["fusion"] = compute_sensor_fusion(payload["temperature"], payload["heart"], payload["color"])
            with self.lock:
                self.last = payload
            time.sleep(SENSOR_REFRESH_MS / 1000.0)

    def get(self):
        with self.lock:
            return json.loads(json.dumps(self.last))

    def stop(self):
        self.running = False
        if self.heart:
            self.heart.stop()

# =========================
# WEATHER
# =========================
class WeatherService:
    def __init__(self, location_name):
        self.location_name = location_name
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.last = {"ok": False, "city": location_name, "temp": "---", "feels": "---", "condition": "Loading..."}

    @staticmethod
    def weather_code_to_text(code):
        mapping = {
            0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Drizzle",
            55: "Dense drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
            71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Rain showers",
            81: "Rain showers", 82: "Violent rain showers", 95: "Thunderstorm",
        }
        return mapping.get(code, f"Code {code}")

    @staticmethod
    def fetch_json(url):
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def fetch_weather(self):
        try:
            query = urllib.parse.urlencode({"name": self.location_name, "count": 1, "language": "en", "format": "json"})
            geo = self.fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}")
            results = geo.get("results") or []
            if not results:
                return {"ok": False, "city": self.location_name, "temp": "---", "feels": "---", "condition": "Location not found"}
            item = results[0]
            weather_query = urllib.parse.urlencode({
                "latitude": item["latitude"], "longitude": item["longitude"],
                "current": "temperature_2m,apparent_temperature,weather_code", "timezone": "auto",
            })
            weather = self.fetch_json(f"https://api.open-meteo.com/v1/forecast?{weather_query}")
            current = weather.get("current", {})
            temp = current.get("temperature_2m")
            feels = current.get("apparent_temperature")
            code = current.get("weather_code")
            city_label = f"{item.get('name', self.location_name)}, {item.get('country', '')}".strip(", ")
            return {
                "ok": True,
                "city": city_label,
                "temp": "---" if temp is None else f"{float(temp):.1f} °C",
                "feels": "---" if feels is None else f"{float(feels):.1f} °C",
                "condition": self.weather_code_to_text(code),
            }
        except Exception:
            return {"ok": False, "city": self.location_name, "temp": "---", "feels": "---", "condition": "Weather unavailable"}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while self.running:
            with self.lock:
                self.last = self.fetch_weather()
            for _ in range(max(1, int(WEATHER_REFRESH_MS / 1000))):
                if not self.running:
                    return
                time.sleep(1)

    def get(self):
        with self.lock:
            return dict(self.last)

    def stop(self):
        self.running = False

# =========================
# FACE CAMERA / RECOGNITION
# =========================
class CameraService:
    MATCH_TH = 0.35
    UNCERTAIN_TH = 0.45
    YOLO_CONF = 0.15
    YOLO_IOU = 0.45
    BOX_PADDING = 0.15
    MIN_FACE_SIZE = 120
    RECOG_INTERVAL_SEC = 1.20
    IMAGES_PER_PERSON = 20
    CAPTURE_INTERVAL = 0.50

    def __init__(self):
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.camera = None
        self.usb_cap = None
        self.cv2 = None
        self.np = None
        self.YOLO = None
        self.DeepFace = None
        self.model = None
        self.camera_ok = False
        self.yolo_ok = False
        self.deepface_ok = False
        self.model_msg = "Vision stack not loaded"
        self.frame_jpeg = TINY_JPEG
        self.crop_jpeg = TINY_JPEG
        self.status = {
            "camera_text": "Camera not started",
            "detail_text": "Waiting for backend startup",
            "identity_text": "---",
            "status_text": "Waiting for face",
            "distance_text": "---",
            "action_mode": None,
            "action_name": "",
            "action_captured": 0,
        }
        self.face_db = {}
        self.last_recog_t = 0.0
        self.frame_index = 0
        self.last_bbox = None
        self.last_result = {"identity_text": "---", "status_text": "Waiting for face", "distance_text": "---", "label_text": "NO FACE", "color": (0, 0, 255)}
        self.action_mode = None
        self.action_name = ""
        self.action_embeddings = []
        self.action_captured = 0
        self.action_existing_count = 0
        self.last_capture_t = 0.0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def list_identities(self):
        if not os.path.isdir(FACE_DB_PATH):
            return []
        names = [p for p in os.listdir(FACE_DB_PATH) if os.path.isdir(os.path.join(FACE_DB_PATH, p))]
        names.sort(key=lambda s: s.lower())
        return names

    def load_database(self):
        db = {}
        if self.np is None or not os.path.isdir(FACE_DB_PATH):
            return db
        for person in self.list_identities():
            emb_path = os.path.join(FACE_DB_PATH, person, "embedding.npy")
            if os.path.isfile(emb_path):
                try:
                    db[person] = self.np.load(emb_path)
                except Exception:
                    pass
        return db

    def refresh_database(self):
        self.face_db = self.load_database()
        if self.yolo_ok and self.deepface_ok:
            self.model_msg = f"YOLO + ArcFace ready | DB entries: {len(self.face_db)}"
        self._update_status(detail_text=self.model_msg)

    def _update_status(self, **kwargs):
        with self.lock:
            self.status.update(kwargs)
            self.status.update({
                "action_mode": self.action_mode,
                "action_name": self.action_name,
                "action_captured": self.action_captured,
            })

    def get_status(self):
        with self.lock:
            return dict(self.status)

    def get_frame(self):
        with self.lock:
            return self.frame_jpeg

    def get_crop(self):
        with self.lock:
            return self.crop_jpeg

    def set_frame(self, jpeg_bytes):
        with self.lock:
            self.frame_jpeg = jpeg_bytes

    def set_crop(self, jpeg_bytes):
        with self.lock:
            self.crop_jpeg = jpeg_bytes

    def _encode_jpeg(self, image, quality=82):
        ok, buffer = self.cv2.imencode(".jpg", image, [int(self.cv2.IMWRITE_JPEG_QUALITY), quality])
        return buffer.tobytes() if ok else TINY_JPEG

    def cosine_distance(self, a, b):
        a = self.np.asarray(a, dtype=self.np.float32)
        b = self.np.asarray(b, dtype=self.np.float32)
        denom = self.np.linalg.norm(a) * self.np.linalg.norm(b)
        if denom == 0:
            return 1.0
        return 1.0 - float(self.np.dot(a, b) / denom)

    def identify(self, embedding):
        best_name = None
        best_dist = 1.0
        for name, ref_emb in self.face_db.items():
            dist = self.cosine_distance(embedding, ref_emb)
            if dist < best_dist:
                best_name = name
                best_dist = dist
        return best_name, best_dist

    @staticmethod
    def expand_box(x1, y1, x2, y2, w, h, pad):
        bw, bh = x2 - x1, y2 - y1
        px, py = int(bw * pad), int(bh * pad)
        return max(0, x1 - px), max(0, y1 - py), min(w, x2 + px), min(h, y2 + py)

    def get_embedding(self, face_bgr):
        emb = self.DeepFace.represent(
            img_path=face_bgr,
            model_name="ArcFace",
            enforce_detection=False,
            detector_backend="skip",
            align=True,
        )
        return self.np.asarray(emb[0]["embedding"], dtype=self.np.float32)

    def _status_for_action(self, msg, state="processing"):
        identity = self.action_name if self.action_name else "---"
        color = (0, 255, 255) if state == STATUS_PROCESSING else ((0, 255, 0) if state == STATUS_READY else (0, 0, 255))
        self.last_result = {
            "identity_text": identity,
            "status_text": msg,
            "distance_text": "---",
            "label_text": msg.upper()[:24],
            "color": color,
        }
        self._update_status(identity_text=identity, status_text=msg, distance_text="---")

    def _reset_action_state(self):
        self.action_mode = None
        self.action_name = ""
        self.action_embeddings = []
        self.action_captured = 0
        self.action_existing_count = 0
        self._update_status()

    def _count_existing_images(self, person_dir):
        img_dir = os.path.join(person_dir, "images")
        if not os.path.isdir(img_dir):
            return 0
        return sum(1 for fname in os.listdir(img_dir) if fname.lower().endswith(IMAGE_EXTS))

    def _prepare_action_target(self):
        person_dir = person_dir_for(self.action_name)
        img_dir = os.path.join(person_dir, "images")
        os.makedirs(person_dir, exist_ok=True)
        os.makedirs(img_dir, exist_ok=True)
        return person_dir, img_dir

    def request_refresh(self):
        self.refresh_database()
        self._status_for_action("Database refreshed", STATUS_READY)
        return {"ok": True, "message": "Database refreshed", "users": self.list_identities()}

    def request_register(self, name):
        name = safe_person_name(name)
        if not name:
            return {"ok": False, "message": "Enter a user name"}
        if self.action_mode is not None:
            return {"ok": False, "message": "Action already in progress"}
        person_dir = os.path.join(FACE_DB_PATH, name)
        if os.path.isdir(person_dir):
            return {"ok": False, "message": "Name exists - use Update"}
        os.makedirs(os.path.join(person_dir, "images"), exist_ok=True)
        self.action_mode = "register"
        self.action_name = name
        self.action_embeddings = []
        self.action_captured = 0
        self.action_existing_count = 0
        self.last_capture_t = 0.0
        self._status_for_action(f"Registering {name} (0/{self.IMAGES_PER_PERSON})", STATUS_PROCESSING)
        return {"ok": True, "message": f"Registering {name}; look at the camera"}

    def request_update(self, name):
        name = safe_person_name(name)
        if not name:
            return {"ok": False, "message": "Enter or pick a user"}
        if self.action_mode is not None:
            return {"ok": False, "message": "Action already in progress"}
        person_dir = os.path.join(FACE_DB_PATH, name)
        if not os.path.isdir(person_dir):
            return {"ok": False, "message": "Name not found in DB"}
        os.makedirs(os.path.join(person_dir, "images"), exist_ok=True)
        self.action_mode = "update"
        self.action_name = name
        self.action_embeddings = []
        self.action_captured = 0
        self.action_existing_count = self._count_existing_images(person_dir)
        self.last_capture_t = 0.0
        self._status_for_action(f"Updating {name} (+{self.IMAGES_PER_PERSON}, base={self.action_existing_count})", STATUS_PROCESSING)
        return {"ok": True, "message": f"Updating {name}; look at the camera"}

    def _delete_rows_from_csv_for_name(self, name_to_delete):
        if not os.path.exists(LOG_CSV):
            return
        tmp_path = LOG_CSV + ".tmp"
        with open(LOG_CSV, "r", newline="", encoding="utf-8") as fin, open(tmp_path, "w", newline="", encoding="utf-8") as fout:
            reader = csv.reader(fin)
            writer = csv.writer(fout)
            header = next(reader, None)
            if header is None:
                return
            writer.writerow(header)
            try:
                name_idx = header.index("name")
            except ValueError:
                shutil.move(tmp_path, LOG_CSV)
                return
            for row in reader:
                if len(row) <= name_idx or row[name_idx] != name_to_delete:
                    writer.writerow(row)
        shutil.move(tmp_path, LOG_CSV)

    def request_delete(self, name):
        name = safe_person_name(name)
        if not name:
            return {"ok": False, "message": "Pick a user to delete"}
        if self.action_mode is not None:
            return {"ok": False, "message": "Wait for current action"}
        person_dir = os.path.join(FACE_DB_PATH, name)
        if not os.path.isdir(person_dir):
            return {"ok": False, "message": "Name not found in DB"}
        shutil.rmtree(person_dir, ignore_errors=True)
        try:
            self._delete_rows_from_csv_for_name(name)
        except Exception:
            pass
        self.refresh_database()
        self._status_for_action(f"Deleted {name}", STATUS_READY)
        return {"ok": True, "message": f"Deleted {name}", "users": self.list_identities()}

    def _save_action_sample(self, face_crop_bgr):
        person_dir, img_dir = self._prepare_action_target()
        filename = f"{self.action_captured:02d}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        self.cv2.imwrite(os.path.join(img_dir, filename), face_crop_bgr)
        emb = self.get_embedding(face_crop_bgr)
        self.action_embeddings.append(emb)
        self.action_captured += 1
        verb = "Registering" if self.action_mode == "register" else "Updating"
        self._status_for_action(f"{verb} {self.action_name} ({self.action_captured}/{self.IMAGES_PER_PERSON})", STATUS_PROCESSING)
        if self.action_captured >= self.IMAGES_PER_PERSON:
            avg_emb = self.np.mean(self.np.vstack(self.action_embeddings), axis=0).astype(self.np.float32)
            tmp_path = os.path.join(person_dir, "embedding.npy.tmp")
            final_path = os.path.join(person_dir, "embedding.npy")
            with open(tmp_path, "wb") as f:
                self.np.save(f, avg_emb)
            os.replace(tmp_path, final_path)
            self.refresh_database()
            finished_name = self.action_name
            finished_verb = "Registered" if self.action_mode == "register" else "Updated"
            self._reset_action_state()
            self._status_for_action(f"{finished_verb} {finished_name}", STATUS_READY)

    def init_runtime(self):
        try:
            import cv2
            import numpy as np
            self.cv2 = cv2
            self.np = np
        except Exception as e:
            self._update_status(camera_text="Camera offline", detail_text=f"OpenCV/NumPy import failed: {e}", identity_text="Unavailable", status_text="Camera initialization failed", distance_text="---")
            return

        try:
            from ultralytics import YOLO
            self.YOLO = YOLO
            model_path = os.path.join(BASE_DIR, "best.pt")
            if os.path.isfile(model_path):
                self.model = YOLO(model_path)
                self.yolo_ok = True
            else:
                self.model_msg = "best.pt not found"
        except Exception as e:
            self.yolo_ok = False
            self.model_msg = f"YOLO unavailable: {e}"

        try:
            from deepface import DeepFace
            self.DeepFace = DeepFace
            self.deepface_ok = True
        except Exception as e:
            self.deepface_ok = False
            self.model_msg += f" | DeepFace unavailable: {e}"

        try:
            from picamera2 import Picamera2
            from libcamera import controls
            self.camera = Picamera2()
            preview_config = self.camera.create_preview_configuration(main={"size": CAMERA_SIZE, "format": "XRGB8888"})
            self.camera.configure(preview_config)
            self.camera.start()
            time.sleep(1.0)
            try:
                self.camera.set_controls({"AeEnable": True, "AwbEnable": True, "AwbMode": controls.AwbModeEnum.Auto, "Brightness": 0.0, "Contrast": 1.0, "Saturation": 1.0, "Sharpness": 1.0})
            except Exception:
                try:
                    self.camera.set_controls({"AeEnable": True, "AwbEnable": True})
                except Exception:
                    pass
            self.camera_ok = True
            cam_text = "Pi Camera ready"
        except Exception as pi_error:
            try:
                self.usb_cap = self.cv2.VideoCapture(0)
                self.usb_cap.set(self.cv2.CAP_PROP_FRAME_WIDTH, CAMERA_SIZE[0])
                self.usb_cap.set(self.cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_SIZE[1])
                if self.usb_cap.isOpened():
                    self.camera_ok = True
                    cam_text = "USB camera ready"
                    self.model_msg += " | Picamera2 unavailable; using USB camera"
                else:
                    raise RuntimeError("USB camera could not open")
            except Exception as usb_error:
                self.camera_ok = False
                self._update_status(camera_text="Camera offline", detail_text=f"Pi camera failed: {pi_error} | USB camera failed: {usb_error}", identity_text="Unavailable", status_text="Camera initialization failed", distance_text="---")
                return

        self.refresh_database()
        self.model_msg += " | Web MJPEG stream"
        self._update_status(camera_text=cam_text, detail_text=self.model_msg, identity_text="---", status_text="Waiting for face", distance_text="---")

    def _capture_frame_bgr(self):
        if self.camera is not None:
            req = None
            try:
                req = self.camera.capture_request()
                frame_xrgb = req.make_array("main").copy()
                return self.cv2.cvtColor(frame_xrgb, self.cv2.COLOR_BGRA2BGR)
            finally:
                if req is not None:
                    try:
                        req.release()
                    except Exception:
                        pass
        if self.usb_cap is not None:
            ok, frame = self.usb_cap.read()
            if ok:
                frame = self.cv2.resize(frame, CAMERA_SIZE)
                return frame
        return None

    def _process_detection_frame(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        bbox = None
        face_crop_bgr = None
        result = dict(self.last_result)

        if self.yolo_ok and self.model is not None:
            results = self.model(frame_bgr, imgsz=256, conf=self.YOLO_CONF, iou=self.YOLO_IOU, verbose=False)[0]
            if results.boxes is not None and len(results.boxes) > 0:
                confs = results.boxes.conf.cpu().numpy()
                best_i = int(self.np.argmax(confs))
                box = results.boxes.xyxy.cpu().numpy()[best_i]
                x1, y1, x2, y2 = map(int, box)
                x1, y1, x2, y2 = self.expand_box(x1, y1, x2, y2, w, h, self.BOX_PADDING)
                bbox = (x1, y1, x2, y2)
                crop = frame_bgr[y1:y2, x1:x2]
                if crop.shape[0] >= self.MIN_FACE_SIZE and crop.shape[1] >= self.MIN_FACE_SIZE:
                    face_crop_bgr = crop.copy()

        if bbox is None or face_crop_bgr is None:
            if self.action_mode is None:
                result = {"identity_text": "---", "status_text": "No face detected", "distance_text": "---", "label_text": "NO FACE", "color": (0, 0, 255)}
            else:
                verb = "Registering" if self.action_mode == "register" else "Updating"
                self._status_for_action(f"{verb} {self.action_name} - no face", STATUS_PROCESSING)
                result = dict(self.last_result)
            self.set_crop(TINY_JPEG)
        else:
            self.set_crop(self._encode_jpeg(face_crop_bgr, quality=82))
            now = time.time()
            if self.action_mode is not None:
                if (now - self.last_capture_t) >= self.CAPTURE_INTERVAL:
                    self.last_capture_t = now
                    try:
                        self._save_action_sample(face_crop_bgr)
                    except Exception as e:
                        self._status_for_action(f"Enrollment failed: {e}", STATUS_FAIL)
                        self._reset_action_state()
                result = dict(self.last_result)
            elif self.deepface_ok and (now - self.last_recog_t) >= self.RECOG_INTERVAL_SEC:
                self.last_recog_t = now
                try:
                    live_emb = self.get_embedding(face_crop_bgr)
                    if self.face_db:
                        best_name, best_dist = self.identify(live_emb)
                        if best_name and best_dist <= self.MATCH_TH:
                            result = {"identity_text": best_name, "status_text": "Recognized", "distance_text": f"{best_dist:.4f}", "label_text": f"{best_name} d={best_dist:.2f}", "color": (0, 255, 0)}
                        elif best_name and best_dist <= self.UNCERTAIN_TH:
                            result = {"identity_text": f"Maybe {best_name}", "status_text": "Uncertain match", "distance_text": f"{best_dist:.4f}", "label_text": f"Maybe {best_name} d={best_dist:.2f}", "color": (0, 255, 255)}
                        else:
                            result = {"identity_text": "Unknown", "status_text": "Unknown person", "distance_text": f"{best_dist:.4f}" if best_name else "---", "label_text": "UNKNOWN", "color": (0, 0, 255)}
                    else:
                        result = {"identity_text": "DB empty", "status_text": "Face detected", "distance_text": "---", "label_text": "DB EMPTY", "color": (0, 255, 255)}
                except Exception:
                    result = {"identity_text": "Error", "status_text": "Embedding error", "distance_text": "---", "label_text": "EMB ERROR", "color": (0, 0, 255)}
            elif not self.deepface_ok:
                result = {"identity_text": "DeepFace offline", "status_text": "Face detected", "distance_text": "---", "label_text": "FACE", "color": (0, 255, 255)}

        self.last_bbox = bbox
        self.last_result = result
        self._update_status(identity_text=result["identity_text"], status_text=result["status_text"], distance_text=result["distance_text"])
        return bbox, result

    def run(self):
        self.init_runtime()
        if not self.camera_ok:
            return
        while self.running:
            try:
                frame_bgr = self._capture_frame_bgr()
                if frame_bgr is None:
                    time.sleep(0.2)
                    continue
                self.frame_index += 1
                if self.frame_index % 6 == 0 or self.action_mode is not None:
                    bbox, result = self._process_detection_frame(frame_bgr)
                else:
                    bbox, result = self.last_bbox, self.last_result

                display_bgr = frame_bgr.copy()
                if bbox is not None:
                    x1, y1, x2, y2 = bbox
                    color = result.get("color", (0, 0, 255))
                    label_text = result.get("label_text", "FACE")
                    self.cv2.rectangle(display_bgr, (x1, y1), (x2, y2), color, 2)
                    self.cv2.rectangle(display_bgr, (x1, max(0, y1 - 32)), (x2, y1), color, -1)
                    self.cv2.putText(display_bgr, label_text, (x1 + 6, max(18, y1 - 10)), self.cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, self.cv2.LINE_AA)
                self.cv2.putText(display_bgr, "Raspberry Pi Camera", (12, 24), self.cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, self.cv2.LINE_AA)
                self.cv2.putText(display_bgr, datetime.now().strftime("%H:%M:%S"), (display_bgr.shape[1] - 110, 24), self.cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, self.cv2.LINE_AA)
                self.set_frame(self._encode_jpeg(display_bgr, quality=82))
                time.sleep(CAMERA_REFRESH_MS / 1000.0)
            except Exception as e:
                self._update_status(camera_text="Camera runtime error", detail_text=str(e), identity_text="Unavailable", status_text="Camera runtime error", distance_text="---")
                time.sleep(0.5)

    def stop(self):
        self.running = False
        try:
            if self.camera is not None:
                self.camera.stop()
        except Exception:
            pass
        try:
            if self.usb_cap is not None:
                self.usb_cap.release()
        except Exception:
            pass

sensor_hub = SensorHub()
weather_service = WeatherService(WEATHER_LOCATION)
camera_service = CameraService()
_services_started = False
_services_lock = threading.Lock()


def start_services_once():
    global _services_started
    with _services_lock:
        if _services_started:
            return
        init_database()
        sync_facedb_users_to_database()
        sensor_hub.start()
        weather_service.start()
        camera_service.start()
        _services_started = True


# =========================
# CALENDAR / MIRROR HOME
# =========================
def _add_calendar_event(events, event_date, title, source="public"):
    if event_date is None:
        return
    if isinstance(event_date, datetime):
        event_date = event_date.date()
    if not isinstance(event_date, date):
        try:
            event_date = date.fromisoformat(str(event_date))
        except Exception:
            return
    key = event_date.isoformat()
    event = {"title": str(title), "source": source}
    if event not in events.setdefault(key, []):
        events[key].append(event)


def _fixed_public_events_for_year(year):
    events = {}
    fixed = [
        (1, 7, "Coptic Christmas"),
        (1, 25, "Police Day / 25 January Revolution"),
        (4, 25, "Sinai Liberation Day"),
        (5, 1, "Labour Day"),
        (6, 30, "30 June Revolution"),
        (7, 23, "23 July Revolution"),
        (10, 6, "Armed Forces Day"),
    ]
    for month, day, title in fixed:
        try:
            _add_calendar_event(events, date(year, month, day), title, "built-in")
        except Exception:
            pass
    return events


def _tabular_gregorian_to_hijri(g_date):
    """Convert Gregorian date to a tabular Hijri date without external packages.

    This fallback keeps Ramadan/Eid markers visible even on Windows preview mode
    or on a Raspberry Pi where optional Hijri packages were not installed. The
    result may differ by about one day from official moon-sighting dates.
    """
    import math

    y, m, d = g_date.year, g_date.month, g_date.day
    if m <= 2:
        y -= 1
        m += 12
    a = math.floor(y / 100)
    b = 2 - a + math.floor(a / 4)
    jd = (
        math.floor(365.25 * (y + 4716))
        + math.floor(30.6001 * (m + 1))
        + d
        + b
        - 1524.5
    )
    jd = math.floor(jd) + 0.5

    def islamic_to_jd(h_year, h_month, h_day):
        return (
            h_day
            + math.ceil(29.5 * (h_month - 1))
            + (h_year - 1) * 354
            + math.floor((3 + 11 * h_year) / 30)
            + 1948439.5
            - 1
        )

    h_year = math.floor((30 * (jd - 1948439.5) + 10646) / 10631)
    h_month = min(12, math.ceil((jd - (29 + islamic_to_jd(h_year, 1, 1))) / 29.5) + 1)
    h_day = int(jd - islamic_to_jd(h_year, h_month, 1) + 1)
    return int(h_year), int(h_month), int(h_day)


def calendar_public_events_for_year(year):
    """Return public/Hijri events for the mirror calendar.

    The app works offline with fixed built-in events. If the optional `holidays`
    package is installed, it adds Egypt public holidays. For Islamic events, it
    first tries `hijridate`/`hijri_converter`; if those packages are missing, it
    uses a built-in tabular Hijri calculation so Ramadan, Eid al-Fitr, Arafat
    Day, and Eid al-Adha still appear in the month-view calendar. Official
    Islamic holiday dates can shift by moon sighting, so these are reminders,
    not legal/official confirmation.
    """
    events = _fixed_public_events_for_year(year)

    try:
        import holidays
        try:
            eg_holidays = holidays.country_holidays("EG", years=[year])
        except Exception:
            eg_holidays = holidays.country_holidays("Egypt", years=[year])
        for event_date, title in eg_holidays.items():
            _add_calendar_event(events, event_date, title, "egypt-holidays")
    except Exception:
        pass

    Gregorian = None
    try:
        from hijridate import Gregorian as _Gregorian
        Gregorian = _Gregorian
    except Exception:
        try:
            from hijri_converter import Gregorian as _Gregorian
            Gregorian = _Gregorian
        except Exception:
            Gregorian = None

    hijri_events = {
        (1, 1): "Islamic New Year",
        (3, 12): "Prophet's Birthday",
        (9, 1): "Ramadan begins",
        (9, 27): "Laylat al-Qadr",
        (10, 1): "Eid al-Fitr",
        (10, 2): "Eid al-Fitr holiday",
        (10, 3): "Eid al-Fitr holiday",
        (12, 9): "Arafat Day",
        (12, 10): "Eid al-Adha",
        (12, 11): "Eid al-Adha holiday",
        (12, 12): "Eid al-Adha holiday",
        (12, 13): "Eid al-Adha holiday",
    }

    d = date(year, 1, 1)
    end = date(year, 12, 31)
    while d <= end:
        try:
            if Gregorian is not None:
                hijri = Gregorian(d.year, d.month, d.day).to_hijri()
                hijri_key = (int(hijri.month), int(hijri.day))
                source = "hijri"
            else:
                _hy, hm, hd = _tabular_gregorian_to_hijri(d)
                hijri_key = (hm, hd)
                source = "hijri-tabular"
            title = hijri_events.get(hijri_key)
            if title:
                _add_calendar_event(events, d, title, source)
        except Exception:
            pass
        d += timedelta(days=1)

    return events


def calendar_notes_between(start_date, end_date):
    init_database()
    notes = {}
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT note_date, note_text FROM calendar_notes
            WHERE note_date >= ? AND note_date <= ?
            ORDER BY note_date
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        for row in rows:
            notes[row["note_date"]] = row["note_text"]
    return notes


def calendar_month_payload(year, month):
    year = int(year)
    month = int(month)
    if month < 1 or month > 12:
        raise ValueError("Month must be between 1 and 12")
    first = date(year, month, 1)
    days_in_month = py_calendar.monthrange(year, month)[1]
    last = date(year, month, days_in_month)
    events = calendar_public_events_for_year(year)
    notes = calendar_notes_between(first, last)
    today_iso = date.today().isoformat()

    days = []
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        iso = d.isoformat()
        days.append({
            "date": iso,
            "day": day_num,
            "weekday": d.weekday(),
            "is_today": iso == today_iso,
            "events": events.get(iso, []),
            "note": notes.get(iso, ""),
        })

    upcoming = []
    today = date.today()
    for iso, event_list in sorted(events.items()):
        try:
            d = date.fromisoformat(iso)
        except Exception:
            continue
        if d >= today and len(upcoming) < 8:
            for ev in event_list:
                upcoming.append({"date": iso, **ev})
                if len(upcoming) >= 8:
                    break

    return {
        "ok": True,
        "year": year,
        "month": month,
        "month_name": first.strftime("%B %Y"),
        "first_weekday": first.weekday(),
        "days": days,
        "upcoming_events": upcoming,
        "note": "Public/Hijri event dates are generated locally and may vary by official moon sighting.",
    }


def save_calendar_note(note_date, note_text):
    init_database()
    note_date = str(note_date or "").strip()
    note_text = str(note_text or "").strip()
    try:
        date.fromisoformat(note_date)
    except Exception:
        raise ValueError("Invalid note date")
    timestamp = now_iso()
    with db_connect() as conn:
        if note_text:
            conn.execute(
                """
                INSERT INTO calendar_notes (note_date, note_text, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(note_date) DO UPDATE SET
                    note_text = excluded.note_text,
                    updated_at = excluded.updated_at
                """,
                (note_date, note_text, timestamp, timestamp),
            )
        else:
            conn.execute("DELETE FROM calendar_notes WHERE note_date = ?", (note_date,))
        conn.commit()
    return {"ok": True, "date": note_date, "note": note_text, "message": "Calendar note saved" if note_text else "Calendar note removed"}


# =========================
# FLASK ROUTES
# =========================
@app.route("/")
def index():
    lab_value_fields = [(key, label) for key, label in LAB_FIELDS if key not in ("age", "gender")]
    return render_template("index.html", lab_fields=LAB_FIELDS, lab_value_fields=lab_value_fields, symptom_options=SYMPTOM_OPTIONS)


def mjpeg_generator(getter, delay=0.05):
    while True:
        frame = getter() or TINY_JPEG
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        time.sleep(delay)


@app.route("/video_feed")
def video_feed():
    return Response(mjpeg_generator(camera_service.get_frame), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/face_crop_feed")
def face_crop_feed():
    return Response(mjpeg_generator(camera_service.get_crop, delay=0.12), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/status")
def api_status():
    now = datetime.now()

    # 12-hour clock format for Home and Summary screens.
    # lstrip("0") removes the leading zero so 06:05 PM becomes 6:05 PM.
    time_12 = now.strftime("%I:%M:%S %p").lstrip("0")
    time_short_12 = now.strftime("%I:%M %p").lstrip("0")

    return jsonify({
        "ok": True,
        "datetime": {
            "time": time_12,
            "time_short": time_short_12,
            "date": now.strftime("%A, %d %B %Y"),
            "zone": "Local time",
        },
        "weather": weather_service.get(),
        "sensors": sensor_hub.get(),
        "camera": camera_service.get_status(),
        "users": camera_service.list_identities(),
    })


@app.route("/api/users")
def api_users():
    return jsonify({"ok": True, "users": camera_service.list_identities()})


@app.route("/api/face/refresh", methods=["POST"])
def api_face_refresh():
    return jsonify(camera_service.request_refresh())


@app.route("/api/face/register", methods=["POST"])
def api_face_register():
    data = request.get_json(silent=True) or {}
    return jsonify(camera_service.request_register(data.get("name")))


@app.route("/api/face/update", methods=["POST"])
def api_face_update():
    data = request.get_json(silent=True) or {}
    return jsonify(camera_service.request_update(data.get("name")))


@app.route("/api/face/delete", methods=["POST"])
def api_face_delete():
    data = request.get_json(silent=True) or {}
    result = camera_service.request_delete(data.get("name"))
    if result.get("ok"):
        try:
            delete_db_user(data.get("name"))
        except Exception:
            pass
    return jsonify(result)


@app.route("/api/lab/<person_name>", methods=["GET"])
def api_lab_get(person_name):
    try:
        return jsonify({"ok": True, "name": safe_person_name(person_name), "values": load_lab_results(person_name)})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/lab/<person_name>", methods=["POST"])
def api_lab_save(person_name):
    try:
        data = request.get_json(silent=True) or {}
        values = data.get("values", data)
        dob = data.get("dob")
        gender = data.get("gender")
        clean = clean_lab_values(values, dob=dob, gender=gender)
        saved = save_lab_results(person_name, clean)
        analysis = analyze_saved_lab_results(saved)
        ai_prediction = None
        ensure_db_user(person_name, dob=dob, gender=gender, symptoms=data.get("symptoms"))
        if has_any_lab_value(saved):
            try:
                ai_prediction = predict_liver_ai_from_values(saved)
            except Exception as e:
                ai_prediction = {"title": "AI Prediction Unavailable", "state": STATUS_PROCESSING, "detail_text": str(e)}
            upsert_lab_result(person_name, saved, source="manual", analysis=analysis, ai_prediction=ai_prediction, dob=dob, gender=gender)
        return jsonify({"ok": True, "message": f"Lab results saved for {safe_person_name(person_name)}", "values": saved, "analysis": analysis, "ai_prediction": ai_prediction})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/lab/<person_name>/analyze", methods=["POST"])
def api_lab_analyze(person_name):
    try:
        values = load_lab_results(person_name)
        return jsonify({"ok": True, "analysis": analyze_saved_lab_results(values)})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/lab/<person_name>/predict", methods=["POST"])
def api_lab_predict(person_name):
    try:
        values = load_lab_results(person_name)
        return jsonify({"ok": True, "analysis": predict_liver_ai_from_values(values)})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/lab/<person_name>/upload_pdf", methods=["POST"])
def api_lab_upload_pdf(person_name):
    try:
        if "pdf" not in request.files:
            return jsonify({"ok": False, "message": "No PDF file uploaded"}), 400
        file = request.files["pdf"]
        if not file.filename.lower().endswith(".pdf"):
            return jsonify({"ok": False, "message": "Please upload a PDF file"}), 400
        name = safe_person_name(person_name)
        filename = f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        pdf_path = os.path.join(UPLOAD_DIR, filename)
        file.save(pdf_path)
        text, method = extract_text_from_pdf(pdf_path)
        extracted = extract_liver_values_from_text(text)
        current = load_lab_results(name)
        for key, value in extracted.items():
            if value is not None:
                current[key] = value
        saved = save_lab_results(name, current)
        analysis = analyze_saved_lab_results(saved)
        ai_prediction = None
        if has_any_lab_value(saved):
            try:
                ai_prediction = predict_liver_ai_from_values(saved)
            except Exception as e:
                ai_prediction = {"title": "AI Prediction Unavailable", "state": STATUS_PROCESSING, "detail_text": str(e)}
        upsert_lab_result(name, saved, source="pdf", pdf_filename=filename, analysis=analysis, ai_prediction=ai_prediction)
        return jsonify({"ok": True, "message": f"PDF processed using {method}", "values": saved, "extracted": extracted, "analysis": analysis, "ai_prediction": ai_prediction, "pdf_filename": filename})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500



@app.route("/api/calendar/month")
def api_calendar_month():
    try:
        today = date.today()
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
        return jsonify(calendar_month_payload(year, month))
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/calendar/note", methods=["POST"])
def api_calendar_note():
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(save_calendar_note(data.get("date"), data.get("note")))
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/db/summary")
def api_db_summary():
    try:
        return jsonify(database_summary(request.args.get("name")))
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/db/user/<person_name>")
def api_db_user(person_name):
    try:
        init_database()
        name = safe_person_name(person_name)
        with db_connect() as conn:
            user = row_to_dict(conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone())
            lab = row_to_dict(conn.execute(
                """
                SELECT lr.* FROM lab_results lr
                JOIN users u ON u.id = lr.user_id
                WHERE u.name = ?
                """, (name,)
            ).fetchone())
        user = attach_symptom_tags(user)
        if lab:
            lab["values"] = json_loads_maybe(lab.get("values_json"), {})
            lab["analysis"] = json_loads_maybe(lab.get("analysis_json"), {})
            lab["ai_prediction"] = json_loads_maybe(lab.get("ai_prediction_json"), {})
        return jsonify({"ok": True, "user": user, "lab": lab, "capture": current_capture(name)})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/workflow/start", methods=["POST"])
def api_workflow_start():
    try:
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        dob = data.get("dob")
        gender = data.get("gender")
        user = ensure_db_user(name, dob=dob, gender=gender, symptoms=data.get("symptoms"))
        reset_capture(user["name"])
        session = update_capture_step(user["name"], "started", {"captured_at": now_iso(), "message": "Started. Capture camera first."})
        return jsonify({"ok": True, "message": "Started checkup. Step 1: capture camera.", "user": user, "capture": session})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/workflow/capture_camera", methods=["POST"])
def api_workflow_capture_camera():
    try:
        data = request.get_json(silent=True) or {}
        user = ensure_db_user(data.get("name"), dob=data.get("dob"), gender=data.get("gender"), symptoms=data.get("symptoms"))
        status = camera_service.get_status()
        payload = {
            "captured_at": now_iso(),
            "camera_text": status.get("camera_text"),
            "detail_text": status.get("detail_text"),
            "identity_text": status.get("identity_text"),
            "status_text": status.get("status_text"),
            "distance_text": status.get("distance_text"),
            "pics_saved": count_person_images(user["name"]),
        }
        session = update_capture_step(user["name"], "camera", payload)
        return jsonify({"ok": True, "message": "Camera step captured. Step 2: capture temperature.", "capture": session, "step": payload})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/workflow/capture_temperature", methods=["POST"])
def api_workflow_capture_temperature():
    try:
        data = request.get_json(silent=True) or {}
        name = safe_person_name(data.get("name"))
        if not name:
            raise ValueError("Enter or select a user name first")
        session = current_capture(name)
        if "camera" not in session.get("steps", {}):
            raise ValueError("Capture the camera step first")
        sensor_data = sensor_hub.get()
        temp = dict(sensor_data.get("temperature", {}))
        temp["captured_at"] = now_iso()
        session = update_capture_step(name, "temperature", temp)
        return jsonify({"ok": True, "message": "Temperature captured. Step 3: capture heart rate.", "capture": session, "step": temp})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/workflow/capture_heart", methods=["POST"])
def api_workflow_capture_heart():
    try:
        data = request.get_json(silent=True) or {}
        name = safe_person_name(data.get("name"))
        if not name:
            raise ValueError("Enter or select a user name first")
        session = current_capture(name)
        steps = session.get("steps", {})
        if "camera" not in steps:
            raise ValueError("Capture the camera step first")
        if "temperature" not in steps:
            raise ValueError("Capture the temperature step first")
        sensor_data = sensor_hub.get()
        heart = dict(sensor_data.get("heart", {}))
        heart["captured_at"] = now_iso()
        session = update_capture_step(name, "heart", heart)
        return jsonify({"ok": True, "message": "Heart step captured. You can now save the checkup to the database.", "capture": session, "step": heart})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/workflow/save", methods=["POST"])
def api_workflow_save():
    try:
        data = request.get_json(silent=True) or {}
        result = save_measurement_session(
            data.get("name"),
            dob=data.get("dob"),
            gender=data.get("gender"),
            symptoms=data.get("symptoms") or [],
            lab_values=data.get("lab_values") or {},
            notes=data.get("notes") or "",
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    sensor_hub.stop()
    weather_service.stop()
    camera_service.stop()
    return jsonify({"ok": True, "message": "Backend services stopped"})


if __name__ == "__main__":
    start_services_once()
    print("\nAI Smart Mirror Web Dashboard")
    print("Local PC/Pi: http://127.0.0.1:5000")
    print("LAN access:  http://<RASPBERRY_PI_IP>:5000")
    print("Press Ctrl+C to stop.\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
