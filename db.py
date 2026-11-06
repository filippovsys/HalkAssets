import os
import sys
import sqlite3
from datetime import datetime

from version import APP_VERSION, DATABASE_VERSION


# ============================================================
# PATHS
# ============================================================

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "database.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backup")
MAX_BACKUPS = 30


# ============================================================
# ПОИСКОВАЯ НОРМАЛИЗАЦИЯ
# ============================================================

# Карта туркменских диакритических букв в простые латинские
# эквиваленты. Используется в поиске: «Seyh» найдёт «Şeyh»,
# «Cek» найдёт «Çek», «Yyldyz» найдёт «Ýyldyz» и наоборот.
TURKMEN_TRANSLIT = {
    "ş": "s",
    "ç": "c",
    "ä": "a",
    "ö": "o",
    "ü": "u",
    "ý": "y",
    "ň": "n",
    "ž": "z",
    # Бонус: русская «ё» тоже ищется как «е».
    "ё": "е",
}


def normalize_for_search(text):
    """
    Приводит строку к «плоскому» виду для регистро- и диакритико-
    независимого поиска.

    Делает две вещи:
      1. Приводит к нижнему регистру (Python .lower() корректно
         обрабатывает Unicode; SQLite LOWER() умеет только ASCII).
      2. Заменяет туркменские диакритические буквы на простые
         латинские аналоги (ş → s, ç → c, ý → y и т.д.).

    Благодаря этому «Seyh», «şeyh» и «ŞEYH» для поиска — одно и
    то же. Результат используется и для поля БД, и для того, что
    набрал пользователь в поиске — тогда LIKE сравнивает уже
    нормализованные строки, и регистр не важен.
    """
    if text is None:
        return ""
    s = str(text).lower()
    for src, dst in TURKMEN_TRANSLIT.items():
        s = s.replace(src, dst)
    return " ".join(s.split())


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_conn():
    """
    Возвращает подключение к базе данных.

    При необходимости:
    1. создаёт папку data;
    2. создаёт базу;
    3. создаёт/обновляет структуру;
    4. выполняет миграции.
    """

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    conn = sqlite3.connect(DB_FILE, timeout=5)

    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    # Регистрируем Python-функцию нормализации как SQLite-функцию.
    # Нужно потому, что встроенный SQLite LOWER() работает только с
    # ASCII: LOWER('К') = 'К', LOWER('Ş') = 'Ş'. Наш normalize_for_search
    # корректно обрабатывает Unicode и убирает туркменскую диакритику.
    # Регистрируем ДО ensure_database, чтобы функция была доступна
    # любому пользовательскому запросу после открытия соединения.
    conn.create_function("normalize_search", 1, normalize_for_search)

    ensure_database(conn)

    return conn


# ============================================================
# INITIAL DATABASE STRUCTURE
# ============================================================

def create_assets_table(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            name TEXT,
            category TEXT,
            location TEXT,
            employee TEXT,
            serial TEXT,
            note TEXT,
            buy_date TEXT,
            price REAL,
            residual REAL,
            last_seen TEXT
        )
    """)

    conn.commit()


def create_dictionaries_tables(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    conn.commit()


def create_removed_assets_table(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS removed_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_id INTEGER,
            code TEXT,
            name TEXT,
            category TEXT,
            location TEXT,
            employee TEXT,
            serial TEXT,
            note TEXT,
            buy_date TEXT,
            price REAL,
            residual REAL,
            last_seen TEXT,
            removed_at TEXT,
            reason TEXT
        )
    """)

    conn.commit()


def create_movement_history_table(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS movement_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            old_location TEXT,
            new_location TEXT,
            old_employee TEXT,
            new_employee TEXT,
            changed_at TEXT,
            FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_movement_history_asset_id
        ON movement_history(asset_id)
    """)

    conn.commit()


def create_meta_table(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.commit()


def create_indexes(conn):
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_assets_location ON assets(location)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_assets_employee ON assets(employee)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_removed_assets_code ON removed_assets(code)")
    conn.commit()


def create_users_table(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            auth_type TEXT NOT NULL,
            password_salt TEXT,
            password_hash TEXT,
            password_cached_at TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            last_login TEXT
        )
    """)

    conn.commit()


