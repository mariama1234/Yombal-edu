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

      // Pré-sélection de l'année active. Quand plusieurs années sont marquées
      // actives, celle qui porte des classes est la seule sur laquelle on
      // puisse inscrire : la retenir évite d'ouvrir le formulaire sur une
      // année vide, où aucune classe n'est proposable.
      const actives = annees.results.filter((a) => a.statut === 'active');
      const utilisable =
        actives.find((a) => classes.results.some((c) => c.id_annee_scolaire === a.id)) ??
        actives[0];
      if (utilisable) {
        this.form.patchValue({ id_annee_scolaire: utilisable.id as any });
      }
    });

    // Changer d'année vide la classe : une classe appartient à une année, et
    // le serveur refuse l'inscription si les deux ne concordent pas.
    this.form.get('id_annee_scolaire')?.valueChanges.subscribe(() => {
      this.form.patchValue({ id_classe: '' }, { emitEvent: false });
      this.erreur.set(null);
    });
  }

  /**
   * Classes proposables : celles de l'année sélectionnée, éventuellement
   * restreintes au niveau choisi.
   *
   * Le filtre sur l'année est ce qui manquait : le formulaire proposait
   * toutes les classes de l'école, y compris celles d'une autre année, et
   * l'inscription partait alors se faire refuser côté serveur.
   */
  classesFiltrees(): Classe[] {
    const idAnnee = this.form.get('id_annee_scolaire')?.value;
    const idNiveau = this.form.get('id_niveau')?.value;
    return this.classes().filter(
      (c) =>
        (!idAnnee || String(c.id_annee_scolaire) === String(idAnnee)) &&
        (!idNiveau || String(c.id_niveau) === String(idNiveau)),
    );
  }

  /** Vrai quand l'année choisie ne comporte encore aucune classe. */
  anneeSansClasse(): boolean {
    const idAnnee = this.form.get('id_annee_scolaire')?.value;
    if (!idAnnee) return false;
    return !this.classes().some((c) => String(c.id_annee_scolaire) === String(idAnnee));
  }

  libelleAnnee(): string {
    const idAnnee = this.form.get('id_annee_scolaire')?.value;
    return this.annees().find((a) => String(a.id) === String(idAnnee))?.libelle ?? '';
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
          this.erreur.set(this.message(err));
          console.error(err);
        },
      });
  }

  /**
   * Message du serveur plutôt qu'un texte générique.
   *
   * « Vérifie les champs et réessaie » cachait des refus parfaitement
   * explicites — « Cette classe relève d'une autre année scolaire » — et
   * rendait la panne indéchiffrable depuis l'écran.
   */
  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps || typeof corps !== 'object') {
      return "Impossible d'enregistrer l'élève. Réessaie dans un instant.";
    }
    const messages = Object.values(corps)
      .map((v) => (Array.isArray(v) ? String(v[0]) : String(v)))
      .filter(Boolean);
    return messages.length
      ? messages.join(' ')
      : "Impossible d'enregistrer l'élève. Vérifie les champs et réessaie.";
  }
}
