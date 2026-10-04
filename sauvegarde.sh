#!/usr/bin/env bash
# Sauvegarde quotidienne de l'assistant
#   - workflows      -> ce dépôt Git (privé), puis push
#   - identifiants   -> disque local uniquement, chiffrés par n8n (clé dans .env)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

LOCAL="$HOME/.local/share/assistant-sauvegardes"
GARDER=14 # nombre de sauvegardes d'identifiants conservées

echec() {
  notify-send -u critical "Sauvegarde assistant" "Échec : $1" 2>/dev/null || true
  echo "ÉCHEC : $1" >&2
  exit 1
}
trap 'echec "ligne $LINENO"' ERR

docker inspect -f '{{.State.Running}}' n8n 2>/dev/null | grep -q true || echec "le conteneur n8n ne tourne pas"

# 1. Workflows (un fichier par workflow, sans les workflows de TEST)
tmp=$(docker exec n8n mktemp -d)
docker exec n8n n8n export:workflow --all --separate --pretty --output="$tmp" >/dev/null
rm -f workflows/*.json
docker cp -q "n8n:$tmp/." workflows/
docker exec n8n rm -rf "$tmp"
# - suppression des workflows de TEST
# - suppression des données internes (staticData) : mémoire de conversation du bot,
#   CVE déjà envoyées... Ces données sont privées et n'ont rien à faire dans Git.
python3 - <<'EOF'
import json, pathlib
for f in pathlib.Path("workflows").glob("*.json"):
    w = json.loads(f.read_text())
    if w.get("name", "").startswith("TEST"):
        f.unlink()
        continue
    w.pop("staticData", None)
    # Métadonnées inutiles à une restauration (dont le nom et l'e-mail du compte n8n dans « shared »)
    for cle in ("shared", "meta", "pinData", "versionId", "activeVersionId", "versionCounter", "versionMetadata",
                "triggerCount", "createdAt", "updatedAt", "sourceWorkflowId", "nodeGroups"):
        w.pop(cle, None)
    f.write_text(json.dumps(w, ensure_ascii=False, indent=2) + "\n")
EOF

# 2. Identifiants : export chiffré, local uniquement, lisible par toi seul
umask 077
mkdir -p "$LOCAL"
docker exec n8n n8n export:credentials --all --output=/tmp/identifiants.json >/dev/null
docker cp -q n8n:/tmp/identifiants.json "$LOCAL/identifiants-$(date +%F).json"
chmod 600 "$LOCAL/identifiants-$(date +%F).json" # docker cp ignore umask
docker exec n8n rm -f /tmp/identifiants.json
ls -1t "$LOCAL"/identifiants-*.json | tail -n +$((GARDER + 1)) | xargs -r rm -f

# Fichiers de secrets des conteneurs (nécessaires pour une restauration) : local uniquement
tar -czf "$LOCAL/secrets-$(date +%F).tar.gz" .env relais.env cerveau.env voix.env studio.env reseau.env
chmod 600 "$LOCAL/secrets-$(date +%F).tar.gz"
ls -1t "$LOCAL"/secrets-*.tar.gz | tail -n +$((GARDER + 1)) | xargs -r rm -f

# 3. Commit (le hook gitleaks vérifie l'absence de secret) et push
git add -A
if git diff --cached --quiet; then
  echo "Aucun changement."
else
  git commit -q -m "Sauvegarde automatique du $(date '+%F %H:%M')"
  git push -q
  echo "Sauvegarde envoyée."
fi

# État publié pour le tableau de bord
ETAT_PUBLIC="${XDG_STATE_HOME:-$HOME/.local/state}/assistant-etat"
mkdir -p "$ETAT_PUBLIC"
printf '{"date":"%s","commit":"%s"}\n' "$(date -Iseconds)" "$(git rev-parse --short HEAD)" > "$ETAT_PUBLIC/sauvegarde.json"
