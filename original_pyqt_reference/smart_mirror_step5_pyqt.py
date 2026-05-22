
import sys
import os
import json
import math
import time
import threading
import urllib.parse
import urllib.request
import re
import csv
import shutil
from datetime import datetime

from PyQt6.QtCore import Qt, QThread, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# =========================
# CONFIG
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FACE_DB_PATH = os.path.join(BASE_DIR, "FaceDB")

# Liver AI model files. Put these four files in BASE_DIR/models/
MODEL_DIR = os.path.join(BASE_DIR, "models")
LIVER_MODEL_PATH = os.path.join(MODEL_DIR, "liver_prediction_model.pkl")
LIVER_COLUMNS_PATH = os.path.join(MODEL_DIR, "liver_model_columns.pkl")
LIVER_ENCODERS_PATH = os.path.join(MODEL_DIR, "liver_label_encoders.pkl")
LIVER_MEDIANS_PATH = os.path.join(MODEL_DIR, "liver_training_medians.pkl")

WEATHER_LOCATION = "Cairo, Egypt"
SENSOR_REFRESH_MS = 450
WEATHER_REFRESH_MS = 15 * 60 * 1000
CAMERA_REFRESH_MS = 33
CAMERA_SIZE = (480, 270)

# Thresholds shown in GUI
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

# Face detection / identification
DETECT_SCALE = 1.1
DETECT_NEIGHBORS = 5
DETECT_MIN_SIZE = (120, 120)
FACE_PREVIEW_SIZE = (200, 200)
LBPH_GOOD_CONF = 70.0
LBPH_UNCERTAIN_CONF = 95.0

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

# Editable default lab reference ranges.
# Adjust these later if you want to match a specific lab's printed reference values.
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


# =========================
# HEART RATE MONITOR
# =========================
from heartrate_monitor import HeartRateMonitor

# =========================
# MLX90614
# =========================
import board
import busio
import adafruit_mlx90614

# =========================
# TCS3200
# =========================
from gpiozero import DigitalInputDevice, OutputDevice


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
    """
    Lightweight rule-based sensor fusion for dashboard screening.
    This is not a diagnosis engine. It simply combines the currently available
    temperature, heart/SpO2, and color outputs into one easy-to-read summary.
    """
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

    score_text = f"Fusion Score: {score}"
    note_text = "Screening summary only - not a medical diagnosis"

    return {
        "title": title,
        "state": state,
        "score_text": score_text,
        "detail_text": detail,
        "note_text": note_text,
    }




def clean_feature_name(name):
    return str(name).replace("\xa0", " ").strip()


# Maps the trained model feature names to the dashboard lab-result keys.
# The model was trained using the dataset's original medical feature names.
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


# Aliases used to read different lab-report styles from a PDF.
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


def gender_to_model_value(value):
    text = str(value or "").strip().lower()
    if text in ("male", "m", "1", "1.0"):
        return 1.0
    if text in ("female", "f", "0", "0.0"):
        return 0.0
    # Keep unknown/other as median later if possible.
    return None


def clean_pdf_text(text):
    text = str(text or "").replace("\xa0", " ")
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_number_after_aliases(text, aliases):
    """
    Extracts the first patient result value after a parameter alias.
    It accepts common separators and ignores units/reference ranges after the first number.
    """
    text = clean_pdf_text(text)
    for alias in aliases:
        pattern = rf"(?:{alias})\s*(?:result|value)?\s*[:=\-]?\s*([0-9]+(?:\.[0-9]+)?)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return safe_float(match.group(1))
    return None


def extract_liver_values_from_text(pdf_text):
    values = {}
    for key, aliases in PDF_PARAMETER_ALIASES.items():
        values[key] = extract_number_after_aliases(pdf_text, aliases)
    return values


def extract_text_from_pdf(pdf_path):
    """
    Reads text from digital PDFs using PyMuPDF. If the PDF is scanned/image-based,
    it falls back to OCR using pdf2image + pytesseract.
    """
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

def analyze_saved_lab_results(values):
    """
    Rule-based liver lab screening.

    This function is used beside the trained AI model. It checks the entered or
    PDF-extracted values against reference ranges, lists all abnormal readings,
    suggests a possible liver-related pattern, and shows a safety warning.

    Important: this is screening support only, not a confirmed medical diagnosis.
    """
    available = 0
    checked = 0
    abnormal = []
    abnormal_details = []
    high_flags = set()
    low_flags = set()

    def _num(name):
        return safe_float(values.get(name))

    # =========================
    # Check each lab parameter
    # =========================
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
            abnormal_details.append(
                f"{label}: {val:g} is LOW compared with reference range {low:g}-{high:g}"
            )
        elif val > high:
            high_flags.add(key)
            abnormal.append(f"{label} is above range")
            abnormal_details.append(
                f"{label}: {val:g} is HIGH compared with reference range {low:g}-{high:g}"
            )

    # Coverage includes all optional lab fields except gender.
    total_optional_numeric = len(LAB_REFERENCE_RANGES) + 1  # + age
    age_value = safe_float(values.get("age"))
    if age_value is not None:
        available += 1
    coverage_text = f"Coverage: {available}/{total_optional_numeric} lab fields entered"

    # =========================
    # Pattern flags
    # =========================
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

    # =========================
    # Determine possible pattern / likely concern
    # =========================
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

    # =========================
    # Severity / warning level
    # =========================
    abnormal_count = len(abnormal)

    # Optional extra escalation for very abnormal values.
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
        advice = (
            "Multiple abnormal readings were detected. Please consult a doctor for proper clinical interpretation."
        )
    else:
        warning_level = "High warning"
        state = STATUS_FAIL
        advice = (
            "Several liver-related readings are outside the reference ranges or one or more readings are markedly "
            "abnormal. Medical consultation is strongly recommended."
        )

    # =========================
    # Build detailed output text
    # =========================
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

    note_text = (
        "Screening support only - not a diagnosis. Please consult a qualified physician for medical diagnosis."
    )

    return {
        "title": pattern,
        "state": state,
        "coverage_text": coverage_text,
        "detail_text": detail_text,
        "advice_text": advice_text,
        "note_text": note_text,
    }


class MLXSensor:
    def __init__(self):
        self.ok = False
        self.last_error = ""
        self.mlx = None
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self.mlx = adafruit_mlx90614.MLX90614(i2c)
            self.ok = True
        except Exception as e:
            self.last_error = str(e)
            self.ok = False

    def read(self):
        if not self.ok or self.mlx is None:
            return {
                "ok": False,
                "text": "Offline",
                "raw": None,
                "error": self.last_error,
            }

        try:
            raw_vals = []
            ambient_vals = []

            for _ in range(MLX_SAMPLES):
                raw_vals.append(float(self.mlx.object_temperature))
                ambient_vals.append(float(self.mlx.ambient_temperature))
                time.sleep(MLX_SAMPLE_DELAY)

            raw_avg = sum(raw_vals) / len(raw_vals)
            ambient_avg = sum(ambient_vals) / len(ambient_vals)

            # Relaxed distance check:
            # only mark "Target too far" when the object reading is both low
            # and too close to ambient temperature.
            if raw_avg < MLX_MIN_VALID_RAW and (raw_avg - ambient_avg) < MLX_MIN_DELTA_OVER_AMBIENT:
                return {
                    "ok": False,
                    "text": "Target too far",
                    "raw": None,
                    "error": f"raw={raw_avg:.2f}C ambient={ambient_avg:.2f}C delta={(raw_avg - ambient_avg):.2f}C",
                }

            temp = round(raw_avg + MLX_CAL_OFFSET, 2)
            return {
                "ok": True,
                "text": f"{temp:.2f} °C",
                "raw": temp,
                "error": f"raw={raw_avg:.2f}C ambient={ambient_avg:.2f}C",
            }
        except Exception as e:
            self.ok = False
            self.last_error = str(e)
            return {
                "ok": False,
                "text": "Read error",
                "raw": None,
                "error": self.last_error,
            }


