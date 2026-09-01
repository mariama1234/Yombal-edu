import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { EleveService } from './eleve.service';
import { Eleve } from '../../core/models/models';

@Component({
  selector: 'app-eleves-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './eleves-list.component.html',
})
export class ElevesListComponent implements OnInit {
  eleves = signal<Eleve[]>([]);
  total = signal(0);
  loading = signal(true);
  filtreStatut = signal<string>('');

  constructor(private eleveService: EleveService) {}

  ngOnInit(): void {
    this.charger();
  }

  charger(): void {
    this.loading.set(true);
    const params: Record<string, string> = this.filtreStatut() ? { statut: this.filtreStatut() } : {};
    this.eleveService.list(params).subscribe({
      next: (res) => {
        this.eleves.set(res.results);
        this.total.set(res.count);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  filtrer(statut: string): void {
    this.filtreStatut.set(statut);
    this.charger();
  }

  initiales(e: Eleve): string {
    return `${e.prenom[0] ?? ''}${e.nom[0] ?? ''}`.toUpperCase();
  }
}
