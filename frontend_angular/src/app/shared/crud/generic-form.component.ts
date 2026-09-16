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

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.enregistrement.set(true);
    const payload = this.form.getRawValue();

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
