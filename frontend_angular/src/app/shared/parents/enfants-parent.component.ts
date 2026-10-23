import { Component, Input, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { RouterLink } from '@angular/router';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/services/auth.service';
import { ToastService } from '../../core/services/toast.service';
import { Eleve, EnfantDuParent, Paginated, ParentTuteur } from '../../core/models/models';

type Lien = EnfantDuParent['lien'];

/**
 * Les enfants d'un parent, sur sa fiche : ceux qui lui sont rattachés, et de
 * quoi en rattacher un autre — un frère ou une sœur inscrit sous un autre
 * parent, ou avant que la fiche n'existe.
 *
 * Un rattachement est une ligne de `/eleves-parents/` : le défaire ne
 * supprime ni l'élève ni le parent.
 */
@Component({
  selector: 'app-enfants-parent',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <div class="form-section" style="max-width:760px;">
      <div class="panel-head" style="margin-bottom:10px;">
        <div class="panel-title">Enfants rattachés ({{ enfants().length }})</div>
      </div>

      @if (enfants().length === 0) {
        <p class="hint" style="margin:0 0 12px;">Aucun enfant n’est encore rattaché à ce parent.</p>
      } @else {
        <div class="choix-liste" style="margin:0 0 14px;">
          @for (e of enfants(); track e.id_lien) {
            <div class="choix-ligne" style="cursor:default;">
              <span>
                @if (voitEleves()) {
                  <a [routerLink]="['/eleves', e.id_eleve, 'dossier']"><strong>{{ e.nom_complet }}</strong></a>
                } @else {
                  <strong>{{ e.nom_complet }}</strong>
                }
                <span class="hint"> · {{ e.matricule }} · {{ e.lien_libelle }}</span>
                @if (e.contact_principal) { <span class="pill a_jour" style="margin-left:6px;">Contact principal</span> }
              </span>
              @if (peutModifier()) {
                <button type="button" class="panel-link" style="color:var(--red);"
                        [disabled]="occupe()" (click)="detacher(e)">Détacher</button>
              }
            </div>
          }
        </div>
      }

      @if (peutModifier()) {
        <div class="field">
          <label for="recherche-enfant">Rattacher un autre enfant</label>
          @if (eleveChoisi(); as eleve) {
            <div class="form-grid" style="align-items:end;">
              <div class="field">
                <span><strong>{{ eleve.prenom }} {{ eleve.nom }}</strong> <span class="hint">({{ eleve.matricule }})</span></span>
              </div>
              <div class="field">
                <label for="lien-enfant">Lien de parenté</label>
                <select class="input" id="lien-enfant" [value]="lien()" (change)="lien.set($any($event.target).value)">
                  <option value="mere">Mère</option>
                  <option value="pere">Père</option>
                  <option value="tuteur">Tuteur</option>
                  <option value="autre">Autre</option>
                </select>
              </div>
            </div>
            <div class="form-actions">
              <button type="button" class="btn-ghost" (click)="eleveChoisi.set(null)">Annuler</button>
              <button type="button" class="btn-primary" [disabled]="occupe()" (click)="rattacher()">Rattacher</button>
            </div>
          } @else {
            <input class="input" id="recherche-enfant" type="search" autocomplete="off"
                   placeholder="Nom, prénom ou matricule de l’élève…"
                   [value]="recherche()" (input)="chercher($any($event.target).value)">
            @if (trouves().length) {
              <div class="choix-liste" role="listbox" aria-label="Élèves trouvés">
                @for (e of trouves(); track e.id) {
                  <button type="button" class="choix-ligne" role="option" (click)="choisir(e)">
                    <span><strong>{{ e.prenom }} {{ e.nom }}</strong></span>
                    <span class="hint">{{ e.matricule }}</span>
                  </button>
                }
              </div>
            } @else if (recherche().trim().length >= 2) {
              <span class="hint">Aucun élève trouvé.</span>
            }
          }
        </div>
      }
      @if (erreur()) { <div class="form-error" style="margin-top:10px;">{{ erreur() }}</div> }
    </div>
  `,
})
export class EnfantsParentComponent implements OnInit {
  @Input({ required: true }) idParent!: number;

  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private toast = inject(ToastService);
  private base = environment.apiUrl;

  enfants = signal<EnfantDuParent[]>([]);
  recherche = signal('');
  trouves = signal<Eleve[]>([]);
  eleveChoisi = signal<Eleve | null>(null);
  lien = signal<Lien>('mere');
  occupe = signal(false);
  erreur = signal<string | null>(null);
  private minuterie?: ReturnType<typeof setTimeout>;

  peutModifier = computed(() => this.auth.peutEcrire('parents'));
  voitEleves = computed(() => this.auth.peutLire('eleves'));

  ngOnInit(): void {
    this.recharger();
  }

  private recharger(): void {
    this.http.get<ParentTuteur>(`${this.base}/parents/${this.idParent}/`).subscribe({
      next: (p) => this.enfants.set(p.enfants ?? []),
      error: () => this.erreur.set('Les enfants de ce parent n’ont pas pu être chargés.'),
    });
  }

  chercher(texte: string): void {
    this.recherche.set(texte);
    clearTimeout(this.minuterie);
    const terme = texte.trim();
    if (terme.length < 2) {
      this.trouves.set([]);
      return;
    }
    this.minuterie = setTimeout(() => {
      this.http
        .get<Paginated<Eleve>>(`${this.base}/eleves/`, { params: { search: terme, page_size: '8' } })
        .subscribe({
          // Un enfant déjà rattaché n'est pas reproposé.
          next: (r) => this.trouves.set((r.results ?? [])
            .filter((e) => !this.enfants().some((x) => x.id_eleve === e.id))),
          error: () => this.trouves.set([]),
        });
    }, 250);
  }

  choisir(eleve: Eleve): void {
    this.eleveChoisi.set(eleve);
    this.trouves.set([]);
    this.recherche.set('');
  }

  rattacher(): void {
    const eleve = this.eleveChoisi();
    if (!eleve) return;
    this.occupe.set(true);
    this.erreur.set(null);
    this.http.post(`${this.base}/eleves-parents/`, {
      id_eleve: eleve.id, id_parent: this.idParent, lien_parente: this.lien(), contact_principal: false,
    }).subscribe({
      next: () => {
        this.occupe.set(false);
        this.eleveChoisi.set(null);
        this.toast.succes('Enfant rattaché', `${eleve.prenom} ${eleve.nom}`);
        this.recharger();
      },
      error: (e) => {
        this.occupe.set(false);
        this.erreur.set(this.message(e, 'Le rattachement a échoué.'));
      },
    });
  }

  detacher(enfant: EnfantDuParent): void {
    if (!confirm(`Détacher ${enfant.nom_complet} de ce parent ? L’élève et le parent sont conservés.`)) return;
    this.occupe.set(true);
    this.erreur.set(null);
    this.http.delete(`${this.base}/eleves-parents/${enfant.id_lien}/`).subscribe({
      next: () => {
        this.occupe.set(false);
        this.toast.succes('Enfant détaché', enfant.nom_complet);
        this.recharger();
      },
      error: (e) => {
        this.occupe.set(false);
        this.erreur.set(this.message(e, 'Le détachement a échoué.'));
      },
    });
  }

  private message(erreur: any, defaut: string): string {
    const corps = erreur?.error;
    if (typeof corps?.detail === 'string') return corps.detail;
    if (Array.isArray(corps?.non_field_errors)) return corps.non_field_errors.join(' ');
    if (Array.isArray(corps)) return corps.join(' ');
    return defaut;
  }
}
