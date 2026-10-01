import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../../core/services/auth.service';

/**
 * Mise en page de la plateforme : un bandeau, sans menu d'école.
 *
 * Ni sélecteur d'année ni recherche d'élèves : le super administrateur ne voit
 * pas l'intérieur des écoles, et ces éléments appelleraient des API qui lui
 * sont fermées.
 */
@Component({
  selector: 'app-plateforme-layout',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  template: `
    <header class="pf-bandeau">
      <div class="pf-bandeau-interieur">
        <a class="pf-marque" routerLink="/plateforme" aria-label="Accueil de la plateforme">
          <span class="brand-mark"><img src="assets/logo-yombaledu.png" alt=""></span>
          <span>
            <span class="pf-marque-nom">YombalEdu</span>
            <span class="pf-marque-sous">Console plateforme</span>
          </span>
        </a>

        <nav class="pf-nav" aria-label="Plateforme">
          <a routerLink="/plateforme" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6"/></svg>
            Écoles
          </a>
          <a routerLink="/plateforme/ecoles/nouvelle" routerLinkActive="active">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 5v14M5 12h14"/></svg>
            Nouvelle école
          </a>
        </nav>

        <div class="pf-compte">
          <div class="pf-compte-texte">
            <span class="pf-compte-nom">{{ nomComplet() }}</span>
            <span class="pf-compte-role">Super administrateur</span>
          </div>
          <span class="pf-avatar" aria-hidden="true">{{ initiales() }}</span>
          <button class="pf-deconnexion" title="Se déconnecter" aria-label="Se déconnecter" (click)="auth.logout()">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M15 17l5-5-5-5"/><path d="M20 12H9"/><path d="M12 19H6a2 2 0 01-2-2V7a2 2 0 012-2h6"/></svg>
          </button>
        </div>
      </div>
    </header>
    <main class="pf-contenu">
      <router-outlet></router-outlet>
    </main>
  `,
})
export class PlateformeLayoutComponent {
  auth = inject(AuthService);

  nomComplet = computed(() => {
    const u = this.auth.currentUser();
    return u ? `${u.prenom} ${u.nom}` : '';
  });

  initiales = computed(() => {
    const u = this.auth.currentUser();
    return u ? `${u.prenom[0] ?? ''}${u.nom[0] ?? ''}`.toUpperCase() : '';
  });
}
