import { Component, OnInit, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/services/auth.service';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import {
  Bilan,
  Classe,
  ImpayesParClasse,
  Paginated,
  SyntheseAssiduite,
  SyntheseEcole,
} from '../../core/models/models';
import { FinanceService } from '../finances/finance.service';

/** Une barre prête à dessiner, en coordonnées du viewBox. */
interface Barre {
  x: number;
  y: number;
  largeur: number;
  hauteur: number;
}

/** Un mois de l'histogramme recettes / dépenses. */
interface MoisTrace {
  cle: string;
  libelle: string;
  recettes: number;
  depenses: number;
  solde: number;
  barreRecettes: Barre;
  barreDepenses: Barre;
  centre: number;
}

/** Une classe de l'histogramme des effectifs. */
interface ClasseTrace {
  nom: string;
  effectif: number;
  capacite: number;
  largeur: number;
  remplissage: number;
  saturee: boolean;
}

const MOIS_COURTS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
                     'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

// Géométrie du graphique d'évolution, en unités du viewBox.
const G = { largeur: 720, hauteur: 240, gauche: 62, droite: 14, haut: 18, bas: 38 };

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './dashboard.component.html',
})
export class DashboardComponent implements OnInit {
  private http = inject(HttpClient);
  private finance = inject(FinanceService);
  auth = inject(AuthService);
  annees = inject(AnneeScolaireService);

  nbEleves = signal<number | null>(null);
  nbClasses = signal<number | null>(null);
  bilan = signal<Bilan | null>(null);
  classes = signal<Classe[]>([]);
  synthese = signal<SyntheseEcole | null>(null);
  impayes = signal<ImpayesParClasse | null>(null);
  assiduite = signal<SyntheseAssiduite | null>(null);

  /** Mentions dans l'ordre du barème, pour l'histogramme de répartition. */
  private readonly ORDRE_MENTIONS = ['Excellent', 'Très bien', 'Bien', 'Assez bien',
                                     'Insuffisant', 'Très insuffisant'];

  /** Mois survolé dans l'histogramme, pour le détail sous le graphique. */
  moisSurvole = signal<MoisTrace | null>(null);

  readonly geometrie = G;

  constructor() {
    // Le tableau de bord suit l'année choisie dans le bandeau haut : consulter
    // une promotion passée doit aussi montrer ses chiffres.
    effect(() => {
      this.annees.idAnnee();
      this.charger();
    });
  }

  ngOnInit(): void {
    this.charger();
  }

