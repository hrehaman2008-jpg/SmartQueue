"""Smart Queue and Digital Token System — entry point.

Run with::

    python run.py

The application starts a development server on http://127.0.0.1:5000
Use the demo credentials documented in README.md to log in.
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)