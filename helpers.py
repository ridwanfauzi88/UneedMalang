import importlib.util
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_app(name, relpath, env=None):
    """Muat modul aplikasi dari path (folder website_* bukan package)."""
    os.environ.update(env or {})
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # Flask menentukan folder template dari sys.modules[__name__]
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True
    return mod


def csrf_of(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def tmpdir():
    return pathlib.Path(tempfile.mkdtemp())
