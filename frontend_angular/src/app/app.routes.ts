import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';
import { accesGuard } from './core/guards/acces.guard';
import { LayoutComponent } from './layout/layout.component';
import { GenericListComponent } from './shared/crud/generic-list.component';
import { GenericFormComponent } from './shared/crud/generic-form.component';

export const routes: Routes = [
  {
    path: 'connexion',
    loadComponent: () => import('./features/auth/login/login.component').then((m) => m.LoginComponent),
  },

  {
    path: '',
    component: LayoutComponent,
    canActivate: [authGuard],
    // Chaque page suit la matrice des autorisations : voir `accesGuard`.
    canActivateChild: [accesGuard],
    children: [
      { path: '', redirectTo: 'tableau-de-bord', pathMatch: 'full' },

      {
        path: 'tableau-de-bord',
        data: { module: 'tableau_de_bord' },
        loadComponent: () => import('./features/dashboard/dashboard.component').then((m) => m.DashboardComponent),
      },

      // ---- Élèves (module dédié, formulaire réel) ----
      {
        path: 'eleves',
        data: { module: 'eleves' },
        loadComponent: () => import('./features/eleves/eleves-list.component').then((m) => m.ElevesListComponent),
      },
      {
        path: 'eleves/nouveau',
        data: { module: 'inscriptions', ecriture: true },
        loadComponent: () => import('./features/eleves/eleve-form.component').then((m) => m.EleveFormComponent),
      },
      {
        path: 'eleves/importer',
        data: { module: 'inscriptions', ecriture: true },
        loadComponent: () => import('./features/eleves/import-eleves.component').then((m) => m.ImportElevesComponent),
      },
      {
        path: 'eleves/reinscrire',
        data: { module: 'inscriptions', ecriture: true },
        loadComponent: () => import('./features/eleves/reinscription.component').then((m) => m.ReinscriptionComponent),
      },
      // Après les routes fixes : sinon « importer » serait pris pour un id.
      {
        path: 'eleves/:id/dossier',
        data: { module: 'eleves' },
        loadComponent: () => import('./features/eleves/eleve-dossier.component').then((m) => m.EleveDossierComponent),
      },
      {
        path: 'eleves/:id/reinscrire',
        data: { module: 'inscriptions', ecriture: true },
        loadComponent: () => import('./features/eleves/eleve-reinscription.component').then((m) => m.EleveReinscriptionComponent),
      },

      // ---- Pédagogie (module dédié : saisie en grille, bulletins,
      //      palmarès ; les référentiels passent par le CRUD générique) ----
      {
        path: 'pedagogie/notes',
        data: { module: 'notes', ecriture: true },
        loadComponent: () => import('./features/pedagogie/saisie-notes.component').then((m) => m.SaisieNotesComponent),
      },
      {
        path: 'pedagogie/bulletins',
        data: { module: 'bulletins', personnel: true },
        loadComponent: () => import('./features/pedagogie/bulletins.component').then((m) => m.BulletinsComponent),
      },
      {
        path: 'pedagogie/palmares',
        data: { module: 'bulletins', personnel: true },
        loadComponent: () => import('./features/pedagogie/palmares.component').then((m) => m.PalmaresComponent),
      },

      // ---- Finances (module dédié : impayés, encaissement, relances,
      //      journal, bilan ; les référentiels passent par le CRUD générique) ----
      {
        path: 'finances',
        data: { module: 'frais', personnel: true },
        loadComponent: () => import('./features/finances/finances-list.component').then((m) => m.FinancesListComponent),
      },
      {
        path: 'finances/caisse',
        data: { module: 'journal_caisse' },
        loadComponent: () => import('./features/finances/caisse.component').then((m) => m.CaisseComponent),
      },
      // L'historique du jour a rejoint la caisse : l'ancienne adresse y mène.
      {
        path: 'finances/historique-caisse',
        redirectTo: 'finances/caisse',
        pathMatch: 'full',
      },
      {
        path: 'finances/tarifs',
        data: { module: 'types_frais' },
        loadComponent: () => import('./features/finances/tarifs-niveaux.component').then((m) => m.TarifsNiveauxComponent),
      },
      {
        path: 'finances/encaisser/:idFrais',
        data: { module: 'encaissement', ecriture: true },
        loadComponent: () => import('./features/finances/paiement-form.component').then((m) => m.PaiementFormComponent),
      },
      {
        path: 'finances/generer',
        data: { module: 'frais', ecriture: true },
        loadComponent: () => import('./features/finances/generer-frais.component').then((m) => m.GenererFraisComponent),
      },
      {
        path: 'finances/relances',
        data: { module: 'relances', ecriture: true },
        loadComponent: () => import('./features/finances/relances.component').then((m) => m.RelancesComponent),
      },
      {
        path: 'finances/journal',
        data: { module: 'journal_caisse' },
        loadComponent: () => import('./features/finances/journal-caisse.component').then((m) => m.JournalCaisseComponent),
      },
      {
        path: 'finances/bilan',
        data: { module: 'budget' },
        loadComponent: () => import('./features/finances/bilan-financier.component').then((m) => m.BilanFinancierComponent),
      },

      {
        path: 'mon-profil',
        data: { module: null },
        loadComponent: () => import('./features/profil/mon-profil.component').then((m) => m.MonProfilComponent),
      },

      // Éléments supprimés, depuis n'importe quel module (`?module=classes`).
      {
        path: 'corbeille',
        data: { module: null },
        loadComponent: () => import('./features/corbeille/corbeille.component').then((m) => m.CorbeilleComponent),
      },

      // ---- Modules via le système CRUD générique ----

      // Référentiels de structure : à poser avant toute inscription.
      { path: 'annees-scolaires', component: GenericListComponent, data: { entityKey: 'annees-scolaires' } },
      { path: 'annees-scolaires/:id', component: GenericFormComponent, data: { entityKey: 'annees-scolaires', ecriture: true } },

      { path: 'niveaux', component: GenericListComponent, data: { entityKey: 'niveaux' } },
      { path: 'niveaux/:id', component: GenericFormComponent, data: { entityKey: 'niveaux', ecriture: true } },

      { path: 'salles', component: GenericListComponent, data: { entityKey: 'salles' } },
      { path: 'salles/:id', component: GenericFormComponent, data: { entityKey: 'salles', ecriture: true } },

      { path: 'conseils-classe', component: GenericListComponent, data: { entityKey: 'conseils-classe' } },
      { path: 'conseils-classe/:id', component: GenericFormComponent, data: { entityKey: 'conseils-classe', ecriture: true } },

      { path: 'parents', component: GenericListComponent, data: { entityKey: 'parents' } },
      { path: 'parents/:id', component: GenericFormComponent, data: { entityKey: 'parents', ecriture: true } },

      { path: 'classes', component: GenericListComponent, data: { entityKey: 'classes' } },
      { path: 'classes/:id', component: GenericFormComponent, data: { entityKey: 'classes', ecriture: true } },

      { path: 'matieres', component: GenericListComponent, data: { entityKey: 'matieres' } },
      { path: 'matieres/:id', component: GenericFormComponent, data: { entityKey: 'matieres', ecriture: true } },

      { path: 'emploi-du-temps', component: GenericListComponent, data: { entityKey: 'emploi-du-temps' } },
      { path: 'emploi-du-temps/:id', component: GenericFormComponent, data: { entityKey: 'emploi-du-temps', ecriture: true } },

      { path: 'presences', component: GenericListComponent, data: { entityKey: 'presences' } },
      { path: 'presences/:id', component: GenericFormComponent, data: { entityKey: 'presences', ecriture: true } },

      { path: 'evaluations', component: GenericListComponent, data: { entityKey: 'evaluations' } },
      { path: 'evaluations/:id', component: GenericFormComponent, data: { entityKey: 'evaluations', ecriture: true } },

      { path: 'notes', component: GenericListComponent, data: { entityKey: 'notes' } },
      { path: 'notes/:id', component: GenericFormComponent, data: { entityKey: 'notes', ecriture: true } },

      { path: 'personnel', component: GenericListComponent, data: { entityKey: 'personnel' } },
      { path: 'personnel/:id', component: GenericFormComponent, data: { entityKey: 'personnel', ecriture: true } },

      // Référentiels financiers : catalogue des frais, postes de charge et
      // leur enveloppe, dépenses.
      { path: 'types-frais', component: GenericListComponent, data: { entityKey: 'types-frais' } },
      { path: 'types-frais/:id', component: GenericFormComponent, data: { entityKey: 'types-frais', ecriture: true } },

      { path: 'types-depenses', component: GenericListComponent, data: { entityKey: 'types-depenses' } },
      { path: 'types-depenses/:id', component: GenericFormComponent, data: { entityKey: 'types-depenses', ecriture: true } },

      { path: 'depenses', component: GenericListComponent, data: { entityKey: 'depenses' } },
      { path: 'depenses/:id', component: GenericFormComponent, data: { entityKey: 'depenses', ecriture: true } },

      { path: 'periodes', component: GenericListComponent, data: { entityKey: 'periodes' } },
      { path: 'periodes/:id', component: GenericFormComponent, data: { entityKey: 'periodes', ecriture: true } },

      { path: 'coefficients', component: GenericListComponent, data: { entityKey: 'coefficients' } },
      { path: 'coefficients/:id', component: GenericFormComponent, data: { entityKey: 'coefficients', ecriture: true } },

      { path: 'affectations', component: GenericListComponent, data: { entityKey: 'affectations' } },
      { path: 'affectations/:id', component: GenericFormComponent, data: { entityKey: 'affectations', ecriture: true } },

      { path: 'utilisateurs', component: GenericListComponent, data: { entityKey: 'utilisateurs' } },
      { path: 'utilisateurs/:id', component: GenericFormComponent, data: { entityKey: 'utilisateurs', ecriture: true } },

      // Consultation seule : pas de route de formulaire.
      { path: 'bulletins', component: GenericListComponent, data: { entityKey: 'bulletins' } },
      { path: 'recus', component: GenericListComponent, data: { entityKey: 'recus' } },

      { path: 'communications', component: GenericListComponent, data: { entityKey: 'communications' } },
      { path: 'communications/:id', component: GenericFormComponent, data: { entityKey: 'communications', ecriture: true } },

      { path: 'documents', component: GenericListComponent, data: { entityKey: 'documents' } },
      { path: 'documents/:id', component: GenericFormComponent, data: { entityKey: 'documents', ecriture: true } },

      { path: 'parametres', component: GenericListComponent, data: { entityKey: 'parametres' } },
      // Avant la route à paramètre : sinon « ecole » serait pris pour un id.
      {
        path: 'parametres/ecole',
        data: { module: 'parametres' },
        loadComponent: () => import('./features/parametres/fiche-ecole.component').then((m) => m.FicheEcoleComponent),
      },
      { path: 'parametres/:id', component: GenericFormComponent, data: { entityKey: 'parametres', ecriture: true } },

      // Consultation seule : pas de route de formulaire pour un journal d'audit.
      { path: 'journal-activite', component: GenericListComponent, data: { entityKey: 'journal-activite' } },
    ],
  },

  { path: '**', redirectTo: 'tableau-de-bord' },
];
