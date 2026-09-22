import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { GenericCrudService } from './generic-crud.service';
import { ToastService } from '../../core/services/toast.service';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { ENTITY_CONFIGS } from './entity-configs';
import { EntityConfig, FieldConfig } from './crud-config.model';

@Component({
  selector: 'app-generic-form',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
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
  enregistrement = signal(false);
  private toast = inject(ToastService);
  private annees = inject(AnneeScolaireService);

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

      const id = this.route.snapshot.paramMap.get('id');
      if (id && id !== 'nouveau') {
        this.modeEdition = true;
        this.idEnCours = +id;
        this.crud.get(this.config.endpoint, +id).subscribe((item) => {
          this.valeursInitiales = item;
          this.form.patchValue(item);
        });
      }
    });
  }

  private construireFormulaire(): void {
    const controls: Record<string, any> = {};
    for (const field of this.config.fields) {
      // Un champ `lecture` n'a pas de contrôle : il affiche une valeur qui
      // vient d'ailleurs, et ne doit pas partir dans le payload.
      if (field.type === 'lecture') continue;
      const validators = field.required ? [Validators.required] : [];
      controls[field.key] = [field.type === 'checkbox' ? false : '', validators];
      if (field.optionsEndpoint) {
        this.optionsParChamp[field.key] = [];
      }
    }
    this.form = this.fb.group(controls);
  }

  private chargerOptions(): void {
    const champsAvecEndpoint = this.config.fields.filter((f) => f.optionsEndpoint);
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
   * Une liste d'options qui se compte par année suit l'année du bandeau haut,
   * comme sa propre page. Sans ça, le budget annoncé ici serait celui de
   * l'année active pendant qu'on consulte un autre exercice.
   */
  private parametresOptions(endpoint: string): Record<string, string> {
    const id = this.annees.idAnnee();
    return ENTITY_CONFIGS[endpoint]?.filtreParAnnee && id !== null
      ? { id_annee_scolaire: String(id) }
      : {};
  }

  libelleOption(field: FieldConfig, option: any): string {
    const cle = field.optionsLabelKey || 'nom';
    return option[cle] ?? `#${option.id}`;
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
      next: () => {
        this.enregistrement.set(false);
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
      },
      error: () => this.enregistrement.set(false),
    });
  }
}
