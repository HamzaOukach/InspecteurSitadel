"""
L'Inspecteur Sitadel, étape 2 : récupérer les libellés officiels INSEE.

Pour chaque code présent dans les fichiers Sitadel nationaux :
  - CJ_DEM  (catégorie juridique, nomenclature Sirène niveau III),
  - APE_DEM (activité principale, NAF rév. 2, sous-classe),
le script interroge l'API publique de métadonnées de l'INSEE et écrit :
  references/categories_juridiques.csv
  references/naf_rev2.csv
avec trois colonnes : code ; libelle ; source (URL de l'API, citable).

Les codes déjà présents dans ces fichiers ne sont pas redemandés, on peut
donc relancer le script sans tout refaire. Un code que l'API ne connaît pas
(par exemple un ancien code APE sur 5 caractères) est écrit avec un libellé
vide : on ne devine rien.

Utilisation :
  python nomenclatures.py
"""

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

FICHIERS = ["data/sitadel_logements.csv", "data/sitadel_locaux.csv"]
API = "https://api.insee.fr/metadonnees/V1/codes"
NOMENCLATURES = {
    # colonne Sitadel : (chemin API, fichier de sortie)
    "CJ_DEM": ("cj/n3", "references/categories_juridiques.csv"),
    "APE_DEM": ("nafr2/sousClasse", "references/naf_rev2.csv"),
}
PAUSE = 0.2  # secondes entre deux appels, pour rester poli avec l'API


def codes_presents(con, colonne: str) -> list[str]:
    union = " UNION ".join(
        f"SELECT DISTINCT trim({colonne}) c FROM read_csv('{f}', all_varchar=true, "
        f"header=true, ignore_errors=true)"
        for f in FICHIERS
    )
    lignes = con.sql(f"SELECT DISTINCT c FROM ({union}) WHERE c IS NOT NULL AND c <> '' ORDER BY c")
    return [r[0] for r in lignes.fetchall()]


def deja_connus(chemin: str) -> dict[str, dict]:
    if not Path(chemin).exists():
        return {}
    with open(chemin, encoding="utf-8", newline="") as f:
        return {r["code"]: r for r in csv.DictReader(f, delimiter=";")}


def interroger(url: str) -> str:
    """Renvoie le libellé, ou '' si l'INSEE ne connaît pas le code."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "inspecteur-sitadel/1.0"}
    )
    for essai in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get("intitule", "")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ""
            if e.code == 429:  # trop d'appels : on attend de plus en plus longtemps
                time.sleep(10 * (essai + 1))
                continue
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(5 * (essai + 1))
    sys.exit(f"Échec répété sur {url}, relancez le script plus tard.")


def main():
    Path("references").mkdir(exist_ok=True)
    con = duckdb.connect()
    for colonne, (chemin_api, sortie) in NOMENCLATURES.items():
        codes = codes_presents(con, colonne)
        connus = deja_connus(sortie)
        a_faire = [c for c in codes if c not in connus]
        print(f"{colonne} : {len(codes)} codes, {len(a_faire)} à demander à l'INSEE")

        for i, code in enumerate(a_faire, 1):
            url = f"{API}/{chemin_api}/{code}"
            connus[code] = {"code": code, "libelle": interroger(url), "source": url}
            if i % 50 == 0:
                print(f"  {i}/{len(a_faire)}")
            time.sleep(PAUSE)

        with open(sortie, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["code", "libelle", "source"], delimiter=";")
            w.writeheader()
            w.writerows(sorted(connus.values(), key=lambda r: r["code"]))
        inconnus = [r["code"] for r in connus.values() if not r["libelle"]]
        print(f"  -> {sortie} ({len(connus)} codes, {len(inconnus)} inconnus de l'INSEE : {inconnus[:10]})")


if __name__ == "__main__":
    main()
