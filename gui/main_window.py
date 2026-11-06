import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, timedelta
import os
import time
import ctypes
from openpyxl import Workbook, load_workbook
from db import (
    get_conn, backup_database, soft_delete_assets, log_audit,
    normalize_for_search,
)
from auth import ChangePasswordDialog
from gui.asset_card import AssetCard
from gui.dictionaries_window import DictionariesWindow
from gui.filter_window import FilterWindow
from gui.removed_assets_window import RemovedAssetsWindow
from settings import load_settings, save_settings, get_printer, set_printer, get_sort, set_sort
import win32print
import win32ui
from PIL import Image, ImageWin, ImageDraw, ImageFont
import barcode
from barcode.writer import ImageWriter
import tempfile


EXPORT_IMPORT_COLUMNS = [
    ("ID",                     "id",          False, True),
    ("Инв. номер",             "code",        True,  False),
    ("Название",               "name",        True,  False),
    ("Категория",              "category",    False, False),
    ("Место",                  "location",    False, False),
    ("Ответственный",          "employee",    False, False),
    ("Серийный номер",         "serial",      False, False),
    ("Примечание",             "note",        False, False),
    ("Дата покупки",           "buy_date",    False, False),
    ("Стоимость",              "price",       False, False),
    ("Остаточная стоимость",   "residual",    False, False),
    ("Последнее сканирование", "last_seen",   False, False),
]


SORTABLE_COLUMNS = ("id", "code", "name", "buy_date", "category", "location", "employee", "last_seen")


# Префикс, отличающий метку кабинета от обычного кода актива.
# При сканировании такого кода программа не ищет актив, а
# автоматически ставит фильтр на указанный кабинет.
LOCATION_CODE_PREFIX = "LOC-"


COLOR_BG = "#f5f6f8"
COLOR_HEADER = "#2f4f6f"
COLOR_HEADER_TEXT = "#ffffff"
COLOR_HEADER_MUTED = "#c7d3e0"
COLOR_CARD = "#ffffff"
COLOR_BORDER = "#d5dbe2"
COLOR_ACCENT = "#4a7ba8"
COLOR_TEXT = "#2f4f6f"
COLOR_MUTED = "#666666"

COLOR_ROW_ODD = "#ffffff"
COLOR_ROW_EVEN = "#e9eef3"

COLOR_LAST_SCANNED = "#b8e6b8"

CURRENCY_SUFFIX = " TMT"


SCANNER_VK_MAP = {}
for _vk in range(0x30, 0x3A):
    SCANNER_VK_MAP[_vk] = chr(_vk)
for _vk in range(0x60, 0x6A):
    SCANNER_VK_MAP[_vk] = chr(_vk - 0x60 + ord("0"))
SCANNER_VK_MAP[0xBD] = "-"
SCANNER_VK_MAP[0x6D] = "-"
SCANNER_VK_MAP[0xBE] = "."
SCANNER_VK_MAP[0x6E] = "."
SCANNER_VK_MAP[0xBC] = ","
SCANNER_VK_MAP[0xBF] = "/"
SCANNER_VK_MAP[0xDB] = "["
SCANNER_VK_MAP[0xDD] = "]"
SCANNER_VK_MAP[0xBA] = ";"
SCANNER_VK_MAP[0xDE] = "'"


def scanner_char_from_event(event):
    vk = event.keycode
    if 0x41 <= vk <= 0x5A:
        shift_pressed = bool(event.state & 0x0001)
        caps_lock_on = bool(event.state & 0x0002)
        is_upper = shift_pressed ^ caps_lock_on
        letter = chr(vk)
        return letter if is_upper else letter.lower()
    return SCANNER_VK_MAP.get(vk)


