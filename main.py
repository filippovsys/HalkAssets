import sys
import atexit
import tkinter as tk
from tkinter import messagebox

from db import init_db, backup_database
from instance_lock import (
    try_acquire_lock, release_lock, start_heartbeat, describe_lock_owner
)
from splash import SplashScreen
from auth import LoginWindow
from gui.main_window import MainWindow


if __name__ == "__main__":
    # ==========================================================
    # ЗАСТАВКА
    # ==========================================================
    splash_root = tk.Tk()
    splash_root.withdraw()

    splash = SplashScreen(splash_root)

    # ==========================================================
    # ТЯЖЁЛАЯ ЧАСТЬ СТАРТА
    # ==========================================================

    try:
        init_db()

        acquired, owner_info = try_acquire_lock()

        if not acquired:
            splash.close()
            splash_root.destroy()

            warning_root = tk.Tk()
            warning_root.withdraw()
            messagebox.showwarning(
                "Программа занята",
                "Уважаемый пользователь, в данный момент программу использует "
                f"{describe_lock_owner(owner_info)}.\n\n"
                "Пожалуйста, попробуйте позже."
            )
            warning_root.destroy()
            sys.exit(0)

        atexit.register(release_lock)
        start_heartbeat()

        backup_database(reason="startup")
    finally:
        pass

    splash.wait(total_seconds=2.0)
    splash.close()
    splash_root.destroy()

    # ==========================================================
    # ЛОГИН
    # ==========================================================
    login_win = LoginWindow()
    login_win.mainloop()

    if login_win.user is None:
        # Пользователь закрыл окно логина или отказался создавать
        # первого администратора — выходим, ничего не трогая.
        release_lock()
        sys.exit(0)

    current_user = login_win.user

    # ==========================================================
    # ГЛАВНОЕ ОКНО
    # ==========================================================
    try:
        app = MainWindow(current_user)
        app.mainloop()
    finally:
        # Логируем выход.
        try:
            from db import get_conn, log_audit
            conn = get_conn()
            log_audit(
                conn, current_user["id"], current_user["username"],
                current_user["display_name"], "logout"
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        release_lock()