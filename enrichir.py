"""
L'Inspecteur Sitadel, étape 3 : enrichir chaque demandeur avec Sirène.

Pour chaque SIREN présent dans les fichiers filtrés d'un département, le
script interroge l'API publique « Recherche d'entreprises »
(https://recherche-entreprises.api.gouv.fr, service de l'État, sans clé) et
garde uniquement des indicateurs officiels sur la structure :
  - liste_idcc   : conventions collectives appliquées. 2150 = « Personnels
                   des sociétés anonymes et fondations d'HLM » (Légifrance
                   KALICONT000005635331), le signal le plus sûr pour repérer
                   un bailleur social dont la forme juridique est une SA ordinaire ;
  - est_ess      : structure de l'économie sociale et solidaire ;
  - est_finess   : inscrite au FINESS, le répertoire national des
                   établissements sanitaires et médico-sociaux ;
  - est_association, est_siae (insertion par l'activité économique),
    est_service_public ;
  - etat_administratif : A = active, C = cessée.

Volontairement, le script ne garde AUCUN nom de dirigeant ni de personne
physique, même si l'API en renvoie.

Le résultat est mis en cache dans references/sirene_enrichi.csv : un SIREN
déjà connu n'est pas redemandé, on peut donc relancer après une coupure, et
un autre département réutilise ce qui est déjà en cache.

Utilisation :
  python enrichir.py --departement 78
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API = "https://recherche-entreprises.api.gouv.fr/search"
CACHE = "references/sirene_enrichi.csv"
CHAMPS = [
    "siren", "trouve", "nom_complet", "nature_juridique", "activite_principale",
    "etat_administratif", "liste_idcc", "est_ess", "est_finess", "est_association",
    "est_siae", "est_service_public", "date_interrogation", "source",
]
PAUSE = 0.25  # l'API accepte environ 7 appels par seconde, on reste en dessous


def sirens_du_departement(dep: str) -> list[str]:
    sirens = set()
    for nom in ["logements", "locaux"]:
        chemin = f"data/{nom}_{dep}_pc_pm.csv"
        if not Path(chemin).exists():
            sys.exit(f"Fichier manquant : {chemin}. Lancez d'abord : python filtrer.py --departement {dep}")
        with open(chemin, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                s = (r.get("SIREN_DEM") or "").strip()
                if len(s) == 9 and s.isdigit():
                    sirens.add(s)
    return sorted(sirens)


def lire_cache() -> dict[str, dict]:
    if not Path(CACHE).exists():
        return {}
    with open(CACHE, encoding="utf-8", newline="") as f:
        return {r["siren"]: r for r in csv.DictReader(f, delimiter=";")}


def ecrire_cache(cache: dict[str, dict]):
    with open(CACHE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CHAMPS, delimiter=";")
        w.writeheader()
        w.writerows(sorted(cache.values(), key=lambda r: r["siren"]))


def appeler(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "inspecteur-sitadel/1.0", "Accept": "application/json"})
    for essai in range(6):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:  # trop d'appels ou serveur occupé : on patiente
                time.sleep(3 * (essai + 1))
                continue
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError):
            time.sleep(3 * (essai + 1))
    raise RuntimeError(f"Échec répété : {url}")


def interroger(siren: str) -> dict:
    url = f"{API}?{urllib.parse.urlencode({'q': siren, 'per_page': 1})}"
    resultats = [r for r in appeler(url).get("results", []) if r.get("siren") == siren]
    ligne = {"siren": siren, "date_interrogation": date.today().isoformat(),
             "source": f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}"}
    if not resultats:
        return {**ligne, "trouve": "0"}
    r = resultats[0]
    c = r.get("complements") or {}
    return {
        **ligne,
        "trouve": "1",
        # catégorie juridique 1xxx = entrepreneur individuel, donc une personne physique : on ne garde pas le nom
        "nom_complet": "" if (r.get("nature_juridique") or "").startswith("1") else (r.get("nom_complet") or ""),
        "nature_juridique": r.get("nature_juridique") or "",
        "activite_principale": r.get("activite_principale") or "",
        "etat_administratif": r.get("etat_administratif") or "",
        "liste_idcc": "|".join(c.get("liste_idcc") or []),
        "est_ess": str(c.get("est_ess", "")),
        "est_finess": str(c.get("est_finess", "")),
        "est_association": str(c.get("est_association", "")),
        "est_siae": str(c.get("est_siae", "")),
        "est_service_public": str(c.get("est_service_public", "")),
    }


def main():
    p = argparse.ArgumentParser(description="L'Inspecteur Sitadel : enrichissement Sirène")
    p.add_argument("--departement", default="78")
    dep = p.parse_args().departement.zfill(2)

    sirens = sirens_du_departement(dep)
    cache = lire_cache()
    a_faire = [s for s in sirens if s not in cache]
    print(f"{len(sirens)} SIREN dans le {dep}, {len(a_faire)} à interroger ({len(cache)} déjà en cache)")

    for i, s in enumerate(a_faire, 1):
        try:
            cache[s] = interroger(s)
        except RuntimeError as e:
            print(f"  {e} : on passe, il sera retenté au prochain lancement")
        if i % 100 == 0:
            ecrire_cache(cache)  # sauvegarde régulière, pour pouvoir reprendre
            print(f"  {i}/{len(a_faire)}")
        time.sleep(PAUSE)
    ecrire_cache(cache)

    du_dep = [cache[s] for s in sirens if s in cache]
    print(f"-> {CACHE} : {len(du_dep)} SIREN du {dep} enrichis, "
          f"{sum(r['trouve'] == '0' for r in du_dep)} introuvables, "
          f"{sum('2150' in r.get('liste_idcc', '').split('|') for r in du_dep)} avec la convention HLM 2150, "
          f"{sum(r.get('est_finess') == 'True' for r in du_dep)} au FINESS, "
          f"{sum(r.get('est_ess') == 'True' for r in du_dep)} ESS")


if __name__ == "__main__":
    main()
