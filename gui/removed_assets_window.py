"""
Окно "Корзина" — список карточек, убранных из активной базы.

Сюда попадают:
- карточки, удалённые вручную пользователем
  (reason="Удалено пользователем");
- карточки, пропавшие из файла бухгалтерии
  (reason="Отсутствует в файле бухгалтерии: ...").

Из этого окна любую карточку можно вернуть в активную базу кнопкой
"Восстановить выбранное" — с проверкой, что инвентарный номер не
успел "переехать" в другую активную карточку.
"""

import tkinter as tk
from tkinter import ttk, messagebox

from db import get_conn, restore_removed_asset


class RemovedAssetsWindow(tk.Toplevel):
    def __init__(self, parent, refresh_callback=None):
        super().__init__(parent)

        self.refresh_callback = refresh_callback

        self.title("Корзина — удалённые карточки")
        self.geometry("960x520")
        self.minsize(700, 350)

        self.create_ui()
        self.load_items()

    # ---------------- UI ----------------

    def create_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x")

        ttk.Label(
            top,
            text=(
                "В корзине лежат карточки, удалённые вручную или "
                "пропавшие из файла бухгалтерии. Любую из них можно "
                "вернуть в активную базу."
            ),
            foreground="gray",
            wraplength=900,
            justify="left"
        ).pack(side="left", padx=10, pady=(8, 4))

        columns = ("code", "name", "location", "employee", "removed_at", "reason")
        self.tree = ttk.Treeview(
            self, columns=columns, show="headings", selectmode="extended"
        )
        self.tree.pack(fill="both", expand=True, padx=10, pady=5)

        self.tree.heading("code", text="Инв №")
        self.tree.heading("name", text="Название")
        self.tree.heading("location", text="Место")
        self.tree.heading("employee", text="Ответственный")
        self.tree.heading("removed_at", text="Когда убрано")
        self.tree.heading("reason", text="Причина")

        self.tree.column("code", width=130, anchor="w")
        self.tree.column("name", width=280, anchor="w")
        self.tree.column("location", width=150, anchor="w")
        self.tree.column("employee", width=170, anchor="w")
        self.tree.column("removed_at", width=140, anchor="w")
        self.tree.column("reason", width=250, anchor="w", stretch=True)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(4, 10))

        ttk.Button(
            bottom, text="Восстановить выбранное",
            command=self.restore_selected
        ).pack(side="left", padx=5)
        ttk.Button(bottom, text="Обновить", command=self.load_items).pack(side="left", padx=5)
        ttk.Button(bottom, text="Закрыть", command=self.destroy).pack(side="right", padx=5)

        self.count_label = ttk.Label(bottom, text="", foreground="gray")
        self.count_label.pack(side="right", padx=15)

    # ---------------- DATA ----------------

    def load_items(self):
        self.tree.delete(*self.tree.get_children())

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, code, name, location, employee, removed_at, reason
            FROM removed_assets
            ORDER BY removed_at DESC, id DESC
        """)
        rows = cur.fetchall()
        conn.close()

        for removed_id, code, name, location, employee, removed_at, reason in rows:
            # В качестве iid используем id записи в removed_assets —
            # так его потом легко достать из self.tree.selection().
            self.tree.insert(
                "", "end", iid=str(removed_id),
                values=(
                    code or "",
                    name or "",
                    location or "—",
                    employee or "—",
                    removed_at or "",
                    reason or ""
                )
            )

        total = len(rows)
        self.count_label.config(
            text=(f"В корзине: {total}" if total else "Корзина пуста")
        )

    # ---------------- RESTORE ----------------

    def restore_selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(
                "Восстановление",
                "Сначала выберите одну или несколько записей в списке."
            )
            return

        if not messagebox.askyesno(
            "Восстановление",
            f"Вернуть в активную базу {len(selection)} карточк(и/ек)?"
        ):
            return

        conn = get_conn()
        restored = 0
        problems = []

        try:
            for removed_id_str in selection:
                try:
                    removed_id = int(removed_id_str)
                except (TypeError, ValueError):
                    continue

                ok, error = restore_removed_asset(conn, removed_id)
                if ok:
                    restored += 1
                else:
                    row = self.tree.item(removed_id_str)["values"]
                    problems.append(f"{row[0] or '(без номера)'} — {error}")
        finally:
            conn.close()

        self.load_items()
        if self.refresh_callback:
            self.refresh_callback()

        message = f"Восстановлено карточек: {restored}"
        if problems:
            message += "\n\nНе удалось восстановить:\n" + "\n".join(problems[:10])
            if len(problems) > 10:
                message += f"\n...и ещё {len(problems) - 10}"
            messagebox.showwarning("Восстановление завершено", message)
        else:
            messagebox.showinfo("Восстановление завершено", message)