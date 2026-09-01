import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { EleveService } from './eleve.service';
import { AuthService } from '../../core/services/auth.service';
import { environment } from '../../../environments/environment';
import { Classe, Niveau, AnneeScolaire, Paginated } from '../../core/models/models';

@Component({
  selector: 'app-eleve-form',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './eleve-form.component.html',
})
export class EleveFormComponent implements OnInit {
  private fb = inject(FormBuilder);
  enregistrement = signal(false);
  erreur = signal<string | null>(null);

  niveaux = signal<Niveau[]>([]);
  classes = signal<Classe[]>([]);
  annees = signal<AnneeScolaire[]>([]);

  form = this.fb.group({
    // Section 1 — Informations de l'élève
    nom: ['', Validators.required],
    prenom: ['', Validators.required],
    date_naissance: [''],
    sexe: ['F', Validators.required],
    adresse: [''],

    // Section 2 — Scolarité
    id_niveau: [''], // utilisé seulement pour filtrer la liste des classes côté écran
    id_classe: ['', Validators.required],
    id_annee_scolaire: ['', Validators.required],
    date_inscription: [new Date().toISOString().slice(0, 10), Validators.required],

    // Section 3 — Parent / tuteur
    parent_nom_complet: [''],
    parent_lien: ['mere'],
    parent_telephone: [''],
    parent_email: [''],
  });

  constructor(
    private eleveService: EleveService,
    private auth: AuthService,
    private http: HttpClient,
    private router: Router
  ) {}

  ngOnInit(): void {
    forkJoin({
      niveaux: this.http.get<Paginated<Niveau>>(`${environment.apiUrl}/niveaux/`),
      classes: this.http.get<Paginated<Classe>>(`${environment.apiUrl}/classes/`),
      annees: this.http.get<Paginated<AnneeScolaire>>(`${environment.apiUrl}/annees-scolaires/`),
    }).subscribe(({ niveaux, classes, annees }) => {
      this.niveaux.set(niveaux.results);
      this.classes.set(classes.results);
      this.annees.set(annees.results);

      // Pré-sélection de l'année active si elle existe
      const active = annees.results.find((a) => a.statut === 'active');
      if (active) this.form.patchValue({ id_annee_scolaire: active.id as any });
    });
  }

  classesFiltrees(): Classe[] {
    const idNiveau = this.form.get('id_niveau')?.value;
    if (!idNiveau) return this.classes();
    return this.classes().filter((c) => String(c.id_niveau) === String(idNiveau));
  }

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.enregistrement.set(true);
    this.erreur.set(null);

    const v = this.form.getRawValue();
    this.eleveService
      .inscrire({
        id_ecole: this.auth.currentUser()?.id_ecole!,
        nom: v.nom!,
        prenom: v.prenom!,
        date_naissance: v.date_naissance || undefined,
        sexe: v.sexe as any,
        adresse: v.adresse || undefined,
        id_classe: +v.id_classe!,
        id_annee_scolaire: +v.id_annee_scolaire!,
        date_inscription: v.date_inscription!,
        parent_nom_complet: v.parent_nom_complet || undefined,
        parent_telephone: v.parent_telephone || undefined,
        parent_email: v.parent_email || undefined,
        parent_lien: v.parent_lien as any,
      })
      .subscribe({
        next: () => {
          this.enregistrement.set(false);
          this.router.navigate(['/eleves']);
        },
        error: (err) => {
          this.enregistrement.set(false);
          this.erreur.set("Impossible d'enregistrer l'élève. Vérifie les champs et réessaie.");
          console.error(err);
        },
      });
  }
}
