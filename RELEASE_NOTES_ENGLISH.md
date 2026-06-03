# Hygie GitHub Releases - English Patch Notes

**Goal:** Make https://github.com/Carryozor/Hygie/releases fully in English.

The detailed source of truth is `CHANGELOG.md` (now synced to latest English version).

The CI workflow (`.github/workflows/ci.yml`) has been updated to **extract the release notes section directly from CHANGELOG.md** for new tagged releases. This ensures future releases on the /releases page will be in proper English using the maintained changelog.

## How to apply to existing (published) releases

You can edit published release descriptions on GitHub (via UI or `gh` CLI).

Use the suggested bodies below (standardized English, with Docker info + summary + link to full CHANGELOG).

Run examples (after `gh auth login`):

```bash
gh release edit v3.1.1 --repo Carryozor/Hygie --title "v3.1.1" --notes-file - << 'EOF'
[ paste the body for v3.1.1 here ]
EOF
```

Repeat for each tag that has French.

## Suggested English bodies for each release (copy-paste ready)

### v3.1.2
## Docker

```yaml
image: ghcr.io/carryozor/hygie:3.1.2
```

## Release Notes

- feat: logo color reflects connected servers dynamically (v3.1.2)
- fix: docker-compose use latest tag for Arcane auto-updates

---
🐳 Docker image: `ghcr.io/carryozor/hygie:3.1.2`

Full details: see [CHANGELOG.md](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md) for v3.1.2 section.

### v3.1.1
## Docker

```yaml
image: ghcr.io/carryozor/hygie:3.1.1
```

## Release Notes

- chore: bump version to 3.1.1
- fix: 3 bugs found in the complete code review

---
🐳 Docker image: `ghcr.io/carryozor/hygie:3.1.1`

Full details: see [CHANGELOG.md](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md) for v3.1.1 section.

### v3.1.0
## Docker

```yaml
image: ghcr.io/carryozor/hygie:3.1.0
```

## Release Notes

- feat: architectural pass — v3.1 (circuit breaker, pipeline, job correlation, validator)

---
🐳 Docker image: `ghcr.io/carryozor/hygie:3.1.0`

Full details: see [CHANGELOG.md](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md) for v3.1.0 section.

### v3.0.3
## Docker

```yaml
image: ghcr.io/carryozor/hygie:3.0.3
```

## Release Notes

- fix: comprehensive quality pass — security, MariaDB parity, perf, i18n (v3.0.3)

---
🐳 Docker image: `ghcr.io/carryozor/hygie:3.0.3`

Full details: see [CHANGELOG.md](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md) for v3.0.3 section.

### v3.0.2
## Docker

```yaml
image: ghcr.io/carryozor/hygie:3.0.2
```

## Release Notes

- refactor: extract _do_scan_one_library, fix Plex scanner bugs (v3.0.2)

---
🐳 Docker image: `ghcr.io/carryozor/hygie:3.0.2`

Full details: see [CHANGELOG.md](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md) for v3.0.2 section.

### v3.0.1
## Docker

```yaml
image: ghcr.io/carryozor/hygie:3.0.1
```

## Release Notes

- fix: Plex scanner fixes, Emby overlays, multi-library scan race condition. See CHANGELOG.

---
🐳 Docker image: `ghcr.io/carryozor/hygie:3.0.1`

Full details: see [CHANGELOG.md](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md) for v3.0.1 section.

### v3.0.0
## Docker

```yaml
image: ghcr.io/carryozor/hygie:3.0.0
```

## Release Notes

See the detailed [CHANGELOG entry for v3.0.0](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md#300--2026-06-02) (major release with Vue 3 rewrite, MariaDB, expert rules, full Plex support, 8-language i18n, architecture improvements, etc.).

---
🐳 Docker image: `ghcr.io/carryozor/hygie:3.0.0`

### Older releases (v2.x)

For v2.5.8 and earlier that used French in the auto-generated "Changes" or titles:

- Standardize title to English: `## Changes` (was `## Changements`)
- Translate specific lines as follows (use in the body under `## Changes`):

Examples of translated commit-style notes:
- feat(v2.5.8): multi-language JSON i18n + customizable Discord alerts
- fix(v2.5.7): discord alerts, DB bloat, backup toggle, import fix
- fix: settings save, qbit dual test, backup disable, storage pre-warm, discord webhook tests
- fix: code review — 8 points fixed (seerr cache, uncaught int(), SSRF, retry, silent logs)

For v2.5.4 title: `## Changes` (was `## Changements`)

See the full English history in [CHANGELOG.md](https://github.com/Carryozor/Hygie/blob/main/CHANGELOG.md) (all sections from v3.0 onwards are maintained in English; older entries have been normalized for consistency).

## Future releases

After pushing a `vX.Y.Z` tag:
- The CI will automatically create/edit the GitHub release using the corresponding English section from `CHANGELOG.md`.
- The release page will show clean English "Release Notes" with the Docker snippet + the changelog excerpt + link.

No more French in the patch notes on https://github.com/Carryozor/Hygie/releases .

If you want to backfill/edit old releases, copy the suggested bodies above into `gh release edit <tag> --notes "..."` or the GitHub web UI.

