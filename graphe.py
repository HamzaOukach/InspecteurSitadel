"""
L'Inspecteur Sitadel, étape 5 : repérer les patterns et dessiner le graphe.

Le graphe relie chaque structure (nœud coloré par famille) aux communes où
elle a obtenu des permis (nœuds gris). L'épaisseur d'un lien = nombre de
permis. Chaque structure porte des « signaux » de prospection :

  recurrent         : au moins SEUIL_RECURRENT permis dans le département ;
  nouvel_entrant    : premier permis de construire en France (depuis 2013,
                      tous départements) dans les MOIS_RECENT derniers mois,
                      et au plus 2 permis en tout ;
  projet_medico     : au moins un permis pour une résidence personnes âgées,
                      handicapés, sociale ou hôtelière à vocation sociale, ou
                      un local de santé ou d'action sociale ;
  en_attente        : au moins un permis autorisé il y a entre MOIS_ATTENTE et
                      MOIS_PEREMPTION mois (un PC vaut 3 ans, prorogeable deux fois
                      un an), toujours sans ouverture de chantier connue. FRAGILE :
                      environ 15 % des ouvertures ne remontent jamais à Sitadel ;
  logements_sociaux : promoteur dont un permis déclare des logements sociaux,
                      indice d'une vente probable à un bailleur (VEFA). FRAGILE :
                      la colonne est « insuffisamment renseignée » selon le SDES.

Tout est intégré dans une seule page HTML (sorties/graphe_{dep}.html) qui
s'ouvre par double clic, sans serveur.

Utilisation :
  python graphe.py --departement 78
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

SEUIL_RECURRENT = 3
MOIS_RECENT = 24
MOIS_ATTENTE = 24          # au delà, le permis n'a pas démarré depuis 2 ans
MOIS_PEREMPTION = 60       # un PC vaut 3 ans, prorogeable 2 fois 1 an : au delà de 5 ans il est sans doute périmé
FAMILLES_CIBLES = ["bailleur social", "ESS médico-sociale", "ESS autre"]
GABARIT = Path("web/graphe_gabarit.html")

# Score de priorité de prospection : une somme de points explicites, calculée pour les seules
# familles cibles. Pas d'apprentissage automatique : on ne connaît pas les clients réels de
# Buddimm, il n'y a donc pas d'exemples sur lesquels entraîner un modèle. Barème à revoir avec Buddimm.
PETITE, GRANDE = 5, 50  # nombre de PC en France (depuis 2013)
BAREME = {
    "coeur_de_cible": (3, "Association ou fondation médico-sociale : cœur de cible de Buddimm"),
    "bailleur":       (2, "Bailleur social : famille cible de Buddimm"),
    "ess_autre":      (1, "Autre structure ESS (sport, culte, enseignement, insertion...) : cible plus éloignée du métier"),
    "petite":         (2, f"{PETITE} permis au plus en France : sans doute pas d'équipe immobilière interne"),
    "grande":         (-2, f"Plus de {GRANDE} permis en France : dispose sans doute de sa propre direction immobilière"),
    "nouvel_entrant": (3, "Nouvel entrant : premier projet immobilier, besoin d'accompagnement"),
    "actif_recent":   (2, f"Permis obtenu dans les {MOIS_RECENT} derniers mois : projet en cours"),
    "projet_medico":  (2, "Projet médico-social : le métier de Buddimm"),
    "en_attente":     (1, "Permis en attente, peut-être bloqué (signal fragile, d'où un seul point)"),
    "local":          (1, "Siège dans le département : acteur local"),
}


def score_priorite(famille: str, permis_france: int, signaux: list[str], recent: bool, local: bool):
    """Renvoie (score, [(points, raison), ...]) ; (None, []) hors familles cibles."""
    if famille not in FAMILLES_CIBLES:
        return None, []
    cles = [{"ESS médico-sociale": "coeur_de_cible", "bailleur social": "bailleur", "ESS autre": "ess_autre"}[famille]]
    if permis_france <= PETITE:
        cles.append("petite")
    if permis_france > GRANDE:
        cles.append("grande")
    cles += [s for s in ("nouvel_entrant", "projet_medico", "en_attente") if s in signaux]
    if recent:
        cles.append("actif_recent")
    if local:
        cles.append("local")
    raisons = [BAREME[c] for c in cles]
    return sum(p for p, _ in raisons), [{"points": p, "raison": r} for p, r in raisons]


def sieges(dep: str) -> dict[str, str]:
    """Code postal du siège de chaque demandeur (permis le plus récent), lu dans les fichiers filtrés."""
    morceaux = [lire(f"data/{f}_{dep}_pc_pm.csv")[["SIREN_DEM", "CODPOST_DEM", "DATE_REELLE_AUTORISATION"]]
                for f in ("logements", "locaux")]
    df = pd.concat(morceaux).sort_values("DATE_REELLE_AUTORISATION")
    return df.groupby("SIREN_DEM")["CODPOST_DEM"].last().to_dict()
COMMUNES_CACHE = Path("references/communes.csv")


def lire(chemin) -> pd.DataFrame:
    return pd.read_csv(chemin, sep=";", dtype=str, keep_default_na=False)


def mois_avant(ref: date, mois: int) -> str:
    a, m = divmod(ref.year * 12 + ref.month - 1 - mois, 12)
    return date(a, m + 1, min(ref.day, 28)).isoformat()


def historique_national(sirens: list[str]) -> pd.DataFrame:
    """Nombre de PC et date du premier PC de chaque SIREN, France entière.
    Sans les fichiers nationaux, renvoie un tableau vide : main() se rabat alors sur l'historique du département."""
    if not all(Path(f"data/sitadel_{f}.csv").exists() for f in ("logements", "locaux")):
        print("Attention : fichiers nationaux absents, l'historique (nouvel entrant, taille) ne porte que sur le département.")
        return pd.DataFrame(columns=["siren", "permis_france", "departements", "premier_permis_france"])
    con = duckdb.connect()
    con.register("cibles", pd.DataFrame({"siren": sirens}))
    lire_csv = "read_csv('data/sitadel_{}.csv', all_varchar=true, header=true, ignore_errors=true)"
    union = " UNION ALL ".join(
        f"SELECT NUM_DAU, SIREN_DEM, DEP_CODE, DATE_REELLE_AUTORISATION FROM {lire_csv.format(f)} WHERE TYPE_DAU = 'PC'"
        for f in ["logements", "locaux"])
    return con.sql(f"""
        SELECT SIREN_DEM AS siren,
               count(DISTINCT NUM_DAU) AS permis_france,
               count(DISTINCT DEP_CODE) AS departements,
               min(DATE_REELLE_AUTORISATION) AS premier_permis_france
        FROM ({union}) p JOIN cibles c ON c.siren = p.SIREN_DEM
        GROUP BY 1""").df()


