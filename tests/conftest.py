import copy
import importlib
import socket
import sys
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
import supabase as supabase_package


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


TEST_USER_ID = "8f53bb7e-651f-4d0d-bc66-d45211a24c48"
TEST_EMAIL = "user@example.test"


class FakeAuth:
    def __init__(self):
        self.sign_up_payloads = []

    def get_user(self, token):
        return SimpleNamespace(user=SimpleNamespace(id=TEST_USER_ID))

    def sign_up(self, payload):
        self.sign_up_payloads.append(copy.deepcopy(payload))
        return SimpleNamespace(
            user=SimpleNamespace(
                id=TEST_USER_ID,
                email=payload["email"],
            )
        )


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.operation = "select"
        self.columns = "*"
        self.filters = []
        self.payload = None

    def select(self, columns):
        self.columns = columns
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = copy.deepcopy(payload)
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = copy.deepcopy(payload)
        return self

    def single(self):
        self.single_row = True
        return self

    def execute(self):
        call = {
            "table": self.table_name,
            "operation": self.operation,
            "columns": self.columns,
            "filters": copy.deepcopy(self.filters),
            "payload": copy.deepcopy(self.payload),
        }
        self.client.calls.append(call)
        data = self.client._next_response(call)

        if self.operation == "select" and self.columns != "*" and isinstance(data, list):
            selected_columns = {column.strip() for column in self.columns.split(",")}
            data = [
                {key: value for key, value in row.items() if key in selected_columns}
                for row in data
                if isinstance(row, dict)
            ]

        return SimpleNamespace(data=copy.deepcopy(data))


class FakeSupabase:
    def __init__(self):
        self.auth = FakeAuth()
        self.current_user = {
            "uuid": TEST_USER_ID,
            "email": TEST_EMAIL,
            "account": "pro",
            "name": "Test User",
        }
        self.calls = []
        self.table_names = []
        self._responses = defaultdict(deque)

    def table(self, table_name):
        self.table_names.append(table_name)
        return FakeQuery(self, table_name)

    def queue_response(self, table_name, operation, data):
        self._responses[(table_name, operation)].append(copy.deepcopy(data))

    def _next_response(self, call):
        key = (call["table"], call["operation"])
        queued = self._responses[key]
        if queued:
            return queued.popleft()

        if (
            call["table"] == "profiles"
            and call["operation"] == "select"
            and ("uuid", TEST_USER_ID) in call["filters"]
        ):
            return [copy.deepcopy(self.current_user)]

        if call["operation"] == "insert":
            return [copy.deepcopy(call["payload"])]

        return []


@pytest.fixture
def fake_supabase():
    return FakeSupabase()


@pytest.fixture
def app_module(monkeypatch, fake_supabase):
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "test-key-that-is-never-used-for-network")
    monkeypatch.setattr(
        supabase_package,
        "create_client",
        lambda *args, **kwargs: fake_supabase,
    )

    def deny_socket_connection(*args, **kwargs):
        raise AssertionError("Network access is disabled in backend tests")

    monkeypatch.setattr(socket.socket, "connect", deny_socket_connection)
    monkeypatch.setattr(socket, "create_connection", deny_socket_connection)

    previous_app = sys.modules.pop("app", None)
    module = importlib.import_module("app")
    try:
        yield module
    finally:
        sys.modules.pop("app", None)
        if previous_app is not None:
            sys.modules["app"] = previous_app


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()


@pytest.fixture
def bearer_headers(fake_supabase):
    def make_headers(profile=None):
        if profile is not None:
            fake_supabase.current_user = {
                **fake_supabase.current_user,
                **profile,
            }
        token = jwt.encode({"sub": TEST_USER_ID}, "local-test-key", algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    return make_headers
