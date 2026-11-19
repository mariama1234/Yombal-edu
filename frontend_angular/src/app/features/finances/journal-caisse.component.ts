import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { JournalCaisse, Soldes } from '../../core/models/models';
import { FinanceService } from './finance.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';
import { PaginationComponent } from '../../shared/pagination/pagination.component';

@Component({
  selector: 'app-journal-caisse',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, SiAccesDirective, PaginationComponent],
  templateUrl: './journal-caisse.component.html',
})
export class JournalCaisseComponent implements OnInit {
  private finance = inject(FinanceService);

  mouvements = signal<JournalCaisse[]>([]);
  soldes = signal<Soldes | null>(null);
  loading = signal(true);
  erreur = signal<string | null>(null);
  telechargement = signal(false);

  journal = signal<'' | 'caisse' | 'banque'>('');
  debut = signal('');
  fin = signal('');

  page = signal(1);
  total = signal(0);
  readonly taillePage = 20;

  ngOnInit(): void {
    this.charger();
    this.finance.soldes().subscribe({ next: (s) => this.soldes.set(s), error: () => {} });
  }

  private get filtres() {
    return { journal: this.journal(), debut: this.debut(), fin: this.fin() };
  }

  /** Tout changement de filtre repart de la première page. */
  charger(): void {
    this.page.set(1);
    this.lire();
  }

  allerPage(page: number): void {
    this.page.set(page);
    this.lire();
  }

  private lire(): void {
    this.loading.set(true);
    this.erreur.set(null);
    this.finance.journal({ ...this.filtres, page: this.page(), page_size: this.taillePage }).subscribe({
      next: (r) => {
        this.mouvements.set(r.results ?? []);
        this.total.set(r.count ?? 0);
        this.loading.set(false);
      },
      error: () => {
        this.erreur.set('Impossible de charger le journal.');
        this.loading.set(false);
      },
    });
  }

  filtrer(type: '' | 'caisse' | 'banque'): void {
    this.journal.set(type);
    this.charger();
  }

  exporter(format: 'xlsx' | 'pdf'): void {
    this.telechargement.set(true);
    this.finance.telecharger('/journal-caisse/export/', { ...this.filtres, format_export: format }).subscribe({
      next: () => this.telechargement.set(false),
      error: () => {
        this.erreur.set("L'export a échoué.");
        this.telechargement.set(false);
      },
    });
  }
}
