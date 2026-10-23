import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, ReactiveFormsModule, ValidatorFn, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable, concat, forkJoin, toArray } from 'rxjs';
import { GenericCrudService } from './generic-crud.service';
import { ToastService } from '../../core/services/toast.service';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { ENTITY_CONFIGS } from './entity-configs';
import { EntityConfig, EtapeSuivante, FieldConfig } from './crud-config.model';
import { AuthService } from '../../core/services/auth.service';
import { Module } from '../../core/models/models';
import { dateDuJour, pasDansLeFutur } from '../dates';
import { EnfantsParentComponent } from '../parents/enfants-parent.component';

@Component({
  selector: 'app-generic-form',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink, EnfantsParentComponent],
  templateUrl: './generic-form.component.html',
})
export class GenericFormComponent implements OnInit {
  config!: EntityConfig;
  form!: FormGroup;
  modeEdition = false;
  idEnCours: number | null = null;
  optionsParChamp: Record<string, any[]> = {};
  /**
   * Valeurs de la ligne ouverte en modification : ce qui est déjà imputé
   * ailleurs (un budget, par exemple) et ne doit pas être décompté deux fois.
   */
  private valeursInitiales: Record<string, any> = {};
  /** Vrai le temps de remplir le formulaire depuis l'API, en modification. */
  private remplissageEnCours = false;
  enregistrement = signal(false);
  /** Pièces choisies dans les champs `fichier`, envoyées après l'enregistrement. */
  fichiersChoisis = signal<Record<string, File>>({});
  /** Champs `fichier` dont la pièce déjà jointe est à retirer à l'enregistrement. */
  retraits = signal<Record<string, boolean>>({});
  erreurFichier = signal<string | null>(null);
  /** Refus du serveur, champ par champ, et ce qui ne vise aucun champ. */
  erreursServeur = signal<Record<string, string>>({});
  erreurGenerale = signal<string | null>(null);
  private toast = inject(ToastService);
  private annees = inject(AnneeScolaireService);
  private auth = inject(AuthService);

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private fb: FormBuilder,
    private crud: GenericCrudService<any>
  ) {}

  ngOnInit(): void {
    this.route.data.subscribe((data) => {
      this.config = ENTITY_CONFIGS[data['entityKey']];
      this.construireFormulaire();
      this.chargerOptions();

      this.suivreLesChampsSources();

      const id = this.route.snapshot.paramMap.get('id');
      if (id && id !== 'nouveau') {
        this.modeEdition = true;
        this.idEnCours = +id;
        this.crud.get(this.config.endpoint, +id).subscribe((item) => {
          this.valeursInitiales = item;
          // Le remplissage ne doit pas être pris pour un choix de l'utilisateur :
          // sinon poser la classe effacerait l'élève qu'on vient de charger.
          this.remplissageEnCours = true;
          this.form.patchValue(item);
          this.remplissageEnCours = false;
          this.rechargerListesDependantes();
        });
      }
    });
  }

  private construireFormulaire(): void {
    const controls: Record<string, any> = {};
    for (const field of this.config.fields) {
      // Un champ `lecture` n'a pas de contrôle : il affiche une valeur qui
      // vient d'ailleurs, et ne doit pas partir dans le payload.
      if (field.type === 'lecture' || field.type === 'fichier') continue;
      const validators: ValidatorFn[] = field.required ? [Validators.required] : [];
      if (field.min !== undefined) validators.push(Validators.min(field.min));
      if (field.pasDansLeFutur) validators.push(pasDansLeFutur);
      controls[field.key] = [field.type === 'checkbox' ? false : this.valeurParDefaut(field), validators];
      if (field.optionsEndpoint) {
        this.optionsParChamp[field.key] = [];
      }
    }
    this.form = this.fb.group(controls);
  }

  /**
   * Valeur de départ d'un champ. En modification, elle est aussitôt remplacée
   * par celle de la ligne chargée : le défaut ne vaut que pour une création.
   */
  private valeurParDefaut(field: FieldConfig): string {
    if (field.defaut === 'aujourdhui') return dateDuJour();
    return '';
  }

  /** Borne haute du sélecteur de date : aujourd'hui pour un champ `pasDansLeFutur`. */
  dateMax(field: FieldConfig): string | null {
    return field.pasDansLeFutur ? dateDuJour() : null;
  }

  /**
   * Ce qui ne va pas dans la saisie d'un champ. Un nombre négatif ou une date
   * à venir se signalent dès la frappe ; un champ vide, seulement une fois
   * quitté — sinon le formulaire s'ouvrirait couvert d'erreurs.
   */
  erreurSaisie(field: FieldConfig): string | null {
    const controle = this.form.get(field.key);
    if (!controle || !controle.invalid || !(controle.touched || controle.dirty)) return null;
    if (controle.hasError('required')) return controle.touched ? 'Ce champ est requis.' : null;
    if (controle.hasError('min')) {
      return field.min === 0 ? 'La valeur ne peut pas être négative.'
                             : `La valeur doit être d'au moins ${field.min}.`;
    }
    if (controle.hasError('dateFuture')) return 'La date ne peut pas dépasser aujourd’hui.';
    return 'Valeur invalide.';
  }

  private chargerOptions(): void {
    const champsAvecEndpoint = this.config.fields.filter(
      (f) => f.optionsEndpoint && !f.optionsFiltrePar);
    if (champsAvecEndpoint.length === 0) return;

    const requetes = champsAvecEndpoint.reduce((acc, f) => {
      acc[f.key] = this.crud.list(f.optionsEndpoint!, this.parametresOptions(f.optionsEndpoint!));
      return acc;
    }, {} as Record<string, any>);

    forkJoin(requetes).subscribe((resultats: any) => {
      for (const key of Object.keys(resultats)) {
        this.optionsParChamp[key] = resultats[key].results;
      }
    });
  }

  /**
   * Une liste dépendante suit le champ qui la restreint : choisir une classe
   * recharge ses élèves, et en changer efface l'élève déjà choisi — il
   * n'appartient plus à la classe affichée.
   */
  private suivreLesChampsSources(): void {
    for (const field of this.config.fields) {
      const lien = field.optionsFiltrePar;
      if (!lien) continue;
      this.form.get(lien.champ)?.valueChanges.subscribe(() => {
        if (this.remplissageEnCours) return;
        this.form.get(field.key)?.setValue('');
        this.chargerListeDependante(field);
      });
    }
  }

  private rechargerListesDependantes(): void {
    for (const field of this.config.fields) {
      if (field.optionsFiltrePar) this.chargerListeDependante(field);
    }
  }

  private chargerListeDependante(field: FieldConfig): void {
    const lien = field.optionsFiltrePar!;
    const valeur = this.form.get(lien.champ)?.value;
    if (!valeur) {
      this.optionsParChamp[field.key] = [];
      return;
    }
    this.crud
      .list(field.optionsEndpoint!, {
        ...this.parametresOptions(field.optionsEndpoint!),
        [lien.param]: String(valeur),
      })
      .subscribe({
        next: (r) => (this.optionsParChamp[field.key] = r.results),
        error: () => (this.optionsParChamp[field.key] = []),
      });
  }

  /** Vrai quand la liste attend qu'on choisisse d'abord son champ source. */
  attendSonChampSource(field: FieldConfig): boolean {
    const lien = field.optionsFiltrePar;
    return !!lien && !this.form.get(lien.champ)?.value;
  }

  libelleChampSource(field: FieldConfig): string {
    const lien = field.optionsFiltrePar;
    const source = this.config.fields.find((f) => f.key === lien?.champ);
    return (source?.label ?? '').toLowerCase();
  }

  /**
   * Une liste d'options qui se compte par année suit l'année du bandeau haut,
   * comme sa propre page. Sans ça, le budget annoncé ici serait celui de
   * l'année active pendant qu'on consulte un autre exercice.
   *
   * `page_size` est large à dessein : une liste déroulante doit tout
   * proposer. Sans lui, elle s'arrêtait à la première page de l'API — vingt
   * élèves sur cinq cents, sans que rien ne le dise.
   */
  private parametresOptions(endpoint: string): Record<string, string> {
    const id = this.annees.idAnnee();
    const params: Record<string, string> = { page_size: '200' };
    if (ENTITY_CONFIGS[endpoint]?.filtreParAnnee && id !== null) {
      params['id_annee_scolaire'] = String(id);
    }
    return params;
  }

  libelleOption(field: FieldConfig, option: any): string {
    const cle = field.optionsLabelKey || 'nom';
    const libelle = option[cle] ?? `#${option.id}`;
    const precision = field.optionsSecondaireKey ? option[field.optionsSecondaireKey] : null;
    return precision ? `${libelle} (${precision})` : String(libelle);
  }

  /** Valeur brute d'un champ `lecture`, lue sur l'option choisie dans son champ source. */
  private valeurSource(field: FieldConfig, cle?: string): unknown {
    const source = field.source;
    if (!source) return null;
    const choisi = this.form.get(source.champ)?.value;
    const option = (this.optionsParChamp[source.champ] ?? [])
      .find((o) => String(o.id) === String(choisi));
    return option?.[cle ?? source.cle];
  }

  /**
   * Ce que la saisie en cours retranche à la valeur affichée.
   *
   * En modification, la ligne ouverte est déjà imputée — la dépense a déjà
   * consommé le budget : seul l'écart avec le montant d'origine se retranche,
   * sinon rouvrir une dépense la décompterait une seconde fois. Changer de
   * catégorie en cours de route remet ce compte à zéro : l'enveloppe qui
   * s'affiche alors est celle d'un autre poste, qui n'a rien vu passer.
   */
  private montantEngage(field: FieldConfig): number {
    const source = field.source;
    if (!source?.compare) return 0;
    const saisi = Number(this.form.get(source.compare)?.value);
    if (!Number.isFinite(saisi)) return 0;
    const memeSource = String(this.form.get(source.champ)?.value ?? '')
                    === String(this.valeursInitiales[source.champ] ?? '');
    const initial = memeSource ? Number(this.valeursInitiales[source.compare] ?? 0) : 0;
    return saisi - (Number.isFinite(initial) ? initial : 0);
  }

  /** Ce qui resterait une fois la saisie enregistrée, ou `null` hors des nombres. */
  private resteApres(field: FieldConfig): number | null {
    const valeur = Number(this.valeurSource(field));
    if (!Number.isFinite(valeur)) return null;
    return valeur - this.montantEngage(field);
  }

  /**
   * Affichage d'un champ `lecture` : le reste, saisie en cours déduite, pour
   * que l'enveloppe descende à mesure que le montant se tape plutôt qu'au
   * retour de l'API. Tant que rien n'est choisi, il n'y a rien à montrer.
   */
  valeurLecture(field: FieldConfig): string {
    const valeur = this.valeurSource(field);
    if (valeur === undefined || valeur === null || valeur === '') return '—';
    const reste = this.resteApres(field);
    const texte = reste === null ? String(valeur) : reste.toLocaleString('fr-FR');
    return field.source?.suffixe ? `${texte} ${field.source.suffixe}` : texte;
  }

  /**
   * Rappel du décompte tant qu'un montant est saisi : d'où l'on part, ce qui
   * est engagé, ce qui reste. Le champ n'affiche que le reste — sans ce
   * rappel, on ne saurait plus de quelle enveloppe il descend.
   */
  detailLecture(field: FieldConfig): string | null {
    const engage = this.montantEngage(field);
    const depart = Number(this.valeurSource(field));
    if (engage === 0 || !Number.isFinite(depart)) return null;
    const nombre = (v: number) => v.toLocaleString('fr-FR');
    const signe = engage > 0 ? '−' : '+';
    return `${nombre(depart)} ${signe} ${nombre(Math.abs(engage))} = ${nombre(depart - engage)}`
         + ` une fois cette ligne enregistrée.`;
  }

  /** Vrai quand la valeur affichée est négative — une enveloppe dépassée. */
  lectureEnNegatif(field: FieldConfig): boolean {
    const reste = this.resteApres(field);
    return reste !== null && reste < 0;
  }

  /**
   * Jauge du champ `lecture` : la part déjà consommée, puis celle qu'ajoute
   * la saisie en cours, en pourcentage de l'enveloppe. Elle donne à voir le
   * décompte pendant la frappe, là où un nombre seul demande de comparer.
   *
   * Sans enveloppe déclarée — un poste sans budget de l'année — il n'y a
   * pas de total auquel rapporter la consommation, donc pas de jauge.
   */
  jaugeLecture(field: FieldConfig): { consomme: number; engage: number; depasse: boolean } | null {
    const cleTotal = field.source?.total;
    const total = cleTotal ? Number(this.valeurSource(field, cleTotal)) : NaN;
    const reste = this.resteApres(field);
    if (!Number.isFinite(total) || total <= 0 || reste === null) return null;
    const part = (montant: number) => Math.max(0, Math.min(100, (montant / total) * 100));
    const engage = this.montantEngage(field);
    // Une saisie revue à la baisse rend du budget : elle raccourcit la part
    // consommée au lieu d'ajouter une part engagée.
    const consomme = part(total - Number(this.valeurSource(field)) + Math.min(0, engage));
    return {
      consomme,
      engage: Math.min(part(Math.max(0, engage)), 100 - consomme),
      depasse: reste < 0,
    };
  }

  /**
   * Vrai quand la saisie fait passer l'enveloppe en dessous de zéro.
   *
   * Averti, pas bloqué : une école peut avoir à passer une dépense au-delà de
   * son enveloppe, et un formulaire qui refuse l'enregistrement la laisserait
   * sans autre solution que de fausser le budget.
   */
  depasseLecture(field: FieldConfig): boolean {
    const compare = field.source?.compare;
    if (!compare) return false;
    const saisi = Number(this.form.get(compare)?.value);
    const reste = this.resteApres(field);
    return Number.isFinite(saisi) && saisi > 0 && reste !== null && reste < 0;
  }

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.enregistrement.set(true);
    this.erreursServeur.set({});
    this.erreurGenerale.set(null);
    const payload = this.form.getRawValue();

    // Un nombre, une date ou une liste déroulante laissés vides n'ont pas de
    // valeur à transmettre : « » n'est ni un nombre, ni une date, ni un
    // identifiant, et l'API le refuse. On omet la clé — le serveur applique
    // alors sa valeur par défaut (un budget non renseigné vaut 0).
    for (const field of this.config.fields) {
      const vide = payload[field.key] === '' || payload[field.key] === null;
      if (vide && ['number', 'date', 'time', 'select'].includes(field.type)) {
        delete payload[field.key];
      }
    }

    const requete = this.modeEdition
      ? this.crud.update(this.config.endpoint, this.idEnCours!, payload)
      : this.crud.create(this.config.endpoint, payload);

    requete.subscribe({
      next: (enregistre) => {
        const id = enregistre?.id ?? this.idEnCours!;
        const pieces = this.requetesPieces(id);
        if (pieces.length === 0) {
          this.terminer(payload, id);
          return;
        }
        concat(...pieces).pipe(toArray()).subscribe({
          next: () => this.terminer(payload, id),
          error: (e) => {
            // La ligne est enregistrée, seule la pièce a été refusée : le
            // formulaire reste ouvert sur elle, en modification, pour qu'on
            // puisse choisir un autre fichier sans tout ressaisir.
            this.enregistrement.set(false);
            this.modeEdition = true;
            this.idEnCours = id;
            this.valeursInitiales = enregistre ?? this.valeursInitiales;
            this.erreurFichier.set(
              'L’enregistrement est fait, mais la pièce jointe a été refusée : '
              + (e?.error?.fichier?.[0] ?? e?.error?.fichier ?? e?.error?.detail
                 ?? 'réessayez avec un autre fichier.'));
          },
        });
      },
      error: (e) => {
        this.enregistrement.set(false);
        this.lireRefus(e?.error);
      },
    });
  }

  /**
   * Répartit un refus de l'API (400) : chaque message sous son champ, le
   * reste en tête du formulaire. Sans cela le bouton se débloquait sans rien
   * dire, et on recommençait sans savoir pourquoi — un e-mail déjà pris, par
   * exemple.
   */
  private lireRefus(corps: unknown): void {
    const texte = (v: unknown): string =>
      Array.isArray(v) ? v.map(texte).join(' ') : typeof v === 'string' ? v : '';
    if (!corps || typeof corps !== 'object') {
      this.erreurGenerale.set('L’enregistrement a échoué. Réessayez dans un instant.');
      return;
    }
    const parChamp: Record<string, string> = {};
    const autres: string[] = [];
    const champs = new Set(this.config.fields.map((f) => f.key));
    for (const [cle, valeur] of Object.entries(corps as Record<string, unknown>)) {
      const message = texte(valeur);
      if (!message) continue;
      if (champs.has(cle)) parChamp[cle] = message;
      else autres.push(message);
    }
    this.erreursServeur.set(parChamp);
    this.erreurGenerale.set(autres.length ? autres.join(' ')
      : Object.keys(parChamp).length ? 'Corrigez les champs signalés.' : 'L’enregistrement a échoué.');
  }

  private terminer(payload: Record<string, any>, id: number): void {
    this.enregistrement.set(false);
    // Le bandeau haut lit sa liste d'années une fois pour toutes : une année
    // ouverte ou modifiée ici doit y apparaître sans recharger la page.
    if (this.config.endpoint === 'annees-scolaires') {
      this.annees.recharger(payload['statut'] === 'active' ? id : undefined);
    }
    // La liste affiche « enregistrement effectué » et propose la suite
    // logique : le nom saisi sert de repère quand il y en a un.
    const champTexte = this.config.fields.find((f) => f.type === 'text');
    const libelle = champTexte ? String(payload[champTexte.key] ?? '').trim() || null : null;
    this.toast.succes(this.modeEdition ? 'Modifications enregistrées' : 'Enregistrement effectué',
                      libelle ?? undefined);
    this.router.navigate(['..'], {
      relativeTo: this.route,
      state: { enregistre: { libelle, creation: !this.modeEdition } },
    });
  }

  // ---- Champs `fichier` -------------------------------------------------

  /**
   * Dépôts et retraits à faire une fois la ligne enregistrée : la pièce se
   * rattache à un identifiant, qu'une création n'a qu'au retour de l'API.
   */
  private requetesPieces(id: number): Observable<unknown>[] {
    const requetes: Observable<unknown>[] = [];
    for (const field of this.config.fields) {
      if (field.type !== 'fichier' || !field.fichier) continue;
      const fichier = this.fichiersChoisis()[field.key];
      if (fichier) {
        requetes.push(this.crud.deposerFichier(this.config.endpoint, id, field.fichier.chemin, fichier));
      } else if (this.retraits()[field.key]) {
        requetes.push(this.crud.retirerFichier(this.config.endpoint, id, field.fichier.chemin));
      }
    }
    return requetes;
  }

  /** Vrai quand la ligne ouverte a déjà une pièce, et qu'on ne l'a pas retirée. */
  pieceJointe(field: FieldConfig): boolean {
    return !!field.fichier && !!this.valeursInitiales[field.fichier.presente]
        && !this.retraits()[field.key];
  }

  choisirFichier(field: FieldConfig, evenement: Event): void {
    const entree = evenement.target as HTMLInputElement;
    const fichier = entree.files?.[0];
    entree.value = '';
    if (!fichier) return;
    this.erreurFichier.set(null);
    this.fichiersChoisis.update((f) => ({ ...f, [field.key]: fichier }));
  }

  annulerChoix(field: FieldConfig): void {
    this.fichiersChoisis.update(({ [field.key]: _, ...reste }) => reste);
  }

  retirerPiece(field: FieldConfig): void {
    this.retraits.update((r) => ({ ...r, [field.key]: true }));
  }

  garderPiece(field: FieldConfig): void {
    this.retraits.update(({ [field.key]: _, ...reste }) => reste);
  }

  /** Ouvre la pièce déjà jointe dans un nouvel onglet, le temps de la relire. */
  ouvrirPiece(field: FieldConfig): void {
    if (!field.fichier || this.idEnCours === null) return;
    this.crud.lireFichier(this.config.endpoint, this.idEnCours, field.fichier.chemin).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        // Révoqué plus tard : trop tôt, l'onglet n'a pas fini de la lire.
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      },
      error: () => this.erreurFichier.set('Cette pièce n’a pas pu être ouverte.'),
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
