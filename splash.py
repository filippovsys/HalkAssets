"""
Заставка при запуске программы.

Показывается раньше главного окна и висит, пока выполняются "тяжёлые"
операции старта: открытие/миграция БД, бэкап, получение сетевой
блокировки. Даже если всё это отработает быстрее 2 секунд, заставка
всё равно достоит минимальное время — чтобы не мелькала одним кадром
(это выглядит как "моргание", а не как осмысленный элемент).

Картинка ищется рядом с EXE (или со скриптом) по имени: splash*.png
или logo*.png. Если найдено несколько файлов — предпочтение отдаётся
версии с 1024 в имени (выше качество исходника), потом с 256, потом
любой другой.
"""

import os
import sys
import time
import tkinter as tk

from PIL import Image, ImageTk


if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# Размер по большей стороне в пикселях. 1024x1024 ужимаем примерно до
# 420x420 — этого достаточно, чтобы картинка смотрелась хорошо и на
# FullHD, и на 4K, и при этом не занимала пол-экрана.
SPLASH_DISPLAY_SIZE = 420

# Минимальное время показа, секунд.
SPLASH_MIN_DURATION = 2.0

# Поддерживаемые расширения.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp")


def _find_splash_file():
    """
    Ищет файл заставки в папке программы.

    Логика выбора, если файлов несколько:
      1. Имя начинается с "splash" или "logo".
      2. Расширение — одно из IMAGE_EXTENSIONS.
      3. Предпочтение: "1024" в имени > "256" в имени > без цифр.
    """
    if not os.path.isdir(BASE_DIR):
        return None

    candidates = []
    try:
        for fname in os.listdir(BASE_DIR):
            low = fname.lower()
            if not low.endswith(IMAGE_EXTENSIONS):
                continue
            if not (low.startswith("splash") or low.startswith("logo")):
                continue

            if "1024" in low:
                priority = 2
            elif "256" in low:
                priority = 1
            else:
                priority = 0

            candidates.append((priority, fname))
    except Exception:
        return None

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return os.path.join(BASE_DIR, candidates[0][1])


class SplashScreen:
    """
    Простое немодальное окно-заставка.

    Использование:
        root = tk.Tk(); root.withdraw()
        splash = SplashScreen(root)
        # ... тяжёлая работа ...
        splash.wait()          # достоит минимум N секунд
        splash.close()
        root.destroy()
    """

    def __init__(self, root):
        self.root = root
        self.win = None
        self.photo = None
        self._start_time = time.monotonic()

        path = _find_splash_file()
        if not path:
            # Файла нет — заставку просто не показываем, программа
            # продолжит запускаться как обычно.
            return

        try:
            img = Image.open(path)
        except Exception:
            return

        # Приводим к RGBA, чтобы одинаково работать и с прозрачными
        # PNG, и с обычными JPG.
        img = img.convert("RGBA")

        # Пропорциональное уменьшение по большей стороне.
        w, h = img.size
        scale = SPLASH_DISPLAY_SIZE / max(w, h)
        if scale < 1:
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            # LANCZOS даёт заметно более чистый результат, чем
            # дефолтный NEAREST/BILINEAR — а для 1024→420 это важно.
            img = img.resize(new_size, Image.LANCZOS)

        self.photo = ImageTk.PhotoImage(img)

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)      # без заголовка и рамки
        self.win.attributes("-topmost", True)

        # Фон под картинкой — как у самого изображения, чтобы
        # случайные белые полосы по краям не бросались в глаза.
        bg = self._background_color(img)
        self.win.configure(bg=bg)

        label = tk.Label(self.win, image=self.photo, borderwidth=0, bg=bg)
        label.pack()

        # Центрируем окно по экрану.
        self.win.update_idletasks()
        win_w = self.win.winfo_width()
        win_h = self.win.winfo_height()
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 3   # чуть выше центра — оптически приятнее
        self.win.geometry(f"+{x}+{y}")

        self.win.update()

    @staticmethod
    def _background_color(img):
        """
        Возвращает hex-цвет верхнего левого пикселя картинки, чтобы фон
        окна-заставки не отличался от фона самой картинки.
        """
        try:
            r, g, b, _a = img.getpixel((0, 0))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return "#ffffff"

    def wait(self, total_seconds=SPLASH_MIN_DURATION):
        """
        Крутит event loop заставки, пока не истечёт total_seconds от
        момента её создания. Если к моменту вызова уже прошло больше —
        сразу выходит (то есть заставка не "продлевает" запуск).
        """
        if self.win is None:
            return

        while True:
            elapsed = time.monotonic() - self._start_time
            remaining = total_seconds - elapsed
            if remaining <= 0:
                break
            try:
                self.root.update()
            except tk.TclError:
                return
            # Небольшой sleep, чтобы не крутить CPU вхолостую.
            time.sleep(min(0.02, remaining))

    def close(self):
        """Убирает окно заставки. Безопасно вызывать повторно."""
        if self.win is None:
            return
        try:
            self.win.destroy()
        except Exception:
            pass
        finally:
            self.win = None