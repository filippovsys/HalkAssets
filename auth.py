"""
Авторизация пользователей.

Два типа учётных записей:

  AD    — доменная учётная запись. Проверяется через Windows API
          LogonUser. Если сети до контроллера домена нет (ноутбук
          в гараже, командировка и т.д.) — программа пробует
          проверить пароль по КЭШУ последнего успешного входа.
          Кэш — это PBKDF2-хэш пароля, сохранённый при последнем
          удачном входе через AD. Работает не дольше CACHE_VALID_DAYS.

  local — локальная учётка программы. Пароль хранится как
          PBKDF2-SHA256 (100 000 итераций) + соль 16 байт.
          Нужна как emergency-доступ и для пользователей, которым
          доменную учётку не заводили.

Первый запуск: если в таблице users нет ни одного локального админа,
показывается форма создания администратора.
"""

import hashlib
import secrets
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta

try:
    import win32security
    import pywintypes
    HAS_AD = True
except Exception:
    HAS_AD = False

from db import get_conn, log_audit


# ============================================================
# НАСТРОЙКИ
# ============================================================

# Сколько дней кэш AD-пароля считается валидным. Если ноутбук не
# выходил в сеть дольше — вход по кэшу запрещается (только через AD).
# 30 дней — компромисс: хватает на командировку, но уволенный
# сотрудник не сможет годами ходить с ноутбуком.
CACHE_VALID_DAYS = 30


# Коды ошибок Windows, означающие что DC ОТВЕТИЛ и пароль/учётка
# просто не подошли. Всё остальное (нет DC, сеть недоступна и т.д.)
# считаем "AD недоступен" и пробуем кэш.
#
#   1326 — ERROR_LOGON_FAILURE (неверный логин/пароль)
#   1331 — ERROR_ACCOUNT_DISABLED
#   1907 — ERROR_PASSWORD_MUST_CHANGE
#   1909 — ERROR_ACCOUNT_LOCKED_OUT
#   1793 — ERROR_ACCOUNT_EXPIRED
AD_REJECTED_CODES = {1326, 1331, 1907, 1909, 1793}


# ============================================================
# ПАРОЛИ (локальные и кэш)
# ============================================================

def hash_password(password, salt=None):
    """
    PBKDF2-SHA256, 100 000 итераций.
    Возвращает (salt_hex, hash_hex).
    """
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    )
    return salt, dk.hex()


def verify_password(password, salt, stored_hash):
    """Проверка пароля в constant-time."""
    try:
        _, computed = hash_password(password, salt)
        return secrets.compare_digest(computed, stored_hash)
    except Exception:
        return False


# ============================================================
# AD-АУТЕНТИФИКАЦИЯ
# ============================================================

def check_ad_credentials(raw_login, password):
    """
    Проверяет учётные данные через Active Directory.

    raw_login понимает три формата:
        "ivanov"                 — просто имя, домен подставит система
        "BANK\\ivanov"           — явно указан домен
        "ivanov@bank.local"      — UPN-формат

    Возвращает (result, reason), где result — одно из:
        True                  — AD подтвердил учётку
        False                 — AD отверг (неверный пароль/учётка и т.п.)
        None                  — AD недоступен (нет сети до DC)
    """

    if not HAS_AD:
        return None, "Модуль pywin32 не установлен"

    raw_login = (raw_login or "").strip()
    if not raw_login:
        return False, "Пустой логин"
    if not password:
        return False, "Пустой пароль"

    if "\\" in raw_login:
        domain, username = raw_login.split("\\", 1)
    elif "@" in raw_login:
        username, domain = raw_login.split("@", 1)
    else:
        domain, username = None, raw_login

    try:
        token = win32security.LogonUser(
            username,
            domain,
            password,
            win32security.LOGON32_LOGON_NETWORK,
            win32security.LOGON32_PROVIDER_DEFAULT,
        )
        win32security.CloseHandle(token)
        return True, None

    except pywintypes.error as e:
        code = getattr(e, "winerror", None)

        # DC ответил и отказал — это "неверный пароль", не сеть.
        if code in AD_REJECTED_CODES:
            if code == 1326:
                return False, "Неверное имя пользователя или пароль"
            if code == 1331:
                return False, "Учётная запись отключена в Active Directory"
            if code == 1907:
                return False, "Требуется смена пароля в домене"
            if code == 1909:
                return False, "Учётная запись заблокирована"
            if code == 1793:
                return False, "Срок действия учётной записи истёк"

        # Всё остальное — считаем, что DC недоступен.
        return None, f"Active Directory недоступен ({code or 'н/д'})"


