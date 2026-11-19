import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  Classe,
  FraisEleve,
  Niveau,
  Paginated,
  SituationGlobale,
  Soldes,
  StatutFinancier,
  TypeFrais,
} from '../../core/models/models';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { AuthService } from '../../core/services/auth.service';
import { FinanceService } from './finance.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';
import { PaginationComponent } from '../../shared/pagination/pagination.component';

/**
 * Trois lectures du même argent : les échéances ouvertes, toutes les
 * échéances, et — la seule qui réponde à « qui est à jour ? » — une ligne
 * par élève.
 */
type Onglet = 'impayes' | 'tous' | 'eleves';

@Component({
  selector: 'app-finances-list',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, SiAccesDirective, PaginationComponent],
  templateUrl: './finances-list.component.html',
})
export class FinancesListComponent implements OnInit {
  private finance = inject(FinanceService);
  private http = inject(HttpClient);
  auth = inject(AuthService);
  annees = inject(AnneeScolaireService);
  private route = inject(ActivatedRoute);

  frais = signal<FraisEleve[]>([]);
  situation = signal<SituationGlobale | null>(null);
  soldes = signal<Soldes | null>(null);
  classes = signal<Classe[]>([]);
  niveaux = signal<Niveau[]>([]);
  typesFrais = signal<TypeFrais[]>([]);
  loading = signal(true);
  erreur = signal<string | null>(null);
  telechargement = signal(false);

  onglet = signal<Onglet>('impayes');
  classe = signal<string>('');
  niveau = signal<string>('');
  typeFrais = signal<string>('');
  statut = signal<StatutFinancier | ''>('');
  echus = signal(false);
  recherche = signal('');

  /** Pagination des onglets « Impayés » et « Tous les frais ». */
  page = signal(1);
  total = signal(0);
  readonly taillePage = 20;

  readonly statuts: { value: StatutFinancier | ''; label: string }[] = [
    { value: '', label: 'Tous les statuts' },
    { value: 'a_jour', label: 'À jour' },
    { value: 'partiel', label: 'Partiel' },
    { value: 'en_retard', label: 'En retard' },
    { value: 'non_paye', label: 'Non payé' },
    { value: 'sans_frais', label: 'Aucun frais' },
  ];

  /** Facturer l'année demande l'écriture sur les frais ; encaisser, sur l'encaissement. */
  peutFacturer = computed(() => this.auth.peutEcrire('frais'));
  peutEncaisser = computed(() => this.auth.peutEcrire('encaissement'));
  peutRelancer = computed(() => this.auth.peutEcrire('relances'));

  /** La recherche est faite par le serveur, sur toutes les pages. */
  lignes = computed(() => this.frais());

  /** Lignes de la vue « par élève » : le filtrage est fait par le serveur. */
  eleves = computed(() => this.situation()?.eleves ?? []);

  totalDu = computed(() => this.somme((f) => Number(f.montant_du)));
  totalPaye = computed(() => this.somme((f) => Number(f.montant_paye ?? 0)));
  totalSolde = computed(() =>
    this.somme((f) => Number(f.solde_restant ?? f.montant_du)),
  );

  private somme(extraire: (f: FraisEleve) => number): number {
    return this.lignes().reduce((total, f) => total + extraire(f), 0);
  }

  ngOnInit(): void {
    // Le tableau de bord renvoie ici avec un statut : « les 12 élèves en
    // retard » doit ouvrir leur liste, pas la liste des échéances.
    const statut = this.route.snapshot.queryParamMap.get('statut');
    if (statut && this.statuts.some((s) => s.value === statut)) {
      this.statut.set(statut as StatutFinancier);
      this.onglet.set('eleves');
    }
    this.charger();
    this.finance.soldes().subscribe({ next: (s) => this.soldes.set(s), error: () => {} });
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/?page_size=200`)
      .subscribe((r) => this.classes.set(r.results ?? []));
    this.http
      .get<Paginated<TypeFrais>>(`${environment.apiUrl}/types-frais/?page_size=200`)
      .subscribe({ next: (r) => this.typesFrais.set(r.results ?? []), error: () => {} });
    this.http
      .get<Paginated<Niveau>>(`${environment.apiUrl}/niveaux/?page_size=200`)
      .subscribe({ next: (r) => this.niveaux.set(r.results ?? []), error: () => {} });
  }

  private get filtres() {
    return {
      classe: this.classe(),
      type_frais: this.typeFrais(),
      echus: this.echus() ? 'true' : '',
    };
  }

  /**
   * Recharge depuis la première page : tout changement de filtre passe par
   * ici, et rester en page 4 d'une liste devenue plus courte afficherait vide.
   */
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

    if (this.onglet() === 'eleves') {
      // Le filtrage se fait côté serveur : c'est lui qui sait quel élève est
      // à jour, et le recalculer ici finirait par diverger du dossier.
      this.finance
        .situationGlobale({
          ...this.annees.params(),
          classe: this.classe(),
          niveau: this.niveau(),
          statut: this.statut(),
          q: this.recherche().trim(),
        })
        .subscribe({
          next: (s) => {
            this.situation.set(s);
            this.loading.set(false);
          },
          error: () => {
            this.erreur.set('Impossible de charger la situation des élèves.');
            this.loading.set(false);
          },
        });
      return;
    }

    const filtres = {
      ...this.filtres,
      search: this.recherche().trim(),
      page: String(this.page()),
      page_size: String(this.taillePage),
    };
    const source =
      this.onglet() === 'impayes'
        ? this.finance.impayes(filtres)
        : this.finance.frais(filtres);

    source.subscribe({
      next: (res) => {
        const lignes = res.results ?? (res as unknown as FraisEleve[]);
        this.frais.set(lignes);
        this.total.set(res.count ?? lignes.length);
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

  /**
   * Tous les onglets filtrent côté serveur : les listes sont paginées, une
   * recherche limitée à la page affichée manquerait les autres élèves.
   */
  surRecherche(terme: string): void {
    this.recherche.set(terme);
    // On attend la fin de la frappe : une requête par lettre tapée pourrait
    // revenir dans le désordre et afficher le résultat d'un terme dépassé.
    clearTimeout(this.minuterieRecherche);
    this.minuterieRecherche = setTimeout(() => this.charger(), 300);
  }

  private minuterieRecherche?: ReturnType<typeof setTimeout>;

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
