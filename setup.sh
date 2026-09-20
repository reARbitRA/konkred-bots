#!/usr/bin/env bash
###############################################################################
# Konkred multi-bot AI ecosystem - one-command bootstrap.
#
#   ./setup.sh            build and start everything
#   ./setup.sh --logs     start, then follow the logs
#   ./setup.sh --down     stop the stack
#   ./setup.sh --status   show health without rebuilding
###############################################################################
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Colours (disabled when not a TTY) ---------------------------------------
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; BLUE=$'\033[34m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=''; RED=''; GREEN=''; YELLOW=''; BLUE=''; DIM=''; RESET=''
fi

info()  { printf '%s▶%s %s\n' "$BLUE"  "$RESET" "$*"; }
ok()    { printf '%s✅%s %s\n' "$GREEN" "$RESET" "$*"; }
warn()  { printf '%s⚠️ %s %s\n' "$YELLOW" "$RESET" "$*"; }
err()   { printf '%s❌%s %s\n' "$RED"   "$RESET" "$*" >&2; }
die()   { err "$*"; exit 1; }

trap 'err "setup failed on line $LINENO"' ERR

banner() {
  printf '%s\n' "${BOLD}${BLUE}"
  cat <<'ART'
  _  __          _            _
 | |/ /___ _ __ | | ___ __ ___  __| |
 | ' // _ \ '_ \| |/ / '__/ _ \/ _` |
 | . \  __/ | | |   <| | |  __/ (_| |
 |_|\_\___|_| |_|_|\_\_|  \___|\__,_|
        multi-bot AI ecosystem
ART
  printf '%s\n' "$RESET"
}

# --- Docker detection --------------------------------------------------------
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
  else
    die "Docker Compose not found. Install Docker Desktop or the compose plugin: https://docs.docker.com/get-docker/"
  fi
}

require_docker() {
  command -v docker >/dev/null 2>&1 \
    || die "Docker is not installed. Get it from https://docs.docker.com/get-docker/"
  docker info >/dev/null 2>&1 \
    || die "The Docker daemon is not running. Start Docker and try again."
  detect_compose
  ok "Docker ready ($(docker --version | cut -d, -f1))"
}

# --- .env handling -----------------------------------------------------------
require_env() {
  if [[ ! -f .env ]]; then
    [[ -f .env.example ]] || die ".env.example is missing - cannot create .env"
    cp .env.example .env
    warn "No .env found, so I created one from .env.example."
    printf '\n   %sEdit .env and add at least one Telegram bot token, then re-run ./setup.sh%s\n\n' "$BOLD" "$RESET"
    printf '   Minimum to be useful:\n'
    printf '     • one TELEGRAM_*_BOT_TOKEN   (from @BotFather)\n'
    printf '     • one AI provider key        (GEMINI_KEY_P1 or GROQ_API_KEY)\n\n'
    exit 0
  fi
  ok ".env present"
}

check_tokens() {
  local found=0 missing=()
  local names=(VOICE PDF IELTS CONTENT CRYPTO)
  for name in "${names[@]}"; do
    local var="TELEGRAM_${name}_BOT_TOKEN"
    local value
    value="$(grep -E "^${var}=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'\''' | xargs || true)"
    if [[ -n "$value" ]]; then
      ok "${name} bot token configured"
      found=$((found + 1))
    else
      missing+=("$name")
    fi
  done

  if (( found == 0 )); then
    err "No Telegram bot tokens found in .env - every bot would stay offline."
    printf '   Add at least one TELEGRAM_*_BOT_TOKEN, then re-run.\n'
    exit 1
  fi
  (( ${#missing[@]} )) && warn "Not configured (will stay offline): ${missing[*]}"

  # Provider keys are optional: warn but continue, the mock provider covers it.
  local providers=0
  for var in GEMINI_KEY_P1 GEMINI_KEY_P2 GEMINI_KEY_P3 GROQ_API_KEY CEREBRAS_API_KEY \
             MISTRAL_API_KEY OPENROUTER_API_KEY CF_API_TOKEN GITHUB_TOKEN; do
    local value
    value="$(grep -E "^${var}=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'\''' | xargs || true)"
    [[ -n "$value" ]] && providers=$((providers + 1))
  done
  if (( providers == 0 )); then
    warn "No AI provider keys set - the gateway will answer from its built-in mock provider."
    warn "Add GEMINI_KEY_P1 or GROQ_API_KEY to get real model output."
  else
    ok "${providers} AI provider credential(s) configured"
  fi
}

# --- Health reporting --------------------------------------------------------
show_status() {
  printf '\n%s── Container status ──%s\n' "$BOLD" "$RESET"
  "${COMPOSE[@]}" ps
  printf '\n%s── Health ──%s\n' "$BOLD" "$RESET"
  for service in redis gateway bot; do
    local cid state health
    cid="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true)"
    if [[ -z "$cid" ]]; then
      printf '  %-9s %s(not running)%s\n' "$service" "$DIM" "$RESET"
      continue
    fi
    state="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null || echo unknown)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$cid" 2>/dev/null || echo n/a)"
    case "$health" in
      healthy)   printf '  %-9s %s%s%s (%s)\n' "$service" "$GREEN" "$state" "$RESET" "$health" ;;
      unhealthy) printf '  %-9s %s%s%s (%s)\n' "$service" "$RED" "$state" "$RESET" "$health" ;;
      *)         printf '  %-9s %s (%s)\n' "$service" "$state" "$health" ;;
    esac
  done
}

