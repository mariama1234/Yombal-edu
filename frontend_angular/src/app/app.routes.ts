import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';
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
    children: [
      { path: '', redirectTo: 'tableau-de-bord', pathMatch: 'full' },

      {
        path: 'tableau-de-bord',
        loadComponent: () => import('./features/dashboard/dashboard.component').then((m) => m.DashboardComponent),
      },

      // ---- Élèves (module dédié, formulaire réel) ----
      {
        path: 'eleves',
        loadComponent: () => import('./features/eleves/eleves-list.component').then((m) => m.ElevesListComponent),
      },
      {
        path: 'eleves/nouveau',
        loadComponent: () => import('./features/eleves/eleve-form.component').then((m) => m.EleveFormComponent),
      },
      {
        path: 'eleves/importer',
        loadComponent: () => import('./features/eleves/import-eleves.component').then((m) => m.ImportElevesComponent),
      },
      {
        path: 'eleves/reinscrire',
        loadComponent: () => import('./features/eleves/reinscription.component').then((m) => m.ReinscriptionComponent),
      },
      // Après les routes fixes : sinon « importer » serait pris pour un id.
      {
        path: 'eleves/:id/dossier',
        loadComponent: () => import('./features/eleves/eleve-dossier.component').then((m) => m.EleveDossierComponent),
      },
      {
        path: 'eleves/:id/reinscrire',
        loadComponent: () => import('./features/eleves/eleve-reinscription.component').then((m) => m.EleveReinscriptionComponent),
      },

      // ---- Pédagogie (module dédié : saisie en grille, bulletins,
      //      palmarès ; les référentiels passent par le CRUD générique) ----
      {
        path: 'pedagogie/notes',
        loadComponent: () => import('./features/pedagogie/saisie-notes.component').then((m) => m.SaisieNotesComponent),
      },
      {
        path: 'pedagogie/bulletins',
        loadComponent: () => import('./features/pedagogie/bulletins.component').then((m) => m.BulletinsComponent),
      },
      {
        path: 'pedagogie/palmares',
        loadComponent: () => import('./features/pedagogie/palmares.component').then((m) => m.PalmaresComponent),
      },

      // ---- Finances (module dédié : impayés, encaissement, relances,
      //      journal, bilan ; les référentiels passent par le CRUD générique) ----
      {
        path: 'finances',
        loadComponent: () => import('./features/finances/finances-list.component').then((m) => m.FinancesListComponent),
      },
      {
        path: 'finances/encaisser/:idFrais',
        loadComponent: () => import('./features/finances/paiement-form.component').then((m) => m.PaiementFormComponent),
      },
      {
        path: 'finances/generer',
        loadComponent: () => import('./features/finances/generer-frais.component').then((m) => m.GenererFraisComponent),
      },
      {
        path: 'finances/relances',
        loadComponent: () => import('./features/finances/relances.component').then((m) => m.RelancesComponent),
      },
      {
        path: 'finances/journal',
        loadComponent: () => import('./features/finances/journal-caisse.component').then((m) => m.JournalCaisseComponent),
      },
      {
        path: 'finances/bilan',
        loadComponent: () => import('./features/finances/bilan-financier.component').then((m) => m.BilanFinancierComponent),
      },

      // ---- Modules via le système CRUD générique ----

      // Référentiels de structure : à poser avant toute inscription.
      { path: 'annees-scolaires', component: GenericListComponent, data: { entityKey: 'annees-scolaires' } },
      { path: 'annees-scolaires/:id', component: GenericFormComponent, data: { entityKey: 'annees-scolaires' } },

      { path: 'niveaux', component: GenericListComponent, data: { entityKey: 'niveaux' } },
      { path: 'niveaux/:id', component: GenericFormComponent, data: { entityKey: 'niveaux' } },

      { path: 'salles', component: GenericListComponent, data: { entityKey: 'salles' } },
      { path: 'salles/:id', component: GenericFormComponent, data: { entityKey: 'salles' } },

      { path: 'conseils-classe', component: GenericListComponent, data: { entityKey: 'conseils-classe' } },
      { path: 'conseils-classe/:id', component: GenericFormComponent, data: { entityKey: 'conseils-classe' } },

      { path: 'parents', component: GenericListComponent, data: { entityKey: 'parents' } },
      { path: 'parents/:id', component: GenericFormComponent, data: { entityKey: 'parents' } },

      { path: 'classes', component: GenericListComponent, data: { entityKey: 'classes' } },
      { path: 'classes/:id', component: GenericFormComponent, data: { entityKey: 'classes' } },

      { path: 'matieres', component: GenericListComponent, data: { entityKey: 'matieres' } },
      { path: 'matieres/:id', component: GenericFormComponent, data: { entityKey: 'matieres' } },

      { path: 'emploi-du-temps', component: GenericListComponent, data: { entityKey: 'emploi-du-temps' } },
      { path: 'emploi-du-temps/:id', component: GenericFormComponent, data: { entityKey: 'emploi-du-temps' } },

      { path: 'presences', component: GenericListComponent, data: { entityKey: 'presences' } },
      { path: 'presences/:id', component: GenericFormComponent, data: { entityKey: 'presences' } },

      { path: 'evaluations', component: GenericListComponent, data: { entityKey: 'evaluations' } },
      { path: 'evaluations/:id', component: GenericFormComponent, data: { entityKey: 'evaluations' } },

      { path: 'notes', component: GenericListComponent, data: { entityKey: 'notes' } },
      { path: 'notes/:id', component: GenericFormComponent, data: { entityKey: 'notes' } },

      { path: 'personnel', component: GenericListComponent, data: { entityKey: 'personnel' } },
      { path: 'personnel/:id', component: GenericFormComponent, data: { entityKey: 'personnel' } },

      // Référentiels financiers : catalogue des frais, postes de charge,
      // dépenses et budgets.
      { path: 'types-frais', component: GenericListComponent, data: { entityKey: 'types-frais' } },
      { path: 'types-frais/:id', component: GenericFormComponent, data: { entityKey: 'types-frais' } },

      { path: 'types-depenses', component: GenericListComponent, data: { entityKey: 'types-depenses' } },
      { path: 'types-depenses/:id', component: GenericFormComponent, data: { entityKey: 'types-depenses' } },

      { path: 'depenses', component: GenericListComponent, data: { entityKey: 'depenses' } },
      { path: 'depenses/:id', component: GenericFormComponent, data: { entityKey: 'depenses' } },

      { path: 'budgets', component: GenericListComponent, data: { entityKey: 'budgets' } },
      { path: 'budgets/:id', component: GenericFormComponent, data: { entityKey: 'budgets' } },

      { path: 'periodes', component: GenericListComponent, data: { entityKey: 'periodes' } },
      { path: 'periodes/:id', component: GenericFormComponent, data: { entityKey: 'periodes' } },

      { path: 'coefficients', component: GenericListComponent, data: { entityKey: 'coefficients' } },
      { path: 'coefficients/:id', component: GenericFormComponent, data: { entityKey: 'coefficients' } },

      { path: 'affectations', component: GenericListComponent, data: { entityKey: 'affectations' } },
      { path: 'affectations/:id', component: GenericFormComponent, data: { entityKey: 'affectations' } },

      { path: 'utilisateurs', component: GenericListComponent, data: { entityKey: 'utilisateurs' } },
      { path: 'utilisateurs/:id', component: GenericFormComponent, data: { entityKey: 'utilisateurs' } },

      // Consultation seule : pas de route de formulaire.
      { path: 'bulletins', component: GenericListComponent, data: { entityKey: 'bulletins' } },
      { path: 'recus', component: GenericListComponent, data: { entityKey: 'recus' } },

      { path: 'communications', component: GenericListComponent, data: { entityKey: 'communications' } },
      { path: 'communications/:id', component: GenericFormComponent, data: { entityKey: 'communications' } },

      { path: 'documents', component: GenericListComponent, data: { entityKey: 'documents' } },
      { path: 'documents/:id', component: GenericFormComponent, data: { entityKey: 'documents' } },

      { path: 'parametres', component: GenericListComponent, data: { entityKey: 'parametres' } },
      { path: 'parametres/:id', component: GenericFormComponent, data: { entityKey: 'parametres' } },

      // Consultation seule : pas de route de formulaire pour un journal d'audit.
      { path: 'journal-activite', component: GenericListComponent, data: { entityKey: 'journal-activite' } },
    ],
  },

  { path: '**', redirectTo: 'tableau-de-bord' },
];
