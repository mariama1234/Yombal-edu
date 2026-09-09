import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  ApercuImport,
  ContexteReinscription,
  Eleve,
  EleveInscriptionPayload,
  FileReinscription,
  Paginated,
  ResultatReinscriptionEleve,
} from '../../core/models/models';

@Injectable({ providedIn: 'root' })
export class EleveService {
  private base = `${environment.apiUrl}/eleves`;

  constructor(private http: HttpClient) {}

  list(params: Record<string, string> = {}): Observable<Paginated<Eleve>> {
    const query = new URLSearchParams(params).toString();
    return this.http.get<Paginated<Eleve>>(`${this.base}/${query ? '?' + query : ''}`);
  }

  get(id: number): Observable<Eleve> {
    return this.http.get<Eleve>(`${this.base}/${id}/`);
  }

  /**
   * Élèves d'une année précédente sans inscription sur l'année visée.
   *
   * Ils ne sont dans aucune liste : ni chez les inscrits — ils ne le sont pas
   * encore — ni chez les partants. C'est la file de la rentrée.
   */
  aReinscrire(params: Record<string, string> = {}): Observable<FileReinscription> {
    const query = new URLSearchParams(params).toString();
    return this.http.get<FileReinscription>(
      `${this.base}/a-reinscrire/${query ? '?' + query : ''}`);
  }

  /** Provenance, année visée et classes possibles : le pré-remplissage de l'écran. */
  contexteReinscription(
    id: number,
    params: Record<string, string> = {},
  ): Observable<ContexteReinscription> {
    const query = new URLSearchParams(params).toString();
    return this.http.get<ContexteReinscription>(
      `${this.base}/${id}/reinscription/${query ? '?' + query : ''}`);
  }

  /** Réinscrit un élève seul dans une classe de l'année suivante. */
  reinscrire(id: number, corps: Record<string, unknown>): Observable<ResultatReinscriptionEleve> {
    return this.http.post<ResultatReinscriptionEleve>(`${this.base}/${id}/reinscrire/`, corps);
  }

  /** Endpoint composite : crée l'élève + son inscription + (optionnel) son parent en un appel. */
  inscrire(payload: EleveInscriptionPayload): Observable<Eleve> {
    return this.http.post<Eleve>(`${this.base}/inscrire/`, payload);
  }

  /**
   * Import d'une liste (.xlsx, .csv ou .docx).
   *
   * Le même fichier est envoyé deux fois : une fois pour l'aperçu, une fois
   * pour la validation. C'est voulu — le serveur ré-analyse le fichier plutôt
   * que de faire confiance à un plan renvoyé par le navigateur.
   */
  importer(fichier: File, classe = '', valider = false): Observable<ApercuImport> {
    const donnees = new FormData();
    donnees.append('fichier', fichier);
    if (classe) {
      donnees.append('classe', classe);
    }
    if (valider) {
      donnees.append('valider', 'true');
    }
    return this.http.post<ApercuImport>(`${this.base}/importer/`, donnees);
  }
}
