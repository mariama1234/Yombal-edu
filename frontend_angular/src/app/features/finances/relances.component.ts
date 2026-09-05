import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Relance, ResultatRelances } from '../../core/models/models';
import { FinanceService } from './finance.service';

/**
 * Relances des impayés.
 *
 * Comme la génération des frais, l'envoi passe par un aperçu : on voit
 * combien de familles seraient contactées, et lesquelles, avant d'envoyer.
 */
@Component({
  selector: 'app-relances',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './relances.component.html',
})
export class RelancesComponent implements OnInit {
  private fb = inject(FormBuilder);
  private finance = inject(FinanceService);

  historique = signal<Relance[]>([]);
  apercu = signal<ResultatRelances | null>(null);
  resultat = signal<ResultatRelances | null>(null);
  erreur = signal<string | null>(null);
  occupe = signal(false);

  form = this.fb.group({
    canal: ['sms', Validators.required],
    jours_retard: [0, [Validators.required, Validators.min(0)]],
    delai_min: [7, [Validators.required, Validators.min(0)]],
  });

  ngOnInit(): void {
    this.chargerHistorique();
    this.form.valueChanges.subscribe(() => this.apercu.set(null));
  }

  private chargerHistorique(): void {
    this.finance.relances().subscribe({
      next: (r) => this.historique.set(r.results ?? []),
      error: () => {},
    });
  }

  private appeler(valider: boolean, sur: (r: ResultatRelances) => void): void {
    this.occupe.set(true);
    this.erreur.set(null);
    this.finance
      .genererRelances({ ...this.form.getRawValue(), valider })
      .subscribe({
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

  envoyer(): void {
    this.appeler(true, (r) => {
      this.resultat.set(r);
      this.apercu.set(null);
      this.chargerHistorique();
    });
  }

  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return "L'envoi des relances a échoué.";
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
