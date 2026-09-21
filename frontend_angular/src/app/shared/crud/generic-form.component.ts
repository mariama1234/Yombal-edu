import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { GenericCrudService } from './generic-crud.service';
import { ToastService } from '../../core/services/toast.service';
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
  enregistrement = signal(false);
  private toast = inject(ToastService);

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
        this.crud.get(this.config.endpoint, +id).subscribe((item) => this.form.patchValue(item));
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
      acc[f.key] = this.crud.list(f.optionsEndpoint!);
      return acc;
    }, {} as Record<string, any>);

    forkJoin(requetes).subscribe((resultats: any) => {
      for (const key of Object.keys(resultats)) {
        this.optionsParChamp[key] = resultats[key].results;
      }
    });
  }

  libelleOption(field: FieldConfig, option: any): string {
    const cle = field.optionsLabelKey || 'nom';
    return option[cle] ?? `#${option.id}`;
  }

  /** Valeur brute d'un champ `lecture`, lue sur l'option choisie dans son champ source. */
  private valeurSource(field: FieldConfig): unknown {
    const source = field.source;
    if (!source) return null;
    const choisi = this.form.get(source.champ)?.value;
    const option = (this.optionsParChamp[source.champ] ?? [])
      .find((o) => String(o.id) === String(choisi));
    return option?.[source.cle];
  }

  /** Affichage d'un champ `lecture`. Tant que rien n'est choisi, il n'y a rien à montrer. */
  valeurLecture(field: FieldConfig): string {
    const valeur = this.valeurSource(field);
    if (valeur === undefined || valeur === null || valeur === '') return '—';
    const nombre = Number(valeur);
    const texte = Number.isFinite(nombre) ? nombre.toLocaleString('fr-FR') : String(valeur);
    return field.source?.suffixe ? `${texte} ${field.source.suffixe}` : texte;
  }

  /** Vrai quand la valeur affichée est négative — un budget déjà dépassé. */
  lectureEnNegatif(field: FieldConfig): boolean {
    return Number(this.valeurSource(field)) < 0;
  }

  /**
   * Vrai quand ce qui est saisi dépasse la valeur affichée.
   *
   * Averti, pas bloqué : une école peut avoir à passer une dépense au-delà de
   * son enveloppe, et un formulaire qui refuse l'enregistrement la laisserait
   * sans autre solution que de fausser le budget.
   */
  depasseLecture(field: FieldConfig): boolean {
    const compare = field.source?.compare;
    if (!compare) return false;
    const saisi = Number(this.form.get(compare)?.value);
    const reste = Number(this.valeurSource(field));
    return Number.isFinite(saisi) && saisi > 0 && Number.isFinite(reste) && saisi > reste;
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
