"""
Documents financiers : état des impayés, journal de caisse, bilan, reçu.

Chaque fonction renvoie un triplet `(contenu, nom_fichier, type_mime)` prêt à
être posé dans une `HttpResponse`. Les deux formats partent des mêmes données :
le PDF sert à imprimer et à remettre au parent, le `.xlsx` à retravailler les
chiffres — c'est exactement la demande du TDR (« Exports Excel & PDF »).

`exports.py` fournit la mécanique (classeur, moteur PDF) ; ici on ne décide
que du contenu et de la mise en forme métier.
"""
from __future__ import annotations

from datetime import date

from . import exports

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"

DEVISE = "F CFA"


def formater_montant(valeur, devise=False):
    """25000 → « 25 000 F CFA ». L'espace fine des locales n'est pas garantie
    dans les polices PDF standard : on garde une espace ordinaire."""
    try:
        entier = int(round(float(valeur or 0)))
    except (TypeError, ValueError):
        return str(valeur)
    texte = f"{entier:,}".replace(",", " ")
    return f"{texte} {DEVISE}" if devise else texte


def _jour(valeur):
    if valeur is None:
        return ""
    if isinstance(valeur, date):
        return valeur.strftime("%d/%m/%Y")
    return valeur.strftime("%d/%m/%Y %H:%M")


def _sous_titre(ecole, complement=None):
    morceaux = [ecole.nom]
    if ecole.telephone:
        morceaux.append(ecole.telephone)
    if complement:
        morceaux.append(complement)
    return " · ".join(morceaux)


# Le nom des fichiers est composé par `exports` : tous les documents de
# l'application le nomment de la même façon.
nom_document = exports.nom_document
_morceau = exports.slugifier


def _nom_etat(document, ecole, extension, precision=None):
    """
    Nom d'un état daté : « etat-des-impayes-ecole-mic-cite-2026-09-16.xlsx ».

    L'école figure dans le nom parce que ces fichiers circulent par e-mail
    entre plusieurs établissements gérés par la même personne ; la date, parce
    qu'un état des impayés du 3 et du 10 ne se distinguent pas autrement.
    """
    return exports.nom_document(document, ecole.nom, precision,
                                extension=extension, date_du_jour=True)


# ---------------------------------------------------------------------------
# État des impayés
# ---------------------------------------------------------------------------

_ENTETES_IMPAYES = ["Matricule", "Élève", "Classe", "Type de frais", "Échéance",
                    "Statut", "Montant dû", "Payé", "Solde"]


def _lignes_impayes(frais):
    aujourdhui = date.today()
    lignes = []
    for f in frais:
        inscription = (f.id_eleve.inscriptions
                       .filter(id_annee_scolaire_id=f.id_annee_scolaire_id)
                       .select_related("id_classe").first())
        retard = f.echeance and f.echeance < aujourdhui
        lignes.append({
            "matricule": f.id_eleve.matricule,
            "eleve": f"{f.id_eleve.prenom} {f.id_eleve.nom}",
            "classe": inscription.id_classe.nom if inscription else "",
            "type": f.id_type_frais.libelle,
            "echeance": f.echeance,
            "statut": ("En retard" if retard else f.get_statut_display()),
            # Le dû net de remise, pour que l'export réclame exactement ce que
            # l'écran et le reçu annoncent.
            "du": f.montant_net,
            "paye": f.montant_paye,
            "solde": f.solde_restant,
        })
    return lignes


