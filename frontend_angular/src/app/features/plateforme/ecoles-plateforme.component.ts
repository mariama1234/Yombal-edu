import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { EcolePlateforme, PlanAbonnement } from '../../core/models/models';
import { AuthService } from '../../core/services/auth.service';
import { GrapheAnneauComponent, Segment } from '../../shared/graphes/graphe-anneau.component';
import { PlateformeService } from './plateforme.service';

type FiltrePaiement = '' | 'paye' | 'impaye';

/** Teinte de l'initiale d'une école : fixée par son identifiant, jamais tirée au hasard. */
const TEINTES = ['var(--serie-1)', 'var(--serie-2)', 'var(--serie-3)', 'var(--navy-700)', 'var(--serie-5)', 'var(--serie-6)'];

/**
 * Accueil de la plateforme : les écoles clientes, leur offre et leur abonnement.
 *
 * Le chiffre mis en avant est celui des abonnements non réglés : c'est lui
 * qui appelle une action. Les écoles s'affichent en cartes — une fiche
 * d'identité par client se lit mieux qu'une ligne de tableau.
 */
@Component({
  selector: 'app-ecoles-plateforme',
  standalone: true,
  imports: [RouterLink, DatePipe, DecimalPipe, GrapheAnneauComponent],
  template: `
    <section class="pf-hero">
      <div>
        <p class="pf-hero-surtitre">Console plateforme</p>
        <h1>Bonjour {{ auth.currentUser()?.prenom }}</h1>
        <p>
          @if (chargement()) { Chargement des écoles… }
          @else if (impayees() > 0) {
            <strong>{{ impayees() }} abonnement(s)</strong> attendent leur règlement.
          } @else if (ecoles().length) {
            Tous les abonnements sont à jour.
          } @else { Créez votre première école cliente pour commencer. }
        </p>
      </div>
      <a class="pf-bouton-clair" routerLink="/plateforme/ecoles/nouvelle">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14M5 12h14"/></svg>
        Nouvelle école
      </a>
    </section>

    <div class="pf-stats">
      <div class="pf-stat">
        <span class="pf-stat-icone" style="background:var(--serie-1-fond); color:var(--serie-1);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6"/></svg>
        </span>
        <div>
          <div class="pf-stat-valeur">{{ ecoles().length }}</div>
          <div class="pf-stat-libelle">École(s) · {{ actives() }} active(s)</div>
        </div>
      </div>
      <div class="pf-stat">
        <span class="pf-stat-icone" style="background:#E4E9F2; color:var(--navy-900);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 2l3 6.5 7 .8-5.2 4.8 1.5 7L12 17.6 5.7 21l1.5-7L2 9.3l7-.8z"/></svg>
        </span>
        <div>
          <div class="pf-stat-valeur">{{ parPlan()['pro'] }}</div>
          <div class="pf-stat-libelle">En offre Pro</div>
        </div>
      </div>
      <div class="pf-stat" [class.alerte]="impayees() > 0">
        <span class="pf-stat-icone" style="background:var(--red-bg); color:var(--red);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5h.01"/></svg>
        </span>
        <div>
          <div class="pf-stat-valeur">{{ impayees() }}</div>
          <div class="pf-stat-libelle">Abonnement(s) non réglé(s)</div>
        </div>
      </div>
      <div class="pf-stat">
        <span class="pf-stat-icone" style="background:var(--green-bg); color:var(--green);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M12 12a4 4 0 100-8 4 4 0 000 8z"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></svg>
        </span>
        <div>
          <div class="pf-stat-valeur">{{ totalEleves() | number:'1.0-0' }}</div>
          <div class="pf-stat-libelle">Élèves suivis</div>
        </div>
      </div>
    </div>

    @if (ecoles().length) {
      <div class="grille-anneaux">
        <div class="panel">
          <div class="panel-head"><div class="panel-title">Répartition des offres</div></div>
          <app-graphe-anneau [segments]="segmentsOffres()" libelleTotal="écoles" />
        </div>
        <div class="panel">
          <div class="panel-head"><div class="panel-title">Abonnements</div></div>
          <app-graphe-anneau [segments]="segmentsPaiement()" libelleTotal="écoles facturées"
                             vide="Aucune école facturée : toutes sont en essai." />
        </div>
      </div>
    }

    <div class="pf-barre-filtres">
      <div class="pf-recherche">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
        <input type="search" placeholder="Rechercher une école, un administrateur…" aria-label="Rechercher une école"
               (input)="recherche.set($any($event.target).value)">
      </div>
      <div class="pf-pastilles" role="group" aria-label="Filtrer par offre">
        @for (o of offres; track o.valeur) {
          <button type="button" class="chip" [class.active]="plan() === o.valeur" (click)="plan.set(o.valeur)">{{ o.libelle }}</button>
        }
      </div>
      <div class="pf-pastilles" role="group" aria-label="Filtrer par paiement">
        @for (p of paiements; track p.valeur) {
          <button type="button" class="chip" [class.active]="paiement() === p.valeur" (click)="paiement.set(p.valeur)">{{ p.libelle }}</button>
        }
      </div>
    </div>

    @if (chargement()) {
      <div class="pf-grille">
        @for (i of [1, 2, 3]; track i) { <div class="pf-carte pf-carte-attente"></div> }
      </div>
    } @else if (visibles().length === 0) {
      <div class="panel pf-vide">
        @if (ecoles().length === 0) {
          <p>Aucune école pour le moment.</p>
          <a class="btn-primary" routerLink="/plateforme/ecoles/nouvelle">Créer la première école</a>
        } @else {
          <p>Aucune école ne correspond à ces filtres.</p>
        }
      </div>
    } @else {
      <div class="pf-grille">
        @for (e of visibles(); track e.id) {
          <a class="pf-carte" [routerLink]="['/plateforme/ecoles', e.id]" [class.alerte]="enRetard(e)">
            <div class="pf-carte-tete">
              <span class="pf-initiale" [style.background]="teinte(e)">{{ initiale(e) }}</span>
              <div class="pf-carte-titre">
                <strong>{{ e.nom }}</strong>
                <span>{{ e.adresse || e.telephone || 'Coordonnées à compléter' }}</span>
              </div>
              <span class="pill offre" [class]="e.plan_abonnement">{{ e.plan_libelle }}</span>
            </div>

            <div class="pf-carte-paiement">
              @if (e.abonnement_paye) {
                <span class="pf-badge ok">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>
                  Payé@if (e.date_paiement) { le {{ e.date_paiement | date:'dd/MM/yyyy' }} }
                </span>
              } @else if (e.plan_abonnement === 'essai') {
                <span class="pf-badge neutre">En période d'essai</span>
              } @else {
                <span class="pf-badge ko">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M12 7v6M12 16.5h.01"/></svg>
                  Non payé
                </span>
              }
              @if (e.statut !== 'actif') { <span class="pill" [class]="e.statut">{{ e.statut_libelle }}</span> }
            </div>

            <div class="pf-carte-chiffres">
              <div><strong>{{ e.nb_eleves | number:'1.0-0' }}</strong><span>élèves</span></div>
              <div><strong>{{ e.nb_utilisateurs }}</strong><span>comptes</span></div>
              <div><strong>{{ e.date_creation | date:'MM/yyyy' }}</strong><span>cliente depuis</span></div>
            </div>

            <div class="pf-carte-pied">
              @if (e.admins.length) {
                <div class="pf-pile-avatars">
                  @for (a of e.admins.slice(0, 3); track a.id) {
                    <span class="pf-mini-avatar" [title]="a.prenom + ' ' + a.nom + ' — ' + a.email">{{ a.prenom[0] }}{{ a.nom[0] }}</span>
                  }
                </div>
                <span class="pf-admins-texte">
                  {{ e.admins[0].prenom }} {{ e.admins[0].nom }}
                  @if (e.admins.length > 1) { <span class="hint">+{{ e.admins.length - 1 }}</span> }
                </span>
              } @else {
                <span class="pf-admins-texte" style="color:var(--red);">Aucun administrateur</span>
              }
              <span class="pf-ouvrir">Ouvrir →</span>
            </div>
          </a>
        }
      </div>
    }
  `,
})
export class EcolesPlateformeComponent implements OnInit {
  private plateforme = inject(PlateformeService);
  auth = inject(AuthService);

