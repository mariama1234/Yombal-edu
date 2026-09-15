"""
Corbeille : des suppressions qu'on peut défaire.

Supprimer une classe emporte ses inscriptions, ses évaluations, leurs notes :
un clic de trop, et une année de saisie disparaissait sans recours. Plutôt que
de marquer les lignes « supprimées » — ce qui obligerait chaque requête du
projet à les écarter, et il suffirait d'en oublier une pour compter des élèves
fantômes — la suppression reste réelle, et on garde de quoi la défaire :

* `instantane()` recense, avec le collecteur de Django, exactement ce que
  `delete()` va effacer en cascade et les références qu'il va mettre à NULL ;
* `restaurer()` réinsère ces lignes à l'identique, clés primaires comprises,
  puis rétablit les références et les compteurs dénormalisés.

Les clés primaires ne sont pas réattribuées par MySQL après une suppression :
réinsérer avec les mêmes identifiants ne peut entrer en collision qu'avec une
ligne déjà restaurée, ce que `restaurer()` vérifie.
"""
import json
from collections import Counter, defaultdict

from django.apps import apps
from django.core import serializers
from django.db import IntegrityError, connections, router, transaction
from django.db.models import QuerySet
from django.db.models.deletion import Collector

from . import finances, models, scolarite

# Au-delà, un `pk__in` devient une requête démesurée.
TAILLE_TRANCHE = 500


class ErreurCorbeille(Exception):
    """Restauration impossible ; le message s'affiche tel quel."""


def _libelle(instance):
    libelle = str(instance)
    # Sans __str__ propre, Django affiche « Evaluation object (12) ».
    if libelle == f"{type(instance).__name__} object ({instance.pk})":
        libelle = f"{str(instance._meta.verbose_name).capitalize()} n°{instance.pk}"
    return libelle[:255]


def _nommer(modele, nombre):
    meta = modele._meta
    return str(meta.verbose_name if nombre == 1 else meta.verbose_name_plural)


def message_blocage(exc):
    """Texte d'une suppression refusée par un PROTECT ou un RESTRICT."""
    bloquants = (getattr(exc, "protected_objects", None)
                 or getattr(exc, "restricted_objects", None) or ())
    compte = Counter(type(obj) for obj in bloquants)
    detail = ", ".join(f"{n} {_nommer(modele, n)}" for modele, n in compte.items())
    return ("Suppression impossible : des éléments qui ne peuvent pas être "
            f"supprimés avec lui y sont rattachés ({detail}).")


def instantane(instance):
    """
    Prépare l'entrée de corbeille d'un objet, à enregistrer une fois
    `instance.delete()` passé.

    À appeler dans la même transaction que la suppression, juste avant : entre
    les deux, rien ne doit pouvoir modifier ce que la cascade emportera. Lève
    `ProtectedError` ou `RestrictedError` exactement comme la suppression.
    """
    using = router.db_for_write(type(instance), instance=instance)
    # `force_collection` : sans lui, le collecteur efface certaines lignes par
    # une requête directe, sans les charger — elles manqueraient à l'instantané.
    collecteur = Collector(using=using, origin=instance, force_collection=True)
    collecteur.collect([instance])
    collecteur.sort()

    objets, resume = [], Counter()
    # `sort()` range les modèles dans l'ordre de suppression, dépendants
    # d'abord ; l'ordre inverse est celui de la réinsertion.
    for modele in reversed(list(collecteur.data)):
        # Tables de liaison many-to-many : le modèle qui porte le champ les
        # restaure déjà, les réinsérer en plus créerait des doublons.
        if modele._meta.auto_created:
            continue
        pks = sorted(obj.pk for obj in collecteur.data[modele])
        # Rechargées en entier : le collecteur ne lit que les colonnes utiles
        # à la cascade.
        for debut in range(0, len(pks), TAILLE_TRANCHE):
            objets.extend(modele._base_manager.using(using)
                          .filter(pk__in=pks[debut:debut + TAILLE_TRANCHE])
                          .order_by("pk"))
        emportes = len(pks) - (1 if modele is type(instance) else 0)
        if emportes:
            resume[_nommer(modele, emportes)] += emportes

    # Les références mises à NULL par la suppression (un mouvement de caisse
    # qui pointait vers un paiement, l'auteur d'une communication…) : on note
    # leur valeur d'origine pour la rétablir.
    liens = []
    for (champ, _valeur), groupes in collecteur.field_updates.items():
        lignes = []
        for groupe in groupes:
            if isinstance(groupe, QuerySet):
                lignes.extend(groupe.values_list("pk", champ.attname))
            else:
                lignes.extend((obj.pk, getattr(obj, champ.attname)) for obj in groupe)
        lignes = [[pk, valeur] for pk, valeur in lignes if valeur is not None]
        if lignes:
            liens.append({"modele": champ.model._meta.label, "champ": champ.attname,
                          "lignes": lignes})

    return models.Corbeille(
        modele=type(instance)._meta.label,
        entite=instance._meta.db_table,
        id_entite=instance.pk,
        libelle=_libelle(instance),
        donnees={"objets": json.loads(serializers.serialize("json", objets)),
                 "liens": liens},
        resume=dict(resume),
        nb_elements=len(objets),
    )


