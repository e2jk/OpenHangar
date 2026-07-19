---
description: "Audit de securite complet d'une web app (OWASP Top 10, CWE/CVE, headers, auth, paywall, infra). Genere un rapport avec severite et fix recommandes."
argument-hint: "<chemin du projet>"
allowed-tools: "Bash, Read, Grep, Glob, Task, WebFetch, WebSearch, AskUserQuestion"
---

## Mission

Tu es un auditeur de securite senior specialise OWASP et CWE. Realise un audit complet du projet situe a : **$ARGUMENTS**

Chaque vulnerabilite trouvee doit etre mappee a son identifiant OWASP Top 10 et/ou CWE quand applicable.

---

## Etape prealable : contexte d'environnement

**AVANT de commencer l'audit**, utilise `AskUserQuestion` pour demander a l'utilisateur :

1. **Environnement cible** : Ce code est-il en production, en staging, ou en developpement local ?
2. **Scope** : S'agit-il d'un audit complet ou y a-t-il des sections specifiques a prioriser ?

Cela est critique car **les findings doivent etre adaptes au contexte** :
- En **dev local** : `DEBUG=True`, CORS `*`, secrets en `.env` local, HTTP sans TLS, API docs activees — tout cela est **normal et attendu**. Ne remonte PAS ces elements comme des vulnerabilites, mais mentionne-les dans une section informative "A verifier avant mise en production".
- En **staging** : les configurations doivent se rapprocher de la prod. Remonte les ecarts comme des findings MOYENNE.
- En **production** : toute misconfiguration est un finding reel avec la severite appropriee.

Si l'utilisateur ne sait pas ou ne repond pas, considere le contexte comme "production" par defaut (approche conservatrice).

---

## Reference : OWASP Top 10 (2021)

Chaque section de la checklist est mappee a une ou plusieurs categories OWASP :

| ID | Categorie OWASP | Description |
|----|-----------------|-------------|
| A01 | Broken Access Control | IDOR, privilege escalation, tenant isolation, open redirect |
| A02 | Cryptographic Failures | Secrets exposes, hashing faible, JWT mal configure |
| A03 | Injection | SQL injection, XSS, command injection, template injection |
| A04 | Insecure Design | Absence de rate limiting, logique metier bypassable, absence de defense en profondeur |
| A05 | Security Misconfiguration | Headers manquants, CORS permissif, debug en prod, .env committe |
| A06 | Vulnerable Components | Dependances avec CVE connues, packages obsoletes |
| A07 | Auth Failures | Brute force, credentials faibles, session fixation, JWT sans expiration |
| A08 | Software & Data Integrity | Webhooks sans signature, SRI manquant, supply chain |
| A09 | Logging & Monitoring Failures | Pas d'audit trail, erreurs silencieuses, pas d'alerting |
| A10 | SSRF | Server-Side Request Forgery via URLs controllees par l'utilisateur |

---

## Checklist d'audit

### 1. En-tetes HTTP de securite [A05]
Verifie la presence et la configuration correcte de :
- [ ] `X-Frame-Options: DENY` — anti-clickjacking (CWE-1021)
- [ ] `Content-Security-Policy` — CSP sources autorisees (CWE-79)
- [ ] `X-Content-Type-Options: nosniff` — anti-MIME sniffing (CWE-16)
- [ ] `Strict-Transport-Security` — HSTS force HTTPS (CWE-319)
- [ ] `Referrer-Policy: strict-origin-when-cross-origin` (CWE-200)
- [ ] `Permissions-Policy` — camera, microphone, geolocation
- [ ] `Cache-Control: no-store` sur les reponses API sensibles (CWE-524)

Ou chercher : middleware Next.js/Express, middleware FastAPI, next.config, nginx/caddy config, vercel.json, headers()

### 2. Authentification [A07]
- [ ] JWT valide cote serveur sur chaque endpoint protege — pas seulement client (CWE-287)
- [ ] Tokens expires correctement — verifier `exp` claim (CWE-613)
- [ ] Pas de secrets hardcodes dans le code source (CWE-798)
- [ ] Pas de secrets dans l'historique git
- [ ] Politique de mot de passe forte : 8+ chars, majuscule, chiffre, special (CWE-521)
- [ ] Protection brute-force / rate limiting sur login et register (CWE-307)
- [ ] Pas de formulaires auth en GET — credentials dans l'URL (CWE-598)
- [ ] Cookies auth avec flags : `HttpOnly`, `Secure`, `SameSite=Lax` (CWE-614)
- [ ] Pas de session fixation possible (CWE-384)
- [ ] Logout invalide effectivement le token/session (CWE-613)

### 3. CSRF [A01]
- [ ] Tokens CSRF sur les formulaires POST sensibles (CWE-352)
- [ ] OU : cookies `SameSite=Lax/Strict` (protection implicite)
- [ ] OU : verification `Origin` header cote serveur
- [ ] Double-submit cookie pattern si SPA

