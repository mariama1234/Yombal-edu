import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { GenericCrudService } from '../../shared/crud/generic-crud.service';
import { ENTITY_CONFIGS } from '../../shared/crud/entity-configs';
import { EntityConfig } from '../../shared/crud/crud-config.model';
import { AuthService } from '../../core/services/auth.service';
import { ToastService } from '../../core/services/toast.service';

/** Une ligne de GET /api/corbeille/. */
export interface ElementCorbeille {
  id: number;
  modele: string;
  entite: string;
  id_entite: number;
  libelle: string;
  type_libelle: string;
  resume: Record<string, number>;
  /** « 30 inscriptions, 4 évaluations » : ce qui reviendra avec l'élément. */
  emporte: string;
  nb_elements: number;
  supprime_par: string | null;
  id_utilisateur: number | null;
  date_suppression: string;
}

/**
 * Les éléments supprimés, et le moyen de les remettre en place.
 *
 * Ouvert depuis un module (`?module=classes`), l'écran ne montre que ce qui a
 * été supprimé dans ce module ; depuis le menu, toute la corbeille.
 */
@Component({
  selector: 'app-corbeille',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './corbeille.component.html',
})
export class CorbeilleComponent {
  private route = inject(ActivatedRoute);
  private crud: GenericCrudService<any> = inject(GenericCrudService);
  private auth = inject(AuthService);
  private toast = inject(ToastService);

  elements = signal<ElementCorbeille[]>([]);
  total = signal(0);
  loading = signal(true);
  erreur = signal<string | null>(null);
  succes = signal<string | null>(null);
  /** Élément dont la restauration ou la suppression est en cours. */
  enCours = signal<number | null>(null);

  page = signal(1);
  precedent = signal(false);
  suivant = signal(false);

  module = signal<EntityConfig | null>(null);
  estAdmin = computed(() => this.auth.currentUser()?.role === 'admin');

  constructor() {
    this.route.queryParamMap.subscribe((params) => {
      const cle = params.get('module');
      this.module.set(cle ? ENTITY_CONFIGS[cle] ?? null : null);
      this.page.set(1);
      this.succes.set(null);
      this.erreur.set(null);
      this.charger();
    });
  }

  charger(): void {
    this.loading.set(true);
    const params: Record<string, string> = { page: String(this.page()) };
    const module = this.module();
    if (module) params['endpoint'] = module.endpoint;

    this.crud.list('corbeille', params).subscribe({
      next: (res) => {
        this.elements.set(res.results ?? []);
        this.total.set(res.count ?? 0);
        this.precedent.set(!!res.previous);
        this.suivant.set(!!res.next);
        this.loading.set(false);
      },
      error: () => {
        this.erreur.set("La corbeille n'a pas pu être chargée.");
        this.loading.set(false);
      },
    });
  }

  restaurer(element: ElementCorbeille): void {
    this.erreur.set(null);
    this.succes.set(null);
    this.enCours.set(element.id);
    this.crud.action('corbeille', element.id, 'restaurer').subscribe({
      next: () => {
        this.toast.succes('Élément restauré',
          `« ${element.libelle} » est de retour${element.emporte ? `, avec ${element.emporte}` : ''}.`);
        this.enCours.set(null);
        this.reculerSiPageVide();
        this.charger();
      },
      error: (e) => {
        this.erreur.set(e?.error?.detail ?? 'La restauration a échoué.');
        this.enCours.set(null);
      },
    });
  }

  supprimerDefinitivement(element: ElementCorbeille): void {
    const emporte = element.emporte ? ` Cela effacera aussi : ${element.emporte}.` : '';
    if (!confirm(`Supprimer définitivement « ${element.libelle} » ?${emporte} Cette action est irréversible.`)) {
      return;
    }
    this.erreur.set(null);
    this.succes.set(null);
    this.enCours.set(element.id);
    this.crud.remove('corbeille', element.id).subscribe({
      next: () => {
        this.toast.succes('Supprimé définitivement', `« ${element.libelle} »`);
        this.enCours.set(null);
        this.reculerSiPageVide();
        this.charger();
      },
      error: (e) => {
        this.erreur.set(e?.error?.detail ?? 'La suppression a échoué.');
        this.enCours.set(null);
      },
    });
  }

  allerPage(delta: number): void {
    this.page.update((p) => Math.max(1, p + delta));
    this.charger();
  }

  /** Vider la dernière ligne d'une page ne doit pas laisser sur une page vide. */
  private reculerSiPageVide(): void {
    if (this.elements().length === 1 && this.page() > 1) this.page.update((p) => p - 1);
  }
}
