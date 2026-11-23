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