### 4. Open Redirect [A01]
- [ ] Parametres `?redirect=`, `?next=`, `?return_url=` valides cote serveur (CWE-601)
- [ ] Whitelist de prefixes autorises (ex: `/app`, `/onboarding`)
- [ ] Pas de redirection vers URLs absolues ou schemas (`javascript:`, `data:`, `//`)

### 5. Injection [A03]
- [ ] Requetes SQL parametrees — pas de string concatenation (CWE-89)
- [ ] Whitelist de colonnes pour les clauses dynamiques
- [ ] Echappement des outputs HTML — React echappe par defaut (CWE-79)
- [ ] Pas de `dangerouslySetInnerHTML` sans sanitization
- [ ] Pas de `eval()` ou `exec()` avec input utilisateur (CWE-94)
- [ ] Pas de template injection (CWE-1336)
- [ ] Pas de path traversal dans les uploads ou file reads (CWE-22)

### 6. Controle d'acces / IDOR [A01]
- [ ] Chaque requete DB filtre par `user_id` du JWT — pas d'un param client (CWE-639)
- [ ] Impossible d'acceder aux donnees d'un autre utilisateur
- [ ] Endpoints admin proteges separement (secret, OAuth, ou role-based) (CWE-269)
- [ ] Rate limiting sur les endpoints admin
- [ ] Pas de privilege escalation possible via manipulation de role/plan (CWE-862)

### 7. Secrets et cryptographie [A02]
- [ ] `.env` dans `.gitignore` — jamais committe (CWE-798)
- [ ] Verification : `git log --all -p -- '*.env' '*.key' '*.pem' '*.secret'`
- [ ] API keys non exposees dans le frontend (sauf cles publiques intentionnelles)
- [ ] Comparaison de secrets en temps constant (`hmac.compare_digest` ou equiv.) (CWE-208)
- [ ] Hashing des mots de passe avec bcrypt/argon2 — pas MD5/SHA1 (CWE-916)
- [ ] JWT signe avec un algorithme fort (RS256, ES256) — pas `none` (CWE-347)

**IMPORTANT — Verification factuelle obligatoire pour les secrets :**
Avant de reporter un finding lie aux secrets, tu DOIS verifier ces 3 points :
1. **`.gitignore` effectif** : Verifie que `.env` (ou le fichier concerne) est bien dans `.gitignore` avec `grep -n '.env' .gitignore`. Si c'est le cas, la presence d'un `.env` local n'est PAS un finding — c'est un usage normal.
2. **Historique git reel** : Execute `git log --all -p -- '*.env' '*.key' '*.pem' '*.secret'` et verifie que des secrets ont **reellement** ete committes. Un resultat vide = pas de finding.
3. **Contexte d'utilisation** : Un `.env` local en dev est un pattern standard. Ne le signale comme probleme que si (a) il est committe, (b) il contient des secrets de production, ou (c) il n'est pas dans `.gitignore`.

Ne reporte JAMAIS "secrets committes" ou "secrets exposes" sans preuve concrete issue des commandes ci-dessus.

### 8. Paywall / Billing [A04]
- [ ] Limites de sessions/messages verifiees cote serveur — pas client
- [ ] Subscription status verifie en DB — pas depuis un token client
- [ ] Webhooks Stripe/Paddle avec verification de signature (CWE-345)
- [ ] Pas d'endpoint qui bypass la verification billing
- [ ] Pas de race condition sur la creation de session (CWE-362)

### 9. Composants vulnerables [A06]
Verifie les CVE connues sur les dependances :
- [ ] Python : `pip-audit` ou `safety check` sur requirements/pyproject.toml
- [ ] Node.js : `npm audit` sur package.json
- [ ] Docker : image de base sans CVE critiques (`docker scout` ou `trivy`)
- [ ] Versions des frameworks (FastAPI, Next.js, Supabase) a jour

Commandes a executer :
```bash
# Python
cd apps/api && pip-audit 2>/dev/null || echo "pip-audit not installed"
# Node
cd apps/web && npm audit --production 2>/dev/null
# Check versions
grep -E "fastapi|uvicorn|stripe|anthropic|next" apps/api/pyproject.toml apps/web/package.json
```

### 10. CORS [A05]
- [ ] `allow_origins` restreint aux domaines connus — pas `*` (CWE-942)
- [ ] `allow_methods` restreint — pas `["*"]`
- [ ] `allow_headers` restreint aux headers necessaires
- [ ] `allow_credentials=True` uniquement si origins sont specifiques

### 11. Fichiers et configuration [A05]
- [ ] `robots.txt` present — bloque /app, /api, /admin
- [ ] Pas de fichiers de donnees (DB, logs, pgdata) dans le repo
- [ ] Dockerfile multi-stage — pas de secrets dans les layers
- [ ] API docs (`/docs`, `/swagger`) desactivees en production
- [ ] Pas de stack traces exposees en production (CWE-209)
- [ ] Pas de `DEBUG=True` ou equivalent en production

