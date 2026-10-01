import { Component, OnInit, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EcolePlateforme } from '../../core/models/models';
import { ToastService } from '../../core/services/toast.service';
import { PlateformeService } from './plateforme.service';

/**
 * Création d'une école cliente, ou sa fiche.
 *
 * À la création, le premier administrateur se saisit avec l'école : sans lui,
 * personne ne pourrait s'y connecter. Sur la fiche, on suit l'offre, le
 * règlement et le statut, et on ajoute des administrateurs.
 */
@Component({
  selector: 'app-ecole-plateforme',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink, DatePipe],
  template: `
    <div class="pf-fil">
      <a routerLink="/plateforme">Écoles</a>
      <span aria-hidden="true">›</span>
      <span>{{ ecole()?.nom ?? 'Nouvelle école' }}</span>
    </div>

    <div class="pf-entete-fiche">
      @if (ecole(); as e) {
        <span class="pf-initiale grande" style="background:var(--navy-800);">{{ initiale(e.nom) }}</span>
      }
      <div>
        <h1>{{ ecole()?.nom ?? 'Nouvelle école cliente' }}</h1>
        <p>
          @if (ecole(); as e) {
            <span class="pill offre" [class]="e.plan_abonnement">{{ e.plan_libelle }}</span>
            <span class="pill" [class]="e.statut">{{ e.statut_libelle }}</span>
            <span class="hint">Cliente depuis le {{ e.date_creation | date:'dd/MM/yyyy' }}</span>
          } @else {
            L'école, son offre, puis son premier administrateur, qui recevra ses identifiants.
          }
        </p>
      </div>
    </div>

    @if (erreurGenerale()) { <div class="form-error">{{ erreurGenerale() }}</div> }

    <div class="pf-fiche" [class.creation]="!ecole()">
      <form class="pf-fiche-principale" [formGroup]="form" (ngSubmit)="enregistrer()">
        <section class="pf-etape">
          <div class="pf-etape-tete">
            <span class="pf-etape-numero">1</span>
            <div><h2>École</h2><p>Coordonnées de l'établissement.</p></div>
          </div>
          <div class="form-grid">
            <div class="field span-2">
              <label for="nom">Nom de l'école *</label>
              <input class="input" id="nom" formControlName="nom" placeholder="École Les Pins">
              @if (erreurs()['nom']) { <span class="hint hint-error">{{ erreurs()['nom'] }}</span> }
            </div>
            <div class="field">
              <label for="telephone">Téléphone</label>
              <input class="input" id="telephone" type="tel" formControlName="telephone" placeholder="+221 …">
            </div>
            <div class="field">
              <label for="email">E-mail de l'école</label>
              <input class="input" id="email" type="email" formControlName="email" placeholder="contact@ecole.sn">
              @if (erreurs()['email']) { <span class="hint hint-error">{{ erreurs()['email'] }}</span> }
            </div>
            <div class="field span-2">
              <label for="adresse">Adresse</label>
              <input class="input" id="adresse" formControlName="adresse" placeholder="Quartier, ville">
            </div>
          </div>
        </section>

        <section class="pf-etape">
          <div class="pf-etape-tete">
            <span class="pf-etape-numero">2</span>
            <div><h2>Offre</h2><p>Ce que l'école a souscrit.</p></div>
          </div>
          <div class="pf-offres" role="radiogroup" aria-label="Offre">
            @for (o of offres; track o.valeur) {
              <label class="pf-offre" [class.choisie]="form.controls.plan_abonnement.value === o.valeur">
                <input type="radio" formControlName="plan_abonnement" [value]="o.valeur">
                <span class="pf-offre-nom">{{ o.libelle }}</span>
                <span class="pf-offre-texte">{{ o.description }}</span>
              </label>
            }
          </div>
          <div class="form-grid" style="margin-top:16px;">
            @if (ecole()) {
              <div class="field">
                <label for="statut">Statut</label>
                <select class="input" id="statut" formControlName="statut">
                  <option value="actif">Actif</option>
                  <option value="suspendu">Suspendu</option>
                  <option value="resilie">Résilié</option>
                </select>
              </div>
            } @else {
              <div class="field">
                <label class="pf-interrupteur">
                  <input type="checkbox" formControlName="abonnement_paye">
                  <span class="pf-interrupteur-piste" aria-hidden="true"></span>
                  Abonnement déjà payé
                </label>
              </div>
            }
            <div class="field">
              <label for="date_paiement">Date du paiement</label>
              <input class="input" id="date_paiement" type="date" formControlName="date_paiement">
              <span class="hint">Vide : aujourd'hui, dès que l'abonnement est marqué payé.</span>
            </div>
          </div>
        </section>

        @if (!ecole()) {
          <section class="pf-etape" formGroupName="admin">
            <div class="pf-etape-tete">
              <span class="pf-etape-numero">3</span>
              <div><h2>Premier administrateur</h2><p>Il se connecte avec cet e-mail, puis crée les autres comptes de l'école.</p></div>
            </div>
            <div class="form-grid">
              <div class="field">
                <label for="admin-prenom">Prénom *</label>
                <input class="input" id="admin-prenom" formControlName="prenom">
              </div>
              <div class="field">
                <label for="admin-nom">Nom *</label>
                <input class="input" id="admin-nom" formControlName="nom">
              </div>
              <div class="field">
                <label for="admin-email">E-mail de connexion *</label>
                <input class="input" id="admin-email" type="email" formControlName="email">
                @if (erreurs()['admin.email']) { <span class="hint hint-error">{{ erreurs()['admin.email'] }}</span> }
              </div>
              <div class="field">
                <label for="admin-telephone">Téléphone</label>
                <input class="input" id="admin-telephone" type="tel" formControlName="telephone">
              </div>
              <div class="field span-2">
                <label for="admin-password">Mot de passe *</label>
                <input class="input" id="admin-password" type="password" formControlName="password" autocomplete="new-password">
                @if (erreurs()['admin.password']) { <span class="hint hint-error">{{ erreurs()['admin.password'] }}</span> }
                @else { <span class="hint">8 caractères au moins, pas uniquement des chiffres.</span> }
              </div>
            </div>
          </section>
        }

        <div class="pf-actions">
          <a class="btn-ghost" routerLink="/plateforme">Annuler</a>
          <button type="submit" class="btn-primary" [disabled]="enregistrement()">
            {{ enregistrement() ? 'Enregistrement…' : (ecole() ? 'Enregistrer les modifications' : 'Créer l’école') }}
          </button>
        </div>
      </form>

      @if (ecole(); as e) {
        <aside class="pf-fiche-cote">
          <div class="pf-resume">
            <div><strong>{{ e.nb_eleves }}</strong><span>élèves actifs</span></div>
            <div><strong>{{ e.nb_utilisateurs }}</strong><span>comptes</span></div>
            <div><strong>{{ e.admins.length }}</strong><span>administrateurs</span></div>
          </div>

          <div class="pf-abonnement" [class.ok]="e.abonnement_paye" [class.ko]="!e.abonnement_paye && e.plan_abonnement !== 'essai'">
            <div class="pf-abonnement-tete">
              <span class="pf-abonnement-titre">Abonnement</span>
              <span class="pill offre" [class]="e.plan_abonnement">{{ e.plan_libelle }}</span>
            </div>
            <div class="pf-abonnement-etat">
              @if (e.abonnement_paye) {
                Payé@if (e.date_paiement) { <span> le {{ e.date_paiement | date:'dd/MM/yyyy' }}</span> }
              } @else if (e.plan_abonnement === 'essai') {
                En période d'essai
              } @else {
                Non payé
              }
            </div>
            <button type="button" class="pf-abonnement-bouton" [disabled]="enregistrement()" (click)="basculerPaiement(e)">
              {{ e.abonnement_paye ? 'Marquer comme non payé' : 'Marquer comme payé aujourd’hui' }}
            </button>
          </div>

          <div class="pf-bloc">
            <div class="pf-bloc-tete">
              <h2>Administrateurs</h2>
              @if (!ajoutOuvert()) {
                <button type="button" class="panel-link" (click)="ajoutOuvert.set(true)">+ Ajouter</button>
              }
            </div>
            @for (a of e.admins; track a.id) {
              <div class="pf-admin">
                <span class="pf-mini-avatar">{{ a.prenom[0] }}{{ a.nom[0] }}</span>
                <div>
                  <strong>{{ a.prenom }} {{ a.nom }}</strong>
                  <span>{{ a.email }}</span>
                  <span class="hint">{{ a.role_libelle }}@if (a.telephone) { · {{ a.telephone }} }@if (a.statut !== 'actif') { · inactif }</span>
                </div>
              </div>
            } @empty {
              <p style="color:var(--red); font-size:13px; margin:0;">Aucun administrateur : personne ne peut se connecter à cette école.</p>
            }

            @if (ajoutOuvert()) {
              <form class="pf-ajout-admin" [formGroup]="formAdmin" (ngSubmit)="ajouterAdmin()">
                @if (erreurAdmin()) { <div class="form-error">{{ erreurAdmin() }}</div> }
                <div class="field"><label for="na-prenom">Prénom *</label><input class="input" id="na-prenom" formControlName="prenom"></div>
                <div class="field"><label for="na-nom">Nom *</label><input class="input" id="na-nom" formControlName="nom"></div>
                <div class="field"><label for="na-email">E-mail de connexion *</label><input class="input" id="na-email" type="email" formControlName="email"></div>
                <div class="field"><label for="na-tel">Téléphone</label><input class="input" id="na-tel" type="tel" formControlName="telephone"></div>
                <div class="field">
                  <label for="na-password">Mot de passe *</label>
                  <input class="input" id="na-password" type="password" formControlName="password" autocomplete="new-password">
                  <span class="hint">8 caractères au moins.</span>
                </div>
                <div class="pf-actions">
                  <button type="button" class="btn-ghost" (click)="ajoutOuvert.set(false)">Annuler</button>
                  <button type="submit" class="btn-primary" [disabled]="enregistrement()">Créer</button>
                </div>
              </form>
            }
          </div>
        </aside>
      }
    </div>
  `,
})
export class EcolePlateformeComponent implements OnInit {
  private fb = inject(FormBuilder);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private plateforme = inject(PlateformeService);
  private toast = inject(ToastService);

