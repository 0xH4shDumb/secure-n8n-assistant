"""Mini API interne de transcription : POST /transcrire (corps = fichier audio) -> {"texte": ...}

Sécurité :
- conteneur sur un réseau Docker interne, sans accès à Internet ;
- en-tête X-Voix-Secret obligatoire (comparaison à temps constant) ;
- taille et durée d'audio limitées ; l'audio n'est jamais écrit sur disque.
"""

import hmac
import io
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from faster_whisper import WhisperModel

SECRET = os.environ["VOIX_SECRET"]
TAILLE_MAX = 10 * 1024 * 1024  # 10 Mo, largement assez pour quelques minutes en Opus
DUREE_MAX = 300  # secondes

if len(SECRET) < 32:
    raise SystemExit("VOIX_SECRET trop court")


def journal(message):
    print(time.strftime("%F %T"), message, flush=True)


# Vocabulaire donné à Whisper pour l'aider à reconnaître les termes techniques
VOCABULAIRE = (
    "Vocabulaire : Nmap, scan SYN, scan connect, TCP, UDP, Kerberos, Kerberoasting, AS-REP roasting, "
    "Pass-the-Hash, Active Directory, BloodHound, NetExec, Impacket, Mimikatz, Metasploit, Burp Suite, "
    "CVE, CVSS, EPSS, SOC, SIEM, EDR, pentest, red team, n8n, Docker, Telegram, École."
)

modele = WhisperModel(os.environ.get("MODELE", "small"), device="cpu", compute_type="int8")
journal(f"Voix prête (modèle Whisper : {os.environ.get('MODELE', 'small')})")


class Gestionnaire(BaseHTTPRequestHandler):
    def repondre(self, statut, corps):
        donnees = json.dumps(corps, ensure_ascii=False).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self):
        self.repondre(200, {"ok": True}) if self.path == "/sante" else self.repondre(404, {"erreur": "introuvable"})

    def do_POST(self):
        if self.path != "/transcrire":
            return self.repondre(404, {"erreur": "introuvable"})
        if not hmac.compare_digest(self.headers.get("X-Voix-Secret", ""), SECRET):
            return self.repondre(403, {"erreur": "interdit"})
        taille = int(self.headers.get("Content-Length") or 0)
        if not 0 < taille <= TAILLE_MAX:
            return self.repondre(413, {"erreur": "audio absent ou trop volumineux"})

        audio = io.BytesIO(self.rfile.read(taille))
        debut = time.time()
        try:
            segments, info = modele.transcribe(audio, language="fr", vad_filter=True, beam_size=5, initial_prompt=VOCABULAIRE)
            if info.duration > DUREE_MAX:
                return self.repondre(413, {"erreur": f"audio trop long (max {DUREE_MAX} s)"})
            texte = " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:  # noqa: BLE001
            journal(f"ERREUR : {type(e).__name__}")
            return self.repondre(422, {"erreur": "audio illisible"})

        journal(f"OK : {info.duration:.0f} s d'audio transcrites en {time.time() - debut:.1f} s")
        self.repondre(200, {"texte": texte, "duree": round(info.duration, 1)})

    def log_message(self, *args):  # pas de journal HTTP par défaut (évite le bruit)
        pass


HTTPServer(("0.0.0.0", 8080), Gestionnaire).serve_forever()