def noms_communes(permis: pd.DataFrame) -> dict[str, str]:
    """Nom le plus fréquent dans Sitadel ; sinon geo.api.gouv.fr (mis en cache)."""
    noms = (permis[permis["commune"] != ""].groupby("code_commune")["commune"]
            .agg(lambda s: s.value_counts().index[0]).to_dict())
    cache = dict(lire(COMMUNES_CACHE)[["code", "nom"]].values) if COMMUNES_CACHE.exists() else {}
    manquants = [c for c in permis["code_commune"].unique() if c not in noms and c not in cache]
    for code in manquants:
        try:
            req = urllib.request.Request(f"https://geo.api.gouv.fr/communes/{code}?fields=nom",
                                         headers={"User-Agent": "inspecteur-sitadel/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                cache[code] = json.load(r).get("nom", "")
        except Exception:
            cache[code] = ""  # commune fusionnée ou code inconnu : on affichera le code
        time.sleep(0.1)
    if manquants:
        with open(COMMUNES_CACHE, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["code", "nom"])
            w.writerows(sorted(cache.items()))
    return {c: noms.get(c) or cache.get(c) or c for c in permis["code_commune"].unique()}


DOCS = Path("docs")  # dossier publié par GitHub Pages


def publier_docs(dep: str, page: str, nb_structures: int, nb_permis: int, date_donnees: date):
    """Copie la démo dans docs/ et régénère la page d'accueil listant tous les départements générés."""
    DOCS.mkdir(exist_ok=True)
    (DOCS / f"graphe_{dep}.html").write_text(page, encoding="utf-8")
    index_json = DOCS / "departements.json"
    deps = json.loads(index_json.read_text(encoding="utf-8")) if index_json.exists() else {}
    deps[dep] = {"structures": nb_structures, "permis": nb_permis, "date_donnees": date_donnees.isoformat()}
    index_json.write_text(json.dumps(deps, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    cartes = "\n".join(
        f'<a class="dep" href="graphe_{d}.html"><span class="num">{d}</span>'
        f'<span>{v["structures"]} structures · {v["permis"]} permis<br>'
        f'<small>permis autorisés jusqu\'au {v["date_donnees"][8:10]}/{v["date_donnees"][5:7]}/{v["date_donnees"][:4]}</small></span></a>'
        for d, v in sorted(deps.items()))
    gabarit = Path("web/accueil_gabarit.html").read_text(encoding="utf-8")
    (DOCS / "index.html").write_text(gabarit.replace("<!--__DEPARTEMENTS__-->", cartes), encoding="utf-8")


def entier(x) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def main():
    p = argparse.ArgumentParser(description="L'Inspecteur Sitadel : patterns et graphe")
    p.add_argument("--departement", default="78")
    dep = p.parse_args().departement.zfill(2)

    for f in [f"sorties/permis_{dep}.csv", f"sorties/demandeurs_{dep}.csv", GABARIT]:
        if not Path(f).exists():
            sys.exit(f"Fichier manquant : {f}. Lancez d'abord classer.py.")

    permis = lire(f"sorties/permis_{dep}.csv")
    dem = lire(f"sorties/demandeurs_{dep}.csv").set_index("siren")
    permis = permis[permis["siren"] != ""]

    # Date des données : la plus récente autorisation présente (pas la date du jour).
    date_donnees = date.fromisoformat(permis["date_autorisation"].max())
    limite_recent = mois_avant(date_donnees, MOIS_RECENT)
    limite_attente = mois_avant(date_donnees, MOIS_ATTENTE)
    limite_peremption = mois_avant(date_donnees, MOIS_PEREMPTION)

    print("Historique national des demandeurs (lecture des fichiers France entière)...")
    hist = historique_national(sorted(permis["siren"].unique())).set_index("siren")
    communes = noms_communes(permis)
    siege = sieges(dep)
    prefixe_cp = "20" if dep in ("2A", "2B") else dep  # codes postaux corses en 20xxx

    # ---- signaux au niveau du permis
    permis["medico"] = (permis["residence"].isin(["personnes âgées", "handicapés", "sociale", "hôtelière à vocation sociale"])
                        | permis["service_public"].isin(["santé", "action sociale"]))
    permis["attente"] = ((permis["etat"] == "autorisé") & (permis["date_ouverture_chantier"] == "")
                         & (permis["date_autorisation"] < limite_attente)
                         & (permis["date_autorisation"] >= limite_peremption))
    permis["sociaux"] = permis["nb_logements_sociaux"].map(entier)

    noeuds, liens = [], []
    for siren, grp in permis.groupby("siren"):
        d = dem.loc[siren]
        h = hist.loc[siren] if siren in hist.index else None
        # sans historique national : premier permis connu dans le département
        premier = str(h["premier_permis_france"]) if h is not None else grp["date_autorisation"].min()
        total_fr = int(h["permis_france"]) if h is not None else len(grp)
        signaux = []
        if len(grp) >= SEUIL_RECURRENT:
            signaux.append("recurrent")
        if premier >= limite_recent and total_fr <= 2:
            signaux.append("nouvel_entrant")
        if grp["medico"].any():
            signaux.append("projet_medico")
        if grp["attente"].any():
            signaux.append("en_attente")
        if d["famille"] == "promoteur" and grp["sociaux"].sum() > 0:
            signaux.append("logements_sociaux")
        cp = siege.get(siren, "")
        score, raisons = score_priorite(d["famille"], total_fr, signaux,
                                        recent=grp["date_autorisation"].max() >= limite_recent,
                                        local=cp.startswith(prefixe_cp))

        noeuds.append({
            "id": f"s{siren}", "type": "structure", "nom": d["denomination"], "siren": siren,
            "famille": d["famille"], "regle": d["regle"], "confiance": d["confiance"],
            "justification": d["justification"], "cj": f'{d["cj"]} {d["cj_libelle"]}'.strip(),
            "ape": f'{d["ape"]} {d["ape_libelle"]}'.strip(),
            "permis_dep": len(grp), "permis_france": total_fr,
            "departements": int(h["departements"]) if h is not None else 1,
            "premier_permis_france": premier, "signaux": signaux,
            "score": score, "raisons": raisons, "siege_cp": cp,
            "source": d["source_sirene"],
            "permis": [{
                "num": r.num_dau, "commune": communes[r.code_commune], "date": r.date_autorisation,
                "etat": r.etat, "ouverture": r.date_ouverture_chantier, "fichier": r.fichier,
                "logements": entier(r.nb_logements), "sociaux": r.sociaux,
                "projet": r.residence and f"résidence {r.residence}" or r.service_public or r.destination_locaux,
                "medico": bool(r.medico), "attente": bool(r.attente), "source": r.source_permis,
            } for r in grp.sort_values("date_autorisation", ascending=False).itertuples()],
        })
        for code, n in grp.groupby("code_commune").size().items():
            liens.append({"from": f"s{siren}", "to": f"c{code}", "permis": int(n)})

    par_commune = permis.groupby("code_commune").size()
    for code, n in par_commune.items():
        noeuds.append({"id": f"c{code}", "type": "commune", "nom": communes[code], "code": code, "permis_dep": int(n)})

    donnees = {
        "departement": dep, "date_donnees": date_donnees.isoformat(),
        "genere_le": date.today().isoformat(),
        "parametres": {"seuil_recurrent": SEUIL_RECURRENT, "mois_recent": MOIS_RECENT, "mois_attente": MOIS_ATTENTE, "mois_peremption": MOIS_PEREMPTION,
                       "score_max": sum(p for k, (p, _) in BAREME.items() if p > 0 and k not in ("bailleur", "ess_autre"))},
        "bareme": [{"points": p, "raison": r} for p, r in BAREME.values()],
        "familles_cibles": FAMILLES_CIBLES, "noeuds": noeuds, "liens": liens,
    }
    js = json.dumps(donnees, ensure_ascii=False).replace("</", "<\\/")
    page = GABARIT.read_text(encoding="utf-8").replace("/*__DONNEES__*/null", js)
    sortie = Path(f"sorties/graphe_{dep}.html")
    sortie.write_text(page, encoding="utf-8")
    publier_docs(dep, page, sum(1 for n in noeuds if n["type"] == "structure"), len(permis), date_donnees)

    structures = [n for n in noeuds if n["type"] == "structure"]

    # Liste de prospection classée, lisible dans un tableur, avec les raisons et les sources.
    notes = sorted((n for n in structures if n["score"] is not None), key=lambda n: (-n["score"], -n["permis_dep"]))
    pd.DataFrame([{
        "rang": i, "score": n["score"], "denomination": n["nom"], "siren": n["siren"], "famille": n["famille"],
        "raisons": " ; ".join(f'{r["points"]:+d} {r["raison"]}' for r in n["raisons"]),
        "permis_departement": n["permis_dep"], "permis_france": n["permis_france"],
        "dernier_permis": n["permis"][0]["date"], "source_sirene": n["source"],
        "source_dernier_permis": n["permis"][0]["source"],
    } for i, n in enumerate(notes, 1)]).to_csv(f"sorties/priorites_{dep}.csv", sep=";", index=False, encoding="utf-8")

    print(f"-> {sortie} : {len(structures)} structures, {len(par_commune)} communes, {len(liens)} liens")
    print(f"-> sorties/priorites_{dep}.csv : {len(notes)} structures cibles notées ; les 10 premières :")
    for n in notes[:10]:
        print(f"   {n['score']:3d}  {n['nom'][:50]:50s} {n['famille']}")
    for s in ["recurrent", "nouvel_entrant", "projet_medico", "en_attente", "logements_sociaux"]:
        tous = sum(s in n["signaux"] for n in structures)
        cibles = sum(s in n["signaux"] and n["famille"] in FAMILLES_CIBLES for n in structures)
        print(f"   {s:18s} {tous:5d} structures, dont {cibles} dans les familles cibles")


if __name__ == "__main__":
    main()