  ecole = signal<EcolePlateforme | null>(null);
  enregistrement = signal(false);
  erreurs = signal<Record<string, string>>({});
  erreurGenerale = signal<string | null>(null);
  ajoutOuvert = signal(false);
  erreurAdmin = signal<string | null>(null);

  form = this.fb.group({
    nom: ['', Validators.required],
    telephone: [''],
    email: [''],
    adresse: [''],
    plan_abonnement: ['standard'],
    statut: ['actif'],
    abonnement_paye: [false],
    date_paiement: [''],
    admin: this.fb.group({
      prenom: ['', Validators.required],
      nom: ['', Validators.required],
      email: ['', [Validators.required, Validators.email]],
      telephone: [''],
      password: ['', [Validators.required, Validators.minLength(8)]],
    }),
  });

  formAdmin = this.fb.group({
    prenom: ['', Validators.required],
    nom: ['', Validators.required],
    email: ['', [Validators.required, Validators.email]],
    telephone: [''],
    password: ['', [Validators.required, Validators.minLength(8)]],
  });

  readonly offres = [
    { valeur: 'essai', libelle: 'Essai', description: 'Découverte gratuite, sans facturation.' },
    { valeur: 'standard', libelle: 'Standard', description: 'Élèves, pédagogie et finances.' },
    { valeur: 'pro', libelle: 'Pro', description: 'Tous les modules, accompagnement inclus.' },
  ];