def impayes(ecole, frais, format="xlsx"):
    lignes = _lignes_impayes(frais)
    total = sum(l["solde"] for l in lignes) if lignes else 0

    if format == "pdf":
        doc = exports.DocumentPDF(
            "État des impayés",
            _sous_titre(ecole, f"{len(lignes)} frais non soldés"),
            pied=ecole.nom, paysage=True,
        )
        doc.encadre("Reste à recouvrer", formater_montant(total, devise=True))
        doc.tableau(
            [("Matricule", 90, "g"), ("Élève", 190, "g"), ("Classe", 90, "g"),
             ("Type de frais", 120, "g"), ("Échéance", 85, "g"), ("Statut", 85, "g"),
             ("Montant dû", 100, "d"), ("Payé", 100, "d"), ("Solde", 100, "d")],
            [[l["matricule"], l["eleve"], l["classe"], l["type"], _jour(l["echeance"]),
              l["statut"], formater_montant(l["du"]), formater_montant(l["paye"]),
              formater_montant(l["solde"])] for l in lignes],
            total=["", "", "", "", "", "TOTAL", "", "", formater_montant(total)],
        )
        return doc.rendu(), _nom_etat("etat-des-impayes", ecole, "pdf"), PDF

    contenu = exports.classeur_xlsx([(
        "Impayés", _ENTETES_IMPAYES,
        [[l["matricule"], l["eleve"], l["classe"], l["type"], _jour(l["echeance"]),
          l["statut"], float(l["du"]), float(l["paye"]), float(l["solde"])] for l in lignes],
    )])
    return contenu, _nom_etat("etat-des-impayes", ecole, "xlsx"), XLSX


# ---------------------------------------------------------------------------
# Journal de caisse / banque
# ---------------------------------------------------------------------------

_ENTETES_JOURNAL = ["Date", "Journal", "Libellé", "Débit", "Crédit", "Solde après"]


def journal(ecole, mouvements, format="xlsx", titre="Journal de caisse"):
    mouvements = list(mouvements)
    debits = sum(m.debit for m in mouvements) if mouvements else 0
    credits = sum(m.credit for m in mouvements) if mouvements else 0

    lignes = [[_jour(m.date_mouvement), m.get_type_journal_display(), m.libelle,
               m.debit, m.credit, m.solde_apres] for m in mouvements]

    if format == "pdf":
        doc = exports.DocumentPDF(titre, _sous_titre(ecole, f"{len(lignes)} mouvements"),
                                  pied=ecole.nom, paysage=True)
        doc.encadre("Solde", formater_montant(credits - debits, devise=True))
        doc.tableau(
            [("Date", 90, "g"), ("Journal", 80, "g"), ("Libellé", 330, "g"),
             ("Débit", 110, "d"), ("Crédit", 110, "d"), ("Solde après", 110, "d")],
            [[l[0], l[1], l[2], formater_montant(l[3]), formater_montant(l[4]),
              formater_montant(l[5])] for l in lignes],
            total=["", "", "TOTAL", formater_montant(debits), formater_montant(credits), ""],
        )
        return doc.rendu(), _nom_etat(titre, ecole, "pdf"), PDF

    contenu = exports.classeur_xlsx([(
        "Journal", _ENTETES_JOURNAL,
        [[l[0], l[1], l[2], float(l[3] or 0), float(l[4] or 0),
          float(l[5] or 0)] for l in lignes],
    )])
    return contenu, _nom_etat(titre, ecole, "xlsx"), XLSX


# ---------------------------------------------------------------------------
# Dépenses
# ---------------------------------------------------------------------------

def depenses(ecole, liste, format="xlsx"):
    liste = list(liste)
    total = sum(d.montant for d in liste) if liste else 0
    lignes = [[_jour(d.date_depense), d.id_type_depense.libelle, d.motif,
               d.justificatif_url or "", d.montant] for d in liste]

    if format == "pdf":
        doc = exports.DocumentPDF("Dépenses", _sous_titre(ecole, f"{len(lignes)} lignes"),
                                  pied=ecole.nom)
        doc.encadre("Total des dépenses", formater_montant(total, devise=True))
        doc.tableau(
            [("Date", 90, "g"), ("Catégorie", 130, "g"), ("Motif", 220, "g"),
             ("Montant", 120, "d")],
            [[l[0], l[1], l[2], formater_montant(l[4])] for l in lignes],
            total=["", "", "TOTAL", formater_montant(total)],
        )
        return doc.rendu(), _nom_etat("depenses", ecole, "pdf"), PDF

    contenu = exports.classeur_xlsx([(
        "Dépenses", ["Date", "Catégorie", "Motif", "Justificatif", "Montant"],
        [[l[0], l[1], l[2], l[3], float(l[4])] for l in lignes],
    )])
    return contenu, _nom_etat("depenses", ecole, "xlsx"), XLSX


# ---------------------------------------------------------------------------
# Bilan financier
# ---------------------------------------------------------------------------

