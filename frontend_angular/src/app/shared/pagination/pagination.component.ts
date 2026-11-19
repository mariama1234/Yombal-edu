import { Component, computed, input, output } from '@angular/core';

/**
 * Pied de tableau commun à toutes les listes paginées par l'API.
 *
 * L'API renvoie vingt lignes à la fois (`PAGE_SIZE`) : sans ce pied, tout ce
 * qui dépasse la première page restait invisible. Le composant ne charge rien
 * lui-même ; il annonce la page voulue et la liste se recharge.
 *
 * Les boutons ne s'affichent que s'il y a plus d'une page, et le pied garde
 * la même hauteur d'une page à l'autre : le tableau ne « saute » pas.
 */
@Component({
  selector: 'app-pagination',
  standalone: true,
  template: `
    <div class="pagination">
      <span>
        @if (total() > 0) {
          {{ premier() }}–{{ dernier() }} sur {{ total() }} {{ libelle() }}
        } @else {
          0 {{ libelle() }}
        }
      </span>
      @if (nbPages() > 1) {
        <span class="pagination-boutons">
          <button type="button" class="btn-ghost" [disabled]="page() <= 1 || desactive()"
                  (click)="aller(page() - 1)" aria-label="Page précédente">← Précédent</button>
          <span class="pagination-numero">Page {{ page() }} / {{ nbPages() }}</span>
          <button type="button" class="btn-ghost" [disabled]="page() >= nbPages() || desactive()"
                  (click)="aller(page() + 1)" aria-label="Page suivante">Suivant →</button>
        </span>
      }
    </div>
  `,
})
export class PaginationComponent {
  /** Page affichée, à partir de 1. */
  page = input.required<number>();
  /** Nombre total de lignes, tel que renvoyé par l'API (`count`). */
  total = input.required<number>();
  taillePage = input(20);
  libelle = input('résultat(s)');
  /** Pendant un chargement : évite deux clics qui sauteraient deux pages. */
  desactive = input(false);

  changer = output<number>();

  nbPages = computed(() => Math.max(1, Math.ceil(this.total() / this.taillePage())));
  premier = computed(() => (this.page() - 1) * this.taillePage() + 1);
  dernier = computed(() => Math.min(this.page() * this.taillePage(), this.total()));

  aller(page: number): void {
    if (page < 1 || page > this.nbPages() || page === this.page()) return;
    this.changer.emit(page);
    // La pagination ne change pas d'URL : on remonte en haut du tableau.
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}
