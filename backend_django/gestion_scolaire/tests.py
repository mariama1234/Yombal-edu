"""
Tests de l'API.

    python manage.py test gestion_scolaire

Deux écoles sont créées systématiquement : l'isolation multi-écoles est
l'invariant central du produit, et la plupart des régressions graves
consisteraient à la percer.
"""
import io
import zipfile
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import (exports, finances, finances_documents, imports_eleves, models,
               pedagogie, photos, scolarite)

Utilisateur = get_user_model()
MDP = "motdepasse123"


class BaseAPI(TestCase):
    """Deux écoles complètes, et des clients authentifiés pour chacune."""

    def setUp(self):
        self.ecole = models.Ecole.objects.create(nom="École A")
        self.autre = models.Ecole.objects.create(nom="École B")

        self.admin = self._compte("admin@a.sn", self.ecole, "admin")
        self.enseignant_user = self._compte("prof@a.sn", self.ecole, "enseignant")
        self.parent_user = self._compte("parent@a.sn", self.ecole, "parent")
        self.admin_autre = self._compte("admin@b.sn", self.autre, "admin")

        self.annee = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31),
        )
        self.niveau = models.Niveau.objects.create(id_ecole=self.ecole, libelle="6ème")
        self.classe = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème A", capacite_max=40,
        )
        self.matiere = models.Matiere.objects.create(id_ecole=self.ecole, nom="Mathématiques")
        self.enseignant = models.Enseignant.objects.create(
            id_ecole=self.ecole, id_utilisateur=self.enseignant_user, matricule="ENS-001",
        )

        self.client = self._client(self.admin.email)
        self.client_autre = self._client(self.admin_autre.email)

    def _compte(self, email, ecole, role):
        return Utilisateur.objects.create_user(
            email=email, id_ecole=ecole, nom="Test", prenom=role.capitalize(),
            role=role, password=MDP,
        )

    def _client(self, email):
        c = APIClient()
        reponse = c.post("/api/auth/token/", {"email": email, "password": MDP}, format="json")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {reponse.data['access']}")
        return c

    def resultats(self, reponse):
        return reponse.data.get("results", reponse.data)


