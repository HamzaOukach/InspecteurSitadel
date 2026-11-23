"""
L'Inspecteur Sitadel, étape 1 : filtrer les données.

Lit les deux gros CSV Sitadel (logements et locaux non résidentiels),
garde uniquement :
  - le département demandé (78 par défaut),
  - les permis de construire (PC),
  - les demandeurs personnes morales (SIREN ou dénomination renseignés),
et écrit deux petits CSV dans le dossier data/.

Utilisation :
  python filtrer.py --explorer                 # affiche les colonnes des fichiers
  python filtrer.py                            # filtre sur le 78
  python filtrer.py --departement 21           # filtre sur un autre département
"""

import argparse
import sys
from pathlib import Path

import duckdb

# Les deux fichiers téléchargés sur data.gouv.fr (renommez-les ainsi
# ou changez les chemins ici).
FICHIERS = {
    "logements": "data/sitadel_logements.csv",
    "locaux": "data/sitadel_locaux.csv",
}

# Noms de colonnes possibles. Le SDES change parfois les noms d'une
# version à l'autre : le script prend le premier qui existe.
# Si rien ne correspond, lancez --explorer et ajoutez le bon nom ici.
CANDIDATS = {
    "departement": ["DEP_CODE", "DEP", "CODE_DEP", "DEPARTEMENT", "DEP_CODE_ADR_TER"],
    "type_dau": ["TYPE_DAU", "TYPE_AUTORISATION", "TYPE"],
    "siren": ["SIREN_DEM", "SIREN_DEMANDEUR", "SIREN"],
    "denomination": ["DENOM_DEM", "DENOMINATION_DEM", "DENOMINATION"],
}


def lire(chemin: str) -> str:
    """Renvoie l'expression SQL qui lit le CSV (tout en texte, pour garder
    les zéros en tête des codes comme 01 ou les SIREN)."""
    return (
        f"read_csv('{chemin}', all_varchar=true, header=true, "
        f"ignore_errors=true)"
    )


def colonnes(con, chemin: str) -> list[str]:
    return [r[0] for r in con.sql(f"DESCRIBE SELECT * FROM {lire(chemin)}").fetchall()]


def trouver(cols: list[str], role: str, chemin: str) -> str:
    par_majuscule = {c.upper(): c for c in cols}
    for nom in CANDIDATS[role]:
        if nom.upper() in par_majuscule:
            return par_majuscule[nom.upper()]
    sys.exit(
        f"\nColonne '{role}' introuvable dans {chemin}.\n"
        f"Lancez : python filtrer.py --explorer\n"
        f"puis ajoutez le bon nom dans CANDIDATS['{role}'] en haut du script."
    )


def explorer(con):
    for nom, chemin in FICHIERS.items():
        print(f"\n=== {nom} : {chemin}")
        cols = colonnes(con, chemin)
        print(f"{len(cols)} colonnes :")
        for c in cols:
            print("  ", c)
        print("\nAperçu (3 lignes) :")
        print(con.sql(f"SELECT * FROM {lire(chemin)} LIMIT 3"))


def filtrer(con, departement: str):
    Path("data").mkdir(exist_ok=True)
    for nom, chemin in FICHIERS.items():
        cols = colonnes(con, chemin)
        dep = trouver(cols, "departement", chemin)
        typ = trouver(cols, "type_dau", chemin)
        siren = trouver(cols, "siren", chemin)
        denom = trouver(cols, "denomination", chemin)

        sortie = f"data/{nom}_{departement}_pc_pm.csv"
        con.sql(f"""
            COPY (
                SELECT *, '{nom}' AS source_fichier
                FROM {lire(chemin)}
                WHERE trim("{dep}") = '{departement}'
                  AND upper(trim("{typ}")) = 'PC'
                  AND (nullif(trim("{siren}"), '') IS NOT NULL
                       OR nullif(trim("{denom}"), '') IS NOT NULL)
            ) TO '{sortie}' (HEADER, DELIMITER ';')
        """)

        n = con.sql(f"SELECT count(*) FROM read_csv('{sortie}', all_varchar=true)").fetchall()[0][0]
        print(f"{nom:10s} -> {sortie} : {n} permis")


def main():
    p = argparse.ArgumentParser(description="L'Inspecteur Sitadel : filtrage")
    p.add_argument("--departement", default="78", help="code du département (défaut : 78)")
    p.add_argument("--explorer", action="store_true", help="affiche les colonnes et s'arrête")
    args = p.parse_args()

    for chemin in FICHIERS.values():
        if not Path(chemin).exists():
            sys.exit(f"Fichier manquant : {chemin}")

    con = duckdb.connect()
    if args.explorer:
        explorer(con)
    else:
        filtrer(con, args.departement.zfill(2))


if __name__ == "__main__":
    main()
