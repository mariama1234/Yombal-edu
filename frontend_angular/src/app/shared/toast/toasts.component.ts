import { Component, inject } from '@angular/core';
import { ToastService } from '../../core/services/toast.service';

/**
 * La pile de notifications, posée une fois pour toute l'application.
 *
 * En haut à droite sur grand écran, en bas et sur toute la largeur sur
 * téléphone, là où le pouce la ferme. Un clic la ferme ; sinon elle s'efface
 * seule, la barre du bas montrant le temps qui reste.
 */
@Component({
  selector: 'app-toasts',
  standalone: true,
  template: `
    <div class="pile" role="status" aria-live="polite">
      @for (t of service.toasts(); track t.id) {
        <div class="toast" [class.succes]="t.genre === 'succes'" [class.info]="t.genre === 'info'"
             [class.erreur]="t.genre === 'erreur'" (click)="service.fermer(t.id)">
          <span class="icone" aria-hidden="true">
            @switch (t.genre) {
              @case ('succes') {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>
              }
              @case ('erreur') {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M12 7v6M12 17h.01"/></svg>
              }
              @default {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M12 11v6M12 7h.01"/></svg>
              }
            }
          </span>
          <div class="texte">
            <div class="titre">{{ t.titre }}</div>
            @if (t.detail) { <div class="detail">{{ t.detail }}</div> }
          </div>
          <button type="button" class="fermer" aria-label="Fermer la notification"
                  (click)="service.fermer(t.id); $event.stopPropagation()">×</button>
          <span class="barre" [style.animation-duration.ms]="t.duree"></span>
        </div>
      }
    </div>
  `,
  styles: [`
    .pile {
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 10000;
      display: flex;
      flex-direction: column;
      gap: 10px;
      width: min(380px, calc(100vw - 32px));
      pointer-events: none;
    }
    .toast {
      position: relative;
      overflow: hidden;
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 13px 14px 15px;
      border: 1px solid var(--line);
      border-left: 4px solid var(--green);
      border-radius: 12px;
      background: var(--surface);
      box-shadow: 0 10px 30px rgba(15, 26, 43, 0.16);
      cursor: pointer;
      pointer-events: auto;
      animation: entree 0.22s ease-out;
    }
    .toast.info { border-left-color: var(--orange-500); }
    .toast.erreur { border-left-color: var(--red); }
    .icone {
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: var(--green-bg);
      color: var(--green);
    }
    .info .icone { background: var(--orange-100); color: var(--orange-500); }
    .erreur .icone { background: var(--red-bg); color: var(--red); }
    .texte { flex: 1; min-width: 0; padding-top: 3px; }
    .titre { font-size: 13.5px; font-weight: 600; color: var(--ink-900); line-height: 1.35; }
    .detail { margin-top: 2px; font-size: 12.5px; color: var(--ink-600); line-height: 1.4; }
    .fermer {
      flex-shrink: 0;
      border: none;
      background: none;
      padding: 0 2px;
      font-size: 20px;
      line-height: 1;
      color: var(--ink-400);
    }
    .fermer:hover { color: var(--ink-900); }
    .barre {
      position: absolute;
      left: 0;
      bottom: 0;
      height: 3px;
      width: 100%;
      background: currentColor;
      color: var(--green);
      opacity: 0.35;
      transform-origin: left;
      animation: temps linear forwards;
    }
    .info .barre { color: var(--orange-500); }
    .erreur .barre { color: var(--red); }
    @keyframes entree {
      from { opacity: 0; transform: translateY(-8px) scale(0.98); }
      to { opacity: 1; transform: none; }
    }
    @keyframes temps {
      from { transform: scaleX(1); }
      to { transform: scaleX(0); }
    }
    @media (max-width: 560px) {
      .pile { top: auto; bottom: 16px; left: 16px; right: 16px; width: auto; flex-direction: column-reverse; }
    }
    @media (prefers-reduced-motion: reduce) {
      .toast { animation: none; }
    }
  `],
})
export class ToastsComponent {
  service = inject(ToastService);
}
