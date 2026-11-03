"""Запуск сервера: python run.py (или start.bat). Порт меняется переменной AVTO_PORT."""
import logging
import sys

import uvicorn

from app.config import DB_PATH, PORT
from app.main import create_app, lan_addresses


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    app = create_app()
    print("=" * 64)
    print(" Сайт отдела автоперевозок запущен.")
    print(" Раздайте сотрудникам адрес (открывать в Chrome или Edge):")
    for a in lan_addresses(PORT):
        print("     ", a)
    print(f" База данных: {DB_PATH}")
    print(" Не закрывайте это окно: пока оно открыто, сайт работает.")
    print("=" * 64)
    sys.stdout.flush()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
