"""
Блокировка "один пользователь одновременно" для сетевой версии.

Идея простая: рядом с базой данных (в папке data/, которая и так
лежит на общей сетевой папке) храним файл instance.lock с именем
пользователя, именем компьютера и временем последнего "сердцебиения".

- При запуске программа проверяет, не занят ли файл кем-то ещё и
  не "протух" ли он (protухший = обновлялся слишком давно, значит
  прошлый пользователь закрыл программу не штатно, например завис ПК
  или выключили питание).
- Если свободен — программа "занимает" его и раз в несколько секунд
  обновляет время (heartbeat), пока работает.
- При обычном закрытии файл удаляется, следующий пользователь может
  сразу зайти.

Это не идеальная защита на 100% (в теории два человека могут нажать
"запустить" в одну и ту же долю секунды на медленной сети), но для
обычной работы одного небольшого офиса — надёжно и достаточно.
"""

import os
import json
import time
import getpass
import socket
import threading
from datetime import datetime

from db import DATA_DIR

LOCK_FILE = os.path.join(DATA_DIR, "instance.lock")

HEARTBEAT_INTERVAL = 5      # как часто обновляем "сердцебиение", секунд
STALE_AFTER_SECONDS = 20    # если обновлений не было дольше этого — считаем, что прошлый сеанс "умер"

_heartbeat_stop = threading.Event()
_lock_info = None


def _get_current_user():
    # USERNAME — переменная окружения Windows с именем текущего
    # пользователя (в т.ч. доменного). getpass.getuser() — запасной
    # вариант на случай, если её почему-то нет.
    return os.environ.get("USERNAME") or getpass.getuser() or "неизвестный пользователь"


def _get_current_computer():
    # COMPUTERNAME — переменная окружения Windows с именем компьютера.
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "неизвестный компьютер"


def _read_lock_file():
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_lock_file(data):
    try:
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return True
    except Exception:
        return False


def try_acquire_lock():
    """
    Пытается "занять" программу для текущего пользователя.

    Возвращает (True, None), если удалось занять,
    или (False, owner_info), если программа уже занята кем-то ещё
    (owner_info — словарь с user/computer того, кто её занял).
    """

    global _lock_info

    existing = _read_lock_file()

    if existing:
        try:
            heartbeat = datetime.fromisoformat(existing.get("heartbeat", ""))
            age = (datetime.now() - heartbeat).total_seconds()
        except Exception:
            age = None

        # Если сердцебиение свежее — значит, программа реально занята.
        if age is not None and age <= STALE_AFTER_SECONDS:
            return False, existing
        # Иначе (протухший лок или битый файл) — считаем, что прошлый
        # сеанс завершился аварийно, и спокойно занимаем сами.

    _lock_info = {
        "user": _get_current_user(),
        "computer": _get_current_computer(),
        "pid": os.getpid(),
        "started": datetime.now().isoformat(),
        "heartbeat": datetime.now().isoformat(),
    }

    _write_lock_file(_lock_info)
    return True, None


def describe_lock_owner(owner_info):
    """
    Человекочитаемое описание того, кто занял программу —
    для текста в предупреждении пользователю.
    """
    if not owner_info:
        return "другой пользователь"

    user = owner_info.get("user", "неизвестный пользователь")
    computer = owner_info.get("computer", "неизвестный компьютер")
    return f'пользователь "{user}" (компьютер "{computer}")'


def start_heartbeat():
    """
    Запускает фоновый поток, который раз в HEARTBEAT_INTERVAL секунд
    обновляет время в файле блокировки, пока программа работает.
    Поток фоновый (daemon) — сам не мешает программе закрыться.
    """

    def loop():
        while not _heartbeat_stop.is_set():
            if _lock_info is not None:
                _lock_info["heartbeat"] = datetime.now().isoformat()
                _write_lock_file(_lock_info)
            _heartbeat_stop.wait(HEARTBEAT_INTERVAL)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


def release_lock():
    """
    Освобождает блокировку при штатном закрытии программы, чтобы
    следующий пользователь мог зайти сразу, не дожидаясь тайм-аута.
    Безопасно вызывать несколько раз подряд.
    """
    _heartbeat_stop.set()
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass
