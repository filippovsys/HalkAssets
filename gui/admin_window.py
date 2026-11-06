"""
Админ-панель: управление пользователями и просмотр журнала действий.

Доступно только администраторам (см. MainWindow.create_menu).
Оба окна — Toplevel, не блокируют главное окно.

Логика безопасности:

  - Нельзя отключить самого себя.
  - Нельзя снять с себя is_admin, если ты последний активный админ.
  - Нельзя отключить последнего активного админа (даже если это
    не ты сам).
  - Пароли локальных пользователей хэшируются PBKDF2-SHA256, в
    открытом виде нигде не хранятся.
  - AD-пользователям сбросить пароль из программы нельзя: пароль
    живёт в домене, менять его надо средствами Windows.
  - Каждое действие (создание, отключение, сброс пароля и т.д.)
    пишется в audit_log.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

from openpyxl import Workbook

from db import (
    get_conn,
    log_audit,
    list_users,
    count_active_admins,
    update_user_flags,
    update_user_display_name,
    set_local_password_hash,
    delete_user,
    query_audit_log,
    count_audit_log,
    get_distinct_audit_actions,
)
from auth import hash_password


# ============================================================
# ОКНО "ПОЛЬЗОВАТЕЛИ"
# ============================================================

class UsersWindow(tk.Toplevel):
    """
    Управление пользователями. Только для админов.
    """

    def __init__(self, parent, current_user):
        super().__init__(parent)

        self.current_user = current_user

        self.title("Пользователи")
        self.geometry("1020x560")
        self.minsize(820, 420)

        self.create_ui()
        self.load_users()

    # ---------------- UI ----------------

    def create_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Button(top, text="Добавить локального", command=self.add_local_user).pack(side="left", padx=4)
        ttk.Button(top, text="Добавить AD-пользователя", command=self.add_ad_user).pack(side="left", padx=4)
        ttk.Button(top, text="Обновить", command=self.load_users).pack(side="left", padx=4)

        columns = ("username", "display_name", "auth_type", "role", "status", "last_login")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)

        self.tree.heading("username", text="Логин")
        self.tree.heading("display_name", text="Отображаемое имя")
        self.tree.heading("auth_type", text="Тип")
        self.tree.heading("role", text="Роль")
        self.tree.heading("status", text="Статус")
        self.tree.heading("last_login", text="Последний вход")

        self.tree.column("username", width=160, anchor="w")
        self.tree.column("display_name", width=220, anchor="w")
        self.tree.column("auth_type", width=80, anchor="center")
        self.tree.column("role", width=140, anchor="center")
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("last_login", width=160, anchor="w", stretch=True)

        self.tree.tag_configure("inactive", foreground="#999999")
        self.tree.tag_configure("me", background="#eaf4ff")

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=(5, 10))

        ttk.Button(btn_frame, text="Изменить имя", command=self.rename_user).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Сбросить пароль", command=self.reset_password).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Сделать/снять админа", command=self.toggle_admin).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Отключить/включить", command=self.toggle_active).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Удалить", command=self.delete_user_action).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Закрыть", command=self.destroy).pack(side="right", padx=4)

        self.count_label = ttk.Label(btn_frame, text="", foreground="gray")
        self.count_label.pack(side="right", padx=15)

    # ---------------- DATA ----------------

    def load_users(self):
        self.tree.delete(*self.tree.get_children())

        conn = get_conn()
        try:
            rows = list_users(conn)
        finally:
            conn.close()

        for (uid, username, display_name, auth_type,
             is_admin, is_active, created_at, last_login) in rows:
            tags = []
            if not is_active:
                tags.append("inactive")
            if uid == self.current_user["id"]:
                tags.append("me")

            self.tree.insert(
                "", "end", iid=str(uid),
                values=(
                    username,
                    display_name,
                    auth_type.upper(),
                    "Администратор" if is_admin else "Пользователь",
                    "Активен" if is_active else "Отключён",
                    last_login or "—",
                ),
                tags=tags
            )

        total = len(rows)
        active = sum(1 for r in rows if r[5])
        self.count_label.config(text=f"Всего: {total} · активных: {active}")

    def _get_selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _get_user_row(self, user_id):
        """Достаёт строку из БД по id — на случай, если что-то поменялось."""
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, username, display_name, auth_type, is_admin, is_active
                FROM users WHERE id=?
            """, (user_id,))
            return cur.fetchone()
        finally:
            conn.close()

    # ---------------- ДОБАВЛЕНИЕ ----------------

    def add_local_user(self):
        dlg = NewLocalUserDialog(self)
        self.wait_window(dlg)
        if dlg.created:
            self.load_users()

    def add_ad_user(self):
        dlg = NewAdUserDialog(self)
        self.wait_window(dlg)
        if dlg.created:
            self.load_users()

    # ---------------- РЕДАКТИРОВАНИЕ ----------------

    def rename_user(self):
        uid = self._get_selected()
        if uid is None:
            messagebox.showinfo("Пользователи", "Выберите пользователя.", parent=self)
            return
        row = self._get_user_row(uid)
        if not row:
            return

        _, username, old_name, auth_type, _, _ = row

        new_name = _ask_string(
            self, "Изменить отображаемое имя",
            f"Пользователь: {username}\n\nНовое отображаемое имя:",
            initial=old_name
        )
        if not new_name or new_name == old_name:
            return

        conn = get_conn()
        try:
            update_user_display_name(conn, uid, new_name)
            log_audit(
                conn,
                self.current_user["id"], self.current_user["username"],
                self.current_user["display_name"],
                "admin_rename_user", entity="users", entity_id=uid,
                details=f"{username}: '{old_name}' -> '{new_name}'"
            )
            conn.commit()
        finally:
            conn.close()

        self.load_users()

    def reset_password(self):
        uid = self._get_selected()
        if uid is None:
            messagebox.showinfo("Пользователи", "Выберите пользователя.", parent=self)
            return
        row = self._get_user_row(uid)
        if not row:
            return

        _, username, display_name, auth_type, _, _ = row

        if auth_type != "local":
            messagebox.showinfo(
                "Сброс пароля",
                "Это AD-пользователь. Пароль живёт в Active Directory — "
                "менять его надо средствами Windows (Ctrl+Alt+Del → \n"
                "Сменить пароль), чтобы сохранить единый аудит входов в домене.",
                parent=self
            )
            return

        dlg = ResetPasswordDialog(self, username, display_name)
        self.wait_window(dlg)
        if dlg.changed:
            messagebox.showinfo(
                "Пароль изменён",
                "Сообщите пользователю новый пароль.\n"
                "При первом удобном случае он может сменить его сам\n"
                "через меню «Пользователь → Сменить пароль».",
                parent=self
            )

    def toggle_admin(self):
        uid = self._get_selected()
        if uid is None:
            messagebox.showinfo("Пользователи", "Выберите пользователя.", parent=self)
            return
        row = self._get_user_row(uid)
        if not row:
            return

        _, username, display_name, auth_type, is_admin, is_active = row
        new_admin = not bool(is_admin)

        # Если снимаем админа — убеждаемся, что в системе останется
        # хотя бы один активный админ.
        if not new_admin:
            conn = get_conn()
            try:
                remaining = count_active_admins(conn, exclude_user_id=uid)
            finally:
                conn.close()

            if is_active and remaining == 0:
                messagebox.showerror(
                    "Нельзя снять",
                    "Это последний активный администратор.\n"
                    "Сначала назначьте другого — иначе управлять "
                    "пользователями будет некому.",
                    parent=self
                )
                return

            if uid == self.current_user["id"]:
                if not messagebox.askyesno(
                    "Снять с себя админа?",
                    "Вы снимаете права администратора с себя.\n\n"
                    "После этого вы больше не сможете открывать это окно. "
                    "Продолжить?",
                    parent=self
                ):
                    return

        conn = get_conn()
        try:
            update_user_flags(conn, uid, new_admin, bool(is_active))
            log_audit(
                conn,
                self.current_user["id"], self.current_user["username"],
                self.current_user["display_name"],
                "admin_set_admin", entity="users", entity_id=uid,
                details=f"{username}: is_admin {bool(is_admin)} -> {new_admin}"
            )
            conn.commit()
        finally:
            conn.close()

        self.load_users()

    def toggle_active(self):
        uid = self._get_selected()
        if uid is None:
            messagebox.showinfo("Пользователи", "Выберите пользователя.", parent=self)
            return
        row = self._get_user_row(uid)
        if not row:
            return

        _, username, display_name, auth_type, is_admin, is_active = row

        if uid == self.current_user["id"]:
            messagebox.showerror(
                "Нельзя",
                "Нельзя отключить самого себя.",
                parent=self
            )
            return

        # Если отключаем активного админа — проверяем, что останется
        # ещё хотя бы один.
        if is_active and is_admin:
            conn = get_conn()
            try:
                remaining = count_active_admins(conn, exclude_user_id=uid)
            finally:
                conn.close()

            if remaining == 0:
                messagebox.showerror(
                    "Нельзя отключить",
                    "Это последний активный администратор.",
                    parent=self
                )
                return

        new_active = not bool(is_active)
        action_text = "отключить" if is_active else "включить"

        if not messagebox.askyesno(
            f"Подтвердите",
            f"{action_text.capitalize()} пользователя «{username}»?",
            parent=self
        ):
            return

        conn = get_conn()
        try:
            update_user_flags(conn, uid, bool(is_admin), new_active)
            log_audit(
                conn,
                self.current_user["id"], self.current_user["username"],
                self.current_user["display_name"],
                "admin_set_active", entity="users", entity_id=uid,
                details=f"{username}: is_active {bool(is_active)} -> {new_active}"
            )
            conn.commit()
        finally:
            conn.close()

        self.load_users()

    def delete_user_action(self):
        uid = self._get_selected()
        if uid is None:
            messagebox.showinfo("Пользователи", "Выберите пользователя.", parent=self)
            return
        row = self._get_user_row(uid)
        if not row:
            return

        _, username, display_name, auth_type, is_admin, is_active = row

        if uid == self.current_user["id"]:
            messagebox.showerror("Нельзя", "Нельзя удалить самого себя.", parent=self)
            return

        if is_admin and is_active:
            conn = get_conn()
            try:
                remaining = count_active_admins(conn, exclude_user_id=uid)
            finally:
                conn.close()

            if remaining == 0:
                messagebox.showerror(
                    "Нельзя удалить",
                    "Это последний активный администратор.",
                    parent=self
                )
                return

        if not messagebox.askyesno(
            "Удалить пользователя?",
            f"Удалить пользователя «{username}» полностью?\n\n"
            "Рекомендуется вместо удаления использовать «Отключить» — "
            "тогда в журнале действий останутся записи с его именем.\n\n"
            "Удалить безвозвратно?",
            parent=self
        ):
            return

        conn = get_conn()
        try:
            log_audit(
                conn,
                self.current_user["id"], self.current_user["username"],
                self.current_user["display_name"],
                "admin_delete_user", entity="users", entity_id=uid,
                details=f"username={username}, display_name={display_name}"
            )
            delete_user(conn, uid)
            conn.commit()
        finally:
            conn.close()

        self.load_users()


