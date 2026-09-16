import { HttpContextToken, HttpInterceptorFn, HttpRequest, HttpResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { ToastService } from '../services/toast.service';

/** À poser sur une requête d'écriture qui ne doit rien annoncer. */
export const SANS_TOAST = new HttpContextToken<boolean>(() => false);

const MESSAGES: Record<string, string> = {
  POST: 'Enregistrement effectué',
  PUT: 'Modifications enregistrées',
  PATCH: 'Modifications enregistrées',
  DELETE: 'Suppression effectuée',
};

/**
 * Confirme chaque enregistrement réussi par une notification.
 *
 * Posé ici plutôt que dans chaque écran : un module ajouté demain annoncera
 * ses enregistrements sans qu'on y pense. L'écran qui a un message plus précis
 * l'affiche lui-même, et le message générique s'efface (voir `ToastService`).
 */
export const toastInterceptor: HttpInterceptorFn = (req, next) => {
  const toast = inject(ToastService);
  return next(req).pipe(
    tap((evenement) => {
      if (evenement instanceof HttpResponse && estEnregistrement(req)) {
        toast.parDefaut(MESSAGES[req.method]);
      }
    }),
  );
};

function estEnregistrement(req: HttpRequest<unknown>): boolean {
  if (!(req.method in MESSAGES) || req.context.get(SANS_TOAST)) return false;
  // Connexion et rafraîchissement du jeton n'enregistrent rien.
  if (!req.url.startsWith(environment.apiUrl) || req.url.includes('/auth/')) return false;

  // Les aperçus (génération de frais, réinscription, relances, import) passent
  // par un POST mais n'écrivent rien : seule la validation s'annonce.
  const corps = req.body;
  if (corps instanceof FormData) {
    return !req.url.includes('/importer/') || corps.get('valider') === 'true';
  }
  if (corps && typeof corps === 'object') {
    const drapeaux = corps as Record<string, unknown>;
    if (drapeaux['valider'] === false || drapeaux['appliquer'] === false) return false;
  }
  return true;
}
