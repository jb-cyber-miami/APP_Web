"""Entry point: gunicorn wsgi:app"""

from dotenv import load_dotenv

load_dotenv()  # reads .env in local development; no-op in production

from app import create_app  # noqa: E402

app = create_app()
