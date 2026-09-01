import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { GenericCrudService } from './generic-crud.service';
import { ENTITY_CONFIGS } from './entity-configs';
import { EntityConfig, RowAction } from './crud-config.model';

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

  constructor(private route: ActivatedRoute, private router: Router, private crud: GenericCrudService<any>) {}

  ngOnInit(): void {
    this.route.data.subscribe((data) => {
      this.config = ENTITY_CONFIGS[data['entityKey']];
      this.charger();
    });
  }

  charger(): void {
    this.loading.set(true);
    this.crud.list(this.config.endpoint).subscribe({
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
