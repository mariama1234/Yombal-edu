import { Component, OnInit, effect, inject, signal, untracked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { environment } from '../../../environments/environment';
import { GenericCrudService } from './generic-crud.service';
import { ENTITY_CONFIGS } from './entity-configs';
import { EntityConfig, EtapeSuivante, FiltreConfig, LienLigne, RowAction } from './crud-config.model';
import { CreerAccesComponent } from '../acces/creer-acces.component';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { ToastService } from '../../core/services/toast.service';
import { AuthService } from '../../core/services/auth.service';
import { Module } from '../../core/models/models';
import { MODULE_DE_L_ENTITE } from '../../core/guards/acces.guard';
import { dateDuJour } from '../dates';

@Component({
  selector: 'app-generic-list',
  standalone: true,
  imports: [CommonModule, RouterLink, CreerAccesComponent],
  templateUrl: './generic-list.component.html',
})
export class GenericListComponent implements OnInit {
  config!: EntityConfig;
  items = signal<any[]>([]);
  loading = signal(true);
  total = signal(0);
  erreur = signal<string | null>(null);

  /** Fiche dont on est en train de créer le compte de connexion. */
  accesPour = signal<any | null>(null);

  /** Libellé du dernier élément envoyé à la corbeille, pour offrir de le récupérer. */
  dernierSupprime = signal<string | null>(null);

  /** Enregistrement qui vient d'être fait depuis le formulaire : on propose la suite. */
  enregistre = signal<{ libelle: string | null; creation: boolean } | null>(null);

  /** Valeur choisie dans chaque liste déroulante, par paramètre d'URL. */
  valeursFiltres = signal<Record<string, string>>({});
  optionsFiltres = signal<Record<string, any[]>>({});

  /** Sélecteur de période (`selecteurPeriode`) : la vue et sa date de référence. */
  vue = signal<'annuelle' | 'mensuelle' | 'hebdomadaire'>('annuelle');
  datePeriode = signal(dateDuJour());

  /** Vrai le temps d'enregistrer un nouvel ordre, ou de préparer un téléchargement. */
  occupe = signal(false);
  private http = inject(HttpClient);

  annees = inject(AnneeScolaireService);
  private toast = inject(ToastService);
  private auth = inject(AuthService);

  /**
   * Ajout, modification, suppression et actions de ligne : seulement si
   * l'entité n'est pas en consultation seule *et* que le profil peut écrire
   * dans son module. Un comptable voit les classes, il ne les modifie pas.
   */
  modifiable(): boolean {
    if (!this.config || this.config.lectureSeule) return false;
    return this.auth.peutEcrire(MODULE_DE_L_ENTITE[this.config.key]);
  }

  constructor(private route: ActivatedRoute, private router: Router, private crud: GenericCrudService<any>) {
    // Changer d'année dans le bandeau haut recharge la liste courante, sans
    // que l'utilisateur ait à revenir dessus.
    effect(() => {
      this.annees.idAnnee();
      if (!this.config?.filtreParAnnee) return;
      // Un filtre qui suit l'année (les classes) perd son choix : la classe
      // retenue n'existe pas dans l'autre année.
      untracked(() => {
        if ((this.config.filtres ?? []).some((f) => this.filtreSuitLAnnee(f))) {
          this.valeursFiltres.update((v) => {
            const reste = { ...v };
            for (const f of this.config.filtres ?? []) if (this.filtreSuitLAnnee(f)) delete reste[f.param];
            return reste;
          });
          this.chargerFiltres();
        }
      });
      this.charger();
    });
  }

  ngOnInit(): void {
    this.route.data.subscribe((data) => {
      this.config = ENTITY_CONFIGS[data['entityKey']];
      this.valeursFiltres.set({});
      this.dernierSupprime.set(null);
      // Transmis par le formulaire à la navigation ; effacé aussitôt lu, sans
      // quoi un rafraîchissement de la page ferait réapparaître le bandeau.
      const etat = history.state?.enregistre ?? null;
      this.enregistre.set(etat);
      if (etat) history.replaceState({ ...history.state, enregistre: null }, '');
      this.chargerFiltres();
      this.charger();
    });
  }

  /** Vrai quand la liste affichée est restreinte à une année archivée. */
  surUneArchive(): boolean {
    return !!this.config?.filtreParAnnee && this.annees.consulteUneArchive();
  }

  libelleAnnee(): string {
    return this.annees.anneeCourante()?.libelle ?? '';
  }

  charger(): void {
    this.loading.set(true);
    const params: Record<string, string> = {};
    const id = this.annees.idAnnee();
    if (this.config.filtreParAnnee && id !== null) {
      params['id_annee_scolaire'] = String(id);
    }
    // Hors suivi : l'effet de l'année ne doit pas se relancer à chaque filtre,
    // `filtrer()` recharge déjà.
    for (const [param, valeur] of Object.entries(untracked(() => this.valeursFiltres()))) {
      if (valeur) params[param] = valeur;
    }
    Object.assign(params, untracked(() => this.parametresPeriode()));
    // Une liste qu'on réordonne doit être entière : déplacer une ligne parmi
    // les vingt premières en laisserait d'autres à un rang inchangé.
    if (this.config.reordonnable) params['page_size'] = '200';
    // Filtre obligatoire dont les options ne sont pas encore là : on attend
    // plutôt que d'afficher toute la liste d'un coup. Sans aucune option (pas
    // de niveau créé), la liste s'affiche entière.
    const options = untracked(() => this.optionsFiltres());
    if ((this.config.filtres ?? []).some(
      (f) => f.obligatoire && !params[f.param] && options[f.param] === undefined)) {
      return;
    }
    this.crud.list(this.config.endpoint, params).subscribe({
      next: (res) => {
        this.items.set(res.results);
        this.total.set(res.count);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  /** Le filtre propose les lignes d'une liste qui suit l'année (les classes). */
  private filtreSuitLAnnee(filtre: FiltreConfig): boolean {
    return !!filtre.optionsEndpoint && !!ENTITY_CONFIGS[filtre.optionsEndpoint]?.filtreParAnnee;
  }

  private chargerFiltres(): void {
    this.optionsFiltres.set({});
    for (const filtre of this.config.filtres ?? []) {
      if (filtre.options || !filtre.optionsEndpoint) {
        this.optionsFiltres.update((o) => ({
          ...o, [filtre.param]: (filtre.options ?? []).map((x) => ({ id: x.value, libelle: x.label })),
        }));
        continue;
      }
      // Une liste déroulante doit tout proposer, pas la première page. Les
      // classes proposées sont celles de l'année du bandeau.
      const params: Record<string, string> = { page_size: '200' };
      const id = this.annees.idAnnee();
      if (this.filtreSuitLAnnee(filtre) && id !== null) params['id_annee_scolaire'] = String(id);
      this.crud.list(filtre.optionsEndpoint, params).subscribe({
        next: (res) => {
          const options = res.results ?? [];
          this.optionsFiltres.update((o) => ({ ...o, [filtre.param]: options }));
          if (filtre.obligatoire && options.length && !this.valeursFiltres()[filtre.param]) {
            this.valeursFiltres.update((v) => ({ ...v, [filtre.param]: String(options[0].id) }));
          }
          if (filtre.obligatoire) this.charger();
        },
        error: () => {
          this.optionsFiltres.update((o) => ({ ...o, [filtre.param]: [] }));
          if (filtre.obligatoire) this.charger();
        },
      });
    }
  }

  libelleOption(filtre: FiltreConfig, option: any): string {
    if (filtre.options) return option.libelle;
    return option[filtre.optionsLabelKey || 'nom'] ?? `#${option.id}`;
  }

  // ---- Sélecteur de période -----------------------------------------------

  private parametresPeriode(): Record<string, string> {
    if (!this.config?.selecteurPeriode || this.vue() === 'annuelle') return {};
    return { vue: this.vue(), date: this.datePeriode() || dateDuJour() };
  }

  choisirVue(vue: string): void {
    this.vue.set(vue as 'annuelle' | 'mensuelle' | 'hebdomadaire');
    this.charger();
  }

  choisirDate(date: string): void {
    this.datePeriode.set(date);
    if (this.vue() !== 'annuelle') this.charger();
  }

  /** Recule ou avance d'un mois ou d'une semaine, selon la vue. */
  decaler(sens: 1 | -1): void {
    const [a, m, j] = (this.datePeriode() || dateDuJour()).split('-').map(Number);
    const d = this.vue() === 'mensuelle' ? new Date(a, m - 1 + sens, 1) : new Date(a, m - 1, j + 7 * sens);
    const deux = (n: number) => String(n).padStart(2, '0');
    this.choisirDate(`${d.getFullYear()}-${deux(d.getMonth() + 1)}-${deux(d.getDate())}`);
  }

  // ---- Ordre d'affichage ---------------------------------------------------

  /**
   * Monte ou descend une ligne d'un cran, puis enregistre l'ordre de la liste
   * affichée. L'écran bouge tout de suite ; un refus du serveur le remet
   * comme il était.
   */
  deplacer(index: number, sens: 1 | -1): void {
    const lignes = [...this.items()];
    const cible = index + sens;
    if (cible < 0 || cible >= lignes.length || this.occupe()) return;
    const avant = this.items();
    [lignes[index], lignes[cible]] = [lignes[cible], lignes[index]];
    this.items.set(lignes);
    this.occupe.set(true);
    this.http.post(`${environment.apiUrl}/${this.config.endpoint}/reordonner/`,
                   { ids: lignes.map((l) => l.id) }).subscribe({
      next: () => this.occupe.set(false),
      error: (e) => {
        this.items.set(avant);
        this.occupe.set(false);
        this.erreur.set(e?.error?.ids ?? e?.error?.detail ?? 'Le nouvel ordre n’a pas pu être enregistré.');
      },
    });
  }

  // ---- Téléchargement --------------------------------------------------------

  telecharger(): void {
    const telechargement = this.config.telechargement;
    if (!telechargement || this.occupe()) return;
    const params: Record<string, string> = {};
    const id = this.annees.idAnnee();
    if (this.config.filtreParAnnee && id !== null) params['id_annee_scolaire'] = String(id);
    for (const [param, valeur] of Object.entries(this.valeursFiltres())) if (valeur) params[param] = valeur;
    Object.assign(params, this.parametresPeriode());
    this.occupe.set(true);
    this.http.get(`${environment.apiUrl}/${this.config.endpoint}/${telechargement.chemin}/`, {
      params, responseType: 'blob', observe: 'response',
    }).subscribe({
      next: (reponse) => {
        const nom = /filename="?([^"]+)"?/.exec(reponse.headers.get('Content-Disposition') ?? '');
        const url = URL.createObjectURL(reponse.body!);
        const lien = document.createElement('a');
        lien.href = url;
        lien.download = nom ? nom[1] : 'document.pdf';
        lien.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        this.occupe.set(false);
      },
      error: () => {
        this.erreur.set('Le document n’a pas pu être préparé.');
        this.occupe.set(false);
      },
    });
  }

  filtrer(param: string, valeur: string): void {
    this.valeursFiltres.update((v) => ({ ...v, [param]: valeur }));
    this.charger();
  }

  filtreActif(): boolean {
    return Object.values(this.valeursFiltres()).some((v) => !!v);
  }

  supprimer(item: any): void {
    if (!confirm('Supprimer cet élément ? Il restera récupérable depuis la corbeille.')) return;
    this.erreur.set(null);
    this.dernierSupprime.set(null);
    this.enregistre.set(null);
    this.crud.remove(this.config.endpoint, item.id).subscribe({
      next: () => {
        const colonne = this.config.columns[0]?.key;
        this.dernierSupprime.set(String((colonne && item[colonne]) ?? `#${item.id}`));
        this.toast.succes('Placé dans la corbeille',
                          `« ${this.dernierSupprime()} » reste récupérable depuis la corbeille.`);
        // Une année supprimée ne doit plus être proposée dans le bandeau haut.
        if (this.config.endpoint === 'annees-scolaires') this.annees.recharger();
        this.charger();
      },
      error: (e) => this.erreur.set(e?.error?.detail ?? 'La suppression a échoué.'),
    });
  }

  /**
   * Créer le compte de connexion d'une fiche : réservé à la direction, et
   * seulement tant que la fiche n'en a pas.
   */
  peutCreerUnAcces(item: any): boolean {
    return !!this.config?.acces && this.auth.peutEcrire('comptes') && !item.a_un_acces;
  }

  libelleAcces(item: any): string {
    const cle = this.config?.acces?.libelle ?? 'nom_complet';
    const nom = item[cle] ?? `${item.prenom ?? ''} ${item.nom ?? ''}`.trim();
    return nom || `#${item.id}`;
  }

  accesCree(): void {
    this.accesPour.set(null);
    this.charger();
  }

  /** Paramètres d'URL d'un raccourci de ligne, ex: `{ classe: 12 }`. */
  parametresLien(lien: LienLigne, item: any): Record<string, unknown> {
    return { [lien.parametre]: item[lien.champ ?? 'id'] };
  }

  ouvrirNouveau(): void {
    this.router.navigate([this.router.url, 'nouveau']);
  }

  actionVisible(action: RowAction, item: any): boolean {
    if (!action.visibleSi) return true;
    return item[action.visibleSi.champ] === action.visibleSi.valeur;
  }

  lancerAction(action: RowAction, item: any): void {
    if (action.confirmation && !confirm(action.confirmation)) return;
    this.erreur.set(null);
    this.crud.action(this.config.endpoint, item.id, action.chemin).subscribe({
      next: (reponse: any) => {
        // Une action qui s'explique (« paramètres envoyés à … ») le dit.
        if (reponse?.detail) this.toast.succes(action.label, reponse.detail);
        this.charger();
      },
      error: (e) => this.erreur.set(e?.error?.detail ?? "L'action a échoué."),
    });
  }

  /**
   * Les étapes suivantes ouvertes au profil : une suite vers un écran qu'il
   * ne peut pas ouvrir ne lui est pas proposée.
   */
  etapesVisibles(): EtapeSuivante[] {
    return (this.config?.suite ?? []).filter((e) => this.accesOuvert(e.module));
  }

  accesOuvert(regle?: string): boolean {
    if (!regle) return true;
    const [module, niveau] = regle.split(':');
    return niveau === 'ecriture'
      ? this.auth.peutEcrire(module as Module)
      : this.auth.peutLire(module as Module);
  }

}
