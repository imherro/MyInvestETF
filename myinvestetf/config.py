from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LOCAL_DATA_DIR = DATA_DIR / "local"
RAW_DATA_DIR = DATA_DIR / "raw"
DB_PATH = LOCAL_DATA_DIR / "myinvestetf.sqlite"

LEADER_INDEX_URL = "https://theme.okbbc.com/api/latest"
FOOTER_SCRIPT_URL = "https://invest.okbbc.com/footer.js"
STATIC_ASSET_VERSION = "20260624-signal-matrix"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8017
