"""Suite-wide isolation for the WhatsApp settings store.

Two existing test modules exercised the WhatsApp seam against the REPO's own
wa_state.json, so a dry run left real markers behind (the file still carried
"momence_pushed:wa-61400111222" on 11 Sep 2026) and the next run answered from
them. Point the store at a throwaway file for the whole session instead. It is
set as an ENV VAR, not an attribute, so it survives the importlib.reload(app)
that several test modules do at import time; per-test monkeypatching of
app.WA_STATE_FILE still wins where a module already does it.
"""
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_WA_STATE_PATH = pathlib.Path(tempfile.mkdtemp(prefix="outdoor-squad-tests-")) / "wa_state.json"
os.environ["OUTDOOR_SQUAD_WA_STATE_FILE"] = str(_WA_STATE_PATH)


@pytest.fixture(scope="session", autouse=True)
def _isolated_wa_state_file():
    import app

    app.WA_STATE_FILE = _WA_STATE_PATH
    app._wa_setting_cache.clear()
    yield
    app._wa_setting_cache.clear()
