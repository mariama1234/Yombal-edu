import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';

/** Réponse de GET /api/tarifs/grille/. */
interface GrilleTarifs {
  niveaux: { id: number; libelle: string }[];
  types: {
    id: number;
    libelle: string;
    nature: 'inscription' | 'mensualite' | 'autre';
    nature_libelle: string;
    montant_defaut: number;
    frequence: string | null;
    mensuel: boolean;
  }[];
  /** Montant par case « idNiveau-idFrais » ; une case absente applique le montant par défaut. */
  tarifs: Record<string, number>;
}

/**
 * Grille des tarifs : un montant par niveau et par frais.
 *
 * L'inscription en 3ème n'est pas celle de 4ème, et chaque établissement a ses
 * propres frais : une ligne par niveau, une colonne par frais, et une case
 * vide applique le montant par défaut du frais. On saisit toute la grille,
 * puis on enregistre d'un coup les seules cases modifiées.
 */
@Component({
  selector: 'app-tarifs-niveaux',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './tarifs-niveaux.component.html',
})
export class TarifsNiveauxComponent implements OnInit {
  private http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}/tarifs/grille/`;

  grille = signal<GrilleTarifs | null>(null);
  /** Saisie en cours, par case ; chaîne vide = montant par défaut du frais. */
  valeurs = signal<Record<string, string>>({});
  private initiales = signal<Record<string, string>>({});

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

  ngOnInit(): void {
    this.charger();
  }

  charger(): void {
    this.chargement.set(true);
    this.http.get<GrilleTarifs>(this.url).subscribe({
      next: (grille) => {
        this.appliquer(grille);
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

  private cle(idNiveau: number, idFrais: number): string {
    return `${idNiveau}-${idFrais}`;
  }

  valeur(idNiveau: number, idFrais: number): string {
    return this.valeurs()[this.cle(idNiveau, idFrais)] ?? '';
  }

  estModifiee(idNiveau: number, idFrais: number): boolean {
    return this.modifiees().includes(this.cle(idNiveau, idFrais));
  }

  changer(idNiveau: number, idFrais: number, evenement: Event): void {
    const saisie = (evenement.target as HTMLInputElement).value.trim();
    this.message.set(null);
    this.valeurs.update((v) => ({ ...v, [this.cle(idNiveau, idFrais)]: saisie }));
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
        this.message.set(`${tarifs.length} tarif(s) enregistré(s).`);
      },
      error: (e) => {
        const corps = e?.error;
        this.erreur.set(
          (typeof corps?.tarifs === 'string' ? corps.tarifs : Array.isArray(corps?.tarifs) && typeof corps.tarifs[0] === 'string' ? corps.tarifs[0] : null)
          ?? corps?.detail ?? "L'enregistrement a échoué.");
        this.occupe.set(false);
      },
    });
  }
}
