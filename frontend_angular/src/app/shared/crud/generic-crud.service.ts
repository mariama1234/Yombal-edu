import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Paginated } from '../../core/models/models';

/**
 * Service HTTP générique : un seul service pour n'importe quelle ressource
 * REST standard exposée par le backend (list/create/update/delete).
 * Utilisé par tous les modules qui passent par le système CRUD générique
 * (Parents, Matières, Présences, Enseignants, Emploi du temps, Dépenses...).
 * Les modules avec une logique métier propre (Élèves, Paiements) ont leur
 * propre service dédié plutôt que celui-ci.
 */
@Injectable({ providedIn: 'root' })
export class GenericCrudService<T extends { id: number }> {
  constructor(private http: HttpClient) {}

  list(endpoint: string, params: Record<string, string> = {}): Observable<Paginated<T>> {
    const query = new URLSearchParams(params).toString();
    const url = `${environment.apiUrl}/${endpoint}/${query ? '?' + query : ''}`;
    return this.http.get<Paginated<T>>(url);
  }

  get(endpoint: string, id: number): Observable<T> {
    return this.http.get<T>(`${environment.apiUrl}/${endpoint}/${id}/`);
  }

  create(endpoint: string, payload: Partial<T>): Observable<T> {
    return this.http.post<T>(`${environment.apiUrl}/${endpoint}/`, payload);
  }

  update(endpoint: string, id: number, payload: Partial<T>): Observable<T> {
    return this.http.patch<T>(`${environment.apiUrl}/${endpoint}/${id}/`, payload);
  }

  remove(endpoint: string, id: number): Observable<void> {
    return this.http.delete<void>(`${environment.apiUrl}/${endpoint}/${id}/`);
  }

  /** Pièce jointe d'une ligne, ex: POST /depenses/12/justificatif/ (champ `fichier`). */
  deposerFichier(endpoint: string, id: number, chemin: string, fichier: File): Observable<unknown> {
    const corps = new FormData();
    corps.append('fichier', fichier);
    return this.http.post(`${environment.apiUrl}/${endpoint}/${id}/${chemin}/`, corps);
  }

  lireFichier(endpoint: string, id: number, chemin: string): Observable<Blob> {
    return this.http.get(`${environment.apiUrl}/${endpoint}/${id}/${chemin}/`, { responseType: 'blob' });
  }

  retirerFichier(endpoint: string, id: number, chemin: string): Observable<unknown> {
    return this.http.delete(`${environment.apiUrl}/${endpoint}/${id}/${chemin}/`);
  }

  /** Action métier sur une ligne, ex: POST /communications/12/envoyer/ */
  action(endpoint: string, id: number, chemin: string, payload: unknown = {}): Observable<T> {
    return this.http.post<T>(`${environment.apiUrl}/${endpoint}/${id}/${chemin}/`, payload);
  }
}
