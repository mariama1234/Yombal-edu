import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  GrilleSaisie,
  LigneMoyennes,
  Palmares,
  ResultatBulletins,
  ResultatSaisieNotes,
  StatistiquesClasse,
} from '../../core/models/models';

type Filtres = Record<string, string | number | boolean | null | undefined>;

/**
 * Point d'entrée unique du module pédagogique.
 *
 * Tous les calculs (moyennes, rangs, mentions) restent côté serveur : les
 * refaire en TypeScript ferait exister deux définitions de la moyenne
 * générale, qui divergeraient au premier changement de règle.
 */
@Injectable({ providedIn: 'root' })
export class PedagogieService {
  private http = inject(HttpClient);
  private base = environment.apiUrl;

  private params(filtres: Filtres = {}): HttpParams {
    let params = new HttpParams();
    for (const [cle, valeur] of Object.entries(filtres)) {
      if (valeur !== null && valeur !== undefined && valeur !== '') {
        params = params.set(cle, String(valeur));
      }
    }
    return params;
  }

  // ---- Saisie des notes ------------------------------------------------
  grille(idEvaluation: number | string): Observable<GrilleSaisie> {
    return this.http.get<GrilleSaisie>(`${this.base}/evaluations/${idEvaluation}/grille/`);
  }

  /** Toute la grille part en une requête, pas une par élève. */
  enregistrerNotes(
    idEvaluation: number,
    notes: { id_eleve: number; valeur: number | null; appreciation?: string | null }[],
  ): Observable<ResultatSaisieNotes> {
    return this.http.post<ResultatSaisieNotes>(
      `${this.base}/evaluations/${idEvaluation}/notes/`, { notes });
  }

  // ---- Moyennes et bulletins -------------------------------------------
  moyennes(idClasse: number, idPeriode: number): Observable<{
    classe: string; periode: string; lignes: LigneMoyennes[];
  }> {
    return this.http.get<{ classe: string; periode: string; lignes: LigneMoyennes[] }>(
      `${this.base}/bulletins/moyennes/`,
      { params: this.params({ id_classe: idClasse, id_periode: idPeriode }) });
  }

  /** `valider: false` renvoie l'aperçu sans écrire de bulletin. */
  genererBulletins(corps: Record<string, unknown>): Observable<ResultatBulletins> {
    return this.http.post<ResultatBulletins>(`${this.base}/bulletins/generer/`, corps);
  }

  // ---- Palmarès et statistiques ----------------------------------------
  palmares(idClasse: number, idPeriode: number, limite?: number): Observable<Palmares> {
    return this.http.get<Palmares>(`${this.base}/bulletins/palmares/`, {
      params: this.params({ id_classe: idClasse, id_periode: idPeriode, limite }),
    });
  }

  statistiques(idClasse: number, idPeriode: number): Observable<StatistiquesClasse> {
    return this.http.get<StatistiquesClasse>(`${this.base}/bulletins/statistiques/`, {
      params: this.params({ id_classe: idClasse, id_periode: idPeriode }),
    });
  }

  // ---- Téléchargements -------------------------------------------------
  telecharger(chemin: string, filtres: Filtres = {}): Observable<unknown> {
    return this.http
      .get(`${this.base}${chemin}`, {
        params: this.params(filtres),
        responseType: 'blob',
        observe: 'response',
      })
      .pipe(
        tap((reponse) => {
          const entete = reponse.headers.get('Content-Disposition') ?? '';
          const trouve = /filename="?([^"]+)"?/.exec(entete);
          const url = URL.createObjectURL(reponse.body!);
          const lien = document.createElement('a');
          lien.href = url;
          lien.download = trouve ? trouve[1] : 'export';
          lien.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        }),
      );
  }
}
