"""Isolated test configuration; never writes credentials or users into the project."""
import os
import tempfile
from pathlib import Path

TEST_STATE = Path(tempfile.mkdtemp(prefix="uneed-test-state-"))
os.environ["UNEED_DATABASE_PATH"] = str(TEST_STATE / "users.sqlite3")
os.environ["UNEED_DSS_CONFIG_PATH"] = str(TEST_STATE / "dss_config.json")
os.environ["UNEED_SITE_CONFIG_PATH"] = str(TEST_STATE / "site_config.json")
os.environ["UNEED_SECRET_KEY"] = "test-user-app-secret-that-is-not-used-outside-tests"
os.environ["UNEED_DEV_SECRET_KEY"] = "test-dev-session-secret-that-is-not-used-outside-tests"
os.environ["UNEED_DEV_KEY"] = "test-only-developer-key"
os.environ["UNEED_ENV"] = "testing"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT_ROOT / "website_user"))
sys.path.insert(0, str(PROJECT_ROOT / "website_dev"))
