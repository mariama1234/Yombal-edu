import { Directive, Input, TemplateRef, ViewContainerRef, effect, inject } from '@angular/core';
import { Module } from '../../core/models/models';
import { AuthService } from '../../core/services/auth.service';

/**
 * N'affiche son contenu que si le profil a accès au module.
 *
 * « Aucun accès » doit être invisible, pas refusé au clic : un raccourci vers
 * un écran fermé n'a rien à faire à l'écran. Posée sur un lien ou un bouton,
 * cette directive le retire pour les profils qui n'y ont pas droit.
 *
 *     <a *siAcces="'depenses'" routerLink="/depenses">Dépenses</a>
 *     <a *siAcces="'frais:ecriture'" routerLink="/finances/generer">Facturer</a>
 *
 * Le serveur refuse de toute façon (403) : ceci ne décide de rien, il ne fait
 * que ne pas proposer ce qui serait refusé.
 */
@Directive({
  selector: '[siAcces]',
  standalone: true,
})
export class SiAccesDirective {
  private modele = inject(TemplateRef<unknown>);
  private conteneur = inject(ViewContainerRef);
  private auth = inject(AuthService);

  private module: Module | null = null;
  private ecriture = false;
  private affiche = false;

  constructor() {
    // Le profil arrive après coup au rafraîchissement de la page : la
    // directive suit le signal plutôt que de trancher une fois pour toutes.
    effect(() => {
      this.auth.currentUser();
      this.reevaluer();
    });
  }

  /** `'module'` pour la lecture, `'module:ecriture'` quand il faut pouvoir écrire. */
  @Input() set siAcces(valeur: string) {
    const [module, niveau] = valeur.split(':');
    this.module = (module as Module) || null;
    this.ecriture = niveau === 'ecriture';
    this.reevaluer();
  }

  private reevaluer(): void {
    const autorise = this.ecriture
      ? this.auth.peutEcrire(this.module)
      : this.auth.peutLire(this.module);
    if (autorise === this.affiche) return;
    this.affiche = autorise;
    this.conteneur.clear();
    if (autorise) this.conteneur.createEmbeddedView(this.modele);
  }
}
