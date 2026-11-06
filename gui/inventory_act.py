import os
import tkinter as tk
from tkinter import ttk
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


ALL_LABEL = "Все"


# ============================================================
# ШРИФТ (для поддержки кириллицы в PDF)
# ============================================================

def _find_font(filename_candidates):
    windows_fonts_dir = r"C:\Windows\Fonts"
    for name in filename_candidates:
        path = os.path.join(windows_fonts_dir, name)
        if os.path.exists(path):
            return path
    return None


_FONT_REGULAR = _find_font(["arial.ttf", "Arial.ttf"])
_FONT_BOLD = _find_font(["arialbd.ttf", "Arialbd.ttf"])

if _FONT_REGULAR:
    pdfmetrics.registerFont(TTFont("ActFont", _FONT_REGULAR))
    FONT_NAME = "ActFont"
else:
    FONT_NAME = "Helvetica"

if _FONT_BOLD:
    pdfmetrics.registerFont(TTFont("ActFont-Bold", _FONT_BOLD))
    FONT_NAME_BOLD = "ActFont-Bold"
else:
    FONT_NAME_BOLD = FONT_NAME


# ============================================================
# ГЕНЕРАЦИЯ PDF
# ============================================================

def generate_inventory_act_pdf(file_path, rows, location, employee,
                                branch_name=""):
    """
    Создаёт PDF «Акт инвентаризации основных средств».

    branch_name — название филиала из настроек (печатается сверху,
    если задано). Если пусто — строка с филиалом не выводится.
    """
    doc = SimpleDocTemplate(
        file_path,
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
    )

    branch_style = ParagraphStyle(
        "Branch",
        fontName=FONT_NAME_BOLD,
        fontSize=14,
        alignment=1,
        spaceAfter=6,
    )
    title_style = ParagraphStyle(
        "Title", fontName=FONT_NAME_BOLD, fontSize=13, alignment=1, spaceAfter=10
    )
    normal_style = ParagraphStyle("Normal", fontName=FONT_NAME, fontSize=10, spaceAfter=4)
    cell_style = ParagraphStyle("Cell", fontName=FONT_NAME, fontSize=9, leading=11)
    header_style = ParagraphStyle(
        "Header", fontName=FONT_NAME_BOLD, fontSize=9, leading=11, textColor=colors.white
    )
    sign_style = ParagraphStyle("Sign", fontName=FONT_NAME, fontSize=10, spaceAfter=18)

    elements = []

    if branch_name:
        elements.append(Paragraph(branch_name, branch_style))

    elements.append(Paragraph(
        "Esasy serişdeleriň tükellemesiniň delilnamasy", title_style
    ))

    loc_text = location if location else "Ählisi bilelikde"
    emp_text = employee if employee else "Ähli jogapkärler"
    today = datetime.now().strftime("%d.%m.%Y")

    elements.append(Paragraph(f"Ýerleşýän ýeri: {loc_text}", normal_style))
    elements.append(Paragraph(f"Jogapkär: {emp_text}", normal_style))
    elements.append(Paragraph(f"Düzülen senesi: {today}", normal_style))
    elements.append(Spacer(1, 8))

    headers = ["№", "tükelleme belgisi", "Ady", "Topary"]
    table_data = [[Paragraph(h, header_style) for h in headers]]

    for i, (code, name, category) in enumerate(rows, start=1):
        table_data.append([
            Paragraph(str(i), cell_style),
            Paragraph(code or "", cell_style),
            Paragraph(name or "", cell_style),
            Paragraph(category or "", cell_style),
        ])

    col_widths = [12 * mm, 32 * mm, 90 * mm, 42 * mm]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f4f6f")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"Jemi sany: {len(rows)}", normal_style))
    elements.append(Spacer(1, 30))

    elements.append(Paragraph(
        "Abat saklanylyşy üçin jogapkär: _______________________ / _______________________ /",
        sign_style
    ))

    doc.build(elements)


# ============================================================
# ДИАЛОГ ВЫБОРА ФИЛЬТРОВ
# ============================================================

class InventoryActDialog(tk.Toplevel):
    def __init__(self, parent, locations, employees, on_generate):
        super().__init__(parent)

        self.on_generate = on_generate

        self.title("Акт инвентаризации")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Место:").grid(row=0, column=0, sticky="w", pady=5)
        self.location_cb = ttk.Combobox(
            frame, state="readonly", width=32,
            values=[ALL_LABEL] + list(locations)
        )
        self.location_cb.set(ALL_LABEL)
        self.location_cb.grid(row=0, column=1, pady=5, padx=(10, 0))

        ttk.Label(frame, text="Ответственный:").grid(row=1, column=0, sticky="w", pady=5)
        self.employee_cb = ttk.Combobox(
            frame, state="readonly", width=32,
            values=[ALL_LABEL] + list(employees)
        )
        self.employee_cb.set(ALL_LABEL)
        self.employee_cb.grid(row=1, column=1, pady=5, padx=(10, 0))

        ttk.Label(
            frame,
            text=(
                "Можно выбрать и место, и ответственного одновременно —\n"
                "тогда в акт попадут активы, подходящие под оба условия."
            ),
            foreground="gray",
            justify="left"
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 15))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=2)

        ttk.Button(btn_frame, text="Сформировать PDF", command=self._on_confirm).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Отмена", command=self.destroy).pack(side="left", padx=5)

    def _on_confirm(self):
        location = self.location_cb.get()
        employee = self.employee_cb.get()

        location = None if location == ALL_LABEL else location
        employee = None if employee == ALL_LABEL else employee

        self.destroy()
        self.on_generate(location, employee)