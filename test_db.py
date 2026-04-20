from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
print('FRONTEND_DIR exists:', FRONTEND_DIR.exists())
print('home.html exists:', (FRONTEND_DIR / 'home.html').exists())