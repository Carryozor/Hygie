#!/bin/bash
# Hygie startup entrypoint
#
# Modes:
#   1. SQLite (défaut)        — aucune configuration requise
#   2. MariaDB embarquée      — EMBEDDED_MARIADB=true + image built avec EMBEDDED_MARIADB_SUPPORT=true
#                               Le container doit tourner en root (user: root dans docker-compose)
#   3. MariaDB externe        — DATABASE_URL=mysql+aiomysql://... (géré par engine.py directement)
#
set -euo pipefail

#── Embedded MariaDB ───────────────────────────────────────────────────────────
if [ "${EMBEDDED_MARIADB:-false}" = "true" ]; then

    # Vérifier que MariaDB est bien installé dans cette image
    if ! command -v mysqld &>/dev/null; then
        echo "[embedded-db] ERREUR: MariaDB n'est pas installé dans cette image." >&2
        echo "[embedded-db] Reconstruire avec: docker build --build-arg EMBEDDED_MARIADB_SUPPORT=true ." >&2
        echo "[embedded-db] Abandonner — ne pas démarrer avec une configuration incorrecte." >&2
        exit 1
    else
        MARIADB_DATA="/app/data/mariadb"
        MARIADB_SOCK="/tmp/hygie-mariadb.sock"
        PASS_FILE="/app/data/.mariadb_pass"

        # Mot de passe persistant — généré à la première exécution si non fourni
        if [ -f "$PASS_FILE" ]; then
            DB_PASS=$(cat "$PASS_FILE")
        else
            if [ -n "${EMBEDDED_MARIADB_PASSWORD:-}" ]; then
                DB_PASS="$EMBEDDED_MARIADB_PASSWORD"
            else
                DB_PASS=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 24)
            fi
            echo "$DB_PASS" > "$PASS_FILE"
            chmod 600 "$PASS_FILE"
            echo "[embedded-db] Mot de passe MariaDB généré et sauvegardé dans $PASS_FILE"
        fi

        mkdir -p "$MARIADB_DATA"

        # Première exécution : initialiser le répertoire de données
        if [ ! -d "$MARIADB_DATA/mysql" ]; then
            echo "[embedded-db] Initialisation du répertoire de données MariaDB..."

            # En mode root, chown le répertoire pour mysql puis init sous cet user
            if [ "$(id -u)" = "0" ]; then
                chown -R mysql:mysql "$MARIADB_DATA"
                INSTALL_USER="--user=mysql"
            else
                INSTALL_USER=""
            fi

            # Essayer mariadb-install-db (nom moderne) puis mysql_install_db (ancien)
            if command -v mariadb-install-db &>/dev/null; then
                mariadb-install-db \
                    $INSTALL_USER \
                    --datadir="$MARIADB_DATA" \
                    --skip-test-db \
                    --auth-root-authentication-method=normal \
                    2>&1 | grep -E "ERROR|WARNING|OK|already" || true
            elif command -v mysql_install_db &>/dev/null; then
                mysql_install_db \
                    $INSTALL_USER \
                    --datadir="$MARIADB_DATA" \
                    --skip-test-db \
                    2>&1 | grep -E "ERROR|WARNING|OK|already" || true
            else
                echo "[embedded-db] ERREUR: mariadb-install-db et mysql_install_db introuvables." >&2
                exit 1
            fi

            echo "[embedded-db] Répertoire de données initialisé."
        fi

        # Démarrer mysqld en arrière-plan
        echo "[embedded-db] Démarrage de MariaDB..."

        if [ "$(id -u)" = "0" ]; then
            MYSQLD_USER="--user=mysql"
        else
            MYSQLD_USER=""
        fi

        mysqld \
            $MYSQLD_USER \
            --datadir="$MARIADB_DATA" \
            --socket="$MARIADB_SOCK" \
            --bind-address=127.0.0.1 \
            --port=3306 \
            --skip-name-resolve \
            --character-set-server=utf8mb4 \
            --collation-server=utf8mb4_unicode_ci \
            --log-error="$MARIADB_DATA/error.log" \
            --max_allowed_packet=64M \
            --innodb_buffer_pool_size=128M \
            &

        # Attendre que MariaDB soit prêt (max 30s)
        echo -n "[embedded-db] Attente de MariaDB"
        READY=0
        for i in $(seq 1 30); do
            if mysqladmin --socket="$MARIADB_SOCK" ping --silent 2>/dev/null; then
                echo " prêt!"
                READY=1
                break
            fi
            echo -n "."
            sleep 1
        done

        if [ "$READY" = "0" ]; then
            echo ""
            echo "[embedded-db] ERREUR: MariaDB n'a pas démarré en 30s." >&2
            if [ -f "$MARIADB_DATA/error.log" ]; then
                echo "[embedded-db] Dernières lignes du log d'erreur:" >&2
                tail -20 "$MARIADB_DATA/error.log" >&2
            fi
            exit 1
        fi

        # Première exécution : créer la base et l'utilisateur
        if [ ! -f "$MARIADB_DATA/.initialized" ]; then
            echo "[embedded-db] Création de la base de données 'hygie'..."

            # Utiliser mariadb ou mysql selon ce qui est disponible
            DB_CLI="mysql"
            command -v mariadb &>/dev/null && DB_CLI="mariadb"

            $DB_CLI --socket="$MARIADB_SOCK" <<EOF
CREATE DATABASE IF NOT EXISTS hygie
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'hygie'@'127.0.0.1'
    IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON hygie.* TO 'hygie'@'127.0.0.1';
FLUSH PRIVILEGES;
EOF
            touch "$MARIADB_DATA/.initialized"
            echo "[embedded-db] Base de données 'hygie' créée."
        fi

        # Exporter l'URL de connexion pour le moteur Python
        export DATABASE_URL="mysql+aiomysql://hygie:${DB_PASS}@127.0.0.1:3306/hygie"
        echo "[embedded-db] MariaDB embarquée active — DATABASE_URL configurée automatiquement."
        echo "[embedded-db] Données dans: $MARIADB_DATA"
    fi
fi

#── Démarrage de Hygie ─────────────────────────────────────────────────────────
# IMPORTANT: --workers 1 is mandatory.
# Hygie uses asyncio.Lock() objects for scan/deletion job exclusivity.
# These locks exist only within a single Python process. Running multiple workers
# WILL cause concurrent scans, duplicate queue entries, and data corruption.
# See ARCHITECTURE.md for the full explanation.
exec uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1
