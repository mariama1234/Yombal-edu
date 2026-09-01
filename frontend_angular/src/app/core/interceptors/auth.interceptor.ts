import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, switchMap, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';

/**
 * Intercepteur fonctionnel (style Angular 15+) :
 * - ajoute automatiquement l'en-tête Authorization sur chaque requête vers l'API
 * - si le serveur répond 401 (token expiré), tente un rafraîchissement silencieux
 *   puis rejoue la requête ; si le rafraîchissement échoue, déconnecte l'utilisateur.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = auth.accessToken;

  const authReq = token
    ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
    : req;

  return next(authReq).pipe(
    catchError((error: HttpErrorResponse) => {
      if (error.status === 401 && !req.url.includes('/auth/token')) {
        return auth.refreshToken().pipe(
          switchMap(() => {
            const retryReq = req.clone({
              setHeaders: { Authorization: `Bearer ${auth.accessToken}` },
            });
            return next(retryReq);
          }),
          catchError((refreshError) => {
            auth.logout();
            return throwError(() => refreshError);
          })
        );
      }
      return throwError(() => error);
    })
  );
};
