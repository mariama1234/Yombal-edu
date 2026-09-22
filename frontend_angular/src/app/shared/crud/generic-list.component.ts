import { Component, OnInit, effect, inject, signal, untracked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { GenericCrudService } from './generic-crud.service';
import { ENTITY_CONFIGS } from './entity-configs';
import { EntityConfig, FiltreConfig, LienLigne, RowAction } from './crud-config.model';
import { CreerAccesComponent } from '../acces/creer-acces.component';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { ToastService } from '../../core/services/toast.service';
import { AuthService } from '../../core/services/auth.service';
import { MODULE_DE_L_ENTITE } from '../../core/guards/acces.guard';

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
      if (this.config?.filtreParAnnee) this.charger();
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

  private chargerFiltres(): void {
    this.optionsFiltres.set({});
    for (const filtre of this.config.filtres ?? []) {
      // Une liste déroulante doit tout proposer, pas la première page.
      this.crud.list(filtre.optionsEndpoint, { page_size: '200' }).subscribe({
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
    return option[filtre.optionsLabelKey || 'nom'] ?? `#${option.id}`;
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
      next: () => this.charger(),
      error: (e) => this.erreur.set(e?.error?.detail ?? "L'action a échoué."),
    });
  }
}
