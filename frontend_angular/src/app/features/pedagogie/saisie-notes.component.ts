import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  Classe,
  Evaluation,
  GrilleSaisie,
  Matiere,
  Paginated,
  PeriodeScolaire,
} from '../../core/models/models';
import { PedagogieService } from './pedagogie.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';

/** Une ligne de la grille, avec la note en cours d'édition. */
interface LigneSaisie {
  id_eleve: number;
  matricule: string;
  nom: string;
  prenom: string;
  valeur: number | null;
  appreciation: string | null;
}

/**
 * Saisie des notes d'une évaluation, en grille (2.1 de la priorisation).
 *
 * Toute la classe est saisie puis envoyée en une requête. Le CRUD imposait un
 * aller-retour par élève — soixante requêtes pour un devoir de 6ème.
 *
 * Les notes sont saisies dans le barème de l'épreuve, pas ramenées sur 20 :
 * l'enseignant recopie ce qu'il a écrit sur la copie, la conversion est
 * l'affaire du calcul de moyennes.
 */
@Component({
  selector: 'app-saisie-notes',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, SiAccesDirective],
  templateUrl: './saisie-notes.component.html',
})
export class SaisieNotesComponent implements OnInit {
  private http = inject(HttpClient);
  private pedagogie = inject(PedagogieService);

  classes = signal<Classe[]>([]);
  periodes = signal<PeriodeScolaire[]>([]);
  matieres = signal<Matiere[]>([]);
  evaluations = signal<Evaluation[]>([]);

  idClasse = signal<number | null>(null);
  idPeriode = signal<number | null>(null);
  idEvaluation = signal<number | null>(null);

  grille = signal<GrilleSaisie | null>(null);
  lignes = signal<LigneSaisie[]>([]);
  erreur = signal<string | null>(null);
  message = signal<string | null>(null);
  occupe = signal(false);

  /** Évaluations de la classe et de la période choisies. */
  evaluationsFiltrees = computed(() =>
    this.evaluations().filter(
      (e) =>
        (!this.idClasse() || e.id_classe === this.idClasse()) &&
        (!this.idPeriode() || e.id_periode === this.idPeriode()),
    ));

  saisies = computed(() => this.lignes().filter((l) => l.valeur !== null).length);

  /** Aperçu de la moyenne, calculé sur les seules notes déjà saisies. */
  moyenneSaisie = computed(() => {
    const notes = this.lignes().map((l) => l.valeur).filter((v): v is number => v !== null);
    if (!notes.length) return null;
    return notes.reduce((a, b) => a + b, 0) / notes.length;
  });

  ngOnInit(): void {
    const api = environment.apiUrl;
    this.http.get<Paginated<Classe>>(`${api}/classes/`)
      .subscribe((r) => this.classes.set(r.results ?? []));
    this.http.get<Paginated<PeriodeScolaire>>(`${api}/periodes/`)
      .subscribe((r) => this.periodes.set(r.results ?? []));
    this.http.get<Paginated<Matiere>>(`${api}/matieres/`)
      .subscribe((r) => this.matieres.set(r.results ?? []));
    this.http.get<Paginated<Evaluation>>(`${api}/evaluations/`)
      .subscribe((r) => this.evaluations.set(r.results ?? []));
  }

  nomMatiere(id: number): string {
    return this.matieres().find((m) => m.id === id)?.nom ?? '—';
  }

  surClasse(valeur: string): void {
    this.idClasse.set(valeur ? Number(valeur) : null);
    this.reinitialiser();
  }

  surPeriode(valeur: string): void {
    this.idPeriode.set(valeur ? Number(valeur) : null);
    this.reinitialiser();
  }

  surEvaluation(valeur: string): void {
    this.idEvaluation.set(valeur ? Number(valeur) : null);
    this.grille.set(null);
    this.lignes.set([]);
    this.message.set(null);
    if (this.idEvaluation()) this.charger();
  }

  private reinitialiser(): void {
    this.idEvaluation.set(null);
    this.grille.set(null);
    this.lignes.set([]);
    this.message.set(null);
  }

  private charger(): void {
    this.occupe.set(true);
    this.erreur.set(null);
    this.pedagogie.grille(this.idEvaluation()!).subscribe({
      next: (g) => {
        this.grille.set(g);
        this.lignes.set(g.eleves.map((e) => ({ ...e })));
        this.occupe.set(false);
      },
      error: (e) => {
        this.erreur.set(this.messageErreur(e));
        this.occupe.set(false);
      },
    });
  }

  /**
   * Une case vidée efface la note plutôt que de valoir zéro : l'élève était
   * absent, ou la saisie était fausse.
   */
  saisir(ligne: LigneSaisie, valeur: string): void {
    const nombre = valeur === '' ? null : Number(valeur);
    this.lignes.update((lignes) =>
      lignes.map((l) =>
        l.id_eleve === ligne.id_eleve
          ? { ...l, valeur: nombre !== null && Number.isNaN(nombre) ? null : nombre }
          : l,
      ));
    this.message.set(null);
  }

  saisirAppreciation(ligne: LigneSaisie, texte: string): void {
    this.lignes.update((lignes) =>
      lignes.map((l) => (l.id_eleve === ligne.id_eleve ? { ...l, appreciation: texte } : l)));
  }

  /** Une note hors barème est signalée avant l'envoi, à la case concernée. */
  horsBareme(ligne: LigneSaisie): boolean {
    const bareme = this.grille()?.bareme;
    if (ligne.valeur === null || bareme === undefined) return false;
    return ligne.valeur < 0 || ligne.valeur > bareme;
  }

  aUneErreur = computed(() => this.lignes().some((l) => this.horsBareme(l)));

  enregistrer(): void {
    const grille = this.grille();
    if (!grille || this.aUneErreur()) return;

    this.occupe.set(true);
    this.erreur.set(null);
    this.pedagogie
      .enregistrerNotes(
        grille.id_evaluation,
        this.lignes().map((l) => ({
          id_eleve: l.id_eleve,
          valeur: l.valeur,
          appreciation: l.appreciation ?? '',
        })),
      )
      .subscribe({
        next: (r) => {
          this.message.set(
            `${r.creees} note(s) créée(s), ${r.modifiees} modifiée(s)` +
            (r.effacees ? `, ${r.effacees} effacée(s)` : '') + '.');
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(this.messageErreur(e));
          this.occupe.set(false);
        },
      });
  }

  private messageErreur(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return "L'enregistrement a échoué.";
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
