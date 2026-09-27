import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { ToastService } from '../../core/services/toast.service';
import { AuthService } from '../../core/services/auth.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';

/** Réponse de GET /api/tarifs/grille/. */
interface GrilleTarifs {
  niveaux: { id: number; libelle: string }[];
  types: TypeGrille[];
  /** Montant par case « idNiveau-idFrais » ; une case absente applique le montant par défaut. */
  tarifs: Record<string, number>;
}

interface TypeGrille {
  id: number;
  libelle: string;
  nature: 'inscription' | 'mensualite' | 'autre';
  nature_libelle: string;
  montant_defaut: number;
  frequence: string | null;
  mensuel: boolean;
}

/** On lit la grille d'un niveau (tous ses frais) ou d'un frais (tous les niveaux). */
type Mode = 'niveau' | 'frais';

/**
 * Tarifs par niveau : un montant par niveau et par frais.
 *
 * Un tableau « une colonne par frais » devenait illisible dès qu'une école a
 * beaucoup de frais : on défilait de côté sans plus savoir sur quelle ligne on
 * était. L'écran lit donc toujours de haut en bas — la liste des frais d'un
 * niveau, ou la liste des niveaux d'un frais — quel que soit leur nombre.
 *
 * Toutes les saisies restent en mémoire d'un niveau à l'autre : on parcourt,
 * on corrige, puis on enregistre d'un coup les seules cases modifiées.
 */
@Component({
  selector: 'app-tarifs-niveaux',
  standalone: true,
  imports: [CommonModule, RouterLink, SiAccesDirective],
  templateUrl: './tarifs-niveaux.component.html',
  styles: [`
    .disposition {
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
      gap: 16px;
      align-items: start;
    }
    .liste {
      position: sticky;
      top: 12px;
      max-height: calc(100vh - 220px);
      display: flex;
      flex-direction: column;
      padding: 12px;
    }
    .liste-elements {
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 2px;
      margin-top: 10px;
    }
    .element {
      position: relative;
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 2px;
      width: 100%;
      padding: 9px 26px 9px 12px;
      border: 1px solid transparent;
      border-radius: 10px;
      background: none;
      text-align: left;
      cursor: pointer;
    }
    .element:hover { background: rgba(15, 23, 42, 0.04); }
    .element.actif { border-color: var(--orange-500); background: var(--surface); }
    .element-nom { font-size: 13.5px; font-weight: 600; color: var(--ink-900); }
    .element-meta { font-size: 11.5px; color: var(--ink-400); }
    .point {
      position: absolute;
      top: 13px;
      right: 11px;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--orange-500);
    }
    .groupe-titre {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: var(--ink-400);
      margin: 18px 0 2px;
    }
    .groupe-titre:first-child { margin-top: 4px; }
    .ligne {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 150px 150px 34px;
      align-items: center;
      gap: 12px;
      padding: 10px 0;
      border-bottom: 1px solid var(--line);
    }
    .ligne:last-child { border-bottom: none; }
    .ligne-nom { font-size: 13.5px; font-weight: 600; color: var(--ink-900); }
    .ligne-meta { font-size: 11.5px; color: var(--ink-400); }
    .ligne-defaut { font-size: 12px; color: var(--ink-400); text-align: right; }
    .propre { color: var(--orange-500); font-weight: 600; }
    .saisie { width: 100%; text-align: right; padding: 8px 10px; }
    .retour {
      width: 30px;
      height: 30px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      color: var(--ink-600);
      cursor: pointer;
    }
    .barre {
      position: sticky;
      bottom: 0;
      z-index: 5;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 16px;
      padding: 12px 16px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--surface);
      box-shadow: 0 -6px 16px rgba(15, 23, 42, 0.06);
    }
    .outils { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    @media (max-width: 900px) {
      .disposition { grid-template-columns: 1fr; }
      .liste { position: static; max-height: 260px; }
    }
    @media (max-width: 600px) {
      .ligne { grid-template-columns: minmax(0, 1fr) 130px 34px; }
      .ligne-defaut { display: none; }
    }
  `],
})
export class TarifsNiveauxComponent implements OnInit {
  private http = inject(HttpClient);
  private toast = inject(ToastService);
  private auth = inject(AuthService);