class TestAuthentificationEtAudit(BaseAPI):
    def test_connexion_journalisee(self):
        self.assertTrue(models.JournalActivite.objects.filter(
            action="connexion", id_utilisateur=self.admin).exists())

    def test_echec_connexion_journalise_sans_rattacher_le_compte(self):
        APIClient().post("/api/auth/token/",
                         {"email": self.admin.email, "password": "faux"}, format="json")
        entree = models.JournalActivite.objects.filter(action="echec_connexion").first()
        self.assertIsNotNone(entree)
        self.assertIsNone(entree.id_utilisateur, "un échec ne doit pas être attribué au compte")

    def test_ecriture_tracee_avec_entite_et_identifiant(self):
        r = self.client.post("/api/matieres/", {"nom": "Français", "id_ecole": self.ecole.pk},
                             format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(models.JournalActivite.objects.filter(
            action="creation", entite="matiere", id_entite=r.data["id"]).exists())

    def test_suppression_tracee_apres_coup(self):
        r = self.client.post("/api/matieres/", {"nom": "Anglais", "id_ecole": self.ecole.pk},
                             format="json")
        pk = r.data["id"]
        self.client.delete(f"/api/matieres/{pk}/")
        self.assertTrue(models.JournalActivite.objects.filter(
            action="suppression", entite="matiere", id_entite=pk).exists())

    def test_journal_reserve_aux_admins(self):
        self.assertEqual(self.client.get("/api/journal-activite/").status_code, 200)
        self.assertEqual(self._client(self.parent_user.email)
                         .get("/api/journal-activite/").status_code, 403)


class TestIsolationMultiEcoles(BaseAPI):
    def test_les_listes_ne_croisent_pas_les_ecoles(self):
        """L'école B n'a que son admin : tout le reste doit lui être invisible."""
        for chemin in ["matieres", "classes", "niveaux", "annees-scolaires",
                       "enseignants", "periodes", "eleves", "coefficients",
                       "affectations", "parents"]:
            with self.subTest(endpoint=chemin):
                r = self.client_autre.get(f"/api/{chemin}/")
                self.assertEqual(r.status_code, 200, r.content)
                self.assertEqual(len(self.resultats(r)), 0,
                                 f"/api/{chemin}/ laisse fuir des données d'une autre école")

    def test_liste_des_comptes_limitee_a_son_ecole(self):
        r = self.client_autre.get("/api/utilisateurs/")
        self.assertEqual(r.status_code, 200)
        emails = {u["email"] for u in self.resultats(r)}
        self.assertEqual(emails, {self.admin_autre.email})

    def test_acces_direct_a_un_objet_d_une_autre_ecole_refuse(self):
        """Le filtrage de liste ne suffit pas : l'accès par identifiant aussi."""
        r = self.client_autre.get(f"/api/classes/{self.classe.pk}/")
        self.assertEqual(r.status_code, 404)
        r = self.client_autre.patch(f"/api/matieres/{self.matiere.pk}/",
                                    {"nom": "Piraté"}, format="json")
        self.assertEqual(r.status_code, 404)
        self.matiere.refresh_from_db()
        self.assertEqual(self.matiere.nom, "Mathématiques")

    def test_inscription_ignore_l_ecole_envoyee_par_le_client(self):
        """Le corps de la requête ne doit jamais décider de l'école."""
        r = self.client.post("/api/eleves/inscrire/", {
            "id_ecole": self.autre.pk,          # tentative d'injection
            "nom": "Fall", "prenom": "Moussa",
            "id_classe": self.classe.pk, "id_annee_scolaire": self.annee.pk,
            "date_inscription": "2026-10-01",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Eleve.objects.get(pk=r.data["id"]).id_ecole_id, self.ecole.pk)

    def test_classe_d_une_autre_ecole_refusee(self):
        classe_b = models.Classe.objects.create(
            id_ecole=self.autre, id_niveau=models.Niveau.objects.create(
                id_ecole=self.autre, libelle="6ème"),
            id_annee_scolaire=models.AnneeScolaire.objects.create(
                id_ecole=self.autre, libelle="2026-2027",
                date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31)),
            nom="6ème B", capacite_max=30,
        )
        r = self.client.post("/api/eleves/inscrire/", {
            "nom": "X", "prenom": "Y", "id_classe": classe_b.pk,
            "id_annee_scolaire": self.annee.pk, "date_inscription": "2026-10-01",
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)


class TestEcritureImposeeSurSonEcole(BaseAPI):
    """
    Filtrer la lecture ne suffit pas : sans imposer id_ecole à l'écriture, un
    client peut créer des enregistrements dans l'école d'autrui — invisibles
    chez lui, bien réels chez la victime.
    """

    # (endpoint, charge utile minimale)
    CREATIONS = [
        ("matieres", {"nom": "Physique"}),
        ("niveaux", {"libelle": "5ème"}),
        ("salles", {"nom": "Salle 12", "capacite": 30}),
        ("parents", {"nom": "Diouf", "prenom": "Awa", "telephone": "+221770000009"}),
        ("types-depenses", {"libelle": "Fournitures"}),
        ("types-frais", {"libelle": "transport", "montant_defaut": "5000.00"}),
        ("enseignants", {"matricule": "ENS-777", "specialite": "Anglais"}),
    ]

    def test_creation_d_une_classe(self):
        """Cas à FK multiples, le plus représentatif du formulaire générique."""
        r = self.client.post("/api/classes/", {
            "nom": "6ème C", "id_niveau": self.niveau.pk,
            "id_annee_scolaire": self.annee.pk, "capacite_max": 35,
            "id_ecole": self.autre.pk,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["id_ecole"], self.ecole.pk)

    def test_creation_sans_id_ecole_acceptee(self):
        """Le formulaire générique n'envoie pas id_ecole : il ne doit pas avoir à le faire."""
        for endpoint, charge in self.CREATIONS:
            with self.subTest(endpoint=endpoint):
                r = self.client.post(f"/api/{endpoint}/", charge, format="json")
                self.assertEqual(r.status_code, 201, f"/api/{endpoint}/ : {r.content}")
                self.assertEqual(r.data["id_ecole"], self.ecole.pk)

    def test_id_ecole_envoye_par_le_client_est_ignore(self):
        for endpoint, charge in self.CREATIONS:
            with self.subTest(endpoint=endpoint):
                r = self.client.post(f"/api/{endpoint}/",
                                     {**charge, "id_ecole": self.autre.pk}, format="json")
                self.assertEqual(r.status_code, 201, f"/api/{endpoint}/ : {r.content}")
                self.assertEqual(r.data["id_ecole"], self.ecole.pk,
                                 f"/api/{endpoint}/ a écrit dans une autre école")

    def test_creation_d_une_annee_scolaire(self):
        r = self.client.post("/api/annees-scolaires/", {
            "libelle": "2027-2028", "date_debut": "2027-10-01", "date_fin": "2028-07-31",
            "id_ecole": self.autre.pk,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["id_ecole"], self.ecole.pk)


class TestFicheEcole(BaseAPI):
    def test_un_admin_ne_voit_que_son_ecole(self):
        r = self.client.get("/api/ecoles/")
        self.assertEqual(r.status_code, 200)
        noms = [e["nom"] for e in self.resultats(r)]
        self.assertEqual(noms, ["École A"])

    def test_impossible_de_modifier_l_ecole_d_autrui(self):
        r = self.client.patch(f"/api/ecoles/{self.autre.pk}/",
                              {"nom": "Renommée"}, format="json")
        self.assertEqual(r.status_code, 404)
        self.autre.refresh_from_db()
        self.assertEqual(self.autre.nom, "École B")

    def test_modification_de_sa_propre_ecole(self):
        r = self.client.patch(f"/api/ecoles/{self.ecole.pk}/",
                              {"telephone": "+221338000000"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def test_abonnement_et_statut_non_modifiables_par_le_client(self):
        r = self.client.patch(f"/api/ecoles/{self.ecole.pk}/",
                              {"plan_abonnement": "premium", "statut": "suspendu"},
                              format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.ecole.refresh_from_db()
        self.assertEqual(self.ecole.plan_abonnement, "essai")
        self.assertEqual(self.ecole.statut, "actif")

    def test_creation_et_suppression_d_ecole_fermees(self):
        self.assertEqual(self.client.post("/api/ecoles/", {"nom": "Nouvelle"},
                                          format="json").status_code, 405)
        self.assertEqual(self.client.delete(f"/api/ecoles/{self.ecole.pk}/").status_code, 405)


class TestSurfaceAPI(BaseAPI):
    """Contrôles appliqués à tous les endpoints, y compris ceux sans test dédié."""

    def endpoints(self):
        from .urls import router
        return [prefix for prefix, _, _ in router.registry]

    def test_tous_les_endpoints_exigent_une_authentification(self):
        anonyme = APIClient()
        for prefix in self.endpoints():
            with self.subTest(endpoint=prefix):
                r = anonyme.get(f"/api/{prefix}/")
                self.assertEqual(r.status_code, 401,
                                 f"/api/{prefix}/ répond sans authentification")

    def test_tous_les_endpoints_repondent_a_un_compte_valide(self):
        """Aucun endpoint ne doit tomber en 500 sur une simple liste."""
        for prefix in self.endpoints():
            with self.subTest(endpoint=prefix):
                r = self.client.get(f"/api/{prefix}/")
                self.assertIn(r.status_code, (200, 403),
                              f"/api/{prefix}/ renvoie {r.status_code}")


class TestComptes(BaseAPI):
    def test_creation_compte_hache_le_mot_de_passe(self):
        r = self.client.post("/api/utilisateurs/", {
            "nom": "Sow", "prenom": "Awa", "email": "awa@a.sn",
            "role": "caissier", "password": MDP,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        u = Utilisateur.objects.get(email="awa@a.sn")
        self.assertNotEqual(u.password, MDP)
        self.assertTrue(u.check_password(MDP))
        self.assertNotIn("password", r.data)

    def test_compte_cree_dans_l_ecole_de_l_admin(self):
        r = self.client.post("/api/utilisateurs/", {
            "nom": "N", "prenom": "P", "email": "n@a.sn", "role": "parent",
            "password": MDP, "id_ecole": self.autre.pk,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Utilisateur.objects.get(email="n@a.sn").id_ecole_id, self.ecole.pk)

    def test_email_deja_pris_refuse(self):
        r = self.client.post("/api/utilisateurs/", {
            "nom": "N", "prenom": "P", "email": self.admin_autre.email,
            "role": "parent", "password": MDP,
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_mot_de_passe_obligatoire_a_la_creation(self):
        r = self.client.post("/api/utilisateurs/", {
            "nom": "N", "prenom": "P", "email": "sans@a.sn", "role": "parent",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_modification_sans_mot_de_passe_le_conserve(self):
        r = self.client.patch(f"/api/utilisateurs/{self.parent_user.pk}/",
                              {"telephone": "+221770000000"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.parent_user.refresh_from_db()
        self.assertTrue(self.parent_user.check_password(MDP))

    def test_impossible_de_supprimer_son_propre_compte(self):
        r = self.client.delete(f"/api/utilisateurs/{self.admin.pk}/")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Utilisateur.objects.filter(pk=self.admin.pk).exists())

    def test_l_unique_admin_ne_peut_pas_se_desactiver(self):
        """Sinon l'école se retrouve sans personne pour gérer les comptes."""
        r = self.client.patch(f"/api/utilisateurs/{self.admin.pk}/",
                              {"statut": "inactif"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_l_unique_admin_ne_peut_pas_se_retrograder(self):
        r = self.client.patch(f"/api/utilisateurs/{self.admin.pk}/",
                              {"role": "caissier"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_desactivation_possible_s_il_reste_un_autre_admin(self):
        self._compte("admin2@a.sn", self.ecole, "admin")
        r = self.client.patch(f"/api/utilisateurs/{self.admin.pk}/",
                              {"statut": "inactif"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def test_suppression_d_un_autre_compte_possible(self):
        r = self.client.delete(f"/api/utilisateurs/{self.parent_user.pk}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Utilisateur.objects.filter(pk=self.parent_user.pk).exists())

    def test_comptes_interdits_aux_non_admins(self):
        self.assertEqual(self._client(self.parent_user.email)
                         .get("/api/utilisateurs/").status_code, 403)


class TestAnneesScolaires(BaseAPI):
    """
    L'écran des années scolaires manquait : ouvrir une nouvelle année obligeait
    à écrire directement en base. Ces tests couvrent la saisie par l'API.
    """

    def test_creation_par_l_admin(self):
        r = self.client.post("/api/annees-scolaires/", {
            "libelle": "2027-2028", "date_debut": "2027-10-01",
            "date_fin": "2028-07-31", "statut": "active",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        annee = models.AnneeScolaire.objects.get(pk=r.data["id"])
        self.assertEqual(annee.id_ecole_id, self.ecole.pk,
                         "l'école vient du compte connecté, jamais du corps de la requête")

    def test_fin_avant_debut_refusee(self):
        r = self.client.post("/api/annees-scolaires/", {
            "libelle": "2027-2028", "date_debut": "2028-07-31",
            "date_fin": "2027-10-01", "statut": "active",
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("date_fin", r.data)

    def test_libelle_unique_par_ecole(self):
        charge = {"libelle": "2026-2027", "date_debut": "2026-10-01",
                  "date_fin": "2027-07-31", "statut": "active"}
        # BaseAPI a déjà créé « 2026-2027 » pour cette école.
        self.assertEqual(self.client.post("/api/annees-scolaires/", charge, format="json")
                         .status_code, 400)
        # La même année reste créable dans une autre école.
        self.assertEqual(self.client_autre.post("/api/annees-scolaires/", charge, format="json")
                         .status_code, 201)

    def test_creation_interdite_a_l_enseignant(self):
        r = self._client(self.enseignant_user.email).post("/api/annees-scolaires/", {
            "libelle": "2027-2028", "date_debut": "2027-10-01", "date_fin": "2028-07-31",
        }, format="json")
        self.assertEqual(r.status_code, 403)

    def test_une_seule_annee_active_a_la_fois(self):
        """
        Deux années actives avaient bloqué toutes les inscriptions : l'écran se
        présélectionnait sur la mauvaise et refusait ensuite chaque classe.
        """
        r = self.client.post("/api/annees-scolaires/", {
            "libelle": "2027-2028", "date_debut": "2027-10-01",
            "date_fin": "2028-07-31", "statut": "active",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)

        self.annee.refresh_from_db()
        self.assertEqual(self.annee.statut, "archivee",
                         "ouvrir une année doit archiver la précédente")
        actives = models.AnneeScolaire.objects.filter(id_ecole=self.ecole, statut="active")
        self.assertEqual(actives.count(), 1)
        self.assertEqual(actives.first().libelle, "2027-2028")

    def test_reactiver_une_annee_archivee_bascule_l_autre(self):
        suivante = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))
        self.annee.refresh_from_db()
        self.assertEqual(self.annee.statut, "archivee")

        r = self.client.patch(f"/api/annees-scolaires/{self.annee.pk}/",
                              {"statut": "active"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

        suivante.refresh_from_db()
        self.assertEqual(suivante.statut, "archivee")
        self.assertEqual(
            models.AnneeScolaire.objects.filter(id_ecole=self.ecole, statut="active").count(), 1)

    def test_l_exclusivite_ne_traverse_pas_les_ecoles(self):
        """Ouvrir une année chez l'un ne doit rien archiver chez l'autre."""
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))

        models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))

        annee_b.refresh_from_db()
        self.assertEqual(annee_b.statut, "active",
                         "l'année de l'autre école ne doit pas être touchée")

    def test_tout_archiver_reste_possible(self):
        """Entre deux années, n'avoir aucune année active est un état légitime."""
        r = self.client.patch(f"/api/annees-scolaires/{self.annee.pk}/",
                              {"statut": "archivee"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            models.AnneeScolaire.objects.filter(id_ecole=self.ecole, statut="active").count(), 0)

    def test_l_annee_expose_ce_qu_elle_porte(self):
        """Avant d'archiver une année, il faut voir si elle porte des données."""
        models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-001", nom="Fall", prenom="Moussa")
        models.Inscription.objects.create(
            id_eleve=models.Eleve.objects.get(matricule="E-001"), id_classe=self.classe,
            id_annee_scolaire=self.annee, date_inscription=date(2026, 10, 1))

        r = self.client.get(f"/api/annees-scolaires/{self.annee.pk}/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["nombre_classes"], 1)
        self.assertEqual(r.data["nombre_inscriptions"], 1)


class TestFiltreParAnnee(BaseAPI):
    """
    Le sélecteur d'année du bandeau haut passe `id_annee_scolaire` à chaque
    liste. Toutes ne portent pas la colonne — une évaluation dépend de sa
    période, un élève de ses inscriptions — d'où ces vérifications par chemin.
    """

    def setUp(self):
        super().setUp()
        self.periode = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=self.annee, libelle="Trimestre 1",
            date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20))

        # Une seconde année, avec sa classe, sa période et son élève.
        self.annee2 = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))
        self.classe2 = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee2, id_niveau=self.niveau,
            nom="5ème A", capacite_max=40)
        self.periode2 = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=self.annee2, libelle="Trimestre 1",
            date_debut=date(2027, 10, 1), date_fin=date(2027, 12, 20))

        # `statut` explicite : une inscription naît « pré-inscrite » et
        # n'entre dans la liste qu'au premier paiement. Ce test-ci porte sur le
        # chemin `inscriptions__id_annee_scolaire`, pas sur cette règle — ses
        # deux élèves sont donc des inscrits confirmés.
        self.eleve1 = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-001", nom="Fall", prenom="Moussa")
        models.Inscription.objects.create(
            id_eleve=self.eleve1, id_classe=self.classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1),
            statut=models.Inscription.Statut.EN_COURS)
        self.eleve2 = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-002", nom="Sarr", prenom="Mariama")
        models.Inscription.objects.create(
            id_eleve=self.eleve2, id_classe=self.classe2, id_annee_scolaire=self.annee2,
            date_inscription=date(2027, 10, 1),
            statut=models.Inscription.Statut.EN_COURS)

    def _ids(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, r.content)
        return {ligne["id"] for ligne in self.resultats(r)}

    def test_classes_filtrees(self):
        self.assertEqual(self._ids(f"/api/classes/?id_annee_scolaire={self.annee.pk}"),
                         {self.classe.pk})
        self.assertEqual(self._ids(f"/api/classes/?id_annee_scolaire={self.annee2.pk}"),
                         {self.classe2.pk})

    def test_eleves_filtres_par_leurs_inscriptions(self):
        """Un élève n'a pas d'année : ce sont ses inscriptions qui la portent."""
        self.assertEqual(self._ids(f"/api/eleves/?id_annee_scolaire={self.annee.pk}"),
                         {self.eleve1.pk})
        self.assertEqual(self._ids(f"/api/eleves/?id_annee_scolaire={self.annee2.pk}"),
                         {self.eleve2.pk})

    def test_eleve_reinscrit_n_apparait_pas_deux_fois(self):
        """La jointure sur les inscriptions dupliquerait la ligne sans distinct()."""
        models.Inscription.objects.create(
            id_eleve=self.eleve1, id_classe=self.classe2, id_annee_scolaire=self.annee2,
            date_inscription=date(2027, 10, 1),
            statut=models.Inscription.Statut.EN_COURS)
        r = self.client.get(f"/api/eleves/?id_annee_scolaire={self.annee2.pk}")
        identifiants = [l["id"] for l in self.resultats(r)]
        self.assertEqual(len(identifiants), len(set(identifiants)),
                         "un élève ne doit apparaître qu'une fois dans la liste")

    def test_evaluations_filtrees_par_la_periode(self):
        evaluation = models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe, id_periode=self.periode,
            type="devoir", date_evaluation=date(2026, 11, 10))
        models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe2, id_periode=self.periode2,
            type="devoir", date_evaluation=date(2027, 11, 10))

        self.assertEqual(self._ids(f"/api/evaluations/?id_annee_scolaire={self.annee.pk}"),
                         {evaluation.pk})

    def test_sans_parametre_tout_est_renvoye(self):
        self.assertEqual(self._ids("/api/classes/"), {self.classe.pk, self.classe2.pk})

    def test_annee_illisible_refusee(self):
        """Une valeur illisible doit être refusée, pas ignorée : sinon l'écran
        afficherait toute l'école en croyant montrer une année."""
        r = self.client.get("/api/classes/?id_annee_scolaire=abc")
        self.assertEqual(r.status_code, 400, r.content)

    def test_le_filtre_ne_perce_pas_l_isolation(self):
        """Filtrer sur l'année d'une autre école ne doit rien révéler."""
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        models.Classe.objects.create(
            id_ecole=self.autre, id_annee_scolaire=annee_b,
            id_niveau=models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème"),
            nom="6ème B", capacite_max=30)

        self.assertEqual(self._ids(f"/api/classes/?id_annee_scolaire={annee_b.pk}"), set())


class TestReferentielsDeStructure(BaseAPI):
    """Niveaux, salles et conseils de classe : saisissables par l'API."""

    def test_creation_niveau(self):
        r = self.client.post("/api/niveaux/", {"libelle": "5ème"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Niveau.objects.get(pk=r.data["id"]).id_ecole_id, self.ecole.pk)

    def test_creation_salle(self):
        r = self.client.post("/api/salles/", {"nom": "Salle 3", "capacite": 35},
                             format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_creation_conseil_de_classe(self):
        periode = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=self.annee, libelle="Trimestre 1",
            date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20))
        r = self.client.post("/api/conseils-classe/", {
            "id_classe": self.classe.pk, "id_periode": periode.pk,
            "date_conseil": "2026-12-22", "decisions": "Passage en 5ème.",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_niveau_interdit_a_l_enseignant(self):
        r = self._client(self.enseignant_user.email).post(
            "/api/niveaux/", {"libelle": "4ème"}, format="json")
        self.assertEqual(r.status_code, 403)


class TestPeriodes(BaseAPI):
    def test_periode_doit_tenir_dans_l_annee(self):
        r = self.client.post("/api/periodes/", {
            "id_annee_scolaire": self.annee.pk, "libelle": "Trimestre 1",
            "date_debut": "2026-09-01", "date_fin": "2026-12-20",
        }, format="json")
        self.assertEqual(r.status_code, 400, "une période hors de l'année doit être refusée")

    def test_fin_avant_debut_refusee(self):
        r = self.client.post("/api/periodes/", {
            "id_annee_scolaire": self.annee.pk, "libelle": "T1",
            "date_debut": "2026-12-20", "date_fin": "2026-10-01",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_periode_valide_acceptee(self):
        r = self.client.post("/api/periodes/", {
            "id_annee_scolaire": self.annee.pk, "libelle": "Trimestre 1",
            "date_debut": "2026-10-01", "date_fin": "2026-12-20",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)


class TestCoefficientsEtAffectations(BaseAPI):
    def test_coefficient_en_double_refuse(self):
        charge = {"id_matiere": self.matiere.pk, "id_classe": self.classe.pk,
                  "id_annee_scolaire": self.annee.pk, "coefficient": "4.00"}
        self.assertEqual(self.client.post("/api/coefficients/", charge, format="json")
                         .status_code, 201)
        self.assertEqual(self.client.post("/api/coefficients/", charge, format="json")
                         .status_code, 400)

    def test_coefficient_negatif_refuse(self):
        r = self.client.post("/api/coefficients/", {
            "id_matiere": self.matiere.pk, "id_classe": self.classe.pk,
            "id_annee_scolaire": self.annee.pk, "coefficient": "-1.00",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_affectation_et_charge_horaire(self):
        r = self.client.post("/api/affectations/", {
            "id_enseignant": self.enseignant.pk, "id_matiere": self.matiere.pk,
            "id_classe": self.classe.pk, "id_annee_scolaire": self.annee.pk,
            "volume_horaire": "4.50",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)

        charges = self.client.get("/api/affectations/charges/")
        self.assertEqual(charges.status_code, 200)
        self.assertEqual(float(charges.data[0]["total_heures"]), 4.5)
        self.assertEqual(charges.data[0]["matricule"], "ENS-001")

    def test_affectation_en_double_refusee(self):
        charge = {"id_enseignant": self.enseignant.pk, "id_matiere": self.matiere.pk,
                  "id_classe": self.classe.pk, "id_annee_scolaire": self.annee.pk}
        self.assertEqual(self.client.post("/api/affectations/", charge, format="json")
                         .status_code, 201)
        self.assertEqual(self.client.post("/api/affectations/", charge, format="json")
                         .status_code, 400)


class TestParcoursEleve(BaseAPI):
    def _inscrire(self, nom="Fall", prenom="Moussa"):
        r = self.client.post("/api/eleves/inscrire/", {
            "nom": nom, "prenom": prenom, "id_classe": self.classe.pk,
            "id_annee_scolaire": self.annee.pk, "date_inscription": "2026-10-01",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return models.Eleve.objects.get(pk=r.data["id"])

    def test_matricules_numerotes_par_ecole(self):
        models.ParametreEcole.objects.create(
            id_ecole=self.ecole, cle="prefixe_matricule", libelle="Préfixe", valeur="MIC")
        premier = self._inscrire("Ba", "Aïssatou")
        second = self._inscrire("Sarr", "Mariama")
        self.assertEqual(premier.matricule, "MIC-2026-0001")
        self.assertEqual(second.matricule, "MIC-2026-0002")

    def test_numerotation_independante_entre_ecoles(self):
        """Le compteur est propre à chaque école, jamais global."""
        self._inscrire("Ba", "Aïssatou")
        self._inscrire("Sarr", "Mariama")

        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        niveau_b = models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème")
        classe_b = models.Classe.objects.create(
            id_ecole=self.autre, id_annee_scolaire=annee_b, id_niveau=niveau_b,
            nom="6ème B", capacite_max=30)

        r = self.client_autre.post("/api/eleves/inscrire/", {
            "nom": "Ndiaye", "prenom": "Fatou", "id_classe": classe_b.pk,
            "id_annee_scolaire": annee_b.pk, "date_inscription": "2026-10-01",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(
            models.Eleve.objects.get(pk=r.data["id"]).matricule.endswith("0001"),
            "la première élève de l'école B doit porter le numéro 1",
        )

    def test_classe_d_une_autre_annee_refusee_avec_un_message_lisible(self):
        """
        Le cas qui faisait échouer toutes les inscriptions en production : deux
        années marquées actives, dont une sans classe. Le formulaire
        présélectionnait l'année vide tout en proposant les classes de l'autre.
        Le refus doit nommer la cause, pas se contenter d'un 400 muet.
        """
        annee_suivante = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))

        r = self.client.post("/api/eleves/inscrire/", {
            "nom": "Ba", "prenom": "Aïssatou",
            "id_classe": self.classe.pk,            # relève de 2026-2027
            "id_annee_scolaire": annee_suivante.pk,  # mais on vise 2027-2028
            "date_inscription": "2027-10-01",
        }, format="json")

        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("id_classe", r.data)
        self.assertIn("année scolaire", str(r.data["id_classe"][0]))
        self.assertFalse(models.Eleve.objects.filter(nom="Ba").exists(),
                         "aucun élève ne doit être créé quand l'inscription est refusée")

    def test_recherche_par_nom_prenom_ou_matricule(self):
        """
        Le champ de recherche du bandeau haut s'appuie là-dessus. Il était
        purement décoratif : rien n'y était branché.
        """
        eleve = self._inscrire("Diagne", "Aïssatou")
        self._inscrire("Fall", "Moussa")

        for terme, attendu in [("Diagne", 1), ("aïssa", 1), ("all", 1),
                               (eleve.matricule, 1), ("introuvable", 0)]:
            with self.subTest(terme=terme):
                r = self.client.get(f"/api/eleves/?search={terme}")
                self.assertEqual(r.status_code, 200, r.content)
                self.assertEqual(len(self.resultats(r)), attendu)

    def test_recherche_ne_traverse_pas_les_ecoles(self):
        models.Eleve.objects.create(
            id_ecole=self.autre, matricule="B-001", nom="Diagne", prenom="Autre")
        self._inscrire("Diagne", "Aïssatou")

        r = self.client.get("/api/eleves/?search=Diagne")
        self.assertEqual(len(self.resultats(r)), 1,
                         "la recherche reste bornée à l'école du compte")

    def test_recherche_de_classe(self):
        r = self.client.get("/api/classes/?search=6ème")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertGreaterEqual(len(self.resultats(r)), 1)

    def test_inscription_ecrit_l_historique(self):
        eleve = self._inscrire()
        historique = models.HistoriqueScolaire.objects.filter(id_eleve=eleve)
        self.assertEqual(historique.count(), 1)
        self.assertEqual(historique.first().type_evenement, "inscription")

    def test_changement_de_classe_trace(self):
        eleve = self._inscrire()
        autre_classe = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=40)
        inscription = models.Inscription.objects.get(id_eleve=eleve)

        r = self.client.patch(f"/api/inscriptions/{inscription.pk}/",
                              {"id_classe": autre_classe.pk}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        trace = models.HistoriqueScolaire.objects.get(
            id_eleve=eleve, type_evenement="changement_classe")
        self.assertEqual(trace.id_ancienne_classe_id, self.classe.pk)
        self.assertEqual(trace.id_nouvelle_classe_id, autre_classe.pk)

    def test_historique_en_lecture_seule(self):
        eleve = self._inscrire()
        r = self.client.post("/api/historique-scolaire/", {
            "id_eleve": eleve.pk, "type_evenement": "radiation",
            "date_evenement": "2026-11-01",
        }, format="json")
        self.assertEqual(r.status_code, 405)

    def test_rattacher_un_second_parent(self):
        eleve = self._inscrire()
        parent = models.ParentTuteur.objects.create(
            id_ecole=self.ecole, nom="Fall", prenom="Ibrahima", telephone="+221770000002")
        lien = {"id_eleve": eleve.pk, "id_parent": parent.pk, "lien_parente": "pere"}
        self.assertEqual(self.client.post("/api/eleves-parents/", lien, format="json")
                         .status_code, 201)
        self.assertEqual(self.client.post("/api/eleves-parents/", lien, format="json")
                         .status_code, 400, "le même parent ne doit pas être rattaché deux fois")

    def test_parent_d_une_autre_ecole_refuse(self):
        eleve = self._inscrire()
        parent_b = models.ParentTuteur.objects.create(
            id_ecole=self.autre, nom="X", prenom="Y")
        r = self.client.post("/api/eleves-parents/", {
            "id_eleve": eleve.pk, "id_parent": parent_b.pk, "lien_parente": "pere",
        }, format="json")
        self.assertEqual(r.status_code, 400)


class TestCommunications(BaseAPI):
    def setUp(self):
        super().setUp()
        self.eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-001", nom="Fall", prenom="Moussa")
        models.Inscription.objects.create(
            id_eleve=self.eleve, id_classe=self.classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1))
        self.parent_fiche = models.ParentTuteur.objects.create(
            id_ecole=self.ecole, id_utilisateur=self.parent_user, nom="Sow", prenom="Awa",
            telephone="+221770000001", email="parent@a.sn")
        self.parent_sans_compte = models.ParentTuteur.objects.create(
            id_ecole=self.ecole, nom="Fall", prenom="Ibrahima", telephone="+221770000002")
        for p in (self.parent_fiche, self.parent_sans_compte):
            models.EleveParent.objects.create(
                id_eleve=self.eleve, id_parent=p, lien_parente="autre")

    def _creer(self, **extra):
        charge = {"objet": "Réunion", "contenu": "Samedi 10h.",
                  "cible": "classe", "id_classe": self.classe.pk}
        charge.update(extra)
        r = self.client.post("/api/communications/", charge, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.data["id"]

    def test_sms_atteint_les_parents_sans_compte(self):
        """Le cas réel : la plupart des familles n'ont pas de compte."""
        pk = self._creer(canal="sms")
        r = self.client.post(f"/api/communications/{pk}/envoyer/", format="json")
        self.assertEqual(r.status_code, 200, r.content)
        contacts = set(models.CommunicationDestinataire.objects
                       .filter(id_communication=pk).values_list("contact", flat=True))
        self.assertEqual(contacts, {"+221770000001", "+221770000002"})

    def test_canal_interne_vise_les_comptes(self):
        pk = self._creer(canal="interne")
        self.client.post(f"/api/communications/{pk}/envoyer/", format="json")
        self.assertTrue(models.CommunicationDestinataire.objects.filter(
            id_communication=pk, id_utilisateur=self.parent_user).exists())

    def test_cible_classe_sans_classe_refusee(self):
        r = self.client.post("/api/communications/",
                             {"objet": "X", "contenu": "Y", "cible": "classe"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_second_envoi_refuse(self):
        pk = self._creer(canal="sms")
        self.client.post(f"/api/communications/{pk}/envoyer/", format="json")
        r = self.client.post(f"/api/communications/{pk}/envoyer/", format="json")
        self.assertEqual(r.status_code, 400)

    def test_auteur_et_ecole_imposes_par_le_serveur(self):
        pk = self._creer()
        communication = models.Communication.objects.get(pk=pk)
        self.assertEqual(communication.id_utilisateur_auteur_id, self.admin.pk)
        self.assertEqual(communication.id_ecole_id, self.ecole.pk)


class TestParametresEtDocuments(BaseAPI):
    def test_cle_dupliquee_renvoie_400_et_non_500(self):
        charge = {"cle": "devise", "libelle": "Devise", "valeur": "FCFA",
                  "type_valeur": "texte", "categorie": "general"}
        self.assertEqual(self.client.post("/api/parametres/", charge, format="json")
                         .status_code, 201)
        self.assertEqual(self.client.post("/api/parametres/", charge, format="json")
                         .status_code, 400)

    def test_valeur_incoherente_avec_le_type_refusee(self):
        r = self.client.post("/api/parametres/", {
            "cle": "seuil", "libelle": "Seuil", "valeur": "beaucoup",
            "type_valeur": "nombre", "categorie": "finance",
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_meme_cle_possible_dans_deux_ecoles(self):
        charge = {"cle": "devise", "libelle": "Devise", "valeur": "FCFA",
                  "type_valeur": "texte", "categorie": "general"}
        self.assertEqual(self.client.post("/api/parametres/", charge, format="json")
                         .status_code, 201)
        self.assertEqual(self.client_autre.post("/api/parametres/", charge, format="json")
                         .status_code, 201)

    def test_visibilite_des_documents_par_role(self):
        for titre, visibilite in [("Règlement", "parents"), ("Note RH", "admin")]:
            self.assertEqual(self.client.post("/api/documents/", {
                "titre": titre, "categorie": "administratif",
                "visibilite": visibilite, "fichier_url": f"/media/{titre}.pdf",
            }, format="json").status_code, 201)

        vus = [d["titre"] for d in self.resultats(
            self._client(self.parent_user.email).get("/api/documents/"))]
        self.assertEqual(vus, ["Règlement"])

    def test_dossier_eleve_reserve_a_l_administration(self):
        eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-009", nom="Ba", prenom="Aïssatou")
        charge = {"id_eleve": eleve.pk, "type_piece": "Extrait de naissance",
                  "fichier_url": "/media/extrait.pdf"}
        self.assertEqual(self.client.post("/api/dossiers/", charge, format="json")
                         .status_code, 201)
        # Le parent lit le module élèves (matrice), mais seulement ses enfants :
        # la pièce d'un élève qui n'est pas le sien ne lui apparaît pas.
        reponse = self._client(self.parent_user.email).get("/api/dossiers/")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(self.resultats(reponse), [])


class BasePedagogie(BaseAPI):
    def setUp(self):
        super().setUp()
        self.periode = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=self.annee, libelle="Trimestre 1",
            date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20))
        self.salle = models.Salle.objects.create(
            id_ecole=self.ecole, nom="Salle 1", capacite=40)
        self.eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-001", nom="Fall", prenom="Moussa")
        models.Inscription.objects.create(
            id_eleve=self.eleve, id_classe=self.classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1))
        self.eleve_b = models.Eleve.objects.create(
            id_ecole=self.autre, matricule="B-001", nom="X", prenom="Y")
        self.evaluation = models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe, id_periode=self.periode,
            type="devoir", date_evaluation=date(2026, 11, 10))


class TestEvaluationsEtNotes(BasePedagogie):
    def test_creation_evaluation(self):
        r = self.client.post("/api/evaluations/", {
            "id_matiere": self.matiere.pk, "id_classe": self.classe.pk,
            "id_periode": self.periode.pk, "type": "composition",
            "date_evaluation": "2026-12-01", "coefficient": "2.00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_evaluation_hors_ecole_refusee(self):
        classe_b = models.Classe.objects.create(
            id_ecole=self.autre, nom="6ème B", capacite_max=30,
            id_niveau=models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème"),
            id_annee_scolaire=models.AnneeScolaire.objects.create(
                id_ecole=self.autre, libelle="2026-2027",
                date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31)))
        r = self.client.post("/api/evaluations/", {
            "id_matiere": self.matiere.pk, "id_classe": classe_b.pk,
            "id_periode": self.periode.pk, "type": "devoir",
            "date_evaluation": "2026-12-01",
        }, format="json")
        self.assertEqual(r.status_code, 400, "une évaluation ne doit pas viser une autre école")

    def test_saisie_de_note_attribuee_au_compte_connecte(self):
        r = self.client.post("/api/notes/", {
            "id_eleve": self.eleve.pk, "id_evaluation": self.evaluation.pk,
            "valeur": "15.50", "id_utilisateur": self.enseignant_user.pk,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Note.objects.get(pk=r.data["id"]).id_utilisateur_id,
                         self.admin.pk, "le saisisseur ne doit pas venir du client")

    def test_une_seule_note_par_eleve_et_evaluation(self):
        charge = {"id_eleve": self.eleve.pk, "id_evaluation": self.evaluation.pk,
                  "valeur": "12.00"}
        self.assertEqual(self.client.post("/api/notes/", charge, format="json")
                         .status_code, 201)
        self.assertEqual(self.client.post("/api/notes/", charge, format="json")
                         .status_code, 400)

    def test_note_negative_refusee(self):
        r = self.client.post("/api/notes/", {
            "id_eleve": self.eleve.pk, "id_evaluation": self.evaluation.pk,
            "valeur": "-3.00",
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_note_sur_un_eleve_d_une_autre_ecole_refusee(self):
        r = self.client.post("/api/notes/", {
            "id_eleve": self.eleve_b.pk, "id_evaluation": self.evaluation.pk,
            "valeur": "10.00",
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_saisie_de_note_interdite_au_parent(self):
        r = self._client(self.parent_user.email).post("/api/notes/", {
            "id_eleve": self.eleve.pk, "id_evaluation": self.evaluation.pk,
            "valeur": "20.00",
        }, format="json")
        self.assertEqual(r.status_code, 403)

    def test_bulletin_unique_par_eleve_classe_periode(self):
        charge = {"id_eleve": self.eleve.pk, "id_classe": self.classe.pk,
                  "id_periode": self.periode.pk, "moyenne_generale": "13.50"}
        self.assertEqual(self.client.post("/api/bulletins/", charge, format="json")
                         .status_code, 201)
        self.assertEqual(self.client.post("/api/bulletins/", charge, format="json")
                         .status_code, 400)

    def test_conseil_de_classe(self):
        r = self.client.post("/api/conseils-classe/", {
            "id_classe": self.classe.pk, "id_periode": self.periode.pk,
            "date_conseil": "2026-12-22", "decisions": "Passage en 5ème.",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)


class TestAbsences(BasePedagogie):
    def test_creation_absence(self):
        r = self.client.post("/api/absences/", {
            "id_eleve": self.eleve.pk, "id_classe": self.classe.pk,
            "date_absence": "2026-11-12T08:00:00Z", "statut": "absent",
            "motif": "Maladie", "justifiee": True,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)

    def test_absence_sur_un_eleve_d_une_autre_ecole_refusee(self):
        r = self.client.post("/api/absences/", {
            "id_eleve": self.eleve_b.pk, "id_classe": self.classe.pk,
            "date_absence": "2026-11-12T08:00:00Z", "statut": "absent",
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_absences_isolees_entre_ecoles(self):
        models.Absence.objects.create(
            id_eleve=self.eleve, id_classe=self.classe,
            date_absence="2026-11-12T08:00:00Z", statut="absent")
        r = self.client_autre.get("/api/absences/")
        self.assertEqual(len(self.resultats(r)), 0)

    def test_absence_interdite_au_parent(self):
        r = self._client(self.parent_user.email).post("/api/absences/", {
            "id_eleve": self.eleve.pk, "id_classe": self.classe.pk,
            "date_absence": "2026-11-12T08:00:00Z", "statut": "absent",
        }, format="json")
        self.assertEqual(r.status_code, 403)


class TestEmploiDuTemps(BasePedagogie):
    def _seance(self, **extra):
        charge = {
            "id_classe": self.classe.pk, "id_matiere": self.matiere.pk,
            "id_enseignant": self.enseignant.pk, "id_salle": self.salle.pk,
            "id_annee_scolaire": self.annee.pk, "jour_semaine": "lundi",
            "heure_debut": "08:00", "heure_fin": "10:00",
        }
        charge.update(extra)
        return self.client.post("/api/emplois-du-temps/", charge, format="json")

    def test_creation_seance(self):
        self.assertEqual(self._seance().status_code, 201)

    def test_fin_avant_debut_refusee(self):
        r = self._seance(heure_debut="10:00", heure_fin="08:00")
        self.assertEqual(r.status_code, 400, "un créneau qui finit avant de commencer")

    def test_classe_deja_occupee(self):
        self.assertEqual(self._seance().status_code, 201)
        autre_matiere = models.Matiere.objects.create(id_ecole=self.ecole, nom="Français")
        r = self._seance(id_matiere=autre_matiere.pk)
        self.assertEqual(r.status_code, 400, "la classe a déjà cours sur ce créneau")

    def test_enseignant_deja_occupe(self):
        self.assertEqual(self._seance().status_code, 201)
        autre_classe = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=30)
        autre_salle = models.Salle.objects.create(id_ecole=self.ecole, nom="Salle 2")
        r = self._seance(id_classe=autre_classe.pk, id_salle=autre_salle.pk)
        self.assertEqual(r.status_code, 400, "l'enseignant ne peut pas être à deux endroits")


class TestFinances(BaseAPI):
    def setUp(self):
        super().setUp()
        self.eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-100", nom="Diop", prenom="Omar")
        self.type_frais = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Mensualité", nature="mensualite", montant_defaut=25000)
        self.frais = models.FraisEleve.objects.create(
            id_eleve=self.eleve, id_type_frais=self.type_frais,
            id_annee_scolaire=self.annee, montant_du=25000,
            echeance=date.today() + timedelta(days=10))

    def test_encaissement_genere_recu_et_mouvement_de_caisse(self):
        r = self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "25000.00", "methode": "wave",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)

        paiement = models.Paiement.objects.get(pk=r.data["id"])
        self.assertTrue(models.Recu.objects.filter(id_paiement=paiement).exists())
        self.assertTrue(models.JournalCaisse.objects.filter(id_paiement=paiement).exists())

        self.frais.refresh_from_db()
        self.assertEqual(self.frais.statut, "paye")

    def test_impayes_listes(self):
        r = self.client.get("/api/frais-eleves/impayes/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.resultats(r)), 1)

    def test_paiement_partiel_laisse_le_frais_ouvert(self):
        r = self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "10000.00", "methode": "especes",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.frais.refresh_from_db()
        self.assertEqual(self.frais.statut, "partiel")
        self.assertEqual(len(self.resultats(self.client.get("/api/frais-eleves/impayes/"))), 1)

    def test_caissier_impose_par_le_serveur(self):
        r = self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "5000.00", "methode": "wave",
            "id_utilisateur_caissier": self.parent_user.pk,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Paiement.objects.get(pk=r.data["id"])
                         .id_utilisateur_caissier_id, self.admin.pk)

    def test_paiement_negatif_refuse(self):
        r = self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "-5000.00", "methode": "especes",
        }, format="json")
        self.assertEqual(r.status_code, 400, "un encaissement négatif viderait la caisse")

    def test_paiement_superieur_au_du_refuse(self):
        r = self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "99000.00", "methode": "especes",
        }, format="json")
        self.assertEqual(r.status_code, 400, "on ne peut pas encaisser plus que le dû")

    def test_paiement_sur_un_frais_d_une_autre_ecole_refuse(self):
        eleve_b = models.Eleve.objects.create(
            id_ecole=self.autre, matricule="B-1", nom="X", prenom="Y")
        type_b = models.TypeFrais.objects.create(
            id_ecole=self.autre, libelle="Mensualité", nature="mensualite", montant_defaut=1000)
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        frais_b = models.FraisEleve.objects.create(
            id_eleve=eleve_b, id_type_frais=type_b, id_annee_scolaire=annee_b,
            montant_du=1000)
        r = self.client.post("/api/paiements/", {
            "id_frais_eleve": frais_b.pk, "montant": "1000.00", "methode": "especes",
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_recus_et_journal_en_lecture_seule(self):
        # Le journal de caisse est en lecture pour tous les profils : l'écriture
        # est refusée par la matrice (403) avant même la méthode (405).
        for endpoint, attendu in [("recus", 405), ("journal-caisse", 403)]:
            with self.subTest(endpoint=endpoint):
                r = self.client.post(f"/api/{endpoint}/", {}, format="json")
                self.assertEqual(r.status_code, attendu)

    def test_recu_consultable_apres_encaissement(self):
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "25000.00", "methode": "wave",
        }, format="json")
        r = self.client.get("/api/recus/")
        self.assertEqual(r.status_code, 200)
        recus = self.resultats(r)
        self.assertEqual(len(recus), 1)
        self.assertEqual(recus[0]["eleve_nom"], "Omar Diop")
        self.assertEqual(len(self.resultats(self.client_autre.get("/api/recus/"))), 0)

    def test_relance_sur_un_impaye(self):
        r = self.client.post("/api/relances/", {
            "id_frais_eleve": self.frais.pk, "canal": "sms",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(self.resultats(self.client_autre.get("/api/relances/"))), 0)

    def test_depense_et_budget(self):
        type_depense = models.TypeDepense.objects.create(
            id_ecole=self.ecole, libelle="Loyer")
        r = self.client.post("/api/depenses/", {
            "id_type_depense": type_depense.pk, "motif": "Loyer novembre",
            "montant": "150000.00", "date_depense": "2026-11-05",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["id_ecole"], self.ecole.pk)

        r = self.client.post("/api/budgets/", {
            "id_type_depense": type_depense.pk, "id_annee_scolaire": self.annee.pk,
            "categorie": "Charges fixes", "montant_prevu": "1800000.00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["id_ecole"], self.ecole.pk)

    def test_journal_de_caisse_isole(self):
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "25000.00", "methode": "especes",
        }, format="json")
        self.assertEqual(len(self.resultats(self.client.get("/api/journal-caisse/"))), 1)
        self.assertEqual(len(self.resultats(self.client_autre.get("/api/journal-caisse/"))), 0)


class TestRapportsEtDestinataires(BaseAPI):
    def test_rapport_demande_en_attente_et_attribue(self):
        r = self.client.post("/api/rapports/", {
            "type_rapport": "effectifs", "format_export": "pdf",
            "statut": "genere", "fichier_url": "/faux.pdf",
            "id_utilisateur": self.parent_user.pk,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        rapport = models.Rapport.objects.get(pk=r.data["id"])
        self.assertEqual(rapport.statut, "en_attente",
                         "le client ne doit pas pouvoir déclarer un rapport généré")
        self.assertIsNone(rapport.fichier_url)
        self.assertEqual(rapport.id_utilisateur_id, self.admin.pk)
        self.assertEqual(rapport.id_ecole_id, self.ecole.pk)

    def test_rapports_isoles(self):
        self.client.post("/api/rapports/", {"type_rapport": "effectifs"}, format="json")
        self.assertEqual(len(self.resultats(self.client_autre.get("/api/rapports/"))), 0)

    def test_un_destinataire_ne_voit_que_ses_messages(self):
        communication = models.Communication.objects.create(
            id_ecole=self.ecole, objet="Note interne", contenu="…", cible="individuel")
        models.CommunicationDestinataire.objects.create(
            id_communication=communication, id_utilisateur=self.parent_user)
        models.CommunicationDestinataire.objects.create(
            id_communication=communication, id_utilisateur=self.enseignant_user)

        vus = self.resultats(self._client(self.parent_user.email)
                             .get("/api/communication-destinataires/"))
        self.assertEqual(len(vus), 1)
        self.assertEqual(vus[0]["id_utilisateur"], self.parent_user.pk)

        # L'admin voit l'ensemble des destinataires de son école.
        self.assertEqual(len(self.resultats(
            self.client.get("/api/communication-destinataires/"))), 2)
        self.assertEqual(len(self.resultats(
            self.client_autre.get("/api/communication-destinataires/"))), 0)

    def test_marquer_lu_reserve_au_destinataire(self):
        communication = models.Communication.objects.create(
            id_ecole=self.ecole, objet="X", contenu="…", cible="individuel")
        destinataire = models.CommunicationDestinataire.objects.create(
            id_communication=communication, id_utilisateur=self.parent_user)

        r = self.client.post(
            f"/api/communication-destinataires/{destinataire.pk}/marquer-lu/", format="json")
        self.assertEqual(r.status_code, 403, "on ne marque pas lu à la place d'autrui")

        r = self._client(self.parent_user.email).post(
            f"/api/communication-destinataires/{destinataire.pk}/marquer-lu/", format="json")
        self.assertEqual(r.status_code, 200, r.content)
        destinataire.refresh_from_db()
        self.assertTrue(destinataire.lu)
        self.assertIsNotNone(destinataire.date_lecture)


# =====================================================================
# Import de listes d'élèves
# =====================================================================

def docx_de_test(titre, rangees):
    """
    Fabrique un .docx minimal en mémoire.

    Le parseur ne lit que `word/document.xml` : produire l'archive à la main
    évite de versionner un binaire de fixture, et rend le contenu du test
    lisible dans le test lui-même.
    """
    def cellule(texte):
        return (f'<w:tc><w:p><w:r><w:t xml:space="preserve">{texte}</w:t>'
                f'</w:r></w:p></w:tc>')

    lignes = "".join(
        "<w:tr>" + "".join(cellule(c) for c in rangee) + "</w:tr>" for rangee in rangees
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{titre}</w:t></w:r></w:p>'
        f'<w:tbl>{lignes}</w:tbl></w:body></w:document>'
    )
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("word/document.xml", document)
    return tampon.getvalue()


def fichier(nom, contenu):
    return SimpleUploadedFile(nom, contenu)


class TestReconnaissanceDesClasses(TestCase):
    """`deviner_classe` lit l'en-tête Word ; c'est là que l'import se trompe le plus."""

    def test_niveaux_avec_et_sans_section(self):
        cas = {
            "Liste nominative CE1 A 2025 / 2026 Mme NDIAYE": ("CE1 A", "CE1"),
            "Liste nominative CI B Mme NDIAYE  2025": ("CI B", "CI"),
            "Liste nominative CM2 A 2025 / 2026 M. DIOP": ("CM2 A", "CM2"),
            "Liste nominative 4e 2025 / 2026": ("4ème", "4ème"),
            "Liste nominative 6e": ("6ème", "6ème"),
            "LISTE PETITE SECTION": ("Petite Section", "Petite Section"),
            "LISTE GRANDE SECTION": ("Grande Section", "Grande Section"),
        }
        for entete, attendu in cas.items():
            self.assertEqual(imports_eleves.deviner_classe(entete), attendu, entete)

    def test_le_nom_de_l_ecole_n_est_pas_pris_pour_une_classe(self):
        # « GS » y désigne Groupe Scolaire, pas Grande Section : le confondre
        # créerait une classe fantôme à chaque import.
        self.assertEqual(imports_eleves.deviner_classe("GS MIC-cité"), (None, None))
        self.assertEqual(imports_eleves.deviner_classe("IEF THIES VILLE"), (None, None))

    def test_l_initiale_d_un_enseignant_n_est_pas_une_section(self):
        self.assertEqual(imports_eleves.deviner_classe("Liste nominative CE2 Mme BA"),
                         ("CE2", "CE2"))

    def test_annotation_entre_parentheses_sortie_de_l_etat_civil(self):
        lignes = imports_eleves.analyser_fichier("CE1.docx", docx_de_test(
            "Liste nominative CE1 A 2025 / 2026",
            [["N°", "PRENOMS", "NOMS"], ["1", "FILY", "LO (NN)"]],
        ))
        self.assertEqual(lignes[0].nom, "Lo")
        self.assertEqual(lignes[0].mention, "NN")


class TestImportEleves(BaseAPI):
    """L'import crée élèves, classes et inscriptions en un seul geste."""

    LISTE = [
        ["N°", "PRENOMS", "NOMS"],
        ["1", "IBRAHIMA", "AIDARA"],
        ["2", "AISSATOU", "BAKHOUM"],
        ["3", "", ""],            # rangée vide laissée par l'enseignant
    ]

    def _importer(self, client=None, valider=True, nom="Liste nominative CE1 A 2025.docx",
                  contenu=None, **extra):
        donnees = {"fichier": fichier(nom, contenu if contenu is not None
                                      else docx_de_test("Liste nominative CE1 A 2025 / 2026",
                                                        self.LISTE))}
        if valider:
            donnees["valider"] = "true"
        donnees.update(extra)
        return (client or self.client).post("/api/eleves/importer/", donnees, format="multipart")

    def test_apercu_n_ecrit_rien(self):
        avant = models.Eleve.objects.count()
        r = self._importer(valider=False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.data["valide"])
        self.assertEqual(r.data["a_creer"], 2)
        self.assertEqual(r.data["classes"][0]["nom"], "CE1 A")
        self.assertFalse(r.data["classes"][0]["existe"])
        self.assertEqual(models.Eleve.objects.count(), avant,
                         "l'aperçu ne doit créer aucun élève")

    def test_import_cree_eleves_classe_et_inscriptions(self):
        r = self._importer()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["resultat"]["eleves_crees"], 2)

        classe = models.Classe.objects.get(id_ecole=self.ecole, nom="CE1 A")
        self.assertEqual(classe.id_niveau.libelle, "CE1")
        self.assertEqual(classe.effectif, 2)
        self.assertEqual(
            models.Inscription.objects.filter(id_classe=classe).count(), 2)

        eleve = models.Eleve.objects.get(id_ecole=self.ecole, nom="Aidara")
        self.assertEqual(eleve.prenom, "Ibrahima")
        self.assertEqual(eleve.date_premiere_inscription, self.annee.date_debut)

    def test_matricules_au_format_prefixe_annee_sequence(self):
        models.ParametreEcole.objects.create(
            id_ecole=self.ecole, cle="prefixe_matricule", libelle="Préfixe", valeur="MIC")
        self._importer()
        matricules = sorted(models.Eleve.objects.filter(id_ecole=self.ecole)
                            .values_list("matricule", flat=True))
        self.assertEqual(matricules, ["MIC-2026-0001", "MIC-2026-0002"])

    def test_second_import_ne_duplique_pas(self):
        self._importer()
        r = self._importer()
        self.assertEqual(r.status_code, 400, "plus rien à créer : l'import doit être refusé")
        self.assertEqual(models.Eleve.objects.filter(id_ecole=self.ecole).count(), 2)

        apercu = self._importer(valider=False)
        self.assertEqual(apercu.data["doublons"], 2)
        self.assertEqual(apercu.data["a_creer"], 0)

    def test_matricules_ne_se_marchent_pas_dessus_entre_deux_imports(self):
        self._importer()
        r = self._importer(contenu=docx_de_test(
            "Liste nominative CE1 B 2025 / 2026",
            [["N°", "PRENOMS", "NOMS"], ["1", "KHADIJA", "TRAORE"]],
        ))
        self.assertEqual(r.status_code, 201, r.content)
        matricules = models.Eleve.objects.filter(id_ecole=self.ecole).values_list(
            "matricule", flat=True)
        self.assertEqual(len(set(matricules)), 3, "chaque élève doit avoir un matricule distinct")

    def test_homonyme_dans_une_autre_classe_est_bien_cree(self):
        # Treize noms des listes de Mic Cité apparaissent dans deux classes :
        # un élève de CI A et un de CM2 A ne sont pas le même enfant. Les
        # écarter comme doublons perdait quatorze élèves réels.
        self._importer()
        r = self._importer(contenu=docx_de_test(
            "Liste nominative CM2 A 2025 / 2026",
            [["N°", "PRENOMS", "NOMS"], ["1", "IBRAHIMA", "AIDARA"]],
        ))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            models.Eleve.objects.filter(id_ecole=self.ecole, nom="Aidara").count(), 2)
        self.assertEqual(r.data["eleves"][0]["decision"], "creation")
        self.assertIn("Homonyme", r.data["eleves"][0]["message"])

    def test_meme_eleve_dans_la_meme_classe_reste_un_doublon(self):
        self._importer()
        apercu = self._importer(valider=False)
        self.assertEqual(apercu.data["doublons"], 2)
        self.assertIn("Déjà inscrit en CE1 A", apercu.data["eleves"][0]["message"])

    def test_doublon_interne_au_fichier_borne_a_la_classe(self):
        r = self._importer(valider=False, contenu=docx_de_test(
            "Liste nominative CE1 A 2025 / 2026",
            [["N°", "PRENOMS", "NOMS"],
             ["1", "IBRAHIMA", "AIDARA"],
             ["2", "IBRAHIMA", "AIDARA"]],
        ))
        self.assertEqual(r.data["a_creer"], 1, "deux fois le même nom dans la même classe")
        self.assertEqual(r.data["doublons"], 1)

    def test_classe_forcee_quand_l_entete_ne_la_donne_pas(self):
        r = self._importer(contenu=docx_de_test(
            "Liste des élèves", [["N°", "PRENOMS", "NOMS"], ["1", "AMAR", "DIOP"]]),
            classe="CM1 B")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(models.Classe.objects.filter(id_ecole=self.ecole, nom="CM1 B").exists())

    def test_ligne_sans_classe_est_ignoree_pas_importee(self):
        r = self._importer(valider=False, contenu=docx_de_test(
            "Liste des élèves", [["N°", "PRENOMS", "NOMS"], ["1", "AMAR", "DIOP"]]))
        self.assertEqual(r.data["a_creer"], 0)
        self.assertEqual(r.data["ignores"], 1)
        self.assertIn("Classe", r.data["eleves"][0]["message"])

    def test_import_csv(self):
        contenu = ("nom;prenom;classe\n"
                   "SARR;MARIAMA;CM2 A\n"
                   "FALL;OUSMANE;CM2 A\n").encode("utf-8")
        r = self._importer(nom="eleves.csv", contenu=contenu)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["resultat"]["eleves_crees"], 2)
        self.assertEqual(models.Eleve.objects.get(nom="Sarr").prenom, "Mariama")

    def test_format_non_supporte_refuse(self):
        r = self._importer(nom="liste.pdf", contenu=b"%PDF-1.4")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Format non reconnu", str(r.data["fichier"]))

    def test_ancien_format_doc_explique_la_marche_a_suivre(self):
        r = self._importer(nom="liste.doc", contenu=b"\xd0\xcf\x11\xe0")
        self.assertEqual(r.status_code, 400)
        self.assertIn(".docx", str(r.data["fichier"]))

    def test_fichier_illisible_refuse_proprement(self):
        r = self._importer(nom="liste.docx", contenu="ceci n est pas une archive".encode())
        self.assertEqual(r.status_code, 400)
        self.assertIn("Word", str(r.data["fichier"]))

    def test_import_reserve_aux_admins(self):
        r = self._importer(client=self._client(self.enseignant_user.email))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(models.Eleve.objects.count(), 0)

    def test_import_journalise(self):
        self._importer()
        trace = models.JournalActivite.objects.filter(
            id_ecole=self.ecole, entite="eleve",
            action=models.JournalActivite.Action.CREATION).last()
        self.assertIsNotNone(trace)
        self.assertIn("Import", trace.description)


class TestIsolationImport(BaseAPI):
    """L'import est une écriture de masse : c'est le pire endroit pour percer l'isolation."""

    def _liste(self):
        return fichier("Liste nominative CE1 A.docx", docx_de_test(
            "Liste nominative CE1 A 2025 / 2026",
            [["N°", "PRENOMS", "NOMS"], ["1", "IBRAHIMA", "AIDARA"]],
        ))

    def test_les_eleves_atterrissent_dans_l_ecole_du_compte(self):
        models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))

        r = self.client_autre.post("/api/eleves/importer/",
                                   {"fichier": self._liste(), "valider": "true"},
                                   format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Eleve.objects.filter(id_ecole=self.autre).count(), 1)
        self.assertEqual(models.Eleve.objects.filter(id_ecole=self.ecole).count(), 0)
        self.assertEqual(models.Classe.objects.filter(id_ecole=self.autre, nom="CE1 A").count(), 1)

    def test_id_ecole_envoye_par_le_client_est_ignore(self):
        r = self.client.post("/api/eleves/importer/",
                             {"fichier": self._liste(), "valider": "true",
                              "id_ecole": self.autre.pk},
                             format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Eleve.objects.filter(id_ecole=self.autre).count(), 0,
                         "le champ id_ecole du formulaire ne doit pas être suivi")
        self.assertEqual(models.Eleve.objects.filter(id_ecole=self.ecole).count(), 1)

    def test_import_refuse_sans_annee_scolaire_active(self):
        self.annee.statut = models.AnneeScolaire.Statut.ARCHIVEE
        self.annee.save()
        r = self.client.post("/api/eleves/importer/",
                             {"fichier": self._liste(), "valider": "true"}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("année scolaire", str(r.data["annee_scolaire"]).lower())

    def test_un_homonyme_d_une_autre_ecole_ne_bloque_pas_l_import(self):
        # Le contrôle de doublon doit être borné à l'école, sinon deux écoles
        # clientes se bloqueraient mutuellement des inscriptions.
        models.Eleve.objects.create(id_ecole=self.autre, matricule="X-1",
                                    nom="Aidara", prenom="Ibrahima")
        r = self.client.post("/api/eleves/importer/",
                             {"fichier": self._liste(), "valider": "true"}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Eleve.objects.filter(id_ecole=self.ecole).count(), 1)


class BaseFinances(BaseAPI):
    """Une école avec trois élèves inscrits, un type de frais et une catégorie de dépense."""

    def setUp(self):
        super().setUp()
        self.type_frais = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Mensualité", nature="mensualite", montant_defaut=25000)
        self.type_depense = models.TypeDepense.objects.create(
            id_ecole=self.ecole, libelle="Loyer")
        self.eleves = [self._inscrire(f"E-{i}", f"Nom{i}") for i in range(1, 4)]

    def _inscrire(self, matricule, nom, classe=None):
        eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule=matricule, nom=nom, prenom="Test")
        models.Inscription.objects.create(
            id_eleve=eleve, id_classe=classe or self.classe,
            id_annee_scolaire=self.annee, date_inscription=self.annee.date_debut)
        return eleve

    def _generer(self, **surcharges):
        corps = {
            "id_type_frais": self.type_frais.pk,
            "id_annee_scolaire": self.annee.pk,
            "echeances": ["2026-11-05"],
            "valider": True,
        }
        corps.update(surcharges)
        corps = {c: v for c, v in corps.items() if v is not None}
        return self.client.post("/api/frais-eleves/generer/", corps, format="json")


class TestGenerationDesFrais(BaseFinances):
    def test_apercu_ne_cree_rien(self):
        r = self._generer(valider=False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["crees"], 3)
        self.assertEqual(r.data["eleves"], 3)
        self.assertFalse(r.data["applique"])
        self.assertEqual(models.FraisEleve.objects.count(), 0,
                         "l'aperçu doit rester sans effet en base")

    def test_generation_par_annee(self):
        r = self._generer()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["crees"], 3)
        self.assertEqual(r.data["total_genere"], 75000.0)
        self.assertEqual(models.FraisEleve.objects.count(), 3)
        self.assertEqual(models.FraisEleve.objects.first().montant_du, Decimal("25000.00"))

    def test_montant_explicite_prioritaire_sur_le_defaut(self):
        self._generer(montant="30000.00")
        self.assertTrue(all(f.montant_du == Decimal("30000.00")
                            for f in models.FraisEleve.objects.all()))

    def test_mensualites_generees_a_partir_d_une_premiere_echeance(self):
        r = self._generer(echeances=None, premiere_echeance="2026-10-05",
                          nombre_echeances=3)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["echeances"], ["2026-10-05", "2026-11-05", "2026-12-05"])
        self.assertEqual(models.FraisEleve.objects.count(), 9, "3 élèves x 3 mensualités")

    def test_le_31_devient_le_dernier_jour_du_mois_court(self):
        self._generer(echeances=None, premiere_echeance="2027-01-31", nombre_echeances=2)
        echeances = sorted({f.echeance for f in models.FraisEleve.objects.all()})
        self.assertEqual(echeances, [date(2027, 1, 31), date(2027, 2, 28)])

    def test_relance_de_la_generation_ne_duplique_pas(self):
        self._generer()
        r = self._generer()
        self.assertEqual(r.data["crees"], 0)
        self.assertEqual(r.data["ignores"], 3)
        self.assertEqual(models.FraisEleve.objects.count(), 3)

    def test_un_nouvel_inscrit_recupere_ses_frais(self):
        self._generer()
        self._inscrire("E-4", "Nouveau")
        r = self._generer()
        self.assertEqual(r.data["crees"], 1)
        self.assertEqual(models.FraisEleve.objects.count(), 4)

    def test_generation_limitee_a_une_classe(self):
        autre_classe = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=30)
        self._inscrire("E-9", "Bravo", classe=autre_classe)
        r = self._generer(id_classe=autre_classe.pk)
        self.assertEqual(r.data["crees"], 1)
        self.assertEqual(r.data["classe"], "6ème B")

    def test_eleve_radie_exclu(self):
        self.eleves[0].statut = models.Eleve.Statut.RADIE
        self.eleves[0].save()
        self.assertEqual(self._generer().data["crees"], 2)

    def test_annee_sans_inscrit_refusee(self):
        annee_vide = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2030-2031",
            date_debut=date(2030, 10, 1), date_fin=date(2031, 7, 31))
        self.assertEqual(self._generer(id_annee_scolaire=annee_vide.pk).status_code, 400)

    def test_montant_negatif_refuse(self):
        self.assertEqual(self._generer(montant="-100.00").status_code, 400)

    def test_sans_echeance_refuse(self):
        r = self.client.post("/api/frais-eleves/generer/", {
            "id_type_frais": self.type_frais.pk,
            "id_annee_scolaire": self.annee.pk,
            "valider": True,
        }, format="json")
        self.assertEqual(r.status_code, 400)

    def test_type_de_frais_d_une_autre_ecole_refuse(self):
        type_b = models.TypeFrais.objects.create(
            id_ecole=self.autre, libelle="Mensualité", nature="mensualite", montant_defaut=1000)
        r = self._generer(id_type_frais=type_b.pk)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(models.FraisEleve.objects.count(), 0)

    def test_annee_d_une_autre_ecole_refusee(self):
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        self.assertEqual(self._generer(id_annee_scolaire=annee_b.pk).status_code, 400)

    def test_generation_reservee_a_l_admin(self):
        caissier = self._compte("caissier@a.sn", self.ecole, "caissier")
        client = self._client(caissier.email)
        r = client.post("/api/frais-eleves/generer/", {
            "id_type_frais": self.type_frais.pk, "id_annee_scolaire": self.annee.pk,
            "echeances": ["2026-11-05"], "valider": True,
        }, format="json")
        self.assertEqual(r.status_code, 403, "facturer une année entière reste un acte d'admin")

    def test_generation_journalisee(self):
        self._generer()
        self.assertTrue(models.JournalActivite.objects
                        .filter(entite="frais_eleve", action="creation").exists())


class TestImpayesParClasse(BaseFinances):
    """
    « Il reste douze millions à encaisser » ne dit pas où aller chercher :
    c'est la classe qui donne la liste d'appel.
    """

    def setUp(self):
        super().setUp()
        self.classe_b = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=40)
        self.eleve_b = self._inscrire("E-9", "Autre", classe=self.classe_b)

        # 6ème A : trois élèves à 25 000, dont un a payé 10 000.
        for eleve in self.eleves:
            models.FraisEleve.objects.create(
                id_eleve=eleve, id_type_frais=self.type_frais,
                id_annee_scolaire=self.annee, montant_du=25000,
                echeance=date.today() + timedelta(days=30))
        premier = models.FraisEleve.objects.get(id_eleve=self.eleves[0])
        models.Paiement.objects.create(
            id_frais_eleve=premier, montant=10000, methode="especes")

        # 6ème B : un élève à 25 000, échéance dépassée, rien payé.
        models.FraisEleve.objects.create(
            id_eleve=self.eleve_b, id_type_frais=self.type_frais,
            id_annee_scolaire=self.annee, montant_du=25000,
            echeance=date.today() - timedelta(days=5))

    def _classes(self):
        r = self.client.get(
            f"/api/frais-eleves/impayes/par-classe/?id_annee_scolaire={self.annee.pk}")
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def test_montants_par_classe(self):
        donnees = self._classes()
        par_nom = {c["classe"]: c for c in donnees["classes"]}

        self.assertEqual(par_nom["6ème A"]["du"], 75000.0)
        self.assertEqual(par_nom["6ème A"]["paye"], 10000.0)
        self.assertEqual(par_nom["6ème A"]["reste"], 65000.0)
        self.assertEqual(par_nom["6ème B"]["reste"], 25000.0)
        self.assertEqual(donnees["total_reste"], 90000.0)

    def test_le_du_ne_double_pas_avec_plusieurs_paiements(self):
        """Joindre frais et paiements dans la même requête gonflerait le dû."""
        premier = models.FraisEleve.objects.get(id_eleve=self.eleves[0])
        for _ in range(3):
            models.Paiement.objects.create(
                id_frais_eleve=premier, montant=1000, methode="especes")

        par_nom = {c["classe"]: c for c in self._classes()["classes"]}
        self.assertEqual(par_nom["6ème A"]["du"], 75000.0,
                         "le montant dû ne dépend pas du nombre de paiements")
        self.assertEqual(par_nom["6ème A"]["paye"], 13000.0)

    def test_classement_par_reste_decroissant(self):
        restes = [c["reste"] for c in self._classes()["classes"]]
        self.assertEqual(restes, sorted(restes, reverse=True))

    def test_eleves_en_retard_comptes(self):
        par_nom = {c["classe"]: c for c in self._classes()["classes"]}
        self.assertEqual(par_nom["6ème B"]["eleves_en_retard"], 1)
        self.assertEqual(par_nom["6ème A"]["eleves_en_retard"], 0,
                         "les échéances à venir ne sont pas des retards")

    def test_isolation_entre_ecoles(self):
        r = self.client_autre.get(
            f"/api/frais-eleves/impayes/par-classe/?id_annee_scolaire={self.annee.pk}")
        self.assertEqual(r.status_code, 400,
                         "l'année d'une autre école ne doit pas être exploitable")


class TestAssiduite(BasePedagogie):
    """Absences et retards sur une fenêtre glissante, pas sur l'année entière."""

    def setUp(self):
        super().setUp()
        maintenant = timezone.now()
        for jours, statut, justifiee in [
            (2, "absent", False), (3, "absent", True), (5, "retard", False),
            (50, "absent", False),  # hors fenêtre de 30 jours
        ]:
            models.Absence.objects.create(
                id_eleve=self.eleve, id_classe=self.classe,
                date_absence=maintenant - timedelta(days=jours),
                statut=statut, justifiee=justifiee)

    def test_fenetre_glissante(self):
        r = self.client.get("/api/absences/synthese/?jours=30")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["total"]["absences"], 2,
                         "l'absence d'il y a 50 jours sort de la fenêtre")
        self.assertEqual(r.data["total"]["retards"], 1)
        self.assertEqual(r.data["total"]["non_justifiees"], 2)

    def test_fenetre_elargie(self):
        r = self.client.get("/api/absences/synthese/?jours=90")
        self.assertEqual(r.data["total"]["absences"], 3)

    def test_classes_les_plus_touchees_en_tete(self):
        r = self.client.get("/api/absences/synthese/?jours=30")
        classes = r.data["classes"]
        self.assertEqual(classes[0]["classe"], self.classe.nom)
        self.assertEqual(classes[0]["absences"], 2)

    def test_eleves_les_plus_absents(self):
        r = self.client.get("/api/absences/synthese/?jours=30")
        self.assertEqual(len(r.data["eleves"]), 1)
        self.assertEqual(r.data["eleves"][0]["matricule"], self.eleve.matricule)

    def test_jours_illisible_refuse(self):
        r = self.client.get("/api/absences/synthese/?jours=beaucoup")
        self.assertEqual(r.status_code, 400, r.content)

    def test_interdit_au_parent(self):
        r = self._client(self.parent_user.email).get("/api/absences/synthese/")
        self.assertEqual(r.status_code, 403)


class TestRelancesAutomatiques(BaseFinances):
    def setUp(self):
        super().setUp()
        self.hier = date.today() - timedelta(days=1)
        for eleve in self.eleves:
            models.FraisEleve.objects.create(
                id_eleve=eleve, id_type_frais=self.type_frais,
                id_annee_scolaire=self.annee, montant_du=25000, echeance=self.hier)

    def _relancer(self, **surcharges):
        corps = {"canal": "sms", "valider": True}
        corps.update(surcharges)
        return self.client.post("/api/relances/generer/", corps, format="json")

    def test_apercu_ne_relance_pas(self):
        r = self._relancer(valider=False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["relances"], 3)
        self.assertEqual(models.Relance.objects.count(), 0)

    def test_relance_des_frais_echus(self):
        r = self._relancer()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["relances"], 3)
        self.assertEqual(r.data["montant_relance"], 75000.0)
        self.assertEqual(models.Relance.objects.count(), 3)

    def test_deuxieme_passage_ne_renvoie_pas_le_meme_jour(self):
        self._relancer()
        r = self._relancer()
        self.assertEqual(r.data["relances"], 0,
                         "sans délai minimum, une tâche relancée trois fois "
                         "enverrait trois SMS au même parent")
        self.assertEqual(models.Relance.objects.count(), 3)

    def test_delai_minimum_desactivable(self):
        self._relancer()
        self.assertEqual(self._relancer(delai_min=0).data["relances"], 3)

    def test_frais_non_echu_ignore(self):
        models.FraisEleve.objects.all().update(echeance=date.today() + timedelta(days=30))
        self.assertEqual(self._relancer().data["relances"], 0)

    def test_jours_de_retard_respectes(self):
        self.assertEqual(self._relancer(jours_retard=30).data["relances"], 0)

    def test_frais_solde_non_relance(self):
        frais = models.FraisEleve.objects.first()
        frais.statut = models.FraisEleve.Statut.PAYE
        frais.save()
        self.assertEqual(self._relancer().data["relances"], 2)

    def test_canal_inconnu_refuse(self):
        self.assertEqual(self._relancer(canal="pigeon").status_code, 400)

    def test_relances_bornees_a_son_ecole(self):
        eleve_b = models.Eleve.objects.create(
            id_ecole=self.autre, matricule="B-1", nom="X", prenom="Y")
        type_b = models.TypeFrais.objects.create(
            id_ecole=self.autre, libelle="Mensualité", nature="mensualite", montant_defaut=1000)
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        models.FraisEleve.objects.create(
            id_eleve=eleve_b, id_type_frais=type_b, id_annee_scolaire=annee_b,
            montant_du=1000, echeance=self.hier)

        self.assertEqual(self._relancer().data["relances"], 3)
        self.assertEqual(models.Relance.objects
                         .filter(id_frais_eleve__id_eleve__id_ecole=self.autre).count(), 0)


class TestCaisseEtDepenses(BaseFinances):
    """Le journal doit rester la seule source du solde : recettes ET charges."""

    def setUp(self):
        super().setUp()
        self.frais = models.FraisEleve.objects.create(
            id_eleve=self.eleves[0], id_type_frais=self.type_frais,
            id_annee_scolaire=self.annee, montant_du=25000)

    def _depenser(self, montant="10000.00", jour=None):
        return self.client.post("/api/depenses/", {
            "id_type_depense": self.type_depense.pk, "motif": "Loyer novembre",
            "montant": montant,
            "date_depense": (jour or self.annee.date_debut).isoformat(),
        }, format="json")

    def test_depense_ecrit_un_debit_au_journal(self):
        r = self._depenser()
        self.assertEqual(r.status_code, 201, r.content)
        mouvement = models.JournalCaisse.objects.get(id_depense=r.data["id"])
        self.assertEqual(mouvement.debit, Decimal("10000.00"))
        self.assertEqual(mouvement.credit, Decimal("0"))

    def test_solde_apres_suit_les_mouvements(self):
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "25000.00", "methode": "especes",
        }, format="json")
        self._depenser("10000.00")
        soldes = [m.solde_apres for m in models.JournalCaisse.objects
                  .filter(type_journal="caisse").order_by("id")]
        self.assertEqual(soldes, [Decimal("25000.00"), Decimal("15000.00")])

    def test_endpoint_soldes(self):
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "20000.00", "methode": "wave",
        }, format="json")
        self._depenser("5000.00")
        r = self.client.get("/api/journal-caisse/soldes/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["banque"], 20000.0, "le mobile money va en banque")
        self.assertEqual(r.data["caisse"], -5000.0)
        self.assertEqual(r.data["total"], 15000.0)

    def test_modification_d_une_depense_corrige_le_journal(self):
        depense = self._depenser("10000.00").data
        r = self.client.patch(f"/api/depenses/{depense['id']}/",
                              {"montant": "4000.00"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(models.JournalCaisse.objects.get(id_depense=depense["id"]).debit,
                         Decimal("4000.00"))

    def test_suppression_d_une_depense_retire_son_mouvement(self):
        depense = self._depenser("10000.00").data
        self.client.delete(f"/api/depenses/{depense['id']}/")
        self.assertEqual(models.JournalCaisse.objects.filter(id_depense=depense["id"]).count(), 0)
        self.assertEqual(models.JournalCaisse.objects.filter(id_ecole=self.ecole).count(), 0)

    def test_budget_realise_suit_les_depenses(self):
        budget = models.Budget.objects.create(
            id_ecole=self.ecole, id_type_depense=self.type_depense,
            id_annee_scolaire=self.annee, categorie="Charges fixes",
            montant_prevu=Decimal("100000.00"))
        self._depenser("30000.00")
        budget.refresh_from_db()
        self.assertEqual(budget.montant_realise, Decimal("30000.00"))

    def _categorie(self, annee=None):
        params = {"id_annee_scolaire": annee.pk} if annee else {}
        return self.client.get(
            f"/api/types-depenses/{self.type_depense.pk}/", params).data

    def _allouer(self, montant, annee=None):
        """L'enveloppe se pose depuis l'écran des catégories, pour l'année consultée."""
        url = f"/api/types-depenses/{self.type_depense.pk}/"
        if annee:
            url += f"?id_annee_scolaire={annee.pk}"
        return self.client.patch(url, {"budget_alloue": montant}, format="json")

    def test_enveloppe_saisie_sur_la_categorie_va_sur_lannee(self):
        r = self._allouer("50000.00")
        self.assertEqual(r.status_code, 200, r.content)
        budget = models.Budget.objects.get(id_type_depense=self.type_depense,
                                           id_annee_scolaire=self.annee)
        self.assertEqual(budget.montant_prevu, Decimal("50000.00"))
        self.assertEqual(r.data["budget_alloue"], "50000.00")

    def test_budget_de_la_categorie_diminue_a_chaque_depense(self):
        self._allouer("50000.00")
        self.assertEqual(self._categorie()["budget_disponible"], 50000.0)
        self._depenser("30000.00")
        categorie = self._categorie()
        self.assertEqual(categorie["montant_depense"], 30000.0)
        self.assertEqual(categorie["budget_disponible"], 20000.0)
        self._depenser("5000.00")
        self.assertEqual(self._categorie()["budget_disponible"], 15000.0)

    def test_depense_supprimee_rend_le_budget(self):
        self._allouer("50000.00")
        depense = self._depenser("30000.00").data
        self.client.delete(f"/api/depenses/{depense['id']}/")
        self.assertEqual(self._categorie()["budget_disponible"], 50000.0,
                         "le disponible se recalcule, il ne se décrémente pas")

    def test_chaque_annee_a_son_enveloppe(self):
        precedente = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2024-2025",
            date_debut=date(2024, 10, 1), date_fin=date(2025, 7, 31),
            statut=models.AnneeScolaire.Statut.ARCHIVEE)
        self._allouer("50000.00")
        self._allouer("20000.00", annee=precedente)
        self._depenser("30000.00", jour=date(2024, 11, 10))

        self.assertEqual(self._categorie()["budget_alloue"], "50000.00")
        self.assertEqual(self._categorie()["budget_disponible"], 50000.0,
                         "une dépense de l'an dernier ne pèse pas sur l'enveloppe en cours")
        archive = self._categorie(annee=precedente)
        self.assertEqual(archive["budget_alloue"], "20000.00")
        self.assertEqual(archive["budget_disponible"], -10000.0)

    def test_enveloppe_posee_apres_coup_rattrape_le_realise(self):
        self._depenser("30000.00")
        self._allouer("50000.00")
        budget = models.Budget.objects.get(id_type_depense=self.type_depense,
                                           id_annee_scolaire=self.annee)
        self.assertEqual(budget.montant_realise, Decimal("30000.00"))

    def test_budget_cree_apres_coup_rattrape_les_depenses(self):
        self._depenser("30000.00")
        r = self.client.post("/api/budgets/", {
            "id_type_depense": self.type_depense.pk, "id_annee_scolaire": self.annee.pk,
            "categorie": "Charges fixes", "montant_prevu": "100000.00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Budget.objects.get(pk=r.data["id"]).montant_realise,
                         Decimal("30000.00"))

    def test_realise_non_modifiable_par_le_client(self):
        r = self.client.post("/api/budgets/", {
            "id_type_depense": self.type_depense.pk, "id_annee_scolaire": self.annee.pk,
            "categorie": "Charges fixes", "montant_prevu": "100000.00",
            "montant_realise": "99000.00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(models.Budget.objects.get(pk=r.data["id"]).montant_realise,
                         Decimal("0"), "un budget tenu ne se déclare pas, il se constate")

    def test_depense_hors_periode_du_budget_ignoree(self):
        budget = models.Budget.objects.create(
            id_ecole=self.ecole, id_type_depense=self.type_depense,
            id_annee_scolaire=self.annee, categorie="Charges fixes",
            montant_prevu=Decimal("100000.00"))
        self._depenser("30000.00", jour=self.annee.date_fin + timedelta(days=40))
        budget.refresh_from_db()
        self.assertEqual(budget.montant_realise, Decimal("0"))

    def test_journal_filtrable_par_type_et_periode(self):
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "5000.00", "methode": "wave",
        }, format="json")
        self._depenser("1000.00")
        self.assertEqual(len(self.resultats(
            self.client.get("/api/journal-caisse/?journal=banque"))), 1)
        self.assertEqual(len(self.resultats(
            self.client.get("/api/journal-caisse/?journal=caisse"))), 1)
        self.assertEqual(len(self.resultats(
            self.client.get("/api/journal-caisse/?debut=2099-01-01"))), 0)

    def test_soldes_isoles_entre_ecoles(self):
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "25000.00", "methode": "especes",
        }, format="json")
        r = self.client_autre.get("/api/journal-caisse/soldes/")
        self.assertEqual(r.data["total"], 0.0)


class TestBilanFinancier(BaseFinances):
    def setUp(self):
        super().setUp()
        # L'année de BaseAPI démarre en octobre : sans recadrage, les
        # encaissements du jour tomberaient hors période et le bilan par
        # défaut (année active) afficherait zéro partout.
        self.annee.date_debut = date.today() - timedelta(days=30)
        self.annee.date_fin = date.today() + timedelta(days=300)
        self.annee.save()

        self.frais = [
            models.FraisEleve.objects.create(
                id_eleve=eleve, id_type_frais=self.type_frais,
                id_annee_scolaire=self.annee, montant_du=25000,
                echeance=date.today() - timedelta(days=5))
            for eleve in self.eleves
        ]
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais[0].pk, "montant": "25000.00", "methode": "wave",
        }, format="json")
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais[1].pk, "montant": "10000.00", "methode": "especes",
        }, format="json")
        self.client.post("/api/depenses/", {
            "id_type_depense": self.type_depense.pk, "motif": "Loyer",
            "montant": "20000.00", "date_depense": date.today().isoformat(),
        }, format="json")

    def _bilan(self, requete=""):
        r = self.client.get(f"/api/finances/bilan/{requete}")
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def test_recouvrement(self):
        recouvrement = self._bilan()["recouvrement"]
        self.assertEqual(recouvrement["attendu"], 75000.0)
        self.assertEqual(recouvrement["encaisse"], 35000.0)
        self.assertEqual(recouvrement["reste"], 40000.0)
        self.assertEqual(recouvrement["taux"], 46.7)

    def test_resultat_et_depenses(self):
        bilan = self._bilan()
        self.assertEqual(bilan["depenses"]["total"], 20000.0)
        self.assertEqual(bilan["resultat"]["solde"], 15000.0)

    def test_tresorerie_ventilee(self):
        tresorerie = self._bilan()["tresorerie"]
        self.assertEqual(tresorerie["banque"], 25000.0)
        self.assertEqual(tresorerie["caisse"], -10000.0, "10 000 encaissés, 20 000 dépensés")
        self.assertEqual(tresorerie["total"], 15000.0)

    def test_ventilation_par_moyen_de_paiement(self):
        par_methode = {l["code"]: l["montant"] for l in self._bilan()["recettes"]["par_methode"]}
        self.assertEqual(par_methode, {"wave": 25000.0, "especes": 10000.0})

    def test_impayes_comptes(self):
        impayes = self._bilan()["impayes"]
        self.assertEqual(impayes["nombre"], 2)
        self.assertEqual(impayes["echus"], 2)
        self.assertEqual(impayes["eleves_concernes"], 2)

    def test_evolution_mensuelle(self):
        evolution = self._bilan()["evolution"]
        mois = date.today().strftime("%Y-%m")
        ligne = next(l for l in evolution if l["mois"] == mois)
        self.assertEqual(ligne["recettes"], 35000.0)
        self.assertEqual(ligne["depenses"], 20000.0)

    def test_budgets_dans_le_bilan(self):
        models.Budget.objects.create(
            id_ecole=self.ecole, id_type_depense=self.type_depense,
            id_annee_scolaire=self.annee, categorie="Charges fixes",
            montant_prevu=Decimal("100000.00"), montant_realise=Decimal("20000.00"))
        budget = self._bilan()["budgets"][0]
        self.assertEqual(budget["prevu"], 100000.0)
        self.assertEqual(budget["ecart"], 80000.0)
        self.assertEqual(budget["consomme"], 20.0)

    def test_annee_par_defaut_active(self):
        self.assertEqual(self._bilan()["periode"]["annee"], self.annee.libelle)

    def test_annee_d_une_autre_ecole_refusee(self):
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        r = self.client.get(f"/api/finances/bilan/?annee={annee_b.pk}")
        self.assertEqual(r.status_code, 400)

    def test_date_illisible_refusee(self):
        self.assertEqual(self.client.get("/api/finances/bilan/?debut=hier").status_code, 400)

    def test_bilan_isole_entre_ecoles(self):
        bilan = self.client_autre.get("/api/finances/bilan/").data
        self.assertEqual(bilan["recouvrement"]["encaisse"], 0.0)
        self.assertEqual(bilan["resultat"]["solde"], 0.0)

    def test_bilan_interdit_au_parent(self):
        client = self._client(self.parent_user.email)
        self.assertEqual(client.get("/api/finances/bilan/").status_code, 403)


class TestExportsFinanciers(BaseFinances):
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def setUp(self):
        super().setUp()
        self.frais = models.FraisEleve.objects.create(
            id_eleve=self.eleves[0], id_type_frais=self.type_frais,
            id_annee_scolaire=self.annee, montant_du=25000,
            echeance=date.today() - timedelta(days=3))
        self.client.post("/api/paiements/", {
            "id_frais_eleve": self.frais.pk, "montant": "5000.00", "methode": "especes",
        }, format="json")
        self.client.post("/api/depenses/", {
            "id_type_depense": self.type_depense.pk, "motif": "Loyer",
            "montant": "7000.00", "date_depense": date.today().isoformat(),
        }, format="json")

    ENDPOINTS = [
        "/api/frais-eleves/impayes/export/",
        "/api/journal-caisse/export/",
        "/api/paiements/export/",
        "/api/depenses/export/",
        "/api/finances/bilan/",
    ]

    def _url(self, endpoint, format):
        return f"{endpoint}?format_export={format}"

    def test_le_parametre_format_de_drf_nest_pas_detourne(self):
        # `?format=` appartient à la négociation de contenu de DRF : s'en
        # servir pour choisir xlsx/pdf faisait répondre 404 à tous les exports.
        r = self.client.get("/api/journal-caisse/export/?format_export=pdf&format=json")
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r["Content-Type"], "application/pdf")

    def test_exports_xlsx(self):
        for endpoint in self.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                r = self.client.get(self._url(endpoint, "xlsx"))
                self.assertEqual(r.status_code, 200, r.content[:200])
                self.assertEqual(r["Content-Type"], self.XLSX)
                self.assertIn("attachment;", r["Content-Disposition"])
                self.assertTrue(r.content.startswith(b"PK"), "un .xlsx est une archive zip")

    def test_exports_pdf(self):
        for endpoint in self.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                r = self.client.get(self._url(endpoint, "pdf"))
                self.assertEqual(r.status_code, 200, r.content[:200])
                self.assertEqual(r["Content-Type"], "application/pdf")
                self.assertTrue(r.content.startswith(b"%PDF-"))
                self.assertTrue(r.content.rstrip().endswith(b"%%EOF"))

    def test_format_inconnu_refuse(self):
        r = self.client.get("/api/journal-caisse/export/?format_export=docx")
        self.assertEqual(r.status_code, 400)

    def test_export_journalise(self):
        self.client.get("/api/journal-caisse/export/")
        self.assertTrue(models.JournalActivite.objects
                        .filter(action="export", entite="finances").exists())

    def test_recu_pdf(self):
        recu = models.Recu.objects.get()
        r = self.client.get(f"/api/recus/{recu.pk}/pdf/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        # Le nom du fichier est normalisé : accents et casse tombent.
        self.assertIn(recu.numero_recu.lower(), r["Content-Disposition"])
        self.assertTrue(r.content.startswith(b"%PDF-"))

    def test_recu_d_une_autre_ecole_introuvable(self):
        recu = models.Recu.objects.get()
        self.assertEqual(self.client_autre.get(f"/api/recus/{recu.pk}/pdf/").status_code, 404)

    def test_export_des_impayes_borne_a_son_ecole(self):
        contenu = self.client_autre.get(
            "/api/frais-eleves/impayes/export/?format_export=pdf").content
        self.assertNotIn(b"Nom1", contenu)

    def test_pdf_multipage_reste_valide(self):
        # 60 mouvements dépassent une page : la pagination et la table xref
        # doivent tenir, sinon le lecteur refuse d'ouvrir le fichier.
        for index in range(60):
            models.JournalCaisse.objects.create(
                id_ecole=self.ecole, type_journal="caisse",
                date_mouvement=date.today(), libelle=f"Mouvement {index}",
                credit=Decimal("1000.00"), solde_apres=Decimal("0"))
        contenu = self.client.get("/api/journal-caisse/export/?format_export=pdf").content
        self.assertGreater(contenu.count(b"/Type /Page /Parent"), 1)
        self.assertNotIn(b"/Count 1 >>", contenu)
        self.assertTrue(contenu.rstrip().endswith(b"%%EOF"))


class TestMoteurPDF(TestCase):
    """Le PDF est écrit à la main : sa structure mérite ses propres tests."""

    def test_document_minimal_valide(self):
        doc = exports.DocumentPDF("Titre", "Sous-titre")
        doc.paragraphe("Une ligne de texte.")
        contenu = doc.rendu()
        self.assertTrue(contenu.startswith(b"%PDF-"))
        self.assertIn(b"/Type /Catalog", contenu)
        self.assertIn(b"startxref", contenu)

    def test_offsets_xref_pointent_sur_les_objets(self):
        doc = exports.DocumentPDF("Titre")
        doc.tableau([("A", 100, "g"), ("B", 100, "d")], [["x", "1"]])
        contenu = doc.rendu()

        depart = int(contenu.rsplit(b"startxref", 1)[1].split(b"%%EOF")[0].strip())
        self.assertEqual(contenu[depart:depart + 4], b"xref")

        # On saute l'en-tête « xref », la ligne de comptage et l'entrée libre
        # de l'objet 0 ; viennent ensuite les objets 1, 2, 3…
        entrees = contenu[depart:].split(b"\n")[3:]
        verifies = 0
        for numero, entree in enumerate(entrees, start=1):
            if not entree.endswith(b" n "):
                break
            offset = int(entree.split()[0])
            self.assertTrue(contenu[offset:].startswith(b"%d 0 obj" % numero),
                            f"l'objet {numero} n'est pas à l'offset annoncé")
            verifies += 1
        self.assertGreaterEqual(verifies, 6, "aucun offset n'a été vérifié")

    def test_parentheses_et_antislash_echappes(self):
        doc = exports.DocumentPDF("Reçu (copie) \\ archive")
        contenu = doc.rendu()
        self.assertIn("Reçu".encode("cp1252") + rb" \(copie\) \\ archive", contenu)

    def test_accents_encodes_en_winansi(self):
        doc = exports.DocumentPDF("Titre")
        doc.paragraphe("Élève réglé")
        self.assertIn("Élève réglé".encode("cp1252"), doc.rendu())

    def test_texte_trop_long_tronque(self):
        court = exports.tronquer("x" * 200, 60, 10)
        self.assertTrue(court.endswith("…"))
        self.assertLessEqual(exports.largeur_texte(court, 10), 60)

    def test_largeur_d_un_caractere_accentue_egale_sa_base(self):
        self.assertEqual(exports.largeur_texte("é", 10), exports.largeur_texte("e", 10))


class TestSeedDemo(TestCase):
    """
    Le jeu de démonstration est la première chose qu'on lance sur un poste
    neuf : s'il casse, personne ne voit l'application tourner.
    """

    def test_seed_idempotent_et_coherent(self):
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

        ecole = models.Ecole.objects.get(nom="École MIC CITE")
        self.assertEqual(models.Eleve.objects.filter(id_ecole=ecole).count(), 3)
        self.assertEqual(models.Depense.objects.filter(id_ecole=ecole).count(), 3,
                         "relancer le seed ne doit pas dupliquer les dépenses")

        # Chaque dépense a sa contrepartie au journal, et le budget « Charges
        # fixes » connaît le loyer déjà payé.
        for depense in models.Depense.objects.filter(id_ecole=ecole):
            self.assertTrue(models.JournalCaisse.objects.filter(id_depense=depense).exists())

        budget = models.Budget.objects.get(id_ecole=ecole, categorie="Charges fixes")
        self.assertEqual(budget.montant_realise, Decimal("150000.00"))
        self.assertEqual(finances.solde(ecole), Decimal("-915000.00"))

    def test_seed_remplit_la_pedagogie(self):
        """Sans notes ni bulletins, les écrans pédagogiques s'ouvrent vides."""
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())
        call_command("seed_demo", verbosity=0, stdout=io.StringIO())

        ecole = models.Ecole.objects.get(nom="École MIC CITE")
        classe = models.Classe.objects.get(id_ecole=ecole, nom="6ème A")

        self.assertEqual(models.Evaluation.objects.filter(id_classe=classe).count(), 3,
                         "relancer le seed ne doit pas dupliquer les évaluations")
        self.assertEqual(models.Note.objects.filter(id_evaluation__id_classe=classe).count(), 9)
        self.assertEqual(models.Bulletin.objects.filter(id_classe=classe).count(), 3)
        self.assertEqual(classe.effectif, 3)

        # Aïssatou : maths (8/10 → 16, coef 1 ; 15/20, coef 3) = 15,25 sur coef 4 ;
        # français 13 sur coef 2 → (15,25×4 + 13×2) / 6 = 14,50
        premiere = models.Bulletin.objects.get(
            id_eleve__matricule="MIC-2026-001", id_classe=classe)
        self.assertEqual(premiere.moyenne_generale, Decimal("14.50"))
        self.assertEqual(premiere.rang_classe, 1)

        # Le classement de démo est net, sans ex æquo : 14,50 / 14,17 / 12,17.
        self.assertEqual(
            [b.rang_classe for b in models.Bulletin.objects
             .filter(id_classe=classe).order_by("rang_classe")],
            [1, 2, 3])


# =====================================================================
# GESTION DES ÉLÈVES — mouvements, réinscription, dossier
# (ÉPIC 1 de la priorisation fonctionnelle)
# =====================================================================

class BaseScolarite(BaseAPI):
    """Une classe de trois élèves, et une classe d'accueil vide."""

    def setUp(self):
        super().setUp()
        self.classe_b = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=40)
        self.eleves = [self._inscrire(f"E-{i}", f"Nom{i}") for i in range(1, 4)]
        scolarite.recaler_effectif(self.classe, self.classe_b)

    def _inscrire(self, matricule, nom, classe=None):
        eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule=matricule, nom=nom, prenom="Test")
        models.Inscription.objects.create(
            id_eleve=eleve, id_classe=classe or self.classe,
            id_annee_scolaire=self.annee, date_inscription=self.annee.date_debut)
        return eleve

    def _effectif(self, classe):
        classe.refresh_from_db(fields=["effectif"])
        return classe.effectif


class TestMouvementsEleve(BaseScolarite):
    def test_changement_de_classe_trace_et_recale_les_effectifs(self):
        eleve = self.eleves[0]
        r = self.client.post(f"/api/eleves/{eleve.pk}/changer-classe/", {
            "id_classe": self.classe_b.pk, "motif": "Rééquilibrage des effectifs",
        }, format="json")
        self.assertEqual(r.status_code, 200, r.content)

        trace = models.HistoriqueScolaire.objects.get(
            id_eleve=eleve, type_evenement="changement_classe")
        self.assertEqual(trace.id_ancienne_classe_id, self.classe.pk)
        self.assertEqual(trace.id_nouvelle_classe_id, self.classe_b.pk)
        self.assertEqual(trace.motif, "Rééquilibrage des effectifs")
        self.assertEqual(self._effectif(self.classe), 2)
        self.assertEqual(self._effectif(self.classe_b), 1)

    def test_changement_vers_la_meme_classe_refuse(self):
        r = self.client.post(f"/api/eleves/{self.eleves[0].pk}/changer-classe/",
                             {"id_classe": self.classe.pk}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_changement_vers_une_autre_annee_refuse(self):
        """Changer d'année, c'est une réinscription — pas un changement de classe."""
        annee_suivante = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))
        classe_suivante = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=annee_suivante, id_niveau=self.niveau,
            nom="5ème A", capacite_max=40)
        r = self.client.post(f"/api/eleves/{self.eleves[0].pk}/changer-classe/",
                             {"id_classe": classe_suivante.pk}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_radiation_annule_l_inscription_et_recale_l_effectif(self):
        eleve = self.eleves[0]
        r = self.client.post(f"/api/eleves/{eleve.pk}/radier/",
                             {"motif": "Absences répétées non justifiées"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

        eleve.refresh_from_db()
        self.assertEqual(eleve.statut, "radie")
        self.assertEqual(models.Inscription.objects.get(id_eleve=eleve).statut, "annulee")
        self.assertTrue(models.HistoriqueScolaire.objects.filter(
            id_eleve=eleve, type_evenement="radiation").exists())
        self.assertEqual(self._effectif(self.classe), 2,
                         "un élève radié ne compte plus dans l'effectif")

    def test_radiation_sans_motif_refusee(self):
        r = self.client.post(f"/api/eleves/{self.eleves[0].pk}/radier/", {}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_radiation_deux_fois_refusee(self):
        url = f"/api/eleves/{self.eleves[0].pk}/radier/"
        self.assertEqual(self.client.post(url, {"motif": "Départ"}, format="json")
                         .status_code, 200)
        self.assertEqual(self.client.post(url, {"motif": "Départ"}, format="json")
                         .status_code, 400)

    def test_transfert_conserve_l_etablissement_d_accueil(self):
        eleve = self.eleves[0]
        r = self.client.post(f"/api/eleves/{eleve.pk}/transferer/",
                             {"etablissement": "Cours Sainte-Marie"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)

        eleve.refresh_from_db()
        self.assertEqual(eleve.statut, "transfere")
        self.assertEqual(models.Inscription.objects.get(id_eleve=eleve).statut, "terminee")
        trace = models.HistoriqueScolaire.objects.get(
            id_eleve=eleve, type_evenement="transfert")
        self.assertIn("Cours Sainte-Marie", trace.motif)

    def test_transfert_sans_etablissement_refuse(self):
        r = self.client.post(f"/api/eleves/{self.eleves[0].pk}/transferer/", {},
                             format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_mouvement_sur_un_eleve_d_une_autre_ecole_refuse(self):
        eleve_b = models.Eleve.objects.create(
            id_ecole=self.autre, matricule="B-001", nom="X", prenom="Y")
        r = self.client.post(f"/api/eleves/{eleve_b.pk}/radier/",
                             {"motif": "Test"}, format="json")
        self.assertEqual(r.status_code, 404,
                         "l'élève d'une autre école ne doit même pas être visible")

    def test_mouvement_interdit_a_l_enseignant(self):
        r = self._client(self.enseignant_user.email).post(
            f"/api/eleves/{self.eleves[0].pk}/radier/", {"motif": "Test"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_inscription_composite_recale_l_effectif(self):
        """Ce chemin oubliait l'effectif : seul l'import de listes le recalait."""
        avant = self._effectif(self.classe)
        r = self.client.post("/api/eleves/inscrire/", {
            "nom": "Diop", "prenom": "Awa", "id_classe": self.classe.pk,
            "id_annee_scolaire": self.annee.pk,
            "date_inscription": self.annee.date_debut.isoformat(),
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self._effectif(self.classe), avant + 1)


class TestReinscription(BaseScolarite):
    def setUp(self):
        super().setUp()
        self.annee_suivante = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))
        self.classe_suivante = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee_suivante,
            id_niveau=self.niveau, nom="5ème A", capacite_max=40)

    def _reinscrire(self, **surcharges):
        corps = {
            "id_annee_source": self.annee.pk,
            "id_annee_cible": self.annee_suivante.pk,
            "id_classe_cible": self.classe_suivante.pk,
            "id_classe_source": self.classe.pk,
            "valider": True,
        }
        corps.update(surcharges)
        return self.client.post("/api/inscriptions/reinscrire/", corps, format="json")

    def test_apercu_ne_cree_rien(self):
        r = self._reinscrire(valider=False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["reinscrits"], 3)
        self.assertFalse(r.data["applique"])
        self.assertEqual(
            models.Inscription.objects.filter(id_annee_scolaire=self.annee_suivante).count(),
            0, "l'aperçu doit rester sans effet en base")

    def test_reinscription_ecrit_les_inscriptions_et_l_historique(self):
        r = self._reinscrire()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["reinscrits"], 3)
        self.assertEqual(
            models.Inscription.objects.filter(id_classe=self.classe_suivante).count(), 3)
        self.assertEqual(
            models.HistoriqueScolaire.objects.filter(type_evenement="reinscription").count(),
            3, "chaque réinscription laisse une trace")
        self.assertEqual(self._effectif(self.classe_suivante), 3)

    def test_reinscription_idempotente(self):
        self._reinscrire()
        r = self._reinscrire()
        self.assertEqual(r.data["reinscrits"], 0)
        self.assertEqual(r.data["ignores"], 3)
        self.assertEqual(
            models.Inscription.objects.filter(id_classe=self.classe_suivante).count(), 3)

    def test_eleve_radie_non_reinscrit(self):
        self.client.post(f"/api/eleves/{self.eleves[0].pk}/radier/",
                         {"motif": "Départ"}, format="json")
        r = self._reinscrire()
        self.assertEqual(r.data["reinscrits"], 2,
                         "un élève radié ne doit pas remonter dans la promotion")

    def test_meme_annee_refusee(self):
        r = self._reinscrire(id_annee_cible=self.annee.pk)
        self.assertEqual(r.status_code, 400, r.content)

    def test_classe_cible_d_une_autre_annee_refusee(self):
        r = self._reinscrire(id_classe_cible=self.classe_b.pk)
        self.assertEqual(r.status_code, 400, r.content)

    def test_avertissement_de_capacite(self):
        self.classe_suivante.capacite_max = 2
        self.classe_suivante.save(update_fields=["capacite_max"])
        r = self._reinscrire(valider=False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNotNone(r.data["avertissement"],
                             "un dépassement de capacité doit être signalé")
        self.assertEqual(r.data["reinscrits"], 3,
                         "signalé, mais pas bloqué : une classe surchargée est une réalité")

    def test_reinscription_interdite_a_lenseignant(self):
        # Matrice : inscriptions en écriture pour caissier et comptable, aucun
        # accès pour l'enseignant.
        r = self._client(self.enseignant_user.email).post("/api/inscriptions/reinscrire/", {
            "id_annee_source": self.annee.pk, "id_annee_cible": self.annee_suivante.pk,
            "id_classe_cible": self.classe_suivante.pk, "valider": True,
        }, format="json")
        self.assertEqual(r.status_code, 403)

    def test_isolation_sur_l_annee_d_une_autre_ecole(self):
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))
        r = self._reinscrire(id_annee_cible=annee_b.pk)
        self.assertEqual(r.status_code, 400, r.content)


class TestDossierEleve(BaseScolarite):
    def test_dossier_assemble_le_parcours_et_les_finances(self):
        eleve = self.eleves[0]
        parent = models.ParentTuteur.objects.create(
            id_ecole=self.ecole, nom="Fall", prenom="Ibrahima", telephone="+221770000002")
        models.EleveParent.objects.create(
            id_eleve=eleve, id_parent=parent, lien_parente="pere", contact_principal=True)
        type_frais = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Mensualité", nature="mensualite", montant_defaut=25000)
        frais = models.FraisEleve.objects.create(
            id_eleve=eleve, id_type_frais=type_frais, id_annee_scolaire=self.annee,
            montant_du=25000)
        models.Paiement.objects.create(
            id_frais_eleve=frais, montant=10000, methode="especes")

        r = self.client.get(f"/api/eleves/{eleve.pk}/dossier/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["eleve"]["matricule"], eleve.matricule)
        self.assertEqual(r.data["scolarite_courante"]["classe"], self.classe.nom)
        self.assertEqual(len(r.data["parents"]), 1)
        self.assertEqual(r.data["parents"][0]["lien"], "Père")
        self.assertEqual(len(r.data["historique"]), 1, "l'inscription initiale est tracée")
        self.assertEqual(Decimal(r.data["finances"]["solde"]), Decimal("15000.00"))

    def test_le_solde_ne_double_pas_avec_plusieurs_paiements(self):
        """Deux Sum et une jointure dans le même aggregate gonfleraient le dû."""
        eleve = self.eleves[0]
        type_frais = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Mensualité", nature="mensualite", montant_defaut=25000)
        frais = models.FraisEleve.objects.create(
            id_eleve=eleve, id_type_frais=type_frais, id_annee_scolaire=self.annee,
            montant_du=25000)
        for _ in range(3):
            models.Paiement.objects.create(
                id_frais_eleve=frais, montant=5000, methode="especes")

        r = self.client.get(f"/api/eleves/{eleve.pk}/dossier/")
        self.assertEqual(Decimal(r.data["finances"]["total_du"]), Decimal("25000.00"))
        self.assertEqual(Decimal(r.data["finances"]["total_paye"]), Decimal("15000.00"))

    def test_dossier_d_un_eleve_d_une_autre_ecole_invisible(self):
        eleve_b = models.Eleve.objects.create(
            id_ecole=self.autre, matricule="B-001", nom="X", prenom="Y")
        self.assertEqual(self.client.get(f"/api/eleves/{eleve_b.pk}/dossier/")
                         .status_code, 404)


# =====================================================================
# GESTION PÉDAGOGIQUE — moyennes, bulletins, palmarès
# (ÉPIC 2 de la priorisation fonctionnelle)
# =====================================================================

class BaseNotes(BaseAPI):
    """
    Une classe de trois élèves, deux matières coefficientées, et une période.

    Le jeu est calibré pour que les moyennes attendues tombent juste : c'est
    ce qui permet de vérifier les pondérations plutôt que de constater qu'un
    nombre sort.
    """

    def setUp(self):
        super().setUp()
        self.periode = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=self.annee, libelle="Trimestre 1",
            date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20))
        self.francais = models.Matiere.objects.create(
            id_ecole=self.ecole, nom="Français")

        # Maths coefficient 4, Français coefficient 2.
        models.CoefficientMatiere.objects.create(
            id_matiere=self.matiere, id_classe=self.classe,
            id_annee_scolaire=self.annee, coefficient=4)
        models.CoefficientMatiere.objects.create(
            id_matiere=self.francais, id_classe=self.classe,
            id_annee_scolaire=self.annee, coefficient=2)

        self.eleves = [self._inscrire(f"E-{i}", f"Nom{i}") for i in range(1, 4)]

        # Un devoir sur 10 coefficient 1, une composition sur 20 coefficient 3.
        self.devoir = models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe, id_periode=self.periode,
            type="devoir", date_evaluation=date(2026, 11, 10),
            coefficient=1, bareme=10)
        self.composition = models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe, id_periode=self.periode,
            type="composition", date_evaluation=date(2026, 12, 10),
            coefficient=3, bareme=20)
        self.compo_francais = models.Evaluation.objects.create(
            id_matiere=self.francais, id_classe=self.classe, id_periode=self.periode,
            type="composition", date_evaluation=date(2026, 12, 12),
            coefficient=1, bareme=20)

    def _inscrire(self, matricule, nom, classe=None):
        eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule=matricule, nom=nom, prenom="Test")
        models.Inscription.objects.create(
            id_eleve=eleve, id_classe=classe or self.classe,
            id_annee_scolaire=self.annee, date_inscription=self.annee.date_debut)
        return eleve

    def _noter(self, evaluation, valeurs):
        """`valeurs` : liste alignée sur self.eleves, None pour « pas de note »."""
        for eleve, valeur in zip(self.eleves, valeurs):
            if valeur is None:
                continue
            models.Note.objects.create(
                id_eleve=eleve, id_evaluation=evaluation,
                id_utilisateur=self.admin, valeur=valeur)


class TestCalculDesMoyennes(BaseNotes):
    def test_la_note_est_ramenee_sur_20_par_le_bareme(self):
        """8/10 vaut 16/20, pas 8 : sans barème toutes les moyennes sont fausses."""
        self._noter(self.devoir, [8, None, None])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        maths = next(m for m in lignes[0]["matieres"] if m["matiere"] == "Mathématiques")
        self.assertEqual(maths["moyenne"], Decimal("16.00"))

    def test_moyenne_de_matiere_ponderee_par_le_coefficient_d_evaluation(self):
        # Devoir 8/10 → 16, coef 1 ; composition 12/20, coef 3.
        # (16×1 + 12×3) / 4 = 13
        self._noter(self.devoir, [8, None, None])
        self._noter(self.composition, [12, None, None])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        maths = next(m for m in lignes[0]["matieres"] if m["matiere"] == "Mathématiques")
        self.assertEqual(maths["moyenne"], Decimal("13.00"))

    def test_moyenne_generale_ponderee_par_le_coefficient_de_matiere(self):
        # Maths 13 (coef 4), Français 10 (coef 2) → (13×4 + 10×2) / 6 = 12
        self._noter(self.devoir, [8, None, None])
        self._noter(self.composition, [12, None, None])
        self._noter(self.compo_francais, [10, None, None])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        self.assertEqual(lignes[0]["moyenne_generale"], Decimal("12.00"))

    def test_matiere_sans_note_exclue_du_calcul(self):
        """Un élève non évalué en français ne doit pas récolter un zéro."""
        self._noter(self.composition, [15, None, None])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        ligne = next(l for l in lignes if l["id_eleve"] == self.eleves[0].pk)
        francais = next(m for m in ligne["matieres"] if m["matiere"] == "Français")
        self.assertIsNone(francais["moyenne"])
        self.assertEqual(ligne["moyenne_generale"], Decimal("15.00"))

    def test_matiere_sans_coefficient_compte_pour_un(self):
        models.CoefficientMatiere.objects.filter(id_matiere=self.francais).delete()
        # Maths 12 (coef 4), Français 17 (coef 1 par défaut) → (48 + 17) / 5 = 13
        self._noter(self.composition, [12, None, None])
        self._noter(self.compo_francais, [17, None, None])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        self.assertEqual(lignes[0]["moyenne_generale"], Decimal("13.00"))

    def test_rang_avec_ex_aequo(self):
        """Convention des établissements : 1, 2, 2, 4 — le rang suivant saute."""
        self._noter(self.composition, [18, 12, 12])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        self.assertEqual([l["rang"] for l in lignes], [1, 2, 2])
        self.assertEqual(lignes[0]["id_eleve"], self.eleves[0].pk)

    def test_eleve_sans_note_non_classe(self):
        self._noter(self.composition, [15, 10, None])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        sans_note = next(l for l in lignes if l["id_eleve"] == self.eleves[2].pk)
        self.assertIsNone(sans_note["rang"])
        self.assertIsNone(sans_note["moyenne_generale"])
        self.assertEqual([l["rang"] for l in lignes[:2]], [1, 2])

    def test_appreciation_par_seuil(self):
        self._noter(self.composition, [17, 11, 5])
        lignes = pedagogie.moyennes_classe(self.ecole, self.classe, self.periode)
        self.assertEqual([l["appreciation"] for l in lignes],
                         ["Excellent", "Assez bien", "Très insuffisant"])

    def test_classe_d_une_autre_ecole_refusee(self):
        classe_b = models.Classe.objects.create(
            id_ecole=self.autre, nom="6ème B", capacite_max=30,
            id_niveau=models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème"),
            id_annee_scolaire=models.AnneeScolaire.objects.create(
                id_ecole=self.autre, libelle="2026-2027",
                date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31)))
        with self.assertRaises(pedagogie.ErreurPedagogie):
            pedagogie.moyennes_classe(self.ecole, classe_b, self.periode)


class TestSaisieEnMasse(BaseNotes):
    def _url(self, evaluation=None):
        return f"/api/evaluations/{(evaluation or self.composition).pk}/notes/"

    def test_grille_liste_tous_les_inscrits(self):
        self._noter(self.composition, [15, None, None])
        r = self.client.get(f"/api/evaluations/{self.composition.pk}/grille/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.data["eleves"]), 3,
                         "les élèves non notés doivent apparaître, sinon on ne sait "
                         "pas qui reste à saisir")
        self.assertEqual(Decimal(r.data["eleves"][0]["valeur"]), Decimal("15.00"))
        self.assertIsNone(r.data["eleves"][1]["valeur"])

    def test_saisie_en_une_requete(self):
        r = self.client.post(self._url(), {"notes": [
            {"id_eleve": e.pk, "valeur": v}
            for e, v in zip(self.eleves, ["15.00", "12.00", "9.50"])
        ]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["creees"], 3)
        self.assertEqual(models.Note.objects.filter(id_evaluation=self.composition).count(), 3)

    def test_le_saisisseur_est_le_compte_connecte(self):
        self.client.post(self._url(), {"notes": [
            {"id_eleve": self.eleves[0].pk, "valeur": "15.00"}]}, format="json")
        note = models.Note.objects.get(id_evaluation=self.composition)
        self.assertEqual(note.id_utilisateur_id, self.admin.pk)

    def test_seconde_saisie_met_a_jour(self):
        charge = {"notes": [{"id_eleve": self.eleves[0].pk, "valeur": "15.00"}]}
        self.client.post(self._url(), charge, format="json")
        charge["notes"][0]["valeur"] = "17.00"
        r = self.client.post(self._url(), charge, format="json")
        self.assertEqual(r.data["modifiees"], 1)
        self.assertEqual(models.Note.objects.get(id_evaluation=self.composition).valeur,
                         Decimal("17.00"))

    def test_valeur_nulle_efface_la_note(self):
        """Une note fausse doit pouvoir être retirée, pas seulement corrigée."""
        self.client.post(self._url(), {"notes": [
            {"id_eleve": self.eleves[0].pk, "valeur": "15.00"}]}, format="json")
        r = self.client.post(self._url(), {"notes": [
            {"id_eleve": self.eleves[0].pk, "valeur": None}]}, format="json")
        self.assertEqual(r.data["effacees"], 1)
        self.assertFalse(models.Note.objects.filter(id_evaluation=self.composition).exists())

    def test_note_hors_bareme_refusee_et_rien_ecrit(self):
        """Transactionnel : une seule note invalide et toute la grille est rejetée."""
        r = self.client.post(self._url(self.devoir), {"notes": [
            {"id_eleve": self.eleves[0].pk, "valeur": "8.00"},
            {"id_eleve": self.eleves[1].pk, "valeur": "15.00"},  # barème 10
        ]}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(models.Note.objects.filter(id_evaluation=self.devoir).count(), 0,
                         "aucune note ne doit subsister d'une grille rejetée")

    def test_eleve_hors_de_la_classe_refuse(self):
        etranger = self._inscrire("E-99", "Etranger", classe=models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème C", capacite_max=40))
        r = self.client.post(self._url(), {"notes": [
            {"id_eleve": etranger.pk, "valeur": "15.00"}]}, format="json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_saisie_interdite_au_parent(self):
        r = self._client(self.parent_user.email).post(self._url(), {"notes": [
            {"id_eleve": self.eleves[0].pk, "valeur": "20.00"}]}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_evaluation_d_une_autre_ecole_invisible(self):
        autre_ecole = models.Ecole.objects.get(pk=self.autre.pk)
        classe_b = models.Classe.objects.create(
            id_ecole=autre_ecole, nom="6ème B", capacite_max=30,
            id_niveau=models.Niveau.objects.create(id_ecole=autre_ecole, libelle="6ème"),
            id_annee_scolaire=models.AnneeScolaire.objects.create(
                id_ecole=autre_ecole, libelle="2026-2027",
                date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31)))
        evaluation_b = models.Evaluation.objects.create(
            id_matiere=models.Matiere.objects.create(id_ecole=autre_ecole, nom="Maths"),
            id_classe=classe_b,
            id_periode=models.PeriodeScolaire.objects.create(
                id_annee_scolaire=classe_b.id_annee_scolaire, libelle="T1",
                date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20)),
            type="devoir", date_evaluation=date(2026, 11, 10))
        r = self.client.get(f"/api/evaluations/{evaluation_b.pk}/grille/")
        self.assertEqual(r.status_code, 404)


class TestBulletins(BaseNotes):
    def setUp(self):
        super().setUp()
        self._noter(self.composition, [18, 12, 12])
        self._noter(self.compo_francais, [15, 15, 9])

    def _generer(self, valider=True):
        return self.client.post("/api/bulletins/generer/", {
            "id_classe": self.classe.pk, "id_periode": self.periode.pk,
            "valider": valider,
        }, format="json")

    def test_apercu_ne_cree_rien(self):
        r = self._generer(valider=False)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["bulletins"], 3)
        self.assertFalse(r.data["applique"])
        self.assertEqual(models.Bulletin.objects.count(), 0)

    def test_generation_ecrit_moyenne_rang_et_appreciation(self):
        r = self._generer()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["crees"], 3)

        # Maths 18 (coef 4), Français 15 (coef 2) → (72 + 30) / 6 = 17
        premier = models.Bulletin.objects.get(id_eleve=self.eleves[0])
        self.assertEqual(premier.moyenne_generale, Decimal("17.00"))
        self.assertEqual(premier.rang_classe, 1)
        self.assertEqual(premier.appreciation_generale, "Excellent")

    def test_regeneration_met_a_jour_sans_doublon(self):
        self._generer()
        models.Note.objects.filter(
            id_eleve=self.eleves[2], id_evaluation=self.compo_francais
        ).update(valeur=Decimal("18.00"))

        r = self._generer()
        self.assertEqual(r.data["mis_a_jour"], 3)
        self.assertEqual(models.Bulletin.objects.count(), 3,
                         "régénérer ne doit pas dupliquer les bulletins")
        # Maths 12 (coef 4), Français 18 (coef 2) → (48 + 36) / 6 = 14
        self.assertEqual(
            models.Bulletin.objects.get(id_eleve=self.eleves[2]).moyenne_generale,
            Decimal("14.00"))

    def test_eleve_sans_note_ignore(self):
        sans_note = self._inscrire("E-9", "SansNote")
        r = self._generer()
        self.assertEqual(r.data["sans_note"], 1)
        self.assertFalse(models.Bulletin.objects.filter(id_eleve=sans_note).exists(),
                         "un bulletin sans moyenne ferait croire à un zéro")

    def test_bulletin_pdf(self):
        self._generer()
        bulletin = models.Bulletin.objects.get(id_eleve=self.eleves[0])
        r = self.client.get(f"/api/bulletins/{bulletin.pk}/pdf/")
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF-"))
        self.assertIn("attachment;", r["Content-Disposition"])

    def test_generation_interdite_au_parent(self):
        r = self._client(self.parent_user.email).post("/api/bulletins/generer/", {
            "id_classe": self.classe.pk, "id_periode": self.periode.pk, "valider": True,
        }, format="json")
        self.assertEqual(r.status_code, 403)

    def test_periode_d_une_autre_annee_refusee(self):
        annee_suivante = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))
        periode_autre = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=annee_suivante, libelle="Trimestre 1",
            date_debut=date(2027, 10, 1), date_fin=date(2027, 12, 20))
        r = self.client.post("/api/bulletins/generer/", {
            "id_classe": self.classe.pk, "id_periode": periode_autre.pk, "valider": True,
        }, format="json")
        self.assertEqual(r.status_code, 400, r.content)


class TestSyntheseEcole(BaseNotes):
    """
    Vue d'ensemble des résultats pour la direction : `statistiques()` ne
    répondait que classe par classe.
    """

    def setUp(self):
        super().setUp()
        # Maths 18 (coef 4) et Français 15 (coef 2) → 17 pour le premier ;
        # 12 et 15 → 13 ; 12 et 9 → 11.
        self._noter(self.composition, [18, 12, 12])
        self._noter(self.compo_francais, [15, 15, 9])

        # Une seconde classe, sur la même période, pour vérifier l'agrégation.
        self.classe_b = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=40)
        self.eleve_b = self._inscrire("E-9", "Autre", classe=self.classe_b)
        evaluation_b = models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe_b, id_periode=self.periode,
            type="composition", date_evaluation=date(2026, 12, 10),
            coefficient=1, bareme=20)
        models.Note.objects.create(
            id_eleve=self.eleve_b, id_evaluation=evaluation_b,
            id_utilisateur=self.admin, valeur=8)

    def test_synthese_agrege_toute_l_ecole(self):
        r = self.client.get(f"/api/bulletins/synthese/?id_periode={self.periode.pk}")
        self.assertEqual(r.status_code, 200, r.content)

        # 3 élèves de 6ème A (17, 13, 11) + 1 de 6ème B (8) = 4 évalués.
        self.assertEqual(r.data["ensemble"]["effectif"], 4)
        self.assertEqual(Decimal(r.data["ensemble"]["moyenne"]), Decimal("12.25"))
        self.assertEqual(r.data["ensemble"]["reussite"], 3, "trois élèves à 10 ou plus")
        self.assertEqual(Decimal(r.data["ensemble"]["taux_reussite"]), Decimal("75.0"))

    def test_classes_classees_par_moyenne(self):
        r = self.client.get(f"/api/bulletins/synthese/?id_periode={self.periode.pk}")
        noms = [c["classe"] for c in r.data["classes"]]
        self.assertEqual(noms, ["6ème A", "6ème B"],
                         "la classe qui réussit le mieux vient en tête")

    def test_matieres_de_la_plus_faible_a_la_plus_forte(self):
        """La direction cherche la matière en difficulté, pas la meilleure."""
        r = self.client.get(f"/api/bulletins/synthese/?id_periode={self.periode.pk}")
        moyennes = [Decimal(m["moyenne"]) for m in r.data["matieres"]]
        self.assertEqual(moyennes, sorted(moyennes))

    def test_classe_sans_note_listee_a_part(self):
        """« Pas encore saisi » ne doit pas se lire comme « mauvais résultats »."""
        vide = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème C", capacite_max=40)
        self._inscrire("E-20", "SansNote", classe=vide)

        r = self.client.get(f"/api/bulletins/synthese/?id_periode={self.periode.pk}")
        sans = [c["classe"] for c in r.data["classes_sans_notes"]]
        self.assertIn("6ème C", sans)
        self.assertNotIn("6ème C", [c["classe"] for c in r.data["classes"]])

    def test_periode_par_defaut(self):
        """Sans paramètre, la synthèse porte sur la période en cours."""
        r = self.client.get("/api/bulletins/synthese/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNotNone(r.data["periode"])

    def test_evolution_lue_sur_les_bulletins(self):
        self.client.post("/api/bulletins/generer/", {
            "id_classe": self.classe.pk, "id_periode": self.periode.pk, "valider": True,
        }, format="json")

        r = self.client.get(f"/api/bulletins/synthese/?id_periode={self.periode.pk}")
        evolution = r.data["evolution"]
        self.assertEqual(len(evolution), 1, "une seule période a des bulletins")
        self.assertEqual(evolution[0]["periode"], "Trimestre 1")
        self.assertEqual(evolution[0]["effectif"], 3)

    def test_periode_d_une_autre_ecole_refusee(self):
        annee_b = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        periode_b = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=annee_b, libelle="Trimestre 1",
            date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20))

        r = self.client.get(f"/api/bulletins/synthese/?id_periode={periode_b.pk}")
        self.assertEqual(r.status_code, 400, r.content)


class TestReleveEtPalmares(BaseNotes):
    def setUp(self):
        super().setUp()
        self._noter(self.devoir, [8, 5, 10])
        self._noter(self.composition, [18, 12, 12])
        self._noter(self.compo_francais, [15, 15, 9])

    def test_releve_detaille_les_notes(self):
        r = self.client.get(
            f"/api/eleves/{self.eleves[0].pk}/releve/?id_periode={self.periode.pk}")
        self.assertEqual(r.status_code, 200, r.content)
        maths = next(m for m in r.data["matieres"] if m["matiere"] == "Mathématiques")
        self.assertEqual(len(maths["notes"]), 2)
        devoir = next(n for n in maths["notes"] if n["type"] == "Devoir")
        self.assertEqual(Decimal(devoir["valeur"]), Decimal("8.00"))
        self.assertEqual(Decimal(devoir["sur_20"]), Decimal("16.00"))

    def test_releve_sans_periode_refuse(self):
        r = self.client.get(f"/api/eleves/{self.eleves[0].pk}/releve/")
        self.assertEqual(r.status_code, 400, r.content)

    def test_palmares_ordonne(self):
        r = self.client.get(
            f"/api/bulletins/palmares/?id_classe={self.classe.pk}"
            f"&id_periode={self.periode.pk}")
        self.assertEqual(r.status_code, 200, r.content)
        rangs = [l["rang"] for l in r.data["lignes"]]
        self.assertEqual(rangs, sorted(rangs))
        self.assertEqual(r.data["lignes"][0]["matricule"], self.eleves[0].matricule)

    def test_palmares_tronque(self):
        r = self.client.get(
            f"/api/bulletins/palmares/?id_classe={self.classe.pk}"
            f"&id_periode={self.periode.pk}&limite=2")
        self.assertEqual(len(r.data["lignes"]), 2)

    def test_statistiques(self):
        r = self.client.get(
            f"/api/bulletins/statistiques/?id_classe={self.classe.pk}"
            f"&id_periode={self.periode.pk}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["inscrits"], 3)
        self.assertEqual(r.data["ensemble"]["effectif"], 3)
        self.assertEqual(r.data["ensemble"]["reussite"], 3)
        self.assertEqual(Decimal(r.data["ensemble"]["taux_reussite"]), Decimal("100.0"))
        self.assertEqual(len(r.data["matieres"]), 2)

    def test_classe_inconnue_refusee(self):
        r = self.client.get(
            f"/api/bulletins/statistiques/?id_classe=999999"
            f"&id_periode={self.periode.pk}")
        self.assertEqual(r.status_code, 400, r.content)

    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    def test_exports_pedagogiques(self):
        base = f"?id_classe={self.classe.pk}&id_periode={self.periode.pk}"
        cas = [
            ("/api/bulletins/palmares/export/" + base + "&format_export=xlsx", self.XLSX, b"PK"),
            ("/api/bulletins/palmares/export/" + base + "&format_export=pdf",
             "application/pdf", b"%PDF-"),
            ("/api/bulletins/moyennes/export/" + base + "&format_export=xlsx", self.XLSX, b"PK"),
            ("/api/bulletins/moyennes/export/" + base + "&format_export=pdf",
             "application/pdf", b"%PDF-"),
            (f"/api/eleves/{self.eleves[0].pk}/releve/export/?id_periode={self.periode.pk}",
             "application/pdf", b"%PDF-"),
        ]
        for url, type_mime, entete in cas:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, r.content[:200])
                self.assertEqual(r["Content-Type"], type_mime)
                self.assertTrue(r.content.startswith(entete))
                self.assertIn("attachment;", r["Content-Disposition"])

    def test_export_journalise(self):
        self.client.get(
            f"/api/bulletins/palmares/export/?id_classe={self.classe.pk}"
            f"&id_periode={self.periode.pk}&format_export=pdf")
        self.assertTrue(
            models.JournalActivite.objects.filter(action="export", entite="bulletin").exists(),
            "un export pédagogique doit être tracé sous sa propre entité")


class BaseDossierFinancier(BaseFinances):
    """
    Un élève, deux années scolaires, un catalogue de frais.

    Deux années dès le départ : la règle la plus facile à casser du module est
    « ne jamais mélanger les paiements de deux années », et on ne la teste pas
    avec une seule.
    """

    def setUp(self):
        super().setUp()
        self.eleve = self.eleves[0]

        self.inscription_frais = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Inscription", nature="inscription", montant_defaut=50000)

        self.annee_passee = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2025-2026",
            date_debut=date(2025, 10, 1), date_fin=date(2026, 7, 31),
            statut=models.AnneeScolaire.Statut.ARCHIVEE,
        )
        self.classe_passee = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee_passee,
            id_niveau=self.niveau, nom="5ème A", capacite_max=40,
        )
        # L'année courante a été archivée par la création ci-dessus si elle
        # était active : on la remet active, c'est celle qu'ouvre l'écran.
        self.annee.statut = models.AnneeScolaire.Statut.ACTIVE
        self.annee.save()

    def _frais(self, montant, echeance, type_frais=None, annee=None, eleve=None,
               remise=0):
        return models.FraisEleve.objects.create(
            id_eleve=eleve or self.eleve,
            id_type_frais=type_frais or self.type_frais,
            id_annee_scolaire=annee or self.annee,
            montant_du=montant, remise=remise, echeance=echeance,
        )

    def _payer(self, frais, montant, methode="especes", **extra):
        paiement = models.Paiement.objects.create(
            id_frais_eleve=frais, montant=Decimal(str(montant)), methode=methode,
            id_utilisateur_caissier=self.admin, **extra)
        finances.enregistrer_paiement(paiement, utilisateur=self.admin)
        return paiement

    def _droits_exoneres(self, eleve=None, annee=None):
        """
        Droits d'inscription émis puis entièrement remisés : la ligne existe —
        l'élève peut être à jour — sans rien changer aux totaux du test.
        """
        annee = annee or self.annee
        return self._frais(50000, date(annee.date_fin.year, 6, 30), self.inscription_frais,
                           annee=annee, eleve=eleve, remise=50000)

    def _situation(self, annee=None, aujourdhui=None):
        # Date figée : les statuts dépendent des échéances dépassées, et un
        # test qui lit l'horloge réelle change de verdict avec le calendrier.
        return finances.situation_eleve(
            self.ecole, self.eleve, annee, aujourdhui=aujourdhui or date(2026, 10, 1))


class TestSituationFinanciereEleve(BaseDossierFinancier):
    def test_nouvel_eleve_tout_est_du(self):
        """Aucun règlement : dû complet, reste égal au dû, statut « non payé »."""
        self._frais(50000, None, self.inscription_frais)
        for mois in (10, 11, 12):
            self._frais(25000, date(2026, mois, 5))

        resume = self._situation()["resume"]
        self.assertEqual(resume["total_du"], 125000)
        self.assertEqual(resume["total_paye"], 0)
        self.assertEqual(resume["reste"], 125000)
        self.assertEqual(resume["statut"], "non_paye")

    def test_paiement_complet_met_a_jour(self):
        self._droits_exoneres()
        frais = self._frais(25000, date(2026, 10, 5))
        self._payer(frais, 25000)

        situation = self._situation()
        self.assertEqual(situation["resume"]["statut"], "a_jour")
        self.assertEqual(situation["resume"]["reste"], 0)
        self.assertEqual(situation["frais"][0]["statut"], "paye")

    def test_paiement_partiel_puis_solde(self):
        """25 000 dus, 10 000 versés : partiel ; les 15 000 suivants soldent."""
        self._droits_exoneres()
        frais = self._frais(25000, date(2026, 11, 5))
        self._payer(frais, 10000)

        ligne = self._situation()["frais"][0]
        self.assertEqual((ligne["montant"], ligne["paye"], ligne["reste"]),
                         (25000, 10000, 15000))
        self.assertEqual(ligne["statut"], "partiel")

        self._payer(frais, 15000)
        ligne = self._situation()["frais"][0]
        self.assertEqual((ligne["paye"], ligne["reste"]), (25000, 0))
        self.assertEqual(ligne["statut"], "paye")
        self.assertEqual(self._situation()["resume"]["statut"], "a_jour")

    def test_echeance_depassee_passe_en_retard(self):
        """Une échéance dépassée l'emporte sur « partiel » : c'est elle qu'on relance."""
        frais = self._frais(25000, date(2026, 10, 5))
        self._payer(frais, 10000)

        situation = self._situation(aujourdhui=date(2026, 11, 20))
        self.assertEqual(situation["resume"]["statut"], "en_retard")
        self.assertTrue(situation["frais"][0]["en_retard"])
        self.assertEqual(situation["resume"]["frais_en_retard"], 1)

        # Avant l'échéance, le même frais partiellement réglé n'est pas en retard.
        situation = self._situation(aujourdhui=date(2026, 10, 1))
        self.assertEqual(situation["resume"]["statut"], "partiel")

    def test_echeancier_couvre_tous_les_mois_de_lannee(self):
        """Octobre 2026 → juillet 2027 : dix mois, listés même sans frais."""
        self._payer(self._frais(25000, date(2026, 10, 5)), 25000)
        self._frais(25000, date(2026, 11, 5))

        echeancier = self._situation()["echeancier"]
        self.assertEqual([m["mois"] for m in echeancier][:3],
                         ["2026-10", "2026-11", "2026-12"])
        self.assertEqual(len(echeancier), 10)
        par_mois = {m["mois"]: m["statut"] for m in echeancier}
        self.assertEqual(par_mois["2026-10"], "a_jour")
        self.assertEqual(par_mois["2026-11"], "non_paye")
        self.assertEqual(par_mois["2026-12"], "sans_frais")

    def test_frais_sans_echeance_reste_visible(self):
        """Une inscription n'a pas de mois : elle ne doit pas disparaître."""
        self._frais(50000, None, self.inscription_frais)
        echeancier = self._situation()["echeancier"]
        self.assertEqual(echeancier[-1]["libelle"], "Sans échéance")
        self.assertEqual(echeancier[-1]["du"], 50000)

    def test_historique_des_paiements(self):
        frais = self._frais(25000, date(2026, 10, 5))
        self._payer(frais, 25000, methode="wave", reference_transaction="TXN-88213",
                    commentaire="Réglé par la mère")

        paiement = self._situation()["paiements"][0]
        self.assertEqual(paiement["montant"], 25000)
        self.assertEqual(paiement["methode_libelle"], "Wave")
        self.assertEqual(paiement["reference_transaction"], "TXN-88213")
        self.assertEqual(paiement["commentaire"], "Réglé par la mère")
        self.assertEqual(paiement["enregistre_par"], "Admin Test")
        self.assertTrue(paiement["numero_recu"])

    def test_libelle_date_le_mois_quand_le_type_se_repete(self):
        self._frais(50000, None, self.inscription_frais)
        self._frais(25000, date(2026, 10, 5))
        self._frais(25000, date(2026, 11, 5))

        libelles = [f["libelle"] for f in self._situation()["frais"]]
        self.assertIn("Inscription", libelles)
        self.assertIn("Mensualité octobre", libelles)
        self.assertIn("Mensualité novembre", libelles)


class TestAnneesNonMelangees(BaseDossierFinancier):
    def setUp(self):
        super().setUp()
        models.Inscription.objects.create(
            id_eleve=self.eleve, id_classe=self.classe_passee,
            id_annee_scolaire=self.annee_passee,
            date_inscription=self.annee_passee.date_debut)
        self._droits_exoneres(annee=self.annee_passee)
        self._payer(self._frais(30000, date(2025, 11, 5), annee=self.annee_passee), 30000)
        self._frais(25000, date(2026, 11, 5))

    def test_chaque_annee_a_sa_propre_ardoise(self):
        passee = self._situation(self.annee_passee)
        self.assertEqual(passee["resume"]["total_du"], 30000)
        self.assertEqual(passee["resume"]["total_paye"], 30000)
        self.assertEqual(passee["resume"]["statut"], "a_jour")

        courante = self._situation(self.annee)
        self.assertEqual(courante["resume"]["total_du"], 25000)
        self.assertEqual(courante["resume"]["total_paye"], 0)
        self.assertEqual(courante["resume"]["statut"], "non_paye")

    def test_le_paiement_dune_annee_ne_compte_pas_dans_lautre(self):
        self.assertEqual(len(self._situation(self.annee)["paiements"]), 0)
        self.assertEqual(len(self._situation(self.annee_passee)["paiements"]), 1)

    def test_les_deux_annees_sont_proposees_au_choix(self):
        libelles = [a["libelle"] for a in self._situation()["annees"]]
        self.assertEqual(libelles, ["2026-2027", "2025-2026"])

    def test_sans_parametre_on_ouvre_lannee_active(self):
        self.assertEqual(self._situation()["annee"]["libelle"], "2026-2027")

    def test_le_dossier_ne_totalise_plus_toutes_les_annees(self):
        """Le dossier scolaire montrait 55 000 dus : la somme de deux ardoises."""
        dossier = scolarite.dossier(self.ecole, self.eleve)
        self.assertEqual(dossier["finances"]["annee"], "2026-2027")
        self.assertEqual(dossier["finances"]["total_du"], 25000)
        self.assertEqual(dossier["finances"]["total_paye"], 0)


class TestRemise(BaseDossierFinancier):
    def test_le_montant_initial_reste_visible(self):
        self._frais(300000, date(2026, 10, 5), remise=50000)
        resume = self._situation()["resume"]
        self.assertEqual(resume["frais_initial"], 300000)
        self.assertEqual(resume["remise"], 50000)
        self.assertEqual(resume["total_du"], 250000)
        self.assertEqual(resume["reste"], 250000)

    def test_la_remise_peut_solder_un_frais(self):
        """Une remise accordée après coup doit faire suivre le statut."""
        self._droits_exoneres()
        frais = self._frais(25000, date(2026, 11, 5))
        self._payer(frais, 20000)
        self.assertEqual(frais.statut, "partiel")

        reponse = self.client.patch(f"/api/frais-eleves/{frais.pk}/",
                                    {"remise": "5000"}, format="json")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        frais.refresh_from_db()
        self.assertEqual(frais.statut, "paye")
        self.assertEqual(self._situation()["resume"]["statut"], "a_jour")

    def test_une_remise_ne_peut_pas_effacer_ce_qui_est_deja_verse(self):
        frais = self._frais(25000, date(2026, 11, 5))
        self._payer(frais, 20000)
        reponse = self.client.patch(f"/api/frais-eleves/{frais.pk}/",
                                    {"remise": "10000"}, format="json")
        self.assertEqual(reponse.status_code, 400, reponse.content)

    def test_la_remise_allege_le_bilan(self):
        self._frais(100000, date(2026, 10, 5), remise=40000)
        bilan = finances.bilan(self.ecole, self.annee)
        self.assertEqual(bilan["recouvrement"]["attendu"], 60000)
        self.assertEqual(bilan["recouvrement"]["reste"], 60000)


class TestOngletFinancesDuDossier(BaseDossierFinancier):
    """GET /api/eleves/{id}/finances/ — ce que l'écran ouvre."""

    def test_une_seule_requete_rend_tout_le_dossier_financier(self):
        self._payer(self._frais(25000, date(2026, 10, 5)), 25000)
        self._frais(25000, date(2026, 11, 5))

        reponse = self.client.get(f"/api/eleves/{self.eleve.pk}/finances/")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        for bloc in ("eleve", "annee", "annees", "scolarite", "resume", "frais",
                     "echeancier", "paiements"):
            self.assertIn(bloc, reponse.data)
        self.assertEqual(reponse.data["resume"]["total_du"], 50000)
        self.assertEqual(reponse.data["resume"]["total_paye"], 25000)
        self.assertEqual(reponse.data["resume"]["reste"], 25000)
        self.assertEqual(reponse.data["scolarite"]["classe"], "6ème A")

    def test_le_choix_de_lannee_change_les_chiffres(self):
        models.Inscription.objects.create(
            id_eleve=self.eleve, id_classe=self.classe_passee,
            id_annee_scolaire=self.annee_passee,
            date_inscription=self.annee_passee.date_debut)
        self._frais(30000, date(2025, 11, 5), annee=self.annee_passee)
        self._frais(25000, date(2026, 11, 5))

        courante = self.client.get(f"/api/eleves/{self.eleve.pk}/finances/")
        self.assertEqual(courante.data["resume"]["total_du"], 25000)

        passee = self.client.get(
            f"/api/eleves/{self.eleve.pk}/finances/",
            {"id_annee_scolaire": self.annee_passee.pk})
        self.assertEqual(passee.data["resume"]["total_du"], 30000)
        self.assertEqual(passee.data["scolarite"]["classe"], "5ème A")

    def test_annee_inconnue_refusee(self):
        reponse = self.client.get(f"/api/eleves/{self.eleve.pk}/finances/",
                                  {"id_annee_scolaire": 999999})
        self.assertEqual(reponse.status_code, 400)

    def test_une_autre_ecole_ne_voit_rien(self):
        reponse = self.client_autre.get(f"/api/eleves/{self.eleve.pk}/finances/")
        self.assertEqual(reponse.status_code, 404)

    def test_un_parent_ne_voit_pas_un_enfant_qui_nest_pas_le_sien(self):
        client = self._client(self.parent_user.email)
        reponse = client.get(f"/api/eleves/{self.eleve.pk}/finances/")
        self.assertEqual(reponse.status_code, 404)

    def test_un_enseignant_na_pas_acces(self):
        client = self._client(self.enseignant_user.email)
        reponse = client.get(f"/api/eleves/{self.eleve.pk}/finances/")
        self.assertEqual(reponse.status_code, 403)


class TestEncaissementDepuisLeDossier(BaseDossierFinancier):
    """POST /api/paiements/encaisser/ — le paiement saisi côté élève."""

    def setUp(self):
        super().setUp()
        self.octobre = self._frais(25000, date(2026, 10, 5))
        self.novembre = self._frais(25000, date(2026, 11, 5))

    def _encaisser(self, **corps):
        corps.setdefault("id_eleve", self.eleve.pk)
        corps.setdefault("methode", "especes")
        return self.client.post("/api/paiements/encaisser/", corps, format="json")

    def test_le_versement_solde_la_plus_ancienne_echeance(self):
        reponse = self._encaisser(montant="25000")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.octobre.refresh_from_db()
        self.novembre.refresh_from_db()
        self.assertEqual(self.octobre.statut, "paye")
        self.assertEqual(self.novembre.statut, "impaye")

    def test_un_versement_peut_couvrir_deux_echeances(self):
        """50 000 sur deux mensualités : deux paiements, deux reçus."""
        self._droits_exoneres()
        reponse = self._encaisser(montant="50000")
        self.assertEqual(len(reponse.data["paiements"]), 2)
        self.assertEqual(models.Recu.objects.count(), 2)
        self.assertEqual(reponse.data["situation"]["resume"]["statut"], "a_jour")

    def test_versement_partiel_puis_complement(self):
        self._encaisser(montant="10000")
        self.octobre.refresh_from_db()
        self.assertEqual(self.octobre.statut, "partiel")
        self.assertEqual(self.octobre.solde_restant, Decimal("15000"))

        self._encaisser(montant="15000")
        self.octobre.refresh_from_db()
        self.assertEqual(self.octobre.statut, "paye")

    def test_on_peut_viser_un_mois_precis(self):
        self._encaisser(montant="25000", mois="2026-11")
        self.octobre.refresh_from_db()
        self.novembre.refresh_from_db()
        self.assertEqual(self.octobre.statut, "impaye")
        self.assertEqual(self.novembre.statut, "paye")

    def test_montant_superieur_au_reste_refuse(self):
        reponse = self._encaisser(montant="80000")
        self.assertEqual(reponse.status_code, 400, reponse.content)
        self.assertEqual(models.Paiement.objects.count(), 0)

    def test_montant_negatif_refuse(self):
        self.assertEqual(self._encaisser(montant="-1000").status_code, 400)
        self.assertEqual(models.Paiement.objects.count(), 0)

    def test_la_date_et_le_commentaire_sont_conserves(self):
        reponse = self._encaisser(
            montant="25000", methode="wave", date_paiement="2026-09-28T10:30:00Z",
            reference_transaction="TXN-99", commentaire="Reçu samedi à la caisse")
        paiement = models.Paiement.objects.get(pk=reponse.data["paiements"][0]["id"])
        self.assertEqual(paiement.date_paiement.date(), date(2026, 9, 28))
        self.assertEqual(paiement.reference_transaction, "TXN-99")
        self.assertEqual(paiement.commentaire, "Reçu samedi à la caisse")

    def test_lencaissement_alimente_la_caisse_et_le_recu(self):
        reponse = self._encaisser(montant="25000")
        paiement = models.Paiement.objects.get(pk=reponse.data["paiements"][0]["id"])
        self.assertTrue(models.Recu.objects.filter(id_paiement=paiement).exists())
        mouvement = models.JournalCaisse.objects.get(id_paiement=paiement)
        self.assertEqual(mouvement.credit, Decimal("25000"))
        self.assertEqual(mouvement.type_journal, "caisse")

    def test_qui_a_encaisse_est_trace(self):
        reponse = self._encaisser(montant="25000")
        paiement = models.Paiement.objects.get(pk=reponse.data["paiements"][0]["id"])
        self.assertEqual(paiement.id_utilisateur_caissier, self.admin)
        self.assertTrue(models.JournalActivite.objects.filter(
            entite="paiement", id_entite=paiement.pk).exists())

    def test_la_situation_renvoyee_est_deja_a_jour(self):
        """Le mois passe de « non payé » à « payé » sans second appel."""
        avant = self.client.get(f"/api/eleves/{self.eleve.pk}/finances/")
        octobre = [m for m in avant.data["echeancier"] if m["mois"] == "2026-10"][0]
        self.assertEqual(octobre["statut"], "non_paye")

        reponse = self._encaisser(montant="25000")
        octobre = [m for m in reponse.data["situation"]["echeancier"]
                   if m["mois"] == "2026-10"][0]
        self.assertEqual(octobre["statut"], "a_jour")

    def test_un_eleve_dune_autre_ecole_est_refuse(self):
        reponse = self.client_autre.post(
            "/api/paiements/encaisser/",
            {"id_eleve": self.eleve.pk, "montant": "25000", "methode": "especes"},
            format="json")
        self.assertEqual(reponse.status_code, 400, reponse.content)
        self.assertEqual(models.Paiement.objects.count(), 0)


class TestSituationGlobale(BaseDossierFinancier):
    """GET /api/frais-eleves/situation/ — un élève par ligne."""

    def setUp(self):
        super().setUp()
        self.autre_classe = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=40,
        )
        # eleves[0] à jour, eleves[1] partiel, eleves[2] rien versé.
        self._droits_exoneres(self.eleves[0])
        self._payer(self._frais(25000, date(2026, 11, 5), eleve=self.eleves[0]), 25000)
        self._payer(self._frais(25000, date(2026, 11, 5), eleve=self.eleves[1]), 10000)
        self._frais(25000, date(2026, 11, 5), eleve=self.eleves[2])

    def _situation_globale(self, **filtres):
        return self.client.get("/api/frais-eleves/situation/", filtres)

    def test_une_ligne_par_eleve_avec_son_statut(self):
        reponse = self._situation_globale()
        self.assertEqual(reponse.status_code, 200, reponse.content)
        par_eleve = {l["id_eleve"]: l for l in reponse.data["eleves"]}
        self.assertEqual(len(par_eleve), 3)
        self.assertEqual(par_eleve[self.eleves[0].pk]["statut"], "a_jour")
        self.assertEqual(par_eleve[self.eleves[1].pk]["statut"], "partiel")
        self.assertEqual(par_eleve[self.eleves[2].pk]["statut"], "non_paye")
        self.assertEqual(par_eleve[self.eleves[1].pk]["reste"], 15000)

    def test_les_totaux_suivent(self):
        totaux = self._situation_globale().data["totaux"]
        self.assertEqual(totaux["du"], 75000)
        self.assertEqual(totaux["paye"], 35000)
        self.assertEqual(totaux["reste"], 40000)

    def test_filtre_par_statut(self):
        reponse = self._situation_globale(statut="partiel")
        self.assertEqual([l["id_eleve"] for l in reponse.data["eleves"]],
                         [self.eleves[1].pk])
        # La répartition reste celle de toute la promotion.
        self.assertEqual(reponse.data["repartition"]["a_jour"], 1)

    def test_filtre_par_classe_et_par_niveau(self):
        eleve = self._inscrire("E-9", "Autre", classe=self.autre_classe)
        self._frais(25000, date(2026, 11, 5), eleve=eleve)

        reponse = self._situation_globale(classe=self.autre_classe.pk)
        self.assertEqual([l["id_eleve"] for l in reponse.data["eleves"]], [eleve.pk])

        reponse = self._situation_globale(niveau=self.niveau.pk)
        self.assertEqual(len(reponse.data["eleves"]), 4)

    def test_recherche_par_nom(self):
        reponse = self._situation_globale(q="Nom2")
        self.assertEqual([l["nom_complet"] for l in reponse.data["eleves"]],
                         ["Test Nom2"])

    def test_statut_inconnu_refuse(self):
        self.assertEqual(self._situation_globale(statut="bizarre").status_code, 400)

    def test_une_classe_dune_autre_ecole_est_refusee(self):
        classe = models.Classe.objects.create(
            id_ecole=self.autre, id_annee_scolaire=models.AnneeScolaire.objects.create(
                id_ecole=self.autre, libelle="2026-2027",
                date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31)),
            id_niveau=models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème"),
            nom="6ème Z", capacite_max=30)
        self.assertEqual(self._situation_globale(classe=classe.pk).status_code, 400)

    def test_lautre_ecole_ne_voit_que_les_siens(self):
        reponse = self.client_autre.get("/api/frais-eleves/situation/")
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.data["eleves"], [])


