"""集中管理資料路徑。資料目錄預設為專案根的 data/，可用環境變數 YTM_DATA_DIR 覆蓋
（例如 Docker 掛載到別處、或家用 NAS 指到共享資料夾）。"""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("YTM_DATA_DIR") or os.path.join(_ROOT, "data")
STATE_DIR = os.path.join(DATA_DIR, "state")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

for _d in (DATA_DIR, STATE_DIR, BACKUP_DIR):
    os.makedirs(_d, exist_ok=True)

POOL_FILE = os.path.join(DATA_DIR, "pool.json")
AUTH_FILE = os.path.join(DATA_DIR, "browser.json")
BLOCKLIST_FILE = os.path.join(DATA_DIR, "blocklist.json")
DAILY_STATE = os.path.join(STATE_DIR, "daily_pick_state.json")
YEARLY_STATE = os.path.join(STATE_DIR, "yearly_state.json")
BOT_CONFIG_FILE = os.path.join(DATA_DIR, "bot_config.json")
BOT_STATE = os.path.join(STATE_DIR, "bot_playlist_state.json")
