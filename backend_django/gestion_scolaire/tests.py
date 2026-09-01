"""
Tests de l'API.

    python manage.py test gestion_scolaire

Deux écoles sont créées systématiquement : l'isolation multi-écoles est
l'invariant central du produit, et la plupart des régressions graves
consisteraient à la percer.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from . import models

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
