"""WSGI/CLI entrypoint. `flask run` and `flask db ...`/`flask seed-dev` use this
via FLASK_APP=wsgi.py (see .env.example)."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=app.config.get("HOST", "127.0.0.1"), port=int(app.config.get("PORT", 5000)))