  ecoles = signal<EcolePlateforme[]>([]);
  chargement = signal(true);
  recherche = signal('');
  plan = signal<'' | PlanAbonnement>('');
  paiement = signal<FiltrePaiement>('');

  readonly offres: { valeur: '' | PlanAbonnement; libelle: string }[] = [
    { valeur: '', libelle: 'Toutes' }, { valeur: 'pro', libelle: 'Pro' },
    { valeur: 'standard', libelle: 'Standard' }, { valeur: 'essai', libelle: 'Essai' },
  ];
  readonly paiements: { valeur: FiltrePaiement; libelle: string }[] = [
    { valeur: '', libelle: 'Tous les paiements' }, { valeur: 'impaye', libelle: 'Non payées' },
    { valeur: 'paye', libelle: 'Payées' },
  ];

  ngOnInit(): void {
    this.plateforme.lister().subscribe({
      next: (r) => { this.ecoles.set(r.results ?? []); this.chargement.set(false); },
      error: () => this.chargement.set(false),
    });
  }

  actives = computed(() => this.ecoles().filter((e) => e.statut === 'actif').length);
  totalEleves = computed(() => this.ecoles().reduce((t, e) => t + e.nb_eleves, 0));

  parPlan = computed(() => {
    const compte: Record<PlanAbonnement, number> = { essai: 0, standard: 0, pro: 0 };
    for (const e of this.ecoles()) compte[e.plan_abonnement] = (compte[e.plan_abonnement] ?? 0) + 1;
    return compte;
  });

