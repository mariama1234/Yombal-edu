import { Component, computed, input, signal } from '@angular/core';
import { abregerMontant, formaterValeur, FormatValeur, pasDeGraduation } from './format';

/** Une colonne de l'histogramme. */
export interface Colonne {
  libelle: string;
  valeur: number;
  /** Colonne à signaler (classe pleine, mention sous la moyenne…). */
  accent?: boolean;
  /** Précision affichée au survol, ex: « 32 / 35 places ». */
  detail?: string;
}

interface ColonneTracee extends Colonne {
  chemin: string;
  x: number;
  y: number;
  centre: number;
  zoneX: number;
  zoneLargeur: number;
}

// Géométrie en unités du viewBox ; la largeur suit le nombre de colonnes.
const HAUTEUR = 230;
const HAUT = 22;
const GAUCHE = 52;
const DROITE = 12;
const LARGEUR_MIN = 520;
const PAS_MIN = 48;
const EPAISSEUR_MAX = 24;
const ARRONDI = 4;

/**
 * Histogramme en colonnes verticales : une seule série, comparée de colonne
 * en colonne.
 *
 * Une seule teinte, puisque les colonnes mesurent la même chose ; seules les
 * colonnes signalées (`accent`) prennent la seconde, nommée dans la légende.
 * Les valeurs ne sont pas écrites sur chaque colonne : la plus haute porte
 * la sienne, les autres se lisent au survol, sous le graphique — lisible
 * aussi au doigt et au clavier, contrairement à une bulle flottante.
 */
@Component({
  selector: 'app-graphe-colonnes',
  standalone: true,
  template: `
    @if (colonnes().length > 0) {
      @if (libelleAccent() && aDesAccents()) {
        <div class="legende" style="margin-bottom:8px;">
          <span class="legende-cle"><i style="background:var(--serie-1);"></i>{{ libelleSerie() }}</span>
          <span class="legende-cle"><i style="background:var(--serie-2);"></i>{{ libelleAccent() }}</span>
        </div>
      }
      <div style="overflow-x:auto;">
        <svg [attr.viewBox]="'0 0 ' + largeur() + ' ' + hauteurTotale()"
             [style.min-width.px]="largeurMin()"
             style="width:100%; height:auto; display:block;"
             role="img" [attr.aria-label]="description()">
          @for (g of graduations(); track g.y) {
            <line [attr.x1]="gauche" [attr.x2]="largeur() - droite" [attr.y1]="g.y" [attr.y2]="g.y"
                  stroke="var(--line)" stroke-width="1"></line>
            <text [attr.x]="gauche - 8" [attr.y]="g.y + 4" text-anchor="end"
                  font-size="10.5" fill="var(--ink-400)">{{ graduation(g.valeur) }}</text>
          }

          @for (c of trace(); track c.libelle; let i = $index) {
            <g (mouseenter)="survole.set(i)" (mouseleave)="survole.set(null)"
               [attr.opacity]="survole() !== null && survole() !== i ? 0.45 : 1">
              <!-- Zone de survol plus large que la colonne. -->
              <rect [attr.x]="c.zoneX" [attr.y]="haut" [attr.width]="c.zoneLargeur"
                    [attr.height]="base - haut" fill="transparent"></rect>
              <path [attr.d]="c.chemin" [attr.fill]="c.accent ? 'var(--serie-2)' : 'var(--serie-1)'"></path>
              <title>{{ c.libelle }} : {{ valeur(c.valeur) }}{{ c.detail ? ' — ' + c.detail : '' }}</title>
            </g>
            @if (i === indexMax() || i === survole()) {
              <text [attr.x]="c.centre" [attr.y]="c.y - 6" text-anchor="middle"
                    font-size="11" font-weight="600" fill="var(--ink-900)">{{ valeurCourte(c.valeur) }}</text>
            }
            <text [attr.x]="c.centre" [attr.y]="base + 16"
                  [attr.text-anchor]="incline() ? 'end' : 'middle'"
                  [attr.transform]="incline() ? 'rotate(-35 ' + c.centre + ' ' + (base + 16) + ')' : null"
                  font-size="10.5" fill="var(--ink-600)">{{ tronquer(c.libelle) }}</text>
          }

          <line [attr.x1]="gauche" [attr.x2]="largeur() - droite" [attr.y1]="base" [attr.y2]="base"
                stroke="var(--ink-400)" stroke-width="1"></line>
        </svg>
      </div>

      <div class="detail-survol">
        @if (enAvant(); as c) {
          <strong>{{ c.libelle }}</strong>
          <span>{{ valeur(c.valeur) }}</span>
          @if (c.detail) { <span style="color:var(--ink-600);">{{ c.detail }}</span> }
        } @else {
          <span style="color:var(--ink-400);">Survolez une colonne pour le détail.</span>
        }
      </div>
    } @else {
      <p class="graphe-vide">{{ vide() }}</p>
    }
  `,
})
export class GrapheColonnesComponent {
  colonnes = input<Colonne[]>([]);
  format = input<FormatValeur>('nombre');
  /** Nom de la série, repris dans la légende quand des colonnes sont signalées. */
  libelleSerie = input('Valeur');
  /** Ce que signale la seconde teinte ; sans lui, pas de légende. */
  libelleAccent = input<string | null>(null);
  vide = input('Aucune donnée pour le moment.');

