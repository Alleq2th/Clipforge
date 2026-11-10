"""WSGI entrypoint for gunicorn: `gunicorn wsgi:app`."""
from app import app  # noqa: F401
