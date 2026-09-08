import { Component, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EleveService } from './eleve.service';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { Eleve } from '../../core/models/models';

@Component({
  selector: 'app-eleves-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './eleves-list.component.html',
})
export class ElevesListComponent {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  annees = inject(AnneeScolaireService);

  eleves = signal<Eleve[]>([]);
  total = signal(0);
  loading = signal(true);
  filtreStatut = signal<string>('');

  /**
   * Terme cherché, reçu de la barre du bandeau haut.
   *
   * L'écran n'a pas son propre champ de recherche : il n'y en a qu'une dans
   * l'application, en haut, et elle pilote cette liste par l'URL.
   */
  recherche = signal<string>('');

  constructor(private eleveService: EleveService) {
    // On écoute l'URL plutôt que de la lire une fois : depuis cette page,
    // relancer une recherche depuis le bandeau haut ne recrée pas le
    // composant, et un `snapshot` ne verrait jamais le nouveau terme.
    this.route.queryParamMap.subscribe((params) => {
      this.recherche.set(params.get('recherche') ?? '');
      this.charger();
    });

    // La liste suit l'année choisie dans le bandeau haut.
    effect(() => {
      this.annees.idAnnee();
      this.charger();
    });
  }

  charger(): void {
    this.loading.set(true);
    const params: Record<string, string> = {};
    if (this.filtreStatut()) params['statut'] = this.filtreStatut();
    if (this.recherche().trim()) params['search'] = this.recherche().trim();

    const annee = this.annees.idAnnee();
    // Une recherche porte sur toute l'école : on cherche un élève parce qu'on
    // ne sait pas où il est, l'année affichée ne doit pas le cacher.
    if (annee !== null && !this.recherche().trim()) {
      params['id_annee_scolaire'] = String(annee);
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

  /** Retire le terme de l'URL : la liste redevient celle de l'année en cours. */
  effacerRecherche(): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: {} });
  }

  filtrer(statut: string): void {
    this.filtreStatut.set(statut);
    this.charger();
  }

  initiales(e: Eleve): string {
    return `${e.prenom[0] ?? ''}${e.nom[0] ?? ''}`.toUpperCase();
  }
}