def bilan(ecole, donnees, format="xlsx"):
    periode = donnees["periode"]
    intitule = periode.get("annee") or (
        f"{_jour(_date(periode['debut']))} → {_jour(_date(periode['fin']))}"
        if periode.get("debut") else "Tout l'historique"
    )

    if format == "pdf":
        return _bilan_pdf(ecole, donnees, intitule)

    recouvrement = donnees["recouvrement"]
    resultat = donnees["resultat"]
    tresorerie = donnees["tresorerie"]

    feuilles = [
        ("Synthèse", ["Indicateur", "Montant"], [
            ["Période", intitule],
            ["Total attendu", recouvrement["attendu"]],
            ["Total encaissé", recouvrement["encaisse"]],
            ["Reste à recouvrer", recouvrement["reste"]],
            ["Taux de recouvrement (%)", recouvrement["taux"]],
            ["Total des dépenses", resultat["depenses"]],
            ["Résultat (recettes − dépenses)", resultat["solde"]],
            ["Solde caisse", tresorerie["caisse"]],
            ["Solde banque", tresorerie["banque"]],
            ["Trésorerie totale", tresorerie["total"]],
            ["Frais impayés (nombre)", donnees["impayes"]["nombre"]],
            ["Frais échus impayés", donnees["impayes"]["echus"]],
            ["Élèves concernés", donnees["impayes"]["eleves_concernes"]],
        ]),
        ("Recettes par frais", ["Type de frais", "Nombre", "Montant"],
         [[l["libelle"], l["nombre"], l["montant"]] for l in donnees["recettes"]["par_type_frais"]]),
        ("Recettes par moyen", ["Moyen de paiement", "Nombre", "Montant"],
         [[l["libelle"], l["nombre"], l["montant"]] for l in donnees["recettes"]["par_methode"]]),
        ("Dépenses", ["Catégorie", "Nombre", "Montant"],
         [[l["libelle"], l["nombre"], l["montant"]] for l in donnees["depenses"]["par_categorie"]]),
        ("Budgets", ["Catégorie", "Prévu", "Réalisé", "Écart", "Consommé (%)"],
         [[b["categorie"], b["prevu"], b["realise"], b["ecart"], b["consomme"]]
          for b in donnees["budgets"]]),
        ("Évolution", ["Mois", "Recettes", "Dépenses", "Solde"],
         [[m["mois"], m["recettes"], m["depenses"], m["recettes"] - m["depenses"]]
          for m in donnees["evolution"]]),
    ]
    return (exports.classeur_xlsx(feuilles),
            exports.nom_document("bilan-financier", ecole.nom, intitule, extension="xlsx"),
            XLSX)


def _date(texte):
    return date.fromisoformat(texte) if texte else None


