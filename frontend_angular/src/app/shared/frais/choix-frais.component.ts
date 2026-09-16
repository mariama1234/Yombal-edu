import { Component, computed, input, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

/** Un frais tel que le bloc l'affiche : le montant est déjà celui du niveau. */
export interface FraisAffiche {
  id: number;
  libelle: string;
  nature: 'inscription' | 'mensualite' | 'autre';
  /** Montant pour le niveau concerné, ou montant par défaut du frais. */
  montant: number;
  /** Vrai quand le montant vient de la grille des tarifs du niveau. */
  tarifNiveau: boolean;
  mensuel: boolean;
  /** Frais que la famille avait l'an passé (réinscription). */
  reconduit?: boolean;
}

/** Au-delà, les frais au choix se cherchent et défilent dans leur propre zone. */
const SEUIL_RECHERCHE = 8;

const ORDRE_NATURES: Record<FraisAffiche['nature'], number> = { inscription: 0, mensualite: 1, autre: 2 };

/**
 * Le bloc « Frais » de l'inscription et de la réinscription.
 *
 * Un seul composant pour les deux écrans : un établissement qui déclare
 * trente frais doit les retrouver présentés de la même façon aux deux
 * endroits. Les frais dus d'office viennent en tête ; les frais au choix
 * suivent en cartes, avec une recherche et une zone qui défile dès qu'ils sont
 * nombreux, et les frais cochés restent visibles en étiquettes même quand la
 * recherche les masque.
 *
 * Le composant n'enregistre rien : il affiche, et signale chaque case cochée
 * ou décochée à l'écran qui l'accueille.
 */
@Component({
  selector: 'app-choix-frais',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './choix-frais.component.html',
  styles: [`
    :host { display: block; }
    .vide {
      border: 1px dashed var(--line);
      border-radius: 12px;
      padding: 18px;
      font-size: 13px;
      color: var(--ink-400);
      text-align: center;
    }
    .titre {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: var(--ink-400);
    }
    .titre-bloc { margin: 4px 0 8px; }
    .titre-ligne {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      margin: 18px 0 8px;
    }
    .compte {
      margin-left: 6px;
      font-weight: 600;
      letter-spacing: 0;
      text-transform: none;
      color: var(--ink-600);
    }
    .outils { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .recherche { width: 220px; padding: 7px 11px; font-size: 13px; }
    .grille {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 10px;
    }
    .grille.defilante {
      max-height: 360px;
      overflow-y: auto;
      padding: 2px 6px 2px 2px;
    }
    .carte {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
      padding: 11px 13px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--surface);
      font-weight: 400;
    }
    label.carte { cursor: pointer; }
    .carte.fixe { background: var(--bg); }
    .carte.cochee { border-color: var(--orange-500); box-shadow: 0 0 0 1px var(--orange-500); }
    .carte input { width: 17px; height: 17px; flex-shrink: 0; accent-color: var(--orange-500); }
    .texte { flex: 1; min-width: 0; }
    .nom {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 13.5px;
      font-weight: 600;
      color: var(--ink-900);
    }
    .meta { font-size: 11px; color: var(--ink-400); }
    .reconduit { color: var(--orange-500); font-weight: 600; }
    .montant { font-size: 13px; font-weight: 600; white-space: nowrap; }
    .etiquettes { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
    .etiquette {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border: 1px solid var(--orange-500);
      border-radius: 20px;
      background: var(--orange-100);
      color: var(--orange-500);
      font-size: 12px;
      font-weight: 600;
    }
    .alerte { margin: 0; font-size: 12.5px; color: var(--red); }
    .vide-recherche { grid-column: 1 / -1; margin: 6px 0; font-size: 13px; color: var(--ink-400); }
    .total {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 18px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }
    .total-titre { font-size: 14px; font-weight: 600; }
    .total-detail { font-size: 12px; color: var(--ink-400); }
    .total-montant { font-size: 24px; font-weight: 700; }
    @media (max-width: 560px) {
      .outils, .recherche { width: 100%; }
      .grille.defilante { max-height: 300px; }
      .total-montant { font-size: 20px; }
    }
  `],
})
export class ChoixFraisComponent {
  frais = input.required<FraisAffiche[]>();
  coches = input.required<number[]>();
  /** Niveau (ou classe) dont les montants sont affichés ; `null` tant qu'il n'est pas connu. */
  niveau = input<string | null>(null);
  messageSansNiveau = input('Choisis d’abord le niveau : le montant de chaque frais en dépend.');
  titreTotal = input('Total à payer à l’inscription');
  /** Faux pour n'ajouter que des frais au choix (élève déjà inscrit). */
  afficherDOffice = input(true);

  basculer = output<number>();
  toutDecocher = output<void>();

  recherche = signal('');

  dOffice = computed(() =>
    this.frais()
      .filter((f) => f.nature !== 'autre')
      .sort((a, b) => ORDRE_NATURES[a.nature] - ORDRE_NATURES[b.nature]));

  auChoix = computed(() =>
    this.frais()
      .filter((f) => f.nature === 'autre')
      .sort((a, b) => a.libelle.localeCompare(b.libelle)));

  rechercheUtile = computed(() => this.auChoix().length > SEUIL_RECHERCHE);

  auChoixFiltres = computed(() => {
    const terme = this.recherche().trim().toLowerCase();
    return terme ? this.auChoix().filter((f) => f.libelle.toLowerCase().includes(terme)) : this.auChoix();
  });

  cochesListe = computed(() => this.auChoix().filter((f) => this.coches().includes(f.id)));
  nombreCoches = computed(() => this.cochesListe().length);

  totalDOffice = computed(() => this.dOffice().reduce((somme, f) => somme + f.montant, 0));
  totalOptions = computed(() => this.cochesListe().reduce((somme, f) => somme + f.montant, 0));
  total = computed(() => this.totalDOffice() + this.totalOptions());

  estCoche(id: number): boolean {
    return this.coches().includes(id);
  }

  origine(frais: FraisAffiche): string {
    return frais.tarifNiveau ? `Tarif ${this.niveau()}` : 'Montant par défaut';
  }
}
