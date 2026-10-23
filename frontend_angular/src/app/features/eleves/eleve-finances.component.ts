import { Component, HostListener, Input, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  FraisDisponibles,
  LigneFrais,
  MethodePaiement,
  SituationFinanciereEleve,
} from '../../core/models/models';
import { FinanceService } from '../finances/finance.service';
import { ToastService } from '../../core/services/toast.service';
import { dateDuJour, instantDuPaiement, pasDansLeFutur } from '../../shared/dates';
import { AuthService } from '../../core/services/auth.service';
import { ChoixFraisComponent, FraisAffiche } from '../../shared/frais/choix-frais.component';

/**
 * Onglet « Finances » du dossier de l'élève.
 *
 * Tout ce qu'un administrateur doit comprendre sans ouvrir une autre page :
 * ce qui était dû, ce qui a été payé, ce qui reste, quels mois sont soldés,
 * et par quel moyen chaque règlement est arrivé.
 *
 * Deux partis pris :
 *
 * * **Une année à la fois.** Le sélecteur en tête n'est pas un confort : les
 *   frais d'une année n'ont jamais été facturés avec ceux d'une autre, et un
 *   total qui les additionne ne correspond à aucune facture réelle. Un ancien
 *   élève garde ainsi une ardoise lisible année par année.
 * * **L'écran ne recalcule rien.** Résumé, échéancier et statuts viennent du
 *   serveur, qui les tire des paiements. Un montant recalculé côté navigateur
 *   finirait par diverger de celui du reçu et de celui de la caisse.
 */
@Component({
  selector: 'app-eleve-finances',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink, ChoixFraisComponent],
  templateUrl: './eleve-finances.component.html',
})
export class EleveFinancesComponent implements OnInit {
  @Input({ required: true }) idEleve!: number;

  private fb = inject(FormBuilder);
  private finance = inject(FinanceService);
  private toast = inject(ToastService);
  private auth = inject(AuthService);

  // Matrice des autorisations : encaisser et ajouter des frais au guichet
  // relèvent de l'encaissement ; accorder une remise ou facturer, des frais.
  droitEncaisser = computed(() => this.auth.peutEcrire('encaissement'));
  droitFacturer = computed(() => this.auth.peutEcrire('frais'));
  /**
   * Ajouter des frais modifie le dossier de l'élève : il faut écrire les frais
   * et le dossier. Le comptable fixe les tarifs, il n'ajoute rien au dossier,
   * qu'il ne lit qu'en consultation.
   */
  droitAjouterFrais = computed(() => this.auth.peutEcrire('frais') && this.auth.peutEcrire('eleves'));

  situation = signal<SituationFinanciereEleve | null>(null);
  chargement = signal(true);
  erreur = signal<string | null>(null);
  message = signal<string | null>(null);
  occupe = signal(false);
  telechargement = signal<number | null>(null);

  /** Fenêtre d'encaissement ouverte, et frais en cours de remise. */
  saisieOuverte = signal(false);
  remiseOuverte = signal<number | null>(null);

  /** Frais cochés dans la fenêtre d'encaissement : le paiement ne va que sur eux. */
  fraisCoches = signal<number[]>([]);

  /** Fenêtre « Ajouter des frais » : frais au choix souscrits en cours d'année. */
  ajoutOuvert = signal(false);
  disponibles = signal<FraisDisponibles | null>(null);
  fraisAjoutes = signal<number[]>([]);
  dateDebutAjout = signal('');

  /** Les frais au choix que l'élève n'a pas encore, au montant de son niveau. */
  fraisAAjouter = computed<FraisAffiche[]>(() =>
    (this.disponibles()?.types ?? [])
      .filter((t) => !t.deja)
      .map((t) => ({
        id: t.id,
        libelle: t.libelle,
        nature: 'autre' as const,
        montant: Number(t.montant),
        tarifNiveau: t.tarif_niveau,
        mensuel: t.mensuel,
      })));

  dejaSouscrits = computed(() =>
    (this.disponibles()?.types ?? []).filter((t) => t.deja).map((t) => t.libelle));

  /** Un frais mensuel est coché : l'écran annonce une échéance par mois jusqu'à la fin de l'année. */
  mensuelsCoches = computed(() =>
    this.fraisAAjouter().some((f) => f.mensuel && this.fraisAjoutes().includes(f.id)));

  methodes: { value: MethodePaiement; label: string }[] = [
    { value: 'especes', label: 'Espèces' },
    { value: 'orange_money', label: 'Orange Money' },
    { value: 'wave', label: 'Wave' },
    { value: 'free_money', label: 'Free Money' },
    { value: 'carte_bancaire', label: 'Carte bancaire' },
  ];

