import { inject } from '@angular/core';
import { ActivatedRouteSnapshot, CanActivateFn, Router } from '@angular/router';
import { catchError, map, of } from 'rxjs';
import { Module } from '../models/models';
import { AuthService } from '../services/auth.service';

/**
 * Module de la matrice dont relève chaque écran du CRUD générique.
 *
 * `null` : ouvert à tout compte connecté.
 */
export const MODULE_DE_L_ENTITE: Record<string, Module | null> = {
  'annees-scolaires': 'annees_scolaires',
  periodes: 'annees_scolaires',
  niveaux: 'classes',
  classes: 'classes',
  salles: 'classes',
  'conseils-classe': 'bulletins',
  parents: 'parents',
  matieres: 'matieres',
  coefficients: 'matieres',
  affectations: 'matieres',
  'emploi-du-temps': 'emploi_du_temps',
  presences: 'presences',
  evaluations: 'notes',
  notes: 'notes',
  personnel: 'personnel',
  'types-frais': 'types_frais',
  'types-depenses': 'types_depenses',
  depenses: 'depenses',
  utilisateurs: 'comptes',
  bulletins: 'bulletins',
  recus: 'frais',
  communications: 'communications',
  // Chacun lit les documents qui lui sont destinés ; la direction seule écrit.
  documents: 'documents',
  parametres: 'parametres',
  'journal-activite': 'comptes',
};

/** Module d'une route : `data.module`, sinon celui de son entité générique. */
export function moduleDeLaRoute(route: ActivatedRouteSnapshot): Module | null {
  if (route.data['module'] !== undefined) return route.data['module'];
  const entite = route.data['entityKey'];
  return entite ? MODULE_DE_L_ENTITE[entite] ?? null : null;
}

/**
 * Ouvre une page selon la matrice des autorisations.
 *
 * Données de route lues :
 * - `module` (ou `entityKey`) : le module à pouvoir lire ;
 * - `ecriture: true` : la page sert à créer ou modifier, il faut pouvoir écrire ;
 * - `personnel: true` : vue sur toute l'école, fermée aux comptes famille.
 *
 * Après un rafraîchissement, le profil n'est pas encore chargé : on l'attend
 * plutôt que de refuser à tort.
 */
export const accesGuard: CanActivateFn = (route) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const autorise = (): boolean => {
    const module = moduleDeLaRoute(route);
    if (route.data['personnel'] && auth.estFamille()) return false;
    return route.data['ecriture'] ? auth.peutEcrire(module) : auth.peutLire(module);
  };
  // Le super administrateur n'a rien à faire dans une école : il retourne à
  // la plateforme (sinon le tableau de bord, refusé lui aussi, bouclerait).
  const refus = () => router.createUrlTree([auth.estSuperAdmin() ? '/plateforme' : '/tableau-de-bord']);

  if (auth.currentUser()) return autorise() || refus();
  return auth.chargerProfil().pipe(
    map(() => autorise() || refus()),
    catchError(() => of(refus())),
  );
};
