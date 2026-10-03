#!/usr/bin/env bash
# Démarre le proxy Caddy (accès au tableau de bord depuis le réseau local) UNIQUEMENT
# quand le PC est sur le réseau de la maison, et l'arrête partout ailleurs.
# La maison est reconnue par le nom de la connexion ET l'adresse MAC de la box :
# un faux point d'accès portant le même nom (« evil twin ») ne suffit pas.
# Lancé chaque minute par systemd (assistant-maison.timer).
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
source reseau.env

journal() { echo "$(date '+%F %T') $*"; }

a_la_maison() {
  nmcli -t -f NAME connection show --active 2>/dev/null | grep -qxF "$CONNEXION_MAISON" || return 1
  local gw mac
  gw=$(ip route show default | awk '{print $3; exit}')
  mac=$(ip neigh show "$gw" 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "lladdr") print $(i + 1)}')
  [ "${mac,,}" = "${MAC_BOX,,}" ]
}

en_marche=$(docker inspect -f '{{.State.Running}}' caddy 2>/dev/null || echo false)

if a_la_maison; then
  LAN_IP=$(ip route get 1.1.1.1 | awk '{for (i = 1; i <= NF; i++) if ($i == "src") print $(i + 1)}')
  export LAN_IP NOM_HOTE="$(hostname)" RESEAU
  # (Re)démarre si arrêté, ou si l'adresse IP du PC a changé
  if [ "$en_marche" != true ] || ! docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' caddy | grep -qx "LAN_IP=$LAN_IP"; then
    docker compose --profile maison up -d caddy >/dev/null 2>&1 && journal "Maison détectée : tableau de bord accessible sur https://$LAN_IP:8443"
  fi
elif [ "$en_marche" = true ]; then
  docker compose --profile maison stop caddy >/dev/null 2>&1 && journal "Hors de la maison : accès réseau au tableau de bord coupé"
fi