class TCS3200Sensor:
    def __init__(self):
        self.ok = False
        self.last_error = ""

        # Tunable color-sensor settings
        self.samples_per_color = 7
        self.channel_duration = 0.06

        # Very conservative black threshold caused the sensor to read Black too often.
        # Keep it low so Black is only returned for truly very weak readings.
        self.black_total_threshold = 8
        self.dark_total_threshold = 18
        self.bright_total_threshold = 42
        self.gray_balance_threshold = 0.08

        try:
            self.OUT = DigitalInputDevice(23)
            self.S0 = OutputDevice(16)
            self.S1 = OutputDevice(20)
            self.S2 = OutputDevice(5)
            self.S3 = OutputDevice(6)

            # 20% output frequency scaling
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
            return 'Dark'
        if total > self.bright_total_threshold:
            return 'Light'
        return ''

    def classify_color(self, red, green, blue):
        total = red + green + blue
        if total <= 0:
            return 'Unknown', 'Unknown', (0.0, 0.0, 0.0)

        nr = red / total
        ng = green / total
        nb = blue / total
        normalized = (nr, ng, nb)

        mx = max(nr, ng, nb)
        mn = min(nr, ng, nb)

        # Return Black only for truly weak overall readings.
        if total < self.black_total_threshold:
            return 'Black', 'Black', normalized

        # Near-balanced channels with enough signal are treated as White.
        if (mx - mn) < self.gray_balance_threshold:
            if total < self.dark_total_threshold:
                return 'Gray', 'Dark Gray', normalized
            if total > self.bright_total_threshold:
                return 'White', 'White', normalized
            return 'Gray', 'Gray', normalized

        # Mixed colors first.
        if nr > 0.38 and ng > 0.34 and nb < 0.24:
            prefix = self._shade_prefix(total)
            shade = f"{prefix} Yellow".strip()
            return 'Yellow', shade, normalized

        if nr > 0.34 and nb > 0.34 and ng < 0.24:
            prefix = self._shade_prefix(total)
            shade = f"{prefix} Magenta".strip()
            return 'Magenta', shade, normalized

        if ng > 0.34 and nb > 0.34 and nr < 0.24:
            prefix = self._shade_prefix(total)
            shade = f"{prefix} Cyan".strip()
            return 'Cyan', shade, normalized

        # Pure dominant-color case.
        values = {'Red': nr, 'Green': ng, 'Blue': nb}
        base = max(values, key=values.get)
        prefix = self._shade_prefix(total)
        shade = f"{prefix} {base}".strip()
        return base, shade, normalized

    def read(self):
        if not self.ok:
            return {
                'ok': False,
                'dominant': 'Offline',
                'shade': 'Offline',
                'base_color': 'Offline',
                'red': None,
                'green': None,
                'blue': None,
                'norm_red': None,
                'norm_green': None,
                'norm_blue': None,
                'error': self.last_error,
            }

        try:
            red = self._average_channel(0, 0)
            blue = self._average_channel(0, 1)
            green = self._average_channel(1, 1)
            base_color, shade, normalized = self.classify_color(red, green, blue)

            nr, ng, nb = normalized
            return {
                'ok': True,
                'dominant': shade,
                'shade': shade,
                'base_color': base_color,
                'red': round(red, 2),
                'green': round(green, 2),
                'blue': round(blue, 2),
                'norm_red': round(nr, 3),
                'norm_green': round(ng, 3),
                'norm_blue': round(nb, 3),
                'error': '',
            }
        except Exception as e:
            self.ok = False
            self.last_error = str(e)
            return {
                'ok': False,
                'dominant': 'Read error',
                'shade': 'Read error',
                'base_color': 'Read error',
                'red': None,
                'green': None,
                'blue': None,
                'norm_red': None,
                'norm_green': None,
                'norm_blue': None,
                'error': self.last_error,
            }


class HeartSensor:
    def __init__(self):
        self.hrm = None
        self.started = False
        self.start_error = ""

        try:
            self.hrm = HeartRateMonitor(
                print_result=False,
                print_interval=0.5,
                window_size=200,
                finger_lost_timeout=0.35,
                debug=False,
            )
            self.hrm.start()
            self.started = True
        except Exception as e:
            self.start_error = str(e)
            self.started = False

    def read(self):
        if not self.started or self.hrm is None:
            return {
                "ok": False,
                "status": "Heart sensor offline",
                "bpm_text": "---",
                "spo2_text": "---",
                "error": self.start_error,
            }

        try:
            sensor_found = getattr(self.hrm, "sensor_found", False)
            finger_detected = getattr(self.hrm, "finger_detected", False)
            bpm = safe_float(getattr(self.hrm, "bpm", None))
            spo2 = safe_float(getattr(self.hrm, "spo2", None))

            if not sensor_found:
                return {
                    "ok": False,
                    "status": "Heart sensor offline",
                    "bpm_text": "---",
                    "spo2_text": "---",
                    "error": "MAX30102 not detected",
                }

            if not finger_detected:
                return {
                    "ok": True,
                    "status": "Place finger on sensor",
                    "bpm_text": "---",
                    "spo2_text": "---" if spo2 is None else f"{spo2:.1f} %",
                    "error": "",
                }

            if bpm is None:
                return {
                    "ok": True,
                    "status": "Finger detected - stabilizing BPM...",
                    "bpm_text": "---",
                    "spo2_text": "---" if spo2 is None else f"{spo2:.1f} %",
                    "error": "",
                }

            return {
                "ok": True,
                "status": "Reading stable",
                "bpm_text": f"{bpm:.1f} BPM",
                "spo2_text": "---" if spo2 is None else f"{spo2:.1f} %",
                "error": "",
            }

        except Exception as e:
            return {
                "ok": False,
                "status": "Heart read error",
                "bpm_text": "---",
                "spo2_text": "---",
                "error": str(e),
            }

    def stop(self):
        if self.hrm is not None:
            try:
                self.hrm.stop()
            except Exception:
                pass


class SensorWorker(QObject):
    sensor_data = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.running = True
        self.mlx = MLXSensor()
        self.tcs = TCS3200Sensor()
        self.heart = HeartSensor()

    def run(self):
        while self.running:
            payload = {
                "temperature": self.mlx.read(),
                "color": self.tcs.read(),
                "heart": self.heart.read(),
            }
            self.sensor_data.emit(payload)
            time.sleep(SENSOR_REFRESH_MS / 1000.0)

    def stop(self):
        self.running = False
        self.heart.stop()


class WeatherWorker(QObject):
    weather_data = pyqtSignal(dict)

    def __init__(self, location_name):
        super().__init__()
        self.running = True
        self.location_name = location_name

    def weather_code_to_text(self, code):
        mapping = {
            0: "Clear",
            1: "Mostly clear",
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Drizzle",
            55: "Dense drizzle",
            61: "Light rain",
            63: "Rain",
            65: "Heavy rain",
            71: "Light snow",
            73: "Snow",
            75: "Heavy snow",
            80: "Rain showers",
            81: "Rain showers",
            82: "Violent rain showers",
            95: "Thunderstorm",
        }
        return mapping.get(code, f"Code {code}")

    def fetch_json(self, url):
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def fetch_weather(self):
        try:
            query = urllib.parse.urlencode({
                "name": self.location_name,
                "count": 1,
                "language": "en",
                "format": "json",
            })
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?{query}"
            geo = self.fetch_json(geo_url)

            results = geo.get("results") or []
            if not results:
                return {
                    "ok": False,
                    "city": self.location_name,
                    "temp": "---",
                    "feels": "---",
                    "condition": "Location not found",
                }

            item = results[0]
            lat = item["latitude"]
            lon = item["longitude"]
            city = item.get("name", self.location_name)
            country = item.get("country", "")

            weather_query = urllib.parse.urlencode({
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code",
                "timezone": "auto",
            })
            weather_url = f"https://api.open-meteo.com/v1/forecast?{weather_query}"
            weather = self.fetch_json(weather_url)

            current = weather.get("current", {})
            temp = current.get("temperature_2m")
            feels = current.get("apparent_temperature")
            code = current.get("weather_code")

            city_label = f"{city}, {country}".strip(", ")

            return {
                "ok": True,
                "city": city_label,
                "temp": "---" if temp is None else f"{float(temp):.1f} °C",
                "feels": "---" if feels is None else f"{float(feels):.1f} °C",
                "condition": self.weather_code_to_text(code),
            }

        except Exception:
            return {
                "ok": False,
                "city": self.location_name,
                "temp": "---",
                "feels": "---",
                "condition": "Weather unavailable",
            }

    def run(self):
        self.weather_data.emit(self.fetch_weather())

        while self.running:
            for _ in range(int(WEATHER_REFRESH_MS / 1000)):
                if not self.running:
                    return
                time.sleep(1)

            self.weather_data.emit(self.fetch_weather())

    def stop(self):
        self.running = False




