import { Component, EventEmitter, Input, Output } from '@angular/core';
import { AlerteAge } from './alerte-age';

/**
 * Pop-up de l'âge hors fourchette, à l'inscription comme à la réinscription.
 *
 * Validé avec le client : le message, puis deux boutons — « L'inscrire quand
 * même » (la dérogation est tracée dans l'historique de l'élève) et « Annuler
 * l'inscription » (rien n'est enregistré). Quand l'école bloque, ou qu'il
 * manque la date de naissance, seul le second est proposé.
 */
@Component({
  selector: 'app-alerte-age-dialog',
  standalone: true,
  template: `
    <div (click)="annuler.emit()"
         style="position:fixed; inset:0; background:rgba(15,23,42,0.45); z-index:1000; display:flex; align-items:flex-start; justify-content:center; padding:72px 16px; overflow-y:auto;">
      <div role="alertdialog" aria-modal="true" aria-labelledby="titre-alerte-age" aria-describedby="message-alerte-age"
           (click)="$event.stopPropagation()"
           style="background:var(--surface); border-radius:14px; width:100%; max-width:520px; padding:22px 24px; box-shadow:0 20px 50px rgba(0,0,0,0.25);">
        <div id="titre-alerte-age" class="form-section-title" style="margin:0 0 10px; color:var(--orange-500);">
          {{ titre() }}
        </div>
        <p id="message-alerte-age" style="font-size:14px; line-height:1.5; margin:0 0 12px;">{{ alerte.message }}</p>
        @if (alerte.confirmable) {
          <p class="hint" style="margin:0 0 16px;">
            Si vous l'inscrivez quand même, la dérogation est enregistrée dans l'historique de l'élève,
            avec votre nom.
          </p>
        } @else if (alerte.code === 'age_hors_fourchette') {
          <p class="hint" style="margin:0 0 16px;">
            L'école n'accepte pas d'inscription hors de la fourchette d'âge : choisissez une classe d'un autre niveau.
          </p>
        } @else {
          <p class="hint" style="margin:0 0 16px;">Renseignez la date de naissance, puis recommencez.</p>
        }
        <div class="form-actions" style="margin-top:0;">
          <button type="button" class="btn-ghost" (click)="annuler.emit()">Annuler l'inscription</button>
          @if (alerte.confirmable) {
            <button type="button" class="btn-primary" [disabled]="occupe" (click)="confirmer.emit()">
              {{ occupe ? 'Inscription…' : libelleConfirmer }}
            </button>
          }
        </div>
      </div>
    </div>
  `,
})
export class AlerteAgeDialogComponent {
  @Input({ required: true }) alerte!: AlerteAge;
  @Input() occupe = false;
  @Input() libelleConfirmer = "L'inscrire quand même";
  @Output() confirmer = new EventEmitter<void>();
  @Output() annuler = new EventEmitter<void>();

  titre(): string {
    return this.alerte.code === 'date_naissance_manquante'
      ? 'Date de naissance manquante'
      : 'Âge hors de la fourchette du niveau';
  }
}
