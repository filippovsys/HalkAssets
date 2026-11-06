"""
Дашборд — сводная информация по активам для быстрого обзора.

Открывается из меню "Отчёты → Дашборд". Показывает:

  - приветствие текущего пользователя (с учётом времени суток);
  - 4 больших цифры: всего активов, первоначальная стоимость,
    остаточная стоимость, сколько давно не сканировалось;
  - круговую диаграмму по категориям;
  - топ-5 мест по количеству техники (горизонтальные бары);
  - список активов, не сканировавшихся > STALE_DAYS дней;
  - последние действия из audit_log.

Вся графика рисуется на tkinter.Canvas без внешних зависимостей:
matplotlib в сборку PyInstaller добавил бы десятки мегабайт, а на
сетевой папке это заметно замедляет запуск. Пирог и горизонтальные
бары несложно нарисовать через Canvas.create_arc / create_rectangle.
"""

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

from db import get_conn


# ============================================================
# КОНСТАНТЫ
# ============================================================

# Порог "давно не сканировалось" в днях. Активы, у которых
# last_seen пустой или старше — попадают в блок предупреждения.
STALE_DAYS = 180

# Сколько последних действий из audit_log показывать.
RECENT_ACTIONS_LIMIT = 10

# Сколько активов из "давно не сканировалось" выводить в списке.
STALE_ASSETS_LIMIT = 10

# Палитра для круговой диаграммы. 12 оттенков — обычно хватает;
# если категорий больше, остальные складываются в "Прочее".
PIE_COLORS = [
    "#2f4f6f", "#4a7ba8", "#5a9bd4", "#7fb3d5",
    "#5fa88c", "#7ec49c", "#c9a227", "#d4a95c",
    "#a85f5f", "#c98484", "#8e6f9e", "#b39dc4",
]


# ============================================================
# ФОРМАТИРОВАНИЕ
# ============================================================

def _format_int(n):
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    return f"{n:,}".replace(",", " ")


def _format_money_card(n):
    """
    Формат денежной суммы для карточки дашборда.

    Для огромных сумм (>= 1 млрд) сокращаем, иначе карточка
    распухает и цифра не влезает. Для обычных сумм — полное число
    с разделителями тысяч и без копеек (на дашборде копейки ни к чему).
    """
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        n = 0.0

    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f} млрд TMT"
    if abs(n) >= 100_000_000:
        return f"{n / 1_000_000:.1f} млн TMT"


def _greeting():
    """Приветствие по времени суток."""
    hour = datetime.now().hour
    if hour < 6:
        return "Доброй ночи"
    if hour < 12:
        return "Доброе утро"
    if hour < 18:
        return "Добрый день"
    return "Добрый вечер"


# ============================================================
# ДАННЫЕ
# ============================================================

