#!/usr/bin/env bash
# Signage Portal: install, update, back up, restore and uninstall (Docker).
#
#   curl -fsSL https://raw.githubusercontent.com/xattribution/signage-portal/main/install.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/xattribution/signage-portal/main/install.sh | sudo bash -s update
#   curl -fsSL https://raw.githubusercontent.com/xattribution/signage-portal/main/install.sh | sudo bash -s backup
#   curl -fsSL https://raw.githubusercontent.com/xattribution/signage-portal/main/install.sh | sudo bash -s restore FILE
#   curl -fsSL https://raw.githubusercontent.com/xattribution/signage-portal/main/install.sh | sudo bash -s uninstall
#
# After the first install the same commands are available as `sudo signage <command>`.
#
# The portal installs to ~/signage for the user who runs sudo (an existing /opt/signage
# install keeps working). Set SIGNAGE_DIR to choose another folder.
#
# Backups hold the database (accounts, settings, streams, displays, sessions), the secret key,
# uploaded and converted media, .env, /etc/signage (NAS shares, storage checks, credentials)
# and the systemd units. They work for both the Docker-volume quick start and the
# host-storage/NAS layout (COMPOSE_FILE in .env includes docker-compose.host-storage.yml).
set -Eeuo pipefail

REPO="${SIGNAGE_REPO:-xattribution/signage-portal}"
REF="${SIGNAGE_REF:-main}"
# Who ran sudo: the install folder lives in that user's home and the code belongs to them.
OWNER="${SUDO_USER:-}"
[ "$OWNER" = "root" ] && OWNER=""
OWNER_HOME="$( { [ -n "$OWNER" ] && getent passwd "$OWNER" | cut -d: -f6; } || true)"
OWNER_HOME="${OWNER_HOME:-$HOME}"
SELF="$(readlink -f "${BASH_SOURCE[0]:-}" 2>/dev/null || true)"
if [ -n "${SIGNAGE_DIR:-}" ]; then
  DIR="$SIGNAGE_DIR"
elif [ -f "$SELF" ] && [ -f "$(dirname "$SELF")/docker-compose.yml" ]; then
  DIR="$(dirname "$SELF")"            # `sudo signage ...` manages the copy it belongs to
elif [ -f /opt/signage/docker-compose.yml ] && [ ! -e "$OWNER_HOME/signage" ]; then
  DIR=/opt/signage                    # earlier installs used /opt/signage
else
  DIR="$OWNER_HOME/signage"
fi
BACKUP_DIR="${SIGNAGE_BACKUP_DIR:-/var/backups/signage}"
PROJECT="signage"
LINK="/usr/local/bin/signage"
FORMAT=1

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
trap 'printf "\033[1;31mError:\033[0m stopped at line %s (%s).\n" "$LINENO" "$BASH_COMMAND" >&2' ERR

usage() {
  cat <<EOF
Usage: sudo signage <command>

  install                 Install Docker if needed, download the portal to $DIR and start it
  update                  Back up the database and settings, download the latest version and rebuild
  backup [--config-only]  Write a backup to $BACKUP_DIR (--config-only skips media)
  restore FILE [--no-start]
                          Restore a backup, on this machine or a new one
  uninstall [--purge] [--no-backup]
                          Remove the containers, images and code. Data is kept unless --purge
EOF
}

need_root() { [ "$(id -u)" -eq 0 ] || die "Run this with sudo."; }

# Read KEY from .env without executing it.
env_get() {
  local key="$1" file="${2:-$DIR/.env}"
  [ -f "$file" ] || return 0
  grep -E "^[[:space:]]*${key}=" "$file" | tail -n1 | sed -E "s/^[[:space:]]*${key}=//; s/^['\"]//; s/['\"][[:space:]]*$//" || true
}

port() { local p; p="$(env_get SIGNAGE_PORT)"; echo "${p:-51480}"; }

host_mode() { case "$(env_get COMPOSE_FILE)" in *host-storage*) return 0 ;; *) return 1 ;; esac; }

compose() { (cd "$DIR" && docker compose "$@"); }

systemd_managed() { command -v systemctl >/dev/null 2>&1 && systemctl is-enabled --quiet signage.service 2>/dev/null; }

running() {
  if systemd_managed; then systemctl is-active --quiet signage.service; return; fi
  [ -f "$DIR/docker-compose.yml" ] && [ -n "$(compose ps --status running -q 2>/dev/null)" ]
}

stack_stop() {
  if systemd_managed; then systemctl stop signage.timer signage.service 2>/dev/null || true
  elif [ -f "$DIR/docker-compose.yml" ]; then compose stop >/dev/null 2>&1 || true; fi
}