def _bilan_pdf(ecole, donnees, intitule):
    recouvrement = donnees["recouvrement"]
    resultat = donnees["resultat"]
    tresorerie = donnees["tresorerie"]

    doc = exports.DocumentPDF("Bilan financier", _sous_titre(ecole, intitule), pied=ecole.nom)

    doc.encadre("Résultat de la période (recettes − dépenses)",
                formater_montant(resultat["solde"], devise=True))

    doc.section("Recouvrement")
    doc.champs([
        ("Total attendu", formater_montant(recouvrement["attendu"], devise=True)),
        ("Total encaissé", formater_montant(recouvrement["encaisse"], devise=True)),
        ("Reste à recouvrer", formater_montant(recouvrement["reste"], devise=True)),
        ("Taux de recouvrement", f"{recouvrement['taux']} %"),
        ("Frais impayés", f"{donnees['impayes']['nombre']} dont "
                          f"{donnees['impayes']['echus']} échus "
                          f"({donnees['impayes']['eleves_concernes']} élèves)"),
    ], largeur_libelle=170)

    doc.section("Trésorerie")
    doc.champs([
        ("Solde caisse", formater_montant(tresorerie["caisse"], devise=True)),
        ("Solde banque", formater_montant(tresorerie["banque"], devise=True)),
        ("Total", formater_montant(tresorerie["total"], devise=True)),
    ], largeur_libelle=170)

    if donnees["recettes"]["par_type_frais"]:
        doc.section("Recettes par type de frais")
        doc.tableau(
            [("Type de frais", 240, "g"), ("Encaissements", 110, "d"), ("Montant", 150, "d")],
            [[l["libelle"], str(l["nombre"]), formater_montant(l["montant"])]
             for l in donnees["recettes"]["par_type_frais"]],
            total=["TOTAL", "", formater_montant(donnees["recettes"]["total"])],
        )

    if donnees["recettes"]["par_methode"]:
        doc.section("Recettes par moyen de paiement")
        doc.tableau(
            [("Moyen de paiement", 240, "g"), ("Opérations", 110, "d"), ("Montant", 150, "d")],
            [[l["libelle"], str(l["nombre"]), formater_montant(l["montant"])]
             for l in donnees["recettes"]["par_methode"]],
        )

    if donnees["depenses"]["par_categorie"]:
        doc.section("Dépenses par catégorie")
        doc.tableau(
            [("Catégorie", 240, "g"), ("Lignes", 110, "d"), ("Montant", 150, "d")],
            [[l["libelle"], str(l["nombre"]), formater_montant(l["montant"])]
             for l in donnees["depenses"]["par_categorie"]],
            total=["TOTAL", "", formater_montant(donnees["depenses"]["total"])],
        )

    if donnees["budgets"]:
        doc.section("Suivi budgétaire")
        doc.tableau(
            [("Catégorie", 200, "g"), ("Prévu", 120, "d"), ("Réalisé", 120, "d"),
             ("Écart", 120, "d"), ("Consommé", 80, "d")],
            [[b["categorie"], formater_montant(b["prevu"]), formater_montant(b["realise"]),
              formater_montant(b["ecart"]), f"{b['consomme']} %"] for b in donnees["budgets"]],
        )

    if donnees["evolution"]:
        doc.section("Évolution mensuelle")
        doc.tableau(
            [("Mois", 140, "g"), ("Recettes", 150, "d"), ("Dépenses", 150, "d"),
             ("Solde", 150, "d")],
            [[m["mois"], formater_montant(m["recettes"]), formater_montant(m["depenses"]),
              formater_montant(m["recettes"] - m["depenses"])] for m in donnees["evolution"]],
        )

    return (doc.rendu(),
            exports.nom_document("bilan-financier", ecole.nom, intitule, extension="pdf"),
            PDF)


# ---------------------------------------------------------------------------
# Reçu de paiement
# ---------------------------------------------------------------------------

