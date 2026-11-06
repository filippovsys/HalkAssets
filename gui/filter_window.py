import tkinter as tk
from tkinter import ttk, messagebox


# Опции фильтра "Не сканировалось":
# (текст в выпадающем списке, значение для SQL).
# None — фильтр не применяется.
# "never" — только те, у кого last_seen вообще пустой.
# Число — "старше N дней".
NOT_SCANNED_OPTIONS = [
    ("Все", None),
    ("Более 30 дней назад", 30),
    ("Более 90 дней назад", 90),
    ("Более 180 дней назад", 180),
    ("Никогда не сканировалось", "never"),
]


class FilterWindow(tk.Toplevel):
    def __init__(self, parent, categories, locations, employees, apply_callback):
        super().__init__(parent)
        self.title("Фильтры")
        self.geometry("380x310")
        self.resizable(False, False)
        self.apply_callback = apply_callback

        self.category_var = tk.StringVar()
        self.location_var = tk.StringVar()
        self.employee_var = tk.StringVar()
        self.not_scanned_var = tk.StringVar(value=NOT_SCANNED_OPTIONS[0][0])

        ttk.Label(self, text="Категория:").pack(pady=(12, 0))
        self.category_cb = ttk.Combobox(
            self, values=[""] + categories,
            textvariable=self.category_var, state="readonly"
        )
        self.category_cb.pack(fill="x", padx=20)

        ttk.Label(self, text="Место:").pack(pady=(10, 0))
        self.location_cb = ttk.Combobox(
            self, values=[""] + locations,
            textvariable=self.location_var, state="readonly"
        )
        self.location_cb.pack(fill="x", padx=20)

        ttk.Label(self, text="Ответственный:").pack(pady=(10, 0))
        self.employee_cb = ttk.Combobox(
            self, values=[""] + employees,
            textvariable=self.employee_var, state="readonly"
        )
        self.employee_cb.pack(fill="x", padx=20)

        # Фильтр по дате последнего сканирования — самый полезный для
        # инвентаризации: сразу показывает, что давно не проверялось
        # (потенциально потеряно/украдено) или вообще ни разу.
        ttk.Label(self, text="Не сканировалось:").pack(pady=(10, 0))
        self.not_scanned_cb = ttk.Combobox(
            self,
            values=[label for label, _ in NOT_SCANNED_OPTIONS],
            textvariable=self.not_scanned_var,
            state="readonly"
        )
        self.not_scanned_cb.pack(fill="x", padx=20)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=15)

        ttk.Button(btn_frame, text="Применить", command=self.apply_filters).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Сбросить", command=self.reset_filters).pack(side="left", padx=5)

    def _get_not_scanned_value(self):
        label = self.not_scanned_var.get()
        for text, value in NOT_SCANNED_OPTIONS:
            if text == label:
                return value
        return None

    def apply_filters(self):
        filters = {
            "category": self.category_var.get() or None,
            "location": self.location_var.get() or None,
            "employee": self.employee_var.get() or None,
            "not_scanned": self._get_not_scanned_value(),
        }
        self.apply_callback(filters)
        self.destroy()

    def reset_filters(self):
        self.category_var.set("")
        self.location_var.set("")
        self.employee_var.set("")
        self.not_scanned_var.set(NOT_SCANNED_OPTIONS[0][0])
        self.apply_callback(None)
        self.destroy()