import { ApplicationConfig, provideZoneChangeDetection } from '@angular/core';
import { provideRouter, withInMemoryScrolling } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';

import { routes } from './app.routes';
import { authInterceptor } from './core/interceptors/auth.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    // Changer d'écran remet en haut de page. Sans ça, passer d'un dossier
    // élève déroulé à « Finances » ouvrait le nouvel écran au milieu, sur un
    // bloc qui n'a rien à voir. `enabled` plutôt que `top` : le retour arrière
    // du navigateur rend, lui, la position qu'on avait quittée.
    provideRouter(routes, withInMemoryScrolling({
      scrollPositionRestoration: 'enabled',
    })),
    provideHttpClient(withInterceptors([authInterceptor])),
  ]
};
