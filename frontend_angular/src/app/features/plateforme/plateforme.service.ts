import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AdminEcole, EcolePlateforme, Paginated } from '../../core/models/models';

/** Les écoles clientes, gérées par le super administrateur. */
@Injectable({ providedIn: 'root' })
export class PlateformeService {
  private http = inject(HttpClient);
  private base = `${environment.apiUrl}/plateforme/ecoles`;

  lister(): Observable<Paginated<EcolePlateforme>> {
    return this.http.get<Paginated<EcolePlateforme>>(`${this.base}/`, { params: { page_size: '500' } });
  }

  lire(id: number): Observable<EcolePlateforme> {
    return this.http.get<EcolePlateforme>(`${this.base}/${id}/`);
  }

  /** Crée l'école et son premier administrateur, d'un seul envoi. */
  creer(corps: Record<string, unknown>): Observable<EcolePlateforme> {
    return this.http.post<EcolePlateforme>(`${this.base}/`, corps);
  }

  modifier(id: number, corps: Record<string, unknown>): Observable<EcolePlateforme> {
    return this.http.patch<EcolePlateforme>(`${this.base}/${id}/`, corps);
  }

  ajouterAdmin(id: number, corps: Record<string, unknown>): Observable<AdminEcole> {
    return this.http.post<AdminEcole>(`${this.base}/${id}/admins/`, corps);
  }
}