# ============================================================
# РАБОТА С ТАБЛИЦЕЙ users
# ============================================================

def get_user_by_login(conn, login):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, username, display_name, auth_type,
               password_salt, password_hash, is_admin, is_active,
               password_cached_at
        FROM users
        WHERE LOWER(username) = LOWER(?)
    """, (login,))
    return cur.fetchone()


def ensure_admin_exists():
    """
    Есть ли хоть один активный локальный админ. Если нет — нужно
    показать диалог создания первого администратора.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM users
        WHERE is_admin = 1 AND is_active = 1 AND auth_type = 'local'
    """)
    count = cur.fetchone()[0]
    conn.close()
    return count > 0


def create_local_user(conn, username, display_name, password, is_admin):
    salt, pwd_hash = hash_password(password)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users
        (username, display_name, auth_type, password_salt, password_hash,
         is_admin, is_active, created_at)
        VALUES (?, ?, 'local', ?, ?, ?, 1, ?)
    """, (
        username, display_name, salt, pwd_hash,
        1 if is_admin else 0,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    return cur.lastrowid


def create_ad_user(conn, username, display_name, is_admin):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users
        (username, display_name, auth_type, is_admin, is_active, created_at)
        VALUES (?, ?, 'ad', ?, 1, ?)
    """, (
        username, display_name,
        1 if is_admin else 0,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    return cur.lastrowid


def touch_last_login(conn, user_id):
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET last_login=? WHERE id=?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id)
    )
    conn.commit()


def update_ad_cache(conn, user_id, password):
    """
    Обновляет кэш пароля AD после успешного входа в сети.
    Вызывается ТОЛЬКО после успешного LogonUser.
    """
    salt, pwd_hash = hash_password(password)
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET password_salt=?, password_hash=?, password_cached_at=?
        WHERE id=?
    """, (
        salt, pwd_hash,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_id
    ))
    conn.commit()


