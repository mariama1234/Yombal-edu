import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { GenericCrudService } from './generic-crud.service';
import { ENTITY_CONFIGS } from './entity-configs';
import { EntityConfig, RowAction } from './crud-config.model';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';

@Component({
  selector: 'app-generic-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './generic-list.component.html',
})
export class GenericListComponent implements OnInit {
  config!: EntityConfig;
  items = signal<any[]>([]);
  loading = signal(true);
  total = signal(0);
  erreur = signal<string | null>(null);

  annees = inject(AnneeScolaireService);

  constructor(private route: ActivatedRoute, private router: Router, private crud: GenericCrudService<any>) {
    // Changer d'année dans le bandeau haut recharge la liste courante, sans
    // que l'utilisateur ait à revenir dessus.
    effect(() => {
      this.annees.idAnnee();
      if (this.config?.filtreParAnnee) this.charger();
    });
  }

  ngOnInit(): void {
    this.route.data.subscribe((data) => {
      this.config = ENTITY_CONFIGS[data['entityKey']];
      this.charger();
    });
  }

  /** Vrai quand la liste affichée est restreinte à une année archivée. */
  surUneArchive(): boolean {
    return !!this.config?.filtreParAnnee && this.annees.consulteUneArchive();
  }

  libelleAnnee(): string {
    return this.annees.anneeCourante()?.libelle ?? '';
  }

  charger(): void {
    this.loading.set(true);
    const params: Record<string, string> = {};
    const id = this.annees.idAnnee();
    if (this.config.filtreParAnnee && id !== null) {
      params['id_annee_scolaire'] = String(id);
    }
    this.crud.list(this.config.endpoint, params).subscribe({
      next: (res) => {
        this.items.set(res.results);
        this.total.set(res.count);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  supprimer(id: number): void {
    if (!confirm('Supprimer cet élément ?')) return;
    this.crud.remove(this.config.endpoint, id).subscribe(() => this.charger());
  }

  ouvrirNouveau(): void {
    this.router.navigate([this.router.url, 'nouveau']);
  }

  actionVisible(action: RowAction, item: any): boolean {
    if (!action.visibleSi) return true;
    return item[action.visibleSi.champ] === action.visibleSi.valeur;
  }

  lancerAction(action: RowAction, item: any): void {
    if (action.confirmation && !confirm(action.confirmation)) return;
    this.erreur.set(null);
    this.crud.action(this.config.endpoint, item.id, action.chemin).subscribe({
      next: () => this.charger(),
      error: (e) => this.erreur.set(e?.error?.detail ?? "L'action a échoué."),
    });
  }
}
