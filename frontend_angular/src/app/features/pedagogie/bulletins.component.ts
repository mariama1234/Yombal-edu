import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import {
  Bulletin,
  Classe,
  Paginated,
  PeriodeScolaire,
  ResultatBulletins,
} from '../../core/models/models';
import { PedagogieService } from './pedagogie.service';

/**
 * Génération des bulletins d'une classe (2.2 de la priorisation).
 *
 * L'aperçu montre le tableau des moyennes — une colonne par matière, le rang
 * calculé — avant que quoi que ce soit ne soit écrit. C'est aussi le document
 * de travail du conseil de classe, d'où l'export.
 *
 * Régénérer met à jour les bulletins existants : une note corrigée après le
 * conseil doit pouvoir se répercuter.
 */
@Component({
  selector: 'app-bulletins',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './bulletins.component.html',
})
export class BulletinsComponent implements OnInit {
  private fb = inject(FormBuilder);
  private http = inject(HttpClient);
  private pedagogie = inject(PedagogieService);

  classes = signal<Classe[]>([]);
  periodes = signal<PeriodeScolaire[]>([]);

  apercu = signal<ResultatBulletins | null>(null);
  resultat = signal<ResultatBulletins | null>(null);
  bulletins = signal<Bulletin[]>([]);
  erreur = signal<string | null>(null);
  occupe = signal(false);

  form = this.fb.group({
    id_classe: [null as number | null, Validators.required],
    id_periode: [null as number | null, Validators.required],
  });

  /** Les colonnes du tableau : les matières évaluées de la classe. */
  matieres = computed(() => {
    const lignes = (this.apercu() ?? this.resultat())?.lignes ?? [];
    return lignes.length ? lignes[0].matieres.map((m) => m.matiere) : [];
  });

  affichage = computed(() => this.apercu() ?? this.resultat());

  ngOnInit(): void {
    const api = environment.apiUrl;
    this.http.get<Paginated<Classe>>(`${api}/classes/`)
      .subscribe((r) => this.classes.set(r.results ?? []));
    this.http.get<Paginated<PeriodeScolaire>>(`${api}/periodes/`)
      .subscribe((r) => this.periodes.set(r.results ?? []));

    this.form.valueChanges.subscribe(() => {
      this.apercu.set(null);
      this.resultat.set(null);
    });
  }

  private appeler(valider: boolean, sur: (r: ResultatBulletins) => void): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    const v = this.form.getRawValue();
    this.pedagogie
      .genererBulletins({ id_classe: v.id_classe, id_periode: v.id_periode, valider })
      .subscribe({
        next: (r) => {
          sur(r);
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(this.message(e));
          this.occupe.set(false);
        },
      });
  }

  previsualiser(): void {
    this.appeler(false, (r) => {
      this.apercu.set(r);
      this.resultat.set(null);
    });
  }

  valider(): void {
    this.appeler(true, (r) => {
      this.resultat.set(r);
      this.apercu.set(null);
      this.chargerBulletins();
    });
  }

  /** Les bulletins écrits, pour proposer le PDF de chacun. */
  private chargerBulletins(): void {
    const v = this.form.getRawValue();
    this.http
      .get<Paginated<Bulletin>>(`${environment.apiUrl}/bulletins/`, {
        params: { id_classe: String(v.id_classe), id_periode: String(v.id_periode) },
      })
      .subscribe((r) => this.bulletins.set(r.results ?? []));
  }

  idBulletin(idEleve: number): number | null {
    return this.bulletins().find((b) => b.id_eleve === idEleve)?.id ?? null;
  }

  telechargerBulletin(idEleve: number): void {
    const id = this.idBulletin(idEleve);
    if (!id) return;
    this.pedagogie.telecharger(`/bulletins/${id}/pdf/`)
      .subscribe({ error: (e) => this.erreur.set(this.message(e)) });
  }

  exporterMoyennes(format: 'xlsx' | 'pdf'): void {
    const v = this.form.getRawValue();
    if (!v.id_classe || !v.id_periode) return;
    this.pedagogie
      .telecharger('/bulletins/moyennes/export/', {
        id_classe: v.id_classe, id_periode: v.id_periode, format_export: format,
      })
      .subscribe({ error: (e) => this.erreur.set(this.message(e)) });
  }

  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return 'La génération a échoué.';
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