def _fetch_stats():
    """
    Один заход в БД — собираем все цифры и списки сразу.
    Так дашборд открывается одним движением, а не 6 разными
    запросами с мигающим окном.
    """

    cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    conn = get_conn()
    cur = conn.cursor()

    # Общая сводка
    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(price), 0),
            COALESCE(SUM(residual), 0)
        FROM assets
    """)
    total_count, total_price, total_residual = cur.fetchone()

    # Давно не сканировалось
    cur.execute("""
        SELECT COUNT(*) FROM assets
        WHERE last_seen IS NULL OR last_seen = '' OR last_seen < ?
    """, (cutoff,))
    stale_count = cur.fetchone()[0]

    # По категориям
    cur.execute("""
        SELECT category, COUNT(*) AS cnt
        FROM assets
        WHERE category IS NOT NULL AND category != ''
        GROUP BY category
        ORDER BY cnt DESC, category
    """)
    categories = cur.fetchall()

    # Топ-5 мест
    cur.execute("""
        SELECT location, COUNT(*) AS cnt
        FROM assets
        WHERE location IS NOT NULL AND location != ''
        GROUP BY location
        ORDER BY cnt DESC, location
        LIMIT 5
    """)
    top_locations = cur.fetchall()

    # Список "давно не сканировалось" (топ-N самых старых)
    cur.execute("""
        SELECT code, name, location, employee, last_seen
        FROM assets
        WHERE last_seen IS NULL OR last_seen = '' OR last_seen < ?
        ORDER BY
            CASE WHEN last_seen IS NULL OR last_seen = '' THEN 0 ELSE 1 END,
            last_seen ASC
        LIMIT ?
    """, (cutoff, STALE_ASSETS_LIMIT))
    stale_assets = cur.fetchall()

    # Последние действия
    cur.execute("""
        SELECT created_at, display_name, username, action, details
        FROM audit_log
        ORDER BY id DESC
        LIMIT ?
    """, (RECENT_ACTIONS_LIMIT,))
    recent_actions = cur.fetchall()

    conn.close()

    return {
        "total_count": total_count,
        "total_price": total_price,
        "total_residual": total_residual,
        "stale_count": stale_count,
        "stale_days": STALE_DAYS,
        "categories": categories,
        "top_locations": top_locations,
        "stale_assets": stale_assets,
        "recent_actions": recent_actions,
    }


# ============================================================
# ОКНО ДАШБОРДА
# ============================================================

class DashboardWindow(tk.Toplevel):

    def __init__(self, parent, current_user):
        super().__init__(parent)

        self.current_user = current_user

        self.title("Дашборд — Основные средства")
        self.minsize(1000, 700)

        # Разворачиваем на весь экран — дашборд рассчитан на
        # максимальную площадь. Пользователь всегда может уменьшить.
        try:
            self.state("zoomed")
        except tk.TclError:
            w, h = 1280, 820
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            self.geometry(f"{w}x{h}+{x}+{y}")

        self.configure(bg="#f5f6f8")

        self._build_ui()
        self._load_and_render()

    # ---------------- UI (каркас) ----------------

    def _build_ui(self):
        # --- Верхняя шапка ---
        header = tk.Frame(self, bg="#f5f6f8")
        header.pack(fill="x", padx=20, pady=(15, 5))

        left = tk.Frame(header, bg="#f5f6f8")
        left.pack(side="left", anchor="w")

        tk.Label(
            left,
            text=f"{_greeting()}, {self.current_user['display_name']}!",
            font=("", 18, "bold"),
            bg="#f5f6f8",
            fg="#2f4f6f",
        ).pack(anchor="w")

        tk.Label(
            left,
            text=datetime.now().strftime("Сегодня %d.%m.%Y, %H:%M"),
            font=("", 10),
            bg="#f5f6f8",
            fg="#666",
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg="#f5f6f8")
        right.pack(side="right", anchor="e")

        ttk.Button(right, text="Обновить", command=self._load_and_render).pack(side="left", padx=4)
        ttk.Button(right, text="Закрыть", command=self.destroy).pack(side="left", padx=4)

        # --- Контейнер, куда при каждой перезагрузке кладётся всё содержимое ---
        # Отдельный фрейм нужен, чтобы при нажатии "Обновить" просто
        # уничтожить старую версию и отрисовать новую, не трогая шапку.
        self.body = tk.Frame(self, bg="#f5f6f8")
        self.body.pack(fill="both", expand=True, padx=20, pady=(10, 20))

    def _load_and_render(self):
        # Чистим тело и грузим свежие данные.
        for child in self.body.winfo_children():
            child.destroy()

        stats = _fetch_stats()

        self._render_cards(stats)
        self._render_middle_row(stats)
        self._render_bottom_row(stats)

    # ---------------- ВЕРХНИЙ РЯД: 4 карточки ----------------

    def _render_cards(self, stats):
        row = tk.Frame(self.body, bg="#f5f6f8")
        row.pack(fill="x", pady=(0, 15))

        stale_count = stats["stale_count"]
        stale_accent = "#c0392b" if stale_count > 0 else "#5fa88c"

        cards = [
            {
                "title": "Всего активов",
                "value": _format_int(stats["total_count"]),
                "accent": "#4a7ba8",
            },
            {
                "title": "Первоначальная стоимость",
                "value": _format_money_card(stats["total_price"]),
                "accent": "#2f4f6f",
            },
            {
                "title": "Остаточная стоимость",
                "value": _format_money_card(stats["total_residual"]),
                "accent": "#5fa88c",
            },
            {
                "title": f"Не сканировалось > {STALE_DAYS} дней",
                "value": _format_int(stale_count),
                "accent": stale_accent,
            },
        ]

        for i, card_data in enumerate(cards):
            card = self._make_card(row, **card_data)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            row.grid_columnconfigure(i, weight=1)

    def _make_card(self, parent, title, value, accent):
        """
        Карточка со сводной цифрой: тонкая цветная полоска сверху,
        серый заголовок, крупное число. Рисуем на tk-виджетах (не
        ttk), потому что ttk.Style на разных темах Windows ведёт себя
        по-разному и легко получить кривой фон.
        """
        card = tk.Frame(parent, bg="white", highlightbackground="#e0e4e8", highlightthickness=1)

        # Цветная полоска сверху
        tk.Frame(card, bg=accent, height=4).pack(fill="x")

        inner = tk.Frame(card, bg="white", padx=16, pady=14)
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner,
            text=title,
            bg="white",
            fg="#777",
            font=("", 9),
            anchor="w",
            justify="left",
        ).pack(anchor="w")

        # Размер числа подбираем от длины: длинные суммы (1 234 567 ₽)
        # должны влезать в карточку без обрезки.
        value_text = str(value)
        if len(value_text) > 14:
            font_size = 15
        elif len(value_text) > 10:
            font_size = 17
        else:
            font_size = 20

        tk.Label(
            inner,
            text=value_text,
            bg="white",
            fg="#2f4f6f",
            font=("", font_size, "bold"),
            anchor="w",
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        return card

    # ---------------- СРЕДНИЙ РЯД: пирог + топ-5 мест ----------------

    def _render_middle_row(self, stats):
        row = tk.Frame(self.body, bg="#f5f6f8")
        row.pack(fill="both", expand=True, pady=(0, 15))

        # Левая половина — категории
        left = self._make_section(row, "Активы по категориям")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

        # Правая половина — топ-5 мест
        right = self._make_section(row, "Топ-5 мест по количеству техники")
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        row.grid_columnconfigure(0, weight=1, uniform="mid")
        row.grid_columnconfigure(1, weight=1, uniform="mid")
        row.grid_rowconfigure(0, weight=1)

        # --- Внутрь левой секции ---
        left_inner = tk.Frame(left, bg="white")
        left_inner.pack(fill="both", expand=True, padx=14, pady=(4, 14))

        self._render_pie(left_inner, stats["categories"])

        # --- Внутрь правой секции ---
        right_inner = tk.Frame(right, bg="white")
        right_inner.pack(fill="both", expand=True, padx=14, pady=(4, 14))

        self._render_top_locations(right_inner, stats["top_locations"])

    def _make_section(self, parent, title):
        """Секция с заголовком и белым фоном."""
        section = tk.Frame(parent, bg="white", highlightbackground="#e0e4e8", highlightthickness=1)
        tk.Label(
            section,
            text=title,
            bg="white",
            fg="#2f4f6f",
            font=("", 11, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 4))
        return section

    # ---------------- ПИРОГ ----------------

    def _render_pie(self, parent, categories):
        if not categories:
            tk.Label(
                parent,
                text="Пока нет данных",
                bg="white",
                fg="#999",
                font=("", 11),
            ).pack(expand=True)
            return

        # Если категорий много — оставляем 11 крупнейших, остальные
        # складываем в "Прочее". Иначе пирог становится нечитаемым.
        max_slices = len(PIE_COLORS)
        if len(categories) > max_slices:
            top = categories[: max_slices - 1]
            others_count = sum(c for _, c in categories[max_slices - 1 :])
            top.append(("Прочее", others_count))
            categories = top

        container = tk.Frame(parent, bg="white")
        container.pack(fill="both", expand=True)

        # Canvas слева
        canvas = tk.Canvas(
            container,
            width=220,
            height=220,
            bg="white",
            highlightthickness=0,
        )
        canvas.pack(side="left", padx=(0, 15), pady=5)

        self._draw_pie(canvas, categories)

        # Легенда справа
        legend = tk.Frame(container, bg="white")
        legend.pack(side="left", fill="both", expand=True, pady=5)

        total = sum(c for _, c in categories) or 1

        for i, (label, count) in enumerate(categories):
            color = PIE_COLORS[i % len(PIE_COLORS)]
            line = tk.Frame(legend, bg="white")
            line.pack(fill="x", pady=2)

            swatch = tk.Frame(line, bg=color, width=12, height=12)
            swatch.pack(side="left", padx=(0, 8))
            swatch.pack_propagate(False)

            percent = count / total * 100

            tk.Label(
                line,
                text=f"{label}",
                bg="white",
                fg="#333",
                font=("", 9),
                anchor="w",
            ).pack(side="left")

            tk.Label(
                line,
                text=f"  {count}  ({percent:.1f}%)",
                bg="white",
                fg="#777",
                font=("", 9),
                anchor="e",
            ).pack(side="right")

    def _draw_pie(self, canvas, items):
        """
        Рисует круговую диаграмму по часовой стрелке, начиная
        с 12 часов. Canvas.create_arc требует start в градусах
        (0 = 3 часа по часовой, 90 = 12 часов), extent отрицательный —
        чтобы шло по часовой.
        """
        total = sum(c for _, c in items)
        if total <= 0:
            canvas.create_text(110, 110, text="Нет данных", fill="#999", font=("", 11))
            return

        pad = 10
        size = 200
        x0, y0 = pad, pad
        x1, y1 = pad + size, pad + size

        start = 90.0
        for i, (_, count) in enumerate(items):
            extent = -count / total * 360
            color = PIE_COLORS[i % len(PIE_COLORS)]

            # Очень тонкие срезы (< 0.5°) пропускаем — они всё равно
            # визуально неразличимы, а create_arc на них ругается.
            if abs(extent) < 0.5:
                start += extent
                continue

            canvas.create_arc(
                x0, y0, x1, y1,
                start=start,
                extent=extent,
                fill=color,
                outline="white",
                width=2,
            )
            start += extent

    # ---------------- ТОП-5 МЕСТ ----------------

    def _render_top_locations(self, parent, top_locations):
        if not top_locations:
            tk.Label(
                parent,
                text="Пока нет данных",
                bg="white",
                fg="#999",
                font=("", 11),
            ).pack(expand=True)
            return

        canvas = tk.Canvas(
            parent,
            bg="white",
            highlightthickness=0,
        )
        canvas.pack(fill="both", expand=True, pady=5)

        # Откладываем отрисовку до момента, когда Canvas уже получил
        # финальный размер — иначе winfo_width() вернёт 1.
        def draw(_event=None):
            canvas.delete("all")

            w = canvas.winfo_width()
            h = canvas.winfo_height()
            if w < 50 or h < 50:
                return

            max_count = max(c for _, c in top_locations) or 1

            left_pad = 140          # место под название места
            right_pad = 60          # место под число справа от бара
            row_gap = 12
            bar_h = max(14, (h - row_gap * (len(top_locations) + 1)) // len(top_locations))

            bar_area_w = max(50, w - left_pad - right_pad)

            y = row_gap
            for location, count in top_locations:
                bar_w = int(count / max_count * bar_area_w)

                # Название места
                canvas.create_text(
                    left_pad - 8, y + bar_h / 2,
                    text=location,
                    anchor="e",
                    fill="#333",
                    font=("", 10),
                )

                # Фон под бар (светло-серый)
                canvas.create_rectangle(
                    left_pad, y,
                    left_pad + bar_area_w, y + bar_h,
                    fill="#f0f2f5",
                    outline="",
                )

                # Заполненная часть
                canvas.create_rectangle(
                    left_pad, y,
                    left_pad + bar_w, y + bar_h,
                    fill="#4a7ba8",
                    outline="",
                )

                # Число после бара
                canvas.create_text(
                    left_pad + bar_w + 8, y + bar_h / 2,
                    text=str(count),
                    anchor="w",
                    fill="#2f4f6f",
                    font=("", 10, "bold"),
                )

                y += bar_h + row_gap

        canvas.bind("<Configure>", draw)
        # Однократный вызов — на случай, если Configure не сработает
        # (например, окно уже нужного размера при открытии).
        canvas.after(50, draw)

    # ---------------- НИЖНИЙ РЯД: два списка ----------------

    def _render_bottom_row(self, stats):
        row = tk.Frame(self.body, bg="#f5f6f8")
        row.pack(fill="both", expand=True)

        left = self._make_section(row, f"Давно не сканировалось (топ-{STALE_ASSETS_LIMIT})")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))

        right = self._make_section(row, "Последние действия")
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        row.grid_columnconfigure(0, weight=1, uniform="bot")
        row.grid_columnconfigure(1, weight=1, uniform="bot")
        row.grid_rowconfigure(0, weight=1)

        self._render_stale_list(left, stats["stale_assets"])
        self._render_actions_list(right, stats["recent_actions"])

    def _render_stale_list(self, section, rows):
        inner = tk.Frame(section, bg="white")
        inner.pack(fill="both", expand=True, padx=14, pady=(4, 14))

        if not rows:
            tk.Label(
                inner,
                text=f"Все активы сканировались за последние {STALE_DAYS} дней 👍",
                bg="white",
                fg="#5fa88c",
                font=("", 11),
            ).pack(expand=True)
            return

        columns = ("code", "name", "location", "employee", "last_seen")
        tree = ttk.Treeview(inner, columns=columns, show="headings", height=STALE_ASSETS_LIMIT)

        tree.heading("code", text="Инв №")
        tree.heading("name", text="Название")
        tree.heading("location", text="Место")
        tree.heading("employee", text="Ответственный")
        tree.heading("last_seen", text="Последнее сканирование")

        tree.column("code", width=110, anchor="w")
        tree.column("name", width=240, anchor="w")
        tree.column("location", width=120, anchor="w")
        tree.column("employee", width=160, anchor="w")
        tree.column("last_seen", width=150, anchor="w", stretch=True)

        for code, name, location, employee, last_seen in rows:
            tree.insert(
                "", "end",
                values=(
                    code or "",
                    name or "",
                    location or "—",
                    employee or "—",
                    last_seen or "Никогда",
                )
            )

        tree.pack(fill="both", expand=True)

    def _render_actions_list(self, section, rows):
        inner = tk.Frame(section, bg="white")
        inner.pack(fill="both", expand=True, padx=14, pady=(4, 14))

        if not rows:
            tk.Label(
                inner,
                text="Журнал действий пуст",
                bg="white",
                fg="#999",
                font=("", 11),
            ).pack(expand=True)
            return

        columns = ("created_at", "who", "action", "details")
        tree = ttk.Treeview(inner, columns=columns, show="headings", height=RECENT_ACTIONS_LIMIT)

        tree.heading("created_at", text="Когда")
        tree.heading("who", text="Кто")
        tree.heading("action", text="Действие")
        tree.heading("details", text="Подробности")

        tree.column("created_at", width=140, anchor="w")
        tree.column("who", width=160, anchor="w")
        tree.column("action", width=150, anchor="w")
        tree.column("details", width=280, anchor="w", stretch=True)

        for created_at, display_name, username, action, details in rows:
            who = display_name or username or "—"
            tree.insert(
                "", "end",
                values=(
                    created_at or "",
                    who,
                    action or "",
                    details or "",
                )
            )

        tree.pack(fill="both", expand=True)