# ============================================================
# ДИАЛОГИ
# ============================================================

def _ask_string(parent, title, prompt, initial=""):
    """
    Мини-диалог ввода строки. Tk-овский simpledialog выглядит страшно,
    поэтому сделаем свой — простой и в едином стиле.
    """
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    frame = ttk.Frame(dlg, padding=15)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text=prompt, justify="left").pack(anchor="w")
    var = tk.StringVar(value=initial)
    entry = ttk.Entry(frame, textvariable=var, width=36)
    entry.pack(pady=(8, 12), anchor="w")
    entry.focus_set()
    entry.select_range(0, "end")

    result = {"value": None}

    def ok():
        result["value"] = var.get().strip()
        dlg.destroy()

    btn = ttk.Frame(frame)
    btn.pack()
    ttk.Button(btn, text="OK", command=ok).pack(side="left", padx=4)
    ttk.Button(btn, text="Отмена", command=dlg.destroy).pack(side="left", padx=4)

    dlg.bind("<Return>", lambda e: ok())
    dlg.bind("<Escape>", lambda e: dlg.destroy())

    parent.wait_window(dlg)
    return result["value"]


class NewLocalUserDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.created = False

        self.title("Новый локальный пользователь")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Логин (латиница, без пробелов):").grid(row=0, column=0, sticky="w", pady=4)
        self.login_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.login_var, width=30).grid(row=0, column=1, pady=4)

        ttk.Label(frame, text="Отображаемое имя:").grid(row=1, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.name_var, width=30).grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Пароль (мин. 8 символов):").grid(row=2, column=0, sticky="w", pady=4)
        self.pwd1_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pwd1_var, show="•", width=30).grid(row=2, column=1, pady=4)

        ttk.Label(frame, text="Повтор пароля:").grid(row=3, column=0, sticky="w", pady=4)
        self.pwd2_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pwd2_var, show="•", width=30).grid(row=3, column=1, pady=4)

        self.admin_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Сделать администратором", variable=self.admin_var).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 12)
        )

        btn = ttk.Frame(frame)
        btn.grid(row=5, column=0, columnspan=2)
        ttk.Button(btn, text="Создать", command=self._create).pack(side="left", padx=4)
        ttk.Button(btn, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self._create())

    def _create(self):
        login = self.login_var.get().strip()
        name = self.name_var.get().strip() or login
        pwd1 = self.pwd1_var.get()
        pwd2 = self.pwd2_var.get()
        is_admin = bool(self.admin_var.get())

        if not login:
            messagebox.showwarning("Создание", "Введите логин.", parent=self)
            return
        if " " in login:
            messagebox.showwarning("Создание", "Логин не должен содержать пробелов.", parent=self)
            return
        if len(pwd1) < 8:
            messagebox.showwarning("Создание", "Пароль минимум 8 символов.", parent=self)
            return
        if pwd1 != pwd2:
            messagebox.showwarning("Создание", "Пароли не совпадают.", parent=self)
            return

        salt, pwd_hash = hash_password(pwd1)

        conn = get_conn()
        try:
            cur = conn.cursor()
            # Проверим уникальность логина.
            cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(?)", (login,))
            if cur.fetchone() is not None:
                messagebox.showerror(
                    "Создание",
                    f"Логин «{login}» уже занят.",
                    parent=self
                )
                return

            cur.execute("""
                INSERT INTO users
                (username, display_name, auth_type, password_salt, password_hash,
                 is_admin, is_active, created_at)
                VALUES (?, ?, 'local', ?, ?, ?, 1, ?)
            """, (
                login, name, salt, pwd_hash,
                1 if is_admin else 0,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

            # Логируем от лица текущего админа. Но чтобы не тащить
            # current_user через всю иерархию диалогов, пишем кратко:
            # user_id=NULL, а в details — логин создателя.
            log_audit(
                conn, None, "system", "system",
                "admin_create_user", entity="users",
                details=f"Создан local-пользователь {login} (admin={is_admin})"
            )
            conn.commit()
        finally:
            conn.close()

        self.created = True
        self.destroy()


class NewAdUserDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.created = False

        self.title("Новый AD-пользователь")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=(
                "Создаётся запись-обёртка для доменной учётки.\n"
                "Пароль здесь не задаётся — он хранится в AD.\n"
                "При первом входе пользователь введёт свой доменный пароль."
            ),
            justify="left",
            foreground="gray"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frame, text="Доменный логин (без домена):").grid(row=1, column=0, sticky="w", pady=4)
        self.login_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.login_var, width=30).grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Отображаемое имя:").grid(row=2, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.name_var, width=30).grid(row=2, column=1, pady=4)

        self.admin_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Сделать администратором", variable=self.admin_var).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 12)
        )

        btn = ttk.Frame(frame)
        btn.grid(row=4, column=0, columnspan=2)
        ttk.Button(btn, text="Создать", command=self._create).pack(side="left", padx=4)
        ttk.Button(btn, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self._create())

    def _create(self):
        login = self.login_var.get().strip()
        name = self.name_var.get().strip() or login
        is_admin = bool(self.admin_var.get())

        # Нормализуем: убираем domain\ или @domain.
        login = login.split("\\")[-1].split("@")[0]

        if not login:
            messagebox.showwarning("Создание", "Введите доменный логин.", parent=self)
            return

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(?)", (login,))
            if cur.fetchone() is not None:
                messagebox.showerror(
                    "Создание",
                    f"Пользователь с логином «{login}» уже есть в программе.",
                    parent=self
                )
                return

            cur.execute("""
                INSERT INTO users
                (username, display_name, auth_type, is_admin, is_active, created_at)
                VALUES (?, ?, 'ad', ?, 1, ?)
            """, (
                login, name,
                1 if is_admin else 0,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            log_audit(
                conn, None, "system", "system",
                "admin_create_user", entity="users",
                details=f"Создан AD-пользователь {login} (admin={is_admin})"
            )
            conn.commit()
        finally:
            conn.close()

        self.created = True
        self.destroy()


class ResetPasswordDialog(tk.Toplevel):
    def __init__(self, parent, username, display_name):
        super().__init__(parent)
        self.changed = False
        self.target_username = username

        self.title("Сброс пароля")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"Пользователь: {display_name} ({username})",
            font=("", 10, "bold")
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frame, text="Новый пароль (мин. 8):").grid(row=1, column=0, sticky="w", pady=4)
        self.pwd1_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pwd1_var, show="•", width=28).grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Повтор:").grid(row=2, column=0, sticky="w", pady=4)
        self.pwd2_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pwd2_var, show="•", width=28).grid(row=2, column=1, pady=4)

        btn = ttk.Frame(frame)
        btn.grid(row=3, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(btn, text="Сбросить", command=self._save).pack(side="left", padx=4)
        ttk.Button(btn, text="Отмена", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self._save())

    def _save(self):
        pwd1 = self.pwd1_var.get()
        pwd2 = self.pwd2_var.get()

        if len(pwd1) < 8:
            messagebox.showwarning("Сброс пароля", "Пароль минимум 8 символов.", parent=self)
            return
        if pwd1 != pwd2:
            messagebox.showwarning("Сброс пароля", "Пароли не совпадают.", parent=self)
            return

        salt, pwd_hash = hash_password(pwd1)

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM users WHERE LOWER(username)=LOWER(?)",
                (self.target_username,)
            )
            row = cur.fetchone()
            if not row:
                messagebox.showerror("Сброс пароля", "Пользователь не найден.", parent=self)
                return

            uid = row[0]
            set_local_password_hash(conn, uid, salt, pwd_hash)
            log_audit(
                conn, None, "system", "system",
                "admin_reset_password", entity="users", entity_id=uid,
                details=f"username={self.target_username}"
            )
            conn.commit()
        finally:
            conn.close()

        self.changed = True
        self.destroy()


