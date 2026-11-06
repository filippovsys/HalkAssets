import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

from db import get_conn, log_movement, get_movement_history


def _parse_money(value):
    """
    Разбирает пользовательский ввод в денежное значение (float).

    Возвращает:
      - float при успехе (0.0 для пустой строки);
      - None, если ввод не является числом.

    Понимает как "1500.50", так и "1 500,50" (пробел-разделитель
    тысяч и запятая как десятичный разделитель).
    """
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text:
        return 0.0
    text = text.replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


class AssetCard(tk.Toplevel):
    def __init__(self, parent, asset=None, refresh_callback=None):
        super().__init__(parent)

        self.asset = asset
        self.refresh_callback = refresh_callback

        self.title("Карточка объекта")
        self.geometry("420x520")
        self.resizable(False, False)

        self.create_ui()
        self.load_dictionaries()

        if asset:
            self.fill_form()

    # ================= UI =================

    def create_ui(self):
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)

        self.vars = {}

        fields = [
            ("Инв. номер", "code"),
            ("Название", "name"),
            ("Категория", "category"),
            ("Место", "location"),
            ("Ответственный", "employee"),
            ("Серийный номер", "serial"),
            ("Примечание", "note"),
            ("Дата покупки (YYYY-MM-DD)", "buy_date"),
            ("Стоимость", "price"),
            ("Остаточная стоимость", "residual"),
        ]

        for i, (label, key) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=3)

            if key in ("category", "location", "employee"):
                cb = ttk.Combobox(frame, state="readonly")
                cb.grid(row=i, column=1, sticky="ew", pady=3)
                self.vars[key] = cb
            else:
                ent = ttk.Entry(frame)
                ent.grid(row=i, column=1, sticky="ew", pady=3)
                self.vars[key] = ent

        frame.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=len(fields), column=0, columnspan=2, pady=15)

        ttk.Button(btn_frame, text="Сохранить", command=self.save).pack(side="left", padx=5)

        # Кнопка истории показывается только для уже существующего
        # актива — у нового карточки истории перемещений пока нет.
        if self.asset:
            ttk.Button(
                btn_frame,
                text="История перемещений",
                command=self.show_movement_history
            ).pack(side="left", padx=5)

        ttk.Button(btn_frame, text="Закрыть", command=self.destroy).pack(side="left", padx=5)

    # ================= LOAD DICTS =================

    def load_dictionaries(self):
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT name FROM categories ORDER BY name")
        self.vars["category"]["values"] = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT name FROM locations ORDER BY name")
        self.vars["location"]["values"] = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT name FROM employees ORDER BY name")
        self.vars["employee"]["values"] = [r[0] for r in cur.fetchall()]

        conn.close()

    # ================= FILL =================

    def fill_form(self):
        for key, widget in self.vars.items():
            value = self.asset.get(key, "")

            if value is None:
                value = ""

            value = str(value)

            if isinstance(widget, ttk.Combobox):
                widget.set(value)
            else:
                widget.delete(0, "end")
                widget.insert(0, value)

    # ================= MOVEMENT HISTORY =================

    def show_movement_history(self):
        """
        Показывает последние перемещения актива (смена места и/или
        ответственного) в отдельном окне.
        """

        conn = get_conn()
        rows = get_movement_history(conn, self.asset["id"], limit=3)
        conn.close()

        win = tk.Toplevel(self)
        win.title("История перемещений")
        win.geometry("560x200")
        win.resizable(False, False)

        columns = ("date", "location", "employee")
        tree = ttk.Treeview(win, columns=columns, show="headings", height=5)

        tree.heading("date", text="Дата")
        tree.heading("location", text="Место (было → стало)")
        tree.heading("employee", text="Ответственный (было → стало)")

        tree.column("date", width=140, anchor="w")
        tree.column("location", width=200, anchor="w")
        tree.column("employee", width=200, anchor="w")

        tree.pack(fill="both", expand=True, padx=10, pady=10)

        if not rows:
            tree.insert("", "end", values=("—", "нет перемещений", "—"))
        else:
            for old_location, new_location, old_employee, new_employee, changed_at in rows:
                tree.insert("", "end", values=(
                    changed_at,
                    f"{old_location or '—'} → {new_location or '—'}",
                    f"{old_employee or '—'} → {new_employee or '—'}"
                ))

    # ================= SAVE =================

    def save(self):
        # --- 1. Сбор и валидация данных ДО открытия БД ---

        data = {}
        for key, widget in self.vars.items():
            data[key] = widget.get().strip()

        if not data["code"] or not data["name"]:
            messagebox.showwarning(
                "Ошибка", "Инвентарный номер и Название обязательны"
            )
            return

        # Числовые поля. Парсер понимает "1 500,50", "1500.50",
        # "1500" и "" (пусто -> 0.0). Возвращает None на мусор —
        # тогда показываем понятную ошибку и НЕ трогаем БД.
        price_val = _parse_money(data["price"])
        if price_val is None:
            messagebox.showerror(
                "Ошибка в поле «Стоимость»",
                f"Не удалось разобрать значение: «{data['price']}».\n\n"
                "Введите число, например: 1500 или 1 500,50"
            )
            return

        residual_val = _parse_money(data["residual"])
        if residual_val is None:
            messagebox.showerror(
                "Ошибка в поле «Остаточная стоимость»",
                f"Не удалось разобрать значение: «{data['residual']}».\n\n"
                "Введите число, например: 1500 или 1 500,50"
            )
            return

        # Дата: пустая или строго YYYY-MM-DD.
        buy_date_str = data["buy_date"]
        if buy_date_str:
            try:
                datetime.strptime(buy_date_str, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror(
                    "Ошибка в поле «Дата покупки»",
                    f"Не удалось разобрать дату: «{buy_date_str}».\n\n"
                    "Ожидается формат ГГГГ-ММ-ДД, например 2026-09-16."
                )
                return

        # --- 2. Работа с БД одной транзакцией ---

        conn = get_conn()
        try:
            cur = conn.cursor()

            # Проверка на дубликат инвентарного номера. При
            # редактировании существующей карточки разрешаем сохранить
            # тот же номер, что уже был у неё.
            cur.execute("SELECT id FROM assets WHERE code=?", (data["code"],))
            duplicate = cur.fetchone()

            if duplicate and (not self.asset or duplicate[0] != self.asset["id"]):
                messagebox.showerror(
                    "Дубликат инвентарного номера",
                    f"Инвентарный номер \"{data['code']}\" уже используется "
                    f"активом с ID {duplicate[0]}.\n\n"
                    "Инвентарный номер должен быть уникальным — проверьте "
                    "номер или откройте существующую карточку для редактирования."
                )
                return

            # Авто-добавление в справочники.
            if data["category"]:
                cur.execute(
                    "INSERT OR IGNORE INTO categories(name) VALUES(?)",
                    (data["category"],)
                )
            if data["location"]:
                cur.execute(
                    "INSERT OR IGNORE INTO locations(name) VALUES(?)",
                    (data["location"],)
                )
            if data["employee"]:
                cur.execute(
                    "INSERT OR IGNORE INTO employees(name) VALUES(?)",
                    (data["employee"],)
                )

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if self.asset:
                # --- UPDATE существующей карточки ---
                #
                # ВАЖНО: last_seen при редактировании карточки НЕ
                # обновляется. Это принципиально для инвентаризации:
                # если менять места/ответственных вручную во время
                # обхода, last_seen обновлялся бы сам, и было бы
                # невозможно отличить «я тут реально был и отсканировал»
                # от «я просто правил карточку из-за стола».
                #
                # last_seen обновляется ТОЛЬКО при сканировании
                # (см. main_window.py, on_key_scan).

                old_location = str(self.asset.get("location") or "")
                old_employee = str(self.asset.get("employee") or "")

                # Перемещение = изменилось место и/или ответственный.
                if old_location != data["location"] or old_employee != data["employee"]:
                    log_movement(
                        conn,
                        self.asset["id"],
                        old_location, data["location"],
                        old_employee, data["employee"],
                        now
                    )

                cur.execute("""
                    UPDATE assets SET
                        code=?,
                        name=?,
                        category=?,
                        location=?,
                        employee=?,
                        serial=?,
                        note=?,
                        buy_date=?,
                        price=?,
                        residual=?
                    WHERE id=?
                """, (
                    data["code"],
                    data["name"],
                    data["category"],
                    data["location"],
                    data["employee"],
                    data["serial"],
                    data["note"],
                    data["buy_date"],
                    price_val,
                    residual_val,
                    self.asset["id"]
                ))
            else:
                # --- INSERT новой карточки ---
                #
                # У новой карточки, созданной вручную через форму,
                # last_seen тоже оставляем пустым: её ещё ни разу
                # не сканировали. Если она создана в результате скана
                # неизвестного кода, то on_key_scan сам проставит
                # last_seen в момент сканирования (см. main_window.py).
                cur.execute("""
                    INSERT INTO assets(
                        code, name, category, location, employee,
                        serial, note, buy_date, price, residual, last_seen
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    data["code"],
                    data["name"],
                    data["category"],
                    data["location"],
                    data["employee"],
                    data["serial"],
                    data["note"],
                    data["buy_date"],
                    price_val,
                    residual_val,
                    None
                ))

            conn.commit()

        except Exception as e:
            conn.rollback()
            messagebox.showerror(
                "Ошибка сохранения",
                f"Не удалось сохранить карточку:\n{e}\n\n"
                "Изменения не применены — база осталась в исходном "
                "состоянии (история перемещений тоже не тронута)."
            )
            return
        finally:
            conn.close()

        # --- 3. Успешное сохранение ---

        if self.refresh_callback:
            self.refresh_callback()

        self.destroy()