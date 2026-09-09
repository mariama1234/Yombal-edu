import { Component, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { EleveService } from './eleve.service';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import {
  Classe,
  Eleve,
  EleveAReinscrire,
  Niveau,
  Paginated,
} from '../../core/models/models';

/**
 * Les trois populations d'une rentrée, qu'un seul tableau confondait :
 *
 * * `inscrits` — inscription confirmée par au moins un règlement ;
 * * `attente` — place réservée, rien d'encaissé : pré-inscrits ;
 * * `a_reinscrire` — inscrits une année précédente, pas encore repassés.
 *
 * La troisième est la plus importante : ces élèves-là n'apparaissaient dans
 * aucune liste dès qu'on basculait sur la nouvelle année. Ils existaient en
 * base, et nulle part à l'écran.
 */
type Vue = 'inscrits' | 'attente' | 'a_reinscrire';

const VUES: Vue[] = ['inscrits', 'attente', 'a_reinscrire'];

@Component({
  selector: 'app-eleves-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './eleves-list.component.html',
})
export class ElevesListComponent {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private http = inject(HttpClient);
  annees = inject(AnneeScolaireService);

  eleves = signal<Eleve[]>([]);
  aReinscrire = signal<EleveAReinscrire[]>([]);
  classes = signal<Classe[]>([]);
  niveaux = signal<Niveau[]>([]);
  total = signal(0);
  loading = signal(true);
  filtreStatut = signal<string>('');
  vue = signal<Vue>('inscrits');
  erreur = signal<string | null>(null);

  /** Pagination de la file : cinq cents lignes ne se traitent pas d'un écran. */
  page = signal(1);
  pages = signal(1);
  precedent = signal(false);
  suivant = signal(false);
  readonly taillePage = 20;

  /** Filtres propres à la file de réinscription. */
  classeSource = signal<string>('');
  niveauSource = signal<string>('');

  /**
   * Terme cherché, reçu de la barre du bandeau haut.
   *
   * L'écran n'a pas son propre champ de recherche : il n'y en a qu'une dans
   * l'application, en haut, et elle pilote cette liste par l'URL.
   */
  recherche = signal<string>('');

  libelleAnnee = computed(() => this.annees.anneeCourante()?.libelle ?? '');

  /** Classes de l'année précédente, pour filtrer la file par classe de départ. */
  classesSource = computed(() => {
    const vues = new Map<number, string>();
    for (const e of this.aReinscrire()) vues.set(e.id_derniere_classe, e.derniere_classe);
    return [...vues].map(([id, nom]) => ({ id, nom })).sort((a, b) => a.nom.localeCompare(b.nom));
  });

  constructor(private eleveService: EleveService) {
    // On écoute l'URL plutôt que de la lire une fois : depuis cette page,
    // relancer une recherche depuis le bandeau haut ne recrée pas le
    // composant, et un `snapshot` ne verrait jamais le nouveau terme.
    this.route.queryParamMap.subscribe((params) => {
      this.recherche.set(params.get('recherche') ?? '');
      // Revenir de l'écran de réinscription doit rouvrir la bonne vue.
      const vue = params.get('vue');
      if (vue && VUES.includes(vue as Vue)) this.vue.set(vue as Vue);
      this.charger();
    });

    // La liste suit l'année choisie dans le bandeau haut.
    effect(() => {
      this.annees.idAnnee();
      this.charger();
      this.chargerReferentiels();
    });
  }

  private chargerReferentiels(): void {
    this.http
      .get<Paginated<Niveau>>(`${environment.apiUrl}/niveaux/`)
      .subscribe({ next: (r) => this.niveaux.set(r.results ?? []), error: () => {} });
    const annee = this.annees.idAnnee();
    if (annee === null) return;
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/?id_annee_scolaire=${annee}`)
      .subscribe({ next: (r) => this.classes.set(r.results ?? []), error: () => {} });
  }

  charger(): void {
    this.loading.set(true);
    this.erreur.set(null);
    const annee = this.annees.idAnnee();

    if (this.vue() === 'a_reinscrire') {
      const params: Record<string, string> = {
        page: String(this.page()),
        page_size: String(this.taillePage),
      };
      if (annee !== null) params['id_annee_scolaire'] = String(annee);
      if (this.recherche().trim()) params['q'] = this.recherche().trim();
      if (this.classeSource()) params['classe'] = this.classeSource();
      if (this.niveauSource()) params['niveau'] = this.niveauSource();

      this.eleveService.aReinscrire(params).subscribe({
        next: (file) => {
          this.aReinscrire.set(file.eleves ?? []);
          this.total.set(file.total ?? 0);
          this.page.set(file.page ?? 1);
          this.pages.set(file.pages ?? 1);
          this.precedent.set(!!file.precedent);
          this.suivant.set(!!file.suivant);
          this.loading.set(false);
        },
        error: () => {
          this.erreur.set("La file de réinscription n'a pas pu être chargée.");
          this.loading.set(false);
        },
      });
      return;
    }

    const params: Record<string, string> = {};
    if (this.filtreStatut()) params['statut'] = this.filtreStatut();
    if (this.recherche().trim()) params['search'] = this.recherche().trim();

    // Une recherche porte sur toute l'école : on cherche un élève parce qu'on
    // ne sait pas où il est, l'année affichée ne doit pas le cacher.
    if (annee !== null && !this.recherche().trim()) {
      params['id_annee_scolaire'] = String(annee);
      params['inscription'] = this.vue() === 'attente' ? 'en_attente' : 'confirmee';
    }

    this.eleveService.list(params).subscribe({
      next: (res) => {
        this.eleves.set(res.results);
        this.total.set(res.count);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  changerVue(vue: Vue): void {
    this.vue.set(vue);
    this.page.set(1);
    this.erreur.set(null);
    this.charger();
  }

  filtrerFile(champ: 'classe' | 'niveau', valeur: string): void {
    if (champ === 'classe') this.classeSource.set(valeur);
    else this.niveauSource.set(valeur);
    // Un filtre change le nombre de pages : rester en page 4 afficherait vide.
    this.page.set(1);
    this.charger();
  }

  allerPage(delta: number): void {
    const cible = this.page() + delta;
    if (cible < 1 || cible > this.pages()) return;
    this.page.set(cible);
    this.charger();
    // La pagination ne change pas d'URL : le retour en haut est explicite.
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /** Retire le terme de l'URL : la liste redevient celle de l'année en cours. */
  effacerRecherche(): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: {} });
  }

  filtrer(statut: string): void {
    this.filtreStatut.set(statut);
    this.charger();
  }

  reinscrire(eleve: EleveAReinscrire): void {
    this.router.navigate(['/eleves', eleve.id, 'reinscrire']);
  }

  initiales(e: { nom: string; prenom: string }): string {
    return `${e.prenom[0] ?? ''}${e.nom[0] ?? ''}`.toUpperCase();
  }
}
