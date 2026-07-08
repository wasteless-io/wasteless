# Wasteless — Roadmap de maturité technique

> Backlog des points qui plafonnent la maturité du projet à ~15/20.
> Objectif : passer la robustesse opérationnelle de ~11/20 à 15+/20 sans
> régresser l'hygiène d'ingénierie (déjà ~17/20).

Légende priorité : 🔴 haute · 🟠 moyenne · 🟢 basse
Légende effort : S (< 1 j) · M (1-3 j) · L (> 3 j)

---

## 1. 🔴 Preuve de production réelle (auto-remédiation battle-tested) — L

**Problème** : tout est local-first (port 8888, docker-compose, boucle 5 min sur
une machine). L'auto-remédiation est *disabled by default*, donc le cœur de la
valeur — agir sur AWS — n'a jamais été validé en conditions réelles.

**Actions**
- [ ] Provisionner un compte AWS de test (sandbox) via Terraform : ressources
      réellement gaspilleuses (EIP non associée, NAT gateway inutilisée, EBS
      orphelin, EC2 idle) — cf. pattern des fixtures Terraform.
- [ ] Activer `auto_remediation.enabled: true` sur ce compte sandbox uniquement,
      exécuter un cycle complet detect → recommend → remediate → verify.
- [ ] Capturer les preuves : `actions_log`, `rollback_snapshots`,
      `savings_realized` alimentés par un vrai passage (pas dry-run).
- [ ] Documenter le run de bout en bout dans `docs/DEPLOYMENT.md` (ou un
      `docs/PRODUCTION_VALIDATION.md`) avec captures / IDs de ressources.
- [ ] `terraform destroy` systématique en fin de validation.

**Fait quand** : au moins une remédiation réelle par type de ressource a été
exécutée, vérifiée (Cost Explorer) et rollback-testée sur un compte sandbox,
avec trace reproductible.

---

## 2. 🟠 Qualité non bloquante → gate (mypy + coverage) — M

**Problème** : mypy est scopé à 4 fichiers, coverage informative et non-bloquante.
Le typage et la couverture réels sont partiels.

**Actions**
- [ ] Élargir mypy progressivement au-delà des 4 fichiers critiques
      (`safeguards`, `remediator`, `jobs`, `aws_clients`) : viser d'abord tout
      `src/core/`, puis `src/detectors/`, puis `ui/utils/`.
- [ ] Corriger ou `# type: ignore[code]` justifiés au fil de l'élargissement.
- [ ] Mesurer la coverage actuelle et fixer un seuil plancher réaliste
      (`--cov-fail-under=N`) en gate bloquant, relevé graduellement.
- [ ] Basculer la coverage d'informative à bloquante dans `.github/workflows/tests.yml`.

**Fait quand** : mypy couvre au minimum tout `src/core/` en gate, et la coverage
a un plancher bloquant en CI.

---

## 3. 🔴 Chemin le plus risqué non testé en CI (AWS/LLM) — M

**Problème** : les intégrations AWS et LLM skippent en CI (choix assumé), donc
le chemin le plus risqué n'a pas de filet automatique.

**Actions**
- [ ] Remplacer les appels AWS réels par des mocks/`moto` dans une nouvelle
      suite d'intégration exécutable en CI (create → detect → remediate →
      rollback sur ressources moto).
- [ ] Idem LLM : figer des réponses (fixtures/VCR) pour tester
      `src/core/llm.py` et la génération de rapports sans clé réelle.
- [ ] Ajouter un job CI (nightly ou déclenché) qui, avec des creds sandbox
      stockés en secrets, exécute la vraie suite `tests/integration/test_real_aws_*`.
- [ ] Documenter clairement quelle couche est mockée vs réelle.

**Fait quand** : le cycle remédiation complet est couvert par des tests
mock-AWS/mock-LLM qui tournent à chaque PR, + un job réel planifié.

---

## 4. 🟢 Facteur bus = 1 — M

**Problème** : dev solo, 159 commits. Critère de maturité "équipe/produit" non
cochable aujourd'hui.

**Actions**
- [ ] Compléter le runbook opérationnel : "comment reprendre le projet à zéro"
      (install, secrets, déploiement, remédiation) — partiellement dans
      `install.sh`/`CLAUDE.md`, à consolider pour un tiers.
- [ ] Documenter les décisions d'architecture non évidentes (ADR légers dans
      `docs/`) pour que les choix soient transmissibles.
- [ ] S'assurer que `CONTRIBUTING.md` permet à un contributeur externe de faire
      tourner tests + lint + collect sans connaissance tacite.
- [ ] (Optionnel) Ouvrir 2-3 issues "good first issue" pour amorcer une
      contribution externe.

**Fait quand** : un tiers peut cloner, installer, tester et faire un cycle
complet en suivant uniquement la doc, sans intervention du mainteneur.

---

## 5. 🟢 Résidus dans le repo — S

**Problème** : `venv 2/` et des `.DS_Store` traînent dans l'arborescence.

**Actions**
- [ ] Supprimer le dossier `venv 2/` (doublon du venv symlinké).
- [ ] Retirer les `.DS_Store` suivis et confirmer qu'ils sont bien dans `.gitignore`.
- [ ] Vérifier `git status` propre après nettoyage.

**Fait quand** : plus aucun résidu d'environnement ou fichier OS dans le repo.

---

## Récapitulatif priorités

| # | Item | Priorité | Effort | Impact maturité |
|---|------|----------|--------|-----------------|
| 1 | Preuve de production réelle | 🔴 | L | ⭐⭐⭐ |
| 3 | AWS/LLM testés en CI | 🔴 | M | ⭐⭐⭐ |
| 2 | mypy + coverage bloquants | 🟠 | M | ⭐⭐ |
| 4 | Facteur bus | 🟢 | M | ⭐⭐ |
| 5 | Résidus repo | 🟢 | S | ⭐ |

**Quick win recommandé** : #5 (immédiat), puis #1 et #3 qui débloquent le
principal levier de note (robustesse opérationnelle 11 → 15).