class FaceCameraWorker(QObject):
    frame_ready = pyqtSignal(object)
    status_ready = pyqtSignal(dict)
    crop_ready = pyqtSignal(object)
    db_ready = pyqtSignal(list)

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
        super().__init__()
        self.running = True
        self.camera = None
        self.cv2 = None
        self.np = None
        self.camera_ok = False
        self.start_error = ""

        self.model = None
        self.deepface_ok = False
        self.yolo_ok = False
        self.model_msg = "Vision stack not loaded"

        self.face_db = {}
        self.last_recog_t = 0.0
        self.last_result = {
            "identity_text": "---",
            "status_text": "Waiting for face",
            "distance_text": "---",
            "label_text": "NO FACE",
            "color": (0, 0, 255),
        }

        self.current_face_crop_bgr = None
        self.current_bbox = None

        # Low-latency design:
        # - camera preview loop runs continuously and only draws the latest detection result
        # - a background detection thread processes the newest frame snapshot occasionally
        self.frame_index = 0
        self.detect_every_n = 6
        self.latest_frame_xrgb = None
        self.latest_frame_lock = threading.Lock()
        self.overlay_lock = threading.Lock()
        self.last_bbox = None
        self.last_face_crop_bgr = None
        self.last_face_crop_rgb = None
        self.last_overlay_result = {
            "identity_text": "---",
            "status_text": "Waiting for face",
            "distance_text": "---",
            "label_text": "NO FACE",
            "color": (0, 0, 255),
        }
        self.detection_thread = None

        self.action_mode = None      # None | register | update
        self.action_name = ""
        self.action_embeddings = []
        self.action_captured = 0
        self.action_reset_done = False
        self.action_existing_count = 0
        self.last_capture_t = 0.0

        self.logs_dir = os.path.join(BASE_DIR, "logs")
        self.log_csv = os.path.join(self.logs_dir, "recognition_log.csv")

    def emit_status(self, camera_text, detail_text, face_identity, face_status, face_distance):
        self.status_ready.emit({
            "camera_text": camera_text,
            "detail_text": detail_text,
            "identity_text": face_identity,
            "status_text": face_status,
            "distance_text": face_distance,
        })

    def emit_blank_crop(self):
        blank = QImage(200, 200, QImage.Format.Format_RGB888)
        blank.fill(Qt.GlobalColor.black)
        self.crop_ready.emit(blank)

    def list_identities(self):
        if not os.path.isdir(FACE_DB_PATH):
            return []
        names = [p for p in os.listdir(FACE_DB_PATH) if os.path.isdir(os.path.join(FACE_DB_PATH, p))]
        names.sort(key=lambda s: s.lower())
        return names

    def load_database(self):
        db = {}
        if not os.path.isdir(FACE_DB_PATH):
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
        self.model_msg = f"YOLO + ArcFace ready | DB entries: {len(self.face_db)}"
        self.db_ready.emit(self.list_identities())

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

    def expand_box(self, x1, y1, x2, y2, w, h, pad):
        bw, bh = x2 - x1, y2 - y1
        px, py = int(bw * pad), int(bh * pad)
        return (
            max(0, x1 - px),
            max(0, y1 - py),
            min(w, x2 + px),
            min(h, y2 + py),
        )

    def get_embedding(self, face_bgr):
        emb = self.DeepFace.represent(
            img_path=face_bgr,
            model_name="ArcFace",
            enforce_detection=False,
            detector_backend="skip",
            align=True,
        )
        return self.np.asarray(emb[0]["embedding"], dtype=self.np.float32)

    def bgr_to_rgb_color(self, color_bgr):
        return (int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0]))

    def neutral_white_balance(self, frame_rgb):
        """
        Mild gray-world white balance correction.
        This is used only to neutralize the severe red/blue cast seen on the Pi camera.
        """
        img = frame_rgb.astype(self.np.float32)
        means = img.reshape(-1, 3).mean(axis=0)
        mean_gray = float(means.mean())
        gains = mean_gray / (means + 1e-6)

        # Keep the correction mild so colors do not become unnatural.
        gains = self.np.clip(gains, 0.70, 1.45)
        img *= gains.reshape(1, 1, 3)

        return self.np.clip(img, 0, 255).astype(self.np.uint8)

    def _status_for_action(self, msg, state="processing"):
        identity = self.action_name if self.action_name else "---"
        self.last_result = {
            "identity_text": identity,
            "status_text": msg,
            "distance_text": "---",
            "label_text": msg.upper()[:24],
            "color": (0, 255, 255) if state == "processing" else ((0, 255, 0) if state == "ready" else (0, 0, 255)),
        }

    def request_refresh(self):
        self.refresh_database()
        self._status_for_action("Database refreshed", "ready")

    def _reset_action_state(self):
        self.action_mode = None
        self.action_name = ""
        self.action_embeddings = []
        self.action_captured = 0
        self.action_reset_done = False
        self.action_existing_count = 0

    def _count_existing_images(self, person_dir):
        img_dir = os.path.join(person_dir, "images")
        if not os.path.isdir(img_dir):
            return 0
        count = 0
        for fname in os.listdir(img_dir):
            if fname.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
                count += 1
        return count

    def _prepare_action_target(self):
        person_dir = os.path.join(FACE_DB_PATH, self.action_name)
        img_dir = os.path.join(person_dir, "images")
        os.makedirs(person_dir, exist_ok=True)
        os.makedirs(img_dir, exist_ok=True)
        return person_dir, img_dir

    def request_register(self, name):
        name = (name or "").strip()
        if not name:
            self._status_for_action("Enter a user name", "fail")
            return
        if self.action_mode is not None:
            self._status_for_action("Action already in progress", "fail")
            return
        person_dir = os.path.join(FACE_DB_PATH, name)
        if os.path.isdir(person_dir):
            self._status_for_action("Name exists - use Update", "fail")
            return

        os.makedirs(os.path.join(person_dir, "images"), exist_ok=True)
        self.action_mode = "register"
        self.action_name = name
        self.action_embeddings = []
        self.action_captured = 0
        self.action_reset_done = False
        self.action_existing_count = 0
        self.last_capture_t = 0.0
        self._status_for_action(f"Registering {name} (0/{self.IMAGES_PER_PERSON})", "processing")

    def request_update(self, name):
        name = (name or "").strip()
        if not name:
            self._status_for_action("Enter or pick a user", "fail")
            return
        if self.action_mode is not None:
            self._status_for_action("Action already in progress", "fail")
            return
        person_dir = os.path.join(FACE_DB_PATH, name)
        if not os.path.isdir(person_dir):
            self._status_for_action("Name not found in DB", "fail")
            return

        os.makedirs(os.path.join(person_dir, "images"), exist_ok=True)
        existing_count = self._count_existing_images(person_dir)
        emb_exists = os.path.isfile(os.path.join(person_dir, "embedding.npy"))
        if existing_count == 0 and emb_exists:
            existing_count = 1

        self.action_mode = "update"
        self.action_name = name
        self.action_embeddings = []
        self.action_captured = 0
        self.action_reset_done = False
        self.action_existing_count = existing_count
        self.last_capture_t = 0.0
        self._status_for_action(
            f"Updating {name} (+{self.IMAGES_PER_PERSON}, base={existing_count})",
            "processing"
        )

    def _delete_rows_from_csv_for_name(self, name_to_delete):
        if not os.path.exists(self.log_csv):
            return
        tmp_path = self.log_csv + ".tmp"
        with open(self.log_csv, "r", newline="", encoding="utf-8") as fin, \
             open(tmp_path, "w", newline="", encoding="utf-8") as fout:
            reader = csv.reader(fin)
            writer = csv.writer(fout)
            header = next(reader, None)
            if header is None:
                return
            writer.writerow(header)
            try:
                name_idx = header.index("name")
            except ValueError:
                shutil.move(tmp_path, self.log_csv)
                return
            for row in reader:
                if len(row) <= name_idx or row[name_idx] != name_to_delete:
                    writer.writerow(row)
        shutil.move(tmp_path, self.log_csv)

    def request_delete(self, name):
        name = (name or "").strip()
        if not name:
            self._status_for_action("Pick a user to delete", "fail")
            return
        if self.action_mode is not None:
            self._status_for_action("Wait for current action", "fail")
            return
        person_dir = os.path.join(FACE_DB_PATH, name)
        if not os.path.isdir(person_dir):
            self._status_for_action("Name not found in DB", "fail")
            return

        shutil.rmtree(person_dir, ignore_errors=True)
        try:
            self._delete_rows_from_csv_for_name(name)
        except Exception:
            pass
        self.refresh_database()
        self._status_for_action(f"Deleted {name}", "ready")

    def _save_action_sample(self, face_crop_bgr):
        person_dir, img_dir = self._prepare_action_target()

        filename = f"{self.action_captured:02d}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        self.cv2.imwrite(os.path.join(img_dir, filename), face_crop_bgr)

        emb = self.get_embedding(face_crop_bgr)
        self.action_embeddings.append(emb)
        self.action_captured += 1

        verb = "Registering" if self.action_mode == "register" else "Updating"
        self._status_for_action(f"{verb} {self.action_name} ({self.action_captured}/{self.IMAGES_PER_PERSON})", "processing")

        if self.action_captured >= self.IMAGES_PER_PERSON:
            avg_emb = self.np.mean(self.np.vstack(self.action_embeddings), axis=0).astype(self.np.float32)

            # Overwrite embedding atomically so the refreshed user embedding is immediately usable.
            tmp_path = os.path.join(person_dir, "embedding.npy.tmp")
            final_path = os.path.join(person_dir, "embedding.npy")
            with open(tmp_path, "wb") as f:
                self.np.save(f, avg_emb)
            os.replace(tmp_path, final_path)

            self.refresh_database()
            finished_name = self.action_name
            finished_verb = "Registered" if self.action_mode == "register" else "Updated"
            self._reset_action_state()
            self._status_for_action(f"{finished_verb} {finished_name}", "ready")

    def init_runtime(self):
        try:
            import cv2
            import numpy as np
            from picamera2 import Picamera2
            from libcamera import controls
            from ultralytics import YOLO
            from deepface import DeepFace

            self.cv2 = cv2
            self.np = np
            self.YOLO = YOLO
            self.DeepFace = DeepFace

            model_path = os.path.join(BASE_DIR, "best.pt")
            if os.path.isfile(model_path):
                self.model = YOLO(model_path)
                self.yolo_ok = True
            else:
                self.yolo_ok = False
                self.model_msg = "best.pt not found"

            self.camera = Picamera2()
            preview_config = self.camera.create_preview_configuration(
                main={"size": CAMERA_SIZE, "format": "XRGB8888"}
            )
            self.camera.configure(preview_config)
            self.camera.start()

            time.sleep(1.0)

            try:
                self.camera.set_controls({
                    "AeEnable": True,
                    "AwbEnable": True,
                    "AwbMode": controls.AwbModeEnum.Auto,
                    "Brightness": 0.0,
                    "Contrast": 1.0,
                    "Saturation": 1.0,
                    "Sharpness": 1.0,
                })
            except Exception:
                try:
                    self.camera.set_controls({
                        "AeEnable": True,
                        "AwbEnable": True,
                    })
                except Exception:
                    pass

            for _ in range(5):
                req = None
                try:
                    req = self.camera.capture_request()
                    _ = req.make_array("main")
                finally:
                    if req is not None:
                        req.release()
                time.sleep(0.03)

            self.camera_ok = True
            self.deepface_ok = True

            self.refresh_database()
            self.model_msg += " | XRGB preview + background detection"

            self.emit_status(
                camera_text="Pi Camera ready",
                detail_text=self.model_msg,
                face_identity="---",
                face_status="Waiting for face",
                face_distance="---",
            )

            self.detection_thread = threading.Thread(target=self._detection_loop, daemon=True)
            self.detection_thread.start()

        except Exception as e:
            self.start_error = str(e)
            self.camera_ok = False
            self.emit_status(
                camera_text="Pi Camera offline",
                detail_text=f"Init failed: {self.start_error}",
                face_identity="Unavailable",
                face_status="Camera initialization failed",
                face_distance="---",
            )


    def _process_detection_frame(self, frame_xrgb):
        frame_bgr = self.cv2.cvtColor(frame_xrgb, self.cv2.COLOR_BGRA2BGR)
        h, w = frame_bgr.shape[:2]
        bbox = None
        face_crop_bgr = None
        face_crop_rgb = None
        result = dict(self.last_overlay_result)

        if self.yolo_ok and self.model is not None:
            results = self.model(frame_bgr, imgsz=256, conf=self.YOLO_CONF, iou=self.YOLO_IOU, verbose=False)[0]
            if results.boxes is not None and len(results.boxes) > 0:
                confs = results.boxes.conf.cpu().numpy()
                best_i = int(self.np.argmax(confs))
                box = results.boxes.xyxy.cpu().numpy()[best_i]
                x1, y1, x2, y2 = map(int, box)
                x1, y1, x2, y2 = self.expand_box(x1, y1, x2, y2, w, h, self.BOX_PADDING)
                bbox = (x1, y1, x2, y2)

                crop_xrgb = frame_xrgb[y1:y2, x1:x2]
                if crop_xrgb.shape[0] >= self.MIN_FACE_SIZE and crop_xrgb.shape[1] >= self.MIN_FACE_SIZE:
                    face_crop_bgr = self.cv2.cvtColor(crop_xrgb, self.cv2.COLOR_BGRA2BGR)
                    face_crop_rgb = self.cv2.cvtColor(face_crop_bgr, self.cv2.COLOR_BGR2RGB)

        if bbox is None or face_crop_bgr is None:
            if self.action_mode is None:
                result = {
                    "identity_text": "---",
                    "status_text": "No face detected",
                    "distance_text": "---",
                    "label_text": "NO FACE",
                    "color": (0, 0, 255),
                }
            else:
                verb = "Registering" if self.action_mode == "register" else "Updating"
                self._status_for_action(f"{verb} {self.action_name} - no face", "processing")
                result = dict(self.last_result)
            self.emit_blank_crop()
        else:
            crop_qimg = QImage(
                face_crop_rgb.data,
                face_crop_rgb.shape[1],
                face_crop_rgb.shape[0],
                face_crop_rgb.shape[1] * face_crop_rgb.shape[2],
                QImage.Format.Format_RGB888,
            ).copy()
            self.crop_ready.emit(crop_qimg)

            now = time.time()
            if self.action_mode is not None:
                if (now - self.last_capture_t) >= self.CAPTURE_INTERVAL:
                    self.last_capture_t = now
                    try:
                        self._save_action_sample(face_crop_bgr)
                    except Exception:
                        self._status_for_action("Enrollment failed", "fail")
                        self._reset_action_state()
                result = dict(self.last_result)
            elif (now - self.last_recog_t) >= self.RECOG_INTERVAL_SEC:
                self.last_recog_t = now
                try:
                    live_emb = self.get_embedding(face_crop_bgr)
                    if self.face_db:
                        best_name, best_dist = self.identify(live_emb)
                        if best_name and best_dist <= self.MATCH_TH:
                            result = {
                                "identity_text": best_name,
                                "status_text": "Recognized",
                                "distance_text": f"{best_dist:.4f}",
                                "label_text": f"{best_name} d={best_dist:.2f}",
                                "color": (0, 255, 0),
                            }
                        elif best_name and best_dist <= self.UNCERTAIN_TH:
                            result = {
                                "identity_text": f"Maybe {best_name}",
                                "status_text": "Uncertain match",
                                "distance_text": f"{best_dist:.4f}",
                                "label_text": f"Maybe {best_name} d={best_dist:.2f}",
                                "color": (0, 255, 255),
                            }
                        else:
                            result = {
                                "identity_text": "Unknown",
                                "status_text": "Unknown person",
                                "distance_text": f"{best_dist:.4f}" if best_name else "---",
                                "label_text": "UNKNOWN",
                                "color": (0, 0, 255),
                            }
                    else:
                        result = {
                            "identity_text": "DB empty",
                            "status_text": "Face detected",
                            "distance_text": "---",
                            "label_text": "DB EMPTY",
                            "color": (0, 255, 255),
                        }
                except Exception:
                    result = {
                        "identity_text": "Error",
                        "status_text": "Embedding error",
                        "distance_text": "---",
                        "label_text": "EMB ERROR",
                        "color": (0, 0, 255),
                    }

        with self.overlay_lock:
            self.last_bbox = bbox
            self.last_face_crop_bgr = face_crop_bgr
            self.last_face_crop_rgb = face_crop_rgb
            self.current_bbox = bbox
            self.current_face_crop_bgr = face_crop_bgr
            self.last_overlay_result = result
            self.last_result = result

    def _detection_loop(self):
        while self.running:
            frame = None
            with self.latest_frame_lock:
                if self.latest_frame_xrgb is not None:
                    frame = self.latest_frame_xrgb.copy()
                    self.latest_frame_xrgb = None

            if frame is None:
                time.sleep(0.01)
                continue

            try:
                self._process_detection_frame(frame)
            except Exception:
                pass

    def run(self):
        self.init_runtime()
        if not self.camera_ok:
            self.emit_blank_crop()
            return

        while self.running:
            req = None
            try:
                req = self.camera.capture_request()
                frame_xrgb = req.make_array("main").copy()
                req.release()
                req = None

                display_xrgb = frame_xrgb.copy()

                # Feed the newest frame snapshot to the background detection loop occasionally.
                self.frame_index += 1
                if (self.frame_index % self.detect_every_n) == 0:
                    with self.latest_frame_lock:
                        self.latest_frame_xrgb = frame_xrgb.copy()

                with self.overlay_lock:
                    bbox = self.last_bbox
                    result = dict(self.last_overlay_result)

                if bbox is not None:
                    x1, y1, x2, y2 = bbox
                    bgr = result["color"]
                    xrgb_color = (int(bgr[0]), int(bgr[1]), int(bgr[2]), 255)
                    label_text = result["label_text"]
                    self.cv2.rectangle(display_xrgb, (x1, y1), (x2, y2), xrgb_color, 2)
                    self.cv2.rectangle(display_xrgb, (x1, max(0, y1 - 32)), (x2, y1), xrgb_color, -1)
                    self.cv2.putText(
                        display_xrgb,
                        label_text,
                        (x1 + 6, max(18, y1 - 10)),
                        self.cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (0, 0, 0, 255),
                        2,
                        self.cv2.LINE_AA,
                    )

                self.cv2.putText(
                    display_xrgb,
                    "Raspberry Pi Camera",
                    (12, 24),
                    self.cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255, 255),
                    2,
                    self.cv2.LINE_AA,
                )
                self.cv2.putText(
                    display_xrgb,
                    datetime.now().strftime("%H:%M:%S"),
                    (display_xrgb.shape[1] - 110, 24),
                    self.cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255, 255),
                    2,
                    self.cv2.LINE_AA,
                )

                qimg = QImage(
                    display_xrgb.data,
                    display_xrgb.shape[1],
                    display_xrgb.shape[0],
                    display_xrgb.strides[0],
                    QImage.Format.Format_RGB32,
                ).copy()
                self.frame_ready.emit(qimg)

                self.emit_status(
                    camera_text="Pi Camera ready",
                    detail_text=self.model_msg,
                    face_identity=result["identity_text"],
                    face_status=result["status_text"],
                    face_distance=result["distance_text"],
                )

                time.sleep(CAMERA_REFRESH_MS / 1000.0)

            except Exception as e:
                if req is not None:
                    try:
                        req.release()
                    except Exception:
                        pass
                self.emit_status(
                    camera_text="Pi Camera runtime error",
                    detail_text=str(e),
                    face_identity="Unavailable",
                    face_status="Camera runtime error",
                    face_distance="---",
                )
                time.sleep(0.5)

    def stop(self):
        self.running = False
        try:
            if self.detection_thread is not None and self.detection_thread.is_alive():
                self.detection_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self.camera is not None:
                self.camera.stop()
        except Exception:
            pass

