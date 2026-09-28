import { Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { abregerMontant, formaterValeur, FormatValeur } from './format';

/** Une part de l'anneau. */
export interface Segment {
  libelle: string;
  valeur: number;
  /** Couleur de la part : un jeton CSS (`var(--serie-1)`) plutôt qu'un hexa. */
  couleur: string;
  /** Page qui détaille cette part, ouverte depuis la légende. */
  lien?: { chemin: string; params?: Record<string, string> };
}

/** Une part prête à dessiner : son arc et sa part du total. */
interface Arc extends Segment {
  chemin: string;
  part: number;
}

// Géométrie de l'anneau, en unités du viewBox : un anneau fin, le centre
// garde la place du total.
const TAILLE = 180;
const RAYON = 70;
const EPAISSEUR = 22;
// 2 px de fond entre deux parts voisines, convertis en angle sur le rayon.
const ECART = 2 / RAYON;

/**
 * Graphique circulaire : la répartition d'un total en quelques parts.
 *
 * Réservé au part-dans-le-tout à six parts au plus : au-delà, ou pour
 * comparer des valeurs proches, des barres se lisent mieux. La légende porte
 * chaque valeur et sa part en toutes lettres — la couleur seule ne suffit
 * pas, et trois des teintes claires contrastent peu sur le blanc.
 */
@Component({
  selector: 'app-graphe-anneau',
  standalone: true,
  imports: [RouterLink],
  template: `
    @if (total() > 0) {
      <div class="anneau">
        <svg [attr.viewBox]="'0 0 ' + taille + ' ' + taille" class="anneau-svg"
             role="img" [attr.aria-label]="description()">
          @for (a of arcs(); track a.libelle; let i = $index) {
            <path [attr.d]="a.chemin" fill="none" [attr.stroke]="a.couleur"
                  [attr.stroke-width]="survole() === i ? epaisseur + 4 : epaisseur"
                  [attr.opacity]="survole() !== null && survole() !== i ? 0.35 : 1"
                  (mouseenter)="survole.set(i)" (mouseleave)="survole.set(null)">
              <title>{{ a.libelle }} : {{ valeur(a.valeur) }} ({{ a.part }} %)</title>
            </path>
          }
          @if (enAvant(); as a) {
            <text [attr.x]="centre" [attr.y]="centre - 6" text-anchor="middle"
                  class="anneau-total">{{ a.part }} %</text>
            <text [attr.x]="centre" [attr.y]="centre + 14" text-anchor="middle"
                  class="anneau-legende-centre">{{ tronquer(a.libelle) }}</text>
          } @else {
            <text [attr.x]="centre" [attr.y]="centre - 6" text-anchor="middle"
                  class="anneau-total">{{ totalAbrege() }}</text>
            <text [attr.x]="centre" [attr.y]="centre + 14" text-anchor="middle"
                  class="anneau-legende-centre">{{ libelleTotal() }}</text>
          }
        </svg>

        <ul class="anneau-legende">
          @for (a of arcs(); track a.libelle; let i = $index) {
            <li [class.estompe]="survole() !== null && survole() !== i"
                (mouseenter)="survole.set(i)" (mouseleave)="survole.set(null)">
              <i [style.background]="a.couleur"></i>
              @if (a.lien) {
                <a class="anneau-nom panel-link" [routerLink]="a.lien.chemin"
                   [queryParams]="a.lien.params ?? {}">{{ a.libelle }}</a>
              } @else {
                <span class="anneau-nom">{{ a.libelle }}</span>
              }
              <span class="anneau-valeur mono">{{ valeur(a.valeur) }}</span>
              <span class="anneau-part mono">{{ a.part }} %</span>
            </li>
          }
        </ul>
      </div>
    } @else {
      <p class="graphe-vide">{{ vide() }}</p>
    }
  `,
})
export class GrapheAnneauComponent {
  segments = input<Segment[]>([]);
  format = input<FormatValeur>('nombre');
  /** Ce que désigne le total au centre, ex: « élèves », « encaissés ». */
  libelleTotal = input('total');
  vide = input('Rien à répartir pour le moment.');

  readonly taille = TAILLE;
  readonly centre = TAILLE / 2;
  readonly epaisseur = EPAISSEUR;

  survole = signal<number | null>(null);

  total = computed(() => this.segments().reduce((t, s) => t + Math.max(0, s.valeur), 0));

  arcs = computed<Arc[]>(() => {
    const total = this.total();
    const visibles = this.segments().filter((s) => s.valeur > 0);
    if (!total) return [];
    // Les parts s'enroulent dans le sens horaire depuis midi.
    let depart = -Math.PI / 2;
    return visibles.map((s) => {
      const angle = (s.valeur / total) * Math.PI * 2;
      const arc: Arc = {
        ...s,
        part: Math.round((s.valeur / total) * 1000) / 10,
        chemin: visibles.length === 1
          ? this.cercle()
          : this.arc(depart + ECART / 2, depart + angle - ECART / 2),
      };
      depart += angle;
      return arc;
    });
  });

  /** Part survolée, montrée au centre à la place du total. */
  enAvant = computed(() => {
    const i = this.survole();
    return i === null ? null : this.arcs()[i] ?? null;
  });

  totalAbrege = computed(() => this.format() === 'montant'
    ? abregerMontant(this.total())
    : formaterValeur(this.total(), 'nombre'));

  description = computed(() => this.arcs()
    .map((a) => `${a.libelle} ${this.valeur(a.valeur)} (${a.part} %)`).join(', '));

  valeur(v: number): string {
    return formaterValeur(v, this.format());
  }

  tronquer(texte: string): string {
    return texte.length > 18 ? texte.slice(0, 17) + '…' : texte;
  }

  private point(angle: number): string {
    return `${this.centre + RAYON * Math.cos(angle)} ${this.centre + RAYON * Math.sin(angle)}`;
  }

  private arc(debut: number, fin: number): string {
    // Une part minuscule garde un trait visible plutôt que de disparaître.
    const finVisible = Math.max(fin, debut + 0.01);
    const grand = finVisible - debut > Math.PI ? 1 : 0;
    return `M ${this.point(debut)} A ${RAYON} ${RAYON} 0 ${grand} 1 ${this.point(finVisible)}`;
  }

  private cercle(): string {
    const haut = this.centre - RAYON;
    const bas = this.centre + RAYON;
    return `M ${this.centre} ${haut} A ${RAYON} ${RAYON} 0 1 1 ${this.centre} ${bas}`
         + ` A ${RAYON} ${RAYON} 0 1 1 ${this.centre} ${haut}`;
  }
}
