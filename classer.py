"""
L'Inspecteur Sitadel, étape 4 : classer chaque demandeur dans une famille.

Couche 1, déterministe : des règles lisibles, testées dans l'ordre ; la
première qui s'applique gagne. Chaque résultat garde l'identifiant de la
règle, sa justification, un niveau de confiance et les liens vers les
sources publiques. Ce qu'aucune règle ne couvre reste « non déterminé ».

Entrées :
  data/logements_{dep}_pc_pm.csv, data/locaux_{dep}_pc_pm.csv  (filtrer.py)
  references/categories_juridiques.csv, references/naf_rev2.csv (nomenclatures.py)
  references/sirene_enrichi.csv (enrichir.py ; facultatif, les règles qui
  en dépendent sont alors simplement ignorées)

Sorties (dossier sorties/) :
  demandeurs_{dep}.csv : une ligne par structure (SIREN)
  permis_{dep}.csv     : une ligne par permis, avec la famille du demandeur

Utilisation :
  python classer.py --departement 78
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

# Jeux officiels du SDES, interrogeables ligne par ligne via l'API DIDO.
DIDO = "https://data.statistiques.developpement-durable.gouv.fr/dido/api/v1/datafiles"
DATAFILES = {
    "logements": "8b35affb-55fc-4c1f-915b-7750f974446a",
    "locaux": "f8f0700f-806c-40a7-83b1-f21cf507e7c4",
}
IDCC_HLM = "2150"  # « Personnels des sociétés anonymes et fondations d'HLM », Légifrance KALICONT000005635331

# Libellés vérifiés dans les dictionnaires SDES (references/dictionnaire_*.xls).
ETAT = {"2": "autorisé", "4": "annulé", "5": "commencé", "6": "terminé"}
RESIDENCE = {"1": "personnes âgées", "2": "étudiants", "3": "tourisme", "4": "hôtelière à vocation sociale",
             "5": "sociale", "6": "handicapés", "7": "autre résidence", "8": "projet mixte"}
SERVICE_PUBLIC = {"1": "transport", "2": "enseignement et recherche", "3": "santé", "4": "action sociale",
                  "5": "ouvrage spécial", "6": "culture et loisir", "8": "plusieurs types"}
DESTINATION_LOCAUX = {"1": "habitation", "2": "hôtels", "3": "bureaux", "4": "commerce", "6": "industrie",
                      "7": "agriculture", "8": "entrepôt", "9": "service public ou d'intérêt collectif"}


# ---------------------------------------------------------------- demandeurs

@dataclass
class Demandeur:
    siren: str
    nom: str
    cj: str            # catégorie juridique (Sitadel, sinon Sirène)
    cj_libelle: str
    ape: str           # activité principale NAF rév. 2
    ape_libelle: str
    idcc: list[str]    # conventions collectives (Sirène)
    ess: bool
    finess: bool
    enrichi: bool      # présent dans references/sirene_enrichi.csv

    @property
    def ape2(self) -> str:
        return self.ape[:2]

    @property
    def est_association_fondation(self) -> bool:
        # 92xx associations et congrégations, 93xx fondations (nomenclature INSEE)
        return self.cj[:2] in ("92", "93")

    @property
    def est_public(self) -> bool:
        # 4xxx personnes morales de droit public soumises au droit commercial,
        # 7xxx personnes morales et organismes soumis au droit administratif
        return self.cj[:1] in ("4", "7")

    @property
    def est_societe(self) -> bool:
        return self.cj[:1] in ("3", "5", "6")

    @property
    def activite_medico_sociale(self) -> bool:
        # NAF 87 hébergement médico-social et social, 88 action sociale sans hébergement,
        # 86.10 activités hospitalières. Les cabinets médicaux et dentaires (86.2x) sont exclus.
        return self.ape2 in ("87", "88") or self.ape.startswith("86.10")


@dataclass
class Regle:
    id: str
    famille: str
    confiance: str
    texte: str
    test: Callable[[Demandeur], bool]
    besoin_enrichissement: bool = False


NOM_HLM = re.compile(r"\bH\.?\s?L\.?\s?M\b|HABITATIONS? (A )?LOYER MODERE|ENTREPRISE SOCIALE POUR L.?HABITAT")
NOM_OPH = re.compile(r"\bOPH\b|OFFICE PUBLIC|HABITAT")

REGLES = [
    Regle("R01", "non déterminé", "sûre",
          "Aucune catégorie juridique connue, ni dans Sitadel ni dans Sirène : on ne devine pas.",
          lambda d: not d.cj),
    Regle("R02", "bailleur social", "forte",
          "Catégorie juridique HLM selon l'INSEE (SA d'HLM, coopérative d'HLM...).",
          lambda d: "HLM" in d.cj_libelle.upper()),
    Regle("R03", "bailleur social", "forte",
          "Applique la convention collective 2150 des sociétés anonymes et fondations d'HLM (Sirène).",
          lambda d: IDCC_HLM in d.idcc, besoin_enrichissement=True),
    Regle("R04", "bailleur social", "forte",
          "Établissement public local à caractère industriel ou commercial (4140) qui loue des logements "
          "(APE 68.20A) ou dont le nom désigne un office public de l'habitat.",
          lambda d: d.cj == "4140" and (d.ape == "68.20A" or bool(NOM_OPH.search(d.nom)))),
    Regle("R05", "bailleur social", "moyenne",
          "Société dont la dénomination contient HLM, « habitation à loyer modéré » ou "
          "« entreprise sociale pour l'habitat ». Repose sur le nom seul.",
          lambda d: d.est_societe and bool(NOM_HLM.search(d.nom))),
    Regle("R06", "ESS médico-sociale", "forte",
          "Association ou fondation dont l'activité est la santé ou le médico-social (APE 86.10, 87, 88) "
          "ou qui est inscrite au FINESS ; ou autre structure ESS selon Sirène avec une telle activité.",
          lambda d: (d.est_association_fondation and (d.activite_medico_sociale or d.finess))
          or (d.ess and d.activite_medico_sociale)),
    Regle("R07", "ESS autre", "forte",
          "Association, congrégation ou fondation avec une autre activité.",
          lambda d: d.est_association_fondation),
    Regle("R08", "collectivité ou établissement public", "forte",
          "Catégorie juridique de droit public (4xxx ou 7xxx) : État, collectivité, intercommunalité, "
          "établissement public.",
          lambda d: d.est_public),
    Regle("R09", "ESS autre", "moyenne",
          "Société déclarée dans l'économie sociale et solidaire selon Sirène (coopérative, foncière "
          "solidaire...) et dont l'activité est le logement : location (68.20A), promotion (41.10) ou "
          "hébergement (55.90Z). Indicateur ESS déclaratif.",
          lambda d: d.ess and (d.ape == "68.20A" or d.ape.startswith("41.10") or d.ape == "55.90Z"),
          besoin_enrichissement=True),
    Regle("R10", "entreprise médico-sociale privée", "forte",
          "Société commerciale dont l'activité est la santé ou le médico-social (APE 86.10, 87, 88) : "
          "EHPAD, crèche ou clinique privés.",
          lambda d: d.est_societe and d.activite_medico_sociale),
    Regle("R11", "promoteur", "forte",
          "Activité de promotion immobilière (APE 41.10A à 41.10D) ou SCI de construction-vente (6541).",
          lambda d: d.ape.startswith("41.10") or d.cj == "6541"),
    Regle("R12", "SCI", "forte",
          "Société civile immobilière ou assimilée (65xx immobilières, ou autre société civile à activité "
          "immobilière). La forme est sûre, le porteur réel du projet est inconnu.",
          lambda d: d.cj in ("6540", "6539", "6542", "6543", "6521")
          or (d.cj == "6599" and d.ape2 in ("68", "41"))),
    Regle("R13", "entreprise autre", "forte",
          "Autre personne morale de droit privé.",
          lambda d: d.cj[:1] in ("2", "3", "5", "6", "8", "9")),
    Regle("R14", "non déterminé", "sûre",
          "Aucune règle ne s'applique.",
          lambda d: True),
]


def classer_demandeur(d: Demandeur) -> Regle:
    for regle in REGLES:
        if regle.besoin_enrichissement and not d.enrichi:
            continue
        if regle.test(d):
            return regle
    raise AssertionError("R14 couvre tous les cas")


# ---------------------------------------------------------------- chargement

def lire(chemin: str) -> pd.DataFrame:
    return pd.read_csv(chemin, sep=";", dtype=str, keep_default_na=False)


def charger_permis(dep: str) -> pd.DataFrame:
    morceaux = []
    for nom in DATAFILES:
        chemin = f"data/{nom}_{dep}_pc_pm.csv"
        if not Path(chemin).exists():
            sys.exit(f"Fichier manquant : {chemin}. Lancez : python filtrer.py --departement {dep}")
        df = lire(chemin)
        df["fichier"] = nom
        morceaux.append(df)
    return pd.concat(morceaux, ignore_index=True).fillna("")


def construire_demandeurs(permis: pd.DataFrame) -> list[Demandeur]:
    cj_lib = dict(lire("references/categories_juridiques.csv")[["code", "libelle"]].values)
    naf_lib = dict(lire("references/naf_rev2.csv")[["code", "libelle"]].values)
    chemin_enr = Path("references/sirene_enrichi.csv")
    enr = lire(str(chemin_enr)).set_index("siren").to_dict("index") if chemin_enr.exists() else {}
    if not enr:
        print("Attention : references/sirene_enrichi.csv absent, les règles R03 et R09 sont ignorées.")

    # Un même SIREN peut avoir déposé plusieurs permis : on prend les codes du permis le plus récent.
    recents = (permis[permis["SIREN_DEM"] != ""]
               .sort_values("DATE_REELLE_AUTORISATION")
               .groupby("SIREN_DEM").tail(1))
    demandeurs = []
    for _, p in recents.iterrows():
        s = p["SIREN_DEM"]
        e = enr.get(s, {})
        trouve = e.get("trouve") == "1"
        cj = p["CJ_DEM"].strip() or (e.get("nature_juridique", "") if trouve else "")
        ape = p["APE_DEM"].strip() or (e.get("activite_principale", "") if trouve else "")
        demandeurs.append(Demandeur(
            siren=s, nom=p["DENOM_DEM"].upper(), cj=cj, cj_libelle=cj_lib.get(cj, ""),
            ape=ape, ape_libelle=naf_lib.get(ape, ""),
            idcc=[i for i in e.get("liste_idcc", "").split("|") if i] if trouve else [],
            ess=trouve and e.get("est_ess") == "True",
            finess=trouve and e.get("est_finess") == "True",
            enrichi=s in enr,
        ))
    return demandeurs


# ---------------------------------------------------------------- sorties

def main():
    p = argparse.ArgumentParser(description="L'Inspecteur Sitadel : classification des demandeurs")
    p.add_argument("--departement", default="78")
    dep = p.parse_args().departement.zfill(2)

    permis = charger_permis(dep)
    demandeurs = construire_demandeurs(permis)
    nb_permis = permis.groupby("SIREN_DEM").size().to_dict()

    lignes = []
    for d in demandeurs:
        r = classer_demandeur(d)
        lignes.append({
            "siren": d.siren, "denomination": d.nom, "famille": r.famille, "regle": r.id,
            "confiance": r.confiance, "justification": r.texte,
            "cj": d.cj, "cj_libelle": d.cj_libelle, "ape": d.ape, "ape_libelle": d.ape_libelle,
            "convention_hlm_2150": IDCC_HLM in d.idcc, "ess_sirene": d.ess, "finess": d.finess,
            "activite_medico_sociale": d.activite_medico_sociale,
            "nb_permis": nb_permis.get(d.siren, 0),
            "source_sirene": f"https://annuaire-entreprises.data.gouv.fr/entreprise/{d.siren}",
            "source_cj": f"https://api.insee.fr/metadonnees/V1/codes/cj/n3/{d.cj}" if d.cj else "",
            "source_ape": f"https://api.insee.fr/metadonnees/V1/codes/nafr2/sousClasse/{d.ape}" if d.ape_libelle else "",
        })
    dem = pd.DataFrame(lignes)

    # Permis sans SIREN (dénomination seule) : demandeur non identifiable, donc non déterminé.
    out = permis.merge(dem, how="left", left_on="SIREN_DEM", right_on="siren")
    sans = out["famille"].isna()
    out.loc[sans, ["famille", "regle", "confiance", "justification"]] = [
        "non déterminé", "R00", "sûre", "Permis sans SIREN : le demandeur ne peut pas être identifié dans Sirène."]

    def num(col):
        return pd.to_numeric(out[col], errors="coerce") if col in out else None

    permis_out = pd.DataFrame({
        "num_dau": out["NUM_DAU"],
        "fichier": out["fichier"],
        "commune": out["ADR_LOCALITE_TER"],
        "code_commune": out["COMM"],
        "date_autorisation": out["DATE_REELLE_AUTORISATION"],
        "date_ouverture_chantier": out["DATE_REELLE_DOC"],
        "date_achevement": out["DATE_REELLE_DAACT"],
        "etat": out["ETAT_DAU"].map(ETAT).fillna(out["ETAT_DAU"]),
        "nb_logements": num("NB_LGT_TOT_CREES"),
        "nb_logements_sociaux": num("NB_LGT_PRET_LOC_SOCIAL"),
        "residence": out["RESIDENCE"].map(RESIDENCE).fillna(""),
        "service_public": out["TYPE_SERVICE_PUBLIC"].map(SERVICE_PUBLIC).fillna(""),
        "destination_locaux": out["DESTINATION_PRINCIPALE"].where(out["fichier"] == "locaux", "").map(DESTINATION_LOCAUX).fillna(""),
        "surface_service_public_creee": num("SURF_PUB_CREEE"),
        "nb_chambres": num("NB_CHAMBRES"),
        "siren": out["SIREN_DEM"],
        "denomination": out["DENOM_DEM"],
        "famille": out["famille"],
        "regle": out["regle"],
        "confiance": out["confiance"],
        "justification": out["justification"],
        "source_permis": DIDO + "/" + out["fichier"].map(DATAFILES) + "/rows?NUM_DAU=eq:" + out["NUM_DAU"],
        "source_demandeur": out["source_sirene"].fillna(""),
    })

    Path("sorties").mkdir(exist_ok=True)
    dem.sort_values(["famille", "nb_permis"], ascending=[True, False]).to_csv(
        f"sorties/demandeurs_{dep}.csv", sep=";", index=False, encoding="utf-8")
    permis_out.sort_values("date_autorisation", ascending=False).to_csv(
        f"sorties/permis_{dep}.csv", sep=";", index=False, encoding="utf-8")

    print(f"{len(dem)} demandeurs et {len(permis_out)} permis classés -> sorties/")
    resume = (permis_out.groupby("famille")
              .agg(permis=("num_dau", "size"), demandeurs=("siren", "nunique"))
              .sort_values("permis", ascending=False))
    print(resume.to_string())
    print("\nRègles utilisées (demandeurs) :")
    print(dem.groupby(["regle", "famille"]).size().to_string())


if __name__ == "__main__":
    main()
