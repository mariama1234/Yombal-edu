import { Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { EleveService } from './eleve.service';
import { AuthService } from '../../core/services/auth.service';
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
 * * `attente` — droits d'inscription ou premier mois non réglés : inscrits incomplets ;
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
  private auth = inject(AuthService);

  /** Inscrire, importer, réinscrire : l'écriture sur les inscriptions. */
  peutInscrire = computed(() => this.auth.peutEcrire('inscriptions'));

  eleves = signal<Eleve[]>([]);
  aReinscrire = signal<EleveAReinscrire[]>([]);
  /** Classes de toutes les années : la liste déroulante les propose toutes. */
  classes = signal<Classe[]>([]);
  niveaux = signal<Niveau[]>([]);
  total = signal(0);
  loading = signal(true);
  filtreStatut = signal<string>('');
  /** Classe choisie pour les vues Inscrits et Inscrits incomplets. */
  filtreClasse = signal<string>('');
  /** Tant que les classes ne sont pas chargées, on ne sait pas laquelle afficher. */
  classesChargees = signal(false);
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

  classeChoisie = computed(
    () => this.classes().find((c) => String(c.id) === this.filtreClasse()) ?? null,
  );

  /** L'année de la classe choisie prime sur celle du bandeau haut. */
  libelleAnnee = computed(() => {
    const id = this.classeChoisie()?.id_annee_scolaire ?? this.annees.idAnnee();
    return this.annees.annees().find((a) => a.id === id)?.libelle ?? '';
  });

  /** Les classes rangées par année, la plus récente d'abord. */
  classesParAnnee = computed(() =>
    this.annees
      .annees()
      .map((annee) => ({
        annee,
        classes: this.classes().filter((c) => c.id_annee_scolaire === annee.id),
      }))
      .filter((groupe) => groupe.classes.length > 0),
  );

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
      if (vue && VUES.includes(vue as Vue)
          && (vue !== 'a_reinscrire' || this.peutInscrire())) this.vue.set(vue as Vue);
      // Depuis la liste des classes : « Élèves » ouvre la liste sur cette classe.
      const classe = params.get('classe');
      if (classe) this.filtreClasse.set(classe);
      this.charger();
    });

    // Toutes années confondues : ne dépend pas du bandeau haut.
    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/?page_size=200`)
      .subscribe({
        next: (r) => {
          this.classes.set(r.results ?? []);
          this.classesChargees.set(true);
        },
        error: () => {
          this.classesChargees.set(true);
        },
      });

    // La liste suit l'année choisie dans le bandeau haut.
    effect(() => {
      this.annees.idAnnee();
      untracked(() => this.retirerClasseDuneAutreAnnee());
      this.charger();
      this.chargerReferentiels();
    });
  }

  private chargerReferentiels(): void {
    this.http
      .get<Paginated<Niveau>>(`${environment.apiUrl}/niveaux/`)
      .subscribe({ next: (r) => this.niveaux.set(r.results ?? []), error: () => {} });
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
    const recherche = this.recherche().trim();
    if (this.filtreStatut()) params['statut'] = this.filtreStatut();
    if (recherche) params['search'] = recherche;

    // Sans classe choisie, tous les inscrits de l'année, toutes classes
    // confondues ; la liste déroulante restreint ensuite à une classe.
    const classe = untracked(() => this.classeChoisie());
    // Une classe appartient à une année : choisir une classe de 2025-2026
    // doit montrer ses élèves de 2025-2026, quelle que soit l'année du bandeau.
    const anneeListe = recherche ? annee : classe?.id_annee_scolaire ?? annee;
    if (classe && !recherche) params['classe'] = String(classe.id);

    // Une recherche porte sur toute l'école : on cherche un élève parce qu'on
    // ne sait pas où il est, l'année affichée ne doit pas le cacher.
    if (anneeListe !== null && !this.recherche().trim()) {
      params['id_annee_scolaire'] = String(anneeListe);
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
    // « Tous » montre tous les élèves inscrits, dans toutes les classes.
    if (!statut) this.filtreClasse.set('');
    this.charger();
  }

  /** Changer d'année dans le bandeau retire une classe qui n'en fait pas partie. */
  private retirerClasseDuneAutreAnnee(): void {
    const actuelle = this.classeChoisie();
    if (actuelle && actuelle.id_annee_scolaire !== this.annees.idAnnee()) {
      this.filtreClasse.set('');
    }
  }

  filtrerClasse(classe: string): void {
    this.filtreClasse.set(classe);
    this.charger();
  }

  reinscrire(eleve: EleveAReinscrire): void {
    this.router.navigate(['/eleves', eleve.id, 'reinscrire']);
  }

  initiales(e: { nom: string; prenom: string }): string {
    return `${e.prenom[0] ?? ''}${e.nom[0] ?? ''}`.toUpperCase();
  }
}