def recu(objet):
    """
    Reçu remis au parent : le justificatif que le TDR demande automatique.

    Il ne se contente pas d'attester le versement : il montre l'échéancier de
    l'année et ce qu'il reste à payer. C'est la question que la famille pose
    en repartant du guichet, et y répondre sur le papier évite un aller-retour.
    """
    paiement = objet.id_paiement
    frais = paiement.id_frais_eleve
    eleve = frais.id_eleve
    ecole = eleve.id_ecole
    annee = frais.id_annee_scolaire

    inscription = (eleve.inscriptions
                   .filter(id_annee_scolaire_id=annee.pk)
                   .select_related("id_classe").first())
    classe = inscription.id_classe.nom if inscription else None

    doc = exports.DocumentPDF(
        f"Reçu n° {objet.numero_recu}",
        _sous_titre(ecole, ecole.adresse or None), pied=ecole.nom)

    doc.encadre("Montant reçu", formater_montant(paiement.montant, devise=True))

    # -- Qui, et pour quelle scolarité ------------------------------------
    doc.section("Élève")
    doc.tableau(
        [("Nom et prénom", 200, "g"), ("Matricule", 130, "g"),
         ("Classe", 110, "g"), ("Année scolaire", 110, "g")],
        [[f"{eleve.prenom} {eleve.nom}", eleve.matricule, classe or "—", annee.libelle]],
    )

    # -- Le versement lui-même --------------------------------------------
    doc.section("Détail du règlement")
    doc.tableau(
        [("Frais réglé", 190, "g"), ("Échéance", 90, "g"), ("Moyen", 110, "g"),
         ("Référence", 110, "g"), ("Montant", 100, "d")],
        [[
            frais.id_type_frais.libelle,
            _jour(frais.echeance) or "—",
            paiement.get_methode_display(),
            paiement.reference_transaction or "—",
            formater_montant(paiement.montant),
        ]],
    )
    doc.champs([
        ("Date du paiement", _jour(paiement.date_paiement)),
        ("Encaissé par", (f"{paiement.id_utilisateur_caissier.prenom} "
                          f"{paiement.id_utilisateur_caissier.nom}")
         if paiement.id_utilisateur_caissier else "—"),
    ] + ([("Commentaire", paiement.commentaire)] if paiement.commentaire else []))

    # -- Où en est l'année ------------------------------------------------
    # Deux requêtes suffisent : les paiements sont préchargés, et le solde de
    # chaque ligne se lit sur les propriétés du modèle, jamais recalculé ici.
    lignes_annee = list(
        type(frais).objects
        .filter(id_eleve=eleve, id_annee_scolaire=annee)
        .select_related("id_type_frais")
        .prefetch_related("paiements")
        .order_by("echeance", "pk")
    )
    if lignes_annee:
        doc.section(f"Situation de l'année {annee.libelle}")
        total_du = total_paye = 0
        corps = []
        for ligne in lignes_annee:
            net, regle = ligne.montant_net, ligne.montant_paye
            total_du += net
            total_paye += regle
            corps.append([
                ligne.id_type_frais.libelle,
                _jour(ligne.echeance) or "—",
                formater_montant(net),
                formater_montant(regle),
                formater_montant(net - regle),
                # La ligne qui vient d'être réglée se repère d'un coup d'œil.
                ("« ce reçu »" if ligne.pk == frais.pk
                 else ligne.get_statut_display()),
            ])
        doc.tableau(
            [("Frais", 170, "g"), ("Échéance", 85, "g"), ("Dû", 95, "d"),
             ("Réglé", 95, "d"), ("Reste", 95, "d"), ("Statut", 100, "g")],
            corps,
            total=["Total", "", formater_montant(total_du),
                   formater_montant(total_paye),
                   formater_montant(total_du - total_paye), ""],
        )
        doc.encadre("Reste à payer sur l'année",
                    formater_montant(total_du - total_paye, devise=True))

    doc.espace(14)
    doc.paragraphe(
        "Ce reçu est délivré automatiquement par le système de gestion de "
        f"{ecole.nom}. Il atteste du paiement ci-dessus et doit être conservé "
        "par la famille.", taille=8.5, gris=0.45)

    nom = nom_document("recu", objet.numero_recu, eleve.prenom, eleve.nom,
                       classe, annee.libelle)
    return doc.rendu(), nom, PDF


# ---------------------------------------------------------------------------
# Recettes détaillées (liste des paiements)
# ---------------------------------------------------------------------------

def paiements(ecole, liste, format="xlsx"):
    liste = list(liste)
    total = sum(p.montant for p in liste) if liste else 0
    lignes = []
    for p in liste:
        frais = p.id_frais_eleve
        lignes.append([
            _jour(p.date_paiement),
            getattr(getattr(p, "recu", None), "numero_recu", ""),
            frais.id_eleve.matricule,
            f"{frais.id_eleve.prenom} {frais.id_eleve.nom}",
            frais.id_type_frais.libelle,
            p.get_methode_display(),
            p.reference_transaction or "",
            p.montant,
        ])

    if format == "pdf":
        doc = exports.DocumentPDF("Journal des recettes",
                                  _sous_titre(ecole, f"{len(lignes)} encaissements"),
                                  pied=ecole.nom, paysage=True)
        doc.encadre("Total encaissé", formater_montant(total, devise=True))
        doc.tableau(
            [("Date", 110, "g"), ("Reçu", 90, "g"), ("Matricule", 90, "g"),
             ("Élève", 180, "g"), ("Type de frais", 120, "g"), ("Moyen", 110, "g"),
             ("Référence", 110, "g"), ("Montant", 110, "d")],
            [[*l[:-1], formater_montant(l[-1])] for l in lignes],
            total=["", "", "", "", "", "", "TOTAL", formater_montant(total)],
        )
        return doc.rendu(), _nom_etat("journal-des-recettes", ecole, "pdf"), PDF

    contenu = exports.classeur_xlsx([(
        "Recettes",
        ["Date", "Reçu", "Matricule", "Élève", "Type de frais", "Moyen", "Référence", "Montant"],
        [[*l[:-1], float(l[-1])] for l in lignes],
    )])
    return contenu, _nom_etat("journal-des-recettes", ecole, "xlsx"), XLSX


