"""Relais Telegram <-> n8n.

1. Va chercher les messages envoyés au bot (long polling) et les transmet à n8n
   sur le réseau Docker interne. Aucun port n'est ouvert sur Internet.
2. Sur le port interne 8081, rend à n8n les services que son connecteur
   Telegram ne sait pas rendre :
   - POST /vocal            : envoyer un message vocal (OGG/Opus)
   - POST /message          : envoyer un message avec des boutons
   - POST /bouton           : répondre au clic sur un bouton (petite notification)
   - POST /retirer-boutons  : retirer les boutons d'un message déjà traité

Sécurité :
- seuls les messages de CHAT_AUTORISE sont transmis, les autres sont ignorés ;
- n8n et le relais s'authentifient mutuellement (en-tête X-Relais-Secret) ;
- tout part UNIQUEMENT vers CHAT_AUTORISE : le destinataire est figé, et seules
  ces 4 actions précises existent (pas d'accès libre à l'API Telegram) ;
- le token n'est jamais écrit dans les journaux (il fait partie de l'URL de l'API).
"""

import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ["TELEGRAM_TOKEN"]
SECRET = os.environ["RELAIS_SECRET"]
WEBHOOK = os.environ["N8N_WEBHOOK"]
CHAT_AUTORISE = int(os.environ["CHAT_AUTORISE"])
API = f"https://api.telegram.org/bot{TOKEN}"

# Menu des commandes affiché par Telegram quand on tape « / »
COMMANDES = [
    {"command": "rapport", "description": "Rapport du jour : agenda et mails"},
    {"command": "veille", "description": "Nouveautés de la veille cyber"},
    {"command": "podcast", "description": "Mon podcast du jour (message vocal)"},
    {"command": "mails", "description": "Vérifier les nouveaux mails maintenant"},
    {"command": "statut", "description": "État de l'assistant"},
    {"command": "erreurs", "description": "Les 5 dernières erreurs"},
    {"command": "oubli", "description": "Effacer notre conversation"},
    {"command": "aide", "description": "Liste des commandes"},
]


def journal(message):
    print(time.strftime("%F %T"), message, flush=True)


def post(url, donnees, entetes=None, delai=15):
    requete = urllib.request.Request(
        url,
        data=json.dumps(donnees).encode(),
        headers={"Content-Type": "application/json", **(entetes or {})},
    )
    with urllib.request.urlopen(requete, timeout=delai) as reponse:
        return json.load(reponse) if reponse.status == 200 else {}


def telegram(methode, donnees, delai=15):
    return post(f"{API}/{methode}", donnees, delai=delai)


def auteur(update):
    """Renvoie (chat_id, user_id) de l'update, quel que soit son type."""
    if "message" in update:
        m = update["message"]
        return m.get("chat", {}).get("id"), m.get("from", {}).get("id")
    if "callback_query" in update:
        c = update["callback_query"]
        return c.get("message", {}).get("chat", {}).get("id"), c.get("from", {}).get("id")
    return None, None


def transmettre(update):
    for essai in range(1, 4):
        try:
            post(WEBHOOK, update, {"X-Relais-Secret": SECRET})
            return
        except Exception as e:  # noqa: BLE001 - on journalise le type seulement
            journal(f"n8n injoignable ({type(e).__name__}), essai {essai}/3")
            time.sleep(3)
    try:
        telegram("sendMessage", {"chat_id": CHAT_AUTORISE, "text": "⚠️ Message reçu, mais n8n ne répond pas."})
    except Exception:  # noqa: BLE001
        pass


VOCAL_MAX = 20 * 1024 * 1024  # 20 Mo (limite Telegram : 50 Mo)


def envoyer_vocal(audio, duree, legende):
    """sendVoice en multipart/form-data, construit à la main (bibliothèque standard uniquement)."""
    limite = uuid.uuid4().hex
    champs = {"chat_id": str(CHAT_AUTORISE), "duration": str(duree)}
    if legende:
        champs["caption"] = legende[:1000]
    corps = b""
    for nom, valeur in champs.items():
        corps += f'--{limite}\r\nContent-Disposition: form-data; name="{nom}"\r\n\r\n{valeur}\r\n'.encode()
    corps += (f'--{limite}\r\nContent-Disposition: form-data; name="voice"; filename="podcast.ogg"\r\n'
              "Content-Type: audio/ogg\r\n\r\n").encode() + audio + f"\r\n--{limite}--\r\n".encode()
    requete = urllib.request.Request(
        f"{API}/sendVoice", data=corps, headers={"Content-Type": f"multipart/form-data; boundary={limite}"}
    )
    with urllib.request.urlopen(requete, timeout=120) as reponse:
        return json.load(reponse).get("ok", False)


