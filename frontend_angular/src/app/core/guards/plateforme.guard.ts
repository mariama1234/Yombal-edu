import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, map, of } from 'rxjs';
import { AuthService } from '../services/auth.service';

/**
 * Sépare les deux espaces : la plateforme au super administrateur, les écoles
 * à tous les autres. Chacun est ramené chez lui s'il se trompe d'adresse.
 *
 * Après un rafraîchissement, le profil n'est pas encore chargé : on l'attend
 * plutôt que de décider sur un rôle inconnu.
 */
function espace(pourSuperAdmin: boolean): CanActivateFn {
  return () => {
    const auth = inject(AuthService);
    const router = inject(Router);
    const decider = () => auth.estSuperAdmin() === pourSuperAdmin
      || router.createUrlTree([auth.accueil()]);
    if (auth.currentUser()) return decider();
    return auth.chargerProfil().pipe(
      map(() => decider()),
      catchError(() => of(router.createUrlTree(['/connexion']))),
    );
  };
}

/** Pages de la plateforme : super administrateur seulement. */
export const plateformeGuard = espace(true);

/** Pages d'une école : tout compte, sauf le super administrateur. */
export const ecoleGuard = espace(false);
