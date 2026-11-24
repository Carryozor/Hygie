"""Break-glass CLI: remove the two-factor authentication of a user.

    docker exec -it hygie python -m backend.tools.reset_mfa --username NAME [--yes]

Disables 2FA, deletes backup codes and challenges, revokes every session
(refresh + access tokens) and writes an ``mfa_reset`` auth event.

Exit codes: 0 done, 1 unknown user, 2 2FA not enabled, 3 database error,
4 aborted at the confirmation prompt.
"""
import argparse
import asyncio
import os
import sys
from typing import Callable, Mapping
from urllib.parse import quote

PASS_FILE = "/app/data/.mariadb_pass"
EXIT_OK, EXIT_UNKNOWN_USER, EXIT_NOT_ENABLED, EXIT_DB_ERROR, EXIT_ABORTED = 0, 1, 2, 3, 4


class ConfigError(Exception):
    """The database cannot be located from the environment."""


def resolve_database_url(env: Mapping[str, str], pass_file: str = PASS_FILE) -> str | None:
    """DATABASE_URL, else rebuilt for the embedded MariaDB, else None (SQLite).

    The entrypoint only exports DATABASE_URL inside its own shell, so a
    `docker exec` does not see it: rebuild it from the persisted password."""
    url = (env.get("DATABASE_URL") or "").strip()
    if url:
        return url
    if env.get("EMBEDDED_MARIADB", "false").lower() != "true":
        return None
    try:
        with open(pass_file, encoding="utf-8") as fh:
            password = fh.read().strip()
    except OSError as e:
        raise ConfigError(f"embedded MariaDB password file unreadable ({pass_file}): {e.strerror}") from e
    if not password:
        raise ConfigError(f"embedded MariaDB password file is empty ({pass_file})")
    return f"mysql+aiomysql://hygie:{quote(password, safe='')}@127.0.0.1:3306/hygie"


def _prepare_environment() -> None:
    """Must run BEFORE backend.db.engine is imported: it reads DATABASE_URL at import."""
    url = resolve_database_url(os.environ)
    if url:
        os.environ["DATABASE_URL"] = url


# backend.db.engine reads DATABASE_URL at import time, and backend.mfa imports it:
# the environment must be settled before those imports below.
_PREP_ERROR: ConfigError | None = None
try:
    _prepare_environment()
except ConfigError as _e:
    _PREP_ERROR = _e

from backend import mfa  # noqa: E402
from backend.auth_events import log_auth_event  # noqa: E402


async def run(username: str, yes: bool, input_fn: Callable[[str], str] = input) -> int:
    from backend.db import engine
    from backend.auth import get_user, revoke_all_refresh_tokens

    if engine.DIALECT == "sqlite" and not os.path.isfile(engine.SQLITE_PATH):
        print(f"Base SQLite introuvable : {engine.SQLITE_PATH} (rien n'a été créé)", file=sys.stderr)
        return EXIT_DB_ERROR
    try:
        await engine.init_db_pool()
        try:
            user = await get_user(username)
            if not user:
                print(f"Utilisateur inconnu : {username}", file=sys.stderr)
                return EXIT_UNKNOWN_USER
            if not user.get("totp_enabled"):
                print(f"La 2FA n'est pas activée pour {username}", file=sys.stderr)
                return EXIT_NOT_ENABLED
            if not yes:
                answer = input_fn(f"Désactiver la 2FA de {username} et fermer toutes ses sessions ? [o/N] ")
                if answer.strip().lower() not in ("o", "oui", "y", "yes"):
                    print("Annulé.")
                    return EXIT_ABORTED
            await mfa.disable(user["id"])
            await revoke_all_refresh_tokens(username)
            await log_auth_event("mfa_reset", ip="cli", username=username, method="cli")
            print(f"2FA désactivée pour {username} ; toutes les sessions ont été révoquées.")
            return EXIT_OK
        finally:
            await engine.close_db_pool()
    except Exception as e:  # any DB / IO failure: report once, exit 3
        print(f"Erreur base de données : {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_DB_ERROR


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Reset a Hygie user's two-factor authentication")
    parser.add_argument("--username", required=True)
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)
    if _PREP_ERROR is not None:
        print(f"Configuration : {_PREP_ERROR}", file=sys.stderr)
        sys.exit(EXIT_DB_ERROR)
    sys.exit(asyncio.run(run(args.username, args.yes)))


if __name__ == "__main__":
    main()