  /** Borne haute du sélecteur de date du paiement : aujourd'hui. */
  readonly aujourdhui = dateDuJour();

  formPaiement = this.fb.group({
    montant: [null as number | null, [Validators.required, Validators.min(1)]],
    methode: ['especes' as MethodePaiement, Validators.required],
    // Le jour même par défaut ; jamais un jour à venir.
    date_paiement: [dateDuJour(), pasDansLeFutur],
    reference_transaction: [''],
    commentaire: [''],
  });

  formRemise = this.fb.group({
    remise: [null as number | null, [Validators.required, Validators.min(0)]],
    motif_remise: [''],
  });

  /** Reste à payer de l'année affichée : le plafond d'un encaissement. */
  reste = computed(() => this.situation()?.resume.reste ?? 0);

  /**
   * Aucun frais n'a encore été émis pour cette année.
   *
   * Sans frais, le reste à payer vaut zéro : il faut distinguer « tout est
   * soldé » de « il n'y a rien à solder », sans quoi l'élève ne pourrait
   * jamais payer, donc jamais voir son inscription confirmée.
   */
  sansFrais = computed(() => (this.situation()?.resume.nombre_frais ?? 0) === 0);

  /**
   * Le bouton n'est éteint que lorsqu'il n'y a réellement rien à encaisser :
   * des frais existent et ils sont tous réglés.
   */
  peutEncaisser = computed(() => {
    const situation = this.situation();
    if (!situation?.annee) return false;
    return this.sansFrais() || this.reste() > 0;
  });

  /** Frais encore ouverts, proposés en cases à cocher. */
  fraisOuverts = computed(() => (this.situation()?.frais ?? []).filter((f) => Number(f.reste) > 0));

  totalCoche = computed(() =>
    this.fraisOuverts()
      .filter((f) => this.fraisCoches().includes(f.id))
      .reduce((somme, f) => somme + Number(f.reste), 0));

  /** Ce qu'on peut encaisser : les frais cochés, ou à défaut tout le reste de l'année. */
  plafond = computed(() => (this.fraisCoches().length ? this.totalCoche() : this.reste()));

  ngOnInit(): void {
    this.charger();
  }

  /** Échap ferme la fenêtre de paiement, comme le bouton ✕. */
  @HostListener('document:keydown.escape')
  surEchap(): void {
    if (this.saisieOuverte() || this.ajoutOuvert()) this.fermer();
  }

  charger(idAnnee?: number | null): void {
    this.chargement.set(true);
    this.erreur.set(null);
    this.finance.situationEleve(this.idEleve, idAnnee ?? null).subscribe({
      next: (s) => {
        this.situation.set(s);
        this.chargement.set(false);
      },
      error: (e) => {
        this.erreur.set(this.messageErreur(e));
        this.chargement.set(false);
      },
    });
  }

  changerAnnee(valeur: string): void {
    this.fermer();
    this.charger(Number(valeur));
  }

  ouvrirSaisie(): void {
    this.remiseOuverte.set(null);
    this.saisieOuverte.set(true);
    this.message.set(null);
    this.erreur.set(null);
    this.fraisCoches.set([]);
    this.formPaiement.reset({ methode: 'especes', date_paiement: dateDuJour(),
                              reference_transaction: '', commentaire: '' });
  }

