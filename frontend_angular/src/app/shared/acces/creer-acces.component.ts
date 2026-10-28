import { Component, EventEmitter, Input, Output, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { ToastService } from '../../core/services/toast.service';

/** Réponse de POST /api/<fiches>/{id}/creer-acces/. */
export interface AccesCree {
  id_utilisateur: number;
  email: string;
  role: string;
  role_libelle: string;
  nom_complet: string;
  mot_de_passe_provisoire: string;
  /** Vrai quand les identifiants sont aussi partis par e-mail. */
  mail_envoye?: boolean;
  detail: string;
}

/**
 * Crée le compte de connexion d'une fiche — parent, élève ou enseignant — et
 * le rattache à elle.
 *
 * Le compte et le rattachement vont ensemble : un compte parent qui n'est
 * relié à aucune fiche ne voit aucun enfant, et c'était jusqu'ici impossible
 * à faire depuis l'application.
 *
 * Le mot de passe provisoire n'est lisible qu'ici, juste après la création :
 * le serveur ne le stocke plus qu'en empreinte. D'où le bouton « Copier » et
 * le rappel de le transmettre.
 */
@Component({
  selector: 'app-creer-acces',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: './creer-acces.component.html',
})
export class CreerAccesComponent {
  /** Endpoint de la fiche, ex: `parents`, `eleves`, `enseignants`. */
  @Input({ required: true }) endpoint!: string;
  @Input({ required: true }) idFiche!: number;
  /** Ce qu'on nomme dans le titre, ex: « Awa Diop ». */
  @Input() libelle = '';
  /** Rôle donné au compte, pour l'annoncer avant la création. */
  @Input() role = '';
  @Input() nom = '';
  @Input() prenom = '';
  @Input() email = '';

  @Output() ferme = new EventEmitter<void>();
  /** Émis une fois le compte créé : l'écran appelant se recharge. */
  @Output() cree = new EventEmitter<AccesCree>();

  private http = inject(HttpClient);
  private fb = inject(FormBuilder);
  private toast = inject(ToastService);

  occupe = signal(false);
  erreur = signal<string | null>(null);
  resultat = signal<AccesCree | null>(null);
  copie = signal(false);

  form = this.fb.nonNullable.group({
    prenom: [''],
    nom: ['', Validators.required],
    email: ['', [Validators.required, Validators.email]],
    // Décoché par défaut : sans messagerie réglée sur le serveur, rien ne part.
    envoyer_par_mail: [false],
  });

  ngOnInit(): void {
    this.form.patchValue({ prenom: this.prenom, nom: this.nom, email: this.email });
  }

  valider(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    this.http
      .post<AccesCree>(
        `${environment.apiUrl}/${this.endpoint}/${this.idFiche}/creer-acces/`,
        this.form.getRawValue())
      .subscribe({
        next: (r) => {
          this.occupe.set(false);
          this.resultat.set(r);
          this.toast.succes('Accès créé', r.mail_envoye
            ? `Paramètres de connexion envoyés à ${r.email}.`
            : `${r.nom_complet} peut maintenant se connecter.`);
          this.cree.emit(r);
        },
        error: (e) => {
          this.occupe.set(false);
          const corps = e?.error ?? {};
          this.erreur.set(corps.detail ?? corps.email?.[0] ?? corps.nom?.[0]
                          ?? "La création de l'accès a échoué.");
        },
      });
  }

  copier(): void {
    const r = this.resultat();
    if (!r) return;
    navigator.clipboard
      ?.writeText(`${r.email} / ${r.mot_de_passe_provisoire}`)
      .then(() => this.copie.set(true))
      .catch(() => this.copie.set(false));
  }
}
