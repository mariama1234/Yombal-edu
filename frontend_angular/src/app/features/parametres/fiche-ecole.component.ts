import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { GenericCrudService } from '../../shared/crud/generic-crud.service';
import { ToastService } from '../../core/services/toast.service';
import { Ecole } from '../../core/models/models';

/**
 * Fiche de l'établissement, modifiable par l'école elle-même.
 *
 * Le nom, l'adresse et les coordonnées se retrouvent sur les reçus, les
 * bulletins et les en-têtes : les laisser à la seule main de la plateforme
 * obligeait l'école à ouvrir un ticket pour un numéro de téléphone changé.
 *
 * L'abonnement et le statut, eux, restent affichés sans être modifiables :
 * ils relèvent du contrat, pas du réglage (le serveur les refuse de toute
 * façon, voir `EcoleSerializer`).
 */
@Component({
  selector: 'app-fiche-ecole',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './fiche-ecole.component.html',
})
export class FicheEcoleComponent implements OnInit {
  private fb = inject(FormBuilder);
  private crud = inject<GenericCrudService<any>>(GenericCrudService);
  private toast = inject(ToastService);

  ecole = signal<Ecole | null>(null);
  chargement = signal(true);
  enregistrement = signal(false);
  erreur = signal<string | null>(null);

  form = this.fb.group({
    nom: ['', Validators.required],
    adresse: [''],
    telephone: [''],
    email: ['', Validators.email],
    logo_url: [''],
  });

  ngOnInit(): void {
    // La liste des écoles est restreinte côté serveur à celle du compte
    // connecté : sa première ligne est la seule qu'il puisse lire.
    this.crud.list('ecoles').subscribe({
      next: (res: any) => {
        const ecole = res.results?.[0] ?? null;
        this.ecole.set(ecole);
        if (ecole) this.form.patchValue(ecole);
        this.chargement.set(false);
        if (!ecole) this.erreur.set("Aucune fiche d'école n'est accessible depuis ce compte.");
      },
      error: () => {
        this.chargement.set(false);
        this.erreur.set("La fiche de l'école n'a pas pu être chargée.");
      },
    });
  }

  submit(): void {
    const ecole = this.ecole();
    if (!ecole) return;
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      this.erreur.set('Vérifiez les champs signalés.');
      return;
    }
    this.enregistrement.set(true);
    this.erreur.set(null);
    this.crud.update('ecoles', ecole.id, this.form.getRawValue() as any).subscribe({
      next: (maj: any) => {
        this.ecole.set(maj);
        this.enregistrement.set(false);
        this.toast.succes('Informations enregistrées', maj.nom);
      },
      error: (e) => {
        this.enregistrement.set(false);
        this.erreur.set(e?.error?.detail ?? "L'enregistrement a échoué.");
      },
    });
  }
}