stack_start() {
  if systemd_managed; then
    (cd "$DIR" && docker compose --project-name "$PROJECT" build) && systemctl start signage.service signage.timer
  else
    compose up -d --build --remove-orphans
  fi
}

# Restart after a backup without rebuilding images.
stack_resume() {
  if systemd_managed; then systemctl start signage.service signage.timer
  else compose start >/dev/null; fi
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    say "Docker is not installed. Installing it with Docker's official script (get.docker.com)."
    curl -fsSL https://get.docker.com | sh
    command -v systemctl >/dev/null 2>&1 && systemctl enable --now docker >/dev/null 2>&1 || true
  fi
  docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing. Install docker-compose-plugin and run this again."
  docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start it (systemctl start docker) and run this again."
}

# Download the source into a new folder and swap it in. Anything in the old folder that is
# not part of the source (.env, overrides, local data folders) is carried over untouched.
fetch_code() {
  local tmp new
  tmp="$(mktemp -d)"
  new="$DIR.new"
  say "Downloading $REPO ($REF)."
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$REF" -o "$tmp/src.tar.gz" \
    || die "Could not download https://github.com/$REPO ($REF)."
  rm -rf "$new"; mkdir -p "$new"
  tar -xzf "$tmp/src.tar.gz" -C "$new" --strip-components=1
  rm -rf "$tmp"
  [ -f "$new/docker-compose.yml" ] || die "The download does not look like the signage portal."
  # The code belongs to the user who ran sudo, so .env can be edited without sudo.
  # Carried-over items (below) keep their own ownership; data folders stay as they are.
  [ -n "$OWNER" ] && chown -R "$OWNER": "$new"
  if [ -d "$DIR" ]; then
    local item name
    for item in "$DIR"/* "$DIR"/.[!.]*; do
      [ -e "$item" ] || continue
      name="$(basename "$item")"
      [ -e "$new/$name" ] || mv "$item" "$new/$name"
    done
    rm -rf "$DIR.old"; mv "$DIR" "$DIR.old"
  fi
  mv "$new" "$DIR"
  rm -rf "$DIR.old"
  chmod 0755 "$DIR/install.sh"
  ln -sf "$DIR/install.sh" "$LINK"
  [ -f "$DIR/.env" ] && chmod 0600 "$DIR/.env"
  return 0
}

wait_healthy() {
  local p h
  p="$(port)"; h="$(env_get SIGNAGE_BIND)"
  case "$h" in ""|0.0.0.0|"::") h=127.0.0.1 ;; esac
  say "Waiting for the portal to answer on port $p."
  for _ in $(seq 1 90); do
    curl -fsS "http://$h:$p/healthz" >/dev/null 2>&1 && return 0
    sleep 2
  done
  warn "The portal did not answer within 3 minutes. Check: cd $DIR && docker compose logs web"
  return 1
}

address() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -n "$ip" ] || ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')"
  echo "http://${ip:-<server-ip>}:$(port)"
}

# ---------------------------------------------------------------------------
cmd_install() {
  need_root
  if [ -f "$DIR/docker-compose.yml" ]; then
    say "The portal is already installed in $DIR. Updating it instead."
    cmd_update; return
  fi
  ensure_docker
  fetch_code
  say "Building and starting. The first build takes a few minutes."
  compose up -d --build
  wait_healthy || true
  say "Installed. Open $(address) in a browser to finish setup."
  say "Later: sudo signage update | backup | restore FILE | uninstall"
}

cmd_update() {
  need_root
  [ -f "$DIR/docker-compose.yml" ] || die "The portal is not installed in $DIR. Run the install command first."
  ensure_docker
  say "Backing up the database and settings before updating."
  cmd_backup --config-only
  fetch_code
  say "Rebuilding and restarting."
  stack_start
  docker image prune -f >/dev/null 2>&1 || true
  wait_healthy || true
  say "Updated. The portal is at $(address)"
}

# Volume mountpoint for one compose volume, or empty when it does not exist.
volume_path() { docker volume inspect -f '{{ .Mountpoint }}' "${PROJECT}_signage-$1" 2>/dev/null || true; }

ensure_volume() {
  docker volume inspect "${PROJECT}_signage-$1" >/dev/null 2>&1 && return 0
  docker volume create --label "com.docker.compose.project=$PROJECT" \
    --label "com.docker.compose.volume=signage-$1" "${PROJECT}_signage-$1" >/dev/null
}

# Where each part lives on this host.
part_path() {
  local part="$1"
  if host_mode; then
    case "$part" in
      data) env_get DATA_ROOT ;;
      originals|rendered) local m; m="$(env_get MEDIA_ROOT)"; [ -n "$m" ] && echo "$m/$part" ;;
    esac
  else
    volume_path "$part"
  fi
}

append_dir() {  # append_dir ARCHIVE NAME SOURCE_DIR
  tar -rf "$1" --numeric-owner --transform "s,^\.,$2," -C "$3" .
}

cmd_backup() {
  need_root
  local config_only=0
  [ "${1:-}" = "--config-only" ] && config_only=1
  [ -d "$DIR" ] || die "The portal is not installed in $DIR."
  command -v docker >/dev/null 2>&1 || die "Docker is not installed."
  mkdir -p "$BACKUP_DIR"; chmod 0700 "$BACKUP_DIR"
  local stamp out stage was_running data
  stamp="$(date +%Y%m%d-%H%M%S)"
  local suffix=""
  [ $config_only = 1 ] && suffix="-config"
  out="$BACKUP_DIR/signage-$(hostname -s)-$stamp$suffix.tar"
  stage="$(mktemp -d)"
  umask 077

  mkdir -p "$stage/config"
  {
    echo "format=$FORMAT"
    echo "created=$(date -Is)"
    echo "host=$(hostname)"
    echo "layout=$(host_mode && echo host-storage || echo volumes)"
    echo "media=$([ $config_only = 1 ] && echo no || echo yes)"
  } > "$stage/manifest.txt"
  [ -f "$DIR/.env" ] && cp -p "$DIR/.env" "$stage/config/env"
  [ -d /etc/signage ] && cp -a /etc/signage "$stage/config/etc-signage"
  for unit in signage.service signage.timer; do
    [ -f "/etc/systemd/system/$unit" ] && cp -p "/etc/systemd/system/$unit" "$stage/config/$unit"
  done
  [ -f /etc/fstab ] && cp -p /etc/fstab "$stage/config/fstab-reference"
  tar -cf "$out" --numeric-owner -C "$stage" manifest.txt config
  rm -rf "$stage"

  data="$(part_path data)"
  [ -n "$data" ] && [ -d "$data" ] || die "Cannot find the portal's data folder. Nothing was backed up."
  was_running=0; running && was_running=1
  # Stop briefly so the database is copied in a consistent state.
  [ $was_running = 1 ] && stack_stop
  if ! append_dir "$out" data "$data"; then
    [ $was_running = 1 ] && stack_resume
    die "Copying the data folder failed."
  fi
  if [ $was_running = 1 ]; then stack_resume || warn "The portal did not restart. Start it with: cd $DIR && docker compose up -d"; fi

  if [ $config_only = 0 ]; then
    local part p
    for part in originals rendered; do
      p="$(part_path "$part")"
      if [ -n "$p" ] && [ -d "$p" ]; then append_dir "$out" "$part" "$p"
      else warn "No $part media folder found; skipping it."; fi
    done
  fi
  chmod 0600 "$out"
  say "Backup written to $out ($(du -h "$out" | cut -f1))."
  say "It contains password hashes and NAS credentials. Keep it somewhere private."
  # Keep the 10 most recent automatic pre-update backups.
  # Backup names are generated above (no spaces), so sorting by name is sorting by date.
  find "$BACKUP_DIR" -maxdepth 1 -name 'signage-*-config.tar' -printf '%f\n' | sort -r | tail -n +11 \
    | while read -r old; do rm -f "$BACKUP_DIR/$old"; done
}

extract_part() {  # extract_part ARCHIVE NAME TARGET WIPE
  local archive="$1" part="$2" target="$3" wipe="$4"
  tar -tf "$archive" "$part" >/dev/null 2>&1 || return 0
  [ "$wipe" = 1 ] && find "$target" -mindepth 1 -delete
  tar -xf "$archive" --numeric-owner -C "$target" --strip-components=1 "$part"
  say "Restored $part."
}

cmd_restore() {
  need_root
  local archive="${1:-}" start=1
  [ "${2:-}" = "--no-start" ] && start=0
  [ -n "$archive" ] || die "Name the backup file: sudo signage restore /var/backups/signage/<file>.tar"
  [ -f "$archive" ] || die "Backup file not found: $archive"
  archive="$(readlink -f "$archive")"
  local manifest
  manifest="$(tar -xOf "$archive" manifest.txt 2>/dev/null)" || die "$archive is not a signage backup."
  grep -q "^format=$FORMAT$" <<<"$manifest" || die "$archive was made by an incompatible version."
  ensure_docker
  [ -f "$DIR/docker-compose.yml" ] || fetch_code
  stack_stop

  local stage
  stage="$(mktemp -d)"
  tar -xf "$archive" -C "$stage" config
  if [ -f "$stage/config/env" ]; then
    [ -f "$DIR/.env" ] && cp -p "$DIR/.env" "$DIR/.env.before-restore"
    install -m 0600 "$stage/config/env" "$DIR/.env"
    say "Restored .env."
  fi
  if [ -d "$stage/config/etc-signage" ]; then
    [ -d /etc/signage ] && { rm -rf /etc/signage.before-restore; cp -a /etc/signage /etc/signage.before-restore; }
    mkdir -p /etc/signage; cp -a "$stage/config/etc-signage/." /etc/signage/
    say "Restored /etc/signage (NAS shares, storage checks, credentials)."
  fi
  local unit restored_units=0
  for unit in signage.service signage.timer; do
    [ -f "$stage/config/$unit" ] && { install -m 0644 "$stage/config/$unit" "/etc/systemd/system/$unit"; restored_units=1; }
  done
  if [ $restored_units = 1 ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload 2>/dev/null || warn "Restored the systemd units, but systemd did not reload. Run: systemctl daemon-reload"
  fi
  rm -rf "$stage"

  # Check every destination before changing anything, so a missing NAS mount aborts cleanly.
  local part target
  declare -A targets=()
  for part in data originals rendered; do
    if host_mode; then
      target="$(part_path "$part")"
      [ -n "$target" ] && [ -d "$target" ] || die "The $part folder (${target:-not set in .env}) does not exist. Nothing was restored except configuration. Mount the NAS and create it first (see docs/PERSISTENCE.md); /etc/fstab from the old host is saved in the backup as config/fstab-reference."
    else
      ensure_volume "$part"; target="$(volume_path "$part")"
    fi
    targets[$part]="$target"
  done
  for part in data originals rendered; do
    target="${targets[$part]}"
    # The database folder is replaced outright so stale SQLite journal files cannot mix in.
    extract_part "$archive" "$part" "$target" "$([ "$part" = data ] && echo 1 || echo 0)"
    if ! host_mode; then
      chown 1000:1000 "$target"   # The containers run as uid 1000.
      [ "$part" = data ] && chmod 0700 "$target"
    fi
  done
  if [ $start = 1 ]; then
    if systemd_managed; then systemctl enable signage.service signage.timer >/dev/null 2>&1 || true; fi
    stack_start
    wait_healthy || true
    say "Restore complete. The portal is at $(address)"
  else
    say "Restore complete. Start it with: cd $DIR && docker compose up -d --build"
  fi
}

cmd_uninstall() {
  need_root
  local purge=0 backup=1 arg
  for arg in "$@"; do
    case "$arg" in --purge) purge=1 ;; --no-backup) backup=0 ;; *) die "Unknown option $arg" ;; esac
  done
  [ -d "$DIR" ] || die "The portal is not installed in $DIR."
  if [ $purge = 1 ] && [ $backup = 1 ]; then
    say "Making a full backup before deleting data (skip with --no-backup)."
    cmd_backup
  fi
  local hm=0
  host_mode && hm=1
  if systemd_managed; then
    systemctl disable --now signage.timer signage.service >/dev/null 2>&1 || true
  fi
  if command -v docker >/dev/null 2>&1 && [ -f "$DIR/docker-compose.yml" ]; then
    local down=(down --remove-orphans --rmi all)
    [ $purge = 1 ] && down+=(--volumes)
    compose "${down[@]}" || true
  fi
  rm -f "$LINK"
  rm -rf "$DIR"
  if [ $purge = 1 ]; then
    say "Removed the portal and its Docker volumes. Backups in $BACKUP_DIR were kept."
    if [ $hm = 1 ]; then warn "Host and NAS folders named in the old .env were not touched."; fi
  else
    say "Removed the portal. Your data is still in the Docker volumes (or host/NAS folders),"
    say "so installing again brings everything back. Delete it all with: uninstall --purge"
  fi
}

# ---------------------------------------------------------------------------
main() {
  local cmd="${1:-install}"
  [ $# -gt 0 ] && shift
  case "$cmd" in
    install)   cmd_install ;;
    update)    cmd_update ;;
    backup)    cmd_backup "$@" ;;
    restore)   cmd_restore "$@" ;;
    uninstall) cmd_uninstall "$@" ;;
    -h|--help|help) usage ;;
    *) usage; exit 1 ;;
  esac
}
# Everything above is only definitions, so the whole script is read before anything runs
# (safe for curl | bash). Nothing here reads from the terminal.
main "$@" </dev/null
