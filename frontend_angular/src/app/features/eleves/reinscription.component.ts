import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  AnneeScolaire,
  Classe,
  Paginated,
  ResultatReinscription,
} from '../../core/models/models';
import { ScolariteService } from './scolarite.service';
import { ToastService } from '../../core/services/toast.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';
import { AlerteAge, alerteAge } from './alerte-age';

/**
 * Réinscription d'une classe en bloc — le cas d'exception.
 *
 * Une classe ne revient pas entière d'une année sur l'autre : des élèves
 * partent, redoublent, changent de classe, d'autres arrivent. L'aperçu liste
 * donc les élèves, tous cochés, et l'on décoche ceux qui ne suivent pas. Les
 * décochés restent dans la file « À réinscrire » ; les nouveaux passent par
 * « Inscrire un élève ».
 *
 * Même contrat que la génération des frais : on prévisualise avant d'écrire.
 */
@Component({
  selector: 'app-reinscription',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink, SiAccesDirective],
  templateUrl: './reinscription.component.html',
})
export class ReinscriptionComponent implements OnInit {
  private fb = inject(FormBuilder);
  private http = inject(HttpClient);
  private scolarite = inject(ScolariteService);
  private toast = inject(ToastService);

  annees = signal<AnneeScolaire[]>([]);
  classes = signal<Classe[]>([]);

  apercu = signal<ResultatReinscription | null>(null);
  resultat = signal<ResultatReinscription | null>(null);
  erreur = signal<string | null>(null);
  /** Élèves cochés hors de la fourchette d'âge : l'écran propose de confirmer. */
  alerte = signal<AlerteAge | null>(null);
  occupe = signal(false);

  /** Élèves cochés à l'aperçu : ceux qui suivent la classe. */
  coches = signal<number[]>([]);

  form = this.fb.group({
    id_annee_source: [null as number | null, Validators.required],
    id_annee_cible: [null as number | null, Validators.required],
    // Obligatoire : verser toutes les classes d'une année dans une seule
    // classe d'arrivée n'a pas de sens, et c'était pourtant ce que « Toutes
    // les classes » proposait en un clic.
    id_classe_source: [null as number | null, Validators.required],
    id_classe_cible: [null as number | null, Validators.required],
    date_inscription: [''],
  });

  private anneeSource = signal<number | null>(null);
  private anneeCible = signal<number | null>(null);
  private classeCible = signal<number | null>(null);

  /** Les listes déroulantes ne proposent que les classes de l'année choisie. */
  classesSource = computed(() =>
    this.classes().filter((c) => c.id_annee_scolaire === this.anneeSource()));
  classesCible = computed(() =>
    this.classes().filter((c) => c.id_annee_scolaire === this.anneeCible()));

  /** L'aperçu comptait toute la classe ; l'effectif suit les cases cochées. */
  effectifVise = computed(() => {
    const a = this.apercu();
    return a ? a.effectif_cible - a.reinscrits + this.coches().length : 0;
  });

  nonRetenus = computed(() => {
    const a = this.apercu();
    return a ? a.eleves.length - a.ignores - this.coches().length : 0;
  });

  avertissement = computed(() => {
    const classe = this.classes().find((c) => c.id === this.classeCible());
    if (!classe?.capacite_max || this.effectifVise() <= classe.capacite_max) return null;
    return `${classe.nom} atteindrait ${this.effectifVise()} élèves pour une capacité de ${classe.capacite_max}.`;
  });

  ngOnInit(): void {
    this.http
      .get<Paginated<AnneeScolaire>>(`${environment.apiUrl}/annees-scolaires/`)
      .subscribe((r) => {
        const annees = (r.results ?? []).sort((a, b) =>
          a.date_debut.localeCompare(b.date_debut));
        this.annees.set(annees);
        // Par défaut : de l'année active vers la suivante, le cas courant.
        const active = annees.find((a) => a.statut === 'active') ?? annees[annees.length - 1];
        if (active) {
          const suivante = annees[annees.indexOf(active) + 1];
          this.form.patchValue({
            id_annee_source: active.id,
            id_annee_cible: suivante?.id ?? null,
          });
        }
      });
    // Toutes les classes : au-delà de vingt, la première page en oubliait.
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/?page_size=200`)
      .subscribe((r) => this.classes.set(r.results ?? []));

    // Changer d'année vide les classes choisies : elles n'existent plus dans
    // le nouveau périmètre.
    this.form.controls.id_annee_source.valueChanges.subscribe((v) => {
      this.anneeSource.set(v);
      this.form.patchValue({ id_classe_source: null }, { emitEvent: false });
    });
    this.form.controls.id_annee_cible.valueChanges.subscribe((v) => {
      this.anneeCible.set(v);
      this.classeCible.set(null);
      this.form.patchValue({ id_classe_cible: null }, { emitEvent: false });
    });
    this.form.controls.id_classe_cible.valueChanges.subscribe((v) => this.classeCible.set(v));

    this.form.valueChanges.subscribe(() => this.apercu.set(null));
  }

  private corps(valider: boolean, derogation = false) {
    const v = this.form.getRawValue();
    return {
      id_annee_source: v.id_annee_source,
      id_annee_cible: v.id_annee_cible,
      id_classe_source: v.id_classe_source || null,
      id_classe_cible: v.id_classe_cible,
      date_inscription: v.date_inscription || null,
      // L'aperçu porte sur toute la classe ; la validation, sur les cochés.
      eleves: valider ? this.coches() : null,
      valider,
      derogation_age: derogation,
    };
  }

  private appeler(valider: boolean, sur: (r: ResultatReinscription) => void, derogation = false): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    this.alerte.set(null);
    this.scolarite.reinscrire(this.corps(valider, derogation)).subscribe({
      next: (r) => {
        sur(r);
        this.occupe.set(false);
      },
      error: (e) => {
        const alerte = alerteAge(e);
        if (alerte) this.alerte.set(alerte);
        else this.erreur.set(this.message(e));
        this.occupe.set(false);
      },
    });
  }

  previsualiser(): void {
    this.appeler(false, (r) => {
      this.apercu.set(r);
      this.coches.set(r.eleves.filter((e) => e.retenu).map((e) => e.id));
    });
  }

  /** `derogation` : renvoi après l'avertissement d'âge, confirmé par la personne. */
  valider(derogation = false): void {
    this.appeler(true, (r) => {
      this.resultat.set(r);
      this.apercu.set(null);
      this.toast.succes('Réinscription de la classe enregistrée', `${this.coches().length} élève(s) retenu(s).`);
    }, derogation);
  }

  basculer(id: number): void {
    this.coches.update((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]));
  }

  toutCocher(): void {
    this.coches.set((this.apercu()?.eleves ?? []).filter((e) => !e.deja_inscrit).map((e) => e.id));
  }

  toutDecocher(): void {
    this.coches.set([]);
  }

  /** Élèves qu'on ne peut pas réinscrire en l'état : date manquante, ou école qui bloque. */
  decocherBloques(): void {
    const bloques = new Set((this.apercu()?.eleves ?? []).filter((e) => e.alerte_age?.bloquant).map((e) => e.id));
    this.coches.update((ids) => ids.filter((id) => !bloques.has(id)));
  }

  bloquesCoches(): number {
    const coches = this.coches();
    return (this.apercu()?.eleves ?? []).filter((e) => e.alerte_age?.bloquant && coches.includes(e.id)).length;
  }

  recommencer(): void {
    this.resultat.set(null);
    this.apercu.set(null);
    this.coches.set([]);
  }

  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return 'La réinscription a échoué.';
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
