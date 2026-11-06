import copy
import json
import os
import sys

# Папка рядом с EXE или скриптом
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")


DEFAULT_SETTINGS = {
    "columns": {},
    "hidden_columns": [],
    "printer": "",
    "scanner_port": "AUTO",
    "sort_column": "id",
    "sort_direction": "desc",
    "branch_name": "",
    "filters": {
        "employee": "",
        "location": "",
        "category": "",
        "date_from": "",
        "date_to": ""
    }
}


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        defaults = copy.deepcopy(DEFAULT_SETTINGS)
        save_settings(defaults)
        return defaults

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return copy.deepcopy(DEFAULT_SETTINGS)

    for k, v in DEFAULT_SETTINGS.items():
        if k not in data:
            data[k] = copy.deepcopy(v)

    return data


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)


# =========================
# КОЛОНКИ
# =========================

def save_column_width(column, width):
    s = load_settings()
    s["columns"][column] = width
    save_settings(s)


def get_column_width(column, default=100):
    s = load_settings()
    return s["columns"].get(column, default)


def set_hidden_columns(cols):
    s = load_settings()
    s["hidden_columns"] = cols
    save_settings(s)


def get_hidden_columns():
    s = load_settings()
    return s.get("hidden_columns", [])


# =========================
# ПРИНТЕР
# =========================

def set_printer(name):
    s = load_settings()
    s["printer"] = name
    save_settings(s)


def get_printer():
    s = load_settings()
    return s.get("printer", "")


# =========================
# СКАНЕР
# =========================

def set_scanner_port(port):
    s = load_settings()
    s["scanner_port"] = port
    save_settings(s)


def get_scanner_port():
    s = load_settings()
    return s.get("scanner_port", "AUTO")


# =========================
# СОРТИРОВКА ТАБЛИЦЫ
# =========================

def set_sort(column, direction):
    s = load_settings()
    s["sort_column"] = column
    s["sort_direction"] = direction
    save_settings(s)


def get_sort():
    s = load_settings()
    return s.get("sort_column", "id"), s.get("sort_direction", "desc")


# =========================
# ФИЛИАЛ
# =========================

def get_branch_name():
    s = load_settings()
    return s.get("branch_name", "")


def set_branch_name(name):
    s = load_settings()
    s["branch_name"] = name
    save_settings(s)


# =========================
# ФИЛЬТРЫ
# =========================

def save_filter(name, value):
    s = load_settings()
    s["filters"][name] = value
    save_settings(s)


def get_filter(name):
    s = load_settings()
    return s["filters"].get(name, "")


def get_all_filters():
    s = load_settings()
    return s.get("filters", {})