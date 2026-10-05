import { Component, OnInit, computed, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { HttpClient } from '@angular/common/http';
import { EleveService } from './eleve.service';
import { AuthService } from '../../core/services/auth.service';
import { FinanceService } from '../finances/finance.service';
import { ToastService } from '../../core/services/toast.service';
import { environment } from '../../../environments/environment';
import {
  AnneeScolaire,
  Classe,
  FactureEleve,
  Niveau,
  Paginated,
  TypeFrais,
} from '../../core/models/models';
import { ChoixFraisComponent, FraisAffiche } from '../../shared/frais/choix-frais.component';
import { SiAccesDirective } from '../../shared/acces/si-acces.directive';
import { AlerteAge, alerteAge, fourchetteLisible } from './alerte-age';

/** Les montants arrivent de l'API en chaînes décimales (« 30000.00 ») ou vides. */
function nombre(valeur: unknown): number | null {
  if (valeur === null || valeur === undefined || valeur === '') return null;
  const n = Number(valeur);
  return Number.isFinite(n) ? n : null;
}

/**
 * Inscription d'un élève, jusqu'à la facture.
 *
 * Le niveau fixe le montant de chaque frais, d'après la grille des tarifs :
 * tant qu'il n'est pas choisi, aucun montant n'est affiché. Les frais dus
 * d'office viennent en tête, les autres se présentent en cartes à cocher, et
 * le total suit chaque case. À l'enregistrement, l'écran laisse place à la
 * facture — matricule et montant total dû — que la famille présentera à la
 * caisse.
 */
@Component({
  selector: 'app-eleve-form',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink, ChoixFraisComponent, SiAccesDirective],
  templateUrl: './eleve-form.component.html',
})
export class EleveFormComponent implements OnInit {
  private fb = inject(FormBuilder);
  private http = inject(HttpClient);
  private eleveService = inject(EleveService);
  private auth = inject(AuthService);
  private finance = inject(FinanceService);
  private toast = inject(ToastService);

  enregistrement = signal(false);
  erreur = signal<string | null>(null);
  /** Renseignée après l'enregistrement : l'écran affiche alors la facture. */
  facture = signal<FactureEleve | null>(null);
  /** Âge hors de la fourchette du niveau : l'écran propose de confirmer. */
  alerte = signal<AlerteAge | null>(null);

  niveaux = signal<Niveau[]>([]);
  classes = signal<Classe[]>([]);
  annees = signal<AnneeScolaire[]>([]);
  typesFrais = signal<TypeFrais[]>([]);
  /** Types de frais cochés, par identifiant. */
  fraisCoches = signal<number[]>([]);

  private idNiveau = signal<string>('');
  private idAnnee = signal<string>('');

  form = this.fb.group({
    // Section 1 — Informations de l'élève
    nom: ['', Validators.required],
    prenom: ['', Validators.required],
    date_naissance: [''],
    sexe: ['F', Validators.required],
    adresse: [''],
    numero_registre: [''],

    // Section 2 — Scolarité : le niveau est obligatoire, il fixe les tarifs.
    id_niveau: ['', Validators.required],
    id_classe: ['', Validators.required],
    id_annee_scolaire: ['', Validators.required],
    date_inscription: [new Date().toISOString().slice(0, 10), Validators.required],

    // Section 3 — Parent / tuteur
    parent_nom_complet: [''],
    parent_lien: ['mere'],
    parent_telephone: [''],
    parent_email: [''],
    parent_adresse: [''],
  });

  niveauChoisi = computed(() => this.niveaux().find((n) => String(n.id) === this.idNiveau()) ?? null);
  /** « de 12 à 14 ans » pour le niveau choisi, affiché sous la date de naissance. */
  ageAdmis = computed(() => fourchetteLisible(this.niveauChoisi()));

  /**
   * Les frais de l'école au montant du niveau choisi. Le bloc « Frais » —
   * le même qu'à la réinscription — les range et calcule le total.
   */
  fraisAffiches = computed<FraisAffiche[]>(() =>
    this.typesFrais().map((t) => ({
      id: t.id,
      libelle: t.libelle,
      nature: t.nature,
      montant: this.montantPour(t),
      tarifNiveau: this.aTarifNiveau(t),
      mensuel: this.mensuel(t),
    })));

  /** Classes de l'année et du niveau choisis. */
  classesFiltrees = computed(() =>
    this.classes().filter(
      (c) =>
        (!this.idAnnee() || String(c.id_annee_scolaire) === this.idAnnee())
        && (!this.idNiveau() || String(c.id_niveau) === this.idNiveau()),
    ));

  anneeSansClasse = computed(() =>
    !!this.idAnnee() && !this.classes().some((c) => String(c.id_annee_scolaire) === this.idAnnee()));

  libelleAnnee = computed(() => this.annees().find((a) => String(a.id) === this.idAnnee())?.libelle ?? '');