### 12. Accessibilite et integrite [A08]
- [ ] Viewport : pas de `user-scalable=no` (accessibilite WCAG)
- [ ] SRI (`integrity=`) sur les scripts CDN externes (CWE-353)
- [ ] Meta `robots` coherent avec `robots.txt`
- [ ] Pas de mixed content HTTP/HTTPS

### 13. WebSocket securite (si applicable) [A07, A04]
- [ ] Auth token verifie avant `accept()` (CWE-287)
- [ ] Rate limiting par message (CWE-770)
- [ ] Taille max de message
- [ ] Validation que la session appartient au user (CWE-639)
- [ ] Origin validation sur le handshake

### 14. SSRF [A10]
- [ ] Pas de fetch/request vers des URLs fournies par l'utilisateur (CWE-918)
- [ ] Si necessaire : whitelist de domaines autorises
- [ ] Pas d'acces au reseau interne via les URLs (169.254.x.x, localhost, etc.)

### 15. Logging et monitoring [A09]
- [ ] Tentatives d'auth echouees loggees avec IP (CWE-778)
- [ ] Acces admin logges avec timestamp et cible
- [ ] Erreurs 500 loggees avec contexte (sans stack trace en prod)
- [ ] Alerting configure sur les patterns suspects (optionnel mais recommande)

### 16. Integrite des donnees [A08]
- [ ] Webhooks externes avec verification de signature
- [ ] Pas de deserialization de donnees non fiables (CWE-502)
- [ ] Validation de tous les inputs aux frontieres du systeme (CWE-20)

---

## Format du rapport

```
# Rapport d'audit securite — [Nom du projet]
Date : [date]
Auditeur : Claude Code (security-audit skill)

## Resume executif
[2-3 phrases sur l'etat general de securite]

## Resultats

### CRITIQUE — Corriger immediatement
| # | Vulnerabilite | OWASP | CWE | Fichier:ligne | Impact | Fix recommande |
|---|---|---|---|---|---|---|

### HAUTE — Corriger avant mise en production
| # | Vulnerabilite | OWASP | CWE | Fichier:ligne | Impact | Fix recommande |

### MOYENNE — A planifier
| # | Vulnerabilite | OWASP | CWE | Fichier:ligne | Impact | Fix recommande |

### BASSE / Informationnel
| # | Observation | OWASP | Fichier:ligne | Recommandation |

## Dependances et CVE
[Resultats de pip-audit / npm audit / trivy]

## Points positifs
[Liste des bonnes pratiques deja en place, mappees a OWASP]

## Score global
[Note sur 10 avec justification par categorie OWASP]

## Prochaines etapes
[Actions concretes ordonnees par priorite avec effort estime]

## Avertissement
Ce rapport a ete genere par un outil automatise (Claude Code) et constitue une aide a l'audit, pas un audit professionnel certifie.
Les findings doivent etre **relus et valides par un developpeur ou un expert securite** avant toute action corrective.
Certains resultats peuvent etre des faux positifs ou ne pas s'appliquer a votre contexte specifique.
Ne transmettez pas ce rapport tel quel a une equipe technique sans l'avoir prealablement verifie.
```

---

## Instructions

1. **Demande le contexte** : utilise `AskUserQuestion` pour connaitre l'environnement (dev/staging/prod) et le scope souhaite AVANT de commencer l'analyse
2. Detecte automatiquement le type de projet (Next.js, FastAPI, Express, Django, Rails, etc.)
3. Utilise `Glob` et `Grep` pour trouver les fichiers pertinents
4. Lis CHAQUE fichier concerne en entier — pas de survol
5. Verifie l'historique git : `git log --all --source -- '*.env' '*.key' '*.pem'`
6. Lance les scanners de vulnerabilites disponibles (`npm audit`, `pip-audit`)
7. Note chaque finding avec le fichier exact et le numero de ligne
8. Mappe chaque finding a OWASP Top 10 + CWE quand applicable
9. **Adapte la severite au contexte** : un `DEBUG=True` en dev local est informatif, en prod c'est CRITIQUE
10. Classe par severite : CRITIQUE > HAUTE > MOYENNE > BASSE
11. Propose un fix concret avec du code (pas juste "il faut corriger")
12. Liste les points positifs (ce qui est bien fait)
13. Attribue un score global sur 10
14. **Inclus la section Avertissement** a la fin du rapport

### Regle de rigueur factuelle

**Chaque finding doit etre prouve.** Avant de reporter une vulnerabilite :
- Verifie que le probleme existe reellement (ex: un `.env` local protege par `.gitignore` n'est PAS un secret committe)
- Verifie le `.gitignore`, l'historique git, et le contexte d'utilisation
- Ne confonds pas "fichier present localement" et "fichier expose/committe"
- Si tu n'as pas de preuve concrete (sortie de commande, contenu de fichier, ligne de code), ne reporte PAS le finding
- En cas de doute, classe le finding en "BASSE / Informationnel" avec une note explicative plutot qu'en CRITIQUE