  /** Direction, vie scolaire et responsable financier fixent les tarifs ; les autres les consultent. */
  peutModifier = computed(() => this.auth.peutEcrire('types_frais'));
  private readonly url = `${environment.apiUrl}/tarifs/grille/`;

  grille = signal<GrilleTarifs | null>(null);
  /** Saisie en cours, par case ; chaîne vide = montant par défaut du frais. */
  valeurs = signal<Record<string, string>>({});
  private initiales = signal<Record<string, string>>({});

  mode = signal<Mode>('niveau');
  selection = signal<number | null>(null);
  recherche = signal('');
  /** Montant à appliquer à tous les niveaux, en lecture « par frais ». */
  montantPourTous = signal('');

  chargement = signal(true);
  occupe = signal(false);
  erreur = signal<string | null>(null);
  message = signal<string | null>(null);

  /** Cases dont la saisie diffère de ce qui est enregistré. */
  modifiees = computed(() => {
    const valeurs = this.valeurs();
    const initiales = this.initiales();
    return Object.keys({ ...valeurs, ...initiales })
      .filter((cle) => (valeurs[cle] ?? '') !== (initiales[cle] ?? ''));
  });

  niveauxFiltres = computed(() => {
    const terme = this.recherche().trim().toLowerCase();
    return (this.grille()?.niveaux ?? []).filter((n) => n.libelle.toLowerCase().includes(terme));
  });

  typesFiltres = computed(() => {
    const terme = this.recherche().trim().toLowerCase();
    return (this.grille()?.types ?? []).filter((t) => t.libelle.toLowerCase().includes(terme));
  });

  niveauChoisi = computed(() =>
    this.mode() === 'niveau' ? this.grille()?.niveaux.find((n) => n.id === this.selection()) ?? null : null);

  fraisChoisi = computed(() =>
    this.mode() === 'frais' ? this.grille()?.types.find((t) => t.id === this.selection()) ?? null : null);

  /** Les frais d'un niveau, rangés : ceux qu'on facture toujours, puis ceux qu'on coche. */
  groupes = computed(() => {
    const types = this.grille()?.types ?? [];
    return [
      { titre: 'Dus d’office', types: types.filter((t) => t.nature !== 'autre') },
      { titre: 'Frais au choix', types: types.filter((t) => t.nature === 'autre') },
    ].filter((groupe) => groupe.types.length);
  });

  ngOnInit(): void {
    this.charger();
  }

  charger(): void {
    this.chargement.set(true);
    this.http.get<GrilleTarifs>(this.url).subscribe({
      next: (grille) => {
        this.appliquer(grille);
        if (this.selection() === null) this.selection.set(grille.niveaux[0]?.id ?? null);
        this.chargement.set(false);
      },
      error: (e) => {
        this.erreur.set(e?.error?.detail ?? "La grille des tarifs n'a pas pu être chargée.");
        this.chargement.set(false);
      },
    });
  }

  private appliquer(grille: GrilleTarifs): void {
    const valeurs: Record<string, string> = {};
    for (const [cle, montant] of Object.entries(grille.tarifs)) valeurs[cle] = String(Number(montant));
    this.grille.set(grille);
    this.valeurs.set(valeurs);
    this.initiales.set({ ...valeurs });
  }

  // ---- Navigation -------------------------------------------------------

  choisirMode(mode: Mode): void {
    if (this.mode() === mode) return;
    const grille = this.grille();
    this.mode.set(mode);
    this.recherche.set('');
    this.montantPourTous.set('');
    this.selection.set((mode === 'niveau' ? grille?.niveaux[0]?.id : grille?.types[0]?.id) ?? null);
  }

  choisir(id: number): void {
    this.selection.set(id);
    this.montantPourTous.set('');
  }

  // ---- Lecture des cases ------------------------------------------------

  private cle(idNiveau: number, idFrais: number): string {
    return `${idNiveau}-${idFrais}`;
  }

  valeur(idNiveau: number, idFrais: number): string {
    return this.valeurs()[this.cle(idNiveau, idFrais)] ?? '';
  }

  aTarifPropre(idNiveau: number, idFrais: number): boolean {
    return this.valeur(idNiveau, idFrais) !== '';
  }

  estModifiee(idNiveau: number, idFrais: number): boolean {
    return this.modifiees().includes(this.cle(idNiveau, idFrais));
  }

