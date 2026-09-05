import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { DossierEleve, ReleveNotes, ResultatReinscription } from '../../core/models/models';

type Filtres = Record<string, string | number | boolean | null | undefined>;

/**
 * Parcours de l'élève : dossier numérique et mouvements.
 *
 * Les trois mouvements (changement de classe, radiation, transfert) ont chacun
 * leur endpoint plutôt qu'un PATCH sur l'inscription : eux seuls écrivent la
 * trace au bon type d'événement et recalent l'effectif de la classe quittée.
 */
@Injectable({ providedIn: 'root' })
export class ScolariteService {
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

  // ---- Dossier ---------------------------------------------------------
  dossier(idEleve: number | string): Observable<DossierEleve> {
    return this.http.get<DossierEleve>(`${this.base}/eleves/${idEleve}/dossier/`);
  }

  releve(idEleve: number | string, idPeriode: number): Observable<ReleveNotes> {
    return this.http.get<ReleveNotes>(`${this.base}/eleves/${idEleve}/releve/`, {
      params: this.params({ id_periode: idPeriode }),
    });
  }

  // ---- Mouvements ------------------------------------------------------
  // Chacun renvoie le dossier remis à jour : l'écran se rafraîchit sans
  // second appel.

  changerDeClasse(idEleve: number, corps: Record<string, unknown>): Observable<DossierEleve> {
    return this.http.post<DossierEleve>(
      `${this.base}/eleves/${idEleve}/changer-classe/`, corps);
  }

  radier(idEleve: number, corps: Record<string, unknown>): Observable<DossierEleve> {
    return this.http.post<DossierEleve>(`${this.base}/eleves/${idEleve}/radier/`, corps);
  }

  transferer(idEleve: number, corps: Record<string, unknown>): Observable<DossierEleve> {
    return this.http.post<DossierEleve>(`${this.base}/eleves/${idEleve}/transferer/`, corps);
  }

  // ---- Réinscription ---------------------------------------------------
  /** `valider: false` renvoie l'aperçu sans rien écrire. */
  reinscrire(corps: Record<string, unknown>): Observable<ResultatReinscription> {
    return this.http.post<ResultatReinscription>(
      `${this.base}/inscriptions/reinscrire/`, corps);
  }

  // ---- Téléchargements -------------------------------------------------
  /**
   * Les exports sont authentifiés par un jeton porté dans l'en-tête
   * Authorization, qu'une balise `<a href>` n'enverrait pas.
   */
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
