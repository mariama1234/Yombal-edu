import { Injectable, computed, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Module, Utilisateur } from '../models/models';

interface TokenResponse {
  access: string;
  refresh: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly ACCESS_KEY = 'yombaledu_access';
  private readonly REFRESH_KEY = 'yombaledu_refresh';

  /** Utilisateur connecté, disponible partout dans l'app via ce signal. */
  readonly currentUser = signal<Utilisateur | null>(null);

  /**
   * Profils dont les comptes n'ouvrent que la fiche de leurs enfants (parent)
   * ou la leur (élève) : les vues sur toute l'école leur sont fermées.
   */
  readonly estFamille = computed(() => {
    const profil = this.currentUser()?.profil;
    return profil === 'parent' || profil === 'eleve';
  });

  /** Administrateur, directeur ou responsable administratif. */
  readonly estDirection = computed(() => this.currentUser()?.profil === 'direction');

  constructor(private http: HttpClient, private router: Router) {}

  /**
   * Le compte peut-il ouvrir ce module ? La matrice vient du serveur, qui
   * refuse de toute façon (403) : ceci ne sert qu'à ne pas proposer à l'écran
   * ce qui serait refusé.
   */
  peutLire(module: Module | null | undefined): boolean {
    if (!module) return true;
    return !!this.currentUser()?.acces?.[module];
  }

  /** Le compte peut-il créer, modifier ou supprimer dans ce module ? */
  peutEcrire(module: Module | null | undefined): boolean {
    if (!module) return true;
    return this.currentUser()?.acces?.[module] === 'ecriture';
  }

  get accessToken(): string | null {
    return localStorage.getItem(this.ACCESS_KEY);
  }

  get isAuthenticated(): boolean {
    return !!this.accessToken;
  }

  login(email: string, password: string): Observable<TokenResponse> {
    return this.http
      .post<TokenResponse>(`${environment.apiUrl}/auth/token/`, { email, password })
      .pipe(
        tap((res) => {
          localStorage.setItem(this.ACCESS_KEY, res.access);
          localStorage.setItem(this.REFRESH_KEY, res.refresh);
        })
      );
  }

  /** À appeler une fois connecté (ex: dans le guard ou au démarrage de l'app) pour charger le profil. */
  chargerProfil(): Observable<Utilisateur> {
    return this.http.get<Utilisateur>(`${environment.apiUrl}/auth/moi/`).pipe(
      tap((user) => this.currentUser.set(user))
    );
  }

  refreshToken(): Observable<TokenResponse> {
    const refresh = localStorage.getItem(this.REFRESH_KEY);
    return this.http
      .post<TokenResponse>(`${environment.apiUrl}/auth/token/refresh/`, { refresh })
      .pipe(tap((res) => localStorage.setItem(this.ACCESS_KEY, res.access)));
  }

  logout(): void {
    localStorage.removeItem(this.ACCESS_KEY);
    localStorage.removeItem(this.REFRESH_KEY);
    this.currentUser.set(null);
    this.router.navigate(['/connexion']);
  }
}