def is_cache_fresh(cached_at_str):
    """
    Проверяет, не старше ли CACHE_VALID_DAYS дней метка кэша.
    """
    if not cached_at_str:
        return False
    try:
        cached_at = datetime.strptime(cached_at_str, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return False
    return (datetime.now() - cached_at) <= timedelta(days=CACHE_VALID_DAYS)


# ============================================================
# ОКНО ЛОГИНА
# ============================================================

class LoginWindow(tk.Tk):
    """
    Окно авторизации. После успешного входа self.user содержит
    dict: id, username, display_name, is_admin, auth_type.
    """

    def __init__(self):
        super().__init__()

        self.user = None

        self.title("HalkAssets — Вход")
        self.resizable(False, False)

        w, h = 440, 280
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")

        if not ensure_admin_exists():
            self.after(50, self._show_first_run_dialog)

        self._build_ui()

    # ---------------- UI ----------------

    def _build_ui(self):
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, text="Вход в систему",
            font=("", 14, "bold")
        ).grid(row=0, column=0, columnspan=2, pady=(0, 15), sticky="w")

        ttk.Label(frame, text="Логин:").grid(row=1, column=0, sticky="w", pady=5)
        self.login_var = tk.StringVar()
        login_entry = ttk.Entry(frame, textvariable=self.login_var, width=32)
        login_entry.grid(row=1, column=1, pady=5)
        login_entry.focus_set()

        ttk.Label(frame, text="Пароль:").grid(row=2, column=0, sticky="w", pady=5)
        self.password_var = tk.StringVar()
        pwd_entry = ttk.Entry(frame, textvariable=self.password_var, show="•", width=32)
        pwd_entry.grid(row=2, column=1, pady=5)

        ttk.Label(
            frame,
            text=(
                "Введите логин и пароль, выданные администратором программы.\n"
                "Если забыли пароль — обратитесь к администратору."
            ),
            foreground="gray",
            justify="left"
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 15))

        login_entry.bind("<Return>", lambda e: self._on_login())
        pwd_entry.bind("<Return>", lambda e: self._on_login())

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(5, 0))
        ttk.Button(btn_frame, text="Войти", command=self._on_login).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Выход", command=self._on_cancel).pack(side="left", padx=5)

    # ---------------- Логика входа ----------------

    def _on_cancel(self):
        self.user = None
        self.destroy()

    def _on_login(self):
        login = self.login_var.get().strip()
        password = self.password_var.get()

        if not login or not password:
            messagebox.showwarning("Вход", "Введите логин и пароль.", parent=self)
            return

        conn = get_conn()
        try:
            row = get_user_by_login(conn, login)

            # --- Пользователь известен программе ---
            if row:
                (uid, username, display_name, auth_type,
                 salt, pwd_hash, is_admin, is_active,
                 cached_at) = row

                if not is_active:
                    messagebox.showerror("Вход", "Учётная запись отключена.", parent=self)
                    return

                if auth_type == "local":
                    if not verify_password(password, salt, pwd_hash):
                        messagebox.showerror("Вход", "Неверный пароль.", parent=self)
                        return
                else:  # ad
                    ok, reason = self._check_ad_with_cache(
                        conn, uid, login, password, salt, pwd_hash, cached_at
                    )
                    if not ok:
                        messagebox.showerror("Вход", reason, parent=self)
                        return

                self.user = {
                    "id": uid,
                    "username": username,
                    "display_name": display_name,
                    "auth_type": auth_type,
                    "is_admin": bool(is_admin),
                }

            # --- Пользователя в таблице нет: пробуем как AD ---
            else:
                result, reason = check_ad_credentials(login, password)

                if result is False:
                    # AD ответил и отверг — показываем понятную ошибку
                    messagebox.showerror("Вход", reason or "Отказано", parent=self)
                    return
                if result is None:
                    # AD недоступен и кэша тоже нет — первый вход
                    # вне сети невозможен.
                    messagebox.showerror(
                        "Вход",
                        "Active Directory недоступен, а для этого пользователя "
                        "ещё нет сохранённого кэша. Первый вход возможен "
                        "только при наличии сети до контроллера домена.",
                        parent=self
                    )
                    return

                # AD подтвердил — создаём запись и сразу пишем кэш.
                uname = login.split("\\")[-1].split("@")[0]
                uid = create_ad_user(conn, uname, uname, is_admin=False)
                update_ad_cache(conn, uid, password)

                self.user = {
                    "id": uid,
                    "username": uname,
                    "display_name": uname,
                    "auth_type": "ad",
                    "is_admin": False,
                }

            # Логируем успешный вход.
            touch_last_login(conn, self.user["id"])
            log_audit(
                conn, self.user["id"], self.user["username"],
                self.user["display_name"], "login",
                details=f"auth_type={self.user['auth_type']}"
            )
            conn.commit()

        finally:
            conn.close()

        self.destroy()

    def _check_ad_with_cache(self, conn, uid, login, password,
                              salt, pwd_hash, cached_at):
        """
        Проверяет AD-пользователя. Если AD доступен — обычная проверка
        через LogonUser (и обновление кэша при успехе). Если AD
        недоступен — проверка по кэшу (если он не старше
        CACHE_VALID_DAYS дней).

        Возвращает (True, None) при успехе или (False, "причина").
        """

        result, reason = check_ad_credentials(login, password)

        if result is True:
            # Успех в сети — обновляем кэш на случай будущего оффлайна.
            update_ad_cache(conn, uid, password)
            return True, None

        if result is False:
            # DC ответил и отверг — кэш не поможет (пароль реально неверный).
            return False, reason or "Неверный пароль"

        # result is None — AD недоступен. Смотрим кэш.
        if not pwd_hash or not salt:
            return False, (
                "Active Directory недоступен, а кэша пароля для этой "
                "учётной записи нет. Первый вход возможен только при "
                "наличии сети."
            )

        if not is_cache_fresh(cached_at):
            return False, (
                f"Active Directory недоступен, а кэш пароля устарел "
                f"(старше {CACHE_VALID_DAYS} дней). Войдите при наличии "
                f"сети до контроллера домена."
            )

        if not verify_password(password, salt, pwd_hash):
            return False, "Неверный пароль (проверено по сохранённому кэшу)."

        return True, None

    # ---------------- Первый запуск ----------------

    def _show_first_run_dialog(self):
        dlg = FirstAdminDialog(self)
        self.wait_window(dlg)