  readonly haut = HAUT;
  readonly gauche = GAUCHE;
  readonly droite = DROITE;
  readonly base = HAUTEUR - 8;

  survole = signal<number | null>(null);

  /** Au-delà de huit colonnes, les libellés s'inclinent pour ne pas se chevaucher. */
  incline = computed(() => this.colonnes().length > 8);
  hauteurTotale = computed(() => HAUTEUR + (this.incline() ? 62 : 26));
  largeurMin = computed(() => Math.max(320, this.colonnes().length * 36 + GAUCHE));
  largeur = computed(() => Math.max(LARGEUR_MIN, GAUCHE + DROITE + this.colonnes().length * PAS_MIN));

  private pas = computed(() => pasDeGraduation(
    Math.max(0, ...this.colonnes().map((c) => c.valeur)), this.format() === 'nombre'));
  private plafond = computed(() => this.pas() * 4);

  graduations = computed(() => [0, 1, 2, 3, 4].map((n) => ({
    valeur: this.pas() * n,
    y: this.base - ((this.base - HAUT) * n) / 4,
  })));

  aDesAccents = computed(() => this.colonnes().some((c) => c.accent));

  indexMax = computed(() => {
    const valeurs = this.colonnes().map((c) => c.valeur);
    const max = Math.max(...valeurs);
    return max > 0 ? valeurs.indexOf(max) : -1;
  });

  trace = computed<ColonneTracee[]>(() => {
    const colonnes = this.colonnes();
    const utile = this.largeur() - GAUCHE - DROITE;
    const pas = utile / colonnes.length;
    const epaisseur = Math.min(EPAISSEUR_MAX, pas * 0.6);
    const hauteurUtile = this.base - HAUT;
    return colonnes.map((c, i) => {
      const centre = GAUCHE + pas * i + pas / 2;
      const x = centre - epaisseur / 2;
      // Une valeur non nulle garde une colonne visible, si petite soit-elle.
      const h = c.valeur > 0 ? Math.max(2, (c.valeur / this.plafond()) * hauteurUtile) : 0;
      const y = this.base - h;
      return {
        ...c, x, y, centre,
        chemin: this.colonne(x, y, epaisseur, h),
        zoneX: GAUCHE + pas * i + 2,
        zoneLargeur: pas - 4,
      };
    });
  });

  enAvant = computed(() => {
    const i = this.survole();
    return i === null ? null : this.trace()[i] ?? null;
  });

  description = computed(() => this.colonnes()
    .map((c) => `${c.libelle} ${this.valeur(c.valeur)}`).join(', '));

  valeur(v: number): string {
    return formaterValeur(v, this.format());
  }

  valeurCourte(v: number): string {
    return this.format() === 'montant' ? abregerMontant(v) : formaterValeur(v, 'nombre');
  }

  graduation(v: number): string {
    return this.format() === 'montant' ? abregerMontant(v) : formaterValeur(v, 'nombre');
  }

  tronquer(texte: string): string {
    return texte.length > 14 ? texte.slice(0, 13) + '…' : texte;
  }

  /** Colonne à sommet arrondi (4 px), carrée contre la ligne de base. */
  private colonne(x: number, y: number, l: number, h: number): string {
    if (h <= 0) return '';
    const r = Math.min(ARRONDI, h, l / 2);
    return `M ${x} ${this.base} V ${y + r} Q ${x} ${y} ${x + r} ${y}`
         + ` H ${x + l - r} Q ${x + l} ${y} ${x + l} ${y + r} V ${this.base} Z`;
  }
}