class Interne(BaseHTTPRequestHandler):
    """Porte d'entrée interne : seul n8n (avec le secret) peut demander l'envoi d'un vocal."""

    def repondre(self, statut, corps):
        donnees = json.dumps(corps).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self):
        self.repondre(200, {"ok": True}) if self.path == "/sante" else self.repondre(404, {"erreur": "introuvable"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path not in ("/vocal", "/message", "/bouton", "/retirer-boutons"):
            return self.repondre(404, {"erreur": "introuvable"})
        if not hmac.compare_digest(self.headers.get("X-Relais-Secret", ""), SECRET):
            journal(f"{url.path} refusé : secret invalide")
            return self.repondre(403, {"erreur": "interdit"})
        if url.path != "/vocal":
            return self.action_json(url.path)
        taille = int(self.headers.get("Content-Length") or 0)
        if not 0 < taille <= VOCAL_MAX:
            return self.repondre(413, {"erreur": "audio absent ou trop volumineux"})
        params = urllib.parse.parse_qs(url.query)
        duree = int((params.get("duree") or ["0"])[0] or 0)
        legende = (params.get("legende") or [""])[0]
        try:
            ok = envoyer_vocal(self.rfile.read(taille), duree, legende)
        except Exception as e:  # noqa: BLE001 - jamais str(e) : pourrait contenir l'URL (token)
            journal(f"sendVoice : {type(e).__name__}")
            return self.repondre(502, {"erreur": f"Telegram a refusé l'envoi ({type(e).__name__})"})
        journal(f"Vocal envoyé ({duree} s)" if ok else "Vocal refusé par Telegram")
        self.repondre(200 if ok else 502, {"ok": ok})

    def action_json(self, chemin):
        taille = int(self.headers.get("Content-Length") or 0)
        if not 0 < taille <= 64 * 1024:
            return self.repondre(413, {"erreur": "corps absent ou trop volumineux"})
        try:
            d = json.loads(self.rfile.read(taille))
            if chemin == "/message":
                donnees = {"chat_id": CHAT_AUTORISE, "text": str(d["texte"])[:4096],
                           "parse_mode": "HTML", "disable_web_page_preview": True}
                if d.get("boutons"):  # [[{"texte": "...", "donnee": "..."}]]
                    donnees["reply_markup"] = {"inline_keyboard": [
                        [{"text": str(b["texte"])[:64], "callback_data": str(b["donnee"])[:64]} for b in ligne]
                        for ligne in d["boutons"]]}
                r = telegram("sendMessage", donnees)
                return self.repondre(200, {"ok": r.get("ok", False), "message_id": r.get("result", {}).get("message_id")})
            if chemin == "/bouton":
                r = telegram("answerCallbackQuery", {"callback_query_id": str(d["callback_id"]),
                                                     "text": str(d.get("texte", ""))[:200]})
                if d.get("message"):
                    telegram("sendMessage", {"chat_id": CHAT_AUTORISE, "text": str(d["message"])[:4096], "parse_mode": "HTML"})
                return self.repondre(200, {"ok": r.get("ok", False)})
            # /retirer-boutons
            r = telegram("editMessageReplyMarkup", {"chat_id": CHAT_AUTORISE, "message_id": int(d["message_id"]),
                                                    "reply_markup": {"inline_keyboard": []}})
            return self.repondre(200, {"ok": r.get("ok", False)})
        except (KeyError, ValueError, TypeError):
            return self.repondre(400, {"erreur": "paramètres invalides"})
        except urllib.error.HTTPError as e:
            # La réponse de Telegram (code + description) ne contient jamais le token : on peut la journaliser
            try:
                raison = json.load(e).get("description", "")[:200]
            except Exception:  # noqa: BLE001
                raison = ""
            journal(f"{chemin} : Telegram {e.code} {raison}")
            return self.repondre(502, {"erreur": f"Telegram a refusé ({e.code} {raison})"})
        except Exception as e:  # noqa: BLE001 - jamais str(e) : pourrait contenir l'URL (token)
            journal(f"{chemin} : {type(e).__name__}")
            return self.repondre(502, {"erreur": f"Telegram a refusé ({type(e).__name__})"})

    def log_message(self, *args):
        pass


def main():
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", 8081), Interne).serve_forever, daemon=True).start()

    try:
        telegram("setMyCommands", {"commands": COMMANDES})
    except Exception as e:  # noqa: BLE001
        journal(f"setMyCommands : {type(e).__name__}")

    journal("Relais démarré")
    offset = None
    while True:
        try:
            # Long polling : Telegram garde la connexion ouverte jusqu'à 50 s
            # et répond dès qu'un message arrive (réaction quasi instantanée).
            r = telegram(
                "getUpdates",
                {"offset": offset, "timeout": 50, "allowed_updates": ["message", "callback_query"]},
                delai=60,
            )
        except Exception as e:  # noqa: BLE001 - jamais str(e) : pourrait contenir l'URL (token)
            journal(f"getUpdates : {type(e).__name__}")
            time.sleep(5)
            continue

        for update in r.get("result", []):
            offset = update["update_id"] + 1  # confirme la réception à Telegram
            chat, user = auteur(update)
            if chat != CHAT_AUTORISE or user != CHAT_AUTORISE:
                journal(f"Ignoré : message d'un inconnu (id {user})")
                continue
            transmettre(update)


if __name__ == "__main__":
    main()