class FirstAdminDialog(tk.Toplevel):
    """
    Диалог создания первого администратора. Показывается один раз.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Первый запуск — создание администратора")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=(
                "В базе ещё нет ни одного администратора.\n"
                "Создайте его сейчас — иначе управлять пользователями\n"
                "и журналом действий будет некому."
            ),
            justify="left"
        ).grid(row=0, column=0, columnspan=2, pady=(0, 15), sticky="w")

        ttk.Label(frame, text="Логин:").grid(row=1, column=0, sticky="w", pady=4)
        self.login_var = tk.StringVar(value="admin")
        ttk.Entry(frame, textvariable=self.login_var, width=28).grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Отображаемое имя:").grid(row=2, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar(value="Администратор")
        ttk.Entry(frame, textvariable=self.name_var, width=28).grid(row=2, column=1, pady=4)

        ttk.Label(frame, text="Пароль:").grid(row=3, column=0, sticky="w", pady=4)
        self.pwd1_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pwd1_var, show="•", width=28).grid(row=3, column=1, pady=4)

        ttk.Label(frame, text="Повтор пароля:").grid(row=4, column=0, sticky="w", pady=4)
        self.pwd2_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pwd2_var, show="•", width=28).grid(row=4, column=1, pady=4)

        ttk.Label(
            frame,
            text="Минимум 8 символов.",
            foreground="gray"
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 12))

        btn = ttk.Frame(frame)
        btn.grid(row=6, column=0, columnspan=2)
        ttk.Button(btn, text="Создать", command=self._create).pack(side="left", padx=5)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Return>", lambda e: self._create())

    def _create(self):
        login = self.login_var.get().strip()
        name = self.name_var.get().strip() or login
        pwd1 = self.pwd1_var.get()
        pwd2 = self.pwd2_var.get()

        if not login:
            messagebox.showwarning("Первый запуск", "Введите логин.", parent=self)
            return
        if len(pwd1) < 8:
            messagebox.showwarning("Первый запуск", "Пароль минимум 8 символов.", parent=self)
            return
        if pwd1 != pwd2:
            messagebox.showwarning("Первый запуск", "Пароли не совпадают.", parent=self)
            return

        conn = get_conn()
        try:
            create_local_user(conn, login, name, pwd1, is_admin=True)
        except Exception as e:
            messagebox.showerror("Первый запуск", f"Не удалось создать: {e}", parent=self)
            return
        finally:
            conn.close()

        messagebox.showinfo("Готово", "Администратор создан. Теперь войдите под ним.", parent=self)
        self.destroy()

    def _cancel(self):
        parent = self.master
        self.destroy()
        if isinstance(parent, LoginWindow):
            parent.user = None
            parent.destroy()


# ============================================================
# СМЕНА ПАРОЛЯ (для локального пользователя)
# ============================================================

class ChangePasswordDialog(tk.Toplevel):
    """
    Смена собственного пароля. Для AD-пользователей недоступно —
    пароль меняется в Windows (Ctrl+Alt+Del → Сменить пароль).
    """

    def __init__(self, parent, current_user):
        super().__init__(parent)
        self.current_user = current_user

        self.title("Смена пароля")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Текущий пароль:").grid(row=0, column=0, sticky="w", pady=4)
        self.old_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.old_var, show="•", width=26).grid(row=0, column=1, pady=4)

        ttk.Label(frame, text="Новый пароль:").grid(row=1, column=0, sticky="w", pady=4)
        self.new1_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.new1_var, show="•", width=26).grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Повтор нового:").grid(row=2, column=0, sticky="w", pady=4)
        self.new2_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.new2_var, show="•", width=26).grid(row=2, column=1, pady=4)

        btn = ttk.Frame(frame)
        btn.grid(row=3, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn, text="Сменить", command=self._on_save).pack(side="left", padx=5)
        ttk.Button(btn, text="Отмена", command=self.destroy).pack(side="left", padx=5)

        self.bind("<Return>", lambda e: self._on_save())

    def _on_save(self):
        old = self.old_var.get()
        new1 = self.new1_var.get()
        new2 = self.new2_var.get()

        if len(new1) < 8:
            messagebox.showwarning("Смена пароля", "Новый пароль минимум 8 символов.", parent=self)
            return
        if new1 != new2:
            messagebox.showwarning("Смена пароля", "Новые пароли не совпадают.", parent=self)
            return

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT password_salt, password_hash FROM users WHERE id=?",
                (self.current_user["id"],)
            )
            row = cur.fetchone()
            if not row:
                messagebox.showerror("Смена пароля", "Пользователь не найден.", parent=self)
                return

            salt, stored_hash = row
            if not verify_password(old, salt, stored_hash):
                messagebox.showerror("Смена пароля", "Текущий пароль неверный.", parent=self)
                return

            new_salt, new_hash = hash_password(new1)
            cur.execute("""
                UPDATE users SET password_salt=?, password_hash=?
                WHERE id=?
            """, (new_salt, new_hash, self.current_user["id"]))

            log_audit(
                conn,
                self.current_user["id"], self.current_user["username"],
                self.current_user["display_name"], "change_password"
            )
            conn.commit()
        finally:
            conn.close()

        messagebox.showinfo("Смена пароля", "Пароль изменён.", parent=self)
        self.destroy()