# ---------------------------------------------------------------------------
# Facture d'inscription et reçu du guichet
# ---------------------------------------------------------------------------

def facture(ecole, donnees):
    """
    Facture remise à l'inscription : matricule de l'élève et montant total dû.

    C'est ce papier que la famille présente à la caisse ; la caissière y lit le
    matricule qui ouvre le dossier au guichet.
    """
    doc = exports.DocumentPDF(
        f"Facture — {donnees['annee']}", _sous_titre(ecole, ecole.adresse or None), pied=ecole.nom)
    doc.encadre("Montant total dû", formater_montant(donnees["total_du"], devise=True))

    doc.section("Élève")
    doc.champs([
        ("Matricule", donnees["matricule"]),
        ("Nom", donnees["nom"]),
        ("Prénom", donnees["prenom"]),
        ("Classe", donnees["classe"] or "—"),
        ("Année scolaire", donnees["annee"]),
        ("Date", _jour(date.today())),
    ])

    doc.section("Frais")
    doc.tableau(
        [("Frais", 330, "g"), ("Montant", 170, "d")],
        [[ligne["libelle"], formater_montant(ligne["montant"])] for ligne in donnees["lignes"]],
        total=["Montant total dû", formater_montant(donnees["total_du"])],
    )
    if float(donnees["total_paye"] or 0) > 0:
        doc.champs([
            ("Déjà réglé", formater_montant(donnees["total_paye"], devise=True)),
            ("Reste à payer", formater_montant(donnees["reste"], devise=True)),
        ])

    doc.espace(14)
    doc.paragraphe(
        "Présentez cette facture à la caisse : le matricule ci-dessus permet d'y "
        "retrouver le dossier et d'enregistrer le règlement.", taille=8.5, gris=0.45)

    nom = nom_document("facture", donnees["matricule"], donnees["prenom"], donnees["nom"],
                       donnees["classe"], donnees["annee"])
    return doc.rendu(), nom, PDF


def recu_encaissement(ecole, paiements):
    """
    Reçu du guichet : matricule, nom, prénom, date, montant payé et montant
    restant. Un versement réparti sur plusieurs frais reste un seul reçu.
    """
    from django.core.exceptions import ObjectDoesNotExist

    from . import finances

    premier = paiements[0]
    eleve = premier.id_frais_eleve.id_eleve
    annee = premier.id_frais_eleve.id_annee_scolaire
    situation = finances.facture(eleve, annee)
    total = sum(p.montant for p in paiements)
    try:
        numero = premier.recu.numero_recu
    except ObjectDoesNotExist:
        numero = f"PAI-{premier.pk:06d}"

    doc = exports.DocumentPDF(
        f"Reçu de paiement n° {numero}", _sous_titre(ecole, ecole.adresse or None), pied=ecole.nom)
    doc.encadre("Montant payé", formater_montant(total, devise=True))

    doc.section("Élève")
    doc.champs([
        ("Matricule", eleve.matricule),
        ("Nom", eleve.nom),
        ("Prénom", eleve.prenom),
        ("Classe", situation["classe"] or "—"),
        ("Année scolaire", annee.libelle),
        ("Date", _jour(premier.date_paiement)),
        ("Moyen de paiement", premier.get_methode_display()),
    ])

    doc.section("Frais réglés")
    doc.tableau(
        [("Frais", 330, "g"), ("Montant", 170, "d")],
        [[p.id_frais_eleve.id_type_frais.libelle, formater_montant(p.montant)]
         for p in paiements],
        total=["Montant payé", formater_montant(total)],
    )
    doc.encadre("Montant restant", formater_montant(situation["reste"], devise=True))
    doc.champs([("Inscription", situation["statut_inscription_libelle"] or "—")])

    doc.espace(14)
    doc.paragraphe(
        "Ce reçu atteste du paiement ci-dessus et doit être conservé par la famille.",
        taille=8.5, gris=0.45)

    nom = nom_document("recu", numero, eleve.prenom, eleve.nom, situation["classe"], annee.libelle)
    return doc.rendu(), nom, PDF
