"""Central paths and runtime config.

Runtime data lives under TASKROUTER_HOME (default ~/taskrouter) so it stays on
the internal disk even though the code repo may live on an external volume.
Versioned JSON config (SLA templates, router rules, harnesses) ships with the
package and is git-tracked; it is never written at runtime.
"""
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent

HOST = os.environ.get("TASKROUTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("TASKROUTER_PORT", "3459"))

#: llm-hub read-only integration base URL. Server-side calls use httpx with
#: trust_env=False so local HTTP(S)_PROXY env vars never intercept localhost.
LLMHUB_BASE_URL = os.environ.get("LLMHUB_BASE_URL", "http://127.0.0.1:3457")
LLMHUB_TIMEOUT_SEC = float(os.environ.get("LLMHUB_TIMEOUT_SEC", "5"))
#: Keychain convention shared with llm-hub (Keychain service = 'llm-hub',
#: account = provider id). M2 only checks key presence (exit code), never reads.
KEYCHAIN_SERVICE = os.environ.get("TASKROUTER_KEYCHAIN_SERVICE", "llm-hub")


def home() -> Path:
    """Runtime data root (db, logs, artifacts). Overridable via TASKROUTER_HOME."""
    return Path(os.environ.get("TASKROUTER_HOME", Path.home() / "taskrouter"))


def db_path() -> Path:
    return home() / "taskrouter.db"


def log_dir() -> Path:
    return home() / "logs"


def artifact_dir() -> Path:
    return home() / "artifacts"


def ensure_dirs() -> None:
    """Create runtime directories if missing (safe to call repeatedly)."""
    for d in (home(), log_dir(), artifact_dir()):
        d.mkdir(parents=True, exist_ok=True)


def sla_templates_path() -> Path:
    return PACKAGE_DIR / "sla_templates.json"


def router_rules_path() -> Path:
    return PACKAGE_DIR / "router_rules.json"


def registry_seed_path() -> Path:
    return PACKAGE_DIR / "registry_seed.json"


def harnesses_dir() -> Path:
    return PACKAGE_DIR / "harnesses"


def dashboard_path() -> Path:
    return PACKAGE_DIR / "dashboard.html"