  initiale(nom: string): string {
    // « École MIC CITE » → « M » : le mot « École » n'aide pas à reconnaître.
    const mots = nom.split(/\s+/).filter((m) => !/^(é|e)cole$/i.test(m));
    return (mots[0]?.[0] ?? nom[0] ?? '?').toUpperCase();
  }

  /** Bascule rapide depuis la carte Abonnement, sans passer par le formulaire. */
  basculerPaiement(e: EcolePlateforme): void {
    this.enregistrement.set(true);
    const corps: Record<string, unknown> = e.abonnement_paye
      ? { abonnement_paye: false, date_paiement: null }
      : { abonnement_paye: true, date_paiement: this.aujourdhui() };
    this.plateforme.modifier(e.id, corps).subscribe({
      next: (maj) => {
        this.enregistrement.set(false);
        this.remplir(maj);
        this.toast.succes(maj.abonnement_paye ? 'Abonnement marqué payé' : 'Abonnement marqué non payé', maj.nom);
      },
      error: () => {
        this.enregistrement.set(false);
        this.toast.erreur('Modification refusée', 'Le paiement n’a pas pu être enregistré.');
      },
    });
  }

  private aujourdhui(): string {
    const d = new Date();
    const deux = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${deux(d.getMonth() + 1)}-${deux(d.getDate())}`;
  }

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) {
      // La fiche n'a pas de premier administrateur à saisir.
      this.form.removeControl('admin' as never);
      this.plateforme.lire(+id).subscribe((e) => this.remplir(e));
    }
  }

  private remplir(e: EcolePlateforme): void {
    this.ecole.set(e);
    this.form.patchValue({
      nom: e.nom, telephone: e.telephone ?? '', email: e.email ?? '', adresse: e.adresse ?? '',
      plan_abonnement: e.plan_abonnement, statut: e.statut,
      abonnement_paye: e.abonnement_paye, date_paiement: e.date_paiement ?? '',
    });
  }

  enregistrer(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      this.erreurGenerale.set('Renseignez les champs marqués d\'un astérisque.');
      return;
    }
    this.enregistrement.set(true);
    this.erreurs.set({});
    this.erreurGenerale.set(null);
    const corps: Record<string, unknown> = { ...this.form.getRawValue() };
    // Une date vide n'est pas une date : le serveur prend alors aujourd'hui.
    if (!corps['date_paiement']) delete corps['date_paiement'];

    const e = this.ecole();
    const requete = e ? this.plateforme.modifier(e.id, corps) : this.plateforme.creer(corps);
    requete.subscribe({
      next: (ecole) => {
        this.enregistrement.set(false);
        this.toast.succes(e ? 'Modifications enregistrées' : 'École créée',
          e ? ecole.nom : `${ecole.nom} peut se connecter avec le compte de son administrateur.`);
        if (e) this.remplir(ecole);
        // Après la création, la suite logique est la fiche de l'école.
        else this.router.navigate(['/plateforme/ecoles', ecole.id]);
      },
      error: (err) => {
        this.enregistrement.set(false);
        this.lireRefus(err?.error);
      },
    });
  }

  ajouterAdmin(): void {
    const e = this.ecole();
    if (!e) return;
    if (this.formAdmin.invalid) {
      this.formAdmin.markAllAsTouched();
      this.erreurAdmin.set('Renseignez les champs marqués d\'un astérisque.');
      return;
    }
    this.enregistrement.set(true);
    this.erreurAdmin.set(null);
    this.plateforme.ajouterAdmin(e.id, this.formAdmin.getRawValue()).subscribe({
      next: (admin) => {
        this.enregistrement.set(false);
        this.toast.succes('Administrateur créé', `${admin.prenom} ${admin.nom} peut se connecter.`);
        this.formAdmin.reset({ prenom: '', nom: '', email: '', telephone: '', password: '' });
        this.ajoutOuvert.set(false);
        this.plateforme.lire(e.id).subscribe((maj) => this.remplir(maj));
      },
      error: (err) => {
        this.enregistrement.set(false);
        const corps = err?.error ?? {};
        this.erreurAdmin.set(Object.values(corps).flat().join(' ') || 'La création a échoué.');
      },
    });
  }

  /** Répartit un refus de l'API ; les erreurs de l'administrateur arrivent sous `admin`. */
  private lireRefus(corps: unknown): void {
    const parChamp: Record<string, string> = {};
    const texte = (v: unknown): string => Array.isArray(v) ? v.map(texte).join(' ') : typeof v === 'string' ? v : '';
    if (corps && typeof corps === 'object') {
      for (const [cle, valeur] of Object.entries(corps as Record<string, unknown>)) {
        if (valeur && typeof valeur === 'object' && !Array.isArray(valeur)) {
          for (const [sous, message] of Object.entries(valeur as Record<string, unknown>)) {
            parChamp[`${cle}.${sous}`] = texte(message);
          }
        } else {
          parChamp[cle] = texte(valeur);
        }
      }
    }
    this.erreurs.set(parChamp);
    const connus = ['nom', 'email', 'admin.email', 'admin.password'];
    const autres = Object.entries(parChamp).filter(([c]) => !connus.includes(c)).map(([, m]) => m);
    this.erreurGenerale.set(autres.length ? autres.join(' ')
      : Object.keys(parChamp).length ? 'Corrigez les champs signalés.' : 'L\'enregistrement a échoué.');
  }
}
