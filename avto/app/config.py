"""Пути к файлам. Работает и из исходников, и из собранного .exe (PyInstaller)."""
import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _static_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "app" / "static"
    return Path(__file__).resolve().parent / "static"


BASE_DIR = _base_dir()
STATIC_DIR = _static_dir()
DATA_DIR = Path(os.environ.get("AVTO_DATA_DIR") or (BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("AVTO_DB") or (DATA_DIR / "avto.db"))
PORT = int(os.environ.get("AVTO_PORT") or 8000)
VERSION = "1.0"