class LabResultsDialog(QDialog):
    def __init__(self, person_name, initial_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Lab Results - {person_name}")
        self.resize(520, 420)
        self.initial_data = initial_data or {}
        self.inputs = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel(f"Enter the available lab values for {person_name}")
        title.setWordWrap(True)
        root.addWidget(title)

        note = QLabel("Leave any field blank if it is not included in the lab report.")
        note.setObjectName("subtleLabel")
        note.setWordWrap(True)
        root.addWidget(note)

        form = QFormLayout()
        form.setSpacing(10)
        root.addLayout(form)

        for key, label in LAB_FIELDS:
            if key == "gender":
                widget = QComboBox()
                widget.addItems(["", "Male", "Female", "Other"])
                value = self.initial_data.get(key)
                if value in ("Male", "Female", "Other"):
                    widget.setCurrentText(value)
            else:
                widget = QLineEdit()
                widget.setPlaceholderText("Optional")
                value = self.initial_data.get(key)
                if value is not None:
                    widget.setText(str(value))
            self.inputs[key] = widget
            form.addRow(label + ":", widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def get_values(self):
        values = {}
        numeric_fields = {
            "age",
            "alkaline_phosphatase",
            "aspartate_aminotransferase",
            "alanine_aminotransferase",
            "total_bilirubin",
            "direct_bilirubin",
            "albumin",
            "total_proteins",
            "albumin_globulin_ratio",
        }

        for key, _label in LAB_FIELDS:
            widget = self.inputs[key]
            if key == "gender":
                text = widget.currentText().strip()
                values[key] = text if text else None
                continue

            text = widget.text().strip()
            if not text:
                values[key] = None
                continue

            if key in numeric_fields:
                try:
                    if key == "age":
                        values[key] = int(float(text))
                    else:
                        values[key] = float(text)
                except ValueError:
                    raise ValueError(f"Invalid value for {_label}: {text}")
            else:
                values[key] = text

        return values


class InfoCard(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        self.title = QLabel(title)
        self.title.setObjectName("cardTitle")
        layout.addWidget(self.title)

        self.body_layout = QVBoxLayout()
        self.body_layout.setSpacing(8)
        layout.addLayout(self.body_layout)

    def add_widget(self, widget):
        self.body_layout.addWidget(widget)


class UserDropdown(QComboBox):
    """Reliable user dropdown for Raspberry Pi/VNC.
    It opens on click and prevents accidental mouse-wheel selection changes.
    """

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.showPopup()

    def wheelEvent(self, event):
        event.ignore()


class SmartMirrorWindow(QMainWindow):
    register_requested = pyqtSignal(str)
    update_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Smart Mirror Dashboard - Step 5 (Lab Screening)")
        self.resize(1500, 980)
        self.current_camera_pixmap = None
        self.current_face_crop = None

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setCentralWidget(self.scroll)

        self.content = QWidget()
        self.content.setMinimumWidth(1450)
        self.scroll.setWidget(self.content)

        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(18)

        self.main_title = QLabel("AI Smart Mirror Dashboard")
        self.main_title.setObjectName("mainTitle")
        self.main_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.main_title)

        # ===== Top row =====
        top_row = QHBoxLayout()
        top_row.setSpacing(18)
        outer.addLayout(top_row, stretch=2)

        self.calendar_card = InfoCard("Calendar")
        self.calendar = QCalendarWidget()
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setGridVisible(True)
        self.calendar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.calendar_card.add_widget(self.calendar)
        top_row.addWidget(self.calendar_card, stretch=1)

        self.datetime_card = InfoCard("Current Date & Time")
        self.time_label = QLabel("--:--:--")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.date_label = QLabel("----")
        self.date_label.setObjectName("dateLabel")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.zone_label = QLabel("Local time")
        self.zone_label.setObjectName("subtleLabel")
        self.zone_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.datetime_card.add_widget(self.time_label)
        self.datetime_card.add_widget(self.date_label)
        self.datetime_card.add_widget(self.zone_label)
        top_row.addWidget(self.datetime_card, stretch=1)

        self.weather_card = InfoCard("Current Weather")
        self.weather_city = QLabel("Loading...")
        self.weather_city.setObjectName("weatherCity")
        self.weather_city.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.weather_temp = QLabel("---")
        self.weather_temp.setObjectName("weatherTemp")
        self.weather_temp.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.weather_condition = QLabel("---")
        self.weather_condition.setObjectName("weatherCondition")
        self.weather_condition.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.weather_feels = QLabel("Feels like: ---")
        self.weather_feels.setObjectName("subtleLabel")
        self.weather_feels.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.weather_card.add_widget(self.weather_city)
        self.weather_card.add_widget(self.weather_temp)
        self.weather_card.add_widget(self.weather_condition)
        self.weather_card.add_widget(self.weather_feels)
        top_row.addWidget(self.weather_card, stretch=1)

        # ===== Sensor row =====
        sensor_grid = QGridLayout()
        sensor_grid.setSpacing(18)
        outer.addLayout(sensor_grid, stretch=1)

        self.temp_card = InfoCard("MLX90614 Temperature")
        self.temp_value = QLabel("---")
        self.temp_value.setObjectName("valueBig")
        self.temp_value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.temp_thresholds = QLabel("Thresholds: Low < 36.1 °C | Normal 36.1–37.2 °C | High > 37.2 °C | Use close forehead distance")
        self.temp_thresholds.setObjectName("thresholdLabel")
        self.temp_thresholds.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.temp_thresholds.setWordWrap(True)

        self.temp_range = QLabel("Current Range: ---")
        self.temp_range.setObjectName("rangeLabel")
        self.temp_range.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.temp_status = QLabel("Waiting for data")
        self.temp_status.setObjectName("statusLabel")
        self.temp_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.temp_card.add_widget(self.temp_value)
        self.temp_card.add_widget(self.temp_thresholds)
        self.temp_card.add_widget(self.temp_range)
        self.temp_card.add_widget(self.temp_status)
        sensor_grid.addWidget(self.temp_card, 0, 0)

        self.heart_card = InfoCard("MAX30102 Heart Rate")
        self.heart_bpm = QLabel("---")
        self.heart_bpm.setObjectName("valueBig")
        self.heart_bpm.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.heart_thresholds = QLabel("Thresholds: Low < 60 BPM | Normal 60–100 BPM | High > 100 BPM")
        self.heart_thresholds.setObjectName("thresholdLabel")
        self.heart_thresholds.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heart_thresholds.setWordWrap(True)

        self.heart_range = QLabel("Current Range: ---")
        self.heart_range.setObjectName("rangeLabel")
        self.heart_range.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.heart_spo2 = QLabel("SpO2: ---")
        self.heart_spo2.setObjectName("valueMedium")
        self.heart_spo2.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.spo2_thresholds = QLabel("Thresholds: Critical < 90% | Low 90–94% | Normal 95–100%")
        self.spo2_thresholds.setObjectName("thresholdLabel")
        self.spo2_thresholds.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spo2_thresholds.setWordWrap(True)

        self.spo2_range = QLabel("SpO2 Range: ---")
        self.spo2_range.setObjectName("spo2RangeLabel")
        self.spo2_range.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.heart_status = QLabel("Waiting for data")
        self.heart_status.setObjectName("statusLabel")
        self.heart_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.heart_card.add_widget(self.heart_bpm)
        self.heart_card.add_widget(self.heart_thresholds)
        self.heart_card.add_widget(self.heart_range)
        self.heart_card.add_widget(self.heart_spo2)
        self.heart_card.add_widget(self.spo2_thresholds)
        self.heart_card.add_widget(self.spo2_range)
        self.heart_card.add_widget(self.heart_status)
        sensor_grid.addWidget(self.heart_card, 0, 1)

        self.color_card = InfoCard("TCS3200 Dominant Color")
        self.color_dominant = QLabel("---")
        self.color_dominant.setObjectName("valueBig")
        self.color_dominant.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.color_rgb = QLabel("R: ---   G: ---   B: ---")
        self.color_rgb.setObjectName("valueMedium")
        self.color_rgb.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.color_status = QLabel("Waiting for data")
        self.color_status.setObjectName("statusLabel")
        self.color_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.color_card.add_widget(self.color_dominant)
        self.color_card.add_widget(self.color_rgb)
        self.color_card.add_widget(self.color_status)
        sensor_grid.addWidget(self.color_card, 0, 2)

        self.fusion_card = InfoCard("Sensor Fusion Summary")
        self.fusion_title = QLabel("Collecting Data")
        self.fusion_title.setObjectName("fusionState")
        self.fusion_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.fusion_score = QLabel("Fusion Score: 0")
        self.fusion_score.setObjectName("valueMedium")
        self.fusion_score.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.fusion_detail = QLabel("Waiting for sensor data")
        self.fusion_detail.setObjectName("statusLabel")
        self.fusion_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fusion_detail.setWordWrap(True)

        self.fusion_note = QLabel("Screening summary only - not a medical diagnosis")
        self.fusion_note.setObjectName("subtleLabel")
        self.fusion_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fusion_note.setWordWrap(True)

        self.fusion_card.add_widget(self.fusion_title)
        self.fusion_card.add_widget(self.fusion_score)
        self.fusion_card.add_widget(self.fusion_detail)
        self.fusion_card.add_widget(self.fusion_note)
        sensor_grid.addWidget(self.fusion_card, 1, 0, 1, 3)

        self.liver_card = InfoCard("Liver Lab Screening")
        self.liver_title = QLabel("No Lab Data")
        self.liver_title.setObjectName("fusionState")
        self.liver_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.liver_coverage = QLabel("Coverage: 0/9 lab fields entered")
        self.liver_coverage.setObjectName("valueMedium")
        self.liver_coverage.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.liver_detail = QLabel("Select a saved user, enter the available lab values, then run the screening.")
        self.liver_detail.setObjectName("statusLabel")
        self.liver_detail.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.liver_detail.setWordWrap(True)
        self.liver_detail.setMinimumHeight(150)

        self.liver_advice = QLabel("Screening support only - not a diagnosis")
        self.liver_advice.setObjectName("subtleLabel")
        self.liver_advice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.liver_advice.setWordWrap(True)
        self.liver_advice.setMinimumHeight(110)

        self.liver_card.add_widget(self.liver_title)
        self.liver_card.add_widget(self.liver_coverage)
        self.liver_card.add_widget(self.liver_detail)
        self.liver_card.add_widget(self.liver_advice)
        sensor_grid.addWidget(self.liver_card, 2, 0, 1, 3)

        # ===== Camera row =====
        camera_row = QHBoxLayout()
        camera_row.setSpacing(18)
        outer.addLayout(camera_row, stretch=3)

        self.camera_card = InfoCard("Raspberry Pi Camera + Face ID")
        self.camera_view = QLabel("Starting camera...")
        self.camera_view.setObjectName("cameraView")
        self.camera_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_view.setFixedSize(960, 540)
        self.camera_view.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.camera_status = QLabel("Initializing...")
        self.camera_status.setObjectName("statusLabel")
        self.camera_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.camera_detail = QLabel("Waiting...")
        self.camera_detail.setObjectName("statusLabel")
        self.camera_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_detail.setWordWrap(True)

        self.camera_card.add_widget(self.camera_view)
        self.camera_card.body_layout.setAlignment(self.camera_view, Qt.AlignmentFlag.AlignCenter)
        self.camera_card.add_widget(self.camera_status)
        self.camera_card.add_widget(self.camera_detail)
        camera_row.addWidget(self.camera_card, stretch=2)

        
        self.face_card = InfoCard("Face Recognition Status")
        self.face_crop = QLabel("No face")
        self.face_crop.setObjectName("faceCrop")
        self.face_crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face_crop.setFixedSize(320, 240)
        self.face_crop.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.face_identity = QLabel("Pending")
        self.face_identity.setObjectName("valueBig")
        self.face_identity.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face_identity.setWordWrap(True)

        self.face_status = QLabel("Waiting...")
        self.face_status.setObjectName("statusLabel")
        self.face_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face_status.setWordWrap(True)

        self.face_distance = QLabel("Confidence: ---")
        self.face_distance.setObjectName("subtleLabel")
        self.face_distance.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.face_note = QLabel("Register captures a new user. Update adds a fresh 20-image set to the selected user and merges it into the existing embedding. Delete removes a saved user.")
        self.face_note.setObjectName("subtleLabel")
        self.face_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.face_note.setWordWrap(True)

        self.user_name_input = QLineEdit()
        self.user_name_input.setPlaceholderText("Type user name here")
        self.user_name_input.setObjectName("inputField")

        self.user_combo = UserDropdown()
        self.user_combo.setObjectName("comboField")
        self.user_combo.setEditable(False)
        self.user_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.user_combo.setMaxVisibleItems(12)
        self.user_combo.setMinimumHeight(42)
        self.user_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)

        buttons_row_1 = QHBoxLayout()
        self.register_btn = QPushButton("Register New")
        self.update_btn = QPushButton("Update Existing")
        buttons_row_1.addWidget(self.register_btn)
        buttons_row_1.addWidget(self.update_btn)

        buttons_row_2 = QHBoxLayout()
        self.delete_btn = QPushButton("Delete User")
        self.refresh_btn = QPushButton("Refresh DB")
        buttons_row_2.addWidget(self.delete_btn)
        buttons_row_2.addWidget(self.refresh_btn)

        buttons_row_3 = QHBoxLayout()
        self.lab_btn = QPushButton("Enter Lab Results")
        self.analyze_lab_btn = QPushButton("Analyze Lab Results")
        buttons_row_3.addWidget(self.lab_btn)
        buttons_row_3.addWidget(self.analyze_lab_btn)

        buttons_row_4 = QHBoxLayout()
        self.upload_pdf_btn = QPushButton("Upload Lab PDF + AI")
        buttons_row_4.addWidget(self.upload_pdf_btn)

        self.face_card.add_widget(self.face_crop)
        self.face_card.body_layout.setAlignment(self.face_crop, Qt.AlignmentFlag.AlignCenter)
        self.face_card.add_widget(self.face_identity)
        self.face_card.add_widget(self.face_status)
        self.face_card.add_widget(self.face_distance)
        self.face_card.add_widget(self.face_note)
        self.face_card.add_widget(self.user_name_input)
        self.face_card.add_widget(self.user_combo)

        buttons_row_1_wrap = QWidget()
        buttons_row_1_wrap.setLayout(buttons_row_1)
        buttons_row_2_wrap = QWidget()
        buttons_row_2_wrap.setLayout(buttons_row_2)
        buttons_row_3_wrap = QWidget()
        buttons_row_3_wrap.setLayout(buttons_row_3)
        buttons_row_4_wrap = QWidget()
        buttons_row_4_wrap.setLayout(buttons_row_4)
        self.face_card.add_widget(buttons_row_1_wrap)
        self.face_card.add_widget(buttons_row_2_wrap)
        self.face_card.add_widget(buttons_row_3_wrap)
        self.face_card.add_widget(buttons_row_4_wrap)

        camera_row.addWidget(self.face_card, stretch=1)


        outer.addStretch(1)

        self.apply_dark_theme()
        self.start_clock()
        self.start_sensor_thread()
        self.start_weather_thread()
        self.start_camera_thread()

        self.set_status_state(self.temp_status, STATUS_PROCESSING)
        self.set_status_state(self.heart_status, STATUS_PROCESSING)
        self.set_status_state(self.color_status, STATUS_PROCESSING)
        self.set_status_state(self.fusion_title, STATUS_PROCESSING)
        self.set_status_state(self.fusion_detail, STATUS_PROCESSING)
        self.set_status_state(self.liver_title, STATUS_PROCESSING)
        self.set_status_state(self.liver_detail, STATUS_PROCESSING)
        self.set_status_state(self.camera_status, STATUS_PROCESSING)
        self.set_status_state(self.camera_detail, STATUS_PROCESSING)
        self.set_status_state(self.face_status, STATUS_PROCESSING)

        self.register_btn.clicked.connect(self.on_register_clicked)
        self.update_btn.clicked.connect(self.on_update_clicked)
        self.delete_btn.clicked.connect(self.on_delete_clicked)
        self.refresh_btn.clicked.connect(self.on_refresh_clicked)
        self.lab_btn.clicked.connect(self.on_lab_entry_clicked)
        self.analyze_lab_btn.clicked.connect(self.on_analyze_lab_clicked)
        self.upload_pdf_btn.clicked.connect(self.on_upload_pdf_clicked)

    def set_status_state(self, label, state):
        label.setProperty("statusState", state)
        self.style().unpolish(label)
        self.style().polish(label)
        label.update()

    def set_range_state(self, label, state):
        label.setProperty("rangeState", state)
        self.style().unpolish(label)
        self.style().polish(label)
        label.update()

    def heart_state_from_status(self, status_text):
        text = (status_text or "").lower()
        if "stable" in text or "ready" in text:
            return STATUS_READY
        if "finger detected" in text or "stabilizing" in text or "processing" in text or "waiting" in text or "initializing" in text:
            return STATUS_PROCESSING
        return STATUS_FAIL

    def generic_state_from_status(self, status_text):
        text = (status_text or "").lower()
        if "ready" in text or "stable" in text or "loaded" in text or "detected" in text or "recognized" in text:
            return STATUS_READY
        if "stabilizing" in text or "processing" in text or "waiting" in text or "initializing" in text or "uncertain" in text:
            return STATUS_PROCESSING
        return STATUS_FAIL

    def face_state_from_status(self, status_text):
        text = (status_text or "").lower()
        if "recognized" in text or "face detected" in text or "registered" in text or "updated" in text or "deleted" in text or "refreshed" in text:
            return STATUS_READY
        if "waiting" in text or "uncertain" in text or "registering" in text or "updating" in text or "capturing" in text:
            return STATUS_PROCESSING
        if "no face" in text:
            return STATUS_FAIL
        return self.generic_state_from_status(status_text)

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QWidget {
                background: #0d1117;
                color: #e6edf3;
                font-family: Arial, Helvetica, sans-serif;
                font-size: 15px;
            }

            QMainWindow {
                background: #0d1117;
            }

            QScrollArea {
                border: none;
                background: #0d1117;
            }

            QScrollBar:vertical {
                background: #111827;
                width: 14px;
                margin: 4px 2px 4px 2px;
                border-radius: 7px;
            }

            QScrollBar::handle:vertical {
                background: #374151;
                min-height: 30px;
                border-radius: 7px;
            }

            QScrollBar::handle:vertical:hover {
                background: #4b5563;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
                border: none;
            }

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }

            QFrame#card {
                background: #161b22;
                border: 1px solid #2d333b;
                border-radius: 18px;
            }

            QLabel#mainTitle {
                font-size: 30px;
                font-weight: 800;
                color: #ffffff;
                padding: 6px 0 10px 0;
            }

            QLabel#cardTitle {
                font-size: 18px;
                font-weight: 700;
                color: #9ecbff;
                padding-bottom: 4px;
            }

            QLabel#timeLabel {
                font-size: 48px;
                font-weight: 800;
                padding-top: 20px;
            }

            QLabel#dateLabel {
                font-size: 24px;
                font-weight: 600;
            }

            QLabel#weatherCity {
                font-size: 20px;
                font-weight: 700;
                padding-top: 18px;
            }

            QLabel#weatherTemp {
                font-size: 42px;
                font-weight: 800;
            }

            QLabel#weatherCondition {
                font-size: 22px;
                font-weight: 600;
            }

            QLabel#valueBig {
                font-size: 34px;
                font-weight: 800;
                padding-top: 16px;
            }

            QLabel#valueMedium {
                font-size: 20px;
                font-weight: 600;
            }

            QLabel#fusionState {
                font-size: 28px;
                font-weight: 800;
                color: #f8fafc;
                padding-top: 8px;
            }

            QLabel#fusionState[statusState="fail"] {
                color: #ef4444;
            }

            QLabel#fusionState[statusState="processing"] {
                color: #facc15;
            }

            QLabel#fusionState[statusState="ready"] {
                color: #22c55e;
            }

            QLabel#thresholdLabel {
                color: #93c5fd;
                font-size: 13px;
                font-weight: 600;
                padding-top: 2px;
            }

            QLabel#rangeLabel {
                color: #f8fafc;
                font-size: 15px;
                font-weight: 700;
            }

            QLabel#rangeLabel[rangeState="low"] {
                color: #ef4444;
            }

            QLabel#rangeLabel[rangeState="normal"] {
                color: #22c55e;
            }

            QLabel#rangeLabel[rangeState="high"] {
                color: #ef4444;
            }

            QLabel#rangeLabel[rangeState="unknown"] {
                color: #facc15;
            }

            QLabel#spo2RangeLabel {
                color: #f8fafc;
                font-size: 15px;
                font-weight: 700;
            }

            QLabel#spo2RangeLabel[rangeState="normal"] {
                color: #22c55e;
            }

            QLabel#spo2RangeLabel[rangeState="low"] {
                color: #facc15;
            }

            QLabel#spo2RangeLabel[rangeState="critical"] {
                color: #ef4444;
            }

            QLabel#spo2RangeLabel[rangeState="unknown"] {
                color: #facc15;
            }

            QLabel#statusLabel {
                font-size: 15px;
                font-weight: 700;
                color: #8b949e;
            }

            QLabel#statusLabel[statusState="fail"] {
                color: #ef4444;
            }

            QLabel#statusLabel[statusState="processing"] {
                color: #facc15;
            }

            QLabel#statusLabel[statusState="ready"] {
                color: #22c55e;
            }

            QLabel#subtleLabel {
                color: #8b949e;
                font-size: 15px;
            }

            QLabel#cameraView {
                background: #05080d;
                border: 1px solid #2d333b;
                border-radius: 14px;
                min-height: 420px;
                padding: 6px;
            }

            QLabel#faceCrop {
                background: #05080d;
                border: 1px solid #2d333b;
                border-radius: 14px;
                min-height: 200px;
                padding: 6px;
            }

            QLineEdit#inputField, QComboBox#comboField {
                background: #0f1720;
                border: 1px solid #2d333b;
                border-radius: 10px;
                padding: 8px 10px;
                color: #e6edf3;
                min-height: 18px;
            }

            QPushButton {
                background: #1f2937;
                border: 1px solid #374151;
                border-radius: 10px;
                color: #e6edf3;
                font-weight: 700;
                padding: 10px 12px;
            }

            QPushButton:hover {
                background: #273446;
            }

            QPushButton:pressed {
                background: #172131;
            }

            QCalendarWidget QWidget {
                alternate-background-color: #0d1117;
            }

            QCalendarWidget QToolButton {
                color: #e6edf3;
                background-color: #1f2937;
                border: none;
                border-radius: 10px;
                padding: 8px;
                margin: 2px;
            }

            QCalendarWidget QMenu {
                background-color: #161b22;
                color: #e6edf3;
            }

            QCalendarWidget QSpinBox {
                background-color: #161b22;
                color: #e6edf3;
                selection-background-color: #2563eb;
                selection-color: white;
            }

            QCalendarWidget QAbstractItemView:enabled {
                background-color: #161b22;
                color: #e6edf3;
                selection-background-color: #2563eb;
                selection-color: white;
                outline: 0;
            }
        """)

    def start_clock(self):
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.update_clock()

    def update_clock(self):
        now = datetime.now()
        self.time_label.setText(now.strftime("%I:%M:%S %p"))
        self.date_label.setText(now.strftime("%A, %d %B %Y"))
        self.zone_label.setText("Local system time")

    def start_sensor_thread(self):
        self.sensor_thread = QThread(self)
        self.sensor_worker = SensorWorker()
        self.sensor_worker.moveToThread(self.sensor_thread)
        self.sensor_thread.started.connect(self.sensor_worker.run)
        self.sensor_worker.sensor_data.connect(self.on_sensor_data)
        self.sensor_thread.start()

    def start_weather_thread(self):
        self.weather_thread = QThread(self)
        self.weather_worker = WeatherWorker(WEATHER_LOCATION)
        self.weather_worker.moveToThread(self.weather_thread)
        self.weather_thread.started.connect(self.weather_worker.run)
        self.weather_worker.weather_data.connect(self.on_weather_data)
        self.weather_thread.start()

    def start_camera_thread(self):
        self.camera_thread = QThread(self)
        self.camera_worker = FaceCameraWorker()
        self.camera_worker.moveToThread(self.camera_thread)
        self.camera_thread.started.connect(self.camera_worker.run)
        self.camera_worker.frame_ready.connect(self.on_camera_frame)
        self.camera_worker.status_ready.connect(self.on_camera_status)
        self.camera_worker.crop_ready.connect(self.on_face_crop)
        self.camera_worker.db_ready.connect(self.on_db_ready)

        self.register_requested.connect(self.camera_worker.request_register, type=Qt.ConnectionType.DirectConnection)
        self.update_requested.connect(self.camera_worker.request_update, type=Qt.ConnectionType.DirectConnection)
        self.delete_requested.connect(self.camera_worker.request_delete, type=Qt.ConnectionType.DirectConnection)
        self.refresh_requested.connect(self.camera_worker.request_refresh, type=Qt.ConnectionType.DirectConnection)

        self.camera_thread.start()

    def on_sensor_data(self, data):
        temp = data["temperature"]
        heart = data["heart"]
        color = data["color"]

        self.temp_value.setText(temp["text"])
        temp_range_text, temp_range_state = classify_temperature(temp["raw"])
        self.temp_range.setText(temp_range_text)
        self.set_range_state(self.temp_range, temp_range_state)
        self.temp_status.setText("Sensor ready" if temp["ok"] else f"Status: {temp['text']}")
        if temp["text"] == "Target too far":
            self.set_status_state(self.temp_status, STATUS_PROCESSING)
        else:
            self.set_status_state(self.temp_status, STATUS_READY if temp["ok"] else STATUS_FAIL)

        self.heart_bpm.setText(heart["bpm_text"])
        heart_range_text, heart_range_state = classify_heart_rate(heart["bpm_text"])
        self.heart_range.setText(heart_range_text)
        self.set_range_state(self.heart_range, heart_range_state)
        self.heart_spo2.setText(f"SpO2: {heart['spo2_text']}")
        spo2_range_text, spo2_range_state = classify_spo2(heart["spo2_text"])
        self.spo2_range.setText(spo2_range_text)
        self.set_range_state(self.spo2_range, spo2_range_state)
        self.heart_status.setText(heart["status"])
        self.set_status_state(self.heart_status, self.heart_state_from_status(heart["status"]))

        self.color_dominant.setText(color["dominant"])
        if color["ok"]:
            self.color_rgb.setText(
                f"R: {color['red']}   G: {color['green']}   B: {color['blue']} | Base: {color['base_color']}"
            )
            self.color_status.setText(
                f"Sensor ready | nR={color['norm_red']} nG={color['norm_green']} nB={color['norm_blue']}"
            )
            self.set_status_state(self.color_status, STATUS_READY)
        else:
            self.color_rgb.setText("R: ---   G: ---   B: ---")
            self.color_status.setText(f"Status: {color['dominant']}")
            self.set_status_state(self.color_status, STATUS_FAIL)

        fusion = compute_sensor_fusion(temp, heart, color)
        self.fusion_title.setText(fusion["title"])
        self.fusion_score.setText(fusion["score_text"])
        self.fusion_detail.setText(fusion["detail_text"])
        self.fusion_note.setText(fusion["note_text"])
        self.set_status_state(self.fusion_title, fusion["state"])
        self.set_status_state(self.fusion_detail, fusion["state"])

    def on_weather_data(self, data):
        self.weather_city.setText(data["city"])
        self.weather_temp.setText(data["temp"])
        self.weather_condition.setText(data["condition"])
        self.weather_feels.setText(f"Feels like: {data['feels']}")

    def on_camera_frame(self, qimg):
        pixmap = QPixmap.fromImage(qimg)
        self.current_camera_pixmap = pixmap
        self.refresh_camera_view()

    def on_face_crop(self, qimg):
        pixmap = QPixmap.fromImage(qimg)
        self.current_face_crop = pixmap
        self.refresh_face_crop()

    def refresh_camera_view(self):
        if self.current_camera_pixmap is None:
            return
        scaled = self.current_camera_pixmap.scaled(
            self.camera_view.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.camera_view.setPixmap(scaled)

    def refresh_face_crop(self):
        if self.current_face_crop is None:
            return
        scaled = self.current_face_crop.scaled(
            self.face_crop.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.face_crop.setPixmap(scaled)

    def on_camera_status(self, data):
        self.camera_status.setText(data["camera_text"])
        self.camera_detail.setText(data["detail_text"])
        self.face_identity.setText(data["identity_text"])
        self.face_status.setText(data["status_text"])
        self.face_distance.setText(f"Confidence: {data['distance_text']}")

        self.set_status_state(self.camera_status, self.generic_state_from_status(data["camera_text"]))
        self.set_status_state(self.camera_detail, self.generic_state_from_status(data["detail_text"]))
        self.set_status_state(self.face_status, self.face_state_from_status(data["status_text"]))


    def on_db_ready(self, names):
        current = self.user_combo.currentText()
        self.user_combo.blockSignals(True)
        self.user_combo.clear()
        self.user_combo.addItems(names)
        if current and current in names:
            self.user_combo.setCurrentText(current)
        self.user_combo.blockSignals(False)

    def on_user_combo_pressed(self, index):
        try:
            self.user_combo.setCurrentIndex(index.row())
            self.user_combo.hidePopup()
        except Exception:
            pass

    def picked_or_typed_name(self):
        typed = self.user_name_input.text().strip()
        if typed:
            return typed
        return self.user_combo.currentText().strip()

    def picked_existing_name(self):
        picked = self.user_combo.currentText().strip()
        if picked:
            return picked
        return self.user_name_input.text().strip()

    def on_register_clicked(self):
        name = self.user_name_input.text().strip()
        if not name:
            self.face_status.setText("Enter a new user name")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return
        self.face_status.setText(f"Starting registration for {name}...")
        self.set_status_state(self.face_status, STATUS_PROCESSING)
        self.register_requested.emit(name)

    def on_update_clicked(self):
        name = self.picked_existing_name()
        if not name:
            self.face_status.setText("Pick or type a user name")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return
        self.face_status.setText(f"Starting update for {name}...")
        self.set_status_state(self.face_status, STATUS_PROCESSING)
        self.update_requested.emit(name)

    def on_delete_clicked(self):
        name = self.picked_existing_name()
        if not name:
            self.face_status.setText("Pick or type a user name")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return
        reply = QMessageBox.question(
            self,
            "Delete user",
            f"Delete '{name}' from FaceDB?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(name)

    def on_refresh_clicked(self):
        self.refresh_requested.emit()

    def lab_results_path(self, person_name):
        return os.path.join(FACE_DB_PATH, person_name, "lab_results.json")

    def load_lab_results(self, person_name):
        path = self.lab_results_path(person_name)
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("values", data)
        except Exception:
            return {}

    def save_lab_results(self, person_name, values):
        person_dir = os.path.join(FACE_DB_PATH, person_name)
        os.makedirs(person_dir, exist_ok=True)
        path = self.lab_results_path(person_name)
        payload = {
            "person_name": person_name,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "values": values,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def update_liver_screening_ui(self, analysis):
        self.liver_title.setText(analysis["title"])
        self.liver_coverage.setText(analysis["coverage_text"])
        self.liver_detail.setText(analysis["detail_text"])
        self.liver_advice.setText(f"{analysis['advice_text']} | {analysis['note_text']}")
        self.set_status_state(self.liver_title, analysis["state"])
        self.set_status_state(self.liver_detail, analysis["state"])


    def load_liver_ai_assets(self):
        try:
            import joblib
        except Exception as e:
            raise RuntimeError(f"joblib is not installed: {e}")

        required = [
            LIVER_MODEL_PATH,
            LIVER_COLUMNS_PATH,
            LIVER_MEDIANS_PATH,
            LIVER_ENCODERS_PATH,
        ]
        missing = [path for path in required if not os.path.isfile(path)]
        if missing:
            raise FileNotFoundError(
                "Missing liver AI files:\n" +
                "\n".join(missing) +
                "\n\nPut the four files inside the models folder next to this dashboard file."
            )

        model = joblib.load(LIVER_MODEL_PATH)
        model_columns = joblib.load(LIVER_COLUMNS_PATH)
        training_medians = joblib.load(LIVER_MEDIANS_PATH)
        label_encoders = joblib.load(LIVER_ENCODERS_PATH)
        return model, model_columns, training_medians, label_encoders

    def build_ai_input_from_lab_values(self, values, model, model_columns, training_medians):
        # Use exact feature names saved in the trained model when available.
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

    def predict_liver_ai_from_values(self, values):
        model, model_columns, training_medians, _label_encoders = self.load_liver_ai_assets()
        input_df, missing = self.build_ai_input_from_lab_values(values, model, model_columns, training_medians)

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

        # Also run the rule-based lab-range screening so the dashboard shows
        # every value that is above or below the reference range, not just the AI result.
        rule_analysis = analyze_saved_lab_results(values)

        ai_detail = f"AI result: {result_text} Confidence: {confidence:.2f}% | Reliability: {reliability}"
        if missing:
            ai_detail += "\nMissing AI parameters: " + ", ".join(missing[:4])
            if len(missing) > 4:
                ai_detail += f" +{len(missing) - 4} more"

        detail = rule_analysis.get("detail_text", "")
        if detail:
            detail += "\n\n" + ai_detail
        else:
            detail = ai_detail

        combined_title = rule_analysis.get("title", title)
        if combined_title in ("No Lab Data", "No Flagged Abnormality"):
            combined_title = title

        combined_state = state
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

    def on_upload_pdf_clicked(self):
        name = self.picked_existing_name()
        if not name:
            self.face_status.setText("Pick a saved user first")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return

        person_dir = os.path.join(FACE_DB_PATH, name)
        if not os.path.isdir(person_dir):
            self.face_status.setText("Selected user was not found in FaceDB")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return

        pdf_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select liver lab report PDF",
            os.path.expanduser("~"),
            "PDF Files (*.pdf)"
        )
        if not pdf_path:
            return

        try:
            self.face_status.setText("Processing PDF lab report...")
            self.set_status_state(self.face_status, STATUS_PROCESSING)
            QApplication.processEvents()

            pdf_text, method = extract_text_from_pdf(pdf_path)
            extracted_values = extract_liver_values_from_text(pdf_text)

            # Merge PDF values with already-saved values.
            values = self.load_lab_results(name)
            for key, value in extracted_values.items():
                if value is not None:
                    values[key] = value

            # Keep existing age/gender if already saved; ask user to confirm/edit after extraction.
            self.save_lab_results(name, values)

            ai_analysis = self.predict_liver_ai_from_values(values)
            self.update_liver_screening_ui(ai_analysis)

            missing_pdf = [label for key, label in LAB_FIELDS if key != "gender" and safe_float(values.get(key)) is None]
            msg = f"PDF processed using {method}. Extracted values were saved for {name}."
            if missing_pdf:
                msg += "\n\nSome fields were not found in the PDF. Use 'Enter Lab Results' to review/correct them before final use."
            msg += "\n\nAI result:\n" + ai_analysis["detail_text"]
            msg += "\n\nMedical note: " + ai_analysis["advice_text"]

            QMessageBox.information(self, "PDF processed", msg)
            self.face_identity.setText(name)
            self.face_status.setText(f"PDF lab report processed for {name}")
            self.set_status_state(self.face_status, ai_analysis["state"])
            self.face_distance.setText(f"Confidence: {ai_analysis['confidence']:.2f}%")

        except Exception as e:
            QMessageBox.critical(self, "PDF processing failed", str(e))
            self.face_status.setText("PDF processing failed")
            self.set_status_state(self.face_status, STATUS_FAIL)

    def on_lab_entry_clicked(self):
        name = self.picked_existing_name()
        if not name:
            self.face_status.setText("Pick a saved user first")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return

        person_dir = os.path.join(FACE_DB_PATH, name)
        if not os.path.isdir(person_dir):
            self.face_status.setText("Selected user was not found in FaceDB")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return

        initial_data = self.load_lab_results(name)
        dialog = LabResultsDialog(name, initial_data, self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                values = dialog.get_values()
                self.save_lab_results(name, values)
                analysis = analyze_saved_lab_results(values)
                self.update_liver_screening_ui(analysis)
                self.face_status.setText(f"Lab results saved for {name}")
                self.set_status_state(self.face_status, STATUS_READY)
                self.face_identity.setText(name)
                self.face_distance.setText("Confidence: Lab data updated")
            except ValueError as e:
                QMessageBox.warning(self, "Invalid input", str(e))
                self.face_status.setText("Lab results were not saved")
                self.set_status_state(self.face_status, STATUS_FAIL)

    def on_analyze_lab_clicked(self):
        name = self.picked_existing_name()
        if not name:
            self.face_status.setText("Pick a saved user first")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return

        person_dir = os.path.join(FACE_DB_PATH, name)
        if not os.path.isdir(person_dir):
            self.face_status.setText("Selected user was not found in FaceDB")
            self.set_status_state(self.face_status, STATUS_FAIL)
            return

        values = self.load_lab_results(name)
        analysis = analyze_saved_lab_results(values)
        self.update_liver_screening_ui(analysis)
        self.face_identity.setText(name)
        self.face_status.setText(f"Lab screening updated for {name}")
        self.set_status_state(self.face_status, analysis["state"])
        self.face_distance.setText("Confidence: Rule-based screening")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_camera_view()
        self.refresh_face_crop()

    def closeEvent(self, event):
        try:
            self.sensor_worker.stop()
            self.sensor_thread.quit()
            self.sensor_thread.wait(2000)
        except Exception:
            pass

        try:
            self.weather_worker.stop()
            self.weather_thread.quit()
            self.weather_thread.wait(2000)
        except Exception:
            pass

        try:
            self.camera_worker.stop()
            self.camera_thread.quit()
            self.camera_thread.wait(3000)
        except Exception:
            pass

        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AI Smart Mirror Dashboard - Step 5 (Lab Screening)")
    window = SmartMirrorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