wait_for_health() {
  local port
  port="$(grep -E '^GATEWAY_PORT=' .env 2>/dev/null | head -1 | cut -d= -f2- | xargs || true)"
  port="${port:-3000}"

  info "Waiting for the gateway to report healthy…"
  for _ in $(seq 1 60); do
    if curl -fsS "http://localhost:${port}/api/health" >/dev/null 2>&1; then
      ok "Gateway is answering on http://localhost:${port}"
      curl -fsS "http://localhost:${port}/api/health" 2>/dev/null \
        | (python3 -m json.tool 2>/dev/null || cat) \
        | head -20 || true
      return 0
    fi
    sleep 2
  done
  warn "The gateway did not respond within 120s. Check: ${COMPOSE[*]} logs gateway"
  return 0
}

print_next_steps() {
  local port
  port="$(grep -E '^GATEWAY_PORT=' .env 2>/dev/null | head -1 | cut -d= -f2- | xargs || true)"
  port="${port:-3000}"
  local admin
  admin="$(grep -E '^ADMIN_KEY=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'\''' | xargs || true)"

  printf '\n%s%s─────────────────────────────────────────────────%s\n' "$BOLD" "$GREEN" "$RESET"
  printf '%s  Konkred is running%s\n' "$BOLD" "$RESET"
  printf '%s%s─────────────────────────────────────────────────%s\n\n' "$BOLD" "$GREEN" "$RESET"
  printf '  Open Telegram and send /start to your bot(s).\n\n'
  printf '  %sEndpoints%s\n' "$BOLD" "$RESET"
  printf '    health     http://localhost:%s/api/health\n' "$port"
  printf '    models     http://localhost:%s/api/models\n' "$port"
  printf '    meta       http://localhost:%s/api/meta\n' "$port"
  if [[ -n "$admin" ]]; then
    printf '    dashboard  http://localhost:%s/api/admin/dashboard  %s(send header x-admin-key)%s\n' "$port" "$DIM" "$RESET"
  else
    printf '    dashboard  %sdisabled - set ADMIN_KEY in .env to enable%s\n' "$DIM" "$RESET"
  fi
  printf '\n  %sCommands%s\n' "$BOLD" "$RESET"
  printf '    logs       %s logs -f\n' "${COMPOSE[*]}"
  printf '    bot logs   %s logs -f bot\n' "${COMPOSE[*]}"
  printf '    restart    %s restart\n' "${COMPOSE[*]}"
  printf '    stop       ./setup.sh --down\n\n'
}

# --- Entry point -------------------------------------------------------------
main() {
  case "${1:-}" in
    --down|down)
      require_docker
      info "Stopping the stack…"
      "${COMPOSE[@]}" down
      ok "Stopped."
      exit 0
      ;;
    --status|status)
      require_docker
      show_status
      exit 0
      ;;
    --help|-h)
      sed -n '3,9p' "$0" | sed 's/^#\{1,\} \{0,1\}//'
      exit 0
      ;;
  esac

  banner
  require_docker
  require_env
  check_tokens

  info "Validating the compose file…"
  "${COMPOSE[@]}" config --quiet
  ok "docker-compose.yml is valid"

  info "Building images (first run pulls base images, this can take a few minutes)…"
  "${COMPOSE[@]}" build

  info "Starting services…"
  "${COMPOSE[@]}" up -d

  show_status
  wait_for_health
  print_next_steps

  if [[ "${1:-}" == "--logs" ]]; then
    info "Following logs (Ctrl+C to detach - containers keep running)…"
    "${COMPOSE[@]}" logs -f
  fi
}

main "$@"