def _relier(lien, using):
    """Rétablit une référence mise à NULL, sans écraser ce qui a été repris depuis."""
    modele = apps.get_model(lien["modele"])
    champ = lien["champ"]
    par_valeur = defaultdict(list)
    for pk, valeur in lien["lignes"]:
        par_valeur[valeur].append(pk)
    for valeur, pks in par_valeur.items():
        for debut in range(0, len(pks), TAILLE_TRANCHE):
            (modele._base_manager.using(using)
             .filter(pk__in=pks[debut:debut + TAILLE_TRANCHE], **{f"{champ}__isnull": True})
             .update(**{champ: valeur}))
    return modele._meta.db_table


def _recaler(restaures, utilisateur):
    """
    Remet d'aplomb ce que la réinsertion brute ne recalcule pas.

    Les lignes reviennent telles qu'elles étaient, sans repasser par `save()`
    ni par les vues : les invariants tenus là-bas doivent être refaits ici.
    """
    par_modele = defaultdict(list)
    for objet in restaures:
        par_modele[type(objet)].append(objet)

    # Une seule année active par école : une autre a pu être activée depuis.
    Annee = models.AnneeScolaire
    for annee in par_modele[Annee]:
        if (annee.statut == Annee.Statut.ACTIVE
                and Annee.objects.filter(id_ecole_id=annee.id_ecole_id, statut=Annee.Statut.ACTIVE)
                .exclude(pk=annee.pk).exists()):
            Annee.objects.filter(pk=annee.pk).update(statut=Annee.Statut.ARCHIVEE)

    classes = {inscription.id_classe_id for inscription in par_modele[models.Inscription]}
    if classes:
        scolarite.recaler_effectif(*models.Classe.objects.filter(pk__in=classes))

    frais = {paiement.id_frais_eleve_id for paiement in par_modele[models.Paiement]}
    for ligne in models.FraisEleve.objects.filter(pk__in=frais):
        finances.recalculer_statut(ligne)

    # La suppression d'une dépense efface son mouvement de caisse : il faut le
    # réécrire. Restaurée avec sa catégorie, elle retrouve au contraire le sien
    # par `_relier()`, et seul le budget est à recompter.
    for depense in par_modele[models.Depense]:
        if models.JournalCaisse.objects.filter(id_depense_id=depense.pk).exists():
            finances.imputer_au_budget(depense)
        else:
            finances.enregistrer_depense(depense, utilisateur=utilisateur)
    for budget in par_modele[models.Budget]:
        finances.imputer_au_budget(models.Depense(
            id_ecole_id=budget.id_ecole_id, id_type_depense_id=budget.id_type_depense_id,
            date_depense=budget.id_annee_scolaire.date_debut))


def restaurer(entree, utilisateur=None):
    """
    Remet en place un élément de la corbeille et tout ce qu'il avait emporté.

    Tout ou rien : si une ligne ne peut pas revenir — un doublon créé depuis,
    un élément parent supprimé entre-temps — rien n'est écrit et l'entrée
    reste dans la corbeille.
    """
    using = router.db_for_write(models.Corbeille)
    connexion = connections[using]

    with transaction.atomic(using=using):
        # Verrou : deux restaurations simultanées réinséreraient deux fois.
        entree = (models.Corbeille.objects.using(using).select_for_update()
                  .filter(pk=entree.pk).first())
        if entree is None:
            raise ErreurCorbeille("Cet élément a déjà été restauré ou supprimé définitivement.")

        try:
            racine = apps.get_model(entree.modele)
        except LookupError:
            raise ErreurCorbeille("Ce type d'élément n'existe plus dans l'application.")
        if racine._base_manager.using(using).filter(pk=entree.id_entite).exists():
            raise ErreurCorbeille(f"« {entree.libelle} » existe déjà.")

        objets = list(serializers.deserialize(
            "json", json.dumps(entree.donnees["objets"]), using=using))
        tables = {objet.object._meta.db_table for objet in objets}

        # Contrôle des clés étrangères suspendu le temps de réinsérer, puis
        # refait d'un bloc : c'est ce que fait `loaddata`, et cela dispense de
        # trouver un ordre d'insertion valide quand deux tables se référencent.
        doublon = False
        with connexion.constraint_checks_disabled():
            try:
                # Point de sauvegarde : une erreur ici doit laisser la
                # connexion utilisable pour réactiver le contrôle.
                with transaction.atomic(using=using):
                    for objet in objets:
                        objet.save(using=using)
                    for lien in entree.donnees.get("liens", []):
                        tables.add(_relier(lien, using))
            except IntegrityError:
                doublon = True
        if doublon:
            raise ErreurCorbeille(
                f"« {entree.libelle} » ne peut pas être restauré : un élément identique "
                "(même libellé, même matricule…) a été créé depuis."
            )

        try:
            connexion.check_constraints(table_names=sorted(tables))
        except IntegrityError:
            raise ErreurCorbeille(
                f"« {entree.libelle} » ne peut pas être restauré : un élément dont il "
                "dépend a été supprimé depuis. Restaurez d'abord celui-là."
            )

        _recaler([objet.object for objet in objets], utilisateur)
        entree.delete()
    return entree
