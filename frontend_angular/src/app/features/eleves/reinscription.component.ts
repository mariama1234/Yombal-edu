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

/**
 * Réinscription d'une promotion d'une année sur l'autre.
 *
 * Même contrat que la génération des frais : on prévisualise (« 42 élèves
 * passeraient de 6ème A en 5ème A ») avant d'écrire. Réinscrire une classe
 * entière ne se défait pas à la main.
 */
@Component({
  selector: 'app-reinscription',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './reinscription.component.html',
})
export class ReinscriptionComponent implements OnInit {
  private fb = inject(FormBuilder);
  private http = inject(HttpClient);
  private scolarite = inject(ScolariteService);

  annees = signal<AnneeScolaire[]>([]);
  classes = signal<Classe[]>([]);

  apercu = signal<ResultatReinscription | null>(null);
  resultat = signal<ResultatReinscription | null>(null);
  erreur = signal<string | null>(null);
  occupe = signal(false);

  form = this.fb.group({
    id_annee_source: [null as number | null, Validators.required],
    id_annee_cible: [null as number | null, Validators.required],
    id_classe_source: [null as number | null],
    id_classe_cible: [null as number | null, Validators.required],
    date_inscription: [''],
  });

  private anneeSource = signal<number | null>(null);
  private anneeCible = signal<number | null>(null);

  /** Les listes déroulantes ne proposent que les classes de l'année choisie. */
  classesSource = computed(() =>
    this.classes().filter((c) => c.id_annee_scolaire === this.anneeSource()));
  classesCible = computed(() =>
    this.classes().filter((c) => c.id_annee_scolaire === this.anneeCible()));

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
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/`)
      .subscribe((r) => this.classes.set(r.results ?? []));

    // Changer d'année vide les classes choisies : elles n'existent plus dans
    // le nouveau périmètre.
    this.form.controls.id_annee_source.valueChanges.subscribe((v) => {
      this.anneeSource.set(v);
      this.form.patchValue({ id_classe_source: null }, { emitEvent: false });
    });
    this.form.controls.id_annee_cible.valueChanges.subscribe((v) => {
      this.anneeCible.set(v);
      this.form.patchValue({ id_classe_cible: null }, { emitEvent: false });
    });

    this.form.valueChanges.subscribe(() => this.apercu.set(null));
  }

  private corps(valider: boolean) {
    const v = this.form.getRawValue();
    return {
      id_annee_source: v.id_annee_source,
      id_annee_cible: v.id_annee_cible,
      id_classe_source: v.id_classe_source || null,
      id_classe_cible: v.id_classe_cible,
      date_inscription: v.date_inscription || null,
      valider,
    };
  }

  private appeler(valider: boolean, sur: (r: ResultatReinscription) => void): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    this.scolarite.reinscrire(this.corps(valider)).subscribe({
      next: (r) => {
        sur(r);
        this.occupe.set(false);
      },
      error: (e) => {
        this.erreur.set(this.message(e));
        this.occupe.set(false);
      },
    });
  }

  previsualiser(): void {
    this.appeler(false, (r) => this.apercu.set(r));
  }

  valider(): void {
    this.appeler(true, (r) => {
      this.resultat.set(r);
      this.apercu.set(null);
    });
  }

  recommencer(): void {
    this.resultat.set(null);
    this.apercu.set(null);
  }

  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return 'La réinscription a échoué.';
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
