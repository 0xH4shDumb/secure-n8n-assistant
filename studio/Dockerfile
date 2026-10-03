# Studio : transforme un script à deux voix en podcast MP3
#   - voix Microsoft (Denise et Henri) via edge-tts, gratuites, sans clé
#   - bascule automatique sur Piper (100 % local) si Microsoft ne répond pas
FROM python@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81
# python:3.13-slim, figée par empreinte

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir edge-tts==7.2.8 piper-tts==1.8.0

# Voix Piper de secours, intégrées à l'image (pas de téléchargement à l'exécution)
WORKDIR /voix-piper
RUN python -m piper.download_voices fr_FR-siwis-medium fr_FR-tom-medium \
 && chmod -R a+rX /voix-piper

COPY serveur.py /app/serveur.py

USER nobody
WORKDIR /tmp
EXPOSE 8080
CMD ["python", "-u", "/app/serveur.py"]