class TestRechercheDesPaiements(BaseDossierFinancier):
    def setUp(self):
        super().setUp()
        self.frais = self._frais(25000, date(2026, 10, 5))
        self._payer(self.frais, 25000, methode="wave",
                    reference_transaction="TXN-88213")

    def test_filtre_par_eleve(self):
        reponse = self.client.get("/api/paiements/", {"eleve": self.eleve.pk})
        self.assertEqual(len(self.resultats(reponse)), 1)
        reponse = self.client.get("/api/paiements/", {"eleve": self.eleves[1].pk})
        self.assertEqual(len(self.resultats(reponse)), 0)

    def test_filtre_par_reference_de_transaction(self):
        reponse = self.client.get("/api/paiements/", {"reference": "88213"})
        self.assertEqual(len(self.resultats(reponse)), 1)
        reponse = self.client.get("/api/paiements/", {"reference": "INCONNU"})
        self.assertEqual(len(self.resultats(reponse)), 0)

    def test_filtre_par_mois_decheance(self):
        self.assertEqual(
            len(self.resultats(self.client.get("/api/paiements/", {"mois": "2026-10"}))), 1)
        self.assertEqual(
            len(self.resultats(self.client.get("/api/paiements/", {"mois": "2026-11"}))), 0)

    def test_mois_illisible_refuse(self):
        reponse = self.client.get("/api/paiements/", {"mois": "octobre"})
        self.assertEqual(reponse.status_code, 400)

    def test_filtre_par_classe_ne_duplique_pas(self):
        reponse = self.client.get("/api/paiements/", {"classe": self.classe.pk})
        self.assertEqual(len(self.resultats(reponse)), 1)


