#!/usr/bin/env bash
# Chien de garde de l'assistant (lancé toutes les 5 min par systemd).
#
# - Vérifie l'état et la santé (healthcheck Docker) de chaque conteneur.
# - Répare seul : redémarrage, puis recréation si besoin (cas « task already exists » de Docker).
# - Alerte DIRECTEMENT via l'API Telegram, sans passer par n8n ni par le relais
#   (ils peuvent être eux-mêmes en panne), et seulement quand l'état CHANGE.
#
# Pour le mettre en sommeil (maintenance) : touch ~/.local/state/assistant-chien-de-garde/pause
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

SERVICES=(n8n relais-telegram cerveau voix studio)
ETAT="${XDG_STATE_HOME:-$HOME/.local/state}/assistant-chien-de-garde"
mkdir -p "$ETAT" && chmod 700 "$ETAT"
[ -e "$ETAT/pause" ] && { echo "En pause (fichier $ETAT/pause présent)."; exit 0; }

# État publié pour le tableau de bord (n8n le lit en lecture seule)
PUBLIC="${XDG_STATE_HOME:-$HOME/.local/state}/assistant-etat"
mkdir -p "$PUBLIC" && chmod 755 "$PUBLIC"

journal() {
  echo "$(date '+%F %T') $*"
  echo "$(date '+%F %T') $*" >> "$PUBLIC/journal.log"
  tail -n 30 "$PUBLIC/journal.log" > "$PUBLIC/journal.tmp" && mv "$PUBLIC/journal.tmp" "$PUBLIC/journal.log"
}

publier_etat() {
  local stats premier=1
  stats=$(docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}' 2>/dev/null)
  {
    printf '{"date":"%s","services":{' "$(date -Iseconds)"
    for svc in "${SERVICES[@]}"; do
      [ $premier = 1 ] || printf ','
      premier=0
      printf '"%s":{"etat":"%s","depuis":"%s","memoire":"%s","redemarrages":%s}' "$svc" "$(etat "$svc")" \
        "$(docker inspect -f '{{.State.StartedAt}}' "$svc" 2>/dev/null)" \
        "$(echo "$stats" | grep "^$svc|" | cut -d'|' -f2 | sed 's/ //g')" \
        "$(docker inspect -f '{{.RestartCount}}' "$svc" 2>/dev/null || echo 0)"
    done
    printf '}}\n'
  } > "$PUBLIC/etat.tmp" && mv "$PUBLIC/etat.tmp" "$PUBLIC/etat.json"
  chmod 600 "$PUBLIC/etat.json" "$PUBLIC/journal.log" 2>/dev/null || true
}

alerter() {
  local texte="$1" token chat
  journal "ALERTE : $texte"
  notify-send -u critical "Assistant" "$texte" 2>/dev/null || true
  token=$(grep -E '^TELEGRAM_TOKEN=' relais.env | cut -d= -f2-)
  chat=$(grep -oE 'CHAT_AUTORISE=[0-9]+' docker-compose.yml | cut -d= -f2)
  if [ -z "$token" ] || [ -z "$chat" ]; then journal "Alerte Telegram impossible : token ou chat id introuvable"; return; fi
  # Le token passe par l'entrée standard (curl -K -) : il n'apparaît jamais dans la liste des processus
  printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" |
    curl -s -m 20 -K - --data-urlencode "chat_id=$chat" --data-urlencode "text=$texte" -o /dev/null -w "%{http_code}" |
    grep -q 200 || journal "Échec de l'envoi de l'alerte Telegram"
}

etat() { docker inspect -f '{{.State.Status}}{{if .State.Health}}/{{.State.Health.Status}}{{end}}' "$1" 2>/dev/null || echo "absent"; }

# « starting » = healthcheck pas encore conclu (démarrage récent) : on laisse sa chance au conteneur
sain() { case "$(etat "$1")" in running | running/healthy | running/starting) return 0 ;; *) return 1 ;; esac; }

reparer() {
  local svc="$1"
  docker compose up -d "$svc" >/dev/null 2>&1
  [ "$(etat "$svc")" = "running/unhealthy" ] && docker compose restart "$svc" >/dev/null 2>&1
  sleep 20
  sain "$svc" && return 0
  # Dernier recours : nouveau conteneur, même configuration
  docker compose up -d --force-recreate "$svc" >/dev/null 2>&1
  sleep 30
  sain "$svc"
}

# Docker lui-même répond-il ?
if ! docker info >/dev/null 2>&1; then
  [ -e "$ETAT/docker" ] || { alerter "🚨 Docker ne répond plus : tout l'assistant est arrêté. Intervention nécessaire sur le PC."; touch "$ETAT/docker"; }
  exit 1
fi
[ -e "$ETAT/docker" ] && { rm -f "$ETAT/docker"; alerter "✅ Docker répond de nouveau."; }

for svc in "${SERVICES[@]}"; do
  if sain "$svc"; then
    [ -e "$ETAT/$svc" ] && { rm -f "$ETAT/$svc"; alerter "✅ $svc est rétabli."; }
    continue
  fi
  avant=$(etat "$svc")
  [ -e "$ETAT/$svc" ] && continue # déjà signalé, réparation déjà tentée : on n'insiste pas à chaque passage
  journal "$svc en panne ($avant) : réparation…"
  if reparer "$svc"; then
    alerter "🔧 $svc était en panne ($avant) : redémarré automatiquement ✅"
  else
    touch "$ETAT/$svc"
    alerter "🚨 $svc est en panne ($avant) et la réparation automatique a échoué. Intervention nécessaire : docker compose logs $svc"
  fi
done

publier_etat
