import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { JournalCaisse, Soldes } from '../../core/models/models';
import { FinanceService } from './finance.service';

@Component({
  selector: 'app-journal-caisse',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
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

  ngOnInit(): void {
    this.charger();
    this.finance.soldes().subscribe({ next: (s) => this.soldes.set(s), error: () => {} });
  }

  private get filtres() {
    return { journal: this.journal(), debut: this.debut(), fin: this.fin() };
  }

  charger(): void {
    this.loading.set(true);
    this.erreur.set(null);
    this.finance.journal(this.filtres).subscribe({
      next: (r) => {
        this.mouvements.set(r.results ?? []);
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