class TestBilanEnrichi(BaseDossierFinancier):
    def test_repartition_des_eleves_et_derniers_paiements(self):
        # Datés dans l'année scolaire : le bilan borne les recettes à sa période.
        self._droits_exoneres(self.eleves[0])
        self._payer(self._frais(25000, date(2026, 11, 5), eleve=self.eleves[0]), 25000,
                    date_paiement=datetime(2026, 11, 6, 9, 0, tzinfo=dt_timezone.utc))
        self._payer(self._frais(25000, date(2026, 11, 5), eleve=self.eleves[1]), 10000,
                    date_paiement=datetime(2026, 11, 7, 9, 0, tzinfo=dt_timezone.utc))
        self._frais(25000, date(2026, 11, 5), eleve=self.eleves[2])

        bilan = finances.bilan(self.ecole, self.annee)
        self.assertEqual(bilan["eleves"]["a_jour"], 1)
        self.assertEqual(bilan["eleves"]["partiel"], 1)
        self.assertEqual(bilan["eleves"]["non_paye"], 1)
        self.assertEqual(bilan["eleves"]["total"], 3)
        self.assertEqual(len(bilan["derniers_paiements"]), 2)
        self.assertEqual(bilan["derniers_paiements"][0]["eleve"], "Test Nom2")


