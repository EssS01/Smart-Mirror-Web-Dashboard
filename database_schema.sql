-- AI Smart Mirror local SQLite schema
-- Created automatically in smart_mirror.db by app.py

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
