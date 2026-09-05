import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  AnneeScolaire,
  Classe,
  Paginated,
  ResultatGenerationFrais,
  TypeFrais,
} from '../../core/models/models';
import { FinanceService } from './finance.service';

/**
 * Facturation en masse : un type de frais, une année, des échéances.
 *
 * L'écran impose l'aperçu avant l'écriture — on voit combien de frais seraient
 * créés et pour quel total avant de valider. Générer dix mensualités pour six
 * cents élèves ne se défait pas d'un clic.
 */
@Component({
  selector: 'app-generer-frais',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './generer-frais.component.html',
})
export class GenererFraisComponent implements OnInit {
  private fb = inject(FormBuilder);
  private http = inject(HttpClient);
  private finance = inject(FinanceService);

  annees = signal<AnneeScolaire[]>([]);
  classes = signal<Classe[]>([]);
  typesFrais = signal<TypeFrais[]>([]);

  apercu = signal<ResultatGenerationFrais | null>(null);
  resultat = signal<ResultatGenerationFrais | null>(null);
  erreur = signal<string | null>(null);
  occupe = signal(false);

  form = this.fb.group({
    id_type_frais: [null as number | null, Validators.required],
    id_annee_scolaire: [null as number | null, Validators.required],
    id_classe: [null as number | null],
    montant: [null as number | null],
    premiere_echeance: ['', Validators.required],
    nombre_echeances: [1, [Validators.required, Validators.min(1), Validators.max(24)]],
  });

  ngOnInit(): void {
    this.http
      .get<Paginated<AnneeScolaire>>(`${environment.apiUrl}/annees-scolaires/`)
      .subscribe((r) => {
        const annees = r.results ?? [];
        this.annees.set(annees);
        const active = annees.find((a) => a.statut === 'active') ?? annees[0];
        if (active) this.form.patchValue({ id_annee_scolaire: active.id });
      });
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/`)
      .subscribe((r) => this.classes.set(r.results ?? []));
    this.http
      .get<Paginated<TypeFrais>>(`${environment.apiUrl}/types-frais/`)
      .subscribe((r) => this.typesFrais.set(r.results ?? []));

    // Changer de type de frais réinitialise l'aperçu : il ne décrit plus la
    // même facturation.
    this.form.valueChanges.subscribe(() => this.apercu.set(null));
  }

  private corps(valider: boolean) {
    const valeurs = this.form.getRawValue();
    return {
      id_type_frais: valeurs.id_type_frais,
      id_annee_scolaire: valeurs.id_annee_scolaire,
      id_classe: valeurs.id_classe || null,
      montant: valeurs.montant ?? null,
      premiere_echeance: valeurs.premiere_echeance,
      nombre_echeances: valeurs.nombre_echeances,
      valider,
    };
  }

  private appeler(valider: boolean, sur: (r: ResultatGenerationFrais) => void): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    this.finance.genererFrais(this.corps(valider)).subscribe({
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

  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return 'La génération a échoué.';
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
