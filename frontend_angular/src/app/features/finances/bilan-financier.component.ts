import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { AnneeScolaire, Bilan, LigneVentilation, Paginated } from '../../core/models/models';
import { FinanceService } from './finance.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';

/**
 * Synthèse « Budget, Dépenses & Bilan » du TDR.
 *
 * L'évolution mensuelle est dessinée en barres CSS plutôt qu'avec une
 * bibliothèque de graphiques : deux séries sur douze mois ne justifient pas
 * d'embarquer un moteur de rendu dans le bundle.
 */
@Component({
  selector: 'app-bilan-financier',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, SiAccesDirective],
  templateUrl: './bilan-financier.component.html',
})
export class BilanFinancierComponent implements OnInit {
  private finance = inject(FinanceService);
  private http = inject(HttpClient);

  bilan = signal<Bilan | null>(null);
  annees = signal<AnneeScolaire[]>([]);
  annee = signal<string>('');
  loading = signal(true);
  erreur = signal<string | null>(null);
  telechargement = signal(false);

  /** Échelle commune aux deux séries, pour que les barres restent comparables. */
  maxEvolution = computed(() => {
    const lignes = this.bilan()?.evolution ?? [];
    return Math.max(1, ...lignes.map((l) => Math.max(l.recettes, l.depenses)));
  });

  ngOnInit(): void {
    this.http
      .get<Paginated<AnneeScolaire>>(`${environment.apiUrl}/annees-scolaires/`)
      .subscribe((r) => this.annees.set(r.results ?? []));
    this.charger();
  }

  charger(): void {
    this.loading.set(true);
    this.erreur.set(null);
    this.finance.bilan({ annee: this.annee() }).subscribe({
      next: (b) => {
        this.bilan.set(b);
        this.annee.set(b.periode.id_annee_scolaire ? String(b.periode.id_annee_scolaire) : '');
        this.loading.set(false);
      },
      error: () => {
        this.erreur.set('Impossible de charger le bilan.');
        this.loading.set(false);
      },
    });
  }

  /** Part d'une ligne dans son total, pour la barre de répartition. */
  part(ligne: LigneVentilation, total: number): number {
    return total ? Math.round((ligne.montant / total) * 100) : 0;
  }

  hauteur(valeur: number): number {
    return Math.round((valeur / this.maxEvolution()) * 100);
  }

  exporter(format: 'xlsx' | 'pdf'): void {
    this.telechargement.set(true);
    this.finance
      .telecharger('/finances/bilan/', { annee: this.annee(), format_export: format })
      .subscribe({
        next: () => this.telechargement.set(false),
        error: () => {
          this.erreur.set("L'export a échoué.");
          this.telechargement.set(false);
        },
      });
  }
}
