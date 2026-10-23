import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { AnneeScolaire, Paginated } from '../models/models';

const CLE_STOCKAGE = 'yombaledu.annee_scolaire';

/**
 * Année scolaire de travail, partagée par toute l'application.
 *
 * Le sélecteur du bandeau haut écrit ici ; les listes le lisent et passent
 * `id_annee_scolaire` à l'API. Consulter une promotion passée revient donc à
 * changer d'année, sans quitter l'écran où l'on se trouve.
 *
 * Le choix est mémorisé dans le navigateur : rouvrir l'application au milieu
 * d'une consultation d'archives ne doit pas ramener sournoisement sur l'année
 * en cours.
 */
@Injectable({ providedIn: 'root' })
export class AnneeScolaireService {
  private http = inject(HttpClient);

  readonly annees = signal<AnneeScolaire[]>([]);
  readonly idAnnee = signal<number | null>(this.lireStockage());
  readonly chargee = signal(false);

  readonly anneeCourante = computed(
    () => this.annees().find((a) => a.id === this.idAnnee()) ?? null,
  );

  /** Vrai quand on consulte autre chose que l'année en cours. */
  readonly consulteUneArchive = computed(() => {
    const annee = this.anneeCourante();
    return annee !== null && annee.statut !== 'active';
  });

  private enCours = false;

  charger(): void {
    if (this.chargee() || this.enCours) return;
    this.lire();
  }

  /**
   * Relit la liste après une création, une modification ou une suppression
   * d'année. Sans ça, une école qui ouvrait sa première année restait sur
   * « Aucune année » jusqu'au prochain rafraîchissement de la page.
   *
   * `activer` : l'année tout juste ouverte comme année en cours devient celle
   * du bandeau — c'est sur elle qu'on s'apprête à travailler.
   */
  recharger(activer?: number): void {
    this.lire(activer);
  }

  /**
   * Oublie la liste à la déconnexion : le service survit au changement de
   * compte, et le suivant — une autre école, ou le super administrateur qui
   * n'en a aucune — aurait hérité des années du précédent.
   */
  vider(): void {
    this.annees.set([]);
    this.chargee.set(false);
  }

  private lire(activer?: number): void {
    this.enCours = true;
    this.http
      .get<Paginated<AnneeScolaire>>(`${environment.apiUrl}/annees-scolaires/`)
      .subscribe({
        next: (r) => {
          const annees = r.results ?? [];
          this.annees.set(annees);
          this.chargee.set(true);
          this.enCours = false;

          if (activer !== undefined && annees.some((a) => a.id === activer)) {
            this.definir(activer);
            return;
          }
          // L'année mémorisée peut avoir été supprimée depuis, ou appartenir à
          // un autre compte : on retombe alors sur l'année active.
          const memorisee = this.idAnnee();
          if (memorisee === null || !annees.some((a) => a.id === memorisee)) {
            const active = annees.find((a) => a.statut === 'active') ?? annees[0];
            this.definir(active ? active.id : null);
          }
        },
        // Pas de `chargee` sur un échec : le prochain écran retentera, au
        // lieu de figer « Aucune année » pour toute la session.
        error: () => (this.enCours = false),
      });
  }

  definir(id: number | null): void {
    this.idAnnee.set(id);
    try {
      if (id === null) localStorage.removeItem(CLE_STOCKAGE);
      else localStorage.setItem(CLE_STOCKAGE, String(id));
    } catch {
      // Navigation privée ou stockage refusé : le choix vaut pour la session.
    }
  }

  /** Paramètres de requête à joindre à une liste filtrable par année. */
  params(): Record<string, number> {
    const id = this.idAnnee();
    return id === null ? {} : { id_annee_scolaire: id };
  }

  private lireStockage(): number | null {
    try {
      const brut = localStorage.getItem(CLE_STOCKAGE);
      return brut ? Number(brut) : null;
    } catch {
      return null;
    }
  }
}
