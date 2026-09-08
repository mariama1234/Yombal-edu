import { Component, ElementRef, HostListener, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject, debounceTime, distinctUntilChanged, switchMap } from 'rxjs';
import { forkJoin, of } from 'rxjs';
import { environment } from '../../environments/environment';
import { Classe, Eleve, Paginated } from '../core/models/models';

/** Nombre de caractères avant de commencer à interroger le serveur. */
const MINIMUM = 2;

/**
 * Recherche globale du bandeau haut.
 *
 * Le champ existait depuis la maquette mais n'était relié à rien : taper un
 * nom ne faisait rien du tout. Il interroge maintenant les élèves et les
 * classes, et mène directement au dossier.
 *
 * La recherche ignore volontairement le filtre d'année : on cherche un élève
 * parce qu'on ne sait pas où il est, et le trouver ne doit pas dépendre de
 * l'année affichée à l'écran. Chaque résultat porte son statut pour situer
 * ceux qui ont quitté l'établissement.
 */
@Component({
  selector: 'app-recherche-globale',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './recherche-globale.component.html',
})
export class RechercheGlobaleComponent {
  private http = inject(HttpClient);
  private router = inject(Router);
  private hote = inject(ElementRef<HTMLElement>);

  private saisies = new Subject<string>();

  terme = signal('');
  eleves = signal<Eleve[]>([]);
  classes = signal<Classe[]>([]);
  ouvert = signal(false);
  cherche = signal(false);

  constructor() {
    this.saisies
      .pipe(
        // 250 ms : assez pour ne pas lancer une requête par frappe, assez peu
        // pour que les résultats suivent la saisie.
        debounceTime(250),
        distinctUntilChanged(),
        switchMap((terme) => {
          if (terme.trim().length < MINIMUM) {
            this.cherche.set(false);
            return of(null);
          }
          this.cherche.set(true);
          const params = { params: { search: terme.trim() } };
          return forkJoin({
            eleves: this.http.get<Paginated<Eleve>>(
              `${environment.apiUrl}/eleves/`, params),
            classes: this.http.get<Paginated<Classe>>(
              `${environment.apiUrl}/classes/`, params),
          });
        }),
      )
      .subscribe({
        next: (reponse) => {
          this.cherche.set(false);
          if (reponse === null) {
            this.eleves.set([]);
            this.classes.set([]);
            return;
          }
          this.eleves.set((reponse.eleves.results ?? []).slice(0, 6));
          this.classes.set((reponse.classes.results ?? []).slice(0, 4));
        },
        error: () => {
          this.cherche.set(false);
          this.eleves.set([]);
          this.classes.set([]);
        },
      });
  }

  saisir(valeur: string): void {
    this.terme.set(valeur);
    this.ouvert.set(valeur.trim().length >= MINIMUM);
    this.saisies.next(valeur);
  }

  aDesResultats(): boolean {
    return this.eleves().length > 0 || this.classes().length > 0;
  }

  termeAssezLong(): boolean {
    return this.terme().trim().length >= MINIMUM;
  }

  ouvrirEleve(eleve: Eleve): void {
    this.fermer();
    this.router.navigate(['/eleves', eleve.id, 'dossier']);
  }

  ouvrirClasses(): void {
    this.fermer();
    this.router.navigate(['/classes']);
  }

  /** Entrée sans sélection : on bascule sur la liste complète des élèves. */
  valider(): void {
    if (!this.termeAssezLong()) return;
    if (this.eleves().length === 1) {
      this.ouvrirEleve(this.eleves()[0]);
      return;
    }
    this.fermer();
    this.router.navigate(['/eleves'], { queryParams: { recherche: this.terme().trim() } });
  }

  fermer(): void {
    this.ouvert.set(false);
    this.terme.set('');
    this.eleves.set([]);
    this.classes.set([]);
  }

  @HostListener('document:click', ['$event'])
  surClicExterieur(evenement: MouseEvent): void {
    if (!this.hote.nativeElement.contains(evenement.target as Node)) {
      this.ouvert.set(false);
    }
  }

  @HostListener('document:keydown.escape')
  surEchap(): void {
    this.ouvert.set(false);
  }

  initiales(e: Eleve): string {
    return `${e.prenom[0] ?? ''}${e.nom[0] ?? ''}`.toUpperCase();
  }
}
