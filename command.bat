py -m venv .venv
call .venv\Scripts\activate.bat
py -m pip install -e ".[dev]"
copy .env.example .env