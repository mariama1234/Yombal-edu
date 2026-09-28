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
  LigneVentilation,
  Paginated,
  SyntheseAssiduite,
  SyntheseEcole,
} from '../../core/models/models';
import { FinanceService } from '../finances/finance.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';
import { GrapheAnneauComponent, Segment } from '../../shared/graphes/graphe-anneau.component';
import { Colonne, GrapheColonnesComponent } from '../../shared/graphes/graphe-colonnes.component';

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

/** Teintes des parts, dans l'ordre validé pour le daltonisme : jamais tirées au hasard. */
const SERIES = ['var(--serie-1)', 'var(--serie-2)', 'var(--serie-3)',
                'var(--serie-4)', 'var(--serie-5)'];

const MOIS_COURTS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
                     'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

// Géométrie du graphique d'évolution, en unités du viewBox.
const G = { largeur: 720, hauteur: 240, gauche: 62, droite: 14, haut: 18, bas: 38 };

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, SiAccesDirective, GrapheAnneauComponent, GrapheColonnesComponent],
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

    // Contenu personnalisé par profil : on ne demande que ce que la matrice
    // ouvre au compte, plutôt que de laisser le serveur refuser panneau par
    // panneau. Les vues sur toute l'école sont fermées aux familles.
    const auth = this.auth;
    const equipe = !auth.estFamille();

    this.http
      .get<Paginated<unknown>>(`${environment.apiUrl}/eleves/${suffixe}`)
      .subscribe((r) => this.nbEleves.set(r.count));
    if (auth.peutLire('classes')) this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/${suffixe}`)
      .subscribe((r) => {
        this.nbClasses.set(r.count);
        this.classes.set(r.results ?? []);
      });

    // Le bilan porte déjà les chiffres financiers de l'année : une seule
    // requête remplace le comptage des impayés et le solde de caisse.
    if (auth.peutLire('budget')) this.finance.bilan(params).subscribe({
      next: (b) => this.bilan.set(b),
      error: () => this.bilan.set(null),
    });

    // Sans période précisée, le serveur retient celle en cours : le directeur
    // ouvre son tableau de bord sur le trimestre du moment.
    if (equipe && auth.peutLire('bulletins')) this.http
      .get<SyntheseEcole>(`${environment.apiUrl}/bulletins/synthese/${suffixe}`)
      .subscribe({
        next: (s) => this.synthese.set(s),
        error: () => this.synthese.set(null),
      });

    if (equipe && auth.peutLire('frais')) this.http
      .get<ImpayesParClasse>(
        `${environment.apiUrl}/frais-eleves/impayes/par-classe/${suffixe}`)
      .subscribe({
        next: (i) => this.impayes.set(i),
        error: () => this.impayes.set(null),
      });

    if (equipe && auth.peutLire('presences')) this.http
      .get<SyntheseAssiduite>(`${environment.apiUrl}/absences/synthese/${suffixe}`)
      .subscribe({
        next: (a) => this.assiduite.set(a),
        error: () => this.assiduite.set(null),
      });
  }

  // ---- Suivi budgétaire ------------------------------------------------

  /**
   * Budgets triés par consommation décroissante : un poste à 95 % en février
   * est une alerte, un poste à 20 % n'appelle rien. Le dépassement est calculé
   * ici pour que le gabarit n'ait pas à comparer des nombres.
   */
  budgets = computed(() => {
    const lignes = this.bilan()?.budgets ?? [];
    return [...lignes]
      .sort((a, b) => b.consomme - a.consomme)
      .map((b) => ({
        ...b,
        // La barre se lit sur 100 % ; le dépassement est dit en toutes lettres
        // plutôt que dessiné hors de sa piste.
        remplissage: Math.min(100, b.consomme),
        depasse: b.realise > b.prevu,
      }));
  });

  totalPrevu = computed(() => this.budgets().reduce((t, b) => t + b.prevu, 0));
  totalRealise = computed(() => this.budgets().reduce((t, b) => t + b.realise, 0));
  budgetsDepasses = computed(() => this.budgets().filter((b) => b.depasse).length);

  // ---- Recouvrement et assiduité --------------------------------------

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

  // ---- Graphiques circulaires -----------------------------------------

  /**
   * Situation de paiement des élèves. Ce sont des états — à jour, en retard —
   * et non des séries : ils prennent les couleurs d'état de l'application,
   * nommées dans la légende, et chaque part ouvre la liste des élèves concernés.
   */
  situationPaiements = computed<Segment[]>(() => {
    const e = this.bilan()?.eleves;
    if (!e) return [];
    const etat = (statut: string, libelle: string, couleur: string): Segment => ({
      libelle, couleur, valeur: e[statut as keyof typeof e] ?? 0,
      lien: { chemin: '/finances', params: { statut } },
    });
    return [
      etat('a_jour', 'À jour', 'var(--green)'),
      etat('partiel', 'Partiel', 'var(--orange-500)'),
      etat('en_retard', 'En retard', 'var(--red)'),
      etat('non_paye', 'Non payé', 'var(--ink-400)'),
    ];
  });

  recettesParType = computed(() => this.parts(this.bilan()?.recettes.par_type_frais));
  recettesParMoyen = computed(() => this.parts(this.bilan()?.recettes.par_methode));
  depensesParCategorie = computed(() => this.parts(this.bilan()?.depenses.par_categorie));

  /**
   * Une ventilation en six parts au plus : les cinq plus grosses, puis le
   * reste regroupé. Au-delà, les teintes se confondent et l'anneau ne se lit
   * plus — la liste complète reste au bilan financier.
   */
  private parts(lignes?: LigneVentilation[]): Segment[] {
    const triees = [...(lignes ?? [])].filter((l) => l.montant > 0)
      .sort((a, b) => b.montant - a.montant);
    const tete = triees.length > 6 ? triees.slice(0, 5) : triees;
    const segments: Segment[] = tete.map((l, i) => ({
      libelle: l.libelle || 'Sans libellé',
      valeur: l.montant,
      couleur: i < SERIES.length ? SERIES[i] : 'var(--serie-6)',
    }));
    if (triees.length > 6) {
      segments.push({
        libelle: 'Autres',
        valeur: triees.slice(5).reduce((t, l) => t + l.montant, 0),
        couleur: 'var(--serie-autre)',
      });
    }
    return segments;
  }

  // ---- Histogrammes en colonnes ---------------------------------------

  colonnesEffectifs = computed<Colonne[]>(() => this.effectifs().map((c) => ({
    libelle: c.nom,
    valeur: c.effectif,
    accent: c.saturee,
    detail: c.capacite ? `${c.effectif} / ${c.capacite} places (${c.remplissage} %)` : undefined,
  })));

  colonnesMentions = computed<Colonne[]>(() => this.mentions().map((m) => ({
    libelle: m.libelle,
    valeur: m.nombre,
    accent: m.faible,
    detail: `${m.part} % des élèves évalués`,
  })));

  /** Les huit classes qui doivent le plus : c'est par là qu'on commence. */
  colonnesImpayes = computed<Colonne[]>(() => (this.impayes()?.classes ?? []).slice(0, 8).map((c) => ({
    libelle: c.classe,
    valeur: c.reste,
    accent: c.eleves_en_retard > 0,
    detail: c.eleves_en_retard > 0
      ? `${c.eleves_en_retard} élève(s) en retard · ${Math.round(c.taux)} % payé`
      : `${Math.round(c.taux)} % payé`,
  })));
}