  ngOnInit(): void {
    const aucunFrais: Paginated<TypeFrais> = { count: 0, next: null, previous: null, results: [] };
    forkJoin({
      niveaux: this.http.get<Paginated<Niveau>>(`${environment.apiUrl}/niveaux/?page_size=200`),
      // Toutes les classes : au-delà de vingt, la première page en oubliait.
      classes: this.http.get<Paginated<Classe>>(`${environment.apiUrl}/classes/?page_size=200`),
      annees: this.http.get<Paginated<AnneeScolaire>>(`${environment.apiUrl}/annees-scolaires/`),
      // Le catalogue n'empêche pas d'inscrire s'il ne se charge pas.
      typesFrais: this.http
        .get<Paginated<TypeFrais>>(`${environment.apiUrl}/types-frais/?page_size=200`)
        .pipe(catchError(() => of(aucunFrais))),
    }).subscribe(({ niveaux, classes, annees, typesFrais }) => {
      this.niveaux.set(niveaux.results);
      this.classes.set(classes.results);
      this.annees.set(annees.results);
      this.typesFrais.set(typesFrais.results ?? []);
      this.preselectionnerAnnee();
    });

    this.form.controls.id_niveau.valueChanges.subscribe((v) => {
      this.idNiveau.set(v ?? '');
      // Une classe d'un autre niveau ne peut pas rester choisie.
      const classe = this.form.controls.id_classe.value;
      if (classe && !this.classesFiltrees().some((c) => String(c.id) === String(classe))) {
        this.form.patchValue({ id_classe: '' }, { emitEvent: false });
      }
    });

    // Changer d'année vide la classe : une classe appartient à une année, et
    // le serveur refuse l'inscription si les deux ne concordent pas.
    this.form.controls.id_annee_scolaire.valueChanges.subscribe((v) => {
      this.idAnnee.set(v ?? '');
      this.form.patchValue({ id_classe: '' }, { emitEvent: false });
      this.erreur.set(null);
    });
  }

  /**
   * Pré-sélection de l'année active. Quand plusieurs années sont marquées
   * actives, celle qui porte des classes est la seule sur laquelle on puisse
   * inscrire.
   */
  private preselectionnerAnnee(): void {
    const actives = this.annees().filter((a) => a.statut === 'active');
    const utilisable =
      actives.find((a) => this.classes().some((c) => c.id_annee_scolaire === a.id)) ?? actives[0];
    if (utilisable) this.form.patchValue({ id_annee_scolaire: String(utilisable.id) });
  }

  /** Montant d'un frais pour le niveau choisi — même règle que le serveur. */
  montantPour(type: TypeFrais): number {
    return nombre(this.niveauChoisi()?.tarifs?.[String(type.id)]) ?? nombre(type.montant_defaut) ?? 0;
  }

  /** Vrai quand le niveau choisi a son propre montant pour ce frais. */
  aTarifNiveau(type: TypeFrais): boolean {
    return nombre(this.niveauChoisi()?.tarifs?.[String(type.id)]) !== null;
  }

  mensuel(type: TypeFrais): boolean {
    return type.nature === 'mensualite' || (type.frequence ?? '').toLowerCase().includes('mens');
  }

  basculerFrais(id: number): void {
    this.fraisCoches.update((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]));
  }

  /** `derogation` : renvoi après l'avertissement d'âge, confirmé par la personne. */
  submit(derogation = false): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.enregistrement.set(true);
    this.erreur.set(null);
    this.alerte.set(null);

    const v = this.form.getRawValue();
    this.eleveService
      .inscrire({
        id_ecole: this.auth.currentUser()?.id_ecole!,
        nom: v.nom!,
        prenom: v.prenom!,
        date_naissance: v.date_naissance || undefined,
        sexe: v.sexe as any,
        adresse: v.adresse || undefined,
        numero_registre: v.numero_registre || undefined,
        id_classe: +v.id_classe!,
        id_annee_scolaire: +v.id_annee_scolaire!,
        date_inscription: v.date_inscription!,
        parent_nom_complet: v.parent_nom_complet || undefined,
        parent_telephone: v.parent_telephone || undefined,
        parent_email: v.parent_email || undefined,
        parent_lien: v.parent_lien as any,
        parent_adresse: v.parent_adresse || undefined,
        derogation_age: derogation,
        frais_optionnels: this.fraisCoches(),
      })
      .subscribe({
        next: (eleve) => {
          this.enregistrement.set(false);
          this.facture.set((eleve as unknown as { facture: FactureEleve }).facture ?? null);
          const f = this.facture();
          if (f) {
            this.toast.succes(`Inscription enregistrée : ${f.prenom} ${f.nom}`,
                              `Matricule ${f.matricule} — inscrit incomplet jusqu'au règlement des droits et du premier mois.`);
          }
          window.scrollTo({ top: 0, behavior: 'smooth' });
        },
        error: (err) => {
          this.enregistrement.set(false);
          const alerte = alerteAge(err);
          if (alerte) {
            this.alerte.set(alerte);
            if (alerte.code === 'date_naissance_manquante') {
              this.form.controls.date_naissance.setErrors({ requise: true });
              this.form.controls.date_naissance.markAsTouched();
            }
            return;
          }
          this.erreur.set(this.message(err));
        },
      });
  }

  telechargerFacture(): void {
    const f = this.facture();
    if (!f) return;
    this.finance
      .telecharger(`/eleves/${f.id_eleve}/facture/`, { id_annee_scolaire: f.id_annee_scolaire })
      .subscribe({ error: () => this.erreur.set("La facture n'a pas pu être générée.") });
  }

  /** Repart d'un formulaire vierge, sur la même année. */
  nouvelleInscription(): void {
    const annee = this.form.controls.id_annee_scolaire.value;
    this.facture.set(null);
    this.erreur.set(null);
    this.alerte.set(null);
    this.fraisCoches.set([]);
    this.form.reset({
      sexe: 'F',
      parent_lien: 'mere',
      id_annee_scolaire: annee,
      date_inscription: new Date().toISOString().slice(0, 10),
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
