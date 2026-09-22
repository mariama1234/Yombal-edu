from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register(r"ecoles", views.EcoleViewSet, basename="ecole")
router.register(r"utilisateurs", views.UtilisateurViewSet, basename="utilisateur")
router.register(r"annees-scolaires", views.AnneeScolaireViewSet, basename="annee-scolaire")
router.register(r"periodes", views.PeriodeScolaireViewSet, basename="periode")
router.register(r"niveaux", views.NiveauViewSet, basename="niveau")
router.register(r"classes", views.ClasseViewSet, basename="classe")
router.register(r"parents", views.ParentTuteurViewSet, basename="parent")
router.register(r"eleves", views.EleveViewSet, basename="eleve")
router.register(r"eleves-parents", views.EleveParentViewSet, basename="eleve-parent")
router.register(r"dossiers", views.DossierAdministratifViewSet, basename="dossier")
router.register(r"historique-scolaire", views.HistoriqueScolaireViewSet, basename="historique-scolaire")
router.register(r"inscriptions", views.InscriptionViewSet, basename="inscription")
router.register(r"enseignants", views.EnseignantViewSet, basename="enseignant")
router.register(r"matieres", views.MatiereViewSet, basename="matiere")
router.register(r"coefficients", views.CoefficientMatiereViewSet, basename="coefficient")
router.register(r"affectations", views.AffectationEnseignantViewSet, basename="affectation")
router.register(r"conseils-classe", views.ConseilClasseViewSet, basename="conseil-classe")
router.register(r"evaluations", views.EvaluationViewSet, basename="evaluation")
router.register(r"notes", views.NoteViewSet, basename="note")
router.register(r"bulletins", views.BulletinViewSet, basename="bulletin")
router.register(r"absences", views.AbsenceViewSet, basename="absence")
router.register(r"salles", views.SalleViewSet, basename="salle")
router.register(r"emplois-du-temps", views.EmploiDuTempsViewSet, basename="emploi-du-temps")
router.register(r"types-frais", views.TypeFraisViewSet, basename="type-frais")
router.register(r"frais-eleves", views.FraisEleveViewSet, basename="frais-eleve")
router.register(r"paiements", views.PaiementViewSet, basename="paiement")
router.register(r"recus", views.RecuViewSet, basename="recu")
router.register(r"relances", views.RelanceViewSet, basename="relance")
router.register(r"types-depenses", views.TypeDepenseViewSet, basename="type-depense")
router.register(r"depenses", views.DepenseViewSet, basename="depense")
router.register(r"budgets", views.BudgetViewSet, basename="budget")
router.register(r"journal-caisse", views.JournalCaisseViewSet, basename="journal-caisse")
router.register(r"communications", views.CommunicationViewSet, basename="communication")
router.register(r"communication-destinataires", views.CommunicationDestinataireViewSet,
                basename="communication-destinataire")
router.register(r"documents", views.DocumentViewSet, basename="document")
router.register(r"rapports", views.RapportViewSet, basename="rapport")
router.register(r"parametres", views.ParametreEcoleViewSet, basename="parametre")
router.register(r"journal-activite", views.JournalActiviteViewSet, basename="journal-activite")
router.register(r"corbeille", views.CorbeilleViewSet, basename="corbeille")

urlpatterns = [
    path("auth/token/", views.ConnexionView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/moi/", views.MoiView.as_view(), name="moi"),
    path("auth/mot-de-passe/", views.ChangementMotDePasseView.as_view(), name="mot-de-passe"),
    path("finances/bilan/", views.BilanFinancierView.as_view(), name="finances-bilan"),
    path("tarifs/grille/", views.GrilleTarifsView.as_view(), name="tarifs-grille"),
    path("caisse/historique/", views.HistoriqueCaisseView.as_view(), name="caisse-historique"),
    path("caisse/cloturer/", views.ClotureCaisseView.as_view(), name="caisse-cloturer"),
    path("", include(router.urls)),
]
