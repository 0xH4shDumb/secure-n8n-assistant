# Voix : transcription des messages vocaux (Whisper), 100 % local, sans accès Internet
FROM python@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81
# python:3.13-slim, figée par empreinte

# av figé : à partir de la 18, PyAV a retiré un paramètre utilisé par faster-whisper 1.2.1
RUN pip install --no-cache-dir faster-whisper==1.2.1 ctranslate2==4.8.2 av==17.1.0

# Le modèle est téléchargé à la construction de l'image : à l'exécution, le
# conteneur n'a pas besoin d'Internet (il est sur un réseau Docker interne).
ARG MODELE=small
ENV MODELE=${MODELE} HF_HUB_OFFLINE=1 HF_HOME=/modeles
RUN HF_HUB_OFFLINE=0 python -c "from faster_whisper import WhisperModel; WhisperModel('${MODELE}', device='cpu', compute_type='int8')" \
 && chmod -R a+rX /modeles

COPY serveur.py /app/serveur.py

USER nobody
EXPOSE 8080
CMD ["python", "-u", "/app/serveur.py"]
