import os
from datetime import datetime

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# ШРИФТ (для поддержки кириллицы в PDF)
# ============================================================
#
# Та же логика, что и в gui/inventory_act.py — используем Arial из
# системных шрифтов Windows (уже задействован для печати штрих-кодов
# и акта инвентаризации).

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
    pdfmetrics.registerFont(TTFont("RemFont", _FONT_REGULAR))
    FONT_NAME = "RemFont"
else:
    FONT_NAME = "Helvetica"

if _FONT_BOLD:
    pdfmetrics.registerFont(TTFont("RemFont-Bold", _FONT_BOLD))
    FONT_NAME_BOLD = "RemFont-Bold"
else:
    FONT_NAME_BOLD = FONT_NAME


# ============================================================
# ГЕНЕРАЦИЯ PDF
# ============================================================

def generate_removed_report_pdf(file_path, rows, source_file_name):
    """
    Создаёт PDF-отчёт "Список активов, списанных при импорте".

    rows: список кортежей
        (code, name, location, employee, last_seen, removed_at)
    source_file_name: имя файла бухгалтерии, из которого пропали эти
        инвентарные номера (для справки в шапке отчёта).
    """

    doc = SimpleDocTemplate(
        file_path,
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
    )

    title_style = ParagraphStyle(
        "Title", fontName=FONT_NAME_BOLD, fontSize=14, alignment=1, spaceAfter=10
    )
    normal_style = ParagraphStyle("Normal", fontName=FONT_NAME, fontSize=10, spaceAfter=4)
    cell_style = ParagraphStyle("Cell", fontName=FONT_NAME, fontSize=8, leading=10)
    header_style = ParagraphStyle(
        "Header", fontName=FONT_NAME_BOLD, fontSize=8, leading=10, textColor=colors.white
    )

    today = datetime.now().strftime("%d.%m.%Y")

    elements = [
        Paragraph("AKTIWLERIŇ SANAWY, IMPORT WAGTYNDA HASAPDAN ÇYKARYLAN", title_style),
        Paragraph(f"Buhgalteriýa faýly: {source_file_name}", normal_style),
        Paragraph(f"Hasapdan öçürilen senesi: {today}", normal_style),
        Spacer(1, 8),
    ]

    headers = [
        "№", "tükelleme belgisi", "Ady", "Ýeri",
        "Jogapkär", "Soňky skanirleme senesi", "Hasapdan öçürilen senesi"
    ]
    table_data = [[Paragraph(h, header_style) for h in headers]]

    for i, (code, name, location, employee, last_seen, removed_at) in enumerate(rows, start=1):
        table_data.append([
            Paragraph(str(i), cell_style),
            Paragraph(code or "", cell_style),
            Paragraph(name or "", cell_style),
            Paragraph(location or "—", cell_style),
            Paragraph(employee or "—", cell_style),
            Paragraph(last_seen or "Skanirlenmedi", cell_style),
            Paragraph(removed_at or "", cell_style),
        ])

    col_widths = [10 * mm, 24 * mm, 45 * mm, 28 * mm, 28 * mm, 30 * mm, 24 * mm]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7a2e2e")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"Jemi hasapdan öçürilen: {len(rows)}", normal_style))

    doc.build(elements)


# ============================================================
# ПОЛНЫЙ ЖУРНАЛ СПИСАНИЙ (ЗА ВСЁ ВРЕМЯ)
# ============================================================

def generate_removed_journal_pdf(file_path, rows):
    """
    Создаёт PDF "Журнал списанных активов" — полная история всех
    списаний за всё время (не только последнего импорта), с указанием
    причины/файла, из-за которого актив был списан.

    rows: список кортежей
        (code, name, location, employee, last_seen, removed_at, reason)
    Ожидается отсортированным по removed_at DESC (сначала свежие).
    """

    doc = SimpleDocTemplate(
        file_path,
        pagesize=landscape(A4),
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
    )

    title_style = ParagraphStyle(
        "Title", fontName=FONT_NAME_BOLD, fontSize=14, alignment=1, spaceAfter=10
    )
    normal_style = ParagraphStyle("Normal", fontName=FONT_NAME, fontSize=10, spaceAfter=4)
    cell_style = ParagraphStyle("Cell", fontName=FONT_NAME, fontSize=8, leading=10)
    header_style = ParagraphStyle(
        "Header", fontName=FONT_NAME_BOLD, fontSize=8, leading=10, textColor=colors.white
    )

    today = datetime.now().strftime("%d.%m.%Y")

    elements = [
        Paragraph("HASAPDAN ÇYKARYLAN AKTIWLERIŇ ŽURNALY", title_style),
        Paragraph(f"Taýýarlanan: {today}", normal_style),
        Spacer(1, 8),
    ]

    headers = [
        "№", "tükelleme belgisi", "Ady", "Ýeri", "Jogapkär",
        "Soňky skanirleme senesi", "Hasapdan öçürilen senesi", "Sebäbi (faýl)"
    ]
    table_data = [[Paragraph(h, header_style) for h in headers]]

    for i, (code, name, location, employee, last_seen, removed_at, reason) in enumerate(rows, start=1):
        table_data.append([
            Paragraph(str(i), cell_style),
            Paragraph(code or "", cell_style),
            Paragraph(name or "", cell_style),
            Paragraph(location or "—", cell_style),
            Paragraph(employee or "—", cell_style),
            Paragraph(last_seen or "Skanirlenmedi", cell_style),
            Paragraph(removed_at or "", cell_style),
            Paragraph(reason or "", cell_style),
        ])

    col_widths = [10*mm, 24*mm, 55*mm, 28*mm, 28*mm, 32*mm, 26*mm, 65*mm]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7a2e2e")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))

    elements.append(table)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"Arhiwde öçürilenleriň jemi: {len(rows)}", normal_style))

    doc.build(elements)
