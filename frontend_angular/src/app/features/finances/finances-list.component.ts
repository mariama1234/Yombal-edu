import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { Classe, FraisEleve, Paginated, Soldes, TypeFrais } from '../../core/models/models';
import { AuthService } from '../../core/services/auth.service';
import { FinanceService } from './finance.service';

type Onglet = 'impayes' | 'tous';

@Component({
  selector: 'app-finances-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './finances-list.component.html',
})
export class FinancesListComponent implements OnInit {
  private finance = inject(FinanceService);
  private http = inject(HttpClient);
  auth = inject(AuthService);

  frais = signal<FraisEleve[]>([]);
  soldes = signal<Soldes | null>(null);
  classes = signal<Classe[]>([]);
  typesFrais = signal<TypeFrais[]>([]);
  loading = signal(true);
  erreur = signal<string | null>(null);
  telechargement = signal(false);

  onglet = signal<Onglet>('impayes');
  classe = signal<string>('');
  typeFrais = signal<string>('');
  echus = signal(false);
  recherche = signal('');

  /** Seul un admin facture l'année ; le caissier encaisse. */
  estAdmin = computed(() => this.auth.currentUser()?.role === 'admin');

  lignes = computed(() => {
    const terme = this.recherche().trim().toLowerCase();
    if (!terme) return this.frais();
    return this.frais().filter((f) =>
      `${f.eleve_prenom ?? ''} ${f.eleve_nom ?? ''}`.toLowerCase().includes(terme),
    );
  });

  totalDu = computed(() => this.somme((f) => Number(f.montant_du)));
  totalPaye = computed(() => this.somme((f) => Number(f.montant_paye ?? 0)));
  totalSolde = computed(() =>
    this.somme((f) => Number(f.solde_restant ?? f.montant_du)),
  );

  private somme(extraire: (f: FraisEleve) => number): number {
    return this.lignes().reduce((total, f) => total + extraire(f), 0);
  }

  ngOnInit(): void {
    this.charger();
    this.finance.soldes().subscribe({ next: (s) => this.soldes.set(s), error: () => {} });
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/`)
      .subscribe((r) => this.classes.set(r.results ?? []));
    this.http
      .get<Paginated<TypeFrais>>(`${environment.apiUrl}/types-frais/`)
      .subscribe({ next: (r) => this.typesFrais.set(r.results ?? []), error: () => {} });
  }

  private get filtres() {
    return {
      classe: this.classe(),
      type_frais: this.typeFrais(),
      echus: this.echus() ? 'true' : '',
    };
  }

  charger(): void {
    this.loading.set(true);
    this.erreur.set(null);
    const source =
      this.onglet() === 'impayes'
        ? this.finance.impayes(this.filtres)
        : this.finance.frais(this.filtres);

    source.subscribe({
      next: (res) => {
        this.frais.set(res.results ?? (res as unknown as FraisEleve[]));
        this.loading.set(false);
      },
      error: () => {
        this.erreur.set("Impossible de charger les frais. Vérifie que l'API est démarrée.");
        this.loading.set(false);
      },
    });
  }

  changerOnglet(onglet: Onglet): void {
    this.onglet.set(onglet);
    this.charger();
  }

  basculerEchus(): void {
    this.echus.update((v) => !v);
    this.charger();
  }

  exporter(format: 'xlsx' | 'pdf'): void {
    this.telechargement.set(true);
    this.finance
      .telecharger('/frais-eleves/impayes/export/', { ...this.filtres, format_export: format })
      .subscribe({
        next: () => this.telechargement.set(false),
        error: () => {
          this.erreur.set("L'export a échoué.");
          this.telechargement.set(false);
        },
      });
  }
}
