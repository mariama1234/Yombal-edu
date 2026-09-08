"""
Tests de l'API.

    python manage.py test gestion_scolaire

Deux écoles sont créées systématiquement : l'isolation multi-écoles est
l'invariant central du produit, et la plupart des régressions graves
consisteraient à la percer.
"""
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from . import exports, finances, imports_eleves, models, pedagogie, scolarite

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

        self.eleve1 = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-001", nom="Fall", prenom="Moussa")
        models.Inscription.objects.create(
            id_eleve=self.eleve1, id_classe=self.classe, id_annee_scolaire=self.annee,
            date_inscription=date(2026, 10, 1))
        self.eleve2 = models.Eleve.objects.create(
            id_ecole=self.ecole, matricule="E-002", nom="Sarr", prenom="Mariama")
        models.Inscription.objects.create(
            id_eleve=self.eleve2, id_classe=self.classe2, id_annee_scolaire=self.annee2,
            date_inscription=date(2027, 10, 1))

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
            date_inscription=date(2027, 10, 1))
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
        self.assertEqual(self._client(self.parent_user.email)
                         .get("/api/dossiers/").status_code, 403)


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
            id_ecole=self.ecole, libelle="mensualite", montant_defaut=25000)
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
            id_ecole=self.autre, libelle="mensualite", montant_defaut=1000)
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
        for endpoint in ["recus", "journal-caisse"]:
            with self.subTest(endpoint=endpoint):
                r = self.client.post(f"/api/{endpoint}/", {}, format="json")
                self.assertEqual(r.status_code, 405)

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
            id_ecole=self.ecole, libelle="mensualite", montant_defaut=25000)
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
            id_ecole=self.autre, libelle="mensualite", montant_defaut=1000)
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
            id_ecole=self.autre, libelle="mensualite", montant_defaut=1000)
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
        self.assertIn(recu.numero_recu, r["Content-Disposition"])
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

    def test_reinscription_interdite_au_caissier(self):
        caissier = self._compte("caisse@a.sn", self.ecole, "caissier")
        r = self._client(caissier.email).post("/api/inscriptions/reinscrire/", {
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
            id_ecole=self.ecole, libelle="mensualite", montant_defaut=25000)
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
            id_ecole=self.ecole, libelle="mensualite", montant_defaut=25000)
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