def create_audit_log_table(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            display_name TEXT,
            action TEXT NOT NULL,
            entity TEXT,
            entity_id INTEGER,
            details TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
        ON audit_log(created_at)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_user_id
        ON audit_log(user_id)
    """)

    conn.commit()


def log_audit(conn, user_id, username, display_name,
               action, entity=None, entity_id=None, details=None):
    """
    Запись в audit_log. НЕ делает commit — фиксируется вместе с
    операцией, к которой относится.
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO audit_log
        (user_id, username, display_name, action, entity, entity_id, details, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        user_id, username, display_name, action,
        entity, entity_id, details,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))


# ============================================================
# META FUNCTIONS
# ============================================================

def get_meta(conn, key, default=None):
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cur.fetchone()
    if row is None:
        return default
    return row[0]


def set_meta(conn, key, value):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO meta (key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
    """, (key, str(value)))
    conn.commit()


def get_schema_version(conn):
    value = get_meta(conn, "schema_version", "0")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def set_schema_version(conn, version):
    set_meta(conn, "schema_version", version)


# ============================================================
# DATABASE METADATA
# ============================================================

def initialize_meta(conn):
    if get_meta(conn, "created_at") is None:
        set_meta(conn, "created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if get_meta(conn, "app_version") is None:
        set_meta(conn, "app_version", APP_VERSION)
    if get_meta(conn, "database_version") is None:
        set_meta(conn, "database_version", DATABASE_VERSION)


# ============================================================
# SCHEMA CHECK
# ============================================================

def ensure_assets_schema(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(assets)")
    cols = [column[1] for column in cur.fetchall()]

    required = {
        "employee": "TEXT",
        "serial": "TEXT",
        "note": "TEXT",
        "buy_date": "TEXT",
        "price": "REAL",
        "residual": "REAL",
        "last_seen": "TEXT"
    }

    changed = False
    for column, column_type in required.items():
        if column not in cols:
            cur.execute(f"ALTER TABLE assets ADD COLUMN {column} {column_type}")
            changed = True

    if changed:
        conn.commit()


def ensure_users_schema(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(users)")
    cols = [column[1] for column in cur.fetchall()]

    if "password_cached_at" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN password_cached_at TEXT")
        conn.commit()


# ============================================================
# MIGRATIONS
# ============================================================

def migrate_0_to_1(conn):
    create_meta_table(conn)
    if get_meta(conn, "created_at") is None:
        set_meta(conn, "created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    set_meta(conn, "app_version", APP_VERSION)
    set_meta(conn, "database_version", 1)
    set_schema_version(conn, 1)


def migrate_1_to_2(conn):
    cur = conn.cursor()

    cur.execute("""
        SELECT code, COUNT(*) AS cnt
        FROM assets
        WHERE code IS NOT NULL
        GROUP BY code
        HAVING cnt > 1
    """)
    duplicates = cur.fetchall()

    renamed = []
    for code, _cnt in duplicates:
        cur.execute("SELECT id FROM assets WHERE code=? ORDER BY id", (code,))
        ids = [row[0] for row in cur.fetchall()]

        for idx, asset_id in enumerate(ids[1:], start=2):
            new_code = f"{code} (дубль {idx})"
            counter = idx
            while True:
                cur.execute("SELECT 1 FROM assets WHERE code=?", (new_code,))
                if cur.fetchone() is None:
                    break
                counter += 1
                new_code = f"{code} (дубль {counter})"
            cur.execute("UPDATE assets SET code=? WHERE id=?", (new_code, asset_id))
            renamed.append((asset_id, code, new_code))

    if renamed:
        print(f"[db] Разрешено дублей code: {len(renamed)}", flush=True)
        for asset_id, old, new in renamed[:20]:
            print(f"  id={asset_id}: '{old}' -> '{new}'", flush=True)
        if len(renamed) > 20:
            print(f"  ... и ещё {len(renamed) - 20}", flush=True)

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_code_unique ON assets(code)"
    )

    conn.commit()
    set_schema_version(conn, 2)


def migrate_2_to_3(conn):
    create_users_table(conn)
    create_audit_log_table(conn)
    set_schema_version(conn, 3)


def migrate_3_to_4(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(users)")
    cols = [column[1] for column in cur.fetchall()]
    if "password_cached_at" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN password_cached_at TEXT")
    conn.commit()
    set_schema_version(conn, 4)


def migrate_database(conn):
    current_version = get_schema_version(conn)

    if current_version > DATABASE_VERSION:
        raise RuntimeError(
            f"Версия базы данных ({current_version}) "
            f"новее поддерживаемой приложением версии "
            f"({DATABASE_VERSION})."
        )

    if current_version < 1:
        migrate_0_to_1(conn)
        current_version = 1

    if current_version < 2:
        migrate_1_to_2(conn)
        current_version = 2

    if current_version < 3:
        migrate_2_to_3(conn)
        current_version = 3

    if current_version < 4:
        migrate_3_to_4(conn)
        current_version = 4

    set_meta(conn, "app_version", APP_VERSION)
    set_meta(conn, "database_version", DATABASE_VERSION)
    conn.commit()


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def ensure_database(conn):
    create_assets_table(conn)
    create_dictionaries_tables(conn)
    create_removed_assets_table(conn)
    create_movement_history_table(conn)
    create_users_table(conn)
    create_audit_log_table(conn)
    create_meta_table(conn)

    ensure_assets_schema(conn)
    ensure_users_schema(conn)

    migrate_database(conn)

    ensure_assets_schema(conn)
    ensure_users_schema(conn)
    create_indexes(conn)

    conn.commit()


# ============================================================
# MOVEMENT HISTORY
# ============================================================

def log_movement(conn, asset_id, old_location, new_location,
                  old_employee, new_employee, changed_at):
    """
    ВАЖНО: НЕ делает commit — коммит выполняет вызывающий код в конце
    своей операции, чтобы история и карточка попадали в одну
    транзакцию.
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO movement_history(
            asset_id, old_location, new_location,
            old_employee, new_employee, changed_at
        )
        VALUES(?,?,?,?,?,?)
    """, (asset_id, old_location, new_location,
          old_employee, new_employee, changed_at))


def get_movement_history(conn, asset_id, limit=3):
    cur = conn.cursor()
    cur.execute("""
        SELECT old_location, new_location, old_employee, new_employee, changed_at
        FROM movement_history
        WHERE asset_id = ?
        ORDER BY changed_at DESC, id DESC
        LIMIT ?
    """, (asset_id, limit))
    return cur.fetchall()


# ============================================================
# SOFT DELETE / RESTORE
# ============================================================

def soft_delete_assets(conn, codes, reason):
    if not codes:
        return 0

    cur = conn.cursor()
    placeholders = ",".join("?" * len(codes))

    cur.execute(
        f"""SELECT id, code, name, category, location, employee,
                   serial, note, buy_date, price, residual, last_seen
            FROM assets WHERE code IN ({placeholders})""",
        codes
    )
    rows = cur.fetchall()
    if not rows:
        return 0

    removed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for r in rows:
        cur.execute("""
            INSERT INTO removed_assets
            (original_id, code, name, category, location, employee,
             serial, note, buy_date, price, residual, last_seen,
             removed_at, reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (*r, removed_at, reason))

    cur.execute(f"DELETE FROM assets WHERE code IN ({placeholders})", codes)
    conn.commit()
    return len(rows)


def restore_removed_asset(conn, removed_id):
    cur = conn.cursor()

    cur.execute("""
        SELECT code, name, category, location, employee, serial, note,
               buy_date, price, residual, last_seen
        FROM removed_assets WHERE id=?
    """, (removed_id,))
    row = cur.fetchone()

    if row is None:
        return False, "запись не найдена в архиве"

    code = row[0]
    if code:
        cur.execute("SELECT 1 FROM assets WHERE code=?", (code,))
        if cur.fetchone() is not None:
            return False, f"инвентарный номер «{code}» уже используется активной карточкой"

    cur.execute("""
        INSERT INTO assets
        (code, name, category, location, employee,
         serial, note, buy_date, price, residual, last_seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, row)

    cur.execute("DELETE FROM removed_assets WHERE id=?", (removed_id,))
    conn.commit()
    return True, None


# ============================================================
# USERS ADMIN
# ============================================================

def list_users(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, display_name, auth_type, is_admin, is_active,
               created_at, last_login
        FROM users
        ORDER BY is_active DESC, is_admin DESC, LOWER(username)
    """)
    return cur.fetchall()


def count_active_admins(conn, exclude_user_id=None):
    cur = conn.cursor()
    if exclude_user_id:
        cur.execute("""
            SELECT COUNT(*) FROM users
            WHERE is_admin=1 AND is_active=1 AND id != ?
        """, (exclude_user_id,))
    else:
        cur.execute("""
            SELECT COUNT(*) FROM users
            WHERE is_admin=1 AND is_active=1
        """)
    return cur.fetchone()[0]


def update_user_flags(conn, user_id, is_admin, is_active):
    cur = conn.cursor()
    cur.execute("""
        UPDATE users SET is_admin=?, is_active=?
        WHERE id=?
    """, (1 if is_admin else 0, 1 if is_active else 0, user_id))
    conn.commit()


def update_user_display_name(conn, user_id, display_name):
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET display_name=? WHERE id=?",
        (display_name, user_id)
    )
    conn.commit()


