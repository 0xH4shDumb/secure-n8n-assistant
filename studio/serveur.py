"""Mini API interne : POST /podcast {"repliques": [{"voix": "A"|"B", "texte": "..."}]} -> OGG/Opus

OGG/Opus est le format des messages vocaux Telegram (onde sonore, vitesse x1,5/x2).

La voix A est féminine (Denise), la voix B masculine (Henri).
Si une seule réplique échoue avec Microsoft, TOUT le podcast est refait avec Piper
(en local) : les voix restent cohérentes et le podcast part toujours.
L'en-tête de réponse X-Moteur indique le moteur utilisé (microsoft ou piper).

Sécurité : en-tête X-Studio-Secret obligatoire, aucun port publié, taille limitée,
fichiers temporaires effacés après chaque podcast.
"""

import asyncio
import hmac
import json
import os
import subprocess
import tempfile
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import edge_tts
from piper import PiperVoice

SECRET = os.environ["STUDIO_SECRET"]
TAILLE_MAX = 200 * 1024  # 200 Ko de script, bien plus qu'un podcast de 10 min
PAUSE_S = 0.35  # silence entre deux répliques

VOIX_MICROSOFT = {"A": "fr-FR-DeniseNeural", "B": "fr-FR-HenriNeural"}
VOIX_PIPER = {"A": "/voix-piper/fr_FR-siwis-medium.onnx", "B": "/voix-piper/fr_FR-tom-medium.onnx"}

if len(SECRET) < 32:
    raise SystemExit("STUDIO_SECRET trop court")


def journal(message):
    print(time.strftime("%F %T"), message, flush=True)


piper = {v: PiperVoice.load(chemin) for v, chemin in VOIX_PIPER.items()}
journal("Studio prêt (Microsoft : Denise/Henri, secours Piper : siwis/tom)")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


async def microsoft(repliques, dossier):
    fichiers = []
    for i, r in enumerate(repliques):
        f = dossier / f"{i:03d}.mp3"
        await edge_tts.Communicate(r["texte"], VOIX_MICROSOFT[r["voix"]]).save(str(f))
        if f.stat().st_size == 0:
            raise RuntimeError("audio vide")
        fichiers.append(f)
    return fichiers


def local(repliques, dossier):
    fichiers = []
    for i, r in enumerate(repliques):
        f = dossier / f"{i:03d}.wav"
        with wave.open(str(f), "wb") as w:
            piper[r["voix"]].synthesize_wav(r["texte"], w)
        fichiers.append(f)
    return fichiers


def monter(fichiers, dossier):
    """Normalise chaque réplique, intercale un court silence, encode en OGG/Opus (format vocal Telegram)."""
    silence = dossier / "silence.wav"
    ffmpeg("-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono", "-t", str(PAUSE_S), silence)
    morceaux = []
    for f in fichiers:
        n = f.with_suffix(".norm.wav")
        ffmpeg("-i", f, "-ar", "24000", "-ac", "1", n)
        morceaux += [n, silence]
    liste = dossier / "liste.txt"
    liste.write_text("".join(f"file '{m}'\n" for m in morceaux))
    sortie = dossier / "podcast.ogg"
    ffmpeg("-f", "concat", "-safe", "0", "-i", liste, "-af", "loudnorm=I=-16:TP=-1.5",
           "-ar", "48000", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", "-application", "voip", sortie)
    duree = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", sortie],
                           capture_output=True, text=True, check=True).stdout.strip()
    return sortie.read_bytes(), round(float(duree))


def produire(repliques):
    with tempfile.TemporaryDirectory() as d:
        dossier = Path(d)
        try:
            fichiers, moteur = asyncio.run(microsoft(repliques, dossier)), "microsoft"
        except Exception as e:  # noqa: BLE001 - n'importe quelle panne -> secours local
            journal(f"Microsoft indisponible ({type(e).__name__}) : bascule sur Piper")
            for f in dossier.iterdir():
                f.unlink()
            fichiers, moteur = local(repliques, dossier), "piper"
        audio, duree = monter(fichiers, dossier)
        return audio, duree, moteur


class Gestionnaire(BaseHTTPRequestHandler):
    def json(self, statut, corps):
        donnees = json.dumps(corps, ensure_ascii=False).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self):
        self.json(200, {"ok": True}) if self.path == "/sante" else self.json(404, {"erreur": "introuvable"})

    def do_POST(self):
        if self.path != "/podcast":
            return self.json(404, {"erreur": "introuvable"})
        if not hmac.compare_digest(self.headers.get("X-Studio-Secret", ""), SECRET):
            return self.json(403, {"erreur": "interdit"})
        taille = int(self.headers.get("Content-Length") or 0)
        if not 0 < taille <= TAILLE_MAX:
            return self.json(413, {"erreur": "script absent ou trop volumineux"})
        try:
            repliques = json.loads(self.rfile.read(taille))["repliques"]
            repliques = [
                {"voix": "B" if str(r.get("voix")).upper() == "B" else "A", "texte": str(r["texte"]).strip()}
                for r in repliques
                if str(r.get("texte", "")).strip()
            ]
            if not repliques:
                raise ValueError
        except Exception:  # noqa: BLE001
            return self.json(400, {"erreur": "format attendu : {repliques: [{voix, texte}]}"})

        debut = time.time()
        try:
            audio, duree, moteur = produire(repliques)
        except Exception as e:  # noqa: BLE001
            journal(f"ERREUR : {type(e).__name__}: {str(e)[:200]}")
            return self.json(500, {"erreur": "échec de la production audio"})

        journal(f"OK : {len(repliques)} répliques, {duree} s d'audio, {len(audio) // 1024} Ko, {moteur}, {time.time() - debut:.0f} s")
        self.send_response(200)
        self.send_header("Content-Type", "audio/ogg")
        self.send_header("X-Duree", str(duree))
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("X-Moteur", moteur)
        self.end_headers()
        self.wfile.write(audio)

    def log_message(self, *args):
        pass


HTTPServer(("0.0.0.0", 8080), Gestionnaire).serve_forever()
