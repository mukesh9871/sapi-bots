import sys
from pathlib import Path

# Make `import hub` work when pytest runs from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/helpers importable

import pytest
import keyring
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    """Tests never touch the real Windows Credential Manager (Milestone 10)."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not set")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring():
    old = keyring.get_keyring()
    ring = MemoryKeyring()
    keyring.set_keyring(ring)
    yield ring
    keyring.set_keyring(old)


@pytest.fixture(autouse=True)
def model_list(tmp_path_factory, monkeypatch):
    """Tests never read or write the real data/models.json (R-MODEL-1): each starts with no list."""
    from hub.chat import models
    monkeypatch.setattr(models, "CACHE", tmp_path_factory.mktemp("models") / "models.json")
    monkeypatch.setitem(models._state, "models", None)
    monkeypatch.setitem(models._state, "default", None)
    monkeypatch.setitem(models._state, "source", "")
    from hub.chat import connectors_claude
    monkeypatch.setattr(connectors_claude, "CACHE", tmp_path_factory.mktemp("conn") / "connectors.json")
    monkeypatch.setitem(connectors_claude._state, "servers", None)
    yield models