# ============================================================
# ОКНО "ЖУРНАЛ ДЕЙСТВИЙ"
# ============================================================

class AuditLogWindow(tk.Toplevel):
    """
    Просмотр audit_log с фильтрами и экспортом в Excel.
    """

    DEFAULT_LIMIT = 1000

    def __init__(self, parent, current_user):
        super().__init__(parent)

        self.current_user = current_user

        self.title("Журнал действий")
        self.geometry("1180x620")
        self.minsize(900, 450)

        self.create_ui()
        self.load_filter_options()
        self.load_entries()

    def create_ui(self):
        # --- Фильтры ---
        filt = ttk.LabelFrame(self, text="Фильтры", padding=10)
        filt.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(filt, text="Пользователь:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.user_var = tk.StringVar(value="Все")
        self.user_cb = ttk.Combobox(filt, textvariable=self.user_var, width=28, state="readonly")
        self.user_cb.grid(row=0, column=1, padx=(0, 15))

        ttk.Label(filt, text="Действие:").grid(row=0, column=2, sticky="w", padx=(0, 5))
        self.action_var = tk.StringVar(value="Все")
        self.action_cb = ttk.Combobox(filt, textvariable=self.action_var, width=28, state="readonly")
        self.action_cb.grid(row=0, column=3, padx=(0, 15))

        ttk.Label(filt, text="С даты:").grid(row=0, column=4, sticky="w", padx=(0, 5))
        self.date_from_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.date_from_var, width=12).grid(row=0, column=5, padx=(0, 15))

        ttk.Label(filt, text="По дату:").grid(row=0, column=6, sticky="w", padx=(0, 5))
        self.date_to_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.date_to_var, width=12).grid(row=0, column=7, padx=(0, 15))

        ttk.Button(filt, text="Применить", command=self.load_entries).grid(row=0, column=8, padx=4)
        ttk.Button(filt, text="Сбросить", command=self.reset_filters).grid(row=0, column=9, padx=4)

        ttk.Label(
            filt,
            text="Даты в формате ГГГГ-ММ-ДД. Обе границы включительные.",
            foreground="gray"
        ).grid(row=1, column=0, columnspan=10, sticky="w", pady=(8, 0))

        # --- Таблица ---
        columns = ("created_at", "display_name", "action", "entity", "entity_id", "details")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)

        self.tree.heading("created_at", text="Дата и время")
        self.tree.heading("display_name", text="Пользователь")
        self.tree.heading("action", text="Действие")
        self.tree.heading("entity", text="Объект")
        self.tree.heading("entity_id", text="ID")
        self.tree.heading("details", text="Подробности")

        self.tree.column("created_at", width=150, anchor="w")
        self.tree.column("display_name", width=180, anchor="w")
        self.tree.column("action", width=160, anchor="w")
        self.tree.column("entity", width=100, anchor="center")
        self.tree.column("entity_id", width=70, anchor="center")
        self.tree.column("details", width=400, anchor="w", stretch=True)

        # --- Кнопки ---
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(5, 10))

        ttk.Button(bottom, text="Экспорт в Excel", command=self.export_excel).pack(side="left", padx=4)
        ttk.Button(bottom, text="Обновить", command=self.load_entries).pack(side="left", padx=4)
        ttk.Button(bottom, text="Закрыть", command=self.destroy).pack(side="right", padx=4)

        self.count_label = ttk.Label(bottom, text="", foreground="gray")
        self.count_label.pack(side="right", padx=15)

    # ---------------- ФИЛЬТРЫ ----------------

    def load_filter_options(self):
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, display_name, username FROM users ORDER BY LOWER(display_name)")
            self.users_map = {"Все": None}
            for uid, dname, uname in cur.fetchall():
                label = f"{dname} ({uname})" if dname != uname else dname
                self.users_map[label] = uid
            self.user_cb["values"] = list(self.users_map.keys())

            actions = get_distinct_audit_actions(conn)
            self.actions_map = {"Все": None}
            for a in actions:
                self.actions_map[a] = a
            self.action_cb["values"] = list(self.actions_map.keys())
        finally:
            conn.close()

    def reset_filters(self):
        self.user_var.set("Все")
        self.action_var.set("Все")
        self.date_from_var.set("")
        self.date_to_var.set("")
        self.load_entries()

    # ---------------- ЗАГРУЗКА ----------------

    def load_entries(self):
        self.tree.delete(*self.tree.get_children())

        user_id = self.users_map.get(self.user_var.get())
        action = self.actions_map.get(self.action_var.get())

        date_from = self.date_from_var.get().strip() or None
        date_to = self.date_to_var.get().strip() or None

        # Валидация дат — если пользователь ввёл мусор, лучше сказать
        # сразу, чем получить непонятную пустую таблицу.
        for label, value in (("С даты", date_from), ("По дату", date_to)):
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror(
                        "Ошибка",
                        f"Поле «{label}»: неверный формат «{value}».\n"
                        "Ожидается ГГГГ-ММ-ДД, например 2026-09-30.",
                        parent=self
                    )
                    return

        conn = get_conn()
        try:
            total = count_audit_log(
                conn, user_id=user_id, action=action,
                date_from=date_from, date_to=date_to
            )
            rows = query_audit_log(
                conn, user_id=user_id, action=action,
                date_from=date_from, date_to=date_to,
                limit=self.DEFAULT_LIMIT
            )
        finally:
            conn.close()

        for (rid, created_at, display_name, username,
             action_name, entity, entity_id, details) in rows:
            self.tree.insert(
                "", "end",
                values=(
                    created_at or "",
                    display_name or username or "—",
                    action_name,
                    entity or "",
                    entity_id if entity_id is not None else "",
                    details or "",
                )
            )

        if total > len(rows):
            self.count_label.config(
                text=f"Показано: {len(rows)} из {total} (уточните фильтры)"
            )
        else:
            self.count_label.config(text=f"Записей: {total}")

    # ---------------- ЭКСПОРТ ----------------

    def export_excel(self):
        file_path = filedialog.asksaveasfilename(
            title="Сохранить журнал действий",
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            initialfile=f"Журнал_действий_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        )
        if not file_path:
            return

        user_id = self.users_map.get(self.user_var.get())
        action = self.actions_map.get(self.action_var.get())
        date_from = self.date_from_var.get().strip() or None
        date_to = self.date_to_var.get().strip() or None

        conn = get_conn()
        try:
            rows = query_audit_log(
                conn, user_id=user_id, action=action,
                date_from=date_from, date_to=date_to,
                limit=100_000
            )
        finally:
            conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "Audit"

        ws.append([
            "Дата и время", "Пользователь", "Логин", "Действие",
            "Объект", "ID объекта", "Подробности"
        ])

        for (rid, created_at, display_name, username,
             action_name, entity, entity_id, details) in rows:
            ws.append([
                created_at or "",
                display_name or "",
                username or "",
                action_name,
                entity or "",
                entity_id if entity_id is not None else "",
                details or "",
            ])

        wb.save(file_path)

        # Отметим сам факт экспорта в журнале (для аудита).
        conn = get_conn()
        try:
            log_audit(
                conn,
                self.current_user["id"], self.current_user["username"],
                self.current_user["display_name"],
                "export_audit_log",
                details=f"Файл: {file_path}; строк: {len(rows)}"
            )
            conn.commit()
        finally:
            conn.close()

        messagebox.showinfo("Готово", f"Журнал сохранён:\n{file_path}\n\nСтрок: {len(rows)}", parent=self)