import { Component, OnInit, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';
import { EleveService } from './eleve.service';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { Eleve } from '../../core/models/models';

@Component({
  selector: 'app-eleves-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './eleves-list.component.html',
})
export class ElevesListComponent implements OnInit {
  private route = inject(ActivatedRoute);
  annees = inject(AnneeScolaireService);

  eleves = signal<Eleve[]>([]);
  total = signal(0);
  loading = signal(true);
  filtreStatut = signal<string>('');
  recherche = signal<string>('');

  private frappes = new Subject<string>();

  constructor(private eleveService: EleveService) {
    this.frappes
      .pipe(debounceTime(250), distinctUntilChanged())
      .subscribe(() => this.charger());

    // La liste suit l'année choisie dans le bandeau haut.
    effect(() => {
      this.annees.idAnnee();
      this.charger();
    });
  }

  ngOnInit(): void {
    // La recherche globale renvoie ici avec son terme : l'écran doit s'ouvrir
    // déjà filtré, sinon la recherche est perdue en chemin.
    const terme = this.route.snapshot.queryParamMap.get('recherche');
    if (terme) this.recherche.set(terme);
    this.charger();
  }

  charger(): void {
    this.loading.set(true);
    const params: Record<string, string> = {};
    if (this.filtreStatut()) params['statut'] = this.filtreStatut();
    if (this.recherche().trim()) params['search'] = this.recherche().trim();

    const annee = this.annees.idAnnee();
    // Une recherche cherche dans toute l'école : on trouve un élève parce
    // qu'on ne sait pas où il est, l'année affichée ne doit pas le cacher.
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

  chercher(valeur: string): void {
    this.recherche.set(valeur);
    this.frappes.next(valeur);
  }

  effacerRecherche(): void {
    this.recherche.set('');
    this.charger();
  }

  filtrer(statut: string): void {
    this.filtreStatut.set(statut);
    this.charger();
  }

  initiales(e: Eleve): string {
    return `${e.prenom[0] ?? ''}${e.nom[0] ?? ''}`.toUpperCase();
  }
}