class BaseRentree(BaseDossierFinancier):
    """
    Une rentrée en cours : trois élèves inscrits l'an passé, aucun encore
    repassé sur l'année active.
    """

    def setUp(self):
        super().setUp()
        self.classe_cible = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème C", capacite_max=2,
        )
        # Les trois élèves de BaseFinances sont inscrits sur l'année active :
        # on les bascule sur l'année passée pour reconstituer une rentrée.
        models.Inscription.objects.filter(id_eleve__in=self.eleves).delete()
        for eleve in self.eleves:
            models.Inscription.objects.create(
                id_eleve=eleve, id_classe=self.classe_passee,
                id_annee_scolaire=self.annee_passee,
                date_inscription=self.annee_passee.date_debut,
                statut=models.Inscription.Statut.EN_COURS)

    def _premier_mois(self, eleve):
        """La première mensualité émise d'office par la réinscription."""
        return (models.FraisEleve.objects
                .filter(id_eleve=eleve, id_annee_scolaire=self.annee, id_type_frais=self.type_frais)
                .order_by("echeance").first())

    def _droits(self, eleve):
        """Les droits d'inscription émis d'office par la réinscription."""
        return models.FraisEleve.objects.get(
            id_eleve=eleve, id_annee_scolaire=self.annee, id_type_frais=self.inscription_frais)


class TestPreInscription(BaseRentree):
    """Une place réservée n'est pas une inscription tant que rien n'est payé."""

    def _reinscrire(self, eleve, classe=None):
        return self.client.post(
            f"/api/eleves/{eleve.pk}/reinscrire/",
            {"id_classe": (classe or self.classe_cible).pk}, format="json")

    def test_une_reinscription_nait_en_attente(self):
        reponse = self._reinscrire(self.eleves[0])
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["statut"], "en_attente")
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])
        self.assertEqual(inscription.statut, "en_attente")

    def test_un_pre_inscrit_ne_figure_pas_parmi_les_inscrits(self):
        self._reinscrire(self.eleves[0])
        reponse = self.client.get("/api/eleves/",
                                  {"id_annee_scolaire": self.annee.pk})
        self.assertNotIn(self.eleves[0].pk,
                         {l["id"] for l in self.resultats(reponse)})

    def test_il_figure_dans_la_liste_des_pre_inscrits(self):
        self._reinscrire(self.eleves[0])
        reponse = self.client.get("/api/eleves/", {
            "id_annee_scolaire": self.annee.pk, "inscription": "en_attente"})
        self.assertEqual({l["id"] for l in self.resultats(reponse)},
                         {self.eleves[0].pk})

    def test_les_droits_dinscription_regles_confirment(self):
        """Droits d'inscription et premier mois réglés : l'élève est inscrit."""
        reponse = self._reinscrire(self.eleves[0])
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])
        droits = self._droits(self.eleves[0])

        self._payer(droits, 50000)
        inscription.refresh_from_db()
        # Les droits seuls ne suffisent pas : il faut aussi le premier mois.
        self.assertEqual(inscription.statut, "en_attente")

        self._payer(self._premier_mois(self.eleves[0]), 25000)
        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_cours")
        liste = self.client.get("/api/eleves/", {"id_annee_scolaire": self.annee.pk})
        self.assertIn(self.eleves[0].pk, {l["id"] for l in self.resultats(liste)})

    def test_une_mensualite_ne_confirme_pas(self):
        """Elle paie le mois, pas l'entrée."""
        reponse = self._reinscrire(self.eleves[0])
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])
        self._frais(50000, None, self.inscription_frais, eleve=self.eleves[0])

        self._payer(self._frais(25000, date(2026, 11, 5), eleve=self.eleves[0]), 25000)

        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_attente")

    def test_des_droits_partiellement_regles_ne_confirment_pas(self):
        reponse = self._reinscrire(self.eleves[0])
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])
        droits = self._droits(self.eleves[0])

        self._payer(droits, 30000)
        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_attente")

        # Premier mois réglé, puis le complément des droits : c'est ce dernier
        # versement qui confirme.
        self._payer(self._premier_mois(self.eleves[0]), 25000)
        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_attente")
        self._payer(droits, 20000)
        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_cours")

    def test_une_remise_qui_solde_les_droits_confirme(self):
        """Le solde compte, pas l'origine des fonds."""
        reponse = self._reinscrire(self.eleves[0])
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])
        droits = self._droits(self.eleves[0])
        self._payer(droits, 30000)
        self._payer(self._premier_mois(self.eleves[0]), 25000)

        r = self.client.patch(f"/api/frais-eleves/{droits.pk}/",
                              {"remise": "20000"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_cours")

    def test_sans_droits_dinscription_emis_rien_ne_confirme(self):
        """Le blocage annoncé : pas de frais d'inscription, pas de confirmation."""
        # Sans tarif d'inscription au catalogue, rien n'est émis d'office.
        self.inscription_frais.delete()
        reponse = self._reinscrire(self.eleves[0])
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])

        self._payer(self._frais(25000, date(2026, 11, 5), eleve=self.eleves[0]), 25000)

        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_attente")
        situation = finances.situation_eleve(self.ecole, self.eleves[0], self.annee)
        self.assertFalse(situation["confirmation"]["frais_emis"])
        self.assertFalse(situation["confirmation"]["confirmee"])

    def test_un_paiement_dune_autre_annee_ne_confirme_rien(self):
        reponse = self._reinscrire(self.eleves[0])
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])
        frais = self._frais(25000, date(2025, 11, 5), eleve=self.eleves[0],
                            annee=self.annee_passee)

        self._payer(frais, 25000)

        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_attente")

    def test_lencaissement_par_le_dossier_confirme_aussi(self):
        """Tous les chemins de paiement passent par le même verrou."""
        reponse = self._reinscrire(self.eleves[0])
        inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])
        encaissement = self.client.post("/api/paiements/encaisser/", {
            # Droits (50 000) et premier mois (25 000), imputés du plus ancien au plus récent.
            "id_eleve": self.eleves[0].pk, "montant": "75000", "methode": "wave",
        }, format="json")
        self.assertEqual(encaissement.status_code, 201, encaissement.content)

        inscription.refresh_from_db()
        self.assertEqual(inscription.statut, "en_cours")
        self.assertTrue(encaissement.data["situation"]["confirmation"]["confirmee"])

    def test_les_frais_sont_generables_pour_un_pre_inscrit(self):
        """Sans ça, l'élève n'aurait jamais rien à payer — donc jamais confirmé."""
        self._reinscrire(self.eleves[0])
        # Le 5 du mois est déjà émis d'office par la réinscription.
        reponse = self._generer(echeances=["2026-11-20"],
                                id_classe=self.classe_cible.pk)
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["crees"], 1)

    def test_toutes_retrouve_lancien_comportement(self):
        self._reinscrire(self.eleves[0])
        reponse = self.client.get("/api/eleves/", {
            "id_annee_scolaire": self.annee.pk, "inscription": "toutes"})
        self.assertIn(self.eleves[0].pk, {l["id"] for l in self.resultats(reponse)})

    def test_etat_inconnu_refuse(self):
        reponse = self.client.get("/api/eleves/", {
            "id_annee_scolaire": self.annee.pk, "inscription": "bizarre"})
        self.assertEqual(reponse.status_code, 400)


class TestFileDeReinscription(BaseRentree):
    """GET /api/eleves/a-reinscrire/ — ceux que la nouvelle année a fait disparaître."""

    def test_les_eleves_de_lan_passe_sont_en_attente_de_reinscription(self):
        reponse = self.client.get("/api/eleves/a-reinscrire/",
                                  {"id_annee_scolaire": self.annee.pk})
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual(reponse.data["total"], 3)
        ligne = reponse.data["eleves"][0]
        self.assertEqual(ligne["derniere_classe"], "5ème A")
        self.assertEqual(ligne["derniere_annee"], "2025-2026")

    def test_un_eleve_reinscrit_sort_de_la_file(self):
        self.client.post(f"/api/eleves/{self.eleves[0].pk}/reinscrire/",
                         {"id_classe": self.classe_cible.pk}, format="json")
        reponse = self.client.get("/api/eleves/a-reinscrire/",
                                  {"id_annee_scolaire": self.annee.pk})
        self.assertEqual(reponse.data["total"], 2)
        self.assertNotIn(self.eleves[0].pk,
                         {l["id"] for l in reponse.data["eleves"]})

    def test_un_eleve_radie_nest_pas_propose(self):
        scolarite.radier(self.ecole, self.eleves[0], motif="Départ")
        reponse = self.client.get("/api/eleves/a-reinscrire/",
                                  {"id_annee_scolaire": self.annee.pk})
        self.assertEqual(reponse.data["total"], 2)

    def test_filtre_par_classe_et_recherche(self):
        reponse = self.client.get("/api/eleves/a-reinscrire/", {
            "id_annee_scolaire": self.annee.pk, "classe": self.classe_passee.pk})
        self.assertEqual(reponse.data["total"], 3)

        reponse = self.client.get("/api/eleves/a-reinscrire/", {
            "id_annee_scolaire": self.annee.pk, "q": "Nom2"})
        self.assertEqual([l["nom_complet"] for l in reponse.data["eleves"]],
                         ["Test Nom2"])

    def test_lautre_ecole_ne_voit_personne(self):
        reponse = self.client_autre.get("/api/eleves/a-reinscrire/",
                                        {"id_annee_scolaire": self.annee.pk})
        self.assertEqual(reponse.status_code, 400)


