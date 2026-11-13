"""scripts/check_database.py never prints any part of the connection string."""
import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.services import store

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_database.py"
spec = importlib.util.spec_from_file_location("check_database", SCRIPT)
check_database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_database)

SECRETS = ("s3cr3t-Pa55", "fv_owner", "ep-example-host", "neondb")
# Each URL carries every secret. The first three are the typos that make a
# driver quote the whole string back.
URLS = [
    "postgres//fv_owner:s3cr3t-Pa55@ep-example-host.aws.neon.tech/neondb",        # missing colon
    "postgresql:/fv_owner:s3cr3t-Pa55@ep-example-host.aws.neon.tech/neondb",      # one slash
    "fv_owner:s3cr3t-Pa55@ep-example-host.aws.neon.tech/neondb?sslmode=require",  # no scheme
    "postgresql://fv_owner:s3cr3t-Pa55@ep-example-host.aws.neon.tech:notaport/neondb",
    "postgresql://fv_owner:s3cr3t-Pa55@ep-example-host.aws.neon.tech/neondb?sslmode=require",
]
DRIVER_MESSAGES = [
    'missing "=" after "{url}" in connection info string',
    'connection failed: connection to server at "{url}" failed: FATAL: password authentication failed for user "fv_owner"',
    'invalid integer value "notaport" for connection option "port" in {url}',
    "could not translate host name \"ep-example-host.aws.neon.tech\" to address ({url})",
    "something nobody anticipated {url} password=s3cr3t-Pa55",
]


def _run(monkeypatch, capsys, url, exc):
    @contextmanager
    def failing_connect():
        raise exc
        yield

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(store, "connect", failing_connect)
    code = check_database.main()
    return code, capsys.readouterr()


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("template", DRIVER_MESSAGES)
def test_no_part_of_the_url_is_ever_printed(monkeypatch, capsys, url, template):
    code, out = _run(monkeypatch, capsys, url, RuntimeError(template.format(url=url)))
    assert code == 2
    printed = out.out + out.err
    for secret in SECRETS + (url,):
        assert secret not in printed
    assert "RuntimeError: " in printed          # the error class is reported


def test_a_real_driver_parse_error_is_redacted(monkeypatch, capsys):
    """psycopg quotes a malformed string back verbatim. Reproduced without any network."""
    from psycopg import conninfo
    url = URLS[0]
    with pytest.raises(Exception) as caught:
        conninfo.conninfo_to_dict(url)
    assert "s3cr3t-Pa55" in str(caught.value)    # the leak this script must not repeat
    code, out = _run(monkeypatch, capsys, url, caught.value)
    assert code == 2
    assert "s3cr3t-Pa55" not in out.out + out.err
    assert "not a valid Postgres URL" in out.out


def test_failure_messages_come_from_a_fixed_set():
    allowed = {m for _, m in check_database._FAILURES} | {check_database._UNKNOWN}
    for text in ("password authentication failed for user x", "timeout expired", "weird new error"):
        described = check_database.describe_failure(ValueError(text))
        assert described.split(": ", 1)[1] in allowed
        assert text not in described


def test_unset_url_is_reported_without_connecting(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert check_database.main() == 1
    assert "not set" in capsys.readouterr().out
