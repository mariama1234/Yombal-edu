import { Component, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../../core/services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  private fb = inject(FormBuilder);
  loading = signal(false);
  erreur = signal<string | null>(null);

  form = this.fb.group({
    email: ['admin@miccite.sn', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });

  constructor(private auth: AuthService, private router: Router) {}

  submit(): void {
    // Le bouton reste cliquable même quand le formulaire est incomplet : on
    // signale ici ce qui manque, plutôt que de laisser un bouton grisé sans
    // explication.
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      this.erreur.set('Renseignez votre adresse e-mail et votre mot de passe.');
      return;
    }
    this.loading.set(true);
    this.erreur.set(null);

    const { email, password } = this.form.getRawValue();
    this.auth.login(email!, password!).subscribe({
      next: () => {
        this.auth.chargerProfil().subscribe(() => {
          this.loading.set(false);
          this.router.navigate(['/tableau-de-bord']);
        });
      },
      error: () => {
        this.loading.set(false);
        this.erreur.set('E-mail ou mot de passe incorrect.');
      },
    });
  }
}
