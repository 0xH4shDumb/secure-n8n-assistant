// Mini API interne : POST /demander { question, consigne? }  ->  { reponse }
//
// Sécurité :
// - joignable uniquement depuis le réseau Docker (aucun port publié) ;
// - en-tête X-Cerveau-Secret obligatoire ;
// - Claude tourne SANS AUCUN OUTIL (--tools "") : il reçoit du texte et renvoie
//   du texte, il ne peut ni lire de fichier, ni exécuter de commande, ni aller
//   sur le web. Une injection de prompt n'a donc aucun moyen d'agir ;
// - une seule requête à la fois, file d'attente limitée (protège le quota Pro).

const http = require('node:http');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const SECRET = process.env.CERVEAU_SECRET;
const MODELE = process.env.MODELE || 'sonnet';
const DELAI_MAX = 180_000; // ms
const TAILLE_MAX = 64 * 1024; // octets acceptés en entrée
const FILE_MAX = 3;

const CONSIGNE_DEFAUT =
  "Tu es l'assistant personnel de l'utilisateur. " +
  'Réponds en français, de façon concise et directe. ' +
  "Écris en texte brut : pas de Markdown (ni **, ni #, ni tableaux), car la réponse s'affiche dans Telegram. " +
  'Garde les noms de failles et de techniques (path traversal, RCE, SSRF, privilege escalation...) en anglais, sans les traduire.';

if (!SECRET || SECRET.length < 32) throw new Error('CERVEAU_SECRET manquant ou trop court');
if (!process.env.CLAUDE_CODE_OAUTH_TOKEN) throw new Error('CLAUDE_CODE_OAUTH_TOKEN manquant');

const journal = (...a) => console.log(new Date().toISOString(), ...a);

function claude(question, consigne) {
  return new Promise((resolve, reject) => {
    const args = [
      '-p',
      '--output-format', 'json',
      '--model', MODELE,
      '--tools', '',
      '--strict-mcp-config',
      '--no-session-persistence',
      '--system-prompt', consigne || CONSIGNE_DEFAUT,
    ];
    // Environnement minimal : seul le token est transmis, rien d'autre
    const env = {
      PATH: process.env.PATH,
      HOME: process.env.HOME,
      CLAUDE_CODE_OAUTH_TOKEN: process.env.CLAUDE_CODE_OAUTH_TOKEN,
      DISABLE_AUTOUPDATER: '1',
      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
    };
    const p = spawn('claude', args, { cwd: '/tmp', env });
    let out = '';
    let err = '';
    const minuteur = setTimeout(() => p.kill('SIGKILL'), DELAI_MAX);
    p.stdout.on('data', d => (out += d));
    p.stderr.on('data', d => (err += d));
    p.on('close', code => {
      clearTimeout(minuteur);
      try {
        const r = JSON.parse(out);
        if (r.is_error) return reject(new Error(String(r.result || 'erreur Claude').slice(0, 300)));
        resolve({ reponse: r.result, duree_ms: r.duration_ms });
      } catch {
        reject(new Error(`claude a échoué (code ${code}) : ${(err || out).slice(0, 300)}`));
      }
    });
    p.stdin.end(question);
  });
}

// File d'attente : une seule exécution à la fois
let chaine = Promise.resolve();
let enAttente = 0;

function secretValide(recu) {
  const a = Buffer.from(String(recu || ''));
  const b = Buffer.from(SECRET);
  return a.length === b.length && crypto.timingSafeEqual(a, b); // comparaison à temps constant
}

function repondre(res, statut, corps) {
  res.writeHead(statut, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(corps));
}

http
  .createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/sante') return repondre(res, 200, { ok: true });
    if (req.method !== 'POST' || req.url !== '/demander') return repondre(res, 404, { erreur: 'introuvable' });
    if (!secretValide(req.headers['x-cerveau-secret'])) return repondre(res, 403, { erreur: 'interdit' });

    let corps = '';
    req.on('data', d => {
      corps += d;
      if (corps.length > TAILLE_MAX) req.destroy();
    });
    req.on('end', () => {
      let donnees;
      try {
        donnees = JSON.parse(corps);
      } catch {
        return repondre(res, 400, { erreur: 'JSON invalide' });
      }
      const question = String(donnees.question || '').trim();
      if (!question) return repondre(res, 400, { erreur: 'question vide' });
      if (enAttente >= FILE_MAX) return repondre(res, 503, { erreur: 'occupé, réessaie dans un instant' });

      enAttente++;
      const debut = Date.now();
      chaine = chaine
        .then(() => claude(question, donnees.consigne))
        .then(r => {
          journal(`OK en ${Date.now() - debut} ms`);
          repondre(res, 200, r);
        })
        .catch(e => {
          journal(`ERREUR : ${e.message}`);
          repondre(res, 502, { erreur: e.message });
        })
        .finally(() => enAttente--);
    });
  })
  .listen(8080, () => journal(`Cerveau prêt (modèle : ${MODELE})`));