class TestReinscriptionIndividuelle(BaseRentree):
    def _reinscrire(self, eleve, classe=None, **corps):
        corps.setdefault("id_classe", (classe or self.classe_cible).pk)
        return self.client.post(f"/api/eleves/{eleve.pk}/reinscrire/", corps,
                                format="json")

    def test_lelevemonte_dans_la_classe_choisie(self):
        reponse = self._reinscrire(self.eleves[0])
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["classe"], "6ème C")
        self.assertEqual(reponse.data["classe_precedente"], "5ème A")
        self.assertEqual(reponse.data["annee"], "2026-2027")

    def test_le_mouvement_est_trace(self):
        self._reinscrire(self.eleves[0])
        trace = models.HistoriqueScolaire.objects.filter(
            id_eleve=self.eleves[0],
            type_evenement=models.HistoriqueScolaire.TypeEvenement.REINSCRIPTION)
        self.assertTrue(trace.exists())
        self.assertTrue(models.JournalActivite.objects.filter(
            entite="inscription").exists())

    def test_leffectif_de_la_classe_suit(self):
        self._reinscrire(self.eleves[0])
        self.classe_cible.refresh_from_db()
        self.assertEqual(self.classe_cible.effectif, 1)

    def test_le_depassement_de_capacite_avertit_sans_bloquer(self):
        """Capacité 2 : le troisième passe, avec un avertissement."""
        self._reinscrire(self.eleves[0])
        self._reinscrire(self.eleves[1])
        reponse = self._reinscrire(self.eleves[2])
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertIn("capacité", reponse.data["avertissement"])

    def test_deux_fois_le_meme_eleve_est_refuse(self):
        self._reinscrire(self.eleves[0])
        reponse = self._reinscrire(self.eleves[0])
        self.assertEqual(reponse.status_code, 400, reponse.content)
        self.assertEqual(
            models.Inscription.objects.filter(
                id_eleve=self.eleves[0], id_annee_scolaire=self.annee).count(), 1)

    def test_un_eleve_radie_ne_peut_pas_etre_reinscrit(self):
        scolarite.radier(self.ecole, self.eleves[0], motif="Départ")
        reponse = self._reinscrire(self.eleves[0])
        self.assertEqual(reponse.status_code, 400, reponse.content)

    def test_un_eleve_sans_passe_est_renvoye_vers_linscription(self):
        nouveau = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-NEW", nom="Neuf", prenom="Tout")
        reponse = self._reinscrire(nouveau)
        self.assertEqual(reponse.status_code, 400, reponse.content)
        self.assertIn("Inscrire un élève", str(reponse.data))

    def test_une_classe_dune_autre_ecole_est_refusee(self):
        autre_annee = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        classe = models.Classe.objects.create(
            id_ecole=self.autre, id_annee_scolaire=autre_annee,
            id_niveau=models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème"),
            nom="6ème Z", capacite_max=30)
        reponse = self._reinscrire(self.eleves[0], classe=classe)
        self.assertEqual(reponse.status_code, 400, reponse.content)

    def test_un_eleve_dune_autre_ecole_est_introuvable(self):
        reponse = self.client_autre.post(
            f"/api/eleves/{self.eleves[0].pk}/reinscrire/",
            {"id_classe": self.classe_cible.pk}, format="json")
        self.assertEqual(reponse.status_code, 404)

    def test_seul_un_admin_reinscrit(self):
        client = self._client(self.parent_user.email)
        reponse = client.post(f"/api/eleves/{self.eleves[0].pk}/reinscrire/",
                              {"id_classe": self.classe_cible.pk}, format="json")
        self.assertEqual(reponse.status_code, 403)


class TestFilePaginee(BaseRentree):
    """502 lignes ne se traitent pas d'un écran : la file se parcourt par pages."""

    def setUp(self):
        super().setUp()
        for i in range(4, 12):
            eleve = models.Eleve.objects.create(
                id_ecole=self.ecole, matricule=f"E-{i}", nom=f"Nom{i}", prenom="Test")
            models.Inscription.objects.create(
                id_eleve=eleve, id_classe=self.classe_passee,
                id_annee_scolaire=self.annee_passee,
                date_inscription=self.annee_passee.date_debut,
                statut=models.Inscription.Statut.EN_COURS)

    def _page(self, **params):
        params.setdefault("id_annee_scolaire", self.annee.pk)
        return self.client.get("/api/eleves/a-reinscrire/", params)

    def test_la_premiere_page_ne_rend_que_sa_tranche(self):
        reponse = self._page(page_size=4)
        self.assertEqual(reponse.data["total"], 11)
        self.assertEqual(len(reponse.data["eleves"]), 4)
        self.assertEqual(reponse.data["pages"], 3)
        self.assertTrue(reponse.data["suivant"])
        self.assertFalse(reponse.data["precedent"])

    def test_les_pages_ne_se_recouvrent_pas(self):
        page1 = {l["id"] for l in self._page(page_size=4, page=1).data["eleves"]}
        page2 = {l["id"] for l in self._page(page_size=4, page=2).data["eleves"]}
        self.assertEqual(len(page1 & page2), 0)
        self.assertEqual(len(page1 | page2), 8)

    def test_la_derniere_page_annonce_la_fin(self):
        reponse = self._page(page_size=4, page=3)
        self.assertEqual(len(reponse.data["eleves"]), 3)
        self.assertFalse(reponse.data["suivant"])
        self.assertTrue(reponse.data["precedent"])

    def test_page_illisible_refusee(self):
        self.assertEqual(self._page(page="deux").status_code, 400)
        self.assertEqual(self._page(page=0).status_code, 400)

    def test_le_filtre_reduit_le_total_pas_seulement_la_page(self):
        reponse = self._page(q="Nom5")
        self.assertEqual(reponse.data["total"], 1)
        self.assertEqual(reponse.data["pages"], 1)


class TestContexteReinscription(BaseRentree):
    """GET /api/eleves/{id}/reinscription/ — ce qui pré-remplit l'écran."""

    def _contexte(self, eleve, client=None):
        return (client or self.client).get(
            f"/api/eleves/{eleve.pk}/reinscription/",
            {"id_annee_scolaire": self.annee.pk})

    def test_il_donne_la_provenance_et_les_classes_possibles(self):
        reponse = self._contexte(self.eleves[0])
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual(reponse.data["eleve"]["nom_complet"], "Test Nom1")
        self.assertEqual(reponse.data["precedente"]["classe"], "5ème A")
        self.assertEqual(reponse.data["precedente"]["annee"], "2025-2026")
        self.assertEqual(reponse.data["annee_cible"]["libelle"], "2026-2027")
        self.assertIn(self.classe_cible.pk, [c["id"] for c in reponse.data["classes"]])
        self.assertIsNone(reponse.data["deja_inscrit"])

    def test_il_annonce_un_eleve_deja_inscrit(self):
        self.client.post(f"/api/eleves/{self.eleves[0].pk}/reinscrire/",
                         {"id_classe": self.classe_cible.pk}, format="json")
        reponse = self._contexte(self.eleves[0])
        self.assertEqual(reponse.data["deja_inscrit"]["classe"], "6ème C")
        self.assertEqual(reponse.data["deja_inscrit"]["statut"], "en_attente")

    def test_les_classes_proposees_sont_celles_de_lannee_cible(self):
        reponse = self._contexte(self.eleves[0])
        noms = [c["nom"] for c in reponse.data["classes"]]
        self.assertNotIn("5ème A", noms, "la classe de l'an passé n'est pas une cible")

    def test_un_eleve_dune_autre_ecole_est_introuvable(self):
        reponse = self._contexte(self.eleves[0], client=self.client_autre)
        self.assertEqual(reponse.status_code, 404)

    def test_seul_un_admin_y_accede(self):
        reponse = self._contexte(self.eleves[0],
                                 client=self._client(self.parent_user.email))
        self.assertEqual(reponse.status_code, 403)


class TestEtatInscriptionDansLaListe(BaseRentree):
    """La colonne « statut » disait « actif » d'un élève qui n'avait rien payé."""

    def test_un_pre_inscrit_est_annonce_comme_tel(self):
        self.client.post(f"/api/eleves/{self.eleves[0].pk}/reinscrire/",
                         {"id_classe": self.classe_cible.pk}, format="json")
        reponse = self.client.get("/api/eleves/", {
            "id_annee_scolaire": self.annee.pk, "inscription": "en_attente"})
        ligne = self.resultats(reponse)[0]
        self.assertEqual(ligne["statut"], "actif")
        self.assertEqual(ligne["inscription_statut"], "en_attente")
        self.assertEqual(ligne["inscription_libelle"], "Inscrit incomplet")
        self.assertEqual(ligne["classe"], "6ème C")

    def test_un_inscrit_confirme_le_dit_aussi(self):
        self.client.post(f"/api/eleves/{self.eleves[0].pk}/reinscrire/",
                         {"id_classe": self.classe_cible.pk}, format="json")
        droits = self._droits(self.eleves[0])
        self._payer(droits, 50000)
        self._payer(self._premier_mois(self.eleves[0]), 25000)
        reponse = self.client.get("/api/eleves/", {"id_annee_scolaire": self.annee.pk})
        ligne = [l for l in self.resultats(reponse) if l["id"] == self.eleves[0].pk][0]
        self.assertEqual(ligne["inscription_statut"], "en_cours")
        self.assertEqual(ligne["inscription_libelle"], "Inscrit")

    def test_letat_suit_lannee_demandee(self):
        """Sur l'année passée, le même élève est un inscrit confirmé."""
        reponse = self.client.get("/api/eleves/",
                                  {"id_annee_scolaire": self.annee_passee.pk})
        ligne = [l for l in self.resultats(reponse) if l["id"] == self.eleves[0].pk][0]
        self.assertEqual(ligne["inscription_statut"], "en_cours")
        self.assertEqual(ligne["classe"], "5ème A")


class TestStatutSansFrais(BaseDossierFinancier):
    """« Rien n'a été facturé » ne doit pas se lire « tout est réglé »."""

    def test_un_eleve_sans_frais_nest_pas_a_jour(self):
        resume = self._situation()["resume"]
        self.assertEqual(resume["total_du"], 0)
        self.assertEqual(resume["statut"], "sans_frais")
        self.assertEqual(resume["statut_libelle"], "Aucun frais")

    def test_il_devient_non_paye_des_que_les_frais_sont_emis(self):
        self._frais(25000, date(2026, 11, 5))
        self.assertEqual(self._situation()["resume"]["statut"], "non_paye")

    def test_puis_a_jour_une_fois_regle(self):
        self._droits_exoneres()
        frais = self._frais(25000, date(2026, 11, 5))
        self._payer(frais, 25000)
        self.assertEqual(self._situation()["resume"]["statut"], "a_jour")

    def test_la_vue_densemble_ne_le_compte_pas_comme_a_jour(self):
        reponse = self.client.get("/api/frais-eleves/situation/",
                                  {"id_annee_scolaire": self.annee.pk})
        par_eleve = {l["id_eleve"]: l for l in reponse.data["eleves"]}
        self.assertEqual(par_eleve[self.eleve.pk]["statut"], "sans_frais")
        self.assertEqual(reponse.data["repartition"]["a_jour"], 0)
        self.assertEqual(reponse.data["repartition"]["sans_frais"], 3)


class TestAJourExigeTousLesFrais(BaseDossierFinancier):
    """
    « À jour », c'est avoir réglé tous les frais qui reviennent à l'élève :
    une facture à laquelle manque un frais dû d'office ne suffit pas.
    """

    def test_droits_regles_sans_mensualite_emise(self):
        """Le cas relevé au test : droits payés, aucune mensualité émise."""
        self._payer(self._frais(30000, date(2026, 10, 20), self.inscription_frais), 30000)
        resume = self._situation()["resume"]
        self.assertEqual(resume["reste"], 0)
        self.assertEqual(resume["statut"], "partiel")
        self.assertEqual(resume["frais_manquants"], ["Mensualité"])

    def test_mensualite_reglee_sans_droits_emis(self):
        self._payer(self._frais(25000, date(2026, 10, 5)), 25000)
        resume = self._situation()["resume"]
        self.assertEqual(resume["statut"], "partiel")
        self.assertEqual(resume["frais_manquants"], ["Inscription"])

    def test_a_jour_une_fois_tout_emis_et_regle(self):
        self._payer(self._frais(50000, date(2026, 10, 5), self.inscription_frais), 50000)
        self._payer(self._frais(25000, date(2026, 10, 5)), 25000)
        resume = self._situation()["resume"]
        self.assertEqual(resume["statut"], "a_jour")
        self.assertEqual(resume["frais_manquants"], [])

    def test_la_vue_densemble_et_le_bilan_suivent(self):
        self._payer(self._frais(30000, date(2026, 10, 20), self.inscription_frais), 30000)
        reponse = self.client.get("/api/frais-eleves/situation/",
                                  {"id_annee_scolaire": self.annee.pk})
        ligne = [l for l in reponse.data["eleves"] if l["id_eleve"] == self.eleve.pk][0]
        self.assertEqual(ligne["statut"], "partiel")
        self.assertEqual(ligne["frais_manquants"], ["Mensualité"])
        self.assertEqual(reponse.data["repartition"]["a_jour"], 0)
        self.assertEqual(finances.bilan(self.ecole, self.annee)["eleves"]["a_jour"], 0)


class TestEncaissementAnnonceLInscription(BaseDossierFinancier):
    """
    L'encaissement dit quand il fait d'un inscrit incomplet un inscrit : c'est
    ce que l'écran annonce (« … est maintenant inscrit »).
    """

    def setUp(self):
        super().setUp()
        self.inscription = models.Inscription.objects.get(
            id_eleve=self.eleve, id_annee_scolaire=self.annee)
        self.inscription.statut = models.Inscription.Statut.EN_ATTENTE
        self.inscription.save()
        self._frais(50000, date(2026, 10, 1), self.inscription_frais)
        self._frais(25000, date(2026, 10, 5))

    def _encaisser(self, montant):
        return self.client.post("/api/paiements/encaisser/", {
            "id_eleve": self.eleve.pk, "id_annee_scolaire": self.annee.pk,
            "montant": montant, "methode": "especes",
        }, format="json")

    def test_droits_et_premier_mois_regles_annoncent_linscription(self):
        reponse = self._encaisser("75000")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["inscription_confirmee"], {
            "eleve": f"{self.eleve.prenom} {self.eleve.nom}",
            "classe": self.classe.nom,
            "annee": self.annee.libelle,
        })

    def test_un_versement_partiel_nannonce_rien(self):
        reponse = self._encaisser("50000")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertIsNone(reponse.data["inscription_confirmee"])

    def test_un_eleve_deja_inscrit_nest_pas_annonce_deux_fois(self):
        self._encaisser("75000")
        self._frais(25000, date(2026, 11, 5))
        reponse = self._encaisser("25000")
        self.assertIsNone(reponse.data["inscription_confirmee"])


class TestNomsDesFichiersTelecharges(BaseDossierFinancier):
    """
    Le nom d'un fichier téléchargé doit dire ce qu'il contient : un dossier de
    téléchargements plein d'« export.pdf » ne sert personne.
    """

    def test_les_etats_financiers_portent_lecole_et_le_jour(self):
        jour = date.today().isoformat()
        cas = [
            (finances_documents.impayes(self.ecole, models.FraisEleve.objects.none(),
                                        format="xlsx"),
             f"etat-des-impayes-ecole-a-{jour}.xlsx"),
            (finances_documents.impayes(self.ecole, models.FraisEleve.objects.none(),
                                        format="pdf"),
             f"etat-des-impayes-ecole-a-{jour}.pdf"),
            (finances_documents.journal(self.ecole, [], format="pdf", titre="Journal de banque"),
             f"journal-de-banque-ecole-a-{jour}.pdf"),
            (finances_documents.depenses(self.ecole, [], format="xlsx"),
             f"depenses-ecole-a-{jour}.xlsx"),
            (finances_documents.paiements(self.ecole, [], format="xlsx"),
             f"journal-des-recettes-ecole-a-{jour}.xlsx"),
        ]
        for (_, nom, _mime), attendu in cas:
            with self.subTest(attendu=attendu):
                self.assertEqual(nom, attendu)

    def test_le_bilan_nomme_sa_periode(self):
        donnees = finances.bilan(self.ecole, self.annee)
        _, nom, _ = finances_documents.bilan(self.ecole, donnees, format="xlsx")
        self.assertEqual(nom, "bilan-financier-ecole-a-2026-2027.xlsx")

    def test_les_documents_pedagogiques_nomment_leleve_la_classe_et_la_periode(self):
        from . import pedagogie_documents

        releve = {
            "eleve": {"prenom": "Ndalé", "nom": "FÉDIOR", "matricule": "E-1"},
            "classe": "6ème A", "periode": "Trimestre 1", "matieres": [],
            "moyenne_generale": None, "rang_classe": None, "effectif": 0,
            "appreciation": None,
        }
        _, nom, _ = pedagogie_documents.bulletin(self.ecole, releve)
        self.assertEqual(nom, "bulletin-ndale-fedior-6eme-a-trimestre-1.pdf")

        _, nom, _ = pedagogie_documents.releve_de_notes(self.ecole, releve)
        self.assertEqual(nom, "releve-de-notes-ndale-fedior-6eme-a-trimestre-1.pdf")

        donnees = {"classe": "6ème A", "periode": "Trimestre 1", "lignes": []}
        _, nom, _ = pedagogie_documents.palmares(self.ecole, donnees, format="pdf")
        self.assertEqual(nom, "palmares-6eme-a-trimestre-1-ecole-a.pdf")

        _, nom, _ = pedagogie_documents.moyennes_classe(self.ecole, donnees, format="xlsx")
        self.assertEqual(nom, "moyennes-6eme-a-trimestre-1-ecole-a.xlsx")

    def test_un_nom_de_fichier_reste_sans_accent_ni_espace(self):
        self.ecole.nom = "École Mixité Cité"
        _, nom, _ = finances_documents.depenses(self.ecole, [], format="pdf")
        self.assertTrue(nom.startswith("depenses-ecole-mixite-cite-"), nom)
        self.assertTrue(nom.isascii(), nom)
        self.assertNotIn(" ", nom)


class TestAutorisationEnClasse(BaseDossierFinancier):
    """
    L'exception de l'administration : un inscrit incomplet, droits
    d'inscription réglés, est autorisé à suivre les cours — sans devenir
    « Inscrit » pour autant.
    """

    def setUp(self):
        super().setUp()
        self.inscription = models.Inscription.objects.get(
            id_eleve=self.eleve, id_annee_scolaire=self.annee)
        self.droits = self._frais(50000, date(2026, 10, 1), self.inscription_frais)
        self.premier_mois = self._frais(25000, date(2026, 10, 5))

    def _autoriser(self, client=None, motif="Difficulté financière passagère"):
        return (client or self.client).post(
            f"/api/eleves/{self.eleve.pk}/autoriser-en-classe/", {"motif": motif}, format="json")

    def _en_classe(self, **params):
        reponse = self.client.get("/api/eleves/", {"id_annee_scolaire": self.annee.pk, **params})
        return [e["id"] for e in self.resultats(reponse)]

    def test_refusee_tant_que_les_droits_ne_sont_pas_regles(self):
        self.assertEqual(self._autoriser().status_code, 400)
        self.inscription.refresh_from_db()
        self.assertFalse(self.inscription.autorise_en_classe)

    def test_lautorisation_laisse_linscription_incomplete(self):
        self._payer(self.droits, 50000)
        self.assertNotIn(self.eleve.pk, self._en_classe())

        reponse = self._autoriser()
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.inscription.refresh_from_db()
        self.assertEqual(self.inscription.statut, "en_attente")
        self.assertTrue(self.inscription.autorise_en_classe)
        self.assertEqual(self.inscription.id_utilisateur_autorisation, self.admin)

        courante = reponse.data["scolarite_courante"]
        self.assertTrue(courante["autorise_en_classe"])
        self.assertEqual(courante["motif_autorisation"], "Difficulté financière passagère")
        self.assertEqual(courante["statut_code"], "en_attente")
        # En classe, et toujours parmi les inscrits incomplets.
        self.assertIn(self.eleve.pk, self._en_classe())
        self.assertIn(self.eleve.pk, self._en_classe(inscription="en_attente"))
        ligne = [e for e in self.resultats(self.client.get(
            "/api/eleves/", {"id_annee_scolaire": self.annee.pk})) if e["id"] == self.eleve.pk][0]
        self.assertTrue(ligne["inscription_autorise_en_classe"])

    def test_le_retrait_sort_leleve_de_la_classe(self):
        self._payer(self.droits, 50000)
        self._autoriser()
        reponse = self.client.post(f"/api/eleves/{self.eleve.pk}/retirer-autorisation/", {},
                                   format="json")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertNotIn(self.eleve.pk, self._en_classe())

    def test_le_premier_mois_regle_confirme_toujours(self):
        self._payer(self.droits, 50000)
        self._autoriser()
        self._payer(self.premier_mois, 25000)
        self.inscription.refresh_from_db()
        self.assertEqual(self.inscription.statut, "en_cours")

    def test_inutile_pour_un_inscrit(self):
        self.inscription.statut = "en_cours"
        self.inscription.save()
        self.assertEqual(self._autoriser().status_code, 400)

    def test_un_motif_est_exige(self):
        self._payer(self.droits, 50000)
        self.assertEqual(self._autoriser(motif="").status_code, 400)

    def test_reservee_aux_profils_qui_inscrivent(self):
        self._payer(self.droits, 50000)
        self.assertEqual(self._autoriser(self._client(self.enseignant_user.email)).status_code, 403)
        self.assertEqual(self._autoriser(self._client(self.parent_user.email)).status_code, 403)

    def test_une_autre_ecole_ne_peut_pas_autoriser(self):
        self._payer(self.droits, 50000)
        self.assertEqual(self._autoriser(self.client_autre).status_code, 404)


class TestAjoutDeFraisEnCoursDAnnee(BaseDossierFinancier):
    """L'élève inscrit qui s'ajoute un frais au choix en cours d'année."""

    def setUp(self):
        super().setUp()
        self.taekwondo = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Taekwondo", nature="autre",
            montant_defaut=5000, frequence="Mensuelle")
        self.uniforme = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Uniforme", nature="autre",
            montant_defaut=15000, frequence="Unique")

    def _ajouter(self, frais, date_debut="2027-01-10", client=None):
        return (client or self.client).post(
            f"/api/eleves/{self.eleve.pk}/ajouter-frais/",
            {"frais": frais, "date_debut": date_debut}, format="json")

    def _lignes(self, type_frais):
        return list(models.FraisEleve.objects
                    .filter(id_eleve=self.eleve, id_type_frais=type_frais)
                    .order_by("echeance"))

    def test_les_frais_proposes_suivent_le_niveau(self):
        models.TarifNiveau.objects.create(id_niveau=self.niveau, id_type_frais=self.taekwondo,
                                          montant=7000)
        reponse = self.client.get(f"/api/eleves/{self.eleve.pk}/frais-disponibles/")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        par_id = {t["id"]: t for t in reponse.data["types"]}
        self.assertEqual(par_id[self.taekwondo.pk]["montant"], 7000)
        self.assertTrue(par_id[self.taekwondo.pk]["tarif_niveau"])
        self.assertTrue(par_id[self.taekwondo.pk]["mensuel"])
        self.assertFalse(par_id[self.uniforme.pk]["mensuel"])
        # La mensualité est due d'office : elle ne se propose pas.
        self.assertNotIn(self.type_frais.pk, par_id)

    def test_un_frais_mensuel_court_jusqua_la_fin_de_lannee(self):
        reponse = self._ajouter([self.taekwondo.pk])
        self.assertEqual(reponse.status_code, 201, reponse.content)
        lignes = self._lignes(self.taekwondo)
        self.assertEqual(len(lignes), 7)
        self.assertEqual(lignes[0].echeance, date(2027, 1, 10))
        self.assertEqual(lignes[-1].echeance, date(2027, 7, 10))
        self.assertEqual(reponse.data["crees"], 7)
        self.assertEqual(reponse.data["total"], 35000)
        self.assertIn("situation", reponse.data)

    def test_un_frais_unique_est_emis_une_fois(self):
        self.assertEqual(self._ajouter([self.uniforme.pk]).status_code, 201)
        lignes = self._lignes(self.uniforme)
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0].montant_du, Decimal("15000"))

    def test_un_frais_deja_souscrit_nest_pas_double(self):
        self._ajouter([self.uniforme.pk])
        reponse = self._ajouter([self.uniforme.pk])
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(len(self._lignes(self.uniforme)), 1)
        disponibles = self.client.get(f"/api/eleves/{self.eleve.pk}/frais-disponibles/").data
        self.assertTrue([t for t in disponibles["types"] if t["id"] == self.uniforme.pk][0]["deja"])

    def test_un_frais_du_doffice_est_refuse(self):
        self.assertEqual(self._ajouter([self.type_frais.pk]).status_code, 400)

    def test_un_frais_dune_autre_ecole_est_refuse(self):
        piscine = models.TypeFrais.objects.create(
            id_ecole=self.autre, libelle="Piscine", nature="autre", montant_defaut=3000)
        self.assertEqual(self._ajouter([piscine.pk]).status_code, 400)
        self.assertFalse(models.FraisEleve.objects.filter(id_type_frais=piscine).exists())

    def test_une_autre_ecole_ne_peut_pas_ajouter(self):
        self.assertEqual(self._ajouter([self.uniforme.pk], client=self.client_autre).status_code, 404)

    def test_un_eleve_sans_inscription_est_refuse(self):
        models.Inscription.objects.filter(id_eleve=self.eleve).delete()
        self.assertEqual(self._ajouter([self.uniforme.pk]).status_code, 400)


class TestHistoriqueCaisse(BaseDossierFinancier):
    """Paiements et dépenses d'une journée, rattachés à leur caissier."""

    def setUp(self):
        super().setUp()
        self.caissier = self._compte("caisse@a.sn", self.ecole, "caissier")
        self.client_caissier = self._client(self.caissier.email)
        midi = datetime(2026, 10, 10, 12, 0, tzinfo=dt_timezone.utc)

        # Encaissé par l'administrateur, en espèces.
        self._payer(self._frais(25000, date(2026, 10, 5)), 25000, date_paiement=midi)
        # Encaissé par le caissier, en Wave.
        frais = self._frais(50000, date(2026, 10, 1), self.inscription_frais, eleve=self.eleves[1])
        paiement = models.Paiement.objects.create(
            id_frais_eleve=frais, montant=Decimal("20000"), methode="wave",
            date_paiement=midi, id_utilisateur_caissier=self.caissier)
        finances.enregistrer_paiement(paiement, utilisateur=self.caissier)
        # Dépense saisie par l'administrateur.
        reponse = self.client.post("/api/depenses/", {
            "id_type_depense": self.type_depense.pk, "motif": "Craies",
            "montant": "3000", "date_depense": "2026-10-10",
        }, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)

    def _historique(self, client=None, **params):
        params.setdefault("date", "2026-10-10")
        return (client or self.client).get("/api/caisse/historique/", params)

    def test_ladministrateur_voit_toute_la_journee(self):
        reponse = self._historique()
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual(len(reponse.data["paiements"]), 2)
        self.assertEqual(len(reponse.data["depenses"]), 1)
        totaux = reponse.data["totaux"]
        self.assertEqual(totaux["encaisse"], 45000)
        self.assertEqual(totaux["depense"], 3000)
        self.assertEqual(totaux["solde"], 42000)
        self.assertEqual(totaux["especes_en_caisse"], 22000)
        par_caissier = {c["nom"]: c for c in reponse.data["par_caissier"]}
        self.assertEqual(par_caissier["Caissier Test"]["encaisse"], 20000)
        self.assertEqual(par_caissier["Admin Test"]["encaisse"], 25000)
        self.assertEqual(par_caissier["Admin Test"]["depense"], 3000)
        self.assertEqual(reponse.data["depenses"][0]["caissier"], "Admin Test")
        self.assertTrue(reponse.data["paiements"][0]["numero_recu"])

    def test_filtre_sur_un_caissier(self):
        reponse = self._historique(caissier=self.caissier.pk)
        self.assertEqual([p["caissier"] for p in reponse.data["paiements"]], ["Caissier Test"])
        self.assertEqual(reponse.data["depenses"], [])

    def test_un_caissier_ne_voit_que_ses_operations(self):
        reponse = self._historique(self.client_caissier, caissier=self.admin.pk)
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual([p["caissier"] for p in reponse.data["paiements"]], ["Caissier Test"])
        self.assertEqual(reponse.data["depenses"], [])

    def test_un_autre_jour_est_vide(self):
        reponse = self._historique(date="2026-10-11")
        self.assertEqual(reponse.data["paiements"], [])
        self.assertEqual(reponse.data["totaux"]["encaisse"], 0)

    def test_isolation_entre_ecoles(self):
        reponse = self._historique(self.client_autre)
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual(reponse.data["paiements"], [])
        self.assertEqual(reponse.data["depenses"], [])
        self.assertEqual(self._historique(self.client_autre, caissier=self.caissier.pk).status_code, 400)

    def test_reserve_a_la_caisse(self):
        enseignant = self._client(self.enseignant_user.email)
        self.assertEqual(self._historique(enseignant).status_code, 403)

    def test_la_depense_retient_qui_la_saisit(self):
        depense = models.Depense.objects.get(motif="Craies")
        self.assertEqual(depense.id_utilisateur, self.admin)

    def test_date_illisible(self):
        self.assertEqual(self._historique(date="10/10/2026").status_code, 400)


class TestClotureDeCaisse(BaseDossierFinancier):
    """L'arrêté du soir : des totaux figés, datés et signés."""

    def setUp(self):
        super().setUp()
        self.jour = "2026-10-10"
        midi = datetime(2026, 10, 10, 12, 0, tzinfo=dt_timezone.utc)
        self._payer(self._frais(25000, date(2026, 10, 5)), 25000, date_paiement=midi)
        self.client.post("/api/depenses/", {
            "id_type_depense": self.type_depense.pk, "motif": "Craies",
            "montant": "3000", "date_depense": self.jour,
        }, format="json")

    def _cloturer(self, client=None, **corps):
        corps.setdefault("date", self.jour)
        return (client or self.client).post("/api/caisse/cloturer/", corps, format="json")

    def _historique(self, client=None):
        return (client or self.client).get("/api/caisse/historique/", {"date": self.jour})

    def test_la_cloture_fige_les_totaux_et_son_auteur(self):
        reponse = self._cloturer(commentaire="Tiroir compté, rien à signaler")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        cloture = reponse.data["cloture"]
        self.assertEqual(cloture["total_encaisse"], 25000)
        self.assertEqual(cloture["total_depense"], 3000)
        self.assertEqual(cloture["especes_attendues"], 22000)
        self.assertEqual(cloture["par"], "Admin Test")
        self.assertEqual(cloture["commentaire"], "Tiroir compté, rien à signaler")
        self.assertEqual(cloture["ecart_encaisse"], 0)
        # L'historique la renvoie ensuite à chaque consultation.
        self.assertIsNotNone(self._historique().data["cloture"])

    def test_une_seconde_cloture_est_refusee(self):
        self._cloturer()
        self.assertEqual(self._cloturer().status_code, 400)
        self.assertEqual(models.ClotureCaisse.objects.count(), 1)

    def test_un_encaissement_posterieur_apparait_en_ecart(self):
        self._cloturer()
        self._payer(self._frais(10000, date(2026, 10, 5)), 10000,
                    date_paiement=datetime(2026, 10, 10, 18, 0, tzinfo=dt_timezone.utc))
        cloture = self._historique().data["cloture"]
        self.assertEqual(cloture["ecart_encaisse"], 10000)
        self.assertEqual(cloture["total_encaisse"], 25000)

    def test_une_journee_sans_cloture_le_dit(self):
        self.assertIsNone(self._historique().data["cloture"])

    def test_chaque_ecole_cloture_la_sienne(self):
        self._cloturer()
        reponse = self.client_autre.post("/api/caisse/cloturer/", {"date": self.jour},
                                         format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["totaux"]["encaisse"], 0)
        self.assertEqual(models.ClotureCaisse.objects.count(), 2)

    def test_reservee_a_la_caisse(self):
        enseignant = self._client(self.enseignant_user.email)
        self.assertEqual(self._cloturer(enseignant).status_code, 403)

    def test_le_caissier_peut_cloturer(self):
        caissier = self._compte("caisse@a.sn", self.ecole, "caissier")
        reponse = self._cloturer(self._client(caissier.email))
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["cloture"]["par"], "Caissier Test")


