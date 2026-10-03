# Cerveau : Claude Code en mode non interactif, derrière une mini API HTTP interne
FROM node@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c
# node:22-slim, figée par empreinte

# Version de Claude Code figée : mise à jour volontaire uniquement
RUN npm install -g --no-fund --no-audit @anthropic-ai/claude-code@2.1.288 \
 && npm cache clean --force

COPY serveur.js /app/serveur.js

USER node
WORKDIR /tmp
EXPOSE 8080
CMD ["node", "/app/serveur.js"]