  /** Montant réellement facturé : le tarif du niveau, sinon le montant par défaut. */
  montantApplique(idNiveau: number, type: TypeGrille): number {
    const saisie = this.valeur(idNiveau, type.id);
    return saisie !== '' ? Number(saisie) : Number(type.montant_defaut);
  }

  nombreTarifsPropres(idNiveau: number): number {
    return (this.grille()?.types ?? []).filter((t) => this.aTarifPropre(idNiveau, t.id)).length;
  }

  nombreNiveauxTarifes(idFrais: number): number {
    return (this.grille()?.niveaux ?? []).filter((n) => this.aTarifPropre(n.id, idFrais)).length;
  }

  /** Ce que coûte une inscription dans ce niveau : les frais dus d'office. */
  totalDOffice(idNiveau: number): number {
    return (this.grille()?.types ?? [])
      .filter((t) => t.nature !== 'autre')
      .reduce((somme, t) => somme + this.montantApplique(idNiveau, t), 0);
  }

  niveauModifie(idNiveau: number): boolean {
    return this.modifiees().some((cle) => cle.startsWith(`${idNiveau}-`));
  }

  fraisModifie(idFrais: number): boolean {
    return this.modifiees().some((cle) => cle.endsWith(`-${idFrais}`));
  }

  // ---- Saisie -----------------------------------------------------------

  changer(idNiveau: number, idFrais: number, evenement: Event): void {
    const saisie = (evenement.target as HTMLInputElement).value.trim();
    this.message.set(null);
    this.valeurs.update((v) => ({ ...v, [this.cle(idNiveau, idFrais)]: saisie }));
  }

  revenirAuDefaut(idNiveau: number, idFrais: number): void {
    this.valeurs.update((v) => ({ ...v, [this.cle(idNiveau, idFrais)]: '' }));
  }

  /** Même montant pour ce frais dans tous les niveaux. */
  appliquerATousLesNiveaux(): void {
    const idFrais = this.selection();
    const montant = this.montantPourTous().trim();
    if (idFrais === null || montant === '') return;
    if (!(Number(montant) >= 0)) {
      this.erreur.set('Le montant doit être un nombre positif.');
      return;
    }
    this.erreur.set(null);
    this.message.set(null);
    this.valeurs.update((v) => {
      const suite = { ...v };
      for (const n of this.grille()?.niveaux ?? []) suite[this.cle(n.id, idFrais)] = montant;
      return suite;
    });
    this.montantPourTous.set('');
  }

  /** Tous les niveaux reviennent au montant par défaut de ce frais. */
  toutRemettreParDefaut(): void {
    const idFrais = this.selection();
    if (idFrais === null) return;
    this.valeurs.update((v) => {
      const suite = { ...v };
      for (const n of this.grille()?.niveaux ?? []) suite[this.cle(n.id, idFrais)] = '';
      return suite;
    });
  }

  annuler(): void {
    this.valeurs.set({ ...this.initiales() });
    this.erreur.set(null);
  }

  enregistrer(): void {
    const cles = this.modifiees();
    if (!cles.length) return;
    const tarifs = cles.map((cle) => {
      const [id_niveau, id_type_frais] = cle.split('-').map(Number);
      const saisie = this.valeurs()[cle] ?? '';
      return { id_niveau, id_type_frais, montant: saisie === '' ? null : saisie };
    });
    if (tarifs.some((t) => t.montant !== null && !(Number(t.montant) >= 0))) {
      this.erreur.set('Chaque montant doit être un nombre positif, ou vide pour le montant par défaut.');
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    this.http.post<GrilleTarifs>(this.url, { tarifs }).subscribe({
      next: (grille) => {
        this.appliquer(grille);
        this.occupe.set(false);
        this.toast.succes('Tarifs enregistrés', `${tarifs.length} case(s) mise(s) à jour.`);
      },
      error: (e) => {
        const corps = e?.error;
        const detail = typeof corps?.tarifs === 'string' ? corps.tarifs
          : Array.isArray(corps?.tarifs) && typeof corps.tarifs[0] === 'string' ? corps.tarifs[0]
          : corps?.detail;
        this.erreur.set(detail ?? "L'enregistrement a échoué.");
        this.occupe.set(false);
      },
    });
  }
}