class TestRecuDePaiement(BaseDossierFinancier):
    """Le justificatif remis à la famille : contenu et nom du fichier."""

    def setUp(self):
        super().setUp()
        models.Inscription.objects.filter(
            id_eleve=self.eleve, id_annee_scolaire=self.annee).update(
                id_classe=self.classe, statut=models.Inscription.Statut.EN_COURS)
        self._frais(50000, None, self.inscription_frais)
        for mois in (10, 11, 12):
            self._frais(25000, date(2026, mois, 5))
        self.paiement = self._payer(
            models.FraisEleve.objects.get(id_type_frais=self.inscription_frais), 50000)
        self.recu = models.Recu.objects.get(id_paiement=self.paiement)

    def _pdf(self):
        return finances_documents.recu(self.recu)

    def test_le_nom_du_fichier_identifie_la_piece(self):
        _, nom, mime = self._pdf()
        self.assertEqual(mime, "application/pdf")
        # Numéro de reçu tel qu'imprimé, puis élève, classe et année.
        self.assertTrue(nom.startswith(f"recu-{self.recu.numero_recu.lower()}-"), nom)
        for morceau in ("test", "nom1", "6eme-a", "2026-2027"):
            self.assertIn(morceau, nom)
        self.assertTrue(nom.endswith(".pdf"))

    def test_le_nom_ne_contient_ni_accent_ni_espace(self):
        self.eleve.prenom = "Ndalé"
        self.eleve.nom = "FÉDIOR"
        self.eleve.save(update_fields=["prenom", "nom"])
        _, nom, _ = self._pdf()
        self.assertIn("ndale-fedior", nom)
        self.assertTrue(nom.isascii(), nom)
        self.assertNotIn(" ", nom)

    def test_le_recu_rend_un_pdf_lisible(self):
        contenu, _, _ = self._pdf()
        self.assertTrue(contenu.startswith(b"%PDF-"))
        self.assertTrue(contenu.rstrip().endswith(b"%%EOF"))
        # Quatre frais + en-têtes + total : le reçu porte l'échéancier de
        # l'année, pas seulement la ligne réglée.
        self.assertGreater(len(contenu), 3000)

    def test_un_paiement_sans_caissier_ne_casse_pas_le_recu(self):
        paiement = models.Paiement.objects.create(
            id_frais_eleve_id=models.FraisEleve.objects.filter(
                echeance=date(2026, 10, 5)).first().pk,
            montant=Decimal("25000"), methode="especes")
        recu = finances.enregistrer_paiement(paiement)
        contenu, nom, _ = finances_documents.recu(recu)
        self.assertTrue(contenu.startswith(b"%PDF-"))
        self.assertIn(recu.numero_recu.lower(), nom)

    def test_un_eleve_sans_inscription_garde_un_recu(self):
        """Un radié en cours d'année doit pouvoir rééditer ses justificatifs."""
        models.Inscription.objects.filter(id_eleve=self.eleve).delete()
        contenu, nom, _ = self._pdf()
        self.assertTrue(contenu.startswith(b"%PDF-"))
        self.assertIn("2026-2027", nom)


class TestFiltreParClasse(BaseAPI):
    """La liste des élèves se restreint à une classe, les classes à un niveau."""

    def setUp(self):
        super().setUp()
        self.classe_b = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=40,
        )
        self.awa = self._inscrit("DIOP", self.classe)
        self.moussa = self._inscrit("FALL", self.classe_b)

    def _inscrit(self, nom, classe):
        eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule=f"M-{nom}", nom=nom, prenom="Test")
        models.Inscription.objects.create(
            id_eleve=eleve, id_classe=classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1), statut=models.Inscription.Statut.EN_COURS)
        return eleve

    def _ids(self, **params):
        reponse = self.client.get("/api/eleves/", params)
        self.assertEqual(reponse.status_code, 200, reponse.content)
        return {ligne["id"] for ligne in self.resultats(reponse)}

    def test_la_classe_restreint_la_liste(self):
        self.assertEqual(self._ids(id_annee_scolaire=self.annee.pk),
                         {self.awa.pk, self.moussa.pk})
        self.assertEqual(self._ids(id_annee_scolaire=self.annee.pk, classe=self.classe_b.pk),
                         {self.moussa.pk})

    def test_la_classe_se_cumule_avec_le_statut(self):
        self.moussa.statut = models.Eleve.Statut.RADIE
        self.moussa.save(update_fields=["statut"])
        self.assertEqual(self._ids(id_annee_scolaire=self.annee.pk,
                                   classe=self.classe_b.pk, statut="radie"), {self.moussa.pk})
        self.assertEqual(self._ids(id_annee_scolaire=self.annee.pk,
                                   classe=self.classe.pk, statut="radie"), set())

    def test_la_classe_dune_autre_ecole_est_refusee(self):
        annee = models.AnneeScolaire.objects.create(
            id_ecole=self.autre, libelle="2026-2027",
            date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31))
        niveau = models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème")
        classe = models.Classe.objects.create(
            id_ecole=self.autre, id_annee_scolaire=annee, id_niveau=niveau,
            nom="6ème A", capacite_max=40)
        reponse = self.client.get("/api/eleves/", {"classe": classe.pk})
        self.assertEqual(reponse.status_code, 400)

    def test_les_classes_se_filtrent_par_niveau(self):
        cinquieme = models.Niveau.objects.create(id_ecole=self.ecole, libelle="5ème")
        models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=cinquieme,
            nom="5ème A", capacite_max=40)
        reponse = self.client.get("/api/classes/", {"id_niveau": cinquieme.pk})
        self.assertEqual([c["nom"] for c in self.resultats(reponse)], ["5ème A"])

    def test_une_liste_deroulante_obtient_toutes_les_classes(self):
        for numero in range(25):
            models.Classe.objects.create(
                id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
                nom=f"6ème {numero:02d}", capacite_max=40)
        self.assertEqual(len(self.resultats(self.client.get("/api/classes/"))), 20)
        reponse = self.client.get("/api/classes/", {"page_size": 200})
        self.assertEqual(len(self.resultats(reponse)), 27)


class TestCorbeille(BaseAPI):
    """Une suppression se défait, avec tout ce qu'elle avait emporté."""

    def setUp(self):
        super().setUp()
        self.eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="M-001", nom="Diop", prenom="Awa")
        models.Inscription.objects.create(
            id_eleve=self.eleve, id_classe=self.classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1), statut=models.Inscription.Statut.EN_COURS)
        scolarite.recaler_effectif(self.classe)
        self.periode = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=self.annee, libelle="Trimestre 1",
            date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20))
        self.evaluation = models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe, id_periode=self.periode,
            type="devoir", date_evaluation=date(2026, 11, 2))
        self.note = models.Note.objects.create(
            id_eleve=self.eleve, id_evaluation=self.evaluation,
            id_utilisateur=self.enseignant_user, valeur=Decimal("14"))
        self.salle = models.Salle.objects.create(id_ecole=self.ecole, nom="Salle 1")

    def _supprimer(self, client, url):
        reponse = client.delete(url)
        self.assertEqual(reponse.status_code, 204, reponse.content)

    def _restaurer(self, client, entree):
        return client.post(f"/api/corbeille/{entree.pk}/restaurer/")

    def test_supprimer_place_lelement_et_sa_cascade_dans_la_corbeille(self):
        self._supprimer(self.client, f"/api/classes/{self.classe.pk}/")
        self.assertFalse(models.Classe.objects.filter(pk=self.classe.pk).exists())
        self.assertFalse(models.Note.objects.filter(pk=self.note.pk).exists())

        entree = models.Corbeille.objects.get(entite="classe", id_entite=self.classe.pk)
        self.assertEqual(entree.id_ecole_id, self.ecole.pk)
        self.assertEqual(entree.id_utilisateur_id, self.admin.pk)
        self.assertEqual(entree.resume.get("note"), 1)
        self.assertEqual(entree.resume.get("inscription"), 1)

        ligne = self.resultats(self.client.get("/api/corbeille/"))[0]
        self.assertEqual(ligne["libelle"], "6ème A")
        self.assertIn("1 note", ligne["emporte"])
        # L'instantané contient des lignes brutes : il ne sort pas par l'API.
        self.assertNotIn("donnees", ligne)

    def test_restaurer_remet_tout_en_place(self):
        historique = models.HistoriqueScolaire.objects.get(id_eleve=self.eleve)
        self._supprimer(self.client, f"/api/classes/{self.classe.pk}/")
        historique.refresh_from_db()
        self.assertIsNone(historique.id_nouvelle_classe_id)

        entree = models.Corbeille.objects.get(entite="classe")
        reponse = self._restaurer(self.client, entree)
        self.assertEqual(reponse.status_code, 200, reponse.content)

        classe = models.Classe.objects.get(pk=self.classe.pk)
        self.assertEqual(classe.effectif, 1)
        self.assertEqual(models.Note.objects.get(pk=self.note.pk).valeur, Decimal("14"))
        historique.refresh_from_db()
        self.assertEqual(historique.id_nouvelle_classe_id, self.classe.pk)
        self.assertFalse(models.Corbeille.objects.filter(pk=entree.pk).exists())
        self.assertTrue(models.JournalActivite.objects.filter(
            action="restauration", entite="classe", id_entite=self.classe.pk).exists())
        # Une entrée restaurée ne se restaure pas deux fois.
        self.assertEqual(self._restaurer(self.client, entree).status_code, 404)

    def test_un_element_sans_son_parent_ne_revient_pas(self):
        self._supprimer(self.client, f"/api/notes/{self.note.pk}/")
        self._supprimer(self.client, f"/api/evaluations/{self.evaluation.pk}/")
        note = models.Corbeille.objects.get(entite="note")
        evaluation = models.Corbeille.objects.get(entite="evaluation")

        reponse = self._restaurer(self.client, note)
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("dépend", reponse.data["detail"])
        self.assertFalse(models.Note.objects.filter(pk=self.note.pk).exists())
        self.assertTrue(models.Corbeille.objects.filter(pk=note.pk).exists())

        self.assertEqual(self._restaurer(self.client, evaluation).status_code, 200)
        self.assertEqual(self._restaurer(self.client, note).status_code, 200)
        self.assertTrue(models.Note.objects.filter(pk=self.note.pk).exists())

    def test_les_references_mises_a_null_sont_retablies(self):
        self.classe.id_enseignant_principal = self.enseignant
        self.classe.save(update_fields=["id_enseignant_principal"])
        self._supprimer(self.client, f"/api/enseignants/{self.enseignant.pk}/")
        self.classe.refresh_from_db()
        self.assertIsNone(self.classe.id_enseignant_principal_id)

        entree = models.Corbeille.objects.get(entite="enseignant")
        self.assertEqual(self._restaurer(self.client, entree).status_code, 200)
        self.classe.refresh_from_db()
        self.assertEqual(self.classe.id_enseignant_principal_id, self.enseignant.pk)

    def test_une_depense_restauree_retrouve_sa_caisse_et_son_budget(self):
        loyer = models.TypeDepense.objects.create(id_ecole=self.ecole, libelle="Loyer")
        budget = models.Budget.objects.create(
            id_type_depense=loyer, id_ecole=self.ecole, id_annee_scolaire=self.annee,
            categorie="Charges fixes", montant_prevu=Decimal("500000"))
        reponse = self.client.post("/api/depenses/", {
            "motif": "Loyer de novembre", "id_type_depense": loyer.pk,
            "montant": "150000", "date_depense": "2026-11-05",
        }, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        pk = reponse.data["id"]

        self._supprimer(self.client, f"/api/depenses/{pk}/")
        budget.refresh_from_db()
        self.assertEqual(budget.montant_realise, 0)
        self.assertFalse(models.JournalCaisse.objects.filter(id_ecole=self.ecole).exists())

        entree = models.Corbeille.objects.get(entite="depense")
        self.assertEqual(self._restaurer(self.client, entree).status_code, 200)
        budget.refresh_from_db()
        self.assertEqual(budget.montant_realise, Decimal("150000"))
        self.assertEqual(models.JournalCaisse.objects.filter(id_depense=pk).count(), 1)

    def test_une_annee_restauree_ne_fait_pas_deux_actives(self):
        self._supprimer(self.client, f"/api/annees-scolaires/{self.annee.pk}/")
        nouvelle = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2027-2028",
            date_debut=date(2027, 10, 1), date_fin=date(2028, 7, 31))

        entree = models.Corbeille.objects.get(entite="annee_scolaire")
        self.assertEqual(self._restaurer(self.client, entree).status_code, 200)
        self.assertEqual(models.AnneeScolaire.objects.get(pk=self.annee.pk).statut, "archivee")
        self.assertEqual(models.AnneeScolaire.objects.get(pk=nouvelle.pk).statut, "active")
        self.assertTrue(models.Note.objects.filter(pk=self.note.pk).exists())

    def test_une_suppression_bloquee_repond_400(self):
        """Des notes saisies par ce compte le protègent : refus lisible, pas une 500."""
        reponse = self.client.delete(f"/api/utilisateurs/{self.enseignant_user.pk}/")
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("note", reponse.data["detail"])
        self.assertTrue(Utilisateur.objects.filter(pk=self.enseignant_user.pk).exists())
        self.assertFalse(models.Corbeille.objects.exists())

    def test_la_corbeille_dune_ecole_est_invisible_pour_une_autre(self):
        self._supprimer(self.client, f"/api/salles/{self.salle.pk}/")
        entree = models.Corbeille.objects.get(entite="salle")

        self.assertEqual(self.resultats(self.client_autre.get("/api/corbeille/")), [])
        self.assertEqual(self._restaurer(self.client_autre, entree).status_code, 404)
        self.assertEqual(self.client_autre.delete(f"/api/corbeille/{entree.pk}/").status_code, 404)
        self.assertFalse(models.Salle.objects.filter(pk=self.salle.pk).exists())
        self.assertTrue(models.Corbeille.objects.filter(pk=entree.pk).exists())

    def test_un_enseignant_ne_voit_et_ne_restaure_que_ses_suppressions(self):
        # Un enseignant ne touche qu'aux notes de ses classes.
        models.AffectationEnseignant.objects.create(
            id_enseignant=self.enseignant, id_matiere=self.matiere,
            id_classe=self.classe, id_annee_scolaire=self.annee)
        prof = self._client(self.enseignant_user.email)
        self._supprimer(prof, f"/api/notes/{self.note.pk}/")
        self._supprimer(self.client, f"/api/salles/{self.salle.pk}/")
        note = models.Corbeille.objects.get(entite="note")
        salle = models.Corbeille.objects.get(entite="salle")

        self.assertEqual([l["id"] for l in self.resultats(prof.get("/api/corbeille/"))], [note.pk])
        self.assertEqual(self._restaurer(prof, salle).status_code, 404)
        self.assertEqual(prof.delete(f"/api/corbeille/{note.pk}/").status_code, 403)
        self.assertEqual(self._restaurer(prof, note).status_code, 200)

    def test_le_filtre_par_module(self):
        self._supprimer(self.client, f"/api/salles/{self.salle.pk}/")
        self._supprimer(self.client, f"/api/notes/{self.note.pk}/")
        salles = self.resultats(self.client.get("/api/corbeille/", {"endpoint": "salles"}))
        self.assertEqual([l["entite"] for l in salles], ["salle"])
        self.assertEqual(self.resultats(self.client.get("/api/corbeille/", {"endpoint": "inconnu"})), [])

    def test_la_suppression_definitive(self):
        self._supprimer(self.client, f"/api/salles/{self.salle.pk}/")
        entree = models.Corbeille.objects.get(entite="salle")
        self._supprimer(self.client, f"/api/corbeille/{entree.pk}/")
        self.assertFalse(models.Corbeille.objects.filter(pk=entree.pk).exists())
        self.assertFalse(models.Salle.objects.filter(pk=self.salle.pk).exists())
        self.assertTrue(models.JournalActivite.objects.filter(
            action="suppression", description__contains="définitive").exists())


class TestNiveauFacultatif(BaseAPI):
    """Une classe peut exister sans niveau, et aucun écran ne doit s'y casser."""

    def test_une_classe_se_cree_sans_niveau(self):
        reponse = self.client.post("/api/classes/", {
            "nom": "Classe unique", "id_annee_scolaire": self.annee.pk, "capacite_max": 30,
        }, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertIsNone(reponse.data["id_niveau"])
        self.assertIsNone(reponse.data["niveau_libelle"])

    def test_supprimer_un_niveau_garde_ses_classes(self):
        self.assertEqual(self.client.delete(f"/api/niveaux/{self.niveau.pk}/").status_code, 204)
        self.classe.refresh_from_db()
        self.assertIsNone(self.classe.id_niveau_id)

    def test_les_ecrans_de_leleve_supportent_une_classe_sans_niveau(self):
        classe = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, nom="Classe unique", capacite_max=30)
        eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="M-SN", nom="Sarr", prenom="Ndeye")
        models.Inscription.objects.create(
            id_eleve=eleve, id_classe=classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1), statut=models.Inscription.Statut.EN_COURS)
        for url in (f"/api/eleves/{eleve.pk}/finances/", f"/api/eleves/{eleve.pk}/dossier/",
                    f"/api/eleves/{eleve.pk}/reinscription/", "/api/frais-eleves/situation/"):
            reponse = self.client.get(url, {"id_annee_scolaire": self.annee.pk})
            self.assertEqual(reponse.status_code, 200, (url, reponse.content))


class TestFraisALInscription(BaseAPI):
    """La facture d'inscription : tarifs du niveau, frais cochés, une ligne chacun."""

    def setUp(self):
        super().setUp()
        Nature = models.TypeFrais.Nature
        self.droits = self._type("Inscription", 50000, nature=Nature.INSCRIPTION)
        self.mensualite = self._type("Mensualité", 25000, "Mensuelle", nature=Nature.MENSUALITE)
        self.transport = self._type("Transport", 15000, "Mensuelle")
        self.uniforme = self._type("Uniforme", 20000, "Une fois")
        models.TarifNiveau.objects.create(
            id_niveau=self.niveau, id_type_frais=self.droits, montant=Decimal("30000"))
        models.TarifNiveau.objects.create(
            id_niveau=self.niveau, id_type_frais=self.mensualite, montant=Decimal("20000"))

    def _type(self, libelle, montant, frequence=None, ecole=None, nature="autre"):
        return models.TypeFrais.objects.create(
            id_ecole=ecole or self.ecole, libelle=libelle, nature=nature,
            montant_defaut=Decimal(montant), frequence=frequence)

    def _inscrire(self, date_inscription="2026-10-01", **extra):
        return self.client.post("/api/eleves/inscrire/", {
            "id_ecole": self.ecole.pk, "nom": "Ndiaye", "prenom": "Fatou",
            "id_classe": self.classe.pk, "id_annee_scolaire": self.annee.pk,
            "date_inscription": date_inscription, **extra,
        }, format="json")

    def _frais(self, reponse, type_frais):
        return (models.FraisEleve.objects
                .filter(id_eleve_id=reponse.data["id"], id_type_frais=type_frais)
                .order_by("echeance"))

    def test_les_tarifs_du_niveau_sappliquent(self):
        reponse = self._inscrire()
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual([f.montant_du for f in self._frais(reponse, self.droits)], [Decimal("30000")])
        self.assertEqual([f.montant_du for f in self._frais(reponse, self.mensualite)],
                         [Decimal("20000")])
        self.assertFalse(self._frais(reponse, self.transport).exists())
        self.assertEqual(Decimal(str(reponse.data["facture"]["total_du"])), Decimal("50000"))
        self.assertEqual(reponse.data["facture"]["matricule"], reponse.data["matricule"])
        self.assertEqual(models.Inscription.objects.get(id_eleve_id=reponse.data["id"]).statut,
                         "en_attente")

    def test_sans_tarif_de_niveau_le_montant_du_type_sapplique(self):
        models.TarifNiveau.objects.all().delete()
        reponse = self._inscrire()
        self.assertEqual([f.montant_du for f in self._frais(reponse, self.droits)], [Decimal("50000")])
        self.assertEqual([f.montant_du for f in self._frais(reponse, self.mensualite)],
                         [Decimal("25000")])

    def test_les_frais_coches_sajoutent_une_fois(self):
        reponse = self._inscrire(frais_optionnels=[self.transport.pk, self.uniforme.pk])
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(self._frais(reponse, self.transport).count(), 1)
        self.assertEqual(self._frais(reponse, self.uniforme).count(), 1)
        self.assertEqual(Decimal(str(reponse.data["facture"]["total_du"])), Decimal("85000"))

    def test_un_frais_au_choix_peut_varier_selon_le_niveau(self):
        models.TarifNiveau.objects.create(
            id_niveau=self.niveau, id_type_frais=self.transport, montant=Decimal("8000"))
        reponse = self._inscrire(frais_optionnels=[self.transport.pk])
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual([f.montant_du for f in self._frais(reponse, self.transport)],
                         [Decimal("8000")])

    def test_un_frais_dune_autre_ecole_est_refuse(self):
        etranger = self._type("Transport", 10000, ecole=self.autre)
        reponse = self._inscrire(frais_optionnels=[etranger.pk])
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(models.Eleve.objects.filter(nom="Ndiaye").exists())

    def test_une_ecole_sans_aucun_tarif_inscrit_quand_meme(self):
        models.TypeFrais.objects.filter(id_ecole=self.ecole).delete()
        models.TarifNiveau.objects.all().delete()
        reponse = self._inscrire()
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertFalse(models.FraisEleve.objects.filter(id_eleve_id=reponse.data["id"]).exists())

    def test_regler_droits_et_mensualite_inscrit_leleve(self):
        reponse = self._inscrire(frais_optionnels=[self.uniforme.pk])
        id_eleve = reponse.data["id"]
        partiel = self.client.post("/api/paiements/encaisser/", {
            "id_eleve": id_eleve, "montant": "40000", "methode": "especes"}, format="json")
        self.assertEqual(partiel.status_code, 201, partiel.content)
        self.assertEqual(models.Inscription.objects.get(id_eleve_id=id_eleve).statut, "en_attente")

        # 30 000 + 20 000 atteints : inscrit, même si l'uniforme reste dû.
        complement = self.client.post("/api/paiements/encaisser/", {
            "id_eleve": id_eleve, "montant": "10000", "methode": "especes"}, format="json")
        self.assertEqual(complement.status_code, 201, complement.content)
        self.assertEqual(models.Inscription.objects.get(id_eleve_id=id_eleve).statut, "en_cours")
        self.assertEqual(Decimal(str(complement.data["situation"]["resume"]["reste"])),
                         Decimal("20000"))


class TestFraisALaReinscription(BaseAPI):
    """Réinscrire émet la facture de la nouvelle année, et reconduit les frais choisis l'an passé."""

    def setUp(self):
        super().setUp()
        Nature = models.TypeFrais.Nature
        self.droits = self._type("Inscription", 50000, nature=Nature.INSCRIPTION)
        self.mensualite = self._type("Mensualité", 25000, "Mensuelle", nature=Nature.MENSUALITE)
        self.transport = self._type("Transport", 15000, "Mensuelle")
        self.uniforme = self._type("Uniforme", 20000, "Une fois")

        self.annee_passee = models.AnneeScolaire.objects.create(
            id_ecole=self.ecole, libelle="2025-2026", statut="archivee",
            date_debut=date(2025, 10, 1), date_fin=date(2026, 7, 31))
        self.classe_passee = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee_passee, id_niveau=self.niveau,
            nom="CM2 A", capacite_max=40)
        self.eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="M-RE", nom="Ba", prenom="Omar")
        models.Inscription.objects.create(
            id_eleve=self.eleve, id_classe=self.classe_passee, id_annee_scolaire=self.annee_passee,
            date_inscription=date(2025, 10, 1), statut=models.Inscription.Statut.EN_COURS)
        # L'an passé, la famille avait pris le transport.
        for type_frais in (self.mensualite, self.transport):
            models.FraisEleve.objects.create(
                id_eleve=self.eleve, id_type_frais=type_frais, id_annee_scolaire=self.annee_passee,
                montant_du=type_frais.montant_defaut, echeance=date(2025, 10, 5))

    def _type(self, libelle, montant, frequence=None, ecole=None, nature="autre"):
        return models.TypeFrais.objects.create(
            id_ecole=ecole or self.ecole, libelle=libelle, nature=nature,
            montant_defaut=Decimal(montant), frequence=frequence)

    def _nombre(self, type_frais):
        return models.FraisEleve.objects.filter(
            id_eleve=self.eleve, id_annee_scolaire=self.annee, id_type_frais=type_frais).count()

    def test_la_reinscription_dun_eleve_reconduit_ses_frais(self):
        reponse = self.client.post(f"/api/eleves/{self.eleve.pk}/reinscrire/",
                                   {"id_classe": self.classe.pk}, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(self._nombre(self.droits), 1)
        self.assertEqual(self._nombre(self.mensualite), 1)
        self.assertEqual(self._nombre(self.transport), 1)
        self.assertEqual(self._nombre(self.uniforme), 0)
        self.assertEqual(reponse.data["frais_emis"], 3)

    def test_un_choix_explicite_remplace_la_reconduction(self):
        reponse = self.client.post(f"/api/eleves/{self.eleve.pk}/reinscrire/", {
            "id_classe": self.classe.pk, "frais_optionnels": [self.uniforme.pk],
        }, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(self._nombre(self.transport), 0)
        self.assertEqual(self._nombre(self.uniforme), 1)

    def test_les_tarifs_du_niveau_darrivee_sappliquent(self):
        models.TarifNiveau.objects.create(
            id_niveau=self.niveau, id_type_frais=self.droits, montant=Decimal("30000"))
        self.client.post(f"/api/eleves/{self.eleve.pk}/reinscrire/",
                         {"id_classe": self.classe.pk}, format="json")
        droits = models.FraisEleve.objects.get(
            id_eleve=self.eleve, id_annee_scolaire=self.annee, id_type_frais=self.droits)
        self.assertEqual(droits.montant_du, Decimal("30000"))

    def test_le_contexte_precoche_les_frais_de_lan_passe(self):
        reponse = self.client.get(f"/api/eleves/{self.eleve.pk}/reinscription/",
                                  {"id_annee_scolaire": self.annee.pk})
        self.assertEqual(reponse.status_code, 200, reponse.content)
        types = {t["id"]: t for t in reponse.data["types_frais"]}
        self.assertTrue(types[self.transport.pk]["reconduit"])
        self.assertFalse(types[self.uniforme.pk]["reconduit"])
        self.assertTrue(types[self.droits.pk]["d_office"])
        self.assertTrue(types[self.mensualite.pk]["mensuel"])

    def test_un_frais_dune_autre_ecole_annule_la_reinscription(self):
        etranger = self._type("Transport", 10000, ecole=self.autre)
        reponse = self.client.post(f"/api/eleves/{self.eleve.pk}/reinscrire/", {
            "id_classe": self.classe.pk, "frais_optionnels": [etranger.pk],
        }, format="json")
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(models.Inscription.objects.filter(
            id_eleve=self.eleve, id_annee_scolaire=self.annee).exists())

    def test_la_reinscription_dune_classe_emet_les_frais_de_chacun(self):
        corps = {"id_annee_source": self.annee_passee.pk, "id_annee_cible": self.annee.pk,
                 "id_classe_source": self.classe_passee.pk, "id_classe_cible": self.classe.pk}
        apercu = self.client.post("/api/inscriptions/reinscrire/", corps, format="json")
        self.assertEqual(apercu.status_code, 200, apercu.content)
        self.assertEqual(self._nombre(self.mensualite), 0)

        reponse = self.client.post("/api/inscriptions/reinscrire/",
                                   {**corps, "valider": True}, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(self._nombre(self.droits), 1)
        self.assertEqual(self._nombre(self.transport), 1)
        self.assertEqual(reponse.data["frais_emis"], 3)


class TestReinscriptionDeClasseSelective(BaseRentree):
    """Une classe ne revient pas entière : on coche qui la suit."""

    def _corps(self, **extra):
        return {"id_annee_source": self.annee_passee.pk, "id_annee_cible": self.annee.pk,
                "id_classe_source": self.classe_passee.pk,
                "id_classe_cible": self.classe_cible.pk, **extra}

    def test_lapercu_liste_les_eleves_a_cocher(self):
        reponse = self.client.post("/api/inscriptions/reinscrire/", self._corps(), format="json")
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual({e["id"] for e in reponse.data["eleves"]}, {e.pk for e in self.eleves})
        self.assertTrue(all(e["retenu"] for e in reponse.data["eleves"]))
        self.assertFalse(models.Inscription.objects.filter(id_annee_scolaire=self.annee).exists())

    def test_seuls_les_eleves_coches_sont_reinscrits(self):
        partant = self.eleves[2]
        reponse = self.client.post("/api/inscriptions/reinscrire/", self._corps(
            eleves=[self.eleves[0].pk, self.eleves[1].pk], valider=True), format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["reinscrits"], 2)
        self.assertEqual(reponse.data["non_retenus"], 1)
        self.assertFalse(models.Inscription.objects.filter(
            id_eleve=partant, id_annee_scolaire=self.annee).exists())
        # Le non-retenu reste dans la file : à réinscrire ailleurs ou à sortir.
        file = self.client.get("/api/eleves/a-reinscrire/", {"id_annee_scolaire": self.annee.pk})
        self.assertEqual([e["id"] for e in file.data["eleves"]], [partant.pk])

    def test_un_eleve_hors_de_la_classe_est_refuse(self):
        intrus = models.Eleve.objects.create(
            id_ecole=self.autre, matricule="X-1", nom="Intrus", prenom="Test")
        reponse = self.client.post("/api/inscriptions/reinscrire/",
                                   self._corps(eleves=[intrus.pk], valider=True), format="json")
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(models.Inscription.objects.filter(id_annee_scolaire=self.annee).exists())

    def test_rien_de_coche_rien_decrit(self):
        reponse = self.client.post("/api/inscriptions/reinscrire/",
                                   self._corps(eleves=[], valider=True), format="json")
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(models.Inscription.objects.filter(id_annee_scolaire=self.annee).exists())


class TestInscritCompletOuIncomplet(BaseRentree):
    """Inscrit = droits d'inscription et premier mois réglés ; sinon inscrit incomplet."""

    def setUp(self):
        super().setUp()
        reponse = self.client.post(f"/api/eleves/{self.eleves[0].pk}/reinscrire/",
                                   {"id_classe": self.classe_cible.pk}, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.inscription = models.Inscription.objects.get(pk=reponse.data["id_inscription"])

    def test_le_premier_mois_seul_ne_confirme_pas(self):
        self._payer(self._premier_mois(self.eleves[0]), 25000)
        self.inscription.refresh_from_db()
        self.assertEqual(self.inscription.statut, "en_attente")
        self.assertEqual(self.inscription.get_statut_display(), "Inscrit incomplet")

    def test_les_droits_seuls_ne_confirment_pas(self):
        self._payer(self._droits(self.eleves[0]), 50000)
        self.inscription.refresh_from_db()
        self.assertEqual(self.inscription.statut, "en_attente")

    def test_le_dossier_dit_ce_qui_manque(self):
        self._payer(self._droits(self.eleves[0]), 50000)
        confirmation = finances.situation_eleve(
            self.ecole, self.eleves[0], self.annee)["confirmation"]
        self.assertFalse(confirmation["confirmee"])
        self.assertEqual(Decimal(str(confirmation["droits_restants"])), 0)
        self.assertTrue(confirmation["premier_mois_emis"])
        self.assertEqual(Decimal(str(confirmation["premier_mois_restant"])), Decimal("25000"))

    def test_les_deux_regles_confirment(self):
        self._payer(self._droits(self.eleves[0]), 50000)
        self._payer(self._premier_mois(self.eleves[0]), 25000)
        self.inscription.refresh_from_db()
        self.assertEqual(self.inscription.statut, "en_cours")


class TestGuichetParMatricule(BaseAPI):
    """De la facture au reçu : la caissière part du matricule."""

    def setUp(self):
        super().setUp()
        models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Inscription", nature="inscription", montant_defaut=Decimal("50000"))
        models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Mensualité", nature="mensualite", montant_defaut=Decimal("25000"),
            frequence="Mensuelle")
        reponse = self.client.post("/api/eleves/inscrire/", {
            "id_ecole": self.ecole.pk, "nom": "Ndiaye", "prenom": "Fatou",
            "id_classe": self.classe.pk, "id_annee_scolaire": self.annee.pk,
            "date_inscription": "2026-10-01"}, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.eleve = models.Eleve.objects.get(pk=reponse.data["id"])
        self._compte("caisse@a.sn", self.ecole, "caissier")
        self.caisse = self._client("caisse@a.sn")

    def _encaisser(self, montant):
        return self.caisse.post("/api/paiements/encaisser/", {
            "id_eleve": self.eleve.pk, "montant": str(montant), "methode": "especes"},
            format="json")

    def test_le_matricule_ouvre_la_facture(self):
        reponse = self.caisse.get("/api/eleves/par-matricule/", {"matricule": self.eleve.matricule})
        self.assertEqual(reponse.status_code, 200, reponse.content)
        self.assertEqual(reponse.data["nom"], "Ndiaye")
        self.assertEqual(reponse.data["prenom"], "Fatou")
        self.assertEqual(reponse.data["classe"], "6ème A")
        self.assertEqual(Decimal(str(reponse.data["total_du"])), Decimal("75000"))
        self.assertEqual(reponse.data["statut_inscription_libelle"], "Inscrit incomplet")

    def test_un_matricule_dune_autre_ecole_est_introuvable(self):
        reponse = self.client_autre.get("/api/eleves/par-matricule/",
                                        {"matricule": self.eleve.matricule})
        self.assertEqual(reponse.status_code, 404)

    def test_la_facture_est_un_pdf(self):
        reponse = self.caisse.get(f"/api/eleves/{self.eleve.pk}/facture/")
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.content.startswith(b"%PDF-"))
        self.assertIn("facture", reponse["Content-Disposition"])

    def test_payer_emet_un_recu_et_le_seuil_inscrit(self):
        reponse = self._encaisser(60000)
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(Decimal(str(reponse.data["situation"]["resume"]["reste"])), Decimal("15000"))
        ids = ",".join(str(p["id"]) for p in reponse.data["paiements"])
        recu = self.caisse.get("/api/paiements/recu/", {"ids": ids})
        self.assertEqual(recu.status_code, 200)
        self.assertTrue(recu.content.startswith(b"%PDF-"))
        self.assertEqual(models.Inscription.objects.get(id_eleve=self.eleve).statut, "en_attente")

        self._encaisser(15000)
        self.assertEqual(models.Inscription.objects.get(id_eleve=self.eleve).statut, "en_cours")

    def test_le_recu_dune_autre_ecole_est_introuvable(self):
        ids = ",".join(str(p["id"]) for p in self._encaisser(50000).data["paiements"])
        self.assertEqual(self.client_autre.get("/api/paiements/recu/", {"ids": ids}).status_code, 404)


class TestPhotoEleve(BaseAPI):
    """La photo du dossier : déposée par un administrateur, lue derrière l'authentification."""

    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        from django.test import override_settings

        dossier = tempfile.mkdtemp()
        reglage = override_settings(MEDIA_ROOT=dossier)
        reglage.enable()
        self.addCleanup(shutil.rmtree, dossier, ignore_errors=True)
        self.addCleanup(reglage.disable)
        self.eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="M-PH", nom="Sow", prenom="Awa")
        self.url = f"/api/eleves/{self.eleve.pk}/photo/"

    def _envoyer(self, client, contenu, nom="photo.png"):
        return client.post(self.url, {"photo": SimpleUploadedFile(nom, contenu)},
                           format="multipart")

    def _lire(self, client):
        reponse = client.get(self.url)
        # Lire tout le flux suffit : le client de test ferme alors la réponse lui-même.
        # Un close() de plus relancerait la fin de requête, qui ferme la connexion
        # MySQL en pleine transaction de test et fait échouer les tests suivants.
        contenu = b"".join(reponse.streaming_content) if reponse.status_code == 200 else None
        return reponse, contenu

    def test_un_admin_depose_la_photo_et_le_dossier_la_signale(self):
        self.assertEqual(self._envoyer(self.client, self.PNG).status_code, 200)
        reponse, contenu = self._lire(self.client)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "image/png")
        self.assertEqual(contenu, self.PNG)
        dossier = self.client.get(f"/api/eleves/{self.eleve.pk}/dossier/")
        self.assertTrue(dossier.data["eleve"]["a_photo"])

    def test_remplacer_la_photo_efface_lancienne(self):
        self._envoyer(self.client, self.PNG)
        self.eleve.refresh_from_db()
        ancienne = photos.chemin(self.eleve)
        self.assertEqual(self._envoyer(self.client, self.JPEG, "photo.jpg").status_code, 200)
        self.assertFalse(ancienne.exists())
        self.assertEqual(self._lire(self.client)[0]["Content-Type"], "image/jpeg")

    def test_un_fichier_qui_nest_pas_une_image_est_refuse(self):
        reponse = self._envoyer(self.client, b"<?php echo 1; ?>", "photo.png")
        self.assertEqual(reponse.status_code, 400)
        self.eleve.refresh_from_db()
        self.assertIsNone(self.eleve.photo_url)

    def test_une_photo_trop_lourde_est_refusee(self):
        reponse = self._envoyer(self.client, self.JPEG + b"\x00" * photos.TAILLE_MAX, "photo.jpg")
        self.assertEqual(reponse.status_code, 400)

    def test_une_autre_ecole_ne_voit_ni_ne_modifie(self):
        self._envoyer(self.client, self.PNG)
        self.assertEqual(self._lire(self.client_autre)[0].status_code, 404)
        self.assertEqual(self._envoyer(self.client_autre, self.JPEG, "x.jpg").status_code, 404)

    def test_un_enseignant_voit_mais_ne_modifie_pas(self):
        self._envoyer(self.client, self.PNG)
        prof = self._client(self.enseignant_user.email)
        self.assertEqual(self._lire(prof)[0].status_code, 200)
        self.assertEqual(self._envoyer(prof, self.JPEG, "x.jpg").status_code, 403)
        self.assertEqual(prof.delete(self.url).status_code, 403)

    def test_retirer_la_photo(self):
        self._envoyer(self.client, self.PNG)
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.assertEqual(self._lire(self.client)[0].status_code, 404)


class TestEncaissementParFraisCoches(BaseAPI):
    """Le paiement ne va que sur les frais cochés."""

    def setUp(self):
        super().setUp()
        models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Inscription", nature="inscription", montant_defaut=Decimal("50000"))
        models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Mensualité", nature="mensualite", montant_defaut=Decimal("25000"),
            frequence="Mensuelle")
        self.transport = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Transport", montant_defaut=Decimal("15000"),
            frequence="Mensuelle")
        reponse = self.client.post("/api/eleves/inscrire/", {
            "id_ecole": self.ecole.pk, "nom": "Ndiaye", "prenom": "Fatou",
            "id_classe": self.classe.pk, "id_annee_scolaire": self.annee.pk,
            "date_inscription": "2026-10-01", "frais_optionnels": [self.transport.pk],
        }, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.eleve = models.Eleve.objects.get(pk=reponse.data["id"])
        self.frais_transport = models.FraisEleve.objects.get(
            id_eleve=self.eleve, id_type_frais=self.transport)

    def _encaisser(self, **extra):
        return self.client.post("/api/paiements/encaisser/", {
            "id_eleve": self.eleve.pk, "methode": "especes", **extra}, format="json")

    def test_seuls_les_frais_coches_sont_regles(self):
        reponse = self._encaisser(montant="15000", frais=[self.frais_transport.pk])
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.frais_transport.refresh_from_db()
        self.assertEqual(self.frais_transport.statut, "paye")
        self.assertEqual(
            models.Paiement.objects.filter(id_frais_eleve__id_eleve=self.eleve).count(), 1)
        # Le transport payé ne vaut pas inscription : droits et mensualité restent dus.
        self.assertEqual(models.Inscription.objects.get(id_eleve=self.eleve).statut, "en_attente")

    def test_le_montant_ne_depasse_pas_les_frais_coches(self):
        reponse = self._encaisser(montant="20000", frais=[self.frais_transport.pk])
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(models.Paiement.objects.exists())

    def test_un_frais_dun_autre_eleve_est_refuse(self):
        autre = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="X-2", nom="Autre", prenom="Eleve")
        frais = models.FraisEleve.objects.create(
            id_eleve=autre, id_type_frais=self.transport, id_annee_scolaire=self.annee,
            montant_du=Decimal("15000"))
        reponse = self._encaisser(montant="15000", frais=[frais.pk])
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(models.Paiement.objects.exists())

    def test_sans_case_cochee_lordre_habituel_sapplique(self):
        reponse = self._encaisser(montant="75000")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(models.Inscription.objects.get(id_eleve=self.eleve).statut, "en_cours")
        self.frais_transport.refresh_from_db()
        self.assertEqual(self.frais_transport.statut, "impaye")


