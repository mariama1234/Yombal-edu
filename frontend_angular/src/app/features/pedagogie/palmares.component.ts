import { Component, OnInit, effect, inject, signal, untracked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { forkJoin } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  Classe,
  Paginated,
  Palmares,
  PeriodeScolaire,
  StatistiquesClasse,
} from '../../core/models/models';
import { PedagogieService } from './pedagogie.service';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';

/**
 * Palmarès et statistiques d'une classe (2.3 de la priorisation).
 *
 * Classement d'un côté, indicateurs de l'autre : taux de réussite, extrêmes,
 * répartition par mention et moyenne de chaque matière. C'est ce qu'on
 * projette au conseil de classe.
 */
@Component({
  selector: 'app-palmares',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink, SiAccesDirective],
  templateUrl: './palmares.component.html',
})
export class PalmaresComponent implements OnInit {
  private fb = inject(FormBuilder);
  private http = inject(HttpClient);
  private pedagogie = inject(PedagogieService);
  private annees = inject(AnneeScolaireService);

  classes = signal<Classe[]>([]);
  periodes = signal<PeriodeScolaire[]>([]);

  palmares = signal<Palmares | null>(null);
  statistiques = signal<StatistiquesClasse | null>(null);
  erreur = signal<string | null>(null);
  occupe = signal(false);

  form = this.fb.group({
    id_classe: [null as number | null, Validators.required],
    id_periode: [null as number | null, Validators.required],
    limite: [null as number | null],
  });

  constructor() {
    // Classes et périodes de l'année du bandeau haut. Sans `page_size`,
    // l'API n'en renvoyait que vingt, toutes années confondues : une classe
    // nouvellement créée n'apparaissait pas dans la liste.
    effect(() => {
      const annee = this.annees.idAnnee();
      untracked(() => this.chargerListes(annee));
    });
  }

  private chargerListes(annee: number | null): void {
    const api = environment.apiUrl;
    const params: Record<string, string> = { page_size: '200' };
    if (annee !== null) params['id_annee_scolaire'] = String(annee);
    this.form.patchValue({ id_classe: null, id_periode: null });
    this.http.get<Paginated<Classe>>(`${api}/classes/`, { params })
      .subscribe((r) => this.classes.set(r.results ?? []));
    this.http.get<Paginated<PeriodeScolaire>>(`${api}/periodes/`, { params })
      .subscribe((r) => this.periodes.set(r.results ?? []));
  }

  ngOnInit(): void {

    this.form.valueChanges.subscribe(() => {
      this.palmares.set(null);
      this.statistiques.set(null);
    });
  }

  /** Les mentions, dans l'ordre décroissant du barème, pour l'histogramme. */
  mentions(repartition: Record<string, number>): { libelle: string; nombre: number }[] {
    const ordre = ['Excellent', 'Très bien', 'Bien', 'Assez bien', 'Insuffisant',
                   'Très insuffisant'];
    return ordre.map((libelle) => ({ libelle, nombre: repartition[libelle] ?? 0 }));
  }

  part(nombre: number, total: number): number {
    return total ? Math.round((nombre * 100) / total) : 0;
  }

  charger(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const v = this.form.getRawValue();
    this.occupe.set(true);
    this.erreur.set(null);

    // Les deux vues décrivent la même période : les charger ensemble évite
    // d'afficher un palmarès et des statistiques désynchronisés.
    forkJoin({
      palmares: this.pedagogie.palmares(v.id_classe!, v.id_periode!, v.limite ?? undefined),
      statistiques: this.pedagogie.statistiques(v.id_classe!, v.id_periode!),
    }).subscribe({
      next: ({ palmares, statistiques }) => {
        this.palmares.set(palmares);
        this.statistiques.set(statistiques);
        this.occupe.set(false);
      },
      error: (e) => {
        this.erreur.set(this.message(e));
        this.occupe.set(false);
      },
    });
  }

  exporter(format: 'xlsx' | 'pdf'): void {
    const v = this.form.getRawValue();
    if (!v.id_classe || !v.id_periode) return;
    this.pedagogie
      .telecharger('/bulletins/palmares/export/', {
        id_classe: v.id_classe, id_periode: v.id_periode, format_export: format,
      })
      .subscribe({ error: (e) => this.erreur.set(this.message(e)) });
  }

  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return 'Le calcul a échoué.';
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
