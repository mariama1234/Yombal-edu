import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  Bilan,
  Budget,
  Depense,
  FraisEleve,
  JournalCaisse,
  Paginated,
  Paiement,
  Relance,
  ResultatGenerationFrais,
  ResultatRelances,
  Soldes,
} from '../../core/models/models';

type Filtres = Record<string, string | number | boolean | null | undefined>;

/**
 * Point d'entrée unique du module financier.
 *
 * Les téléchargements passent par `telecharger()` plutôt que par un simple
 * lien : les exports sont authentifiés par un jeton porté dans l'en-tête
 * Authorization, qu'une balise `<a href>` n'enverrait pas.
 */
@Injectable({ providedIn: 'root' })
export class FinanceService {
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

  // ---- Frais et impayés ------------------------------------------------
  frais(filtres: Filtres = {}): Observable<Paginated<FraisEleve>> {
    return this.http.get<Paginated<FraisEleve>>(`${this.base}/frais-eleves/`, {
      params: this.params(filtres),
    });
  }

  impayes(filtres: Filtres = {}): Observable<Paginated<FraisEleve>> {
    return this.http.get<Paginated<FraisEleve>>(`${this.base}/frais-eleves/impayes/`, {
      params: this.params(filtres),
    });
  }

  unFrais(id: number | string): Observable<FraisEleve> {
    return this.http.get<FraisEleve>(`${this.base}/frais-eleves/${id}/`);
  }

  /** `valider: false` renvoie l'aperçu sans rien écrire. */
  genererFrais(corps: Record<string, unknown>): Observable<ResultatGenerationFrais> {
    return this.http.post<ResultatGenerationFrais>(
      `${this.base}/frais-eleves/generer/`, corps);
  }

  // ---- Paiements et reçus ---------------------------------------------
  encaisser(corps: Record<string, unknown>): Observable<Paiement> {
    return this.http.post<Paiement>(`${this.base}/paiements/`, corps);
  }

  paiements(filtres: Filtres = {}): Observable<Paginated<Paiement>> {
    return this.http.get<Paginated<Paiement>>(`${this.base}/paiements/`, {
      params: this.params(filtres),
    });
  }

  // ---- Relances --------------------------------------------------------
  relances(): Observable<Paginated<Relance>> {
    return this.http.get<Paginated<Relance>>(`${this.base}/relances/`);
  }

  genererRelances(corps: Record<string, unknown>): Observable<ResultatRelances> {
    return this.http.post<ResultatRelances>(`${this.base}/relances/generer/`, corps);
  }

  // ---- Journal ---------------------------------------------------------
  journal(filtres: Filtres = {}): Observable<Paginated<JournalCaisse>> {
    return this.http.get<Paginated<JournalCaisse>>(`${this.base}/journal-caisse/`, {
      params: this.params(filtres),
    });
  }

  soldes(): Observable<Soldes> {
    return this.http.get<Soldes>(`${this.base}/journal-caisse/soldes/`);
  }

  // ---- Dépenses et budgets --------------------------------------------
  depenses(filtres: Filtres = {}): Observable<Paginated<Depense>> {
    return this.http.get<Paginated<Depense>>(`${this.base}/depenses/`, {
      params: this.params(filtres),
    });
  }

  budgets(filtres: Filtres = {}): Observable<Paginated<Budget>> {
    return this.http.get<Paginated<Budget>>(`${this.base}/budgets/`, {
      params: this.params(filtres),
    });
  }

  // ---- Bilan -----------------------------------------------------------
  bilan(filtres: Filtres = {}): Observable<Bilan> {
    return this.http.get<Bilan>(`${this.base}/finances/bilan/`, {
      params: this.params(filtres),
    });
  }

  // ---- Téléchargements -------------------------------------------------
  /**
   * Récupère un export en binaire et le remet au navigateur.
   *
   * Le nom du fichier vient de l'en-tête `Content-Disposition` posé par le
   * serveur : c'est lui qui sait de quelle période parle l'export.
   */
  telecharger(chemin: string, filtres: Filtres = {}): Observable<Blob> {
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
          this.enregistrer(reponse.body!, trouve ? trouve[1] : 'export');
        }),
      ) as unknown as Observable<Blob>;
  }

  private enregistrer(contenu: Blob, nom: string): void {
    const url = URL.createObjectURL(contenu);
    const lien = document.createElement('a');
    lien.href = url;
    lien.download = nom;
    lien.click();
    // Libéré après le clic, sinon le navigateur annule le téléchargement.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
