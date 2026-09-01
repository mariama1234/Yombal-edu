import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Utilisateur } from '../models/models';

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

  constructor(private http: HttpClient, private router: Router) {}

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
