import { Component, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../core/services/auth.service';
import { ToastService } from '../../core/services/toast.service';
import { Utilisateur } from '../../core/models/models';

/**
 * « Mon profil » : ce que chacun peut faire sur son propre compte.
 *
 * Son mot de passe d'abord — jusqu'ici, un compte créé par la direction
 * gardait le mot de passe provisoire transmis de vive voix, faute d'écran
 * pour le changer. Son nom et son téléphone ensuite, qu'on corrige sans
 * déranger l'administration.
 *
 * L'e-mail et le rôle restent affichés sans être modifiables : l'identifiant
 * de connexion et les droits appartiennent à la direction.
 */
@Component({
  selector: 'app-mon-profil',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: './mon-profil.component.html',
})
export class MonProfilComponent {
  private http = inject(HttpClient);
  private fb = inject(FormBuilder);
  private toast = inject(ToastService);
  auth = inject(AuthService);

  occupeProfil = signal(false);
  occupeMotDePasse = signal(false);
  erreurProfil = signal<string | null>(null);
  erreurMotDePasse = signal<string | null>(null);
  motDePasseChange = signal(false);

  utilisateur = computed(() => this.auth.currentUser());

  /** Les modules ouverts au compte, tels que le serveur les renvoie. */
  acces = computed(() => {
    const acces = this.auth.currentUser()?.acces ?? {};
    return Object.entries(acces)
      .map(([module, niveau]) => ({ module: module.replace(/_/g, ' '), niveau }))
      .sort((a, b) => a.module.localeCompare(b.module));
  });

  formProfil = this.fb.nonNullable.group({
    prenom: ['', Validators.required],
    nom: ['', Validators.required],
    telephone: [''],
  });

  formMotDePasse = this.fb.nonNullable.group({
    mot_de_passe_actuel: ['', Validators.required],
    nouveau_mot_de_passe: ['', [Validators.required, Validators.minLength(8)]],
    confirmation: ['', Validators.required],
  });

  ngOnInit(): void {
    const remplir = (u: Utilisateur | null) => {
      if (!u) return;
      this.formProfil.patchValue({ prenom: u.prenom, nom: u.nom, telephone: u.telephone ?? '' });
    };
    // Après un rafraîchissement, le profil n'est pas encore là.
    remplir(this.auth.currentUser());
    if (!this.auth.currentUser()) {
      this.auth.chargerProfil().subscribe({ next: remplir, error: () => {} });
    }
  }

  enregistrerProfil(): void {
    if (this.formProfil.invalid) {
      this.formProfil.markAllAsTouched();
      return;
    }
    this.occupeProfil.set(true);
    this.erreurProfil.set(null);
    this.http
      .patch<Utilisateur>(`${environment.apiUrl}/auth/moi/`, this.formProfil.getRawValue())
      .subscribe({
        next: (u) => {
          this.occupeProfil.set(false);
          this.auth.currentUser.set(u);
          this.toast.succes('Profil enregistré', 'Vos informations sont à jour.');
        },
        error: (e) => {
          this.occupeProfil.set(false);
          this.erreurProfil.set(this.message(e, "L'enregistrement a échoué."));
        },
      });
  }

  changerMotDePasse(): void {
    const valeurs = this.formMotDePasse.getRawValue();
    if (this.formMotDePasse.invalid) {
      this.formMotDePasse.markAllAsTouched();
      return;
    }
    if (valeurs.nouveau_mot_de_passe !== valeurs.confirmation) {
      this.erreurMotDePasse.set('Les deux saisies du nouveau mot de passe diffèrent.');
      return;
    }
    this.occupeMotDePasse.set(true);
    this.erreurMotDePasse.set(null);
    this.motDePasseChange.set(false);
    this.http
      .post(`${environment.apiUrl}/auth/mot-de-passe/`, {
        mot_de_passe_actuel: valeurs.mot_de_passe_actuel,
        nouveau_mot_de_passe: valeurs.nouveau_mot_de_passe,
      })
      .subscribe({
        next: () => {
          this.occupeMotDePasse.set(false);
          this.motDePasseChange.set(true);
          this.formMotDePasse.reset();
          this.toast.succes('Mot de passe changé',
                            'Utilisez-le à votre prochaine connexion.');
        },
        error: (e) => {
          this.occupeMotDePasse.set(false);
          this.erreurMotDePasse.set(this.message(e, 'Le changement a échoué.'));
        },
      });
  }

  /** Le message du serveur, quel que soit le champ qui le porte. */
  private message(erreur: any, defaut: string): string {
    const corps = erreur?.error;
    if (typeof corps === 'string') return corps;
    if (corps && typeof corps === 'object') {
      const premier = Object.values(corps)[0];
      if (Array.isArray(premier)) return String(premier[0]);
      if (typeof premier === 'string') return premier;
    }
    return defaut;
  }
}