def set_local_password_hash(conn, user_id, salt, pwd_hash):
    cur = conn.cursor()
    cur.execute("""
        UPDATE users SET password_salt=?, password_hash=?
        WHERE id=? AND auth_type='local'
    """, (salt, pwd_hash, user_id))
    conn.commit()


def delete_user(conn, user_id):
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()


# ============================================================
# AUDIT LOG QUERY
# ============================================================

def query_audit_log(conn, user_id=None, action=None,
                    date_from=None, date_to=None, limit=1000):
    where = ["1=1"]
    params = []

    if user_id:
        where.append("user_id=?")
        params.append(user_id)
    if action:
        where.append("action=?")
        params.append(action)
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("created_at <= ?")
        params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)

    sql = f"""
        SELECT id, created_at, display_name, username, action,
               entity, entity_id, details
        FROM audit_log
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT ?
    """
    params.append(limit)

    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def count_audit_log(conn, user_id=None, action=None,
                    date_from=None, date_to=None):
    where = ["1=1"]
    params = []

    if user_id:
        where.append("user_id=?")
        params.append(user_id)
    if action:
        where.append("action=?")
        params.append(action)
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("created_at <= ?")
        params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)

    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) FROM audit_log WHERE {' AND '.join(where)}",
        params
    )
    return cur.fetchone()[0]


def get_distinct_audit_actions(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")
    return [r[0] for r in cur.fetchall()]


# ============================================================
# BACKUP
# ============================================================

def backup_database(reason="auto"):
    if not os.path.exists(DB_FILE):
        return None

    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"database_{timestamp}_{reason}.db"
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        src = sqlite3.connect(DB_FILE, timeout=5)
        try:
            dst = sqlite3.connect(backup_path)
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        _cleanup_old_backups()
        return backup_path

    except Exception:
        return None


def _cleanup_old_backups(max_backups=MAX_BACKUPS):
    try:
        if not os.path.exists(BACKUP_DIR):
            return

        files = [
            os.path.join(BACKUP_DIR, f)
            for f in os.listdir(BACKUP_DIR)
            if f.startswith("database_") and f.endswith(".db")
        ]
        files.sort(key=os.path.getmtime, reverse=True)

        for old_file in files[max_backups:]:
            os.remove(old_file)
    except Exception:
        pass


# ============================================================
# LEGACY COMPATIBILITY
# ============================================================

def init_db():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.create_function("normalize_search", 1, normalize_for_search)

    try:
        ensure_database(conn)
    finally:
        conn.close()