  /** Une école en essai ne doit rien encore : elle n'est pas comptée en impayé. */
  enRetard(e: EcolePlateforme): boolean {
    return !e.abonnement_paye && e.plan_abonnement !== 'essai' && e.statut === 'actif';
  }

  impayees = computed(() => this.ecoles().filter((e) => this.enRetard(e)).length);

  segmentsOffres = computed<Segment[]>(() => {
    const p = this.parPlan();
    return [
      { libelle: 'Pro', valeur: p.pro, couleur: 'var(--navy-900)' },
      { libelle: 'Standard', valeur: p.standard, couleur: 'var(--serie-1)' },
      { libelle: 'Essai', valeur: p.essai, couleur: 'var(--serie-autre)' },
    ];
  });

  /** Payé ou non : des états, donc les couleurs d'état, nommées dans la légende. */
  segmentsPaiement = computed<Segment[]>(() => {
    const facturees = this.ecoles().filter((e) => e.plan_abonnement !== 'essai');
    return [
      { libelle: 'Payées', valeur: facturees.filter((e) => e.abonnement_paye).length, couleur: 'var(--green)' },
      { libelle: 'Non payées', valeur: facturees.filter((e) => !e.abonnement_paye).length, couleur: 'var(--red)' },
    ];
  });

  visibles = computed(() => {
    const texte = this.recherche().trim().toLowerCase();
    return this.ecoles().filter((e) =>
      (!texte || [e.nom, e.email, e.telephone, e.adresse, ...e.admins.flatMap((a) => [a.email, `${a.prenom} ${a.nom}`])]
        .some((v) => (v ?? '').toLowerCase().includes(texte)))
      && (!this.plan() || e.plan_abonnement === this.plan())
      && (this.paiement() === '' || (this.paiement() === 'paye') === e.abonnement_paye));
  });

  initiale(e: EcolePlateforme): string {
    // « École MIC CITE » → « M » : le mot « École » n'aide pas à reconnaître.
    const mots = e.nom.split(/\s+/).filter((m) => !/^(é|e)cole$/i.test(m));
    return (mots[0]?.[0] ?? e.nom[0] ?? '?').toUpperCase();
  }

  teinte(e: EcolePlateforme): string {
    return TEINTES[e.id % TEINTES.length];
  }
}
