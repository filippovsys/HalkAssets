import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from db import get_conn


# Соответствие: таблица справочника -> поле в таблице assets, которое
# на неё ссылается. Нужно, чтобы при переименовании/удалении позиции
# можно было согласованно обновить все карточки активов, которые её
# используют, а не оставлять в базе "осиротевший" текст, которого
# больше нет в самом справочнике.
DICTIONARIES = [
    ("Категории", "categories", "category"),
    ("Места", "locations", "location"),
    ("Сотрудники", "employees", "employee"),
]


class DictionariesWindow(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)

        self.title("Справочники")
        self.geometry("560x480")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        for label, table_name, asset_field in DICTIONARIES:
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=label)
            self.create_tab(tab, table_name, asset_field)

    # =====================================================
    # UNIVERSAL TAB
    # =====================================================

    def create_tab(self, tab, table_name, asset_field):
        # Список позиций. Рядом с именем в скобках показывается,
        # сколько активов сейчас используют эту позицию — сразу видно,
        # что можно спокойно удалить, а что затронет карточки.
        list_frame = ttk.Frame(tab)
        list_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        listbox = tk.Listbox(list_frame, activestyle="dotbox")
        listbox.pack(fill="both", expand=True, side="left")

        scrollbar = ttk.Scrollbar(list_frame, command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.config(yscrollcommand=scrollbar.set)

        # Показанная в списке строка ("Касса  (12)") -> настоящее имя
        # позиции в базе ("Касса"), без счётчика.
        name_by_display = {}

        ttk.Label(
            tab,
            text=(
                "Можно вставить сразу несколько строк (по одной позиции "
                "в строке) — добавятся все разом. Двойной клик по позиции "
                "в списке — загрузить её в поле для переименования."
            ),
            foreground="gray",
            justify="left",
            wraplength=520
        ).pack(fill="x", padx=10, pady=(0, 4))

        entry = tk.Text(tab, height=3)
        entry.pack(fill="x", padx=10, pady=(0, 5))

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(pady=(0, 10))

        # ---------------- ЗАГРУЗКА СПИСКА ----------------

        def load_items():
            listbox.delete(0, tk.END)
            name_by_display.clear()

            conn = get_conn()
            cur = conn.cursor()
            cur.execute(f"SELECT name FROM {table_name} ORDER BY name")
            names = [r[0] for r in cur.fetchall()]

            for name in names:
                cur.execute(
                    f"SELECT COUNT(*) FROM assets WHERE {asset_field}=?", (name,)
                )
                count = cur.fetchone()[0]
                display = f"{name}  ({count})" if count else name
                name_by_display[display] = name
                listbox.insert(tk.END, display)

            conn.close()

        def get_selected_name():
            sel = listbox.curselection()
            if not sel:
                return None
            return name_by_display.get(listbox.get(sel[0]))

        # ---------------- ДОБАВЛЕНИЕ (в т.ч. массовое) ----------------

        def add_items():
            raw_text = entry.get("1.0", "end").strip()
            if not raw_text:
                return

            lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
            if not lines:
                return

            conn = get_conn()
            cur = conn.cursor()

            added = 0
            skipped = 0

            for name in lines:
                try:
                    cur.execute(f"INSERT INTO {table_name} (name) VALUES (?)", (name,))
                    added += 1
                except sqlite3.IntegrityError:
                    skipped += 1

            conn.commit()
            conn.close()

            entry.delete("1.0", "end")
            load_items()

            if skipped:
                messagebox.showinfo(
                    "Добавление завершено",
                    f"Добавлено новых: {added}\nУже существовало (пропущено): {skipped}"
                )

        # ---------------- ПЕРЕИМЕНОВАНИЕ ----------------

        def load_selected_for_rename(event=None):
            name = get_selected_name()
            if name is None:
                return
            entry.delete("1.0", "end")
            entry.insert("1.0", name)

        def rename_item():
            name = get_selected_name()
            if name is None:
                messagebox.showinfo("Переименование", "Сначала выберите позицию в списке.")
                return

            new_name = entry.get("1.0", "end").strip()

            if not new_name:
                messagebox.showwarning("Переименование", "Введите новое название в поле ввода.")
                return

            if "\n" in entry.get("1.0", "end-1c").strip():
                messagebox.showwarning(
                    "Переименование",
                    "Для переименования введите только одну строку — новое название "
                    "(для массового добавления нескольких позиций используйте кнопку "
                    "\"Добавить\")."
                )
                return

            if new_name == name:
                return

            conn = get_conn()
            cur = conn.cursor()

            cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE name=?", (new_name,))
            if cur.fetchone()[0] > 0:
                messagebox.showwarning(
                    "Переименование",
                    f"Позиция \"{new_name}\" уже есть в справочнике."
                )
                conn.close()
                return

            cur.execute(f"SELECT COUNT(*) FROM assets WHERE {asset_field}=?", (name,))
            usage_count = cur.fetchone()[0]

            cur.execute(f"UPDATE {table_name} SET name=? WHERE name=?", (new_name, name))

            if usage_count:
                cur.execute(
                    f"UPDATE assets SET {asset_field}=? WHERE {asset_field}=?",
                    (new_name, name)
                )

            conn.commit()
            conn.close()

            entry.delete("1.0", "end")
            load_items()

            if usage_count:
                messagebox.showinfo(
                    "Готово",
                    f"Переименовано в \"{new_name}\".\nОбновлено карточек активов: {usage_count}."
                )

        # ---------------- УДАЛЕНИЕ ----------------

        def delete_item():
            name = get_selected_name()
            if name is None:
                messagebox.showinfo("Удаление", "Сначала выберите позицию в списке.")
                return

            conn = get_conn()
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM assets WHERE {asset_field}=?", (name,))
            usage_count = cur.fetchone()[0]

            if usage_count:
                confirm = messagebox.askyesno(
                    "Удалить позицию",
                    f"Позицию \"{name}\" сейчас использует {usage_count} "
                    f"актив(ов). При удалении это поле у них станет пустым "
                    f"(данные самих карточек не удаляются).\n\n"
                    f"Продолжить?"
                )
            else:
                confirm = messagebox.askyesno("Удалить позицию", f"Удалить \"{name}\"?")

            if not confirm:
                conn.close()
                return

            if usage_count:
                cur.execute(
                    f"UPDATE assets SET {asset_field}=NULL WHERE {asset_field}=?", (name,)
                )

            cur.execute(f"DELETE FROM {table_name} WHERE name=?", (name,))
            conn.commit()
            conn.close()

            load_items()

        # ---------------- ПРИВЯЗКИ И КНОПКИ ----------------

        listbox.bind("<Double-Button-1>", load_selected_for_rename)

        # Enter в поле ввода — сразу добавить (удобно вводить позиции
        # одну за другой). Для вставки нескольких строк сразу это не
        # мешает: вставка через буфер обмена не задействует Enter.
        def on_enter(event):
            add_items()
            return "break"

        entry.bind("<Return>", on_enter)

        ttk.Button(btn_frame, text="Добавить", command=add_items).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Переименовать выбранное", command=rename_item).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Удалить выбранное", command=delete_item).pack(side="left", padx=5)

        load_items()