class MainWindow(tk.Tk):
    def __init__(self, current_user):
        self.current_user = current_user

        self._enable_windows_dpi_awareness()

        super().__init__()

        self.configure(bg=COLOR_BG)

        self._setup_style()

        role = " · администратор" if self.current_user.get("is_admin") else ""
        self.title(
            f"Склад — Основные средства · "
            f"{self.current_user['display_name']}{role}"
        )

        try:
            self.state("zoomed")
        except tk.TclError:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            self.geometry(f"{screen_w}x{screen_h}+0+0")

        self.minsize(900, 500)

        self.scan_buffer = ""
        self.last_scan_time = 0

        # Активный фильтр, который сейчас применён к таблице.
        self.current_filters = None

        self.load_settings()

        saved_column, saved_direction = get_sort()
        self.sort_column = saved_column if saved_column in SORTABLE_COLUMNS else "id"
        self.sort_direction = saved_direction if saved_direction in ("asc", "desc") else "desc"

        self.create_menu()
        self.create_ui()
        self.load_assets()
        self.bind_all("<Key>", self.on_key_scan)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- DPI ----------------
    @staticmethod
    def _enable_windows_dpi_awareness():
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # ---------------- STYLE ----------------
    def _setup_style(self):
        style = ttk.Style(self)

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Treeview.Heading",
            background=COLOR_HEADER,
            foreground="white",
            font=("", 10, "bold"),
            relief="flat",
            padding=(6, 8),
            borderwidth=0,
        )
        style.map(
            "Treeview.Heading",
            background=[("active", COLOR_ACCENT)],
            foreground=[("active", "white")],
        )

        style.configure(
            "Treeview",
            rowheight=26,
            font=("", 10),
            fieldbackground="white",
            background="white",
        )

        style.configure("Toolbar.TButton", padding=(12, 6), font=("", 10))
        style.configure("Accent.TButton", padding=(12, 6), font=("", 10, "bold"))

    # ---------------- SETTINGS ----------------
    def load_settings(self):
        self.settings = load_settings()

    def save_settings(self):
        save_settings(self.settings)

    # ---------------- MENU ----------------
    def create_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Файл", menu=file_menu)
        file_menu.add_command(label="Экспорт в Excel", command=self.export_excel)
        file_menu.add_command(label="Импорт из Excel", command=self.import_excel)
        file_menu.add_command(
            label="Импорт из бухгалтерии (амортизация)",
            command=self.import_accounting_excel
        )
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.logout)

        dict_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Справочники", menu=dict_menu)
        dict_menu.add_command(
            label="Открыть справочники",
            command=self.open_dictionaries
        )

        report_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Отчёты", menu=report_menu)
        report_menu.add_command(
            label="Дашборд",
            command=self.open_dashboard
        )
        report_menu.add_separator()
        report_menu.add_command(
            label="Печать акта инвентаризации",
            command=self.print_inventory_act
        )
        report_menu.add_command(
            label="Журнал списанных активов",
            command=self.show_removed_assets_journal
        )
        report_menu.add_separator()
        report_menu.add_command(label="Фильтры", command=self.open_filters)

        service_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Сервис", menu=service_menu)
        service_menu.add_command(
            label="Напечатать метку кабинета",
            command=self.print_location_label
        )
        service_menu.add_command(
            label="Корзина (удалённые карточки)",
            command=self.open_trash
        )
        if self.current_user.get("is_admin"):
            service_menu.add_separator()
            service_menu.add_command(
                label="Сбросить отметки сканирования (перед инвентаризацией)",
                command=self.reset_all_last_seen
            )

        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Настройки", menu=settings_menu)
        settings_menu.add_command(
            label="Название филиала",
            command=self.change_branch_name
        )
        settings_menu.add_separator()
        settings_menu.add_command(
            label="Сохранить размеры колонок",
            command=self.save_column_sizes
        )
        settings_menu.add_command(
            label="Выбрать принтер штрих-кодов",
            command=self.choose_printer
        )
        if self.current_user.get("auth_type") == "local":
            settings_menu.add_separator()
            settings_menu.add_command(
                label="Сменить мой пароль",
                command=self.change_own_password
            )

        if self.current_user.get("is_admin"):
            admin_menu = tk.Menu(menubar, tearoff=0)
            menubar.add_cascade(label="Администрирование", menu=admin_menu)
            admin_menu.add_command(
                label="Пользователи",
                command=self.open_users_admin
            )
            admin_menu.add_command(
                label="Журнал действий",
                command=self.open_audit_log
            )

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Помощь", menu=help_menu)
        help_menu.add_command(label="О программе", command=self.show_about)

    # ---------------- UI ----------------

    def create_ui(self):
        self._build_header()
        self._build_toolbar()
        self._build_statusbar()
        self._build_table()

    def _build_header(self):
        header = tk.Frame(self, bg=COLOR_HEADER, height=76)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=COLOR_HEADER)
        left.pack(side="left", padx=20, pady=10, anchor="w")

        tk.Label(
            left,
            text="HalkAssets",
            bg=COLOR_HEADER,
            fg=COLOR_HEADER_TEXT,
            font=("", 18, "bold"),
        ).pack(anchor="w")

        tk.Label(
            left,
            text="Система учёта основных средств",
            bg=COLOR_HEADER,
            fg=COLOR_HEADER_MUTED,
            font=("", 10),
        ).pack(anchor="w", pady=(1, 0))

        right = tk.Frame(header, bg=COLOR_HEADER)
        right.pack(side="right", padx=20, pady=10, anchor="e")

        role_text = "Администратор" if self.current_user.get("is_admin") else "Пользователь"
        auth_text = "AD" if self.current_user.get("auth_type") == "ad" else "Локальный вход"

        tk.Label(
            right,
            text=self.current_user["display_name"],
            bg=COLOR_HEADER,
            fg=COLOR_HEADER_TEXT,
            font=("", 12, "bold"),
            anchor="e",
        ).pack(anchor="e")

        tk.Label(
            right,
            text=f"{role_text} · {auth_text}",
            bg=COLOR_HEADER,
            fg=COLOR_HEADER_MUTED,
            font=("", 9),
            anchor="e",
        ).pack(anchor="e", pady=(1, 0))

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=COLOR_BG)
        bar.pack(fill="x", side="top", padx=15, pady=(12, 4))

        actions = tk.Frame(bar, bg=COLOR_BG)
        actions.pack(side="left")

        ttk.Button(
            actions, text="➕  Добавить",
            style="Accent.TButton", command=self.add_asset
        ).pack(side="left", padx=(0, 5))

        ttk.Button(
            actions, text="🔄  Обновить",
            style="Toolbar.TButton",
            command=lambda: self.load_assets()
        ).pack(side="left", padx=5)

        ttk.Button(
            actions, text="🔀  Переприкрепить",
            style="Toolbar.TButton", command=self.bulk_reassign
        ).pack(side="left", padx=5)

        ttk.Button(
            actions, text="🗑  Удалить",
            style="Toolbar.TButton", command=self.delete_selected_assets
        ).pack(side="left", padx=5)

        ttk.Button(
            bar, text="🖨  Штрих-код",
            style="Toolbar.TButton", command=self.print_selected_barcode
        ).pack(side="right", padx=(5, 0))

        search_box = tk.Frame(
            bar, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
        )
        search_box.pack(side="right", padx=(0, 10))

        tk.Label(
            search_box, text="🔍",
            bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 11),
        ).pack(side="left", padx=(10, 4), pady=5)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.on_search)
        self.search_entry = tk.Entry(
            search_box,
            textvariable=self.search_var,
            width=32,
            relief="flat",
            bg=COLOR_CARD,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            font=("", 10),
        )
        self.search_entry.pack(side="left", pady=5)

        self.clear_btn = tk.Label(
            search_box, text="✖",
            bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 10),
            cursor="hand2",
        )
        self.clear_btn.pack(side="left", padx=(4, 10))
        self.clear_btn.bind("<Button-1>", lambda e: self.clear_search())
        self.clear_btn.bind("<Enter>", lambda e: self.clear_btn.config(fg=COLOR_TEXT))
        self.clear_btn.bind("<Leave>", lambda e: self.clear_btn.config(fg=COLOR_MUTED))

    def _build_statusbar(self):
        status = tk.Frame(self, bg=COLOR_BG)
        status.pack(fill="x", side="bottom")
        ttk.Separator(status, orient="horizontal").pack(fill="x")

        inner = tk.Frame(status, bg=COLOR_BG, height=32)
        inner.pack(fill="x")
        inner.pack_propagate(False)

        self.status_label = tk.Label(
            inner, text="", anchor="w",
            bg=COLOR_BG, fg=COLOR_MUTED, font=("", 9),
        )
        self.status_label.pack(side="left", padx=15)

        self.user_label = tk.Label(
            inner, text="", anchor="e",
            bg=COLOR_BG, fg=COLOR_MUTED, font=("", 9),
        )
        self.user_label.pack(side="right", padx=15)

    def _build_table(self):
        wrapper = tk.Frame(
            self, bg=COLOR_CARD,
            highlightbackground=COLOR_BORDER,
            highlightthickness=1,
        )
        wrapper.pack(fill="both", expand=True, padx=15, pady=(4, 10))

        columns = ("id", "code", "name", "buy_date", "category",
                   "location", "employee", "last_seen")
        # selectmode="extended" — чтобы можно было выделять несколько
        # строк сразу (Ctrl+клик / Shift+клик). Нужно для массового
        # переприкрепления и удаления.
        self.tree = ttk.Treeview(
            wrapper, columns=columns, show="headings",
            selectmode="extended"
        )
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.column_headers = {
            "id": "ID",
            "code": "Инв №",
            "name": "Название",
            "buy_date": "Дата покупки",
            "category": "Категория",
            "location": "Место",
            "employee": "Ответственный",
            "last_seen": "Последнее сканирование"
        }

        self.tree.tag_configure(
            "last_scanned",
            background=COLOR_LAST_SCANNED,
            font=("", 10, "bold"),
        )

        self.tree.tag_configure("oddrow", background=COLOR_ROW_ODD)
        self.tree.tag_configure("evenrow", background=COLOR_ROW_EVEN)

        for col in columns:
            default_width = {
                "id": 60,
                "code": 130,
                "name": 300,
                "buy_date": 110,
                "category": 150,
                "location": 150,
                "employee": 180,
                "last_seen": 170,
            }.get(col, 120)
            width = self.settings.get("columns", {}).get(col, default_width)
            self.tree.column(col, width=width, stretch=(col == columns[-1]))

        self.refresh_column_headers()

        self.tree.bind("<Double-1>", self.open_asset)
        self.tree.bind("<ButtonRelease-1>", self.save_column_sizes)
        self.tree.bind("<Button-3>", self._show_context_menu)

    # ---------------- CONTEXT MENU ----------------
    def _show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item and item not in self.tree.selection():
            self.tree.selection_set(item)

        selection = self.tree.selection()
        if not selection:
            return

        menu = tk.Menu(self, tearoff=0)

        if len(selection) == 1:
            menu.add_command(
                label="Открыть карточку",
                command=lambda: self.open_asset(None)
            )
            menu.add_command(
                label="Печать штрих-кода",
                command=self.print_selected_barcode
            )
            menu.add_separator()

        menu.add_command(
            label=f"Переприкрепить выбранное ({len(selection)})",
            command=self.bulk_reassign
        )
        menu.add_command(
            label=f"Удалить в корзину ({len(selection)})",
            command=self.delete_selected_assets
        )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ---------------- SORTING ----------------
    def refresh_column_headers(self):
        for col in self.tree["columns"]:
            text = self.column_headers[col]
            if col == self.sort_column:
                arrow = " ▼" if self.sort_direction == "desc" else " ▲"
                text += arrow
            self.tree.heading(col, text=text, command=lambda c=col: self.on_sort_column(c))

    def on_sort_column(self, col):
        if self.sort_column == col:
            self.sort_direction = "asc" if self.sort_direction == "desc" else "desc"
        else:
            self.sort_column = col
            self.sort_direction = "desc"

        set_sort(self.sort_column, self.sort_direction)
        self.refresh_column_headers()
        self.load_assets(self.search_var.get().strip())

    # ---------------- DATA ----------------
    def load_assets(self, search_text="", filters=None, highlight_code=None):
        if filters is None:
            filters = self.current_filters

        self.tree.delete(*self.tree.get_children())
        conn = get_conn()
        cur = conn.cursor()

        where_parts = ["1=1"]
        params = []

        if search_text:
            search = f"%{normalize_for_search(search_text)}%"
            where_parts.append(
                "(normalize_search(code) LIKE ? "
                "OR normalize_search(name) LIKE ? "
                "OR normalize_search(category) LIKE ? "
                "OR normalize_search(location) LIKE ? "
                "OR normalize_search(employee) LIKE ? "
                "OR normalize_search(serial) LIKE ? "
                "OR normalize_search(note) LIKE ?)"
            )
            params.extend([search] * 7)

        if filters:
            if filters.get("category"):
                where_parts.append("category=?")
                params.append(filters["category"])
            if filters.get("location"):
                where_parts.append("location=?")
                params.append(filters["location"])
            if filters.get("employee"):
                where_parts.append("employee=?")
                params.append(filters["employee"])

            not_scanned = filters.get("not_scanned")
            if not_scanned == "never":
                where_parts.append("(last_seen IS NULL OR last_seen = '')")
            elif isinstance(not_scanned, int):
                cutoff = (datetime.now() - timedelta(days=not_scanned)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                where_parts.append(
                    "(last_seen IS NULL OR last_seen = '' OR last_seen < ?)"
                )
                params.append(cutoff)

        where_sql = " AND ".join(where_parts)

        cur.execute("SELECT COUNT(*), COALESCE(SUM(residual), 0) FROM assets")
        total_count, total_sum = cur.fetchone()

        cur.execute(
            f"SELECT COUNT(*), COALESCE(SUM(residual), 0) FROM assets WHERE {where_sql}",
            params
        )
        filtered_count, filtered_sum = cur.fetchone()

        sort_column = self.sort_column if self.sort_column in SORTABLE_COLUMNS else "id"
        sort_direction = "ASC" if self.sort_direction == "asc" else "DESC"
        query = (
            f"SELECT id, code, name, buy_date, category, location, employee, last_seen "
            f"FROM assets WHERE {where_sql} ORDER BY {sort_column} {sort_direction}"
        )
        cur.execute(query, params)

        highlight_item = None
        for idx, row in enumerate(cur.fetchall()):
            code_value = row[1]

            if highlight_code is not None and str(code_value) == str(highlight_code):
                tags = ("last_scanned",)
                item_id = self.tree.insert("", "end", values=row, tags=tags)
                highlight_item = item_id
            else:
                tags = ("oddrow",) if idx % 2 else ("evenrow",)
                self.tree.insert("", "end", values=row, tags=tags)

        conn.close()

        if highlight_item:
            self.tree.see(highlight_item)

        self._update_status(total_count, total_sum, filtered_count, filtered_sum, filters)

    @staticmethod
    def _format_money(value):
        try:
            value = float(value or 0)
        except (TypeError, ValueError):
            value = 0.0
        return f"{value:,.2f}".replace(",", " ").replace(".", ",") + CURRENCY_SUFFIX

    def _update_status(self, total_count, total_sum, filtered_count, filtered_sum, filters=None):
        if not hasattr(self, "status_label"):
            return

        now_str = datetime.now().strftime("%H:%M:%S")

        filter_parts = []
        if filters:
            if filters.get("location"):
                filter_parts.append(f"место: {filters['location']}")
            if filters.get("employee"):
                filter_parts.append(f"ответственный: {filters['employee']}")
            if filters.get("category"):
                filter_parts.append(f"категория: {filters['category']}")
            ns = filters.get("not_scanned")
            if ns == "never":
                filter_parts.append("никогда не сканировалось")
            elif isinstance(ns, int):
                filter_parts.append(f"не сканировалось > {ns} дней")

        if filtered_count == total_count:
            left = (
                f"Записей: {total_count}   ·   "
                f"Сумма остаточной стоимости: {self._format_money(total_sum)}"
            )
        else:
            left = (
                f"Показано: {filtered_count} из {total_count}   ·   "
                f"Остаточная (по фильтру): {self._format_money(filtered_sum)}"
            )
            if filter_parts:
                left += "   ·   фильтр: " + "; ".join(filter_parts)

        self.status_label.config(text=left)

        role = "админ" if self.current_user.get("is_admin") else "пользователь"
        self.user_label.config(
            text=f"{self.current_user['display_name']} ({role})   ·   обновлено в {now_str}"
        )

    # ---------------- SEARCH ----------------
    def on_search(self, *args):
        self.load_assets(self.search_var.get().strip())

    def clear_search(self):
        self.search_var.set("")
        self.current_filters = None
        self.search_entry.focus_set()
        self.load_assets()

    # ---------------- COLUMN ----------------
    def save_column_sizes(self, event=None):
        if "columns" not in self.settings:
            self.settings["columns"] = {}
        for col in self.tree["columns"]:
            self.settings["columns"][col] = self.tree.column(col)["width"]
        self.save_settings()

    # ---------------- CARD ----------------
    def add_asset(self):
        AssetCard(self, refresh_callback=self.load_assets)
        self.log_action("open_new_card")

    def open_asset(self, event):
        item = self.tree.focus()
        if not item:
            return

        values = self.tree.item(item)["values"]
        asset_id = values[0]

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM assets WHERE id=?", (asset_id,))
        row = cur.fetchone()
        conn.close()

        if row:
            columns = ["id","code","name","category","location","employee","serial","note","buy_date","price","residual","last_seen"]
            asset = dict(zip(columns, row))
            AssetCard(self, asset, self.load_assets)

    # ---------------- BULK REASSIGN ----------------

    def bulk_reassign(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning(
                "Переприкрепить",
                "Сначала выделите одну или несколько строк в таблице.\n\n"
                "Несколько строк можно выделить через Ctrl+клик или Shift+клик."
            )
            return

        codes = []
        for i in selection:
            value = self.tree.item(i)["values"][1]
            if value is None:
                continue
            code = str(value).strip()
            if code:
                codes.append(code)

        if not codes:
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM locations ORDER BY name")
        locations = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT name FROM employees ORDER BY name")
        employees = [r[0] for r in cur.fetchall()]
        conn.close()

        win = tk.Toplevel(self)
        win.title(f"Переприкрепить: {len(codes)} карточк(и/ек)")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=(
                f"Будет изменено карточек: {len(codes)}.\n"
                "Отметьте галочками, что именно нужно изменить,\n"
                "и выберите новые значения из списков."
            ),
            justify="left",
            foreground="gray"
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 15))

        change_location_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame, text="Изменить место",
            variable=change_location_var
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        location_var = tk.StringVar(value="")
        location_cb = ttk.Combobox(
            frame, values=[""] + locations,
            textvariable=location_var, state="readonly", width=36
        )
        location_cb.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        change_employee_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame, text="Изменить ответственного",
            variable=change_employee_var
        ).grid(row=3, column=0, sticky="w", pady=(4, 0))
        employee_var = tk.StringVar(value="")
        employee_cb = ttk.Combobox(
            frame, values=[""] + employees,
            textvariable=employee_var, state="readonly", width=36
        )
        employee_cb.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        btn = ttk.Frame(frame)
        btn.grid(row=5, column=0, columnspan=2, pady=(10, 0))

        def on_apply():
            do_location = bool(change_location_var.get())
            do_employee = bool(change_employee_var.get())

            new_location = location_var.get().strip() if do_location else None
            new_employee = employee_var.get().strip() if do_employee else None

            if not do_location and not do_employee:
                messagebox.showinfo(
                    "Переприкрепить",
                    "Не выбрано ни одного действия.",
                    parent=win
                )
                return

            if do_location and not new_location:
                messagebox.showwarning(
                    "Переприкрепить",
                    "Отмечено «Изменить место», но не выбрано новое место.",
                    parent=win
                )
                return

            if do_employee and not new_employee:
                messagebox.showwarning(
                    "Переприкрепить",
                    "Отмечено «Изменить ответственного», но не выбран новый.",
                    parent=win
                )
                return

            win.destroy()
            self._do_bulk_reassign(codes, new_location, new_employee)

        ttk.Button(btn, text="Применить", command=on_apply).pack(side="left", padx=5)
        ttk.Button(btn, text="Отмена", command=win.destroy).pack(side="left", padx=5)

        win.bind("<Return>", lambda e: on_apply())

    def _do_bulk_reassign(self, codes, new_location, new_employee):
        if not codes:
            return

        conn = get_conn()
        cur = conn.cursor()

        placeholders = ",".join("?" * len(codes))
        cur.execute(
            f"""SELECT id, code, location, employee
                FROM assets WHERE code IN ({placeholders})""",
            codes
        )
        rows = cur.fetchall()

        if not rows:
            conn.close()
            messagebox.showinfo("Переприкрепить", "Ничего не найдено.")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if new_location:
            cur.execute(
                "INSERT OR IGNORE INTO locations(name) VALUES(?)",
                (new_location,)
            )
        if new_employee:
            cur.execute(
                "INSERT OR IGNORE INTO employees(name) VALUES(?)",
                (new_employee,)
            )

        updated = 0

        try:
            for asset_id, code, old_location, old_employee in rows:
                final_location = new_location if new_location is not None else (old_location or "")
                final_employee = new_employee if new_employee is not None else (old_employee or "")

                if (old_location or "") == final_location and (old_employee or "") == final_employee:
                    continue

                if (old_location or "") != final_location or (old_employee or "") != final_employee:
                    cur.execute("""
                        INSERT INTO movement_history(
                            asset_id, old_location, new_location,
                            old_employee, new_employee, changed_at
                        )
                        VALUES (?,?,?,?,?,?)
                    """, (
                        asset_id,
                        old_location or "", final_location,
                        old_employee or "", final_employee,
                        now
                    ))

                cur.execute("""
                    UPDATE assets
                    SET location=?, employee=?
                    WHERE id=?
                """, (final_location, final_employee, asset_id))

                updated += 1

            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            messagebox.showerror(
                "Ошибка переприкрепления",
                f"Не удалось выполнить операцию:\n{e}\n\n"
                "Изменения не применены."
            )
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

        parts = []
        if new_location is not None:
            parts.append(f"место → «{new_location}»")
        if new_employee is not None:
            parts.append(f"ответственный → «{new_employee}»")

        self.log_action(
            "bulk_reassign",
            entity="assets",
            details=(
                f"Кодов: {len(codes)}; обновлено: {updated}; "
                + "; ".join(parts)
            )
        )

        self.load_assets()

        messagebox.showinfo(
            "Переприкрепление завершено",
            f"Обновлено карточек: {updated}.\n\n"
            + "\n".join(parts)
        )

    # ---------------- SCANNER ----------------
    SCAN_MAX_KEY_INTERVAL = 0.4

    def on_key_scan(self, event):
        if event.keysym in ("Shift_L","Shift_R","Control_L","Control_R","Alt_L","Alt_R"):
            return

        if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text)):
            self.scan_buffer = ""
            return

        now = time.monotonic()
        if self.scan_buffer and (now - self.last_scan_time) > self.SCAN_MAX_KEY_INTERVAL:
            self.scan_buffer = ""
        self.last_scan_time = now

        if event.keysym == "Return":
            code = self.scan_buffer.strip()
            self.scan_buffer = ""
            if not code:
                return

            # Метка кабинета
            if code.upper().startswith(LOCATION_CODE_PREFIX.upper()):
                self._apply_location_scan(code)
                return

            # Обычный код актива
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT id FROM assets WHERE code=?", (code,))
            row = cur.fetchone()

            if row:
                cur.execute("UPDATE assets SET last_seen=? WHERE code=?",
                            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), code))
                conn.commit()
                conn.close()
                self.load_assets(self.search_var.get().strip(), highlight_code=code)
            else:
                conn.close()
                AssetCard(self, {"code": code}, self.load_assets)
            return

        scan_char = scanner_char_from_event(event)
        if scan_char:
            self.scan_buffer += scan_char

    def _apply_location_scan(self, code):
        """
        Обрабатывает сканирование метки кабинета.
        Ожидаемый формат: «LOC-<id>», например «LOC-5».
        """
        raw = code[len(LOCATION_CODE_PREFIX):].strip()

        try:
            loc_id = int(raw)
        except ValueError:
            messagebox.showwarning(
                "Метка кабинета",
                f"Не удалось разобрать ID кабинета из метки «{code}».\n\n"
                f"Ожидается формат LOC-<номер>, например LOC-5."
            )
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM locations WHERE id=?", (loc_id,))
        row = cur.fetchone()
        conn.close()

        if row is None:
            messagebox.showwarning(
                "Метка кабинета",
                f"Кабинет с ID={loc_id} не найден в справочнике.\n\n"
                f"Возможно, кабинет был удалён — тогда метку нужно\n"
                f"перепечатать (Сервис → Напечатать метку кабинета)."
            )
            return

        location_name = row[0]

        self.current_filters = {
            "category": None,
            "location": location_name,
            "employee": None,
            "not_scanned": None,
        }
        self.load_assets()

        self.log_action(
            "scan_location_label",
            entity="locations",
            entity_id=loc_id,
            details=f"Кабинет: {location_name}"
        )

    # ---------------- LOCATION LABEL PRINT ----------------

    def print_location_label(self):
        """
        Печать метки кабинета для наклейки на дверь.

        ВАЖНО: Code128 умеет кодировать ТОЛЬКО латиницу, цифры и
        обычные знаки. Русские буквы, символ «№», «ёлочки» и т.п.
        он не поддерживает — попытка закодировать такое имя падает.

        Поэтому в сам штрих-код кладём ASCII-код LOC-<id кабинета>
        (например «LOC-5»), а человеческое название кабинета
        печатаем крупным текстом под штрих-кодом.
        """
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM locations ORDER BY name")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            messagebox.showinfo(
                "Метка кабинета",
                "В справочнике пока нет ни одного кабинета.\n"
                "Добавьте хотя бы один через Справочники → Места."
            )
            return

        labels = [f"{name}  →  LOC-{loc_id}" for loc_id, name in rows]
        id_by_label = {f"{name}  →  LOC-{loc_id}": (loc_id, name)
                       for loc_id, name in rows}

        win = tk.Toplevel(self)
        win.title("Метка кабинета")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=(
                "Выберите кабинет — на принтер уйдёт этикетка с кодом\n"
                "вида LOC-<номер> для наклейки на дверь.\n\n"
                "Русское название кабинета печатается под штрих-кодом\n"
                "как текст: на двери будет видно, что это за кабинет.\n\n"
                "При сканировании метки программа автоматически поставит\n"
                "фильтр по выбранному кабинету."
            ),
            justify="left",
            foreground="gray"
        ).pack(anchor="w", pady=(0, 10))

        location_var = tk.StringVar(value=labels[0])
        ttk.Combobox(
            frame, values=labels,
            textvariable=location_var, state="readonly", width=48
        ).pack(fill="x")

        btn = ttk.Frame(frame)
        btn.pack(pady=(15, 0))

        def do_print():
            chosen = location_var.get().strip()
            if not chosen:
                return

            loc_id, loc_name = id_by_label.get(chosen, (None, None))
            if loc_id is None:
                return

            barcode_code = f"{LOCATION_CODE_PREFIX}{loc_id}"
            win.destroy()

            try:
                self.print_barcode_direct(barcode_code, loc_name, big=True)
            except Exception as e:
                messagebox.showerror(
                    "Ошибка печати метки",
                    f"Не удалось напечатать метку:\n{e}"
                )
                return

            self.log_action(
                "print_location_label",
                entity="locations",
                entity_id=loc_id,
                details=f"Кабинет: {loc_name}; код метки: {barcode_code}"
            )

        ttk.Button(btn, text="Напечатать", command=do_print).pack(side="left", padx=5)
        ttk.Button(btn, text="Отмена", command=win.destroy).pack(side="left", padx=5)

        win.bind("<Return>", lambda e: do_print())

    # ---------------- PRINTER CHOICE ----------------
    def choose_printer(self):
        try:
            printers = [
                p[2] for p in win32print.EnumPrinters(
                    win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
                )
            ]
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить список принтеров:\n{e}")
            return

        if not printers:
            messagebox.showwarning("Принтеры", "Не найдено ни одного установленного принтера")
            return

        win = tk.Toplevel(self)
        win.title("Выбор принтера")
        win.geometry("380x140")
        win.resizable(False, False)

        ttk.Label(win, text="Принтер для печати штрих-кодов:").pack(pady=(15, 5))

        current = get_printer()
        default_value = current if current in printers else printers[0]
        printer_var = tk.StringVar(value=default_value)

        ttk.Combobox(
            win, values=printers, textvariable=printer_var,
            state="readonly", width=42
        ).pack(padx=15)

        def save_choice():
            chosen = printer_var.get()
            if not chosen:
                return
            set_printer(chosen)
            self.settings["printer"] = chosen
            messagebox.showinfo("Готово", f"Принтер сохранён:\n{chosen}")
            win.destroy()

        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="Сохранить", command=save_choice).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Отмена", command=win.destroy).pack(side="left", padx=5)

    # ---------------- PRINT BARCODE ----------------
    def print_selected_barcode(self):
        item = self.tree.focus()
        if not item:
            messagebox.showwarning("Печать", "Выберите объект")
            return

        values = self.tree.item(item)["values"]
        code = str(values[1])
        name = str(values[2])
        self.print_barcode_direct(code, name)
        self.log_action("print_barcode", entity="assets", details=f"code={code}")

    def print_barcode_direct(self, code, name, big=False):
        """
        Печать штрих-кода на принтере этикеток.

        big=True — увеличенный режим для меток кабинетов: полосы шире,
        код и название крупнее.
        """
        printer_name = get_printer()
        if not printer_name:
            messagebox.showwarning(
                "Печать",
                "Принтер не выбран.\n"
                "Откройте меню Настройки → Выбрать принтер штрих-кодов."
            )
            return

        # Code128 работает только с ASCII. Если в code попали русские
        # буквы, символ «№», кавычки-ёлочки и т.п. — понятная ошибка
        # вместо тихого падения.
        if not all(ord(ch) < 128 for ch in str(code)):
            raise ValueError(
                f"Код «{code}» содержит не-ASCII символы (например, "
                f"русские буквы или символ «№»). Code128 их не "
                f"поддерживает. Используйте латиницу, цифры и обычные знаки."
            )

        LOGPIXELSX, LOGPIXELSY, HORZRES = 88, 90, 8
        try:
            probe_dc = win32ui.CreateDC()
            probe_dc.CreatePrinterDC(printer_name)
            printer_dpi = probe_dc.GetDeviceCaps(LOGPIXELSX) or probe_dc.GetDeviceCaps(LOGPIXELSY)
            page_width = probe_dc.GetDeviceCaps(HORZRES)
            probe_dc.DeleteDC()
            if not printer_dpi:
                raise ValueError("Принтер вернул нулевой DPI")
        except Exception:
            printer_dpi = 203
            page_width = None

        def mm_to_px(mm):
            return max(1, round(mm / 25.4 * printer_dpi))

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        temp_file.close()

        # big=True используется для меток кабинетов. Ширина полосы
        # (module_width) определяет общую длину штрих-кода по
        # горизонтали: чем шире полоса, тем длиннее код. Раньше было
        # 0.5 мм — код получался очень широкий и не влезал целиком в
        # поле зрения сканера. 0.3 мм — та же плотность, что у обычных
        # активов, зато высоту оставляем чуть больше актива, чтобы
        # издалека было видно.
        if big:
            module_height_mm = 12
            module_width_mm = 0.3
            code_font_mm = 4.5
            name_font_mm = 4.0
        else:
            module_height_mm = 10
            module_width_mm = 0.3
            code_font_mm = 3.2
            name_font_mm = 3.0

        barcode_obj = barcode.Code128(code, writer=ImageWriter())
        barcode_obj.write(temp_file.name, {
            "module_height": module_height_mm,
            "module_width": module_width_mm,
            "font_size": 0,
            "quiet_zone": 2,
            "dpi": printer_dpi,
        })

        barcode_img = Image.open(temp_file.name)
        bw, bh = barcode_img.size
        canvas_width = page_width if page_width else bw

        def load_font(mm_height):
            px = max(12, mm_to_px(mm_height))
            for fname in ("arialbd.ttf", "arial.ttf"):
                try:
                    return ImageFont.truetype(fname, px)
                except Exception:
                    continue
            return ImageFont.load_default()

        code_font = load_font(code_font_mm)
        name_font = load_font(name_font_mm)

        measure_img = Image.new("RGB", (10, 10))
        measure_draw = ImageDraw.Draw(measure_img)

        def text_width(text, font):
            try:
                return measure_draw.textlength(text, font=font)
            except AttributeError:
                return font.getsize(text)[0]

        def text_line_height(font):
            try:
                bbox = font.getbbox("Ągy")
                return bbox[3] - bbox[1]
            except AttributeError:
                return font.getsize("Ągy")[1]

        def wrap_text(text, font, max_width, max_lines):
            words = str(text).split()
            lines = []
            current = ""
            for w in words:
                trial = (current + " " + w).strip()
                if text_width(trial, font) <= max_width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = w
                    if len(lines) == max_lines - 1:
                        break
            if current:
                lines.append(current)

            used_words = sum(len(l.split()) for l in lines)
            if used_words < len(words):
                last = lines[-1]
                while last and text_width(last + "…", font) > max_width:
                    if " " not in last:
                        last = last[:-1]
                    else:
                        last = last.rsplit(" ", 1)[0]
                lines[-1] = (last + "…") if last else "…"
            return lines

        margin = mm_to_px(2)
        text_max_width = max(1, canvas_width - 2 * margin)

        code_lines = wrap_text(code, code_font, text_max_width, max_lines=1)
        name_lines = wrap_text(name, name_font, text_max_width, max_lines=3)

        line_gap = mm_to_px(0.8)
        block_gap = mm_to_px(1.2)
        code_line_h = text_line_height(code_font)
        name_line_h = text_line_height(name_font)

        text_block_height = (
            block_gap
            + len(code_lines) * code_line_h + (len(code_lines) - 1) * line_gap
            + block_gap
            + len(name_lines) * name_line_h + (len(name_lines) - 1) * line_gap
            + block_gap
        )

        canvas_height = bh + text_block_height
        new_img = Image.new("RGB", (canvas_width, canvas_height), "white")
        new_img.paste(barcode_img, (max(0, (canvas_width - bw) // 2), 0))

        draw = ImageDraw.Draw(new_img)
        y = bh + block_gap
        for line in code_lines:
            w = text_width(line, code_font)
            draw.text(((canvas_width - w) // 2, y), line, fill="black", font=code_font)
            y += code_line_h + line_gap

        y += block_gap - line_gap
        for line in name_lines:
            w = text_width(line, name_font)
            draw.text(((canvas_width - w) // 2, y), line, fill="black", font=name_font)
            y += name_line_h + line_gap

        new_img.save(temp_file.name)

        hprinter = win32print.OpenPrinter(printer_name)
        try:
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(printer_name)

            bmp = Image.open(temp_file.name)
            dib = ImageWin.Dib(bmp)

            actual_page_width = hdc.GetDeviceCaps(HORZRES)
            x_offset = max(0, (actual_page_width - bmp.width) // 2)

            hdc.StartDoc("Barcode Print")
            hdc.StartPage()
            dib.draw(
                hdc.GetHandleOutput(),
                (x_offset, 0, x_offset + bmp.width, bmp.height)
            )
            hdc.EndPage()
            hdc.EndDoc()
            hdc.DeleteDC()
        finally:
            win32print.ClosePrinter(hprinter)

        os.unlink(temp_file.name)

    # ---------------- DELETE ----------------
    def delete_selected_assets(self):
        items = self.tree.selection()
        if not items:
            messagebox.showwarning("Удаление", "Выберите объекты для удаления")
            return

        if not messagebox.askyesno(
            "Удаление",
            f"Убрать {len(items)} объектов в корзину?\n\n"
            "Карточки не удаляются бесследно — их можно будет "
            "восстановить через меню Сервис → Корзина."
        ):
            return

        codes = []
        for i in items:
            value = self.tree.item(i)["values"][1]
            if value is None:
                continue
            code = str(value).strip()
            if code:
                codes.append(code)

        if not codes:
            return

        conn = get_conn()
        try:
            moved = soft_delete_assets(conn, codes, reason="Удалено пользователем")
        finally:
            conn.close()

        self.load_assets()

        if moved:
            self.log_action(
                "soft_delete",
                entity="assets",
                details=f"Кодов: {len(codes)}; перенесено: {moved}"
            )
            messagebox.showinfo(
                "Удаление",
                f"В корзину убрано карточек: {moved}.\n\n"
                "Восстановить можно через меню Сервис → Корзина."
            )
        else:
            messagebox.showinfo("Удаление", "Нечего было удалять.")

    # ---------------- FILTERS ----------------
    def open_filters(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM categories ORDER BY name")
        categories = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT name FROM locations ORDER BY name")
        locations = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT name FROM employees ORDER BY name")
        employees = [r[0] for r in cur.fetchall()]
        conn.close()

        FilterWindow(self, categories, locations, employees, self.apply_filters)

    def apply_filters(self, filters):
        if filters is None or not any([
            filters.get("category"),
            filters.get("location"),
            filters.get("employee"),
            filters.get("not_scanned"),
        ]):
            self.current_filters = None
        else:
            self.current_filters = filters

        self.load_assets(self.search_var.get().strip())

    # ---------------- IMPORT EXCEL ----------------
    def import_excel(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("Excel files", "*.xlsx")]
        )
        if not file_path:
            return

        try:
            wb = load_workbook(file_path)
            ws = wb.active
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{e}")
            return

        headers = [cell.value for cell in ws[1]]

        col_index = {}
        for header, field, required, is_id in EXPORT_IMPORT_COLUMNS:
            if header in headers:
                col_index[field] = headers.index(header)

        missing_required = [
            header for header, field, required, is_id in EXPORT_IMPORT_COLUMNS
            if required and field not in col_index
        ]

        if missing_required:
            messagebox.showerror(
                "Ошибка",
                "В файле отсутствуют обязательные колонки:\n" + ", ".join(missing_required) +
                "\n\nИспользуйте файл, полученный через \"Экспорт в Excel\","
                " либо добавьте эти колонки вручную."
            )
            return

        def cell(row, field):
            idx = col_index.get(field)
            if idx is None or idx >= len(row):
                return None
            return row[idx]

        backup_database(reason="before_import")

        conn = get_conn()
        cur = conn.cursor()

        added = 0
        updated = 0
        skipped = 0

        for row in ws.iter_rows(min_row=2, values_only=True):
            code = cell(row, "code")
            name = cell(row, "name")

            if not code or not name:
                skipped += 1
                continue

            code = str(code).strip()
            name = str(name).strip()

            category = cell(row, "category")
            location = cell(row, "location")
            employee = cell(row, "employee")
            serial = cell(row, "serial")
            note = cell(row, "note")
            buy_date = cell(row, "buy_date")
            price = cell(row, "price")
            residual = cell(row, "residual")

            category = str(category).strip() if category else None
            location = str(location).strip() if location else None
            employee = str(employee).strip() if employee else None
            serial = str(serial).strip() if serial else None
            note = str(note).strip() if note else None

            buy_date_str = None
            if isinstance(buy_date, datetime):
                buy_date_str = buy_date.strftime("%Y-%m-%d")
            elif buy_date:
                buy_date_text = str(buy_date).strip()
                for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
                    try:
                        buy_date_str = datetime.strptime(buy_date_text, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue

            price_provided = price not in (None, "")
            try:
                price_val = float(price) if price_provided else None
            except (TypeError, ValueError):
                price_val = None
                price_provided = False

            residual_provided = residual not in (None, "")
            try:
                residual_val = float(residual) if residual_provided else None
            except (TypeError, ValueError):
                residual_val = None
                residual_provided = False

            if category:
                cur.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (category,))
            if location:
                cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (location,))
            if employee:
                cur.execute("INSERT OR IGNORE INTO employees (name) VALUES (?)", (employee,))

            cur.execute("SELECT id FROM assets WHERE code=?", (code,))
            existing = cur.fetchone()

            if existing:
                update_fields = ["name=?"]
                update_values = [name]

                if category is not None:
                    update_fields.append("category=?")
                    update_values.append(category)
                if location is not None:
                    update_fields.append("location=?")
                    update_values.append(location)
                if employee is not None:
                    update_fields.append("employee=?")
                    update_values.append(employee)
                if serial is not None:
                    update_fields.append("serial=?")
                    update_values.append(serial)
                if note is not None:
                    update_fields.append("note=?")
                    update_values.append(note)
                if buy_date_str is not None:
                    update_fields.append("buy_date=?")
                    update_values.append(buy_date_str)
                if price_provided and price_val is not None:
                    update_fields.append("price=?")
                    update_values.append(price_val)
                if residual_provided and residual_val is not None:
                    update_fields.append("residual=?")
                    update_values.append(residual_val)

                update_values.append(code)
                cur.execute(
                    f"UPDATE assets SET {', '.join(update_fields)} WHERE code=?",
                    update_values
                )
                updated += 1
            else:
                cur.execute("""
                    INSERT INTO assets
                    (code, name, category, location, employee,
                     serial, note, buy_date, price, residual)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (code, name, category, location, employee,
                      serial, note, buy_date_str,
                      price_val if price_val is not None else 0,
                      residual_val if residual_val is not None else 0))
                added += 1

        conn.commit()
        conn.close()
        self.load_assets()

        message = f"Добавлено: {added}\nОбновлено: {updated}"
        if skipped:
            message += f"\nПропущено (нет номера/названия): {skipped}"

        self.log_action(
            "import_excel",
            entity="assets",
            details=f"Добавлено: {added}, обновлено: {updated}, пропущено: {skipped}"
        )
        messagebox.showinfo("Импорт завершён", message)

    # ---------------- IMPORT ИЗ БУХГАЛТЕРИИ ----------------
    def import_accounting_excel(self):
        file_path = filedialog.askopenfilename(
            title="Выберите файл ведомости из бухгалтерии",
            filetypes=[("Excel files", "*.xlsx")]
        )
        if not file_path:
            return

        try:
            wb = load_workbook(file_path, data_only=True)
            ws = wb.worksheets[-1]
        except Exception as e:
            messagebox.showerror(
                "Ошибка",
                f"Не удалось открыть файл:\n{e}\n\n"
                "Файл должен быть в формате .xlsx."
            )
            return

        def normalize_header(value):
            text = str(value).replace("-", "")
            return " ".join(text.split()).strip().lower()

        COST_HEADER_PRIORITY = [
            "первоначальная стоимость",
            "балансовая стоимость на конец периода",
            "балансовая стоимость",
        ]

        header_row_idx = None
        col_code = col_name = col_date = None
        cost_candidates = {}

        for row_idx, row in enumerate(
            ws.iter_rows(min_row=1, max_row=30, values_only=True), start=1
        ):
            for col_idx, value in enumerate(row):
                if value is None:
                    continue
                text = normalize_header(value)
                if text == "инвентарный номер ос":
                    col_code = col_idx
                    header_row_idx = row_idx
                elif text == "наименование ос":
                    col_name = col_idx
                elif text == "дата ввода в эксп.":
                    col_date = col_idx
                elif text in COST_HEADER_PRIORITY:
                    cost_candidates[text] = col_idx

            if header_row_idx is not None and col_name is not None and col_date is not None and cost_candidates:
                break

        col_cost = None
        for header_text in COST_HEADER_PRIORITY:
            if header_text in cost_candidates:
                col_cost = cost_candidates[header_text]
                break

        if header_row_idx is None or None in (col_code, col_name, col_date, col_cost):
            messagebox.showerror(
                "Ошибка",
                "Не удалось найти нужные колонки в файле."
            )
            return

        backup_database(reason="before_accounting_import")

        conn = get_conn()
        cur = conn.cursor()

        created = 0
        updated = 0
        skipped = 0
        problems = []
        codes_in_file = set()

        for row in ws.iter_rows(min_row=header_row_idx + 2, values_only=True):
            if col_code >= len(row):
                continue

            raw_code = row[col_code]
            if raw_code is None or str(raw_code).strip() == "":
                continue

            code = str(raw_code).strip().rstrip(".")
            codes_in_file.add(code)

            raw_name = row[col_name] if col_name < len(row) else None
            name = str(raw_name).strip() if raw_name else code

            raw_date = row[col_date] if col_date < len(row) else None
            buy_date_str = None
            if raw_date:
                if isinstance(raw_date, datetime):
                    buy_date_str = raw_date.strftime("%Y-%m-%d")
                else:
                    date_text = str(raw_date).strip()
                    for fmt in ("%d.%m.%y", "%d.%m.%Y"):
                        try:
                            buy_date_str = datetime.strptime(date_text, fmt).strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue
                    if buy_date_str is None:
                        problems.append(f"{code}: не удалось разобрать дату \"{date_text}\"")

            raw_cost = row[col_cost] if col_cost < len(row) else None
            price_val = None
            if raw_cost is not None:
                cost_text = str(raw_cost).replace(" ", "").replace("\xa0", "").replace(",", ".").strip()
                if "." in cost_text:
                    try:
                        price_val = float(cost_text)
                    except ValueError:
                        problems.append(f"{code}: не удалось разобрать стоимость \"{raw_cost}\"")

            cur.execute("SELECT id FROM assets WHERE code=?", (code,))
            existing = cur.fetchone()

            if existing:
                fields = []
                values = []
                if buy_date_str is not None:
                    fields.append("buy_date=?")
                    values.append(buy_date_str)
                if price_val is not None:
                    fields.append("price=?")
                    values.append(price_val)

                if fields:
                    values.append(code)
                    cur.execute(f"UPDATE assets SET {', '.join(fields)} WHERE code=?", values)
                    updated += 1
                else:
                    skipped += 1
            else:
                cur.execute("""
                    INSERT INTO assets (code, name, buy_date, price)
                    VALUES (?, ?, ?, ?)
                """, (code, name, buy_date_str, price_val if price_val is not None else 0))
                created += 1

        conn.commit()

        cur.execute("SELECT code FROM assets")
        db_codes = {r[0] for r in cur.fetchall()}
        missing_codes = sorted(db_codes - codes_in_file)

        removed_count = 0
        removed_declined = 0
        report_path = None

        if missing_codes:
            placeholders = ",".join("?" * len(missing_codes))

            cur.execute(
                f"SELECT code, name FROM assets WHERE code IN ({placeholders})",
                missing_codes
            )
            name_by_code = {r[0]: r[1] for r in cur.fetchall()}

            preview_lines = [f"{c} — {name_by_code.get(c, '')}" for c in missing_codes[:15]]
            more_preview = f"\n...и ещё {len(missing_codes) - 15}" if len(missing_codes) > 15 else ""

            confirm = messagebox.askyesno(
                "Обнаружены списанные активы",
                f"В файле бухгалтерии не найдено {len(missing_codes)} инв. "
                f"номеров, которые есть в базе:\n\n"
                f"{chr(10).join(preview_lines)}{more_preview}\n\n"
                f"Обычно это значит, что актив списан. Перенести эти "
                f"карточки в архив списанных и убрать из активной базы?\n\n"
                f"Если не уверены, что файл содержит ВСЕ действующие "
                f"активы — нажмите \"Нет\", ничего тронуто не будет."
            )

            if confirm:
                cur.execute(
                    f"""SELECT id, code, name, category, location, employee,
                               serial, note, buy_date, price, residual, last_seen
                        FROM assets WHERE code IN ({placeholders})""",
                    missing_codes
                )
                to_remove = cur.fetchall()

                removed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                reason = f"Отсутствует в файле бухгалтерии: {os.path.basename(file_path)}"

                for r in to_remove:
                    (aid, acode, aname, acategory, alocation, aemployee,
                     aserial, anote, abuy_date, aprice, aresidual, alast_seen) = r

                    cur.execute("""
                        INSERT INTO removed_assets
                        (original_id, code, name, category, location, employee,
                         serial, note, buy_date, price, residual, last_seen,
                         removed_at, reason)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (aid, acode, aname, acategory, alocation, aemployee,
                          aserial, anote, abuy_date, aprice, aresidual,
                          alast_seen, removed_at, reason))

                cur.execute(
                    f"DELETE FROM assets WHERE code IN ({placeholders})",
                    missing_codes
                )
                conn.commit()
                removed_count = len(to_remove)

                report_rows = [
                    (r[1], r[2], r[4], r[5], r[11], removed_at)
                    for r in to_remove
                ]

                report_path = filedialog.asksaveasfilename(
                    title="Сохранить отчёт о списанных активах",
                    defaultextension=".pdf",
                    filetypes=[("PDF files", "*.pdf")],
                    initialfile=f"Списано_{datetime.now().strftime('%Y-%m-%d')}.pdf"
                )
                if report_path:
                    try:
                        from gui.removed_report import generate_removed_report_pdf
                        generate_removed_report_pdf(
                            report_path, report_rows, os.path.basename(file_path)
                        )
                    except Exception as e:
                        messagebox.showerror(
                            "Ошибка", f"Не удалось создать отчёт о списании:\n{e}"
                        )
                        report_path = None
            else:
                removed_declined = len(missing_codes)

        conn.close()
        self.load_assets()

        message = f"Создано новых карточек: {created}\nОбновлено (дата/стоимость): {updated}"
        if skipped:
            message += f"\nПропущено (не удалось разобрать данные): {skipped}"
        if removed_count:
            message += f"\nСписано (перенесено в архив): {removed_count}"
        if removed_declined:
            message += f"\nНе найдено в файле, но оставлено как есть: {removed_declined}"
        if problems:
            preview = "\n".join(problems[:15])
            more = f"\n...и ещё {len(problems) - 15}" if len(problems) > 15 else ""
            message += f"\n\nПроблемные строки:\n{preview}{more}"

        self.log_action(
            "import_accounting",
            entity="assets",
            details=(
                f"Файл: {os.path.basename(file_path)}; "
                f"создано: {created}, обновлено: {updated}, "
                f"списано: {removed_count}, пропущено: {skipped}"
            )
        )
        messagebox.showinfo("Импорт из бухгалтерии завершён", message)

        if report_path:
            try:
                os.startfile(report_path)
            except Exception:
                pass

    # ---------------- АКТ ИНВЕНТАРИЗАЦИИ (PDF) ----------------
    def print_inventory_act(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM locations ORDER BY name")
        locations = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT name FROM employees ORDER BY name")
        employees = [r[0] for r in cur.fetchall()]
        conn.close()

        from gui.inventory_act import InventoryActDialog
        InventoryActDialog(self, locations, employees, self._generate_inventory_act)

    def _generate_inventory_act(self, location, employee):
        conn = get_conn()
        cur = conn.cursor()

        query = "SELECT code, name, category FROM assets WHERE 1=1"
        params = []
        if location:
            query += " AND location=?"
            params.append(location)
        if employee:
            query += " AND employee=?"
            params.append(employee)
        query += " ORDER BY code"

        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            messagebox.showwarning(
                "Нет данных",
                "По выбранным условиям не найдено ни одного актива."
            )
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile="Акт инвентаризации.pdf"
        )
        if not file_path:
            return

        try:
            from gui.inventory_act import generate_inventory_act_pdf
            from settings import get_branch_name
            generate_inventory_act_pdf(
                file_path, rows, location, employee,
                branch_name=get_branch_name()
            )
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать PDF:\n{e}")
            return

        self.log_action(
            "print_inventory_act",
            details=f"Место: {location or 'все'}; Ответственный: {employee or 'все'}; Позиций: {len(rows)}"
        )
        messagebox.showinfo("Готово", f"Акт инвентаризации сохранён:\n{file_path}\n\nПозиций: {len(rows)}")

        try:
            os.startfile(file_path)
        except Exception:
            pass

    # ---------------- ЖУРНАЛ СПИСАННЫХ АКТИВОВ (PDF) ----------------
    def show_removed_assets_journal(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT code, name, location, employee, last_seen, removed_at, reason
            FROM removed_assets
            ORDER BY removed_at DESC
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            messagebox.showinfo(
                "Журнал списанных активов",
                "Архив списанных активов пуст — ни один актив ещё не списывался."
            )
            return

        file_path = filedialog.asksaveasfilename(
            title="Сохранить журнал списанных активов",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=f"Журнал списанных активов_{datetime.now().strftime('%Y-%m-%d')}.pdf"
        )
        if not file_path:
            return

        try:
            from gui.removed_report import generate_removed_journal_pdf
            generate_removed_journal_pdf(file_path, rows)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать журнал:\n{e}")
            return

        self.log_action(
            "print_removed_journal",
            details=f"Позиций: {len(rows)}"
        )
        messagebox.showinfo(
            "Готово",
            f"Журнал списанных активов сохранён:\n{file_path}\n\nПозиций: {len(rows)}"
        )

        try:
            os.startfile(file_path)
        except Exception:
            pass

    # ---------------- EXPORT EXCEL ----------------
    def export_excel(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                                 filetypes=[("Excel files", "*.xlsx")])
        if not file_path:
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Assets"

        fields = [field for header, field, required, is_id in EXPORT_IMPORT_COLUMNS]
        headers = [header for header, field, required, is_id in EXPORT_IMPORT_COLUMNS]
        ws.append(headers)

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(fields)} FROM assets ORDER BY id DESC")
        for row in cur.fetchall():
            ws.append(list(row))
        conn.close()

        wb.save(file_path)
        self.log_action("export_excel", entity="assets", details=os.path.basename(file_path))
        messagebox.showinfo("Готово", "Excel файл сохранён")

    # ---------------- DICTIONARIES ----------------
    def open_dictionaries(self):
        DictionariesWindow(self)

    # ---------------- TRASH ----------------
    def open_trash(self):
        RemovedAssetsWindow(self, refresh_callback=self.load_assets)

    # ---------------- RESET LAST SEEN ----------------

    def reset_all_last_seen(self):
        if not messagebox.askyesno(
            "Сбросить отметки сканирования",
            "У всех активов будет очищено поле «Последнее сканирование».\n\n"
            "Делайте это ПЕРЕД стартом инвентаризации:\n"
            "после сброса фильтр «Не сканировалось» покажет только тех, "
            "кого вы реально ещё не обошли.\n\n"
            "Продолжить?"
        ):
            return

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE assets SET last_seen=NULL")
            affected = cur.rowcount

            log_audit(
                conn,
                self.current_user["id"],
                self.current_user["username"],
                self.current_user["display_name"],
                "reset_all_last_seen",
                entity="assets",
                details=f"Обнулено записей: {affected}"
            )
            conn.commit()
        finally:
            conn.close()

        self.load_assets()
        messagebox.showinfo(
            "Готово",
            f"Отметки сканирования сброшены.\n\n"
            f"Затронуто записей: {affected}"
        )

    # ---------------- DASHBOARD ----------------

    def open_dashboard(self):
        from gui.dashboard import DashboardWindow
        DashboardWindow(self, self.current_user)

    # ---------------- ADMIN WINDOWS ----------------

    def open_users_admin(self):
        from gui.admin_window import UsersWindow
        UsersWindow(self, self.current_user)

    def open_audit_log(self):
        from gui.admin_window import AuditLogWindow
        AuditLogWindow(self, self.current_user)

    # ---------------- AUDIT ----------------
    def log_action(self, action, entity=None, entity_id=None, details=None):
        try:
            conn = get_conn()
            log_audit(
                conn,
                self.current_user["id"],
                self.current_user["username"],
                self.current_user["display_name"],
                action, entity, entity_id, details
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ---------------- USER MENU ----------------
    def change_own_password(self):
        ChangePasswordDialog(self, self.current_user)

    def change_branch_name(self):
        from settings import get_branch_name, set_branch_name

        win = tk.Toplevel(self)
        win.title("Название филиала")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=(
                "Введите название филиала — оно будет отображаться\n"
                "в шапке акта инвентаризации и других отчётов."
            ),
            justify="left",
            foreground="gray"
        ).pack(anchor="w", pady=(0, 10))

        ttk.Label(frame, text="Например: Halkbank Balkan şahamçasy").pack(anchor="w", pady=(0, 4))

        name_var = tk.StringVar(value=get_branch_name())
        entry = ttk.Entry(frame, textvariable=name_var, width=48)
        entry.pack(fill="x")
        entry.focus_set()
        entry.select_range(0, "end")

        btn = ttk.Frame(frame)
        btn.pack(pady=(15, 0))

        def save():
            new_name = name_var.get().strip()
            set_branch_name(new_name)
            self.settings["branch_name"] = new_name
            self.log_action(
                "change_branch_name",
                details=f"Новое название: {new_name or '(пусто)'}"
            )
            win.destroy()

        ttk.Button(btn, text="Сохранить", command=save).pack(side="left", padx=5)
        ttk.Button(btn, text="Отмена", command=win.destroy).pack(side="left", padx=5)

        win.bind("<Return>", lambda e: save())

    def logout(self):
        if not messagebox.askyesno(
            "Выход",
            "Выйти из программы?\n\n"
            "После выхода можно будет войти под другим пользователем."
        ):
            return
        self.log_action("logout")
        self.destroy()
        import sys
        sys.exit(0)

    # ---------------- CLOSE ----------------
    def on_close(self):
        open_toplevels = [
            w for w in self.winfo_children()
            if isinstance(w, tk.Toplevel) and w.winfo_exists()
        ]

        if open_toplevels:
            msg = (
                f"Открыто дочерних окон: {len(open_toplevels)}.\n"
                "Несохранённые изменения в них будут потеряны.\n\n"
                "Всё равно закрыть программу?"
            )
        else:
            msg = "Закрыть программу?"

        if not messagebox.askyesno("Закрыть программу?", msg):
            return

        self.log_action("logout")
        self.destroy()

    # ---------------- ABOUT ----------------
    def show_about(self):
        from version import APP_NAME, APP_VERSION, BUILD_DATE
        messagebox.showinfo(
            "О программе",
            f"{APP_NAME}\n"
            "Система учёта основных средств\n"
            "\n"
            f"Версия: {APP_VERSION}  (сборка от {BUILD_DATE})\n"
            "\n"
            "Разработано для Халкбанка\n"
            "Автор: Filippow Denis"
        )