class TestTypesDeFraisLibres(BaseAPI):
    """Chaque établissement nomme ses frais ; seule la nature pèse sur les règles."""

    def _type(self, client=None, **corps):
        return (client or self.client).post("/api/types-frais/", corps, format="json")

    def test_une_ecole_cree_ses_propres_frais(self):
        reponse = self._type(libelle="Adiya", nature="inscription", montant_defaut="10000")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        self.assertEqual(reponse.data["libelle"], "Adiya")
        self.assertEqual(reponse.data["nature_libelle"], "Frais d'inscription")

    def test_les_noms_choisis_servent_a_linscription_et_a_la_confirmation(self):
        self._type(libelle="Adiya", nature="inscription", montant_defaut="10000")
        self._type(libelle="Cotisation mensuelle", nature="mensualite", montant_defaut="5000",
                   frequence="Mensuelle")
        repas = self._type(libelle="Repas du midi", montant_defaut="3000")
        self.assertEqual(repas.data["nature"], "autre")

        reponse = self.client.post("/api/eleves/inscrire/", {
            "id_ecole": self.ecole.pk, "nom": "Diallo", "prenom": "Moussa",
            "id_classe": self.classe.pk, "id_annee_scolaire": self.annee.pk,
            "date_inscription": "2026-10-01", "frais_optionnels": [repas.data["id"]],
        }, format="json")
        self.assertEqual(reponse.status_code, 201, reponse.content)
        libelles = [ligne["libelle"] for ligne in reponse.data["facture"]["lignes"]]
        self.assertEqual(libelles[0], "Adiya")
        self.assertTrue(libelles[1].startswith("Cotisation mensuelle"))
        self.assertEqual(libelles[2], "Repas du midi")
        self.assertEqual(Decimal(str(reponse.data["facture"]["total_du"])), Decimal("18000"))

        # « Adiya » + « Cotisation mensuelle » réglés : inscrit, quel que soit leur nom.
        paiement = self.client.post("/api/paiements/encaisser/", {
            "id_eleve": reponse.data["id"], "montant": "15000", "methode": "especes"},
            format="json")
        self.assertEqual(paiement.status_code, 201, paiement.content)
        self.assertEqual(models.Inscription.objects.get(id_eleve_id=reponse.data["id"]).statut,
                         "en_cours")

    def test_une_seule_nature_inscription_par_ecole(self):
        self._type(libelle="Adiya", nature="inscription", montant_defaut="10000")
        reponse = self._type(libelle="Droits d'entrée", nature="inscription", montant_defaut="5000")
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("nature", reponse.data)

    def test_deux_frais_ne_portent_pas_le_meme_nom(self):
        self._type(libelle="Transport", montant_defaut="10000")
        reponse = self._type(libelle="Transport", montant_defaut="12000")
        self.assertEqual(reponse.status_code, 400)

    def test_une_autre_ecole_garde_sa_liberte(self):
        self._type(libelle="Adiya", nature="inscription", montant_defaut="10000")
        reponse = self._type(self.client_autre, libelle="Adiya", nature="inscription",
                             montant_defaut="7000")
        self.assertEqual(reponse.status_code, 201, reponse.content)


class TestGrilleDesTarifs(BaseAPI):
    """Un montant par niveau et par frais ; une case vide applique le montant par défaut."""

    def setUp(self):
        super().setUp()
        self.inscription = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Inscription", nature="inscription",
            montant_defaut=Decimal("50000"))
        self.transport = models.TypeFrais.objects.create(
            id_ecole=self.ecole, libelle="Transport", montant_defaut=Decimal("10000"),
            frequence="Mensuelle")
        self.troisieme = models.Niveau.objects.create(id_ecole=self.ecole, libelle="3ème")
        self.classe_3e = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.troisieme,
            nom="3ème A", capacite_max=40)

    def _enregistrer(self, tarifs, client=None):
        return (client or self.client).post("/api/tarifs/grille/", {"tarifs": tarifs}, format="json")

    def _case(self, niveau, type_frais, montant):
        return {"id_niveau": niveau.pk, "id_type_frais": type_frais.pk, "montant": montant}

    def test_la_grille_senregistre_et_se_relit(self):
        reponse = self._enregistrer([
            self._case(self.niveau, self.inscription, "30000"),
            self._case(self.troisieme, self.inscription, "35000"),
        ])
        self.assertEqual(reponse.status_code, 200, reponse.content)
        tarifs = reponse.data["tarifs"]
        self.assertEqual(Decimal(str(tarifs[f"{self.niveau.pk}-{self.inscription.pk}"])), Decimal("30000"))
        self.assertEqual(Decimal(str(tarifs[f"{self.troisieme.pk}-{self.inscription.pk}"])), Decimal("35000"))
        # Les frais dus d'office viennent en premières colonnes.
        self.assertEqual(reponse.data["types"][0]["id"], self.inscription.pk)
        self.assertEqual(self.client.get("/api/tarifs/grille/").data["tarifs"], tarifs)

    def test_chaque_niveau_paie_son_tarif(self):
        self._enregistrer([
            self._case(self.niveau, self.inscription, "30000"),
            self._case(self.troisieme, self.inscription, "35000"),
            self._case(self.troisieme, self.transport, "12000"),
        ])
        self.assertEqual(finances.tarif(self.inscription, self.classe), Decimal("30000"))
        self.assertEqual(finances.tarif(self.inscription, self.classe_3e), Decimal("35000"))
        self.assertEqual(finances.tarif(self.transport, self.classe), Decimal("10000"))
        self.assertEqual(finances.tarif(self.transport, self.classe_3e), Decimal("12000"))

    def test_une_case_videe_revient_au_montant_par_defaut(self):
        self._enregistrer([self._case(self.niveau, self.inscription, "30000")])
        self._enregistrer([self._case(self.niveau, self.inscription, None)])
        self.assertFalse(models.TarifNiveau.objects.exists())
        self.assertEqual(finances.tarif(self.inscription, self.classe), Decimal("50000"))

    def test_le_niveau_expose_ses_tarifs_au_formulaire(self):
        self._enregistrer([self._case(self.niveau, self.inscription, "30000")])
        reponse = self.client.get(f"/api/niveaux/{self.niveau.pk}/")
        self.assertEqual(Decimal(str(reponse.data["tarifs"][str(self.inscription.pk)])), Decimal("30000"))

    def test_un_niveau_dune_autre_ecole_est_refuse(self):
        niveau_b = models.Niveau.objects.create(id_ecole=self.autre, libelle="6ème")
        reponse = self._enregistrer([self._case(niveau_b, self.inscription, "1000")])
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(models.TarifNiveau.objects.exists())

    def test_un_montant_negatif_est_refuse(self):
        reponse = self._enregistrer([self._case(self.niveau, self.inscription, "-5")])
        self.assertEqual(reponse.status_code, 400)

    def test_reservee_aux_administrateurs(self):
        prof = self._client(self.enseignant_user.email)
        self.assertEqual(prof.get("/api/tarifs/grille/").status_code, 403)


class TestMatriceAutorisations(BaseAPI):
    """
    La matrice des autorisations par profil, cellule par cellule.

    Pour chaque module du fichier et chaque rôle : lire répond 200 ou 403,
    écrire répond 403 ou autre chose (400 sur un corps vide : la requête a
    passé le contrôle d'accès et bute sur la validation).
    """
    from .permissions import ECRITURE, LECTURE, MATRICE, PROFIL_DU_ROLE

    # Une liste représentative par module.
    ENDPOINTS = {
        "eleves": "eleves", "inscriptions": "inscriptions", "parents": "parents",
        "classes": "classes", "matieres": "matieres", "emploi_du_temps": "emplois-du-temps",
        "presences": "absences", "notes": "notes", "bulletins": "bulletins",
        "personnel": "enseignants", "frais": "frais-eleves", "encaissement": "paiements",
        "relances": "relances", "journal_caisse": "journal-caisse", "depenses": "depenses",
        "budget": "budgets", "types_frais": "types-frais", "parametres": "parametres",
    }

    def setUp(self):
        super().setUp()
        self.clients = {}
        for role in self.PROFIL_DU_ROLE:
            compte = self._compte(f"{role}@matrice.sn", self.ecole, role)
            self.clients[role] = self._client(compte.email)

    def test_tous_les_roles_ont_un_profil(self):
        self.assertEqual(set(Utilisateur.Role.values), set(self.PROFIL_DU_ROLE))

    def test_lecture_selon_la_matrice(self):
        for module, endpoint in self.ENDPOINTS.items():
            for role, profil in self.PROFIL_DU_ROLE.items():
                with self.subTest(module=module, role=role):
                    attendu = 403 if self.MATRICE[module][profil] is None else 200
                    r = self.clients[role].get(f"/api/{endpoint}/")
                    self.assertEqual(r.status_code, attendu, r.content[:200])

    def test_ecriture_selon_la_matrice(self):
        for module, endpoint in self.ENDPOINTS.items():
            for role, profil in self.PROFIL_DU_ROLE.items():
                with self.subTest(module=module, role=role):
                    r = self.clients[role].post(f"/api/{endpoint}/", {}, format="json")
                    if self.MATRICE[module][profil] == self.ECRITURE:
                        self.assertNotEqual(r.status_code, 403, r.content[:200])
                    else:
                        self.assertEqual(r.status_code, 403)

    def test_quelques_cellules_du_fichier(self):
        """Relu à la main sur le fichier, pour qu'une faute de frappe dans la matrice se voie."""
        M, E, L = self.MATRICE, self.ECRITURE, self.LECTURE
        self.assertEqual(M["depenses"]["caissier"], None)
        self.assertEqual(M["depenses"]["comptable"], E)
        self.assertEqual(M["types_frais"]["responsable_financier"], E)
        self.assertEqual(M["types_frais"]["comptable"], L)
        self.assertEqual(M["encaissement"]["caissier"], E)
        self.assertEqual(M["encaissement"]["comptable"], L)
        self.assertEqual(M["personnel"]["vie_scolaire"], L)
        self.assertEqual(M["personnel"]["enseignant"], None)
        self.assertEqual(M["journal_caisse"]["direction"], L)
        self.assertEqual(M["parametres"]["vie_scolaire"], None)
        self.assertEqual(M["notes"]["enseignant"], E)
        self.assertEqual(M["frais"]["eleve"], None)
        self.assertEqual(M["frais"]["parent"], L)

    def test_moi_renvoie_les_acces(self):
        r = self.clients["comptable"].get("/api/auth/moi/")
        self.assertEqual(r.data["profil"], "comptable")
        self.assertEqual(r.data["role_libelle"], "Comptable")
        self.assertEqual(r.data["acces"]["depenses"], "ecriture")
        self.assertEqual(r.data["acces"]["frais"], "lecture")
        self.assertNotIn("notes", r.data["acces"])

    def test_directeur_gere_les_comptes_et_compte_comme_administrateur(self):
        directeur = Utilisateur.objects.get(email="directeur@matrice.sn")
        self.assertEqual(self.clients["directeur"].get("/api/utilisateurs/").status_code, 200)
        self.assertEqual(self.clients["surveillant"].get("/api/utilisateurs/").status_code, 403)
        Utilisateur.objects.filter(
            id_ecole=self.ecole, role__in=("admin", "responsable_administratif"),
        ).exclude(pk=self.admin.pk).delete()
        # L'admin peut quitter la direction : le directeur reste à la tête de l'école.
        r = self.client.patch(f"/api/utilisateurs/{self.admin.pk}/", {"role": "enseignant"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        # Mais le dernier profil de direction ne peut pas s'en retirer.
        r = self.clients["directeur"].patch(
            f"/api/utilisateurs/{directeur.pk}/", {"role": "comptable"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_les_caissiers_listes_sont_ceux_qui_encaissent(self):
        r = self.clients["comptable"].get("/api/caisse/historique/")
        self.assertEqual(r.status_code, 200)
        roles = {c["role"] for c in r.data["caissiers"]}
        self.assertIn("caissier", roles)
        self.assertIn("surveillant", roles)
        self.assertNotIn("comptable", roles)

    def test_bilan_reserve_aux_profils_du_budget(self):
        self.assertEqual(self.clients["comptable"].get("/api/finances/bilan/").status_code, 200)
        self.assertEqual(self.clients["caissier"].get("/api/finances/bilan/").status_code, 403)


class TestPerimetreFamilleEtEnseignant(BaseAPI):
    """Un parent ne voit que ses enfants, un élève que lui-même, un enseignant que ses classes."""

    def setUp(self):
        super().setUp()
        self.enfant = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-101", nom="Diop", prenom="Awa")
        self.autre_eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-102", nom="Sy", prenom="Omar")
        for eleve in (self.enfant, self.autre_eleve):
            models.Inscription.objects.create(
                id_eleve=eleve, id_classe=self.classe, id_annee_scolaire=self.annee,
                date_inscription=date(2026, 10, 1))
        fiche = models.ParentTuteur.objects.create(
            id_ecole=self.ecole, nom="Diop", prenom="Modou", telephone="+221770000009",
            id_utilisateur=self.parent_user)
        models.EleveParent.objects.create(id_eleve=self.enfant, id_parent=fiche, lien_parente="pere")
        self.eleve_user = self._compte("eleve@a.sn", self.ecole, "eleve")
        self.autre_eleve.id_utilisateur = self.eleve_user
        self.autre_eleve.save()
        self.parent = self._client(self.parent_user.email)
        self.eleve = self._client(self.eleve_user.email)
        self.prof = self._client(self.enseignant_user.email)

    def test_le_parent_ne_voit_que_ses_enfants(self):
        ids = [e["id"] for e in self.resultats(self.parent.get("/api/eleves/"))]
        self.assertEqual(ids, [self.enfant.pk])
        self.assertEqual(self.parent.get(f"/api/eleves/{self.autre_eleve.pk}/dossier/").status_code, 404)
        self.assertEqual(self.parent.get(f"/api/eleves/{self.autre_eleve.pk}/finances/").status_code, 404)
        self.assertEqual(self.parent.get(f"/api/eleves/{self.enfant.pk}/dossier/").status_code, 200)

    def test_leleve_ne_voit_que_lui_meme(self):
        ids = [e["id"] for e in self.resultats(self.eleve.get("/api/eleves/"))]
        self.assertEqual(ids, [self.autre_eleve.pk])
        # Pas d'accès aux frais : la section finances du dossier n'est pas envoyée.
        dossier = self.eleve.get(f"/api/eleves/{self.autre_eleve.pk}/dossier/").data
        self.assertIsNone(dossier["finances"])
        self.assertIsNotNone(dossier["resultats"])

    def test_les_vues_sur_toute_lecole_sont_fermees_aux_familles(self):
        self.assertEqual(self.parent.get("/api/frais-eleves/situation/").status_code, 403)
        self.assertEqual(self.parent.get("/api/absences/synthese/").status_code, 403)
        r = self.parent.get("/api/conseils-classe/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.resultats(r), [])

    def test_la_famille_ne_modifie_rien(self):
        r = self.parent.patch(f"/api/eleves/{self.enfant.pk}/", {"nom": "X"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_le_dossier_vu_par_un_enseignant_na_ni_finances_ni_parents(self):
        dossier = self.prof.get(f"/api/eleves/{self.enfant.pk}/dossier/").data
        self.assertIsNone(dossier["finances"])
        self.assertIsNone(dossier["parents"])
        self.assertIsNotNone(dossier["resultats"])

    def test_lenseignant_ne_voit_les_notes_que_de_ses_classes(self):
        periode = models.PeriodeScolaire.objects.create(
            id_annee_scolaire=self.annee, libelle="T1",
            date_debut=date(2026, 10, 1), date_fin=date(2026, 12, 20))
        models.Evaluation.objects.create(
            id_matiere=self.matiere, id_classe=self.classe, id_periode=periode,
            date_evaluation=date(2026, 11, 10))
        self.assertEqual(self.resultats(self.prof.get("/api/evaluations/")), [])
        models.AffectationEnseignant.objects.create(
            id_enseignant=self.enseignant, id_matiere=self.matiere,
            id_classe=self.classe, id_annee_scolaire=self.annee)
        self.assertEqual(len(self.resultats(self.prof.get("/api/evaluations/"))), 1)


class TestCreationDAcces(BaseAPI):
    """« Créer son accès » : le compte de connexion d'une fiche, et son rattachement."""

    def setUp(self):
        super().setUp()
        self.fiche_parent = models.ParentTuteur.objects.create(
            id_ecole=self.ecole, nom="Diop", prenom="Modou",
            telephone="+221770000009", email="modou.diop@exemple.sn")
        self.enfant = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-201", nom="Diop", prenom="Awa")
        models.EleveParent.objects.create(
            id_eleve=self.enfant, id_parent=self.fiche_parent, lien_parente="pere")

    def _creer(self, client, endpoint, id_fiche, **corps):
        return client.post(f"/api/{endpoint}/{id_fiche}/creer-acces/", corps, format="json")

    def test_lacces_dun_parent_ouvre_le_dossier_de_ses_enfants(self):
        r = self._creer(self.client, "parents", self.fiche_parent.pk)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data["email"], "modou.diop@exemple.sn")
        self.assertEqual(r.data["role"], "parent")
        mot_de_passe = r.data["mot_de_passe_provisoire"]
        self.assertGreaterEqual(len(mot_de_passe), 8)

        self.fiche_parent.refresh_from_db()
        compte = self.fiche_parent.id_utilisateur
        self.assertIsNotNone(compte)
        self.assertEqual((compte.nom, compte.prenom), ("Diop", "Modou"))
        self.assertEqual(compte.telephone, "+221770000009")

        # Le compte se connecte et ne voit que l'enfant de la fiche.
        connexion = APIClient()
        jeton = connexion.post("/api/auth/token/",
                               {"email": compte.email, "password": mot_de_passe}, format="json")
        self.assertEqual(jeton.status_code, 200, jeton.content)
        connexion.credentials(HTTP_AUTHORIZATION=f"Bearer {jeton.data['access']}")
        self.assertEqual([e["id"] for e in self.resultats(connexion.get("/api/eleves/"))],
                         [self.enfant.pk])

    def test_lacces_dun_eleve_et_celui_dun_enseignant(self):
        r = self._creer(self.client, "eleves", self.enfant.pk, email="awa.diop@exemple.sn")
        self.assertEqual(r.status_code, 201, r.content)
        self.enfant.refresh_from_db()
        self.assertEqual(self.enfant.id_utilisateur.role, "eleve")

        # Un enseignant n'a ni nom ni e-mail sur sa fiche : ils sont saisis.
        fiche = models.Enseignant.objects.create(id_ecole=self.ecole, matricule="ENS-777")
        r = self._creer(self.client, "enseignants", fiche.pk)
        self.assertEqual(r.status_code, 400)
        r = self._creer(self.client, "enseignants", fiche.pk,
                        nom="Ndiaye", prenom="Fatou", email="fatou.ndiaye@exemple.sn")
        self.assertEqual(r.status_code, 201, r.content)
        fiche.refresh_from_db()
        self.assertEqual(fiche.id_utilisateur.role, "enseignant")
        self.assertEqual(self.client.get(f"/api/enseignants/{fiche.pk}/").data["nom_complet"],
                         "Fatou Ndiaye")

    def test_une_fiche_na_quun_acces_et_un_email_ne_sert_quune_fois(self):
        self.assertEqual(self._creer(self.client, "parents", self.fiche_parent.pk).status_code, 201)
        r = self._creer(self.client, "parents", self.fiche_parent.pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("déjà un accès", r.data["detail"])

        autre = models.ParentTuteur.objects.create(
            id_ecole=self.ecole, nom="Sy", prenom="Omar", telephone="+221770000010")
        r = self._creer(self.client, "parents", autre.pk, email="MODOU.DIOP@exemple.sn")
        self.assertEqual(r.status_code, 400)
        self.assertIn("e-mail", str(r.data["email"]))

    def test_reserve_a_la_direction_et_a_son_ecole(self):
        for role in ("surveillant", "caissier", "comptable", "enseignant"):
            compte = self._compte(f"{role}@acces.sn", self.ecole, role)
            with self.subTest(role=role):
                r = self._creer(self._client(compte.email), "parents", self.fiche_parent.pk)
                self.assertEqual(r.status_code, 403)
        # Une autre école ne voit pas la fiche.
        self.assertEqual(self._creer(self.client_autre, "parents", self.fiche_parent.pk).status_code, 404)
        self.assertFalse(models.ParentTuteur.objects.get(pk=self.fiche_parent.pk).id_utilisateur_id)

    def test_la_creation_est_journalisee(self):
        self._creer(self.client, "parents", self.fiche_parent.pk)
        self.assertTrue(models.JournalActivite.objects.filter(
            action="creation", entite="utilisateur",
            description__contains="Création de l'accès parent").exists())


class TestMonProfil(BaseAPI):
    """Ce que chacun fait sur son propre compte : son mot de passe, son nom."""

    NOUVEAU = "TorpilleVerte7"

    def _changer(self, client, actuel, nouveau):
        return client.post("/api/auth/mot-de-passe/",
                           {"mot_de_passe_actuel": actuel, "nouveau_mot_de_passe": nouveau},
                           format="json")

    def test_chacun_change_son_mot_de_passe_et_se_reconnecte_avec(self):
        parent = self._client(self.parent_user.email)
        r = self._changer(parent, MDP, self.NOUVEAU)
        self.assertEqual(r.status_code, 200, r.content)

        connexion = APIClient()
        self.assertEqual(connexion.post("/api/auth/token/",
                                        {"email": self.parent_user.email, "password": MDP},
                                        format="json").status_code, 401)
        self.assertEqual(connexion.post("/api/auth/token/",
                                        {"email": self.parent_user.email, "password": self.NOUVEAU},
                                        format="json").status_code, 200)
        self.assertTrue(models.JournalActivite.objects.filter(
            action="modification", entite="utilisateur",
            description="Changement de son mot de passe").exists())

    def test_le_mot_de_passe_actuel_est_exige_et_le_nouveau_verifie(self):
        parent = self._client(self.parent_user.email)
        self.assertEqual(self._changer(parent, "mauvais mot de passe", self.NOUVEAU).status_code, 400)
        # Trop court, trop courant, ou identique à l'actuel : refusés.
        for nouveau in ("court", "password", MDP):
            with self.subTest(nouveau=nouveau):
                self.assertEqual(self._changer(parent, MDP, nouveau).status_code, 400)
        self.parent_user.refresh_from_db()
        self.assertTrue(self.parent_user.check_password(MDP))

    def test_un_compte_non_connecte_ne_change_rien(self):
        self.assertEqual(self._changer(APIClient(), MDP, self.NOUVEAU).status_code, 401)

    def test_chacun_corrige_son_nom_et_son_telephone(self):
        parent = self._client(self.parent_user.email)
        r = parent.patch("/api/auth/moi/",
                         {"prenom": "Modou", "telephone": "+221770001122"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["prenom"], "Modou")
        self.parent_user.refresh_from_db()
        self.assertEqual(self.parent_user.telephone, "+221770001122")

    def test_on_ne_se_donne_ni_role_ni_adresse_de_connexion(self):
        parent = self._client(self.parent_user.email)
        r = parent.patch("/api/auth/moi/",
                         {"role": "admin", "email": "pirate@a.sn", "statut": "inactif"},
                         format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.parent_user.refresh_from_db()
        self.assertEqual(self.parent_user.role, "parent")
        self.assertEqual(self.parent_user.email, "parent@a.sn")
        self.assertEqual(self.parent_user.statut, "actif")


class TestListeDesElevesPourUneSaisie(BaseAPI):
    """Ce que propose la liste déroulante « Élève » d'un formulaire d'absence."""

    def setUp(self):
        super().setUp()
        self.autre_classe = models.Classe.objects.create(
            id_ecole=self.ecole, id_annee_scolaire=self.annee, id_niveau=self.niveau,
            nom="6ème B", capacite_max=40)
        # Deux homonymes : le nom seul ne dit pas de qui il s'agit.
        self.awa = self._inscrire("MIC-0001", "Ba", "Awa", self.classe)
        self.aminata = self._inscrire("MIC-0002", "Ba", "Aminata", self.classe)
        self.dautre_classe = self._inscrire("MIC-0003", "Ba", "Ousmane", self.autre_classe)

    def _inscrire(self, matricule, nom, prenom, classe):
        eleve = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule=matricule, nom=nom, prenom=prenom)
        models.Inscription.objects.create(
            id_eleve=eleve, id_classe=classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1))
        return eleve

    def test_la_liste_se_restreint_a_la_classe_choisie(self):
        lignes = self.resultats(self.client.get("/api/eleves/", {"classe": self.classe.pk}))
        self.assertEqual({l["id"] for l in lignes}, {self.awa.pk, self.aminata.pk})

    def test_chaque_eleve_porte_son_nom_complet_et_son_matricule(self):
        lignes = self.resultats(self.client.get("/api/eleves/", {"classe": self.classe.pk}))
        par_id = {l["id"]: l for l in lignes}
        self.assertEqual(par_id[self.awa.pk]["nom_complet"], "Awa Ba")
        self.assertEqual(par_id[self.aminata.pk]["nom_complet"], "Aminata Ba")
        self.assertEqual(par_id[self.awa.pk]["matricule"], "MIC-0001")

    def test_une_classe_dune_autre_ecole_est_refusee(self):
        classe_b = models.Classe.objects.create(
            id_ecole=self.autre,
            id_annee_scolaire=models.AnneeScolaire.objects.create(
                id_ecole=self.autre, libelle="2026-2027",
                date_debut=date(2026, 10, 1), date_fin=date(2027, 7, 31)),
            nom="6ème A", capacite_max=40)
        r = self.client.get("/api/eleves/", {"classe": classe_b.pk})
        self.assertEqual(r.status_code, 400)
