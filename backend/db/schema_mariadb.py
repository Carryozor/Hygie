"""MariaDB DDL for all Hygie tables.

Key differences from SQLite DDL:
- INTEGER PRIMARY KEY AUTOINCREMENT → INT NOT NULL AUTO_INCREMENT, PRIMARY KEY (id)
- TEXT PRIMARY KEY → VARCHAR(255) PRIMARY KEY
- DEFAULT (strftime(...)) → application provides timestamp; column DEFAULT NULL
- REAL → DOUBLE
- All tables use ENGINE=InnoDB CHARSET=utf8mb4
"""

MARIADB_TABLES: list[tuple[str, str]] = [
    (
        "settings",
        """CREATE TABLE IF NOT EXISTS settings (
            `key`  VARCHAR(255) NOT NULL,
            value  LONGTEXT     NOT NULL,
            PRIMARY KEY (`key`)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "users",
        """CREATE TABLE IF NOT EXISTS users (
            id            INT          NOT NULL AUTO_INCREMENT,
            username      VARCHAR(255) NOT NULL,
            password_hash TEXT         NOT NULL,
            created_at    VARCHAR(32)  NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_users_username (username)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "refresh_tokens",
        """CREATE TABLE IF NOT EXISTS refresh_tokens (
            id         INT          NOT NULL AUTO_INCREMENT,
            user_id    INT          NOT NULL,
            token_hash VARCHAR(255) NOT NULL,
            expires_at VARCHAR(32)  NOT NULL,
            created_at VARCHAR(32)  NOT NULL,
            revoked    TINYINT      NOT NULL DEFAULT 0,
            PRIMARY KEY (id),
            UNIQUE KEY uq_rt_token (token_hash),
            CONSTRAINT fk_rt_user FOREIGN KEY (user_id)
                REFERENCES users(id) ON DELETE CASCADE
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "libraries",
        """CREATE TABLE IF NOT EXISTS libraries (
            id              VARCHAR(255) NOT NULL,
            name            TEXT         NOT NULL,
            emby_library_id TEXT         NOT NULL,
            conditions      LONGTEXT     NOT NULL DEFAULT ('[]'),
            logic           VARCHAR(10)  NOT NULL DEFAULT 'AND',
            grace_days      INT          NOT NULL DEFAULT 7,
            seerr_conditions LONGTEXT   NOT NULL DEFAULT ('[]'),
            enabled         TINYINT      NOT NULL DEFAULT 1,
            created_at      VARCHAR(32)  DEFAULT NULL,
            server_id       VARCHAR(255) DEFAULT '0',
            deletion_unit   VARCHAR(20)  NOT NULL DEFAULT 'episode',
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "media_queue",
        """CREATE TABLE IF NOT EXISTS media_queue (
            id                  INT          NOT NULL AUTO_INCREMENT,
            emby_id             VARCHAR(255) NOT NULL,
            title               TEXT         NOT NULL,
            media_type          TEXT         NOT NULL,
            library_id          VARCHAR(255) NOT NULL,
            library_name        TEXT         NOT NULL,
            file_path           TEXT         NOT NULL,
            poster_url          TEXT         DEFAULT '',
            tmdb_id             VARCHAR(64)  DEFAULT '',
            seerr_id            INT          DEFAULT NULL,
            seerr_user_id       INT          DEFAULT NULL,
            seerr_username      TEXT         DEFAULT '',
            seerr_request_url   TEXT         DEFAULT '',
            radarr_id           INT          DEFAULT NULL,
            sonarr_id           INT          DEFAULT NULL,
            detected_at         VARCHAR(32)  NOT NULL,
            delete_at           VARCHAR(32)  NOT NULL,
            added_date          VARCHAR(32)  DEFAULT NULL,
            last_played         VARCHAR(32)  DEFAULT NULL,
            status              VARCHAR(20)  NOT NULL DEFAULT 'pending',
            notified_30d        TINYINT      DEFAULT 0,
            notified_7d         TINYINT      DEFAULT 0,
            notified_1d         TINYINT      DEFAULT 0,
            notified_now        TINYINT      DEFAULT 0,
            notified_detected   TINYINT      DEFAULT 0,
            notified_thresholds LONGTEXT     DEFAULT ('[]'),
            sonarr_series_id    INT          DEFAULT NULL,
            season_number       INT          DEFAULT NULL,
            plex_rating_key     VARCHAR(64)  DEFAULT NULL,
            view_count          INT          DEFAULT 0,
            PRIMARY KEY (id),
            UNIQUE KEY uq_mq_emby_id (emby_id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "ignored_media",
        """CREATE TABLE IF NOT EXISTS ignored_media (
            id               INT          NOT NULL AUTO_INCREMENT,
            emby_id          VARCHAR(255) NOT NULL,
            title            TEXT         NOT NULL,
            media_type       TEXT         DEFAULT 'Movie',
            library_id       VARCHAR(255) DEFAULT '',
            library_name     TEXT         DEFAULT '',
            file_path        TEXT         DEFAULT '',
            poster_url       TEXT         DEFAULT '',
            tmdb_id          VARCHAR(64)  DEFAULT '',
            seerr_id         INT          DEFAULT NULL,
            seerr_user_id    INT          DEFAULT NULL,
            seerr_username   TEXT         DEFAULT '',
            seerr_request_url TEXT        DEFAULT '',
            radarr_id        INT          DEFAULT NULL,
            sonarr_id        INT          DEFAULT NULL,
            added_date       VARCHAR(32)  DEFAULT NULL,
            last_played      VARCHAR(32)  DEFAULT NULL,
            reason           TEXT         DEFAULT '',
            ignored_at       VARCHAR(32)  NOT NULL,
            expire_at        VARCHAR(32)  DEFAULT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_im_emby_id (emby_id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "seerr_user_rules",
        """CREATE TABLE IF NOT EXISTS seerr_user_rules (
            id             INT          NOT NULL AUTO_INCREMENT,
            seerr_user_id  INT          NOT NULL,
            seerr_username VARCHAR(255) NOT NULL,
            library_id     VARCHAR(255) NOT NULL,
            grace_days     INT          NOT NULL DEFAULT 30,
            enabled        TINYINT      NOT NULL DEFAULT 1,
            discord_id     VARCHAR(255) DEFAULT '',
            created_at     VARCHAR(32)  DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "logs",
        """CREATE TABLE IF NOT EXISTS logs (
            id      INT         NOT NULL AUTO_INCREMENT,
            ts      VARCHAR(32) NOT NULL,
            level   VARCHAR(10) NOT NULL,
            source  VARCHAR(64) NOT NULL,
            message LONGTEXT    NOT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "job_history",
        """CREATE TABLE IF NOT EXISTS job_history (
            id          INT         NOT NULL AUTO_INCREMENT,
            job_type    VARCHAR(64) NOT NULL,
            started_at  VARCHAR(32) NOT NULL,
            finished_at VARCHAR(32) DEFAULT NULL,
            status      VARCHAR(20) DEFAULT NULL,
            message     LONGTEXT    DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "stats_history",
        """CREATE TABLE IF NOT EXISTS stats_history (
            id               INT         NOT NULL AUTO_INCREMENT,
            ts               VARCHAR(32) NOT NULL,
            total_deleted    INT         DEFAULT 0,
            total_scanned    INT         DEFAULT 0,
            space_freed_bytes BIGINT     DEFAULT 0,
            month            VARCHAR(7)  NOT NULL,
            library_id       VARCHAR(255) DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "rate_limit",
        """CREATE TABLE IF NOT EXISTS rate_limit (
            `key` VARCHAR(255) NOT NULL,
            ts    DOUBLE       NOT NULL
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "expert_rules",
        """CREATE TABLE IF NOT EXISTS expert_rules (
            id         INT          NOT NULL AUTO_INCREMENT,
            name       TEXT         NOT NULL,
            library_id INT          DEFAULT NULL,
            conditions LONGTEXT     NOT NULL DEFAULT ('[]'),
            operator   VARCHAR(10)  NOT NULL DEFAULT 'AND',
            action     VARCHAR(20)  NOT NULL DEFAULT 'queue',
            enabled    TINYINT      NOT NULL DEFAULT 1,
            priority   INT          NOT NULL DEFAULT 0,
            created_at VARCHAR(32)  DEFAULT NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
    (
        "notifications",
        """CREATE TABLE IF NOT EXISTS notifications (
            id       INT         NOT NULL AUTO_INCREMENT,
            media_id INT         NOT NULL,
            threshold VARCHAR(20) NOT NULL,
            sent_at  VARCHAR(32) DEFAULT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_notif (media_id, threshold),
            CONSTRAINT fk_notif_media FOREIGN KEY (media_id)
                REFERENCES media_queue (id) ON DELETE CASCADE
        ) ENGINE=InnoDB CHARSET=utf8mb4""",
    ),
]

MARIADB_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_rt_user ON refresh_tokens(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_logs_ts        ON logs(ts)",
    "CREATE INDEX IF NOT EXISTS idx_media_status   ON media_queue(status)",
    "CREATE INDEX IF NOT EXISTS idx_media_delete_at ON media_queue(delete_at)",
    "CREATE INDEX IF NOT EXISTS idx_media_emby_id  ON media_queue(emby_id)",
    "CREATE INDEX IF NOT EXISTS idx_media_lib_id   ON media_queue(library_id)",
    "CREATE INDEX IF NOT EXISTS idx_ignored_emby   ON ignored_media(emby_id)",
    "CREATE INDEX IF NOT EXISTS idx_rate_limit_key ON rate_limit(`key`, ts)",
    "CREATE INDEX IF NOT EXISTS idx_notif_media    ON notifications(media_id)",
]