  /** Cocher un frais propose d'en régler le reste : le montant suit les cases. */
  basculerFrais(id: number): void {
    this.fraisCoches.update((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]));
    this.formPaiement.patchValue({ montant: this.fraisCoches().length ? this.totalCoche() : null });
  }

  ouvrirRemise(frais: LigneFrais): void {
    this.saisieOuverte.set(false);
    this.remiseOuverte.update((id) => (id === frais.id ? null : frais.id));
    this.formRemise.reset({ remise: frais.remise, motif_remise: frais.motif_remise ?? '' });
  }

  fermer(): void {
    this.saisieOuverte.set(false);
    this.ajoutOuvert.set(false);
    this.remiseOuverte.set(null);
  }

  encaisser(): void {
    if (this.formPaiement.invalid) return this.formPaiement.markAllAsTouched();
    const v = this.formPaiement.getRawValue();
    if (Number(v.montant) > this.plafond()) {
      this.erreur.set(this.fraisCoches().length
        ? 'Le montant dépasse le reste des frais cochés.'
        : 'Le montant dépasse le reste à payer.');
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);

    this.finance
      .encaisserPourEleve({
        id_eleve: this.idEleve,
        id_annee_scolaire: this.situation()?.annee?.id ?? null,
        montant: v.montant,
        methode: v.methode,
        frais: this.fraisCoches().length ? this.fraisCoches() : null,
        // Le champ HTML rend « 2026-10-05 » ; l'API attend un instant. Omis
        // (aujourd'hui), le serveur date le paiement de maintenant.
        date_paiement: instantDuPaiement(v.date_paiement) ?? null,
        reference_transaction: v.reference_transaction || null,
        commentaire: v.commentaire || null,
      })
      .subscribe({
        next: (resultat) => {
          // Le serveur renvoie la situation déjà recalculée : pas de second
          // appel, et surtout aucun risque d'afficher un état intermédiaire.
          this.situation.set(resultat.situation);
          this.toast.succes('Paiement enregistré',
                            `${resultat.paiements.length} frais réglé(s) ou entamé(s).`);
          const inscrit = resultat.inscription_confirmee;
          if (inscrit) {
            this.toast.succes(`🎉 ${inscrit.eleve} est maintenant inscrit`,
                              `Droits d'inscription et premier mois réglés${inscrit.classe ? ' — ' + inscrit.classe : ''}.`);
          }
          this.saisieOuverte.set(false);
          this.fraisCoches.set([]);
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(this.messageErreur(e));
          this.occupe.set(false);
        },
      });
  }

  appliquerRemise(idFrais: number): void {
    if (this.formRemise.invalid) return this.formRemise.markAllAsTouched();
    const v = this.formRemise.getRawValue();
    this.occupe.set(true);
    this.erreur.set(null);

    this.finance
      .modifierFrais(idFrais, {
        remise: v.remise,
        motif_remise: v.motif_remise || null,
      })
      .subscribe({
        next: () => {
          this.toast.succes('Remise enregistrée');
          this.remiseOuverte.set(null);
          this.occupe.set(false);
          this.charger(this.situation()?.annee?.id ?? null);
        },
        error: (e) => {
          this.erreur.set(this.messageErreur(e));
          this.occupe.set(false);
        },
      });
  }

  ouvrirAjout(): void {
    const annee = this.situation()?.annee;
    if (!annee) return;
    this.saisieOuverte.set(false);
    this.remiseOuverte.set(null);
    this.erreur.set(null);
    this.fraisAjoutes.set([]);
    this.disponibles.set(null);
    // Aujourd'hui, ramené dans les bornes de l'année affichée.
    const aujourdhui = new Date().toISOString().slice(0, 10);
    this.dateDebutAjout.set(aujourdhui < annee.date_debut ? annee.date_debut
      : aujourdhui > annee.date_fin ? annee.date_fin : aujourdhui);
    this.ajoutOuvert.set(true);
    this.finance.fraisDisponibles(this.idEleve, annee.id).subscribe({
      next: (d) => this.disponibles.set(d),
      error: (e) => this.erreur.set(this.messageErreur(e)),
    });
  }

  basculerAjout(id: number): void {
    this.fraisAjoutes.update((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]));
  }

  ajouterFrais(): void {
    const annee = this.situation()?.annee;
    if (!annee || !this.fraisAjoutes().length) return;
    this.occupe.set(true);
    this.erreur.set(null);
    this.finance
      .ajouterFrais(this.idEleve, {
        frais: this.fraisAjoutes(),
        date_debut: this.dateDebutAjout() || null,
        id_annee_scolaire: annee.id,
      })
      .subscribe({
        next: (r) => {
          this.situation.set(r.situation);
          this.toast.succes(
            `Frais ajoutés : ${r.frais.map((f) => f.libelle).join(', ')}`,
            `${r.crees} échéance(s) émise(s), ${Number(r.total).toLocaleString('fr-FR')} F au total.`,
          );
          this.ajoutOuvert.set(false);
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(this.messageErreur(e));
          this.occupe.set(false);
        },
      });
  }

  /** Le reçu est produit par le serveur : même document que celui archivé. */
  telechargerRecu(idRecu: number): void {
    this.telechargement.set(idRecu);
    this.finance.telecharger(`/recus/${idRecu}/pdf/`).subscribe({
      next: () => this.telechargement.set(null),
      error: () => {
        this.erreur.set("Le reçu n'a pas pu être téléchargé.");
        this.telechargement.set(null);
      },
    });
  }

  private messageErreur(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return "L'opération a échoué.";
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
