# 🕵️ Inspecteur Sitadel

Pour chaque permis de construire d'un département, l'outil dit à quelle famille appartient le demandeur (bailleur social, structure ESS médico-sociale, collectivité, promoteur, SCI...) et fait ressortir les porteurs de projet qui correspondent au profil des clients visés par Buddimm, direction immobilière externalisée : associations médico-sociales, structures de l'ESS et bailleurs sociaux, souvent sans équipe immobilière interne.

**Démo : [hamzaoukach.github.io/InspecteurSitadel](https://hamzaoukach.github.io/InspecteurSitadel/)** (Yvelines, 7 608 permis, 3 957 demandeurs)
## Ce que fait l'outil

Cinq scripts s'enchaînent ; chacun lit le résultat du précédent.

| Étape | Script | Ce qu'il fait | Ce qu'il produit |
| --- | --- | --- | --- |
| 1 | `filtrer.py` | Garde les PC du département déposés par des personnes morales (DuckDB sur les fichiers nationaux) | `data/logements_<dep>_pc_pm.csv`, `data/locaux_<dep>_pc_pm.csv` |
| 2 | `nomenclatures.py` | Récupère le libellé officiel de chaque catégorie juridique et code APE (API de métadonnées de l'INSEE) | `references/categories_juridiques.csv`, `references/naf_rev2.csv` |
| 3 | `enrichir.py` | Interroge l'API Recherche d'entreprises pour chaque SIREN : convention collective, FINESS, ESS, état actif. Aucun nom de dirigeant n'est conservé | `references/sirene_enrichi.csv` (cache) |
| 4 | `classer.py` | Classe chaque demandeur avec les règles R01 à R14, lisibles en tête du script ; un cas non couvert reste « non déterminé » | `sorties/demandeurs_<dep>.csv`, `sorties/permis_<dep>.csv` |
| 5 | `graphe.py` | Calcule les signaux de prospection et le score de priorité, génère la page | `sorties/priorites_<dep>.csv`, `sorties/graphe_<dep>.html`, `docs/` |

Chaque ligne de sortie renvoie à sa source : la fiche Sirène du demandeur et la ligne officielle du permis sur l'API DIDO du SDES.

## Lancer l'outil

Python 3.11 ou plus récent.

```bash
pip install -r requirements.txt
```

Les deux fichiers nationaux (1,3 Go) ne sont pas dans le dépôt. Les télécharger depuis le jeu [« Liste des permis de construire et autres autorisations d'urbanisme »](https://www.data.gouv.fr/datasets/liste-des-permis-de-construire-et-autres-autorisations-durbanisme) du SDES (listes des autorisations créant des logements et créant des locaux non résidentiels) et les enregistrer sous `data/sitadel_logements.csv` et `data/sitadel_locaux.csv`.

```bash
python filtrer.py --departement 78
python nomenclatures.py
python enrichir.py --departement 78     # une vingtaine de minutes la première fois, mis en cache ensuite
python classer.py --departement 78
python graphe.py --departement 78
```

Les extraits filtrés du 78, les nomenclatures INSEE et le cache Sirène sont versionnés. Sans les fichiers nationaux, on peut donc relancer pour le 78 `classer.py` puis `graphe.py` ; ce dernier calcule alors l'historique des demandeurs (nouvel entrant, nombre de permis) sur le seul département et le signale.

## Familles et signaux

Familles : bailleur social, ESS médico-sociale, ESS autre, collectivité ou établissement public, entreprise médico-sociale privée, promoteur, SCI, entreprise autre, non déterminé. Les trois premières sont les cibles de Buddimm.

Indices décisifs au delà de la forme juridique :

- **Convention collective 2150** (sociétés anonymes et fondations d'HLM) : repère les bailleurs sociaux dont la forme juridique est une SA ordinaire, comme Immobilière 3F.
- **Inscription au FINESS** : repère les associations médico-sociales dont le code d'activité est trompeur.

Signaux de prospection : construit régulièrement, nouvel entrant (premier permis en France depuis moins de deux ans), projet médico-social, permis en attente (fragile), promoteur déclarant des logements sociaux (fragile). Le score de priorité additionne des points explicites ; le barème est en tête de `graphe.py`.

## Sources

- [Base Sitadel du SDES](https://www.data.gouv.fr/datasets/liste-des-permis-de-construire-et-autres-autorisations-durbanisme), dictionnaires des variables et note de présentation dans `references/`
- API de métadonnées de l'INSEE (catégories juridiques, NAF rév. 2)
- [API Recherche d'entreprises](https://recherche-entreprises.api.gouv.fr) et [Annuaire des entreprises](https://annuaire-entreprises.data.gouv.fr)
- [Convention collective 2150](https://code.travail.gouv.fr/convention-collective/2150-personnels-des-societes-anonymes-et-fondations-dhlm)
- [geo.api.gouv.fr](https://geo.api.gouv.fr) pour quelques noms de communes

Outils : Python, DuckDB, pandas, vis-network. Assistants de code : Claude Code.