  private charger(): void {
    const params = this.annees.params();
    const requete = new URLSearchParams(
      Object.entries(params).map(([c, v]) => [c, String(v)]),
    ).toString();
    const suffixe = requete ? `?${requete}` : '';

    this.http
      .get<Paginated<unknown>>(`${environment.apiUrl}/eleves/${suffixe}`)
      .subscribe((r) => this.nbEleves.set(r.count));
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/${suffixe}`)
      .subscribe((r) => {
        this.nbClasses.set(r.count);
        this.classes.set(r.results ?? []);
      });

    // Le bilan porte déjà les chiffres financiers de l'année : une seule
    // requête remplace le comptage des impayés et le solde de caisse.
    this.finance.bilan(params).subscribe({
      next: (b) => this.bilan.set(b),
      error: () => this.bilan.set(null),
    });

    // Sans période précisée, le serveur retient celle en cours : le directeur
    // ouvre son tableau de bord sur le trimestre du moment.
    this.http
      .get<SyntheseEcole>(`${environment.apiUrl}/bulletins/synthese/${suffixe}`)
      .subscribe({
        next: (s) => this.synthese.set(s),
        error: () => this.synthese.set(null),
      });

    this.http
      .get<ImpayesParClasse>(
        `${environment.apiUrl}/frais-eleves/impayes/par-classe/${suffixe}`)
      .subscribe({
        next: (i) => this.impayes.set(i),
        // Le caissier n'est pas seul à ouvrir cet écran : un enseignant n'a
        // pas accès aux finances, et le panneau disparaît simplement.
        error: () => this.impayes.set(null),
      });

    this.http
      .get<SyntheseAssiduite>(`${environment.apiUrl}/absences/synthese/${suffixe}`)
      .subscribe({
        next: (a) => this.assiduite.set(a),
        error: () => this.assiduite.set(null),
      });
  }

  // ---- Recouvrement et assiduité --------------------------------------

  /** Les cinq classes qui doivent le plus : c'est par là qu'on commence. */
  classesDebitrices = computed(() => (this.impayes()?.classes ?? []).slice(0, 5));

  /** Part du reste dû, pour dimensionner les barres du panneau impayés. */
  partReste(reste: number): number {
    const maximum = Math.max(1, ...(this.impayes()?.classes ?? []).map((c) => c.reste));
    return (reste / maximum) * 100;
  }

  aDesImpayes = computed(() => (this.impayes()?.classes?.length ?? 0) > 0);

  aDeLAssiduite = computed(() => {
    const total = this.assiduite()?.total;
    return !!total && total.absences + total.retards > 0;
  });

  classesAbsenteistes = computed(() => (this.assiduite()?.classes ?? []).slice(0, 5));
  elevesAbsenteistes = computed(() => (this.assiduite()?.eleves ?? []).slice(0, 5));

  partAssiduite(nombre: number): number {
    const classes = this.assiduite()?.classes ?? [];
    const maximum = Math.max(1, ...classes.map((c) => c.absences + c.retards));
    return (nombre / maximum) * 100;
  }

  // ---- Résultats de l'école -------------------------------------------

  /** Vrai quand des notes existent : sinon le panneau annonce qu'il n'y en a pas. */
  aDesResultats = computed(() => (this.synthese()?.ensemble?.effectif ?? 0) > 0);

  mentions = computed(() => {
    const repartition = this.synthese()?.repartition ?? {};
    const total = Object.values(repartition).reduce((t, n) => t + n, 0);
    return this.ORDRE_MENTIONS.map((libelle) => {
      const nombre = repartition[libelle] ?? 0;
      return {
        libelle,
        nombre,
        part: total ? Math.round((nombre * 100) / total) : 0,
        // Sous la moyenne : c'est là que la direction doit regarder.
        faible: libelle === 'Insuffisant' || libelle === 'Très insuffisant',
      };
    });
  });

  /** Les cinq classes les plus en difficulté, celles où il faut aller voir. */
  classesFragiles = computed(() => {
    const classes = this.synthese()?.classes ?? [];
    return [...classes].reverse().slice(0, 5);
  });

  matieresFragiles = computed(() => (this.synthese()?.matieres ?? []).slice(0, 5));

  /** Progression d'une période à l'autre, en points de moyenne. */
  evolution = computed(() => {
    const lignes = this.synthese()?.evolution ?? [];
    return lignes.map((ligne, index) => {
      const precedente = index > 0 ? lignes[index - 1].moyenne : null;
      const ecart =
        precedente !== null && ligne.moyenne !== null
          ? Number(ligne.moyenne) - Number(precedente)
          : null;
      return { ...ligne, ecart };
    });
  });

  // ---- Histogramme recettes / dépenses --------------------------------

  /** Plafond de l'axe, arrondi à un pas rond pour que les graduations parlent. */
  private plafond = computed(() => {
    const lignes = this.bilan()?.evolution ?? [];
    const maximum = Math.max(0, ...lignes.map((l) => Math.max(l.recettes, l.depenses)));
    if (maximum === 0) return 1;
    const pas = Math.pow(10, Math.floor(Math.log10(maximum)) - 1) * 5;
    return Math.ceil(maximum / pas) * pas;
  });

  /** Quatre graduations, toutes atteintes par le graphique. */
  graduations = computed(() => {
    const haut = this.plafond();
    const utile = G.hauteur - G.haut - G.bas;
    return [0, 0.25, 0.5, 0.75, 1].map((part) => ({
      valeur: haut * part,
      y: G.haut + utile * (1 - part),
    }));
  });

  mois = computed<MoisTrace[]>(() => {
    const lignes = this.bilan()?.evolution ?? [];
    if (!lignes.length) return [];

    const haut = this.plafond();
    const utile = G.hauteur - G.haut - G.bas;
    const largeurUtile = G.largeur - G.gauche - G.droite;
    const pas = largeurUtile / lignes.length;
    // 2 px de fond entre deux barres voisines : sans cet espace, deux séries
    // adjacentes se lisent comme un seul bloc bicolore.
    const largeurBarre = Math.min(22, (pas - 10) / 2);

    return lignes.map((ligne, index) => {
      const gaucheGroupe = G.gauche + pas * index + (pas - largeurBarre * 2 - 2) / 2;
      const hauteur = (valeur: number) => Math.max(valeur > 0 ? 2 : 0, (valeur / haut) * utile);
      const hr = hauteur(ligne.recettes);
      const hd = hauteur(ligne.depenses);

      return {
        cle: ligne.mois,
        libelle: this.libelleMois(ligne.mois),
        recettes: ligne.recettes,
        depenses: ligne.depenses,
        solde: ligne.recettes - ligne.depenses,
        barreRecettes: { x: gaucheGroupe, y: G.haut + utile - hr, largeur: largeurBarre, hauteur: hr },
        barreDepenses: {
          x: gaucheGroupe + largeurBarre + 2, y: G.haut + utile - hd,
          largeur: largeurBarre, hauteur: hd,
        },
        centre: gaucheGroupe + largeurBarre + 1,
      };
    });
  });

  baseY = computed(() => G.haut + (G.hauteur - G.haut - G.bas));

  private libelleMois(cle: string): string {
    const [annee, mois] = cle.split('-');
    return `${MOIS_COURTS[Number(mois) - 1] ?? mois} ${annee.slice(2)}`;
  }

  /** 1 250 000 → « 1,25 M » ; 84 000 → « 84 k ». Un axe en unités brutes
   *  déborde et se lit mal. */
  abrege(valeur: number): string {
    if (valeur >= 1_000_000) return `${(valeur / 1_000_000).toFixed(valeur >= 10_000_000 ? 0 : 1)} M`;
    if (valeur >= 1_000) return `${Math.round(valeur / 1_000)} k`;
    return String(Math.round(valeur));
  }

  survoler(m: MoisTrace | null): void {
    this.moisSurvole.set(m);
  }

  // ---- Effectifs par classe -------------------------------------------

  effectifs = computed<ClasseTrace[]>(() => {
    const classes = [...this.classes()].sort((a, b) => b.effectif - a.effectif);
    const maximum = Math.max(1, ...classes.map((c) => Math.max(c.effectif, c.capacite_max || 0)));
    return classes.map((c) => {
      const capacite = c.capacite_max || 0;
      return {
        nom: c.nom,
        effectif: c.effectif,
        capacite,
        largeur: (c.effectif / maximum) * 100,
        remplissage: capacite ? Math.round((c.effectif / capacite) * 100) : 0,
        // Une classe pleine ou débordée est ce que la direction cherche du
        // regard : elle est signalée par sa couleur ET par sa mention.
        saturee: capacite > 0 && c.effectif >= capacite,
      };
    });
  });

  totalEffectif = computed(() => this.effectifs().reduce((t, c) => t + c.effectif, 0));
  classesSaturees = computed(() => this.effectifs().filter((c) => c.saturee).length);
}
