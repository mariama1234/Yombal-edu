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

      // ---- Finances (module dédié : impayés, encaissement, journal) ----
      {
        path: 'finances',
        loadComponent: () => import('./features/finances/finances-list.component').then((m) => m.FinancesListComponent),
      },
      {
        path: 'finances/encaisser/:idFrais',
        loadComponent: () => import('./features/finances/paiement-form.component').then((m) => m.PaiementFormComponent),
      },
      {
        path: 'finances/journal',
        loadComponent: () => import('./features/finances/journal-caisse.component').then((m) => m.JournalCaisseComponent),
      },

      // ---- Modules via le système CRUD générique ----
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

      { path: 'notes', component: GenericListComponent, data: { entityKey: 'notes' } },
      { path: 'notes/:id', component: GenericFormComponent, data: { entityKey: 'notes' } },

      { path: 'personnel', component: GenericListComponent, data: { entityKey: 'personnel' } },
      { path: 'personnel/:id', component: GenericFormComponent, data: { entityKey: 'personnel' } },

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
