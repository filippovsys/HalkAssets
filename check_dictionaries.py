"""
Диагностика справочников: категории, места, сотрудники.

Запускается вручную из консоли:

    python check_dictionaries.py

Показывает проблемы, которые легко не заметить глазами:

  - дубли по регистру: "Касса" и "касса" и "КАССА" — на самом деле
    три разные записи, которые нужно свести в одну;
  - дубли по пробелам: "Сейф " и "Сейф", "Каб.  305" и "Каб. 305";
  - пустые/почти пустые значения, случайно попавшие при импорте;
  - позиции справочника, которые не использует ни один актив
    (кандидаты на удаление);
  - активы, у которых значение поля отсутствует в справочнике
    (рассинхрон — обычно после ручных правок в БД).

Только показывает. Ничего не меняет и не удаляет — чтобы случайно
не сломать боевую базу накануне инвентаризации.
"""

import sys
import sqlite3

from db import get_conn


# ============================================================
# НАСТРОЙКА
# ============================================================

# (Человеческое имя, таблица справочника, поле в assets)
DICTIONARIES = [
    ("Категории",  "categories", "category"),
    ("Места",      "locations",  "location"),
    ("Сотрудники", "employees",  "employee"),
]


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _normalize(s):
    """
    Приводит строку к «каноническому» виду для сравнения:
    убирает пробелы по краям, сжимает двойные пробелы в одинарные,
    приводит к нижнему регистру. Позволяет найти "Касса" и " касса "
    как одно и то же.
    """
    if s is None:
        return ""
    return " ".join(str(s).split()).strip().lower()


def _find_canonical_duplicates(names):
    """
    Ищет группы имён, которые совпадают после нормализации.
    Возвращает список групп: каждая группа — список оригинальных
    строк (>= 2 штук).
    """
    groups = {}
    for n in names:
        key = _normalize(n)
        groups.setdefault(key, []).append(n)
    return [g for g in groups.values() if len(g) > 1]


def _find_whitespace_issues(names):
    """
    Ищет имена, у которых лишние пробелы по краям или двойные
    внутри. Возвращает список таких имён как есть.
    """
    bad = []
    for n in names:
        if n is None:
            continue
        s = str(n)
        if s != s.strip() or "  " in s or "\t" in s or "\n" in s:
            bad.append(s)
    return bad


def _find_empty(names):
    """Пустые или состоящие только из пробелов записи."""
    return [n for n in names if n is None or str(n).strip() == ""]


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def main():
    try:
        conn = get_conn()
    except Exception as e:
        print(f"Не удалось открыть базу данных: {e}")
        sys.exit(1)

    cur = conn.cursor()
    total_problems = 0

    print()
    print("=" * 70)
    print("  ДИАГНОСТИКА СПРАВОЧНИКОВ HalkAssets")
    print("=" * 70)

    for label, table, asset_field in DICTIONARIES:
        cur.execute(f"SELECT name FROM {table} ORDER BY name")
        names = [r[0] for r in cur.fetchall()]

        print()
        print(f"── {label}  (всего записей: {len(names)})")
        print()

        # 1. Пустые значения
        empties = _find_empty(names)
        if empties:
            print(f"   ⚠ Пустые записи: {len(empties)}")
            for e in empties:
                print(f"       {repr(e)}")
            total_problems += len(empties)

        # 2. Лишние пробелы
        ws_issues = _find_whitespace_issues(names)
        # Исключаем те, что уже попали в "пустые"
        ws_issues = [w for w in ws_issues if w.strip() != ""]
        if ws_issues:
            print(f"   ⚠ Лишние/двойные пробелы: {len(ws_issues)}")
            for w in ws_issues:
                print(f"       {repr(w)}")
            total_problems += len(ws_issues)

        # 3. Дубли по канонической форме (регистр + пробелы)
        canon_dups = _find_canonical_duplicates(names)
        if canon_dups:
            print(f"   ⚠ Дубли (регистр/пробелы): {len(canon_dups)} групп")
            for group in canon_dups:
                # Предлагаем оставить вариант с Заглавной первой буквой
                # и без лишних пробелов — обычно это «правильная» форма.
                clean = [g for g in group if g == g.strip()]
                suggested = sorted(clean or group, key=len)[0] if clean else group[0]
                joined = "  /  ".join(f"«{g}»" for g in group)
                print(f"       {joined}")
                print(f"         → оставить как  «{suggested}»")
            total_problems += len(canon_dups)

        # 4. Не используются в активах
        unused = []
        for name in names:
            cur.execute(
                f"SELECT COUNT(*) FROM assets WHERE {asset_field}=?",
                (name,)
            )
            if cur.fetchone()[0] == 0:
                unused.append(name)

        if unused:
            print(f"   ℹ Не используются ни одним активом: {len(unused)}")
            for u in unused:
                print(f"       «{u}»")
            # Не считаем за проблему — просто информация.

        # 5. Рассинхрон: в активах есть значения, которых нет в справочнике
        cur.execute(
            f"""SELECT DISTINCT {asset_field} FROM assets
                WHERE {asset_field} IS NOT NULL AND {asset_field} != ''"""
        )
        asset_values = [r[0] for r in cur.fetchall()]
        names_set = set(names)
        orphan_values = [v for v in asset_values if v not in names_set]

        if orphan_values:
            print(f"   ⚠ Значения в активах вне справочника: {len(orphan_values)}")
            for v in orphan_values:
                cur.execute(
                    f"SELECT COUNT(*) FROM assets WHERE {asset_field}=?",
                    (v,)
                )
                cnt = cur.fetchone()[0]
                print(f"       «{v}»  (используется у {cnt} активов)")
            total_problems += len(orphan_values)

        if not any([empties, ws_issues, canon_dups, orphan_values]):
            print("   ✓ Проблем не найдено")

    print()
    print("=" * 70)
    if total_problems:
        print(f"  Итого проблем найдено: {total_problems}")
        print()
        print("  Что делать:")
        print("   1. Открой программу → Справочники.")
        print("   2. Переименуй «неправильные» записи в «правильные»:")
        print("      — двойной клик по позиции в списке загрузит её в поле;")
        print("      — отредактируй имя → кнопка «Переименовать выбранное».")
        print("      Все активы, использовавшие старое имя, обновятся автоматически.")
        print("   3. Пустые записи и «неиспользуемые» — просто удали.")
        print("   4. Если у актива поле «висит» вне справочника — открой")
        print("      справочник, добавь туда это значение (кнопка «Добавить»),")
        print("      потом в активе сможешь выбрать его через выпадающий список.")
    else:
        print("  ✓ Справочники чистые, править нечего.")
    print("=" * 70)
    print()

    conn.close()


if __name__ == "__main__":
    main()