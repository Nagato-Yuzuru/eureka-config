"""Golden convergence table for linear-bot-remint (YUZ-90).

The script's contract: converge (custody token, MCP wiring, expiry record)
to healthy, with minting as the single non-repeatable step — the Linear
server keeps one active client_credentials token per app, so every mint
kills the previous token. The goldens pin the atomicity order (custody is
written BEFORE any wiring is touched; the wiring is untouched when the mint
fails) and that a re-run repairs a half-done state without minting again.
External binaries (fnox, curl, claude) are PATH stubs that append to a call
log; jq and date are real.
"""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REMINT = REPO_ROOT / "dot_claude" / "night" / "bin" / "executable_linear-bot-remint"

STUB_TOKEN = "lin_api_STUBTOKEN123"

FNOX_STUB = """\
echo "fnox $*" >> "@LOG@"
if [[ " $* " == *" get LINEAR_BOT_CLIENT_SECRET "* ]]; then printf 'client-secret-stub'; fi
exit 0
"""

# curl without -f: HTTP errors surface as a parseable body, exit 0
CURL_STUB = """\
mode=$(cat "@MODE@" 2>/dev/null || echo ok)
echo "curl" >> "@LOG@"
case "$mode" in
  ok) printf '{"access_token":"@TOKEN@","token_type":"Bearer","expires_in":2592000,"scope":"read,write"}' ;;
  http400) printf '{"error":"invalid_client","error_description":"client secret mismatch"}' ;;
  no-expiry) printf '{"access_token":"@TOKEN@","token_type":"Bearer"}' ;;
esac
exit 0
"""

CLAUDE_STUB = """\
echo "claude $*" >> "@LOG@"
sub="${1:-} ${2:-}"
if [[ "$sub" == "mcp remove" ]]; then exit 0; fi
if [[ "$sub" == "mcp add" ]]; then
  if [[ -f "@TOKEN_FILE@" ]] && [[ "$*" == *"$(cat "@TOKEN_FILE@")"* ]]; then
    echo "claude-add-saw-custody" >> "@LOG@"
  fi
  exit "$(cat "@ADDRC@" 2>/dev/null || echo 0)"
fi
exit 0
"""


class RemintHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.night = tmp_path / "night"
        self.night.mkdir()
        self.token_file = self.night / "linear-bot-token"
        self.expiry_file = self.night / "linear-bot-expiry"
        self.log = tmp_path / "calls.log"
        self.curl_mode = tmp_path / "curl-mode"
        self.add_rc = tmp_path / "add-rc"
        self.curl_mode.write_text("ok")
        self.add_rc.write_text("0")
        stubs = tmp_path / "stubs"
        stubs.mkdir()
        for name, body in (
            ("fnox", FNOX_STUB),
            ("curl", CURL_STUB),
            ("claude", CLAUDE_STUB),
        ):
            body = (
                body.replace("@LOG@", str(self.log))
                .replace("@MODE@", str(self.curl_mode))
                .replace("@TOKEN@", STUB_TOKEN)
                .replace("@TOKEN_FILE@", str(self.token_file))
                .replace("@ADDRC@", str(self.add_rc))
            )
            p = stubs / name
            p.write_text("#!/bin/bash\n" + body)
            p.chmod(0o755)
        home = tmp_path / "home"
        home.mkdir()
        self.env = {
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "HOME": str(home),
            "NIGHT_DIR": str(self.night),
        }

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(REMINT), *args],
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
        )

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text().splitlines()


def test_fresh_mint_persists_custody_then_wires(tmp_path: Path) -> None:
    h = RemintHarness(tmp_path)
    p = h.run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "minted fresh" in p.stdout
    assert h.token_file.read_text() == STUB_TOKEN
    assert stat.S_IMODE(h.token_file.stat().st_mode) == 0o600
    exp = int(h.expiry_file.read_text())
    assert abs(exp - (time.time() + 2592000)) < 300
    calls = h.calls()
    assert calls.index("curl") < calls.index("claude mcp remove -s user linear-bot")
    # the atomicity pin: when `claude mcp add` fires, custody already holds
    # the token the header carries
    assert "claude-add-saw-custody" in calls


def test_rerun_reuses_custody_without_second_mint(tmp_path: Path) -> None:
    h = RemintHarness(tmp_path)
    assert h.run().returncode == 0
    p = h.run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "reused custody token" in p.stdout
    assert h.calls().count("curl") == 1
    assert len([c for c in h.calls() if c.startswith("claude mcp add")]) == 2


def test_fresh_flag_forces_mint(tmp_path: Path) -> None:
    h = RemintHarness(tmp_path)
    assert h.run().returncode == 0
    p = h.run("--fresh")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "minted fresh" in p.stdout
    assert h.calls().count("curl") == 2


def test_mint_http_error_leaves_wiring_untouched(tmp_path: Path) -> None:
    # the old script removed the connection before knowing the mint worked;
    # a failed mint must leave the (possibly still working) wiring alone
    h = RemintHarness(tmp_path)
    h.curl_mode.write_text("http400")
    p = h.run()
    assert p.returncode == 1, p.stdout + p.stderr
    assert "invalid_client" in p.stderr
    assert not any(c.startswith("claude") for c in h.calls())
    assert not h.token_file.exists()
    assert not h.expiry_file.exists()


def test_missing_expires_in_refuses_and_writes_nothing(tmp_path: Path) -> None:
    # a null/absent expires_in must not become an already-expired (epoch)
    # expiry record that preflight then reads as truth
    h = RemintHarness(tmp_path)
    h.curl_mode.write_text("no-expiry")
    p = h.run()
    assert p.returncode == 1, p.stdout + p.stderr
    assert "mint failed" in p.stderr
    assert not h.token_file.exists()
    assert not h.expiry_file.exists()
    assert not any(c.startswith("claude") for c in h.calls())


def test_failed_wiring_repaired_by_rerun_without_reminting(tmp_path: Path) -> None:
    # crash-mid-run drill: mint ok, `claude mcp add` dies. Custody must
    # survive, and the re-run must repair the wiring from custody instead of
    # minting again (a second mint would kill the just-persisted token)
    h = RemintHarness(tmp_path)
    h.add_rc.write_text("1")
    p = h.run()
    assert p.returncode != 0
    assert h.token_file.read_text() == STUB_TOKEN
    assert h.expiry_file.exists()
    h.add_rc.write_text("0")
    p = h.run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "reused custody token" in p.stdout
    assert h.calls().count("curl") == 1
    assert "claude-add-saw-custody" in h.calls()


def test_stale_custody_near_expiry_mints_fresh(tmp_path: Path) -> None:
    # custody below the reuse floor (preflight would warn about it) is not
    # reused — the run mints a fresh token
    h = RemintHarness(tmp_path)
    h.token_file.write_text("old-token")
    h.expiry_file.write_text(str(int(time.time()) + 2 * 86400))
    p = h.run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "minted fresh" in p.stdout
    assert h.token_file.read_text() == STUB_TOKEN
    assert h.calls().count("curl") == 1


def test_night_flag_blocks_minting(tmp_path: Path) -> None:
    h = RemintHarness(tmp_path)
    (h.night / "active.json").write_text("{}")
    p = h.run()
    assert p.returncode == 3
    assert "day capability" in p.stderr
    assert h.calls() == []


def test_unknown_arg_is_a_usage_error(tmp_path: Path) -> None:
    h = RemintHarness(tmp_path)
    p = h.run("--bogus")
    assert p.returncode == 2
    assert "usage" in p.stderr
    assert h.calls